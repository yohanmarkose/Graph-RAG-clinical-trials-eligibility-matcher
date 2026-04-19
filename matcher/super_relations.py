"""
Super-relation definitions and KG traversal interface for ReKnoS.

A super-relation groups semantically similar fine-grained KG relations
under a single abstract label. The ClinicalTrialsKGInterface provides
async Neo4j traversal for each super-relation.

Reference: "Reasoning over Knowledge Graphs with Super-Relations"
           ReKnoS, ICLR 2025 — https://openreview.net/forum?id=rTCJ29pkuA
"""

from __future__ import annotations

import logging
from typing import TypedDict

from neo4j import AsyncDriver  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class EntityRef(TypedDict):
    """A typed reference to a single node in the clinical trials KG."""

    id: str
    """Node identifier: concept_id for SNOMED, rxcui for RxNorm,
    normalized_name for Biomarker, elementId() for Criterion, nct_id for Trial."""

    label: str
    """Human-readable display name for LLM prompts."""

    node_type: str
    """One of: SNOMEDConcept | RxNormConcept | Biomarker | Criterion | Trial."""


# ---------------------------------------------------------------------------
# Super-relation registry
#
# Each entry describes one abstract traversal step over the KG.
#   description  — shown to the LLM when scoring relation relevance
#   input_types  — set of node_type strings this relation can start from
#   output_type  — node_type produced at the far end
#   cypher       — parameterised Cypher; receives $entity_ids (list of id strings)
# ---------------------------------------------------------------------------

SUPER_RELATIONS: dict[str, dict] = {
    # ── SNOMED hierarchy ─────────────────────────────────────────────────────
    "snomed_generalize": {
        "description": (
            "Walk up the SNOMED IS_A hierarchy to find broader (parent) "
            "disease concepts — useful when a trial targets a parent category"
        ),
        "input_types": {"SNOMEDConcept"},
        "output_type": "SNOMEDConcept",
        "cypher": """
            UNWIND $entity_ids AS eid
            MATCH (s:SNOMEDConcept {concept_id: eid})-[:IS_A]->(parent:SNOMEDConcept)
            RETURN DISTINCT parent.concept_id AS id,
                            parent.term       AS label,
                            'SNOMEDConcept'   AS node_type
        """,
    },
    "snomed_specialize": {
        "description": (
            "Walk down the SNOMED IS_A hierarchy to find narrower (child) "
            "disease subtypes — useful when a trial targets a specific subtype"
        ),
        "input_types": {"SNOMEDConcept"},
        "output_type": "SNOMEDConcept",
        "cypher": """
            UNWIND $entity_ids AS eid
            MATCH (child:SNOMEDConcept)-[:IS_A]->(s:SNOMEDConcept {concept_id: eid})
            RETURN DISTINCT child.concept_id AS id,
                            child.term       AS label,
                            'SNOMEDConcept'  AS node_type
        """,
    },
    # ── Condition → Criteria ─────────────────────────────────────────────────
    "condition_match": {
        "description": (
            "Find inclusion criteria that require this SNOMED disease concept "
            "— directly links a condition to trials that include it"
        ),
        "input_types": {"SNOMEDConcept"},
        "output_type": "Criterion",
        "cypher": """
            UNWIND $entity_ids AS eid
            MATCH (s:SNOMEDConcept {concept_id: eid})<-[:REQUIRES_CONDITION]-(c:Criterion)
            RETURN DISTINCT elementId(c)                                   AS id,
                            coalesce(c.text, c.description, 'criterion')   AS label,
                            'Criterion'                                    AS node_type
        """,
    },
    "condition_exclude": {
        "description": (
            "Find exclusion criteria that rule out patients with this SNOMED "
            "disease concept — helps identify trials that would exclude the patient"
        ),
        "input_types": {"SNOMEDConcept"},
        "output_type": "Criterion",
        "cypher": """
            UNWIND $entity_ids AS eid
            MATCH (s:SNOMEDConcept {concept_id: eid})<-[:EXCLUDES_CONDITION]-(c:Criterion)
            RETURN DISTINCT elementId(c)                                   AS id,
                            coalesce(c.text, c.description, 'criterion')   AS label,
                            'Criterion'                                    AS node_type
        """,
    },
    # ── Biomarker → Criteria ─────────────────────────────────────────────────
    "biomarker_match": {
        "description": (
            "Find inclusion criteria that require a specific biomarker status "
            "(e.g. HER2-positive, EGFR-mutant) — links biomarker to eligibility criteria"
        ),
        "input_types": {"Biomarker"},
        "output_type": "Criterion",
        "cypher": """
            UNWIND $entity_ids AS eid
            MATCH (b:Biomarker {normalized_name: eid})<-[:REQUIRES_BIOMARKER]-(c:Criterion)
            RETURN DISTINCT elementId(c)                                   AS id,
                            coalesce(c.text, c.description, 'criterion')   AS label,
                            'Criterion'                                    AS node_type
        """,
    },
    # ── Drug/Therapy → Criteria ───────────────────────────────────────────────
    "therapy_match": {
        "description": (
            "Find inclusion criteria that require prior treatment with a specific "
            "drug (RxNorm concept) — links prior therapy to eligibility criteria"
        ),
        "input_types": {"RxNormConcept"},
        "output_type": "Criterion",
        "cypher": """
            UNWIND $entity_ids AS eid
            MATCH (rx:RxNormConcept {rxcui: eid})<-[:REQUIRES_PRIOR_DRUG]-(c:Criterion)
            RETURN DISTINCT elementId(c)                                   AS id,
                            coalesce(c.text, c.description, 'criterion')   AS label,
                            'Criterion'                                    AS node_type
        """,
    },
    # ── Criteria → Trial ─────────────────────────────────────────────────────
    "criterion_to_trial": {
        "description": (
            "Follow a matched criterion back to the clinical trial it belongs to "
            "— the final hop that yields candidate trials"
        ),
        "input_types": {"Criterion"},
        "output_type": "Trial",
        "cypher": """
            MATCH (c:Criterion)
            WHERE elementId(c) IN $entity_ids
              AND NOT c:_deleted
            MATCH (t:Trial)-[:HAS_CRITERION]->(c)
            WHERE t.status IN ['RECRUITING', 'ACTIVE_NOT_RECRUITING']
            RETURN DISTINCT t.nct_id  AS id,
                            t.title   AS label,
                            'Trial'   AS node_type
        """,
    },
}


