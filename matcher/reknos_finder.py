"""
ReKnoS candidate finder for clinical trial matching.

Implements the ReKnoS framework (ICLR 2025) adapted for patient-to-trial
matching. Replaces the fixed IS_A traversal in Stage 1 of MatchEngine with
an LLM-guided multi-hop reasoning loop over super-relations.

Algorithm (per patient query):
  For each reasoning step 1..L:
    1. Compute applicable super-relations for the current frontier (no LLM)
    2. If candidates > N, ask LLM to score and select the top N
    3. Traverse each selected super-relation to expand the frontier
    4. Collect any Trial nodes found
    5. Optionally ask LLM whether to stop early
  Return the union of all Trial NCT IDs found across all steps.

LLM calls are bounded by 2L (scoring + stop check per step), plus at most 1
extra per step if every step triggers both — i.e. O(L) total, matching the
theoretical bound in the paper.

Reference: "Reasoning over Knowledge Graphs with Super-Relations"
           ReKnoS, ICLR 2025 — https://openreview.net/forum?id=rTCJ29pkuA
"""

from __future__ import annotations

import logging

from llm.provider import LLMProvider
from matcher.patient_schema import PatientProfile
from matcher.super_relations import (
    SUPER_RELATIONS,
    ClinicalTrialsKGInterface,
    EntityRef,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_SCORE_SYSTEM = (
    "You are a clinical trial matching specialist navigating a medical "
    "knowledge graph. Your goal is to find the most relevant reasoning steps "
    "to identify eligible trials for the given patient."
)

_SCORE_USER_TMPL = """\
Patient: {question}
Current reasoning path: {path}

Select the {N} most useful next steps from the following options:
{options}

Reply with exactly {N} relation names (one per line), most useful first.
Only output the relation names — no explanations or extra text."""

_STOP_SYSTEM = (
    "You are a clinical trial matching specialist. "
    "Decide whether the trials retrieved so far are sufficient."
)

_STOP_USER_TMPL = """\
Patient: {question}
Reasoning path so far: {path}
Trials found so far ({count}): {trial_list}

Should reasoning stop here, or should we explore further to find more relevant trials?
Reply with exactly one word: STOP or CONTINUE."""


class ReKnoSCandidateFinder:
    """LLM-guided multi-hop candidate finder using super-relations.

    Designed as a drop-in augmentation for ``MatchEngine.find_candidate_trials``.
    Results are unioned with the existing IS_A Cypher traversal by default.

    Args:
        kg:             KG traversal interface (wraps Neo4j async driver).
        llm:            LLM provider used for relation scoring and stop decisions.
        N:              Number of super-relations selected per step (default: 3).
        L:              Maximum reasoning depth in hops (default: 3).
        use_stop_check: Whether to ask the LLM to stop early once trials are found.
                        Disable to always run all L steps (higher recall, more LLM calls).
    """

    def __init__(
        self,
        kg: ClinicalTrialsKGInterface,
        llm: LLMProvider,
        N: int = 3,
        L: int = 3,
        use_stop_check: bool = True,
    ) -> None:
        self._kg = kg
        self._llm = llm
        self.N = N
        self.L = L
        self.use_stop_check = use_stop_check
        self._llm_calls: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def find_candidates(self, patient: PatientProfile) -> list[str]:
        """Run the ReKnoS reasoning loop and return NCT IDs of candidate trials.

        Collects Trial nodes found at any reasoning depth.
        LLM call count is bounded by 2L.

        Args:
            patient: Structured patient profile with SNOMED/RxNorm concept IDs.

        Returns:
            List of NCT ID strings for candidate trials.
        """
        self._llm_calls = 0
        frontier = self._get_seed_entities(patient)

        if not frontier:
            logger.warning("ReKnoS: patient has no resolvable seed entities — skipping")
            return []

        question = self._build_question(patient)
        all_trial_ids: set[str] = set()
        path: list[str] = []

        for step in range(1, self.L + 1):
            # Step 1 — available super-relations (pure graph operation, no LLM)
            candidate_relations = self._kg.get_applicable_super_relations(frontier, path)
            if not candidate_relations:
                logger.debug("ReKnoS step %d: no applicable super-relations — stopping", step)
                break

            # Step 2 — LLM scores only when there are more candidates than N
            if len(candidate_relations) > self.N:
                selected = await self._score_super_relations(
                    question, candidate_relations, path
                )
            else:
                selected = candidate_relations

            path.extend(selected)
            logger.debug("ReKnoS step %d/%d: selected %s", step, self.L, selected)

            # Step 3 — expand frontier via selected super-relations
            next_frontier: list[EntityRef] = []
            for sr in selected:
                reached = await self._kg.get_entities_via_super_relation(frontier, sr)
                next_frontier.extend(reached)

            if not next_frontier:
                logger.debug("ReKnoS step %d: frontier empty after traversal — stopping", step)
                break

            # Collect Trial nodes found at this step
            trial_ids = self._kg.extract_trial_ids(next_frontier)
            if trial_ids:
                all_trial_ids.update(trial_ids)
                logger.debug(
                    "ReKnoS step %d: found %d new trial(s) (total=%d)",
                    step,
                    len(trial_ids),
                    len(all_trial_ids),
                )

                # Step 4 — optional early-stop check (skip on final step)
                if step < self.L and self.use_stop_check:
                    if await self._should_stop(question, all_trial_ids, path):
                        logger.debug("ReKnoS: LLM decided STOP at step %d", step)
                        break

            frontier = next_frontier

        logger.info(
            "ReKnoS complete: %d candidate trial(s) | LLM calls=%d | path=%s",
            len(all_trial_ids),
            self._llm_calls,
            " → ".join(path) or "(none)",
        )
        return list(all_trial_ids)

    @property
    def llm_call_count(self) -> int:
        """Number of LLM calls made during the last ``find_candidates()`` invocation."""
        return self._llm_calls

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_question(self, patient: PatientProfile) -> str:
        """Construct a natural-language question from the PatientProfile."""
        parts: list[str] = []
        if patient.age is not None:
            parts.append(f"{patient.age}-year-old")
        if patient.gender:
            parts.append(patient.gender.lower())
        if patient.condition_names:
            parts.append(f"with {', '.join(patient.condition_names)}")
        if patient.biomarkers:
            bm_str = ", ".join(
                f"{b.name}-{'positive' if b.status == 'positive' else 'negative'}"
                for b in patient.biomarkers
            )
            parts.append(f"biomarkers: {bm_str}")
        if patient.prior_therapy_names:
            parts.append(f"prior therapy: {', '.join(patient.prior_therapy_names)}")
        if patient.ecog_status is not None:
            parts.append(f"ECOG {patient.ecog_status}")
        if patient.therapeutic_area:
            parts.append(f"({patient.therapeutic_area})")
        description = " ".join(parts) or "unknown patient"
        return f"Find recruiting clinical trials for: {description}"

    def _get_seed_entities(self, patient: PatientProfile) -> list[EntityRef]:
        """Convert the PatientProfile into seed EntityRef nodes for graph traversal.

        Seeds come from three sources:
          - SNOMED concept IDs → SNOMEDConcept nodes
          - RxNorm CUIs → RxNormConcept nodes
          - Biomarker names → Biomarker nodes (normalized to lowercase)
        """
        seeds: list[EntityRef] = []

        condition_names = patient.condition_names
        for i, concept_id in enumerate(patient.conditions):
            label = condition_names[i] if i < len(condition_names) else concept_id
            seeds.append(EntityRef(id=concept_id, label=label, node_type="SNOMEDConcept"))

        therapy_names = patient.prior_therapy_names
        for i, rxcui in enumerate(patient.prior_therapies):
            label = therapy_names[i] if i < len(therapy_names) else rxcui
            seeds.append(EntityRef(id=rxcui, label=label, node_type="RxNormConcept"))

        for bm in patient.biomarkers:
            seeds.append(
                EntityRef(
                    id=bm.name.lower(),   # matches Biomarker.normalized_name in KG
                    label=bm.name,
                    node_type="Biomarker",
                )
            )

        return seeds

    async def _score_super_relations(
        self,
        question: str,
        candidates: list[str],
        current_path: list[str],
    ) -> list[str]:
        """Ask the LLM to select the top-N most relevant super-relations.

        Returns at most N relation names. Falls back to the first N candidates
        if the LLM response cannot be parsed.
        """
        path_str = " → ".join(current_path) if current_path else "starting point"
        options_lines = "\n".join(
            f"- {name}: {SUPER_RELATIONS[name]['description']}"
            for name in candidates
            if name in SUPER_RELATIONS
        )
        prompt = _SCORE_USER_TMPL.format(
            question=question,
            path=path_str,
            N=self.N,
            options=options_lines,
        )

        self._llm_calls += 1
        response = await self._llm.complete(
            system_prompt=_SCORE_SYSTEM,
            user_prompt=prompt,
            temperature=0.0,
            max_tokens=200,
        )

        selected = _parse_relation_names(response, candidates)
        if not selected:
            logger.debug("ReKnoS scoring: LLM response unparseable, using first %d", self.N)
            selected = candidates[: self.N]

        return selected[: self.N]

    async def _should_stop(
        self,
        question: str,
        trial_ids: set[str],
        path: list[str],
    ) -> bool:
        """Ask the LLM whether to stop reasoning or continue exploring.

        Returns True if the LLM says STOP.
        """
        trial_list = ", ".join(sorted(trial_ids)[:10])
        if len(trial_ids) > 10:
            trial_list += f" (+{len(trial_ids) - 10} more)"

        prompt = _STOP_USER_TMPL.format(
            question=question,
            path=" → ".join(path),
            count=len(trial_ids),
            trial_list=trial_list,
        )

        self._llm_calls += 1
        response = await self._llm.complete(
            system_prompt=_STOP_SYSTEM,
            user_prompt=prompt,
            temperature=0.0,
            max_tokens=10,
        )
        return response.strip().upper().startswith("STOP")


# ---------------------------------------------------------------------------
# Parse helper
# ---------------------------------------------------------------------------


def _parse_relation_names(response: str, valid_names: list[str]) -> list[str]:
    """Extract valid super-relation names from an LLM text response (one per line)."""
    valid_set = set(valid_names)
    selected: list[str] = []
    for line in response.strip().splitlines():
        name = line.strip().lstrip("- ").strip()
        if name in valid_set and name not in selected:
            selected.append(name)
    return selected
