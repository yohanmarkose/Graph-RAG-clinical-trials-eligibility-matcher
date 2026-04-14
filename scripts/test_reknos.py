"""
Standalone ReKnoS comparison test.

Runs the same patient profile through both the baseline SNOMED IS_A traversal
and the ReKnoS multi-hop finder, then prints a side-by-side comparison.

Usage:
    # Make sure Neo4j is running and seeded first:
    #   docker-compose up -d
    #   python scripts/seed_demo_data.py

    python scripts/test_reknos.py

Environment:
    Reads NEO4J_* and LLM_* settings from .env (same as the API).
"""

from __future__ import annotations

import asyncio
import logging
import time

from neo4j import AsyncGraphDatabase

from config.settings import get_settings
from llm.provider import get_llm_provider
from matcher.match_engine import MatchEngine
from matcher.patient_schema import BiomarkerStatus, PatientProfile
from matcher.reknos_finder import ReKnoSCandidateFinder
from matcher.super_relations import ClinicalTrialsKGInterface

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Test patients — edit SNOMED concept IDs to match your seeded data
# ---------------------------------------------------------------------------

TEST_PATIENTS: list[dict] = [
    {
        # ── WHY THIS CASE MATTERS ────────────────────────────────────────────
        # NCT00000002 (T-DM1 after trastuzumab) is registered in the KG under
        # SNOMED 408643008 "Metastatic breast cancer" as its STUDIES_CONDITION.
        #
        # This patient has SNOMED 427685000 "HER2+ breast cancer" — a SIBLING
        # of 408643008 in the SNOMED tree (both are children of 254837009
        # "Breast cancer"), NOT an ancestor.
        #
        # Baseline (IS_A traversal only walks UP):
        #   427685000 → 254837009 → 363346000   ← only these are searched
        #   408643008 is NEVER in this path → NCT00000002 is MISSED
        #
        # ReKnoS (adds biomarker + therapy paths):
        #   her2 (Biomarker) ← REQUIRES_BIOMARKER ← Criterion ← NCT00000002 ✓
        #   224905 (trastuzumab) ← REQUIRES_PRIOR_DRUG ← Criterion ← NCT00000002 ✓
        # ────────────────────────────────────────────────────────────────────
        "name": "HER2+ Breast Cancer + prior trastuzumab → should find NCT00000002",
        "profile": PatientProfile(
            age=58,
            gender="Female",
            conditions=["427685000"],          # HER2-positive breast cancer
            condition_names=["HER2-positive breast cancer"],
            biomarkers=[BiomarkerStatus(name="HER2", status="positive")],
            prior_therapies=["224905"],        # trastuzumab
            prior_therapy_names=["trastuzumab"],
            ecog_status=1,
            therapeutic_area="oncology",
        ),
    },
    {
        # ── WHY THIS CASE MATTERS ────────────────────────────────────────────
        # NCT00000011 (FOLFIRI second-line) requires prior oxaliplatin.
        # A patient with plain "Colorectal cancer" (363406005) does NOT match
        # the trial's STUDIES_CONDITION (94260004 Metastatic CRC) via IS_A.
        # ReKnoS finds it via the therapy path:
        #   151399 (oxaliplatin) ← REQUIRES_PRIOR_DRUG ← Criterion ← NCT00000011
        #
        # NOTE: the frontend DRUG_TO_RXNORM maps "oxaliplatin" → "77991" but the
        # seed data uses rxcui "151399". This test uses the seed-data CUI directly.
        # ────────────────────────────────────────────────────────────────────
        "name": "Colorectal cancer + prior oxaliplatin → should find NCT00000011",
        "profile": PatientProfile(
            age=52,
            gender="Female",
            conditions=["363406005"],          # Colorectal cancer (NOT metastatic)
            condition_names=["Colorectal cancer"],
            biomarkers=[],
            prior_therapies=["151399"],        # oxaliplatin (seed-data CUI)
            prior_therapy_names=["oxaliplatin"],
            ecog_status=1,
            therapeutic_area="oncology",
        ),
    },
]


async def run_comparison(patient_name: str, patient: PatientProfile) -> None:
    cfg = get_settings()

    driver = AsyncGraphDatabase.driver(
        cfg.neo4j.uri,
        auth=(cfg.neo4j.user, cfg.neo4j.password),
    )

    try:
        llm = get_llm_provider(cfg)
        kg_interface = ClinicalTrialsKGInterface(driver)
        reknos_finder = ReKnoSCandidateFinder(
            kg=kg_interface,
            llm=llm,
            N=cfg.reknos.width,
            L=cfg.reknos.depth,
            use_stop_check=cfg.reknos.use_stop_check,
        )

        engine_baseline = MatchEngine(driver)
        engine_reknos = MatchEngine(driver, reknos_finder=reknos_finder)

        print(f"\n{'=' * 60}")
        print(f"Patient: {patient_name}")
        print(f"{'=' * 60}")

        # Baseline
        t0 = time.perf_counter()
        baseline_ids = await engine_baseline.find_candidate_trials(patient)
        baseline_ms = (time.perf_counter() - t0) * 1000

        # ReKnoS (union)
        t1 = time.perf_counter()
        reknos_ids = await engine_reknos.find_candidate_trials(patient, use_reknos=True)
        reknos_ms = (time.perf_counter() - t1) * 1000

        baseline_set = set(baseline_ids)
        reknos_set = set(reknos_ids)
        reknos_only = reknos_set - baseline_set

        print(f"  Baseline SNOMED traversal : {len(baseline_set):>3} candidates  ({baseline_ms:.0f} ms)")
        print(f"  ReKnoS union              : {len(reknos_set):>3} candidates  ({reknos_ms:.0f} ms)")
        print(f"  Extra trials via ReKnoS   : {len(reknos_only):>3}")
        print(f"  ReKnoS LLM calls          : {reknos_finder.llm_call_count}")

        if reknos_only:
            print(f"\n  New NCT IDs found by ReKnoS:")
            for nct_id in sorted(reknos_only):
                print(f"    - {nct_id}")

        # Verify LLM call bound (should be ≤ 2L)
        max_calls = 2 * cfg.reknos.depth
        status = "PASS" if reknos_finder.llm_call_count <= max_calls else "FAIL"
        print(f"\n  LLM call bound (≤ 2L={max_calls}): {status}")

    finally:
        await driver.close()


async def main() -> None:
    for patient_info in TEST_PATIENTS:
        await run_comparison(patient_info["name"], patient_info["profile"])
    print()


if __name__ == "__main__":
    asyncio.run(main())
