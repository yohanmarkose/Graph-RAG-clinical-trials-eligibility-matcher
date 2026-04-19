from __future__ import annotations

import asyncio
import json
import logging
import re

from llm.provider import LLMProvider
from matcher.patient_schema import BiomarkerStatus, PatientProfile
from nlp.entity_linker import EntityLinker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

EXPLANATION_SYSTEM_PROMPT = """\
You are a clinical trial eligibility specialist. Your role is to explain, in plain language,
why a patient profile matches or does not fully match a specific clinical trial.

Rules:
- Be concise and structured — use the exact format shown below.
- Use ✓ for criteria the patient satisfies, ✗ for criteria that disqualify the patient,
  and ? for criteria where patient data is missing or unknown.
- Do NOT make medical recommendations. Always end with the disclaimer.
- Keep the Match Summary to 1–2 sentences; include the numeric score.
- List each criterion on its own line in the Inclusion / Exclusion sections.

Required output format:

**Match Summary:** <1–2 sentences describing overall fit, including score X/100>

**Inclusion Criteria:**
✓/✗/? <criterion name> — <brief reason relating it to this patient>
...

**Exclusion Criteria:**
✓/✗ <criterion name> — <brief reason>
...

**Note:** <any caveats about missing data or recommended follow-up>. \
This is a screening tool — consult your care team before making any decisions.\
"""

PATIENT_PARSE_SYSTEM_PROMPT = """\
You are a medical information extractor. Parse the free-text patient description into a
structured JSON object. Extract every available clinical detail.

Return ONLY valid JSON with this exact schema (use null for missing fields):
{
  "age": <integer or null>,
  "gender": <"Male" | "Female" | "Other" | null>,
  "condition_names": [<string>, ...],
  "biomarkers": [{"name": <string>, "status": <"positive" | "negative">}, ...],
  "prior_therapy_names": [<string>, ...],
  "ecog_status": <0-5 integer or null>,
  "therapeutic_area": <"oncology" | "cardiology" | "neurology" | "endocrinology" |
                        "immunology" | "infectious_disease" | "pulmonology" |
                        "psychiatry" | null>
}

Rules:
- Extract ALL mentioned conditions, drugs / therapies, and biomarkers.
- Normalise biomarker status to "positive" or "negative".
- For ECOG, convert plain text (e.g. "ECOG 1", "performance status 2") → integer.
- therapeutic_area: infer from conditions if not stated explicitly (e.g. breast cancer → oncology).
- Return only the JSON object — no markdown, no explanation text.\
"""


# ---------------------------------------------------------------------------
# MatchExplainer
# ---------------------------------------------------------------------------


