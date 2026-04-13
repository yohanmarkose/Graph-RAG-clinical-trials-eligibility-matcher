"""
Scoring helper functions for the clinical trial matching engine.

Each sub-scorer returns a (points_earned, points_possible, detail) tuple
so the final score breakdown is transparent and explainable.

Score scale: 0–100 total, allocated as:
  40 pts — condition match
  25 pts — biomarker match
  15 pts — prior therapy match
  10 pts — demographics (age 5 + gender 5)
  10 pts — trial quality (phase 5 + status 3 + enrollment 2)
"""

from __future__ import annotations

from typing import Any

from matcher.patient_schema import PatientProfile

# ---------------------------------------------------------------------------
# Weight constants (must sum to 100)
# ---------------------------------------------------------------------------

W_CONDITION = 40
W_BIOMARKER = 25
W_PRIOR_THERAPY = 15
W_DEMOGRAPHICS = 10
W_TRIAL_QUALITY = 10


# ---------------------------------------------------------------------------
# Condition scoring
# ---------------------------------------------------------------------------


def score_conditions(
    patient_condition_ids: list[str],
    required_conditions: list[dict],
) -> tuple[float, float, dict]:
    """Score how well patient conditions satisfy trial REQUIRES_CONDITION criteria.

    Each required condition can be matched three ways:
      - direct (concept_id exact match)  → 100 % of that criterion's share
      - ancestor match (patient IS_A required)   → 75 %
      - descendant match (required IS_A patient) → 50 %

    The match type is recorded in ``required_conditions`` entries as
    ``match_type``: "direct" | "ancestor" | "descendant" | "none".

    Args:
        patient_condition_ids: SNOMED concept IDs the patient has.
        required_conditions: list of dicts from Neo4j, each with keys:
            concept_id, term, match_type (set by the engine after graph traversal).

    Returns:
        (points_earned, W_CONDITION, detail_dict)
    """
    if not required_conditions:
        # No condition requirements — award full points (trial is condition-agnostic)
        return float(W_CONDITION), float(W_CONDITION), {"note": "no_condition_requirements"}

    patient_set = set(patient_condition_ids)
    per_criterion = W_CONDITION / len(required_conditions)
    earned = 0.0
    matched: list[dict] = []
    unmatched: list[dict] = []

    for req in required_conditions:
        match_type = req.get("match_type", "none")
        if match_type == "direct":
            earned += per_criterion * 1.00
            matched.append({**req, "points": round(per_criterion, 2)})
        elif match_type == "ancestor":
            earned += per_criterion * 0.75
            matched.append({**req, "points": round(per_criterion * 0.75, 2)})
        elif match_type == "descendant":
            earned += per_criterion * 0.50
            matched.append({**req, "points": round(per_criterion * 0.50, 2)})
        else:
            unmatched.append({**req, "points": 0.0})

    detail = {
        "matched": matched,
        "unmatched": unmatched,
        "match_fraction": len(matched) / len(required_conditions),
    }
    return round(earned, 2), float(W_CONDITION), detail


# ---------------------------------------------------------------------------
# Biomarker scoring
# ---------------------------------------------------------------------------


def score_biomarkers(
    patient_biomarker_map: dict[str, str],
    required_biomarkers: list[dict],
) -> tuple[float, float, dict]:
    """Score biomarker criterion satisfaction.

    Args:
        patient_biomarker_map: {normalised_name: "positive"|"negative"}.
        required_biomarkers: list of dicts with keys: name, status
            (from :REQUIRES_BIOMARKER edges on Criterion nodes).

    Returns:
        (points_earned, W_BIOMARKER, detail_dict)
    """
    if not required_biomarkers:
        return float(W_BIOMARKER), float(W_BIOMARKER), {"note": "no_biomarker_requirements"}

    per = W_BIOMARKER / len(required_biomarkers)
    earned = 0.0
    matched: list[dict] = []
    unmatched: list[dict] = []

    for req in required_biomarkers:
        name_key = req.get("name", "").lower()
        required_status = req.get("status", "").lower()
        patient_status = patient_biomarker_map.get(name_key)

        if patient_status is not None and patient_status == required_status:
            earned += per
            matched.append({**req, "patient_status": patient_status})
        else:
            unmatched.append({**req, "patient_status": patient_status})

    detail = {
        "matched": matched,
        "unmatched": unmatched,
        "match_fraction": len(matched) / len(required_biomarkers) if required_biomarkers else 1.0,
    }
    return round(earned, 2), float(W_BIOMARKER), detail


# ---------------------------------------------------------------------------
# Prior therapy scoring
# ---------------------------------------------------------------------------


def score_prior_therapies(
    patient_therapy_cuis: list[str],
    required_therapies: list[dict],
) -> tuple[float, float, dict]:
    """Score prior therapy criterion satisfaction.

    Scoring logic:
      - Required therapy present  → full per-criterion share (15 pts / N)
      - No therapy required       → award a flat 10 pts (trial is open)
      - Required therapy missing  → 0 pts for that criterion

    Args:
        patient_therapy_cuis: RxNorm CUIs for prior treatments.
        required_therapies: list of dicts with key ``rxcui`` from Neo4j.

    Returns:
        (points_earned, W_PRIOR_THERAPY, detail_dict)
    """
    if not required_therapies:
        return float(W_PRIOR_THERAPY - 5), float(W_PRIOR_THERAPY), {"note": "no_therapy_requirements"}

    patient_set = set(patient_therapy_cuis)
    per = W_PRIOR_THERAPY / len(required_therapies)
    earned = 0.0
    matched: list[dict] = []
    unmatched: list[dict] = []

    for req in required_therapies:
        rxcui = req.get("rxcui", "")
        if rxcui in patient_set:
            earned += per
            matched.append(req)
        else:
            unmatched.append(req)

    detail = {"matched": matched, "unmatched": unmatched}
    return round(earned, 2), float(W_PRIOR_THERAPY), detail


