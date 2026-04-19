"""
Reads CSVs produced by ``snowflake_etl/export_for_neo4j.py`` and creates:
  - (:Trial) nodes
  - (:Condition) nodes + [:STUDIES_CONDITION] + optional [:MAPS_TO_SNOMED]
  - (:Intervention) nodes + [:USES_INTERVENTION] + optional [:MAPS_TO_RXNORM]
  - (:Criterion) nodes + [:HAS_CRITERION]

Usage:
    python -m kg_builder.load_trials --category oncology
    python -m kg_builder.load_trials --category all
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

import pandas as pd
from neo4j import AsyncGraphDatabase

from config.settings import get_settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"

TRIAL_BATCH = 500
BATCH_SIZE = 5_000
CRITERIA_BATCH = 10_000


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _lowercase_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Snowflake exports UPPERCASE headers — lowercase them for convenience."""
    df.columns = [c.lower() for c in df.columns]
    return df


# ---------------------------------------------------------------------------
# 1. load_trial_nodes
# ---------------------------------------------------------------------------

async def load_trial_nodes(driver, csv_path: str | Path) -> int:
    """Create/update (:Trial) nodes from neo4j_trials.csv. Returns count."""
    df = _lowercase_columns(pd.read_csv(csv_path, dtype=str).fillna(""))
    total = 0

    async with driver.session() as session:
        for i in range(0, len(df), TRIAL_BATCH):
            chunk = df.iloc[i : i + TRIAL_BATCH]
            batch = chunk.to_dict("records")
            await session.run(
                """
                UNWIND $batch AS t
                MERGE (trial:Trial {nct_id: t.nct_id})
                SET trial.title            = t.title,
                    trial.brief_summary    = t.brief_summary,
                    trial.phase            = t.phase,
                    trial.status           = t.status,
                    trial.enrollment       = toIntegerOrNull(t.enrollment),
                    trial.start_date       = t.start_date,
                    trial.sponsor          = t.sponsor,
                    trial.min_age          = toIntegerOrNull(t.min_age),
                    trial.max_age          = toIntegerOrNull(t.max_age),
                    trial.gender           = t.gender,
                    trial.therapeutic_area = t.therapeutic_area,
                    trial.url              = t.url
                """,
                batch=batch,
            )
            total += len(batch)
            if total % 1_000 < TRIAL_BATCH:
                print(f"    Trial nodes: {total:,} / {len(df):,}")

    print(f"  Loaded {total:,} Trial nodes.")
    return total


# ---------------------------------------------------------------------------
# 2. load_conditions
# ---------------------------------------------------------------------------

async def load_conditions(driver, csv_path: str | Path) -> int:
    """
    Create (:Condition) nodes, [:STUDIES_CONDITION] edges,
    and optional [:MAPS_TO_SNOMED] edges.  Returns total condition rows processed.
    """
    df = _lowercase_columns(pd.read_csv(csv_path, dtype=str).fillna(""))
    total = len(df)

    # --- unique Condition nodes ---
    unique_conds = df.drop_duplicates(subset=["condition_normalized"])
    async with driver.session() as session:
        for i in range(0, len(unique_conds), BATCH_SIZE):
            chunk = unique_conds.iloc[i : i + BATCH_SIZE]
            batch = chunk.to_dict("records")
            await session.run(
                """
                UNWIND $batch AS c
                MERGE (cond:Condition {normalized_name: c.condition_normalized})
                SET cond.name = c.condition_name
                """,
                batch=batch,
            )
    print(f"    Created {len(unique_conds):,} unique Condition nodes.")

    # --- STUDIES_CONDITION edges ---
    async with driver.session() as session:
        for i in range(0, len(df), BATCH_SIZE):
            chunk = df.iloc[i : i + BATCH_SIZE]
            batch = chunk.to_dict("records")
            await session.run(
                """
                UNWIND $batch AS c
                MATCH (t:Trial {nct_id: c.nct_id})
                MATCH (cond:Condition {normalized_name: c.condition_normalized})
                MERGE (t)-[:STUDIES_CONDITION]->(cond)
                """,
                batch=batch,
            )

    # --- MAPS_TO_SNOMED edges (where snomed_concept_id is populated) ---
    df_linked = df[df["snomed_concept_id"].str.len() > 0]
    if not df_linked.empty:
        unique_links = df_linked.drop_duplicates(
            subset=["condition_normalized", "snomed_concept_id"]
        )
        async with driver.session() as session:
            for i in range(0, len(unique_links), BATCH_SIZE):
                chunk = unique_links.iloc[i : i + BATCH_SIZE]
                batch = chunk.to_dict("records")
                await session.run(
                    """
                    UNWIND $batch AS c
                    MATCH (cond:Condition {normalized_name: c.condition_normalized})
                    MATCH (sc:SNOMEDConcept {concept_id: c.snomed_concept_id})
                    MERGE (cond)-[:MAPS_TO_SNOMED]->(sc)
                    """,
                    batch=batch,
                )
        print(f"    Created {len(unique_links):,} MAPS_TO_SNOMED edges.")
    else:
        print("    No SNOMED links found (entity linking runs in a later phase).")

    print(f"  Processed {total:,} condition rows.")
    return total


