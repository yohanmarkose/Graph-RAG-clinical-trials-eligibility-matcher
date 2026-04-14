"""
Phase 5 (Prompt 7): Load SNOMED CT into Neo4j and create all graph indexes.

Functions:
  1. create_indexes()              — constraints + indexes for the full schema
  2. load_snomed_concepts()        — (:SNOMEDConcept) nodes from CSV
  3. load_snomed_relationships()   — [:IS_A] edges from CSV
  4. verify_snomed()               — validation queries

Usage:
    Called by kg_builder/build_ontology.py (the orchestrator).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

BATCH_SIZE = 5_000


# ---------------------------------------------------------------------------
# 1. create_indexes
# ---------------------------------------------------------------------------

_INDEX_STATEMENTS = [
    "CREATE CONSTRAINT trial_nct_id IF NOT EXISTS FOR (t:Trial) REQUIRE t.nct_id IS UNIQUE",
    "CREATE INDEX trial_status IF NOT EXISTS FOR (t:Trial) ON (t.status)",
    "CREATE INDEX trial_phase IF NOT EXISTS FOR (t:Trial) ON (t.phase)",
    "CREATE INDEX trial_therapeutic_area IF NOT EXISTS FOR (t:Trial) ON (t.therapeutic_area)",
    "CREATE CONSTRAINT snomed_id IF NOT EXISTS FOR (s:SNOMEDConcept) REQUIRE s.concept_id IS UNIQUE",
    "CREATE CONSTRAINT rxnorm_id IF NOT EXISTS FOR (r:RxNormConcept) REQUIRE r.rxcui IS UNIQUE",
    "CREATE INDEX condition_name IF NOT EXISTS FOR (c:Condition) ON (c.normalized_name)",
    "CREATE INDEX intervention_name IF NOT EXISTS FOR (i:Intervention) ON (i.normalized_name)",
    "CREATE INDEX criterion_type IF NOT EXISTS FOR (cr:Criterion) ON (cr.type)",
    "CREATE INDEX criterion_parsing_status IF NOT EXISTS FOR (cr:Criterion) ON (cr.parsing_status)",
    "CREATE INDEX biomarker_name IF NOT EXISTS FOR (b:Biomarker) ON (b.normalized_name)",
]


async def create_indexes(driver) -> None:
    """Create all uniqueness constraints and indexes (idempotent)."""
    async with driver.session() as session:
        for stmt in _INDEX_STATEMENTS:
            await session.run(stmt)
            short = stmt.split("IF NOT EXISTS")[0].strip()
            logger.info("  OK  %s", short)
    print(f"  Created/verified {len(_INDEX_STATEMENTS)} indexes & constraints.")


# ---------------------------------------------------------------------------
# 2. load_snomed_concepts
# ---------------------------------------------------------------------------

async def load_snomed_concepts(driver, csv_path: str | Path) -> int:
    """
    Load (:SNOMEDConcept) nodes from snomed_concepts.csv.
    Returns total nodes merged.
    """
    csv_path = Path(csv_path)
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    total = 0

    async with driver.session() as session:
        for i in range(0, len(df), BATCH_SIZE):
            chunk = df.iloc[i : i + BATCH_SIZE]
            batch = chunk.to_dict("records")
            await session.run(
                """
                UNWIND $batch AS row
                MERGE (s:SNOMEDConcept {concept_id: row.concept_id})
                SET s.term = row.preferred_term, s.semantic_tag = row.semantic_tag
                """,
                batch=batch,
            )
            total += len(batch)
            if total % 10_000 < BATCH_SIZE:
                print(f"    SNOMED concepts: {total:,} / {len(df):,}")

    print(f"  Loaded {total:,} SNOMED concepts.")
    return total


# ---------------------------------------------------------------------------
# 3. load_snomed_relationships
# ---------------------------------------------------------------------------

async def load_snomed_relationships(driver, csv_path: str | Path) -> int:
    """
    Load [:IS_A] relationships between SNOMEDConcept nodes.
    Returns total relationships merged.
    """
    csv_path = Path(csv_path)
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    # Only IS_A relationships for the hierarchy
    df_isa = df[df["type"].str.lower() == "is_a"]
    total = 0

    async with driver.session() as session:
        for i in range(0, len(df_isa), BATCH_SIZE):
            chunk = df_isa.iloc[i : i + BATCH_SIZE]
            batch = chunk.to_dict("records")
            await session.run(
                """
                UNWIND $batch AS row
                MATCH (child:SNOMEDConcept {concept_id: row.source_id})
                MATCH (parent:SNOMEDConcept {concept_id: row.destination_id})
                MERGE (child)-[:IS_A]->(parent)
                """,
                batch=batch,
            )
            total += len(batch)
            if total % 10_000 < BATCH_SIZE:
                print(f"    SNOMED IS_A rels: {total:,} / {len(df_isa):,}")

    print(f"  Loaded {total:,} SNOMED IS_A relationships.")
    return total


# ---------------------------------------------------------------------------
# 4. verify_snomed
# ---------------------------------------------------------------------------

async def verify_snomed(driver) -> None:
    """Run validation queries and print results."""
    async with driver.session() as session:
        # Total nodes
        result = await session.run("MATCH (s:SNOMEDConcept) RETURN count(s) AS cnt")
        record = await result.single()
        node_count = record["cnt"]

        # Total IS_A rels
        result = await session.run(
            "MATCH (:SNOMEDConcept)-[r:IS_A]->(:SNOMEDConcept) RETURN count(r) AS cnt"
        )
        record = await result.single()
        rel_count = record["cnt"]

        print(f"\n  SNOMED verification:")
        print(f"    Total SNOMEDConcept nodes : {node_count:,}")
        print(f"    Total IS_A relationships  : {rel_count:,}")

        # Ancestor test: Malignant neoplasm of breast (254837009) up to 3 hops
        result = await session.run(
            """
            MATCH path = (c:SNOMEDConcept {concept_id: '254837009'})-[:IS_A*1..3]->(ancestor:SNOMEDConcept)
            RETURN ancestor.concept_id AS id, ancestor.term AS term, length(path) AS hops
            ORDER BY hops
            LIMIT 10
            """
        )
        records = [r async for r in result]
        if records:
            print(f"    Ancestors of 'Malignant neoplasm of breast' (254837009), up to 3 hops:")
            for r in records:
                print(f"      [{r['hops']} hop(s)] {r['id']} — {r['term']}")
        else:
            print("    WARNING: concept 254837009 not found or no IS_A ancestors.")
