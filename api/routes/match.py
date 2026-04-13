"""POST /match — patient-to-trial matching endpoint."""

from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.dependencies import get_explainer, get_match_engine
from llm.explainer import MatchExplainer
from matcher.match_engine import MatchEngine
from matcher.patient_schema import PatientProfile

logger = logging.getLogger(__name__)
router = APIRouter(tags=["matching"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class TrialMatch(BaseModel):
    nct_id: str
    title: str
    phase: str
    status: str
    sponsor: str
    score: float
    score_breakdown: dict
    matched_criteria: list
    unmatched_criteria: list
    explanation: Optional[str] = None
    url: str


class MatchResponse(BaseModel):
    patient_summary: str
    total_candidates: int
    total_after_exclusions: int
    matches: list[TrialMatch]
    query_time_ms: float
    therapeutic_area: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/match", response_model=MatchResponse)
async def match_patient(
    patient: PatientProfile,
    top_n: int = Query(default=10, ge=1, le=50, description="Max trials to return"),
    include_explanations: bool = Query(
        default=True, description="Generate LLM explanations for each match"
    ),
    therapeutic_area: Optional[str] = Query(
        default=None, description="Override patient's therapeutic area filter"
    ),
    match_engine: MatchEngine = Depends(get_match_engine),
    explainer: MatchExplainer = Depends(get_explainer),
):
    """Match a patient profile against clinical trials in the knowledge graph.

    Returns scored, ranked trials with optional LLM-generated explanations.
    """
    t0 = time.perf_counter()

    # Apply query-param override for therapeutic_area
    if therapeutic_area and not patient.therapeutic_area:
        patient = patient.model_copy(update={"therapeutic_area": therapeutic_area})

    try:
        raw_matches = await match_engine.match(patient, top_n=top_n)
    except Exception:
        logger.exception("MatchEngine.match() failed for patient: %s", patient)
        raise HTTPException(status_code=500, detail="Matching engine error — check server logs")

    if include_explanations and raw_matches:
        try:
            raw_matches = await explainer.explain_matches(patient, raw_matches)
        except Exception:
            logger.warning(
                "Explanation generation failed; returning matches without explanations"
            )

    matches: list[TrialMatch] = []
    for m in raw_matches:
        trial = m.get("trial", {})
        nct_id = m["nct_id"]
        matches.append(
            TrialMatch(
                nct_id=nct_id,
                title=trial.get("title") or "Unknown",
                phase=trial.get("phase") or "Unknown",
                status=trial.get("status") or "Unknown",
                sponsor=trial.get("sponsor") or "Unknown",
                score=m["score"],
                score_breakdown=m.get("score_breakdown", {}),
                matched_criteria=m.get("matched_criteria", []),
                unmatched_criteria=m.get("unmatched_criteria", []),
                explanation=m.get("explanation"),
                url=trial.get("url") or f"https://clinicaltrials.gov/study/{nct_id}",
            )
        )

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    n = len(matches)

    logger.info(
        "POST /match → %d matches in %.1f ms (patient: %s)",
        n,
        elapsed_ms,
        _summarise_patient(patient),
    )

    return MatchResponse(
        patient_summary=_summarise_patient(patient),
        total_candidates=n,
        total_after_exclusions=n,
        matches=matches,
        query_time_ms=elapsed_ms,
        therapeutic_area=patient.therapeutic_area,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summarise_patient(patient: PatientProfile) -> str:
    """One-line human-readable summary of the patient profile."""
    parts: list[str] = []
    if patient.age is not None:
        parts.append(f"{patient.age}yo")
    if patient.gender:
        parts.append(patient.gender)
    if patient.condition_names:
        parts.append(", ".join(patient.condition_names))
    elif patient.conditions:
        parts.append(f"{len(patient.conditions)} condition(s)")
    if patient.biomarkers:
        bm_str = "/".join(
            f"{b.name}+" if b.status == "positive" else f"{b.name}-"
            for b in patient.biomarkers
        )
        parts.append(bm_str)
    if patient.prior_therapy_names:
        parts.append(f"prior: {', '.join(patient.prior_therapy_names)}")
    return " · ".join(parts) or "Unknown patient"