class MatchExplainer:
    """Generates LLM-powered explanations for trial match results and parses
    free-text patient descriptions into structured PatientProfile objects.
    """

    def __init__(
        self,
        llm: LLMProvider,
        entity_linker: EntityLinker | None = None,
    ) -> None:
        self._llm = llm
        self._linker = entity_linker

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def explain_match(
        self,
        patient: PatientProfile,
        trial: dict,
        match_result: dict,
    ) -> str:
        """Generate a plain-language explanation for a single trial match.

        Args:
            patient: The patient profile being matched.
            trial: Trial metadata dict — typically ``match_result["trial"]``.
            match_result: Full match result dict from ``MatchEngine.match()``.

        Returns:
            Formatted explanation string following the standard template.
        """
        user_prompt = _build_explanation_prompt(patient, trial, match_result)
        try:
            return await self._llm.complete(
                system_prompt=EXPLANATION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.3,
                max_tokens=800,
            )
        except Exception:
            logger.exception(
                "LLM explanation failed for trial %s", match_result.get("nct_id")
            )
            return _fallback_explanation(match_result)

    async def explain_matches(
        self,
        patient: PatientProfile,
        matches: list[dict],
    ) -> list[dict]:
        """Add LLM explanations to all match results in parallel.

        Uses ``asyncio.Semaphore(5)`` to cap concurrent LLM calls and avoid
        rate-limit errors.

        Args:
            patient: The patient profile.
            matches: List of match result dicts from ``MatchEngine.match()``.

        Returns:
            New list with an ``"explanation"`` key added to each dict.
        """
        semaphore = asyncio.Semaphore(5)

        async def _explain_one(match: dict) -> dict:
            async with semaphore:
                trial = match.get("trial", {})
                explanation = await self.explain_match(patient, trial, match)
                return {**match, "explanation": explanation}

        return list(await asyncio.gather(*[_explain_one(m) for m in matches]))

    async def parse_free_text_patient(self, text: str) -> PatientProfile:
        """Parse a free-text clinical description into a PatientProfile.

        Steps:
          1. LLM extracts structured JSON from the narrative text.
          2. Each condition name is linked to a SNOMED concept ID via EntityLinker
             (LLM fallback used when fuzzy matching confidence is below threshold).
          3. Each prior therapy name is linked to an RxNorm CUI via EntityLinker.

        Args:
            text: Free-text patient description (e.g. from a clinician's note).

        Returns:
            Populated PatientProfile. Conditions / therapies that cannot be
            resolved are omitted from the concept-ID lists but kept in the
            ``*_names`` display lists.
        """
        if self._linker is None:
            raise ValueError(
                "parse_free_text_patient requires an EntityLinker. "
                "Pass one to MatchExplainer(llm, entity_linker=EntityLinker())."
            )
        raw = await self._llm.complete(
            system_prompt=PATIENT_PARSE_SYSTEM_PROMPT,
            user_prompt=text,
            temperature=0.0,
            max_tokens=600,
        )
        parsed = _parse_json_response(raw)
        return await self._resolve_entities(parsed)

    # ------------------------------------------------------------------
    # Private — entity resolution
    # ------------------------------------------------------------------

    async def _resolve_entities(self, parsed: dict) -> PatientProfile:
        """Resolve free-text names to SNOMED / RxNorm concept IDs."""
        condition_names: list[str] = parsed.get("condition_names") or []
        therapy_names: list[str] = parsed.get("prior_therapy_names") or []

        # --- Conditions → SNOMED ---
        conditions: list[str] = []
        for name in condition_names:
            hits = self._linker.link_condition_to_snomed(name)
            if hits:
                conditions.append(hits[0]["concept_id"])
            else:
                fallback = await self._linker.link_with_llm_fallback(
                    name, "condition", self._llm
                )
                if fallback:
                    conditions.append(fallback["concept_id"])
                else:
                    logger.debug("Could not resolve condition to SNOMED: %s", name)

        # --- Prior therapies → RxNorm ---
        prior_therapies: list[str] = []
        for name in therapy_names:
            hits = self._linker.link_drug_to_rxnorm(name)
            if hits:
                prior_therapies.append(hits[0]["rxcui"])
            else:
                fallback = await self._linker.link_with_llm_fallback(
                    name, "drug", self._llm
                )
                if fallback:
                    prior_therapies.append(fallback["rxcui"])
                else:
                    logger.debug("Could not resolve drug to RxNorm: %s", name)

        # --- Biomarkers ---
        biomarkers: list[BiomarkerStatus] = []
        for bm in parsed.get("biomarkers") or []:
            if isinstance(bm, dict) and bm.get("name") and bm.get("status"):
                biomarkers.append(
                    BiomarkerStatus(name=bm["name"], status=bm["status"])
                )

        return PatientProfile(
            age=parsed.get("age"),
            gender=parsed.get("gender"),
            conditions=conditions,
            condition_names=condition_names,
            biomarkers=biomarkers,
            prior_therapies=prior_therapies,
            prior_therapy_names=therapy_names,
            ecog_status=parsed.get("ecog_status"),
            therapeutic_area=parsed.get("therapeutic_area"),
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _build_explanation_prompt(
    patient: PatientProfile,
    trial: dict,
    match_result: dict,
) -> str:
    """Construct the user-turn prompt for the explanation LLM call."""
    lines: list[str] = []

    # --- Patient summary ---
    lines.append("## Patient Profile")
    lines.append(f"- Age: {patient.age if patient.age is not None else 'Unknown'}")
    lines.append(f"- Gender: {patient.gender or 'Unknown'}")
    if patient.condition_names:
        lines.append(f"- Conditions: {', '.join(patient.condition_names)}")
    elif patient.conditions:
        lines.append(f"- Condition IDs (SNOMED): {', '.join(patient.conditions)}")
    if patient.biomarkers:
        bm_str = ", ".join(f"{b.name} {b.status}" for b in patient.biomarkers)
        lines.append(f"- Biomarkers: {bm_str}")
    if patient.prior_therapy_names:
        lines.append(f"- Prior therapies: {', '.join(patient.prior_therapy_names)}")
    elif patient.prior_therapies:
        lines.append(f"- Prior therapy IDs (RxNorm): {', '.join(patient.prior_therapies)}")
    if patient.ecog_status is not None:
        lines.append(f"- ECOG status: {patient.ecog_status}")
    lines.append("")

    # --- Trial summary ---
    lines.append("## Trial Information")
    lines.append(f"- NCT ID: {trial.get('nct_id', 'Unknown')}")
    lines.append(f"- Title: {trial.get('title', 'Unknown')}")
    lines.append(f"- Phase: {trial.get('phase', 'Unknown')}")
    lines.append(f"- Status: {trial.get('status', 'Unknown')}")
    lines.append(f"- Sponsor: {trial.get('sponsor', 'Unknown')}")
    if trial.get("conditions"):
        lines.append(f"- Conditions studied: {', '.join(trial['conditions'])}")
    if trial.get("interventions"):
        inv_names = [i.get("name", "") for i in trial["interventions"] if i.get("name")]
        if inv_names:
            lines.append(f"- Interventions: {', '.join(inv_names)}")
    min_age = trial.get("min_age")
    max_age = trial.get("max_age")
    lines.append(
        f"- Age range: {min_age if min_age is not None else '?'}–"
        f"{max_age if max_age is not None else '?'} years"
    )
    lines.append(f"- Gender eligibility: {trial.get('gender', 'All')}")
    if trial.get("brief_summary"):
        lines.append(f"- Brief summary: {trial['brief_summary'][:300]}")
    lines.append("")

    # --- Overall score ---
    lines.append("## Match Score")
    lines.append(f"Overall: {match_result.get('score', 0):.1f} / 100")
    lines.append("")

    # --- Score breakdown ---
    breakdown = match_result.get("score_breakdown", {})
    if breakdown:
        lines.append("## Score Breakdown")
        for category, data in breakdown.items():
            earned = data.get("earned", 0)
            possible = data.get("possible", 0)
            lines.append(
                f"- {category.replace('_', ' ').title()}: {earned}/{possible} pts"
            )
        lines.append("")

    # --- Matched criteria details ---
    matched = match_result.get("matched_criteria", [])
    if matched:
        lines.append("## Matched Criteria")
        for c in matched:
            lines.append(f"  - {_format_criterion(c)}")
        lines.append("")

    # --- Unmatched / unknown criteria ---
    unmatched = match_result.get("unmatched_criteria", [])
    if unmatched:
        lines.append("## Unmatched / Unknown Criteria")
        for c in unmatched:
            lines.append(f"  - {_format_criterion(c)}")
        lines.append("")

    # --- Demographics detail ---
    demo_detail = breakdown.get("demographics", {}).get("detail", {})
    if demo_detail:
        lines.append("## Demographics Check")
        lines.append(f"- Age: {demo_detail.get('age', 'unknown')}")
        lines.append(f"- Gender: {demo_detail.get('gender', 'unknown')}")
        lines.append("")

    lines.append(
        "Using the information above, generate a structured explanation "
        "following the required output format."
    )
    return "\n".join(lines)


def _parse_json_response(raw: str) -> dict:
    """Strip markdown fences if present and parse JSON from an LLM response."""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning(
            "LLM returned non-JSON for patient parse; using empty profile. "
            "Raw response (first 200 chars): %s",
            cleaned[:200],
        )
        return {}


def _fallback_explanation(match_result: dict) -> str:
    """Return a minimal explanation when the LLM call fails."""
    score = match_result.get("score", 0)
    nct_id = match_result.get("nct_id", "Unknown")
    n_matched = len(match_result.get("matched_criteria", []))
    n_unmatched = len(match_result.get("unmatched_criteria", []))
    return (
        f"**Match Summary:** Trial {nct_id} received a score of {score:.1f}/100 "
        f"({n_matched} criteria matched, {n_unmatched} not matched or unknown).\n\n"
        "**Note:** Detailed explanation unavailable — LLM service error. "
        "This is a screening tool — consult your care team before making any decisions."
    )


def _format_criterion(criterion: dict) -> str:
    """Format a single criterion dict (from matched/unmatched lists) into a readable string."""
    # Condition criterion (has SNOMED term)
    if "term" in criterion:
        concept = criterion.get("concept_id", "?")
        return f"Condition: {criterion['term']} (SNOMED {concept})"
    # Biomarker criterion
    if "name" in criterion and "status" in criterion:
        patient_status = criterion.get("patient_status", "unknown")
        return (
            f"Biomarker: {criterion['name']} required={criterion['status']} "
            f"(patient: {patient_status})"
        )
    # Drug / prior therapy criterion
    if "rxcui" in criterion:
        name = criterion.get("name", criterion["rxcui"])
        return f"Prior therapy: {name} (RxNorm {criterion['rxcui']})"
    # Fallback
    return str(criterion)
