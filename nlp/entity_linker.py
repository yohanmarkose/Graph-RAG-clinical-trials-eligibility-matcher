"""
Entity linking pipeline: maps free-text condition and drug names to
SNOMED CT and RxNorm concepts.

Lookup files (produced by Phase 3 parsers):
  data/processed/snomed_synonym_lookup.json
  data/processed/rxnorm_synonym_lookup.json

Expected lookup format:
  SNOMED:  { "<lowercased_term>": {"concept_id": "...", "term": "...", "semantic_tag": "..."}, ... }
  RxNorm:  { "<lowercased_term>": {"rxcui": "...",      "name": "...", "tty": "..."},          ... }

Linking strategy (per entity):
  1. Exact match against lowercased key
  2. RapidFuzz WRatio fuzzy match with configurable score threshold
  3. LLM fallback when fuzzy confidence is below threshold
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz
from rapidfuzz import process as fuzz_process

from llm.provider import LLMProvider

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_SNOMED_LOOKUP = _BASE_DIR / "data" / "processed" / "snomed_synonym_lookup.json"
_DEFAULT_RXNORM_LOOKUP = _BASE_DIR / "data" / "processed" / "rxnorm_synonym_lookup.json"


class EntityLinker:
    """Links free-text condition and drug names to SNOMED CT and RxNorm concepts.

    The linker loads synonym dictionaries once at construction time and
    pre-builds key lists for efficient rapidfuzz batch queries.

    Example::

        linker = EntityLinker()
        hits = linker.link_condition_to_snomed("HER2-positive breast cancer")
        # [{"concept_id": "254837009", "term": "Breast cancer", "score": 92.3, "method": "fuzzy"}]
    """

    def __init__(
        self,
        snomed_lookup_path: str | Path = _DEFAULT_SNOMED_LOOKUP,
        rxnorm_lookup_path: str | Path = _DEFAULT_RXNORM_LOOKUP,
    ) -> None:
        self._snomed: dict[str, dict] = self._load_lookup(snomed_lookup_path, "SNOMED")
        self._rxnorm: dict[str, dict] = self._load_lookup(rxnorm_lookup_path, "RxNorm")

        # Pre-build term lists for rapidfuzz — avoids re-allocating per call.
        # For 100K+ synonym lists this is the key optimisation.
        self._snomed_terms: list[str] = list(self._snomed.keys())
        self._rxnorm_terms: list[str] = list(self._rxnorm.keys())

        logger.info(
            "EntityLinker ready: %d SNOMED terms, %d RxNorm terms",
            len(self._snomed_terms),
            len(self._rxnorm_terms),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_lookup(path: str | Path, label: str) -> dict[str, dict]:
        """Load a synonym lookup JSON file; return empty dict if missing."""
        p = Path(path)
        if not p.exists():
            logger.warning(
                "%s lookup not found at %s — entity linking will be degraded", label, p
            )
            return {}
        data: dict[str, dict] = json.loads(p.read_text(encoding="utf-8"))
        logger.info("Loaded %s lookup: %d entries from %s", label, len(data), p)
        return data

    @staticmethod
    def _normalise(text: str) -> str:
        return text.lower().strip()

    # ------------------------------------------------------------------
    # Public sync linking methods
    # ------------------------------------------------------------------

    def link_condition_to_snomed(
        self,
        condition_name: str,
        threshold: float = 85.0,
    ) -> list[dict]:
        """Map a condition string to up to 3 SNOMED CT concepts.

        Returns a list of dicts: {concept_id, term, score, method}.
        An exact match returns a single-element list immediately (score=100).
        """
        if not self._snomed:
            return []

        normalised = self._normalise(condition_name)

        # Step 1 — exact match
        if normalised in self._snomed:
            entry = self._snomed[normalised]
            concept_id = entry[0] if isinstance(entry, list) else entry.get("concept_id", "")
            return [
                {
                    "concept_id": concept_id,
                    "term": normalised,
                    "score": 100.0,
                    "method": "exact",
                }
            ]

        # Step 2 — fuzzy match
        fuzzy_hits = fuzz_process.extract(
            normalised,
            self._snomed_terms,
            scorer=fuzz.WRatio,
            score_cutoff=threshold,
            limit=3,
        )
        results = []
        for term, score, _ in fuzzy_hits:
            entry = self._snomed[term]
            concept_id = entry[0] if isinstance(entry, list) else entry.get("concept_id", "")
            results.append({
                "concept_id": concept_id,
                "term": term,
                "score": float(score),
                "method": "fuzzy",
            })
        return results

    def link_drug_to_rxnorm(
        self,
        drug_name: str,
        threshold: float = 85.0,
    ) -> list[dict]:
        """Map a drug / intervention name to up to 3 RxNorm concepts.

        Returns a list of dicts: {rxcui, name, score, method}.
        """
        if not self._rxnorm:
            return []

        normalised = self._normalise(drug_name)

        # Step 1 — exact match
        if normalised in self._rxnorm:
            entry = self._rxnorm[normalised]
            rxcui = entry[0] if isinstance(entry, list) else entry.get("rxcui", "")
            return [
                {
                    "rxcui": rxcui,
                    "name": normalised,
                    "score": 100.0,
                    "method": "exact",
                }
            ]

        # Step 2 — fuzzy match
        fuzzy_hits = fuzz_process.extract(
            normalised,
            self._rxnorm_terms,
            scorer=fuzz.WRatio,
            score_cutoff=threshold,
            limit=3,
        )
        results = []
        for term, score, _ in fuzzy_hits:
            entry = self._rxnorm[term]
            rxcui = entry[0] if isinstance(entry, list) else entry.get("rxcui", "")
            results.append({
                "rxcui": rxcui,
                "name": term,
                "score": float(score),
                "method": "fuzzy",
            })
        return results

    # ------------------------------------------------------------------
    # LLM fallback
    # ------------------------------------------------------------------

    async def link_with_llm_fallback(
        self,
        text: str,
        entity_type: str,
        llm: LLMProvider,
    ) -> dict | None:
        """Use the LLM to suggest a concept name when fuzzy confidence is low.

        entity_type: "condition" (→ SNOMED) or "drug" (→ RxNorm).
        The LLM is asked for a canonical concept name; that name is then
        re-run through the synonym lookup at a relaxed threshold (70).

        Returns the best match dict (with method="llm_fallback") or None.
        """
        if entity_type == "condition":
            system = (
                "You are a medical terminology expert. "
                "Given a clinical condition name, return the most likely SNOMED CT concept. "
                'Respond with ONLY valid JSON in this exact format: '
                '{"concept_name": "<canonical SNOMED term>", "concept_id_hint": "<SNOMED concept ID if known or empty string>"}'
            )
            user = f"What is the most likely SNOMED CT concept for '{text}'?"
        else:
            system = (
                "You are a pharmaceutical terminology expert. "
                "Given a drug or intervention name, return the most likely RxNorm concept. "
                'Respond with ONLY valid JSON in this exact format: '
                '{"concept_name": "<canonical RxNorm name>", "rxcui_hint": "<RxCUI if known or empty string>"}'
            )
            user = f"What is the most likely RxNorm concept for '{text}'?"

        try:
            raw = await llm.complete(system, user, temperature=0.0, max_tokens=200)
            parsed: dict = json.loads(raw.strip())
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("LLM fallback JSON parse error for '%s': %s", text, exc)
            return None

        candidate: str = parsed.get("concept_name", "").strip()
        if not candidate:
            return None

        # Re-run the candidate through the synonym lookup at a relaxed threshold
        if entity_type == "condition":
            hits = self.link_condition_to_snomed(candidate, threshold=70.0)
            if hits:
                result = dict(hits[0])
                result["method"] = "llm_fallback"
                return result
        else:
            hits = self.link_drug_to_rxnorm(candidate, threshold=70.0)
            if hits:
                result = dict(hits[0])
                result["method"] = "llm_fallback"
                return result

        return None

    # ------------------------------------------------------------------
    # Snowflake bulk linking
    # ------------------------------------------------------------------

    async def link_all_conditions_in_snowflake(
        self,
        sf_conn: Any,
        llm: LLMProvider,
        therapeutic_area: str,
    ) -> None:
        """Link every NULL-SNOMED condition in CLEAN.TRIAL_CONDITIONS.

        For each unresolved condition_normalized:
          1. Exact match
          2. Fuzzy match (threshold 85)
          3. LLM fallback
        On success: UPDATE row with concept info + INSERT into TRACKING.ENTITY_LINKING_PROGRESS.
        """
        cursor = sf_conn.cursor()
        try:
            cursor.execute(
                """
                SELECT DISTINCT condition_normalized
                FROM CLEAN.TRIAL_CONDITIONS
                WHERE snomed_concept_id IS NULL
                  AND therapeutic_area = %s
                ORDER BY condition_normalized
                """,
                (therapeutic_area,),
            )
            conditions: list[str] = [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()

        total = len(conditions)
        linked_count = 0
        logger.info(
            "Linking %d unresolved conditions for therapeutic area '%s'",
            total,
            therapeutic_area,
        )

        for condition in conditions:
            hits = self.link_condition_to_snomed(condition)

            if not hits or hits[0]["score"] < 85.0:
                fallback = await self.link_with_llm_fallback(condition, "condition", llm)
                if fallback:
                    hits = [fallback]

            if hits:
                best = hits[0]
                upd = sf_conn.cursor()
                try:
                    upd.execute(
                        """
                        UPDATE CLEAN.TRIAL_CONDITIONS
                        SET snomed_concept_id = %s,
                            snomed_term       = %s,
                            linking_method    = %s,
                            linking_score     = %s
                        WHERE condition_normalized = %s
                          AND therapeutic_area = %s
                          AND snomed_concept_id IS NULL
                        """,
                        (
                            best.get("concept_id"),
                            best.get("term"),
                            best.get("method"),
                            best.get("score"),
                            condition,
                            therapeutic_area,
                        ),
                    )
                    upd.execute(
                        """
                        INSERT INTO TRACKING.ENTITY_LINKING_PROGRESS
                            (entity_text, entity_type, therapeutic_area,
                             concept_id, concept_term, linking_method, linking_score, linked_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP())
                        """,
                        (
                            condition,
                            "condition",
                            therapeutic_area,
                            best.get("concept_id"),
                            best.get("term"),
                            best.get("method"),
                            best.get("score"),
                        ),
                    )
                    sf_conn.commit()
                    linked_count += 1
                finally:
                    upd.close()

        print(
            f"Conditions linked: {linked_count} / {total} "
            f"for therapeutic area '{therapeutic_area}'"
        )

    async def link_all_interventions_in_snowflake(
        self,
        sf_conn: Any,
        llm: LLMProvider,
        therapeutic_area: str,
    ) -> None:
        """Link every NULL-RxNorm intervention in CLEAN.TRIAL_INTERVENTIONS.

        Same three-step strategy as conditions (exact → fuzzy → LLM fallback).
        On success: UPDATE row + INSERT into TRACKING.ENTITY_LINKING_PROGRESS.
        """
        cursor = sf_conn.cursor()
        try:
            cursor.execute(
                """
                SELECT DISTINCT intervention_normalized
                FROM CLEAN.TRIAL_INTERVENTIONS
                WHERE rxnorm_concept_id IS NULL
                  AND therapeutic_area = %s
                ORDER BY intervention_normalized
                """,
                (therapeutic_area,),
            )
            interventions: list[str] = [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()

        total = len(interventions)
        linked_count = 0
        logger.info(
            "Linking %d unresolved interventions for therapeutic area '%s'",
            total,
            therapeutic_area,
        )

        for intervention in interventions:
            hits = self.link_drug_to_rxnorm(intervention)

            if not hits or hits[0]["score"] < 85.0:
                fallback = await self.link_with_llm_fallback(intervention, "drug", llm)
                if fallback:
                    hits = [fallback]

            if hits:
                best = hits[0]
                upd = sf_conn.cursor()
                try:
                    upd.execute(
                        """
                        UPDATE CLEAN.TRIAL_INTERVENTIONS
                        SET rxnorm_concept_id = %s,
                            rxnorm_term       = %s,
                            linking_method    = %s,
                            linking_score     = %s
                        WHERE intervention_normalized = %s
                          AND therapeutic_area = %s
                          AND rxnorm_concept_id IS NULL
                        """,
                        (
                            best.get("rxcui"),
                            best.get("name"),
                            best.get("method"),
                            best.get("score"),
                            intervention,
                            therapeutic_area,
                        ),
                    )
                    upd.execute(
                        """
                        INSERT INTO TRACKING.ENTITY_LINKING_PROGRESS
                            (entity_text, entity_type, therapeutic_area,
                             concept_id, concept_term, linking_method, linking_score, linked_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP())
                        """,
                        (
                            intervention,
                            "drug",
                            therapeutic_area,
                            best.get("rxcui"),
                            best.get("name"),
                            best.get("method"),
                            best.get("score"),
                        ),
                    )
                    sf_conn.commit()
                    linked_count += 1
                finally:
                    upd.close()

        print(
            f"Interventions linked: {linked_count} / {total} "
            f"for therapeutic area '{therapeutic_area}'"
        )