# ---------------------------------------------------------------------------
# 3. load_interventions
# ---------------------------------------------------------------------------

async def load_interventions(driver, csv_path: str | Path) -> int:
    """
    Create (:Intervention) nodes, [:USES_INTERVENTION] edges,
    and optional [:MAPS_TO_RXNORM] edges.  Returns total rows processed.
    """
    df = _lowercase_columns(pd.read_csv(csv_path, dtype=str).fillna(""))
    total = len(df)

    # --- unique Intervention nodes ---
    unique_intv = df.drop_duplicates(subset=["intervention_normalized"])
    async with driver.session() as session:
        for i in range(0, len(unique_intv), BATCH_SIZE):
            chunk = unique_intv.iloc[i : i + BATCH_SIZE]
            batch = chunk.to_dict("records")
            await session.run(
                """
                UNWIND $batch AS iv
                MERGE (intv:Intervention {normalized_name: iv.intervention_normalized})
                SET intv.name = iv.intervention_name,
                    intv.type = iv.intervention_type
                """,
                batch=batch,
            )
    print(f"    Created {len(unique_intv):,} unique Intervention nodes.")

    # --- USES_INTERVENTION edges ---
    async with driver.session() as session:
        for i in range(0, len(df), BATCH_SIZE):
            chunk = df.iloc[i : i + BATCH_SIZE]
            batch = chunk.to_dict("records")
            await session.run(
                """
                UNWIND $batch AS iv
                MATCH (t:Trial {nct_id: iv.nct_id})
                MATCH (intv:Intervention {normalized_name: iv.intervention_normalized})
                MERGE (t)-[:USES_INTERVENTION]->(intv)
                """,
                batch=batch,
            )

    # --- MAPS_TO_RXNORM edges (where rxnorm_rxcui is populated) ---
    df_linked = df[df["rxnorm_rxcui"].str.len() > 0]
    if not df_linked.empty:
        unique_links = df_linked.drop_duplicates(
            subset=["intervention_normalized", "rxnorm_rxcui"]
        )
        async with driver.session() as session:
            for i in range(0, len(unique_links), BATCH_SIZE):
                chunk = unique_links.iloc[i : i + BATCH_SIZE]
                batch = chunk.to_dict("records")
                await session.run(
                    """
                    UNWIND $batch AS iv
                    MATCH (intv:Intervention {normalized_name: iv.intervention_normalized})
                    MATCH (rx:RxNormConcept {rxcui: iv.rxnorm_rxcui})
                    MERGE (intv)-[:MAPS_TO_RXNORM]->(rx)
                    """,
                    batch=batch,
                )
        print(f"    Created {len(unique_links):,} MAPS_TO_RXNORM edges.")
    else:
        print("    No RxNorm links found (entity linking runs in a later phase).")

    print(f"  Processed {total:,} intervention rows.")
    return total


# ---------------------------------------------------------------------------
# 4. load_criteria
# ---------------------------------------------------------------------------

async def load_criteria(driver, csv_path: str | Path) -> int:
    """
    Create (:Criterion) nodes and [:HAS_CRITERION] edges in a single pass.
    Skips criteria that already exist in Neo4j (resume-safe).
    Returns total criteria loaded in this run.
    """
    df = _lowercase_columns(pd.read_csv(csv_path, dtype=str).fillna(""))

    # --- Check which criteria already exist (for resume) ---
    existing_ids: set[str] = set()
    async with driver.session() as session:
        result = await session.run("MATCH (c:Criterion) RETURN c.id AS id")
        existing_ids = {r["id"] async for r in result}

    if existing_ids:
        before = len(df)
        df = df[~df["criterion_id"].isin(existing_ids)]
        skipped = before - len(df)
        print(f"    Resuming: {skipped:,} criteria already loaded, {len(df):,} remaining.")

    if df.empty:
        print("  All criteria already loaded.")
        return 0

    total = 0

    # --- Single pass: create node + edge together ---
    async with driver.session() as session:
        for i in range(0, len(df), CRITERIA_BATCH):
            chunk = df.iloc[i : i + CRITERIA_BATCH]
            batch = chunk.to_dict("records")
            await session.run(
                """
                UNWIND $batch AS cr
                MATCH (t:Trial {nct_id: cr.nct_id})
                MERGE (c:Criterion {id: cr.criterion_id})
                SET c.type           = cr.criterion_type,
                    c.raw_text       = cr.raw_text,
                    c.parsing_status = COALESCE(cr.parsing_status, 'pending'),
                    c.parsed_json    = cr.parsed_json
                MERGE (t)-[:HAS_CRITERION {type: cr.criterion_type}]->(c)
                """,
                batch=batch,
            )
            total += len(batch)
            if total % 10_000 < CRITERIA_BATCH:
                print(f"    Criterion nodes: {total:,} / {len(df):,}")

    print(f"  Loaded {total:,} Criterion nodes with HAS_CRITERION edges.")
    return total


