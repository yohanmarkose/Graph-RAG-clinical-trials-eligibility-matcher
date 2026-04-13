"""GET /trials/{nct_id} — full trial detail with criteria and entity links."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_driver

logger = logging.getLogger(__name__)
router = APIRouter(tags=["trials"])

_DB = "neo4j"


@router.get("/trials/{nct_id}")
async def get_trial(
    nct_id: str,
    driver=Depends(get_driver),
):
    """Return full trial metadata including parsed criteria with SNOMED/RxNorm links."""
    async with driver.session(database=_DB) as session:

        # --- Trial node + conditions + interventions ---
        trial_result = await session.run(
            """
            MATCH (t:Trial {nct_id: $nct_id})
            OPTIONAL MATCH (t)-[:STUDIES_CONDITION]->(c:Condition)
            OPTIONAL MATCH (t)-[:USES_INTERVENTION]->(i:Intervention)
            RETURN t,
                   collect(DISTINCT c.name)               AS conditions,
                   collect(DISTINCT {name: i.name, type: i.type}) AS interventions
            """,
            nct_id=nct_id,
        )
        record = await trial_result.single()

    if not record:
        raise HTTPException(status_code=404, detail=f"Trial {nct_id} not found")

    trial_props = dict(record["t"])
    conditions = [c for c in record["conditions"] if c]
    interventions = [i for i in record["interventions"] if i.get("name")]

    # --- Criteria with linked entities ---
    async with driver.session(database=_DB) as session:
        crit_result = await session.run(
            """
            MATCH (t:Trial {nct_id: $nct_id})-[r:HAS_CRITERION]->(cr:Criterion)
            OPTIONAL MATCH (cr)-[:REQUIRES_CONDITION]->(sc:SNOMEDConcept)
            OPTIONAL MATCH (cr)-[rel_bm:REQUIRES_BIOMARKER]->(b:Biomarker)
            OPTIONAL MATCH (cr)-[:REQUIRES_PRIOR_DRUG]->(rx:RxNormConcept)
            OPTIONAL MATCH (cr)-[:EXCLUDES_CONDITION]->(exc_sc:SNOMEDConcept)
            OPTIONAL MATCH (cr)-[:EXCLUDES_PRIOR_DRUG]->(exc_rx:RxNormConcept)
            RETURN r.type                                              AS criterion_type,
                   cr.text                                            AS text,
                   cr.category                                        AS category,
                   collect(DISTINCT {concept_id: sc.concept_id,
                                     term: sc.preferred_term})        AS required_conditions,
                   collect(DISTINCT {name: b.normalized_name,
                                     status: rel_bm.status})          AS required_biomarkers,
                   collect(DISTINCT {rxcui: rx.rxcui,
                                     name: rx.name})                  AS required_drugs,
                   collect(DISTINCT {concept_id: exc_sc.concept_id,
                                     term: exc_sc.preferred_term})    AS excluded_conditions,
                   collect(DISTINCT {rxcui: exc_rx.rxcui,
                                     name: exc_rx.name})              AS excluded_drugs
            ORDER BY criterion_type
            """,
            nct_id=nct_id,
        )

        criteria: list[dict] = []
        async for row in crit_result:
            criteria.append(
                {
                    "type": row["criterion_type"],
                    "text": row["text"],
                    "category": row["category"],
                    "required_conditions": [
                        c for c in row["required_conditions"] if c.get("concept_id")
                    ],
                    "required_biomarkers": [
                        b for b in row["required_biomarkers"] if b.get("name")
                    ],
                    "required_drugs": [
                        d for d in row["required_drugs"] if d.get("rxcui")
                    ],
                    "excluded_conditions": [
                        c for c in row["excluded_conditions"] if c.get("concept_id")
                    ],
                    "excluded_drugs": [
                        d for d in row["excluded_drugs"] if d.get("rxcui")
                    ],
                }
            )

    return {
        **trial_props,
        "conditions": conditions,
        "interventions": interventions,
        "criteria": criteria,
    }