# ---------------------------------------------------------------------------
# Demographics scoring
# ---------------------------------------------------------------------------


def score_demographics(
    patient: PatientProfile,
    trial_meta: dict,
) -> tuple[float, float, dict]:
    """Score age + gender criterion satisfaction (max 10 pts).

    Args:
        patient: PatientProfile with age and gender fields.
        trial_meta: dict with keys min_age, max_age, gender from Neo4j.

    Returns:
        (points_earned, W_DEMOGRAPHICS, detail_dict)
    """
    earned = 0.0
    detail: dict[str, Any] = {}

    # Age — 5 pts
    min_age = trial_meta.get("min_age")
    max_age = trial_meta.get("max_age")
    age = patient.age

    if age is None:
        detail["age"] = "unknown"
        earned += 2.5  # Partial credit when age not provided
    else:
        age_ok = (min_age is None or age >= min_age) and (max_age is None or age <= max_age)
        if age_ok:
            earned += 5.0
            detail["age"] = "in_range"
        else:
            detail["age"] = f"out_of_range (trial: {min_age}-{max_age}, patient: {age})"

    # Gender — 5 pts
    trial_gender = (trial_meta.get("gender") or "All").upper()
    patient_gender = (patient.gender or "").upper()

    if trial_gender in ("ALL", "") or not patient_gender:
        earned += 5.0
        detail["gender"] = "all_genders_accepted"
    elif patient_gender == trial_gender or trial_gender == "ALL":
        earned += 5.0
        detail["gender"] = "match"
    else:
        detail["gender"] = f"mismatch (trial: {trial_gender}, patient: {patient_gender})"

    return round(earned, 2), float(W_DEMOGRAPHICS), detail


# ---------------------------------------------------------------------------
# Trial quality scoring
# ---------------------------------------------------------------------------


def score_trial_quality(trial_meta: dict) -> tuple[float, float, dict]:
    """Score trial quality signals (max 10 pts).

    Breakdown:
      Phase:      Phase 3 → 5, Phase 2 → 3, Phase 1 → 1, other → 0
      Status:     RECRUITING → 3, ACTIVE_NOT_RECRUITING → 2, other → 0
      Enrollment: ≥ 100 → 2, < 100 → 1, unknown → 0

    Args:
        trial_meta: dict with keys phase, status, enrollment.

    Returns:
        (points_earned, W_TRIAL_QUALITY, detail_dict)
    """
    earned = 0.0
    detail: dict[str, Any] = {}

    # Phase — up to 5 pts
    phase_raw = (trial_meta.get("phase") or "").upper()
    if "3" in phase_raw:
        phase_pts = 5.0
    elif "2" in phase_raw:
        phase_pts = 3.0
    elif "1" in phase_raw:
        phase_pts = 1.0
    else:
        phase_pts = 0.0
    earned += phase_pts
    detail["phase"] = {"value": trial_meta.get("phase"), "points": phase_pts}

    # Status — up to 3 pts
    status_raw = (trial_meta.get("status") or "").upper().replace(" ", "_")
    if status_raw == "RECRUITING":
        status_pts = 3.0
    elif status_raw in ("ACTIVE_NOT_RECRUITING", "ACTIVE,_NOT_RECRUITING"):
        status_pts = 2.0
    else:
        status_pts = 0.0
    earned += status_pts
    detail["status"] = {"value": trial_meta.get("status"), "points": status_pts}

    # Enrollment — up to 2 pts
    enrollment = trial_meta.get("enrollment")
    if enrollment is not None:
        enroll_pts = 2.0 if enrollment >= 100 else 1.0
    else:
        enroll_pts = 0.0
    earned += enroll_pts
    detail["enrollment"] = {"value": enrollment, "points": enroll_pts}

    return round(earned, 2), float(W_TRIAL_QUALITY), detail


# ---------------------------------------------------------------------------
# Composite scorer
# ---------------------------------------------------------------------------


def compute_total_score(
    condition_result: tuple[float, float, dict],
    biomarker_result: tuple[float, float, dict],
    therapy_result: tuple[float, float, dict],
    demo_result: tuple[float, float, dict],
    quality_result: tuple[float, float, dict],
) -> dict:
    """Combine sub-scores into a final 0–100 score with full breakdown.

    Returns a dict suitable for inclusion in a MatchResult:
    {
        "score": <float 0-100>,
        "breakdown": {
            "condition":       {"earned": X, "possible": 40, "detail": {...}},
            "biomarker":       {"earned": X, "possible": 25, "detail": {...}},
            "prior_therapy":   {"earned": X, "possible": 15, "detail": {...}},
            "demographics":    {"earned": X, "possible": 10, "detail": {...}},
            "trial_quality":   {"earned": X, "possible": 10, "detail": {...}},
        }
    }
    """
    components = {
        "condition": condition_result,
        "biomarker": biomarker_result,
        "prior_therapy": therapy_result,
        "demographics": demo_result,
        "trial_quality": quality_result,
    }

    total_earned = sum(r[0] for r in components.values())
    total_possible = sum(r[1] for r in components.values())

    # Normalise to 100 in case weights don't sum exactly
    score = round((total_earned / total_possible) * 100, 1) if total_possible > 0 else 0.0

    breakdown = {
        name: {"earned": r[0], "possible": r[1], "detail": r[2]}
        for name, r in components.items()
    }

    return {"score": score, "breakdown": breakdown}
