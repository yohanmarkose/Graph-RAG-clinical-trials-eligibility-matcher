"""
Test patient profiles for the Clinical Trial Matcher.

Contains both structured (PatientProfile dict) and free-text versions
of patients designed to produce different matching outcomes against
the real oncology data in Neo4j.

Usage:
    # Run all test patients through the API
    python scripts/test_patients.py

    # Or import and use individually
    from scripts.test_patients import PATIENTS
"""

from __future__ import annotations

import json
import sys
import time

import requests

API_URL = "http://localhost:8000"

# ============================================================================
# TEST PATIENTS
# ============================================================================

PATIENTS = {

    # ------------------------------------------------------------------
    # SHOULD MATCH WELL (high scores, multiple trials)
    # ------------------------------------------------------------------

    "PASS_1_breast_cancer_her2": {
        "description": "Classic HER2+ breast cancer — should match many oncology trials",
        "expected": "STRONG MATCH — multiple breast cancer trials, high scores",
        "structured": {
            "age": 55,
            "gender": "Female",
            "conditions": ["254837009"],              # Malignant neoplasm of breast
            "condition_names": ["Breast Cancer"],
            "biomarkers": [{"name": "HER2", "status": "positive"}],
            "prior_therapies": [],
            "prior_therapy_names": [],
            "ecog_status": 1,
            "therapeutic_area": "oncology",
        },
        "freetext": (
            "55-year-old woman with HER2-positive breast cancer. "
            "ECOG performance status 1. No prior systemic therapy."
        ),
    },

    "PASS_2_nsclc_egfr": {
        "description": "EGFR+ NSCLC — common oncology trial target",
        "expected": "STRONG MATCH — NSCLC is the 2nd most common condition in graph",
        "structured": {
            "age": 62,
            "gender": "Male",
            "conditions": ["254637007"],              # Non-small cell lung cancer
            "condition_names": ["Non-Small Cell Lung Cancer"],
            "biomarkers": [{"name": "EGFR", "status": "positive"}],
            "prior_therapies": [],
            "prior_therapy_names": [],
            "ecog_status": 0,
            "therapeutic_area": "oncology",
        },
        "freetext": (
            "62-year-old male with EGFR-mutant non-small cell lung cancer. "
            "Treatment-naive. ECOG 0."
        ),
    },

    "PASS_3_aml": {
        "description": "AML patient with prior chemo — should match many leukemia trials",
        "expected": "STRONG MATCH — AML has 398 trials in the graph",
        "structured": {
            "age": 48,
            "gender": "Male",
            "conditions": ["91861009"],               # Acute myeloid leukemia
            "condition_names": ["Acute Myeloid Leukemia"],
            "biomarkers": [],
            "prior_therapies": ["3002"],              # Cyclophosphamide
            "prior_therapy_names": ["cyclophosphamide"],
            "ecog_status": 1,
            "therapeutic_area": "oncology",
        },
        "freetext": (
            "48-year-old man with relapsed acute myeloid leukemia. "
            "Previously treated with cyclophosphamide. ECOG 1."
        ),
    },

    "PASS_4_multiple_myeloma": {
        "description": "Multiple myeloma — most common condition in graph (521 trials)",
        "expected": "STRONG MATCH — highest trial count condition",
        "structured": {
            "age": 67,
            "gender": "Male",
            "conditions": ["109989006"],              # Multiple myeloma
            "condition_names": ["Multiple Myeloma"],
            "biomarkers": [],
            "prior_therapies": [],
            "prior_therapy_names": [],
            "ecog_status": 1,
            "therapeutic_area": "oncology",
        },
        "freetext": (
            "67-year-old man with newly diagnosed multiple myeloma. "
            "No prior therapy. ECOG performance status 1."
        ),
    },

    "PASS_5_hcc_with_prior_therapy": {
        "description": "HCC patient with prior bevacizumab — common combo",
        "expected": "GOOD MATCH — HCC has 380 trials, bevacizumab is common",
        "structured": {
            "age": 59,
            "gender": "Male",
            "conditions": ["25370001"],               # Hepatocellular carcinoma
            "condition_names": ["Hepatocellular Carcinoma"],
            "biomarkers": [{"name": "PD-L1", "status": "positive"}],
            "prior_therapies": ["253337"],             # Bevacizumab
            "prior_therapy_names": ["bevacizumab"],
            "ecog_status": 0,
            "therapeutic_area": "oncology",
        },
        "freetext": (
            "59-year-old man with hepatocellular carcinoma, PD-L1 positive. "
            "Previously treated with bevacizumab. ECOG 0."
        ),
    },

    # ------------------------------------------------------------------
    # SHOULD PARTIALLY MATCH (some results but lower scores)
    # ------------------------------------------------------------------

    "PARTIAL_1_elderly_breast_cancer": {
        "description": "Very elderly patient — age may exclude from some trials",
        "expected": "PARTIAL — age 89 will fail min/max age on many trials",
        "structured": {
            "age": 89,
            "gender": "Female",
            "conditions": ["254837009"],              # Malignant neoplasm of breast
            "condition_names": ["Breast Cancer"],
            "biomarkers": [],
            "prior_therapies": [],
            "prior_therapy_names": [],
            "ecog_status": 3,
            "therapeutic_area": "oncology",
        },
        "freetext": (
            "89-year-old woman with breast cancer. "
            "Poor performance status, ECOG 3. No prior therapy."
        ),
    },

    "PARTIAL_2_generic_cancer": {
        "description": "Generic 'malignant neoplasm' — broad match, low specificity",
        "expected": "PARTIAL — broad condition matches many but scores lower",
        "structured": {
            "age": 50,
            "gender": "Female",
            "conditions": ["363346000"],              # Malignant neoplastic disease (broad)
            "condition_names": ["Cancer (general)"],
            "biomarkers": [],
            "prior_therapies": [],
            "prior_therapy_names": [],
            "ecog_status": None,
            "therapeutic_area": "oncology",
        },
        "freetext": (
            "50-year-old woman diagnosed with cancer. "
            "Specific subtype pending pathology."
        ),
    },

    # ------------------------------------------------------------------
    # SHOULD FAIL / NO MATCHES
    # ------------------------------------------------------------------

    "FAIL_1_wrong_area": {
        "description": "Cardiology patient — no cardiology trials in oncology-filtered graph",
        "expected": "NO MATCH — condition not in oncology data",
        "structured": {
            "age": 70,
            "gender": "Male",
            "conditions": ["84114007"],               # Heart failure
            "condition_names": ["Heart Failure"],
            "biomarkers": [],
            "prior_therapies": [],
            "prior_therapy_names": [],
            "ecog_status": None,
            "therapeutic_area": "cardiology",
        },
        "freetext": (
            "70-year-old man with congestive heart failure. "
            "NYHA class III. On metoprolol and lisinopril."
        ),
    },

    "FAIL_2_no_conditions": {
        "description": "Patient with no conditions — matcher requires at least one",
        "expected": "NO MATCH — empty condition list returns no candidates",
        "structured": {
            "age": 40,
            "gender": "Female",
            "conditions": [],
            "condition_names": [],
            "biomarkers": [],
            "prior_therapies": [],
            "prior_therapy_names": [],
            "ecog_status": None,
            "therapeutic_area": None,
        },
        "freetext": "40-year-old healthy woman seeking preventive screening.",
    },

    "FAIL_3_pediatric": {
        "description": "Pediatric patient — most oncology trials require age >= 18",
        "expected": "NO MATCH or very few — age 8 excluded from adult trials",
        "structured": {
            "age": 8,
            "gender": "Male",
            "conditions": ["91861009"],               # AML
            "condition_names": ["Acute Myeloid Leukemia"],
            "biomarkers": [],
            "prior_therapies": [],
            "prior_therapy_names": [],
            "ecog_status": 0,
            "therapeutic_area": "oncology",
        },
        "freetext": (
            "8-year-old boy with acute myeloid leukemia. "
            "No prior treatment. ECOG 0."
        ),
    },
}


