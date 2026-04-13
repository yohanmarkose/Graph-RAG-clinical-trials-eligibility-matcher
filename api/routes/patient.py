"""POST /patient/parse — free-text patient description → PatientProfile."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_explainer
from llm.explainer import MatchExplainer
from matcher.patient_schema import PatientProfile

logger = logging.getLogger(__name__)
router = APIRouter(tags=["patient"])


class ParseRequest(BaseModel):
    text: str


@router.post("/patient/parse", response_model=PatientProfile)
async def parse_patient(
    body: ParseRequest,
    explainer: MatchExplainer = Depends(get_explainer),
):
    """Parse a free-text clinical description into a structured PatientProfile.

    The LLM extracts age, gender, conditions, biomarkers, prior therapies,
    and ECOG status. Conditions and drugs are resolved to SNOMED / RxNorm IDs
    via the entity linker (LLM fallback for low-confidence matches).

    Example input:
        "58-year-old woman with HER2-positive metastatic breast cancer,
         prior trastuzumab and pertuzumab, ECOG 1"
    """
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="text must not be empty")

    try:
        profile = await explainer.parse_free_text_patient(body.text)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        logger.exception("parse_free_text_patient failed")
        raise HTTPException(status_code=500, detail="Patient parsing failed — check server logs")

    logger.info(
        "POST /patient/parse → age=%s gender=%s conditions=%s",
        profile.age,
        profile.gender,
        profile.condition_names,
    )
    return profile