# ---------------------------------------------------------------------------
# 5. run_trial_loader (orchestrator)
# ---------------------------------------------------------------------------

async def run_trial_loader(
    category: str | None = None,
    resume: bool = False,
) -> None:
    """
    Run the full trial loading pipeline.
    Validates that prerequisite CSVs exist, then loads in sequence.

    With ``resume=True``, skips steps 1-3 (trials/conditions/interventions)
    if Trial nodes already exist and jumps straight to criteria loading
    (which itself skips already-loaded criteria).
    """
    csv_files = {
        "trials":        DATA_PROCESSED / "neo4j_trials.csv",
        "conditions":    DATA_PROCESSED / "neo4j_trial_conditions.csv",
        "interventions": DATA_PROCESSED / "neo4j_trial_interventions.csv",
        "criteria":      DATA_PROCESSED / "neo4j_criteria.csv",
    }

    missing = [name for name, path in csv_files.items() if not path.exists()]
    if missing:
        cat_flag = f"--category {category}" if category else "--category all"
        print(f"ERROR: Missing CSV files: {', '.join(missing)}")
        print(f"\nRun the Snowflake export first:")
        print(f"  python -m snowflake_etl.export_for_neo4j {cat_flag}")
        sys.exit(1)

    cfg = get_settings()
    driver = AsyncGraphDatabase.driver(
        cfg.neo4j.uri,
        auth=(cfg.neo4j.user, cfg.neo4j.password),
    )

    try:
        # Verify connectivity
        async with driver.session() as session:
            result = await session.run("RETURN 1 AS n")
            await result.single()
        print("Connected to Neo4j.\n")

        pipeline_start = time.monotonic()

        if resume:
            # Check if steps 1-3 are already done
            async with driver.session() as session:
                result = await session.run(
                    "MATCH (t:Trial) RETURN count(t) AS cnt"
                )
                record = await result.single()
                trial_count = record["cnt"]

            if trial_count > 0:
                print(f"  Resume mode: {trial_count:,} Trial nodes found — "
                      f"skipping steps 1-3.\n")
                steps = [
                    ("4/4 — Loading Eligibility Criteria (resume)",
                     load_criteria, csv_files["criteria"]),
                ]
            else:
                print("  Resume mode: no Trial nodes found — running full pipeline.\n")
                resume = False

        if not resume:
            steps = [
                ("1/4 — Loading Trial nodes",
                 load_trial_nodes, csv_files["trials"]),
                ("2/4 — Loading Conditions",
                 load_conditions, csv_files["conditions"]),
                ("3/4 — Loading Interventions",
                 load_interventions, csv_files["interventions"]),
                ("4/4 — Loading Eligibility Criteria",
                 load_criteria, csv_files["criteria"]),
            ]

        counts = {}
        for label, fn, path in steps:
            t0 = time.monotonic()
            print(f"Step {label} …")
            n = await fn(driver, path)
            elapsed = time.monotonic() - t0
            counts[label] = n
            print(f"  ({elapsed:.1f}s)\n")

        # Summary
        total_elapsed = time.monotonic() - pipeline_start
        print(f"{'='*60}")
        print(f"Trial loading complete in {total_elapsed:.1f}s")
        for label, n in counts.items():
            print(f"  {label}: {n:,}")
        print(f"{'='*60}\n")

    finally:
        await driver.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load Snowflake-exported trial data into Neo4j.",
    )
    parser.add_argument(
        "--category",
        default="all",
        help='Therapeutic area that was exported (for display only). Default: all',
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from where you left off — skips steps 1-3 if Trial nodes "
             "exist, and skips already-loaded criteria.",
    )
    args = parser.parse_args()
    category = None if args.category.lower() == "all" else args.category

    asyncio.run(run_trial_loader(category, resume=args.resume))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print("Phase 6: Loading trials into Neo4j\n")
    main()