class ClinicalTrialsKGInterface:
    """Async Neo4j interface for ReKnoS super-relation traversal.

    Args:
        driver:   Async Neo4j driver.
        database: Neo4j database name (default ``"neo4j"``).
    """

    def __init__(self, driver: AsyncDriver, database: str = "neo4j") -> None:
        self._driver = driver
        self._database = database

    # ------------------------------------------------------------------
    # Super-relation selection (no LLM, no I/O)
    # ------------------------------------------------------------------

    def get_applicable_super_relations(
        self,
        entities: list[EntityRef],
        current_path: list[str],
    ) -> list[str]:
        """Return the names of super-relations applicable to the current frontier.

        A super-relation is applicable when at least one entity in the frontier
        has a node_type that matches the super-relation's ``input_types``.
        Already-traversed super-relations are excluded to avoid cycles.

        Args:
            entities:     Current frontier nodes.
            current_path: Super-relation names already used in this reasoning chain.

        Returns:
            Ordered list of applicable super-relation names.
        """
        present_types: set[str] = {e["node_type"] for e in entities}
        used: set[str] = set(current_path)
        return [
            name
            for name, defn in SUPER_RELATIONS.items()
            if name not in used and defn["input_types"] & present_types
        ]

    # ------------------------------------------------------------------
    # KG traversal
    # ------------------------------------------------------------------

    async def get_entities_via_super_relation(
        self,
        entities: list[EntityRef],
        super_rel: str,
    ) -> list[EntityRef]:
        """Execute the super-relation's Cypher and return reachable entities.

        Only entities whose ``node_type`` matches the super-relation's
        ``input_types`` are used as starting points.

        Args:
            entities:  Current frontier.
            super_rel: Name of the super-relation to traverse.

        Returns:
            List of EntityRef nodes reachable via the super-relation.
        """
        defn = SUPER_RELATIONS.get(super_rel)
        if defn is None:
            logger.warning("ReKnoS: unknown super-relation %r — skipping", super_rel)
            return []

        valid_ids = [
            e["id"] for e in entities if e["node_type"] in defn["input_types"]
        ]
        if not valid_ids:
            return []

        reached: list[EntityRef] = []
        async with self._driver.session(database=self._database) as session:
            result = await session.run(defn["cypher"], entity_ids=valid_ids)
            async for record in result:
                reached.append(
                    EntityRef(
                        id=str(record["id"]),
                        label=str(record["label"]),
                        node_type=record["node_type"],
                    )
                )

        logger.debug(
            "super_rel=%s | inputs=%d → reached=%d %s nodes",
            super_rel,
            len(valid_ids),
            len(reached),
            defn["output_type"],
        )
        return reached

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def extract_trial_ids(entities: list[EntityRef]) -> list[str]:
        """Return the NCT IDs of any Trial nodes in the frontier."""
        return [e["id"] for e in entities if e["node_type"] == "Trial"]
