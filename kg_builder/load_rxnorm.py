"""
Phase 5 (Prompt 7): Load RxNorm into Neo4j.

Functions:
  1. load_rxnorm_concepts()        — (:RxNormConcept) nodes from CSV
  2. load_rxnorm_relationships()   — typed edges from CSV
  3. verify_rxnorm()               — validation queries

Usage:
    Called by kg_builder/build_ontology.py (the orchestrator).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

BATCH_SIZE = 5_000

# Map CSV relationship_type values to Neo4j relationship types
_REL_TYPE_MAP = {
    "has_ingredient": "HAS_INGREDIENT",
    "tradename_of":   "TRADENAME_OF",
    "consists_of":    "CONSISTS_OF",
    "form_of":        "FORM_OF",
}


# ---------------------------------------------------------------------------
# 1. load_rxnorm_concepts
# ---------------------------------------------------------------------------

async def load_rxnorm_concepts(driver, csv_path: str | Path) -> int:
    """
    Load (:RxNormConcept) nodes from rxnorm_concepts.csv.
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
                MERGE (r:RxNormConcept {rxcui: row.rxcui})
                SET r.name = row.name, r.tty = row.tty
                """,
                batch=batch,
            )
            total += len(batch)
            if total % 10_000 < BATCH_SIZE:
                print(f"    RxNorm concepts: {total:,} / {len(df):,}")

    print(f"  Loaded {total:,} RxNorm concepts.")
    return total


# ---------------------------------------------------------------------------
# 2. load_rxnorm_relationships
# ---------------------------------------------------------------------------

async def load_rxnorm_relationships(driver, csv_path: str | Path) -> int:
    """
    Load typed relationships between RxNormConcept nodes.
    Groups by relationship type and creates the appropriate Neo4j edge.
    Returns total relationships merged.
    """
    csv_path = Path(csv_path)
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    total = 0

    async with driver.session() as session:
        for rel_csv_type, neo4j_type in _REL_TYPE_MAP.items():
            df_rel = df[df["relationship_type"] == rel_csv_type]
            if df_rel.empty:
                continue

            # Neo4j does not allow parameterised relationship types,
            # so we use one query per type with the type baked in.
            cypher = f"""
                UNWIND $batch AS row
                MATCH (src:RxNormConcept {{rxcui: row.source_rxcui}})
                MATCH (tgt:RxNormConcept {{rxcui: row.target_rxcui}})
                MERGE (src)-[:{neo4j_type}]->(tgt)
            """

            for i in range(0, len(df_rel), BATCH_SIZE):
                chunk = df_rel.iloc[i : i + BATCH_SIZE]
                batch = chunk.to_dict("records")
                await session.run(cypher, batch=batch)
                total += len(batch)
                if total % 10_000 < BATCH_SIZE:
                    print(f"    RxNorm rels: {total:,} / {len(df):,}")

    print(f"  Loaded {total:,} RxNorm relationships.")
    return total


# ---------------------------------------------------------------------------
# 3. verify_rxnorm
# ---------------------------------------------------------------------------

async def verify_rxnorm(driver) -> None:
    """Run validation queries and print results."""
    async with driver.session() as session:
        # Total nodes
        result = await session.run("MATCH (r:RxNormConcept) RETURN count(r) AS cnt")
        record = await result.single()
        node_count = record["cnt"]

        # Total rels (all types)
        result = await session.run(
            """
            MATCH (:RxNormConcept)-[r]->(:RxNormConcept)
            RETURN type(r) AS rel_type, count(r) AS cnt
            ORDER BY cnt DESC
            """
        )
        rel_rows = [r async for r in result]

        print(f"\n  RxNorm verification:")
        print(f"    Total RxNormConcept nodes : {node_count:,}")
        for r in rel_rows:
            print(f"    {r['rel_type']:<25} : {r['cnt']:,}")

        # Test: trastuzumab brand names and ingredients
        result = await session.run(
            """
            MATCH (drug:RxNormConcept)
            WHERE toLower(drug.name) CONTAINS 'trastuzumab'
            OPTIONAL MATCH (drug)<-[:TRADENAME_OF]-(brand:RxNormConcept)
            OPTIONAL MATCH (drug)-[:HAS_INGREDIENT]->(ing:RxNormConcept)
            RETURN drug.rxcui AS rxcui, drug.name AS name,
                   collect(DISTINCT brand.name) AS brands,
                   collect(DISTINCT ing.name) AS ingredients
            LIMIT 5
            """
        )
        records = [r async for r in result]
        if records:
            print(f"    Trastuzumab lookup:")
            for r in records:
                print(f"      {r['rxcui']} — {r['name']}")
                if r["brands"]:
                    print(f"        brands: {r['brands'][:5]}")
                if r["ingredients"]:
                    print(f"        ingredients: {r['ingredients'][:5]}")
        else:
            print("    WARNING: 'trastuzumab' not found in RxNorm data.")
