"""
Phase 5 (Prompt 7): Ontology backbone orchestrator.

Connects to Neo4j and runs in order:
  1. Create indexes & constraints
  2. Load SNOMED CT concepts + IS_A relationships
  3. Load RxNorm concepts + typed relationships
  4. Verify both ontologies

Usage:
    python -m kg_builder.build_ontology
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

from neo4j import AsyncGraphDatabase

from config.settings import get_settings
from kg_builder.load_snomed import (
    create_indexes,
    load_snomed_concepts,
    load_snomed_relationships,
    verify_snomed,
)
from kg_builder.load_rxnorm import (
    load_rxnorm_concepts,
    load_rxnorm_relationships,
    verify_rxnorm,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"

# Expected CSV files
SNOMED_CONCEPTS_CSV = DATA_PROCESSED / "snomed_concepts.csv"
SNOMED_RELATIONSHIPS_CSV = DATA_PROCESSED / "snomed_relationships.csv"
RXNORM_CONCEPTS_CSV = DATA_PROCESSED / "rxnorm_concepts.csv"
RXNORM_RELATIONSHIPS_CSV = DATA_PROCESSED / "rxnorm_relationships.csv"


async def build_ontology() -> None:
    """Run the full ontology build pipeline."""
    # Validate prerequisites
    missing = []
    for path in [
        SNOMED_CONCEPTS_CSV,
        SNOMED_RELATIONSHIPS_CSV,
        RXNORM_CONCEPTS_CSV,
        RXNORM_RELATIONSHIPS_CSV,
    ]:
        if not path.exists():
            missing.append(str(path))
    if missing:
        print("ERROR: Missing prerequisite CSV files:")
        for m in missing:
            print(f"  - {m}")
        print("\nRun Phase 3 parsers first:")
        print("  python -m data_ingestion.parse_snomed")
        print("  python -m data_ingestion.parse_rxnorm")
        sys.exit(1)

    # Connect to Neo4j
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

        # Step 1: Indexes & constraints
        t0 = time.monotonic()
        print("Step 1/5 — Creating indexes & constraints …")
        await create_indexes(driver)
        print(f"  ({time.monotonic() - t0:.1f}s)\n")

        # Step 2: SNOMED concepts
        t0 = time.monotonic()
        print("Step 2/5 — Loading SNOMED CT concepts …")
        await load_snomed_concepts(driver, SNOMED_CONCEPTS_CSV)
        print(f"  ({time.monotonic() - t0:.1f}s)\n")

        # Step 3: SNOMED relationships
        t0 = time.monotonic()
        print("Step 3/5 — Loading SNOMED CT IS_A relationships …")
        await load_snomed_relationships(driver, SNOMED_RELATIONSHIPS_CSV)
        print(f"  ({time.monotonic() - t0:.1f}s)\n")

        # Step 4: RxNorm concepts
        t0 = time.monotonic()
        print("Step 4/5 — Loading RxNorm concepts …")
        await load_rxnorm_concepts(driver, RXNORM_CONCEPTS_CSV)
        print(f"  ({time.monotonic() - t0:.1f}s)\n")

        # Step 5: RxNorm relationships
        t0 = time.monotonic()
        print("Step 5/5 — Loading RxNorm relationships …")
        await load_rxnorm_relationships(driver, RXNORM_RELATIONSHIPS_CSV)
        print(f"  ({time.monotonic() - t0:.1f}s)\n")

        # Verification
        print("=" * 60)
        print("Verification\n")
        await verify_snomed(driver)
        await verify_rxnorm(driver)

        total_elapsed = time.monotonic() - pipeline_start
        print(f"\n{'=' * 60}")
        print(f"Ontology backbone build complete in {total_elapsed:.1f}s")
        print(f"{'=' * 60}\n")

    finally:
        await driver.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print("Phase 5: Building ontology backbone in Neo4j\n")
    asyncio.run(build_ontology())
