"""
Pipeline runner — orchestrates the full or demo data pipeline.

Usage:
    python scripts/run_pipeline.py --demo
    python scripts/run_pipeline.py --full --category oncology
    python scripts/run_pipeline.py --full --category all
    python scripts/run_pipeline.py --stats
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

# Make project root importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import get_settings


# ---------------------------------------------------------------------------
# Connectivity checks
# ---------------------------------------------------------------------------


async def _check_neo4j(cfg) -> bool:
    from neo4j import AsyncGraphDatabase  # type: ignore[import-untyped]

    try:
        driver = AsyncGraphDatabase.driver(cfg.neo4j.uri, auth=(cfg.neo4j.user, cfg.neo4j.password))
        await driver.verify_connectivity()
        await driver.close()
        print(f"[OK] Neo4j reachable at {cfg.neo4j.uri}")
        return True
    except Exception as exc:
        print(f"[FAIL] Neo4j not reachable: {exc}")
        return False


def _check_snowflake(cfg) -> bool:
    if cfg.snowflake is None:
        print("[SKIP] Snowflake credentials not configured in .env")
        return False
    try:
        conn = cfg.snowflake.get_snowflake_connection()
        conn.close()
        print("[OK] Snowflake connection successful")
        return True
    except Exception as exc:
        print(f"[FAIL] Snowflake not reachable: {exc}")
        return False


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


async def _show_stats(cfg) -> None:
    from neo4j import AsyncGraphDatabase  # type: ignore[import-untyped]

    driver = AsyncGraphDatabase.driver(cfg.neo4j.uri, auth=(cfg.neo4j.user, cfg.neo4j.password))
    try:
        async with driver.session() as session:
            print("\n=== Neo4j Graph Stats ===")
            for label, query in [
                ("Trials",        "MATCH (t:Trial) RETURN count(t) AS n"),
                ("SNOMED",        "MATCH (s:SNOMEDConcept) RETURN count(s) AS n"),
                ("RxNorm",        "MATCH (r:RxNormConcept) RETURN count(r) AS n"),
                ("Biomarkers",    "MATCH (b:Biomarker) RETURN count(b) AS n"),
                ("Criteria",      "MATCH (c:Criterion) RETURN count(c) AS n"),
                ("Parsed criteria","MATCH (c:Criterion {parsing_status:'parsed'}) RETURN count(c) AS n"),
                ("Conditions",    "MATCH (c:Condition) RETURN count(c) AS n"),
                ("Interventions", "MATCH (i:Intervention) RETURN count(i) AS n"),
            ]:
                r = await session.run(query)
                row = await r.single()
                print(f"  {label:<22} {row['n']}")

            print("\n=== Trials by Therapeutic Area ===")
            r = await session.run(
                "MATCH (t:Trial) RETURN t.therapeutic_area AS area, count(t) AS cnt ORDER BY cnt DESC"
            )
            async for row in r:
                print(f"  {row['area']:<25} {row['cnt']}")

            print("\n=== Trials by Status ===")
            r = await session.run(
                "MATCH (t:Trial) RETURN t.status AS status, count(t) AS cnt ORDER BY cnt DESC"
            )
            async for row in r:
                print(f"  {row['status']:<35} {row['cnt']}")
    finally:
        await driver.close()


# ---------------------------------------------------------------------------
# Demo pipeline
# ---------------------------------------------------------------------------


async def run_demo(cfg) -> None:
    print("\n--- Step 1/4: Checking Neo4j connectivity ---")
    if not await _check_neo4j(cfg):
        print("Start Neo4j first:  docker-compose up -d")
        sys.exit(1)

    print("\n--- Step 2/4: Seeding demo data ---")
    result = subprocess.run(
        [sys.executable, "scripts/seed_demo_data.py"],
        capture_output=False,
    )
    if result.returncode != 0:
        print("[FAIL] seed_demo_data.py exited with errors")
        sys.exit(1)

    print("\n--- Step 3/4: Running test suite ---")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_matcher.py", "-v", "--tb=short"],
        capture_output=False,
    )
    if result.returncode != 0:
        print("[WARN] Some tests failed — review output above")

    print("\n--- Step 4/4: Starting API server ---")
    print("Starting FastAPI on http://localhost:8000 (Ctrl-C to stop)")
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--reload",
         "--host", cfg.api.host, "--port", str(cfg.api.port)],
    )


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


async def run_full(cfg, category: str) -> None:
    print(f"\n=== Full pipeline — category: {category} ===")

    print("\n--- Step 1/9: Checking connectivity ---")
    neo4j_ok = await _check_neo4j(cfg)
    sf_ok = _check_snowflake(cfg)
    if not neo4j_ok or not sf_ok:
        print("Fix connectivity issues above before running the full pipeline.")
        sys.exit(1)

    area_flag = [] if category == "all" else ["--category", category]

    steps = [
        ("2/9", "Fetching all trials from ClinicalTrials.gov (~15 min)",
         [sys.executable, "-m", "data_ingestion.fetch_trials"]),
        ("3/9", "Loading raw data into Snowflake staging",
         [sys.executable, "-m", "snowflake_etl.load_staging"]),
        ("4/9", "Cleaning & tagging therapeutic areas in Snowflake",
         [sys.executable, "-m", "snowflake_etl.transform"]),
        ("5/9", f"Exporting {category} trials from Snowflake → Neo4j",
         [sys.executable, "-m", "snowflake_etl.export_for_neo4j"] + area_flag),
        ("6/9", "Parsing SNOMED CT RF2 files",
         [sys.executable, "-m", "data_ingestion.parse_snomed"]),
        ("7/9", "Parsing RxNorm RRF files",
         [sys.executable, "-m", "data_ingestion.parse_rxnorm"]),
        ("8/9", f"Parsing eligibility criteria with LLM (area: {category})",
         [sys.executable, "-m", "nlp.criteria_parser"] + area_flag),
        ("9/9", "Running test suite",
         [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"]),
    ]

    for step_label, description, cmd in steps:
        print(f"\n--- Step {step_label}: {description} ---")
        result = subprocess.run(cmd, capture_output=False)
        if result.returncode != 0:
            print(f"[FAIL] Step {step_label} failed. Aborting.")
            sys.exit(1)

    print("\n=== Full pipeline complete. Starting API server… ===")
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--reload",
         "--host", cfg.api.host, "--port", str(cfg.api.port)],
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clinical Trial Matcher pipeline runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/run_pipeline.py --demo
  python scripts/run_pipeline.py --full --category oncology
  python scripts/run_pipeline.py --full --category all
  python scripts/run_pipeline.py --stats
""",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo",  action="store_true", help="Seed demo data (no Snowflake needed)")
    mode.add_argument("--full",  action="store_true", help="Run full data pipeline")
    mode.add_argument("--stats", action="store_true", help="Show current graph statistics")
    parser.add_argument(
        "--category",
        default="oncology",
        help="Therapeutic area for --full pipeline (default: oncology, or 'all')",
    )
    args = parser.parse_args()

    cfg = get_settings()

    if args.stats:
        asyncio.run(_show_stats(cfg))
    elif args.demo:
        asyncio.run(run_demo(cfg))
    else:
        asyncio.run(run_full(cfg, args.category))


if __name__ == "__main__":
    main()
