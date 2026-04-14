"""
Phase 6 (Prompt 8): Exact-match entity linking in Neo4j.

Links:
  - (:Condition)    → [:MAPS_TO_SNOMED] → (:SNOMEDConcept)   via exact name match
  - (:Intervention) → [:MAPS_TO_RXNORM] → (:RxNormConcept)   via exact name match

Fuzzy matching is deferred to Phase 7 (RapidFuzz-based linker).

Usage:
    Called after load_trials.py, or standalone:
        python -m kg_builder.link_entities
"""

from __future__ import annotations

import asyncio
import logging
import time

from neo4j import AsyncGraphDatabase

from config.settings import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. link_conditions_to_snomed
# ---------------------------------------------------------------------------

async def link_conditions_to_snomed(driver) -> tuple[int, int]:
    """
    For each (:Condition) without a MAPS_TO_SNOMED edge, attempt an exact
    match against SNOMEDConcept.term (case-insensitive).

    Returns (matched, total_unlinked).
    """
    async with driver.session() as session:
        # Count unlinked conditions
        result = await session.run(
            """
            MATCH (cond:Condition)
            WHERE NOT (cond)-[:MAPS_TO_SNOMED]->()
            RETURN count(cond) AS cnt
            """
        )
        record = await result.single()
        total_unlinked = record["cnt"]

        if total_unlinked == 0:
            print("  All Condition nodes already linked to SNOMED.")
            return 0, 0

        # Exact match: condition.normalized_name == toLower(snomed.term)
        result = await session.run(
            """
            MATCH (cond:Condition)
            WHERE NOT (cond)-[:MAPS_TO_SNOMED]->()
            WITH cond
            MATCH (sc:SNOMEDConcept)
            WHERE toLower(sc.term) = cond.normalized_name
            MERGE (cond)-[:MAPS_TO_SNOMED]->(sc)
            RETURN count(*) AS matched
            """
        )
        record = await result.single()
        matched = record["matched"]

    print(f"  Conditions linked to SNOMED: {matched:,} / {total_unlinked:,} unlinked")
    return matched, total_unlinked


# ---------------------------------------------------------------------------
# 2. link_interventions_to_rxnorm
# ---------------------------------------------------------------------------

async def link_interventions_to_rxnorm(driver) -> tuple[int, int]:
    """
    For each (:Intervention) without a MAPS_TO_RXNORM edge, attempt an exact
    match against RxNormConcept.name (case-insensitive).

    Returns (matched, total_unlinked).
    """
    async with driver.session() as session:
        # Count unlinked interventions
        result = await session.run(
            """
            MATCH (intv:Intervention)
            WHERE NOT (intv)-[:MAPS_TO_RXNORM]->()
            RETURN count(intv) AS cnt
            """
        )
        record = await result.single()
        total_unlinked = record["cnt"]

        if total_unlinked == 0:
            print("  All Intervention nodes already linked to RxNorm.")
            return 0, 0

        # Exact match: intervention.normalized_name == toLower(rxnorm.name)
        result = await session.run(
            """
            MATCH (intv:Intervention)
            WHERE NOT (intv)-[:MAPS_TO_RXNORM]->()
            WITH intv
            MATCH (rx:RxNormConcept)
            WHERE toLower(rx.name) = intv.normalized_name
            MERGE (intv)-[:MAPS_TO_RXNORM]->(rx)
            RETURN count(*) AS matched
            """
        )
        record = await result.single()
        matched = record["matched"]

    print(f"  Interventions linked to RxNorm: {matched:,} / {total_unlinked:,} unlinked")
    return matched, total_unlinked


# ---------------------------------------------------------------------------
# run_entity_linking (orchestrator)
# ---------------------------------------------------------------------------

async def run_entity_linking() -> None:
    """Connect to Neo4j and run exact-match entity linking."""
    cfg = get_settings()
    driver = AsyncGraphDatabase.driver(
        cfg.neo4j.uri,
        auth=(cfg.neo4j.user, cfg.neo4j.password),
    )

    try:
        async with driver.session() as session:
            result = await session.run("RETURN 1 AS n")
            await result.single()
        print("Connected to Neo4j.\n")

        t0 = time.monotonic()

        print("Step 1/2 — Linking Conditions → SNOMED (exact match) …")
        snomed_matched, snomed_total = await link_conditions_to_snomed(driver)

        print("\nStep 2/2 — Linking Interventions → RxNorm (exact match) …")
        rxnorm_matched, rxnorm_total = await link_interventions_to_rxnorm(driver)

        elapsed = time.monotonic() - t0
        print(f"\n{'='*60}")
        print(f"Entity linking complete in {elapsed:.1f}s")
        print(f"  SNOMED : {snomed_matched:,} matched / {snomed_total:,} unlinked")
        print(f"  RxNorm : {rxnorm_matched:,} matched / {rxnorm_total:,} unlinked")
        print(f"  (Fuzzy matching deferred to Phase 7)")
        print(f"{'='*60}\n")

    finally:
        await driver.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print("Phase 6: Entity linking (exact match)\n")
    asyncio.run(run_entity_linking())
