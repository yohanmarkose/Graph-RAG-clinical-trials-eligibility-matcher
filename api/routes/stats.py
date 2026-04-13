"""GET /stats — knowledge graph statistics."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from api.dependencies import get_driver

logger = logging.getLogger(__name__)
router = APIRouter(tags=["stats"])

_DB = "neo4j"


@router.get("/stats")
async def get_stats(driver=Depends(get_driver)):
    """Return graph statistics: node counts, trials by therapeutic area, phase, and status."""
    async with driver.session(database=_DB) as session:

        # --- Overall node counts (chained WITH carries each count forward) ---
        counts_result = await session.run(
            """
            MATCH (t:Trial)          WITH count(t)  AS total_trials
            MATCH (sc:SNOMEDConcept) WITH total_trials, count(sc) AS snomed_concepts
            MATCH (rx:RxNormConcept) WITH total_trials, snomed_concepts, count(rx) AS rxnorm_concepts
            MATCH (cr:Criterion)
            RETURN total_trials, snomed_concepts, rxnorm_concepts, count(cr) AS criteria
            """
        )
        counts = await counts_result.single()

        # --- Trials by therapeutic area ---
        area_result = await session.run(
            """
            MATCH (t:Trial)
            RETURN coalesce(t.therapeutic_area, 'unknown') AS area, count(t) AS count
            ORDER BY count DESC
            """
        )
        by_area: dict[str, int] = {r["area"]: r["count"] async for r in area_result}

        # --- Trials by phase ---
        phase_result = await session.run(
            """
            MATCH (t:Trial)
            RETURN coalesce(t.phase, 'unknown') AS phase, count(t) AS count
            ORDER BY count DESC
            """
        )
        by_phase: dict[str, int] = {r["phase"]: r["count"] async for r in phase_result}

        # --- Trials by status ---
        status_result = await session.run(
            """
            MATCH (t:Trial)
            RETURN coalesce(t.status, 'unknown') AS status, count(t) AS count
            ORDER BY count DESC
            """
        )
        by_status: dict[str, int] = {r["status"]: r["count"] async for r in status_result}

    return {
        "totals": {
            "trials": counts["total_trials"] if counts else 0,
            "snomed_concepts": counts["snomed_concepts"] if counts else 0,
            "rxnorm_concepts": counts["rxnorm_concepts"] if counts else 0,
            "criteria": counts["criteria"] if counts else 0,
        },
        "trials_by_therapeutic_area": by_area,
        "trials_by_phase": by_phase,
        "trials_by_status": by_status,
    }
