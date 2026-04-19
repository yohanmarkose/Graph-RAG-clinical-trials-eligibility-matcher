"""
Unit tests for eligibility criteria parsing utilities.
"""

from __future__ import annotations

import pytest

from matcher.patient_schema import BiomarkerStatus, LabValue, PatientProfile
from matcher.scorer import (
    W_BIOMARKER,
    W_CONDITION,
    W_DEMOGRAPHICS,
    W_PRIOR_THERAPY,
    W_TRIAL_QUALITY,
    compute_total_score,
    score_biomarkers,
    score_conditions,
    score_demographics,
    score_prior_therapies,
    score_trial_quality,
)


# ---------------------------------------------------------------------------
# score_conditions
# ---------------------------------------------------------------------------


class TestScoreConditions:
    def test_direct_match_awards_full_points(self):
        required = [{"concept_id": "254837009", "term": "Breast cancer", "match_type": "direct"}]
        earned, possible, detail = score_conditions(["254837009"], required)
        assert earned == W_CONDITION
        assert possible == W_CONDITION
        assert len(detail["matched"]) == 1

    def test_ancestor_match_awards_75_percent(self):
        required = [{"concept_id": "254837009", "term": "Breast cancer", "match_type": "ancestor"}]
        earned, possible, _ = score_conditions(["427685000"], required)
        assert earned == pytest.approx(W_CONDITION * 0.75, rel=0.01)

    def test_descendant_match_awards_50_percent(self):
        required = [{"concept_id": "254837009", "term": "Breast cancer", "match_type": "descendant"}]
        earned, possible, _ = score_conditions(["363346000"], required)
        assert earned == pytest.approx(W_CONDITION * 0.50, rel=0.01)

    def test_no_match_awards_zero(self):
        required = [{"concept_id": "254837009", "term": "Breast cancer", "match_type": "none"}]
        earned, possible, detail = score_conditions(["49049000"], required)
        assert earned == 0.0
        assert len(detail["unmatched"]) == 1

    def test_no_requirements_awards_full(self):
        earned, possible, detail = score_conditions(["254837009"], [])
        assert earned == W_CONDITION
        assert "no_condition_requirements" in detail.get("note", "")

    def test_partial_match_proportional(self):
        required = [
            {"concept_id": "254837009", "term": "Breast cancer",    "match_type": "direct"},
            {"concept_id": "93143009",  "term": "Leukemia",         "match_type": "none"},
        ]
        earned, possible, _ = score_conditions(["254837009"], required)
        assert earned == pytest.approx(W_CONDITION / 2, rel=0.01)


# ---------------------------------------------------------------------------
# score_biomarkers
# ---------------------------------------------------------------------------


class TestScoreBiomarkers:
    def test_matching_status_awards_full(self):
        bmap = {"her2": "positive"}
        required = [{"name": "HER2", "status": "positive"}]
        earned, possible, detail = score_biomarkers(bmap, required)
        assert earned == W_BIOMARKER
        assert len(detail["matched"]) == 1

    def test_wrong_status_awards_zero(self):
        bmap = {"her2": "negative"}
        required = [{"name": "HER2", "status": "positive"}]
        earned, possible, detail = score_biomarkers(bmap, required)
        assert earned == 0.0
        assert len(detail["unmatched"]) == 1

    def test_missing_biomarker_awards_zero(self):
        bmap = {}
        required = [{"name": "HER2", "status": "positive"}]
        earned, possible, _ = score_biomarkers(bmap, required)
        assert earned == 0.0

    def test_no_requirements_awards_full(self):
        earned, possible, detail = score_biomarkers({}, [])
        assert earned == W_BIOMARKER

    def test_partial_match(self):
        bmap = {"her2": "positive", "egfr": "negative"}
        required = [
            {"name": "HER2",  "status": "positive"},
            {"name": "PD-L1", "status": "positive"},   # patient doesn't have PD-L1 data
        ]
        earned, possible, _ = score_biomarkers(bmap, required)
        assert earned == pytest.approx(W_BIOMARKER / 2, rel=0.01)


# ---------------------------------------------------------------------------
# score_prior_therapies
# ---------------------------------------------------------------------------


class TestScorePriorTherapies:
    def test_required_and_present_full_score(self):
        earned, possible, detail = score_prior_therapies(["224905"], [{"rxcui": "224905"}])
        assert earned == W_PRIOR_THERAPY
        assert len(detail["matched"]) == 1

    def test_required_and_missing_zero(self):
        earned, possible, detail = score_prior_therapies([], [{"rxcui": "224905"}])
        assert earned == 0.0
        assert len(detail["unmatched"]) == 1

    def test_no_requirement_partial_score(self):
        earned, possible, detail = score_prior_therapies(["224905"], [])
        # No requirements → awards W_PRIOR_THERAPY - 5
        assert earned == W_PRIOR_THERAPY - 5


# ---------------------------------------------------------------------------
# score_demographics
# ---------------------------------------------------------------------------


