"""
Core matching engine — four stages:

  Stage 1  find_candidate_trials   — coarse SNOMED IS_A graph traversal
  Stage 2  apply_exclusions        — hard-filter on demographics + exclusion edges
  Stage 3  score_trials            — per-criterion scoring (0-100)
  Stage 4  match                   — orchestration + top-N ranked results

All Neo4j I/O uses the async driver.
"""

from __future__ import annotations

import logging
from typing import Any

from neo4j import AsyncDriver  # type: ignore[import-untyped]

from matcher.patient_schema import PatientProfile
from matcher.scorer import (
    compute_total_score,
    score_biomarkers,
    score_conditions,
    score_demographics,
    score_prior_therapies,
    score_trial_quality,
)

logger = logging.getLogger(__name__)


class MatchEngine:
    """Matches a patient profile against recruiting clinical trials in Neo4j.

    Args:
        driver: Async Neo4j driver (from ``neo4j.AsyncGraphDatabase.driver``).
        database: Neo4j database name (default ``"neo4j"``).
    """

    def __init__(self, driver: AsyncDriver, database: str = "neo4j") -> None:
        self._driver = driver
        self._database = database

    # ------------------------------------------------------------------
    # Stage 1 — Coarse candidate retrieval
    # ------------------------------------------------------------------

    async def find_candidate_trials(self, patient: PatientProfile) -> list[str]:
        """Return NCT IDs of trials that potentially match the patient's conditions.

        Uses two complementary Cypher queries:
          A) Walk the SNOMED IS_A hierarchy (0..3 hops) from each patient
             condition to find trials whose Criterion nodes REQUIRE_CONDITION
             an ancestor concept.
          B) Match via the Trial→Condition→SNOMED path for trials without
             parsed Criterion nodes yet.

        Both queries respect the optional therapeutic_area filter.

        Returns:
            Deduplicated list of NCT ID strings.
        """
        if not patient.conditions:
            logger.warning("Patient has no conditions — returning empty candidate set")
            return []

        async with self._driver.session(database=self._database) as session:
            # Query A — criterion-level SNOMED IS_A traversal
            result_a = await session.run(
                """
                UNWIND $patient_conditions AS cond_id
                MATCH (pc:SNOMEDConcept {concept_id: cond_id})
                MATCH (pc)-[:IS_A*0..3]->(ancestor:SNOMEDConcept)
                MATCH (criterion:Criterion)-[:REQUIRES_CONDITION]->(ancestor)
                MATCH (trial:Trial)-[:HAS_CRITERION]->(criterion)
                WHERE trial.status IN ['RECRUITING', 'ACTIVE_NOT_RECRUITING']
                  AND ($therapeutic_area IS NULL OR trial.therapeutic_area = $therapeutic_area)
                RETURN DISTINCT trial.nct_id AS nct_id
                """,
                patient_conditions=patient.conditions,
                therapeutic_area=patient.therapeutic_area,
            )
            nct_ids_a: set[str] = {record["nct_id"] async for record in result_a}

            # Query B — trial→condition→SNOMED path (catches trials with no parsed criteria)
            result_b = await session.run(
                """
                MATCH (trial:Trial)-[:STUDIES_CONDITION]->(c:Condition)-[:MAPS_TO_SNOMED]->(sc:SNOMEDConcept)
                WHERE (
                    sc.concept_id IN $patient_conditions
                    OR EXISTS {
                        MATCH (pc:SNOMEDConcept)-[:IS_A*1..3]->(sc)
                        WHERE pc.concept_id IN $patient_conditions
                    }
                )
                AND trial.status IN ['RECRUITING', 'ACTIVE_NOT_RECRUITING']
                AND ($therapeutic_area IS NULL OR trial.therapeutic_area = $therapeutic_area)
                RETURN DISTINCT trial.nct_id AS nct_id
                """,
                patient_conditions=patient.conditions,
                therapeutic_area=patient.therapeutic_area,
            )
            nct_ids_b: set[str] = {record["nct_id"] async for record in result_b}

        candidates = list(nct_ids_a | nct_ids_b)
        logger.info(
            "Stage 1: %d candidates from SNOMED traversal (%d via criteria, %d via condition nodes)",
            len(candidates),
            len(nct_ids_a),
            len(nct_ids_b),
        )
        return candidates

    # ------------------------------------------------------------------
    # Stage 2 — Hard exclusion filter
    # ------------------------------------------------------------------

    async def apply_exclusions(
        self,
        patient: PatientProfile,
        candidate_nct_ids: list[str],
    ) -> list[str]:
        """Hard-filter candidates that fail any definitive exclusion criterion.

        Checks (in order):
          1. Age window — trial.min_age ≤ patient.age ≤ trial.max_age
          2. Gender     — trial.gender ∈ {"All", "ALL"} or matches patient
          3. Excluded conditions via :EXCLUDES_CONDITION edges on criteria
          4. Excluded drugs via :EXCLUDES_PRIOR_DRUG edges on criteria
          5. Excluded biomarkers via :EXCLUDES_BIOMARKER edges on criteria

        Returns:
            Subset of ``candidate_nct_ids`` that passed all exclusion checks.
        """
        if not candidate_nct_ids:
            return []

        biomarker_map = patient.biomarker_map()
        passing: list[str] = []

        async with self._driver.session(database=self._database) as session:
            for nct_id in candidate_nct_ids:
                if not await self._passes_exclusions(
                    session, nct_id, patient, biomarker_map
                ):
                    continue
                passing.append(nct_id)

        logger.info(
            "Stage 2: %d / %d candidates survived exclusion filter",
            len(passing),
            len(candidate_nct_ids),
        )
        return passing

    async def _passes_exclusions(
        self,
        session: Any,
        nct_id: str,
        patient: PatientProfile,
        biomarker_map: dict[str, str],
    ) -> bool:
        """Return False if the patient is excluded from this trial."""
        # --- Fetch trial demographics ---
        meta_result = await session.run(
            """
            MATCH (t:Trial {nct_id: $nct_id})
            RETURN t.min_age AS min_age,
                   t.max_age AS max_age,
                   t.gender  AS gender
            """,
            nct_id=nct_id,
        )
        meta = await meta_result.single()
        if meta is None:
            return False  # Trial not found — exclude

        # Age check
        if patient.age is not None:
            if meta["min_age"] is not None and patient.age < meta["min_age"]:
                return False
            if meta["max_age"] is not None and patient.age > meta["max_age"]:
                return False

        # Gender check
        trial_gender = (meta["gender"] or "All").upper()
        if trial_gender not in ("ALL", "") and patient.gender:
            if patient.gender.upper() != trial_gender:
                return False

        # --- Excluded conditions ---
        if patient.conditions:
            excl_cond = await session.run(
                """
                MATCH (t:Trial {nct_id: $nct_id})-[:HAS_CRITERION {type: 'exclusion'}]->(cr:Criterion)
                MATCH (cr)-[:EXCLUDES_CONDITION]->(sc:SNOMEDConcept)
                WHERE sc.concept_id IN $patient_conditions
                RETURN count(sc) AS cnt
                """,
                nct_id=nct_id,
                patient_conditions=patient.conditions,
            )
            row = await excl_cond.single()
            if row and row["cnt"] > 0:
                return False

        # --- Excluded drugs ---
        if patient.prior_therapies:
            excl_drug = await session.run(
                """
                MATCH (t:Trial {nct_id: $nct_id})-[:HAS_CRITERION {type: 'exclusion'}]->(cr:Criterion)
                MATCH (cr)-[:EXCLUDES_PRIOR_DRUG]->(rx:RxNormConcept)
                WHERE rx.rxcui IN $patient_therapies
                RETURN count(rx) AS cnt
                """,
                nct_id=nct_id,
                patient_therapies=patient.prior_therapies,
            )
            row = await excl_drug.single()
            if row and row["cnt"] > 0:
                return False

        # --- Excluded biomarkers ---
        if biomarker_map:
            excl_bio = await session.run(
                """
                MATCH (t:Trial {nct_id: $nct_id})-[:HAS_CRITERION {type: 'exclusion'}]->(cr:Criterion)
                MATCH (cr)-[:EXCLUDES_BIOMARKER {status: $bm_status}]->(b:Biomarker)
                WHERE b.normalized_name IN $bm_names
                RETURN count(b) AS cnt
                """,
                nct_id=nct_id,
                bm_names=list(biomarker_map.keys()),
                # Pass a comma-joined status string; checked per-biomarker below
                bm_status="",  # placeholder — real check is per-entry below
            )
            # More precise: check each biomarker individually
            for bm_name, bm_status in biomarker_map.items():
                check = await session.run(
                    """
                    MATCH (t:Trial {nct_id: $nct_id})-[:HAS_CRITERION {type: 'exclusion'}]->(cr:Criterion)
                    MATCH (cr)-[rel:EXCLUDES_BIOMARKER]->(b:Biomarker)
                    WHERE b.normalized_name = $bm_name AND rel.status = $bm_status
                    RETURN count(b) AS cnt
                    """,
                    nct_id=nct_id,
                    bm_name=bm_name,
                    bm_status=bm_status,
                )
                row = await check.single()
                if row and row["cnt"] > 0:
                    return False

        return True

    # ------------------------------------------------------------------
    # Stage 3 — Inclusion scoring
    # ------------------------------------------------------------------

    async def score_trials(
        self,
        patient: PatientProfile,
        filtered_nct_ids: list[str],
    ) -> list[dict]:
        """Score each candidate trial 0–100 against patient inclusion criteria.

        For each trial, fetches:
          - Required conditions (with IS_A match type resolved during query)
          - Required biomarkers
          - Required prior therapies
          - Trial metadata (for demographics + quality scoring)

        Returns:
            List of dicts sorted descending by score, each containing:
            {nct_id, score, score_breakdown, matched_criteria, unmatched_criteria}
        """
        if not filtered_nct_ids:
            return []

        scored: list[dict] = []
        biomarker_map = patient.biomarker_map()

        async with self._driver.session(database=self._database) as session:
            for nct_id in filtered_nct_ids:
                result = await self._score_one_trial(
                    session, nct_id, patient, biomarker_map
                )
                scored.append(result)

        scored.sort(key=lambda x: x["score"], reverse=True)
        logger.info("Stage 3: scored %d trials", len(scored))
        return scored

    async def _score_one_trial(
        self,
        session: Any,
        nct_id: str,
        patient: PatientProfile,
        biomarker_map: dict[str, str],
    ) -> dict:
        """Compute a full scored result for a single trial."""

        # --- Trial metadata ---
        meta_result = await session.run(
            """
            MATCH (t:Trial {nct_id: $nct_id})
            RETURN t.min_age    AS min_age,
                   t.max_age    AS max_age,
                   t.gender     AS gender,
                   t.phase      AS phase,
                   t.status     AS status,
                   t.enrollment AS enrollment
            """,
            nct_id=nct_id,
        )
        meta_record = await meta_result.single()
        trial_meta: dict = dict(meta_record) if meta_record else {}

        # --- Required conditions with IS_A match resolution ---
        req_cond_result = await session.run(
            """
            MATCH (t:Trial {nct_id: $nct_id})-[:HAS_CRITERION {type: 'inclusion'}]->(cr:Criterion)
            MATCH (cr)-[:REQUIRES_CONDITION]->(sc:SNOMEDConcept)
            OPTIONAL MATCH (pc:SNOMEDConcept)-[:IS_A*0..3]->(sc)
            WHERE pc.concept_id IN $patient_conditions
            RETURN sc.concept_id AS concept_id,
                   sc.term       AS term,
                   CASE
                     WHEN sc.concept_id IN $patient_conditions THEN 'direct'
                     WHEN pc IS NOT NULL                       THEN 'ancestor'
                     ELSE 'none'
                   END AS match_type
            """,
            nct_id=nct_id,
            patient_conditions=patient.conditions,
        )
        required_conditions: list[dict] = [dict(r) async for r in req_cond_result]

        # Descendant check: trial requires concept that IS_A of patient condition
        for req in required_conditions:
            if req["match_type"] == "none" and patient.conditions:
                desc_check = await session.run(
                    """
                    MATCH (sc:SNOMEDConcept {concept_id: $req_cid})-[:IS_A*1..3]->(anc:SNOMEDConcept)
                    WHERE anc.concept_id IN $patient_conditions
                    RETURN count(anc) AS cnt
                    """,
                    req_cid=req["concept_id"],
                    patient_conditions=patient.conditions,
                )
                row = await desc_check.single()
                if row and row["cnt"] > 0:
                    req["match_type"] = "descendant"

        # --- Required biomarkers ---
        req_bio_result = await session.run(
            """
            MATCH (t:Trial {nct_id: $nct_id})-[:HAS_CRITERION {type: 'inclusion'}]->(cr:Criterion)
            MATCH (cr)-[rel:REQUIRES_BIOMARKER]->(b:Biomarker)
            RETURN b.normalized_name AS name,
                   rel.status        AS status
            """,
            nct_id=nct_id,
        )
        required_biomarkers: list[dict] = [dict(r) async for r in req_bio_result]

        # --- Required prior therapies ---
        req_therapy_result = await session.run(
            """
            MATCH (t:Trial {nct_id: $nct_id})-[:HAS_CRITERION {type: 'inclusion'}]->(cr:Criterion)
            MATCH (cr)-[:REQUIRES_PRIOR_DRUG]->(rx:RxNormConcept)
            RETURN rx.rxcui AS rxcui,
                   rx.name  AS name
            """,
            nct_id=nct_id,
        )
        required_therapies: list[dict] = [dict(r) async for r in req_therapy_result]

        # --- Sub-scores ---
        cond_result = score_conditions(patient.conditions, required_conditions)
        bio_result = score_biomarkers(biomarker_map, required_biomarkers)
        therapy_result = score_prior_therapies(patient.prior_therapies, required_therapies)
        demo_result = score_demographics(patient, trial_meta)
        quality_result = score_trial_quality(trial_meta)

        composite = compute_total_score(
            cond_result, bio_result, therapy_result, demo_result, quality_result
        )

        # Collect matched / unmatched criteria for explanation layer
        matched_criteria = (
            cond_result[2].get("matched", [])
            + bio_result[2].get("matched", [])
            + therapy_result[2].get("matched", [])
        )
        unmatched_criteria = (
            cond_result[2].get("unmatched", [])
            + bio_result[2].get("unmatched", [])
            + therapy_result[2].get("unmatched", [])
        )

        return {
            "nct_id": nct_id,
            "score": composite["score"],
            "score_breakdown": composite["breakdown"],
            "matched_criteria": matched_criteria,
            "unmatched_criteria": unmatched_criteria,
        }

    # ------------------------------------------------------------------
    # Stage 4 — Orchestration
    # ------------------------------------------------------------------

    async def match(
        self,
        patient: PatientProfile,
        top_n: int = 10,
    ) -> list[dict]:
        """Run all four matching stages and return the top N ranked trials.

        Pipeline:
          Stage 1 → find_candidate_trials   (SNOMED graph traversal)
          Stage 2 → apply_exclusions        (hard demographic + exclusion filter)
          Stage 3 → score_trials            (0-100 inclusion score)
          Stage 4 → enrich top N with full trial metadata

        Returns:
            List of MatchResult dicts (at most ``top_n``), sorted by score desc:
            {
                nct_id, score, score_breakdown,
                matched_criteria, unmatched_criteria,
                trial: {title, status, phase, sponsor, url, ...}
            }
        """
        # Stage 1
        candidates = await self.find_candidate_trials(patient)
        if not candidates:
            logger.info("No candidates found for patient profile")
            return []

        # Stage 2
        filtered = await self.apply_exclusions(patient, candidates)
        if not filtered:
            logger.info("All candidates excluded after Stage 2")
            return []

        # Stage 3
        scored = await self.score_trials(patient, filtered)

        # Stage 4 — enrich top N with full trial metadata
        top = scored[:top_n]
        top_nct_ids = [r["nct_id"] for r in top]
        trial_metadata = await self._fetch_trial_metadata(top_nct_ids)

        results: list[dict] = []
        for match_result in top:
            nct_id = match_result["nct_id"]
            results.append({
                **match_result,
                "trial": trial_metadata.get(nct_id, {}),
            })

        logger.info(
            "match() complete: %d candidates → %d filtered → returning top %d",
            len(candidates),
            len(filtered),
            len(results),
        )
        return results

    # ------------------------------------------------------------------
    # Metadata helper
    # ------------------------------------------------------------------

    async def _fetch_trial_metadata(self, nct_ids: list[str]) -> dict[str, dict]:
        """Fetch full display metadata for a list of NCT IDs.

        Returns:
            {nct_id: {title, status, phase, sponsor, url, enrollment,
                      min_age, max_age, gender, therapeutic_area,
                      conditions: [...], interventions: [...]}}
        """
        if not nct_ids:
            return {}

        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                UNWIND $nct_ids AS nct_id
                MATCH (t:Trial {nct_id: nct_id})
                OPTIONAL MATCH (t)-[:STUDIES_CONDITION]->(c:Condition)
                OPTIONAL MATCH (t)-[:USES_INTERVENTION]->(i:Intervention)
                RETURN t.nct_id              AS nct_id,
                       t.title               AS title,
                       t.brief_summary       AS brief_summary,
                       t.status              AS status,
                       t.phase               AS phase,
                       t.sponsor             AS sponsor,
                       t.url                 AS url,
                       t.enrollment          AS enrollment,
                       t.min_age             AS min_age,
                       t.max_age             AS max_age,
                       t.gender              AS gender,
                       t.therapeutic_area    AS therapeutic_area,
                       collect(DISTINCT c.name) AS conditions,
                       collect(DISTINCT {name: i.name, type: i.type}) AS interventions
                """,
                nct_ids=nct_ids,
            )
            metadata: dict[str, dict] = {}
            async for record in result:
                nct_id = record["nct_id"]
                metadata[nct_id] = {
                    "nct_id": nct_id,
                    "title": record["title"],
                    "brief_summary": record["brief_summary"],
                    "status": record["status"],
                    "phase": record["phase"],
                    "sponsor": record["sponsor"],
                    "url": record["url"],
                    "enrollment": record["enrollment"],
                    "min_age": record["min_age"],
                    "max_age": record["max_age"],
                    "gender": record["gender"],
                    "therapeutic_area": record["therapeutic_area"],
                    "conditions": [c for c in record["conditions"] if c],
                    "interventions": [i for i in record["interventions"] if i.get("name")],
                }

        return metadata
