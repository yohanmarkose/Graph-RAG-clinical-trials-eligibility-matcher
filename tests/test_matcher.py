"""
Integration tests for the MatchEngine.
"""

from __future__ import annotations

import pytest

from matcher.patient_schema import BiomarkerStatus, PatientProfile
from tests.conftest import requires_neo4j


# ---------------------------------------------------------------------------
# Test 1 — HER2+ breast cancer patient ranks HER2 trials high
# ---------------------------------------------------------------------------


@requires_neo4j
async def test_her2_breast_cancer_matches_her2_trials(match_engine, patient_her2_breast):
    """HER2+ breast cancer patient should surface NCT00000001 (trastuzumab+pertuzumab)."""
    results = await match_engine.match(patient_her2_breast, top_n=10)

    assert results, "Expected at least one match for HER2+ breast cancer patient"

    nct_ids = [r["nct_id"] for r in results]
    assert "NCT00000001" in nct_ids, (
        "NCT00000001 (HER2+ trial) should appear in top-10 matches"
    )

    # The HER2 trial should rank in the top 3
    top3 = nct_ids[:3]
    assert "NCT00000001" in top3, (
        f"NCT00000001 should be in top 3 for HER2+ patient; got {top3}"
    )

    # Score should be meaningfully high
    her2_result = next(r for r in results if r["nct_id"] == "NCT00000001")
    assert her2_result["score"] >= 50, (
        f"Expected score ≥ 50 for HER2 match, got {her2_result['score']}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Age exclusion
# ---------------------------------------------------------------------------


@requires_neo4j
async def test_age_exclusion_filters_out_adult_only_trials(match_engine, patient_too_young):
    """A 15-year-old should be excluded from trials with min_age=18."""
    results = await match_engine.match(patient_too_young, top_n=20)

    for r in results:
        trial_meta = r.get("trial", {})
        min_age = trial_meta.get("min_age")
        if min_age is not None and patient_too_young.age is not None:
            assert patient_too_young.age >= min_age, (
                f"Trial {r['nct_id']} (min_age={min_age}) should have been excluded "
                f"for patient age {patient_too_young.age}"
            )


# ---------------------------------------------------------------------------
# Test 3 — Prior trastuzumab: excluded from NCT00000001, eligible for NCT00000002
# ---------------------------------------------------------------------------


@requires_neo4j
async def test_prior_trastuzumab_exclusion_and_inclusion(
    match_engine, patient_her2_breast_prior_trastu
):
    """Patient with prior trastuzumab must be excluded from NCT00000001 (excludes prior trastu)
    but included in NCT00000002 (requires prior trastu)."""
    results = await match_engine.match(patient_her2_breast_prior_trastu, top_n=20)
    nct_ids = [r["nct_id"] for r in results]

    # NCT00000001 excludes prior trastuzumab → should NOT appear
    assert "NCT00000001" not in nct_ids, (
        "NCT00000001 should be excluded for a patient who previously received trastuzumab"
    )

    # NCT00000002 requires prior trastuzumab → should appear
    assert "NCT00000002" in nct_ids, (
        "NCT00000002 (T-DM1 after trastuzumab) should match patient with prior trastuzumab"
    )


# ---------------------------------------------------------------------------
# Test 4 — NSCLC EGFR+ matches lung trials, not breast trials
# ---------------------------------------------------------------------------


@requires_neo4j
async def test_nsclc_egfr_matches_lung_not_breast(match_engine, patient_nsclc_egfr):
    """EGFR+ NSCLC patient should match osimertinib trial (NCT00000007), not breast trials."""
    results = await match_engine.match(patient_nsclc_egfr, top_n=10)
    nct_ids = [r["nct_id"] for r in results]

    # EGFR+ NSCLC osimertinib trial should appear
    assert "NCT00000007" in nct_ids, (
        "NCT00000007 (osimertinib in EGFR+ NSCLC) should match EGFR+ NSCLC patient"
    )

    # Breast cancer trials (NCT00000001-005) should NOT appear
    breast_trials = {"NCT00000001", "NCT00000002", "NCT00000003", "NCT00000004", "NCT00000005"}
    matching_breast = breast_trials & set(nct_ids)
    assert not matching_breast, (
        f"Breast cancer trials should not match NSCLC patient; found: {matching_breast}"
    )


# ---------------------------------------------------------------------------
# Test 5 — Empty patient profile → no / very few matches
# ---------------------------------------------------------------------------


@requires_neo4j
async def test_empty_profile_returns_no_matches(match_engine, patient_empty):
    """A patient with no conditions should return no candidates from the graph traversal."""
    results = await match_engine.match(patient_empty, top_n=10)
    assert results == [], (
        "Empty patient profile should yield no matches (no SNOMED conditions to traverse)"
    )


# ---------------------------------------------------------------------------
# Test 6 — Therapeutic area filter
# ---------------------------------------------------------------------------


@requires_neo4j
async def test_therapeutic_area_filter_restricts_results(match_engine):
    """With therapeutic_area='cardiology', only cardiology trials should be returned."""
    cardiology_patient = PatientProfile(
        age=60,
        gender="Male",
        conditions=["84114007"],       # Heart failure
        condition_names=["Heart failure"],
        therapeutic_area="cardiology",
    )
    results = await match_engine.match(cardiology_patient, top_n=20)

    for r in results:
        trial_area = r.get("trial", {}).get("therapeutic_area", "")
        assert trial_area == "cardiology", (
            f"Trial {r['nct_id']} has area '{trial_area}', "
            f"expected 'cardiology' due to therapeutic_area filter"
        )


# ---------------------------------------------------------------------------
# Unit tests (no Neo4j required)
# ---------------------------------------------------------------------------


def test_patient_profile_biomarker_map(patient_her2_breast):
    bmap = patient_her2_breast.biomarker_map()
    assert bmap == {"her2": "positive"}


def test_patient_profile_lab_map_empty(patient_her2_breast):
    assert patient_her2_breast.lab_map() == {}


def test_patient_profile_defaults():
    p = PatientProfile()
    assert p.conditions == []
    assert p.biomarkers == []
    assert p.prior_therapies == []
    assert p.age is None


def test_biomarker_status_model():
    b = BiomarkerStatus(name="EGFR", status="positive")
    assert b.name == "EGFR"
    assert b.status == "positive"