# ============================================================================
# Runner — test all patients against the API
# ============================================================================

def run_all_tests(api_url: str = API_URL) -> None:
    """Send each test patient to POST /match and print results."""
    print(f"Testing against API at {api_url}\n")

    # Check API is running
    try:
        resp = requests.get(f"{api_url}/", timeout=5)
        print(f"API health: {resp.json()}\n")
    except requests.exceptions.ConnectionError:
        print(f"ERROR: Cannot connect to API at {api_url}. Is uvicorn running?")
        sys.exit(1)

    for name, patient in PATIENTS.items():
        print(f"{'='*70}")
        print(f"TEST: {name}")
        print(f"  {patient['description']}")
        print(f"  Expected: {patient['expected']}")

        payload = patient["structured"]
        t0 = time.perf_counter()

        try:
            resp = requests.post(
                f"{api_url}/match",
                json=payload,
                params={"top_n": 5, "include_explanations": False},
                timeout=60,
            )
            elapsed = (time.perf_counter() - t0) * 1000

            if resp.status_code == 200:
                data = resp.json()
                matches = data.get("matches", [])
                print(f"  Result: {len(matches)} matches in {elapsed:.0f}ms")
                for m in matches[:3]:
                    print(f"    {m['score']:.0f}/100 — {m['nct_id']} — {m['title'][:60]}")
                if not matches:
                    print(f"    (no matches)")
            else:
                print(f"  ERROR {resp.status_code}: {resp.text[:200]}")

        except Exception as exc:
            print(f"  ERROR: {exc}")

        print()

    # Also test free text parsing
    print(f"{'='*70}")
    print("FREE TEXT PARSING TEST")
    print(f"{'='*70}")
    test_text = PATIENTS["PASS_1_breast_cancer_her2"]["freetext"]
    print(f"  Input: {test_text}")
    try:
        resp = requests.post(
            f"{api_url}/patient/parse",
            json={"text": test_text},
            timeout=30,
        )
        if resp.status_code == 200:
            profile = resp.json()
            print(f"  Parsed profile:")
            print(f"    Age: {profile.get('age')}")
            print(f"    Gender: {profile.get('gender')}")
            print(f"    Conditions: {profile.get('condition_names')}")
            print(f"    Biomarkers: {profile.get('biomarkers')}")
            print(f"    ECOG: {profile.get('ecog_status')}")
            print(f"    SNOMED IDs: {profile.get('conditions')}")
        else:
            print(f"  ERROR {resp.status_code}: {resp.text[:200]}")
    except Exception as exc:
        print(f"  ERROR: {exc}")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else API_URL
    run_all_tests(url)