class TestScoreDemographics:
    def _patient(self, age=None, gender=None):
        return PatientProfile(age=age, gender=gender)

    def _meta(self, min_age=None, max_age=None, gender="All"):
        return {"min_age": min_age, "max_age": max_age, "gender": gender}

    def test_age_in_range_awards_5(self):
        earned, _, detail = score_demographics(self._patient(45), self._meta(18, 75))
        assert detail["age"] == "in_range"
        # gender "All" also adds 5
        assert earned == W_DEMOGRAPHICS

    def test_age_below_min_awards_zero_age(self):
        earned, _, detail = score_demographics(self._patient(15), self._meta(18, 75))
        assert "out_of_range" in detail["age"]

    def test_age_above_max_awards_zero_age(self):
        earned, _, detail = score_demographics(self._patient(80), self._meta(18, 75))
        assert "out_of_range" in detail["age"]

    def test_gender_mismatch_zero(self):
        earned, _, detail = score_demographics(
            self._patient(50, "Male"), self._meta(18, 75, "Female")
        )
        assert "mismatch" in detail["gender"]

    def test_trial_all_gender_accepts_any(self):
        earned, _, detail = score_demographics(
            self._patient(50, "Male"), self._meta(18, 75, "All")
        )
        assert "all_genders_accepted" in detail["gender"]

    def test_unknown_age_partial_credit(self):
        earned, _, detail = score_demographics(self._patient(None, "Female"), self._meta(18, 75))
        assert detail["age"] == "unknown"
        assert earned == pytest.approx(7.5)  # 2.5 (age) + 5 (gender)


# ---------------------------------------------------------------------------
# score_trial_quality
# ---------------------------------------------------------------------------


class TestScoreTrialQuality:
    def test_phase3_recruiting_large_enrollment(self):
        meta = {"phase": "Phase 3", "status": "RECRUITING", "enrollment": 500}
        earned, possible, detail = score_trial_quality(meta)
        assert detail["phase"]["points"]      == 5.0
        assert detail["status"]["points"]     == 3.0
        assert detail["enrollment"]["points"] == 2.0
        assert earned == 10.0

    def test_phase2_active_not_recruiting(self):
        meta = {"phase": "Phase 2", "status": "ACTIVE_NOT_RECRUITING", "enrollment": 50}
        earned, possible, detail = score_trial_quality(meta)
        assert detail["phase"]["points"]  == 3.0
        assert detail["status"]["points"] == 2.0
        assert detail["enrollment"]["points"] == 1.0

    def test_unknown_phase_zero(self):
        meta = {"phase": None, "status": "RECRUITING", "enrollment": 100}
        earned, _, detail = score_trial_quality(meta)
        assert detail["phase"]["points"] == 0.0

    def test_no_enrollment_zero(self):
        meta = {"phase": "Phase 3", "status": "RECRUITING", "enrollment": None}
        earned, _, detail = score_trial_quality(meta)
        assert detail["enrollment"]["points"] == 0.0


# ---------------------------------------------------------------------------
# compute_total_score
# ---------------------------------------------------------------------------


class TestComputeTotalScore:
    def _make_result(self, earned, possible):
        return (earned, possible, {})

    def test_perfect_score(self):
        result = compute_total_score(
            self._make_result(40, 40),
            self._make_result(25, 25),
            self._make_result(15, 15),
            self._make_result(10, 10),
            self._make_result(10, 10),
        )
        assert result["score"] == 100.0

    def test_zero_score(self):
        result = compute_total_score(
            self._make_result(0, 40),
            self._make_result(0, 25),
            self._make_result(0, 15),
            self._make_result(0, 10),
            self._make_result(0, 10),
        )
        assert result["score"] == 0.0

    def test_breakdown_keys_present(self):
        result = compute_total_score(
            self._make_result(20, 40),
            self._make_result(12, 25),
            self._make_result(10, 15),
            self._make_result(5, 10),
            self._make_result(7, 10),
        )
        assert set(result["breakdown"].keys()) == {
            "condition", "biomarker", "prior_therapy", "demographics", "trial_quality"
        }

    def test_partial_score_normalised_to_100(self):
        result = compute_total_score(
            self._make_result(20, 40),   # 50%
            self._make_result(25, 25),   # 100%
            self._make_result(15, 15),   # 100%
            self._make_result(10, 10),   # 100%
            self._make_result(10, 10),   # 100%
        )
        # (20+25+15+10+10) / 100 = 80
        assert result["score"] == pytest.approx(80.0, rel=0.01)


# ---------------------------------------------------------------------------
# Age parsing helpers (used by fetch_trials — tested inline)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("18 Years",  18),
    ("18 years",  18),
    ("N/A",       None),
    ("",          None),
    ("65 Months", 65),
])
def test_age_parsing(raw: str, expected):
    """Mirrors the parse logic in data_ingestion.fetch_trials.parse_trial_record."""
    import re

    def parse_age(text: str | None) -> int | None:
        if not text or text.strip().upper() in ("N/A", ""):
            return None
        m = re.search(r"\d+", text)
        return int(m.group()) if m else None

    assert parse_age(raw) == expected
