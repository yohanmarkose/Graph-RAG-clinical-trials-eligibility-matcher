"""
Phase 3: SNOMED CT RF2 parser.

Reads SNOMED CT RF2 release files from data/snomed/, filters to the clinically
relevant subset (~50-100K concepts), builds synonym lookups, and exports CSVs
and JSON for Neo4j loading.

Expected RF2 files in snomed_dir (Snapshot or Full variants both work):
  sct2_Concept_*        — concept IDs and active flag
  sct2_Description_*    — terms / synonyms per concept
  sct2_Relationship_*   — IS_A and other relationships

Usage:
    python -m data_ingestion.parse_snomed
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# RF2 type_id values
FSN_TYPE_ID = "900000000000003001"   # Fully Specified Name
SYN_TYPE_ID = "900000000000013009"   # Synonym

# IS_A relationship type
IS_A_TYPE_ID = "116680003"

# Clinical hierarchy roots to keep
CLINICAL_ROOTS: dict[str, str] = {
    "404684003": "Clinical Finding",
    "71388002":  "Procedure",
    "373873005": "Pharmaceutical/Biologic Product",
    "123037004": "Body Structure",
}

# RF2 column names (tab-delimited, consistent across release types)
CONCEPT_COLS = ["id", "effectiveTime", "active", "moduleId", "definitionStatusId"]
DESCRIPTION_COLS = [
    "id", "effectiveTime", "active", "moduleId", "conceptId",
    "languageCode", "typeId", "term", "caseSignificanceId",
]
RELATIONSHIP_COLS = [
    "id", "effectiveTime", "active", "moduleId",
    "sourceId", "destinationId", "relationshipGroup",
    "typeId", "characteristicTypeId", "modifierId",
]

# Chunk size for reading large RF2 files
CHUNK_SIZE = 500_000


# ---------------------------------------------------------------------------
# 1. load_snomed_concepts
# ---------------------------------------------------------------------------

def load_snomed_concepts(snomed_dir: str) -> pd.DataFrame:
    """
    Load active SNOMED concepts from the RF2 Concept file.

    Returns DataFrame with columns: concept_id, active
    """
    snomed_path = Path(snomed_dir)
    matches = sorted(snomed_path.glob("**/sct2_Concept_*"))
    if not matches:
        raise FileNotFoundError(
            f"No sct2_Concept_* file found under {snomed_dir}. "
            "Place the RF2 release files in data/snomed/ and retry."
        )
    concept_file = matches[0]
    logger.info("Reading concepts from %s", concept_file)

    chunks = []
    for chunk in pd.read_csv(
        concept_file,
        sep="\t",
        dtype=str,
        header=0,
        names=CONCEPT_COLS,
        chunksize=CHUNK_SIZE,
        low_memory=False,
    ):
        chunks.append(chunk[chunk["active"] == "1"][["id", "active"]])

    df = pd.concat(chunks, ignore_index=True)
    df = df.rename(columns={"id": "concept_id"})
    logger.info("Loaded %d active concepts", len(df))
    return df


# ---------------------------------------------------------------------------
# 2. load_snomed_descriptions
# ---------------------------------------------------------------------------

def _strip_semantic_tag(term: str) -> str:
    """Remove trailing parenthetical semantic tag: 'Breast cancer (disorder)' → 'Breast cancer'."""
    return re.sub(r"\s*\([^)]+\)\s*$", "", term).strip()


def _extract_semantic_tag(term: str) -> str | None:
    """Extract semantic tag: 'Breast cancer (disorder)' → 'disorder'."""
    m = re.search(r"\(([^)]+)\)\s*$", term)
    return m.group(1) if m else None


def load_snomed_descriptions(snomed_dir: str) -> pd.DataFrame:
    """
    Load active SNOMED descriptions (FSNs and synonyms) from the RF2 Description file.

    Returns DataFrame with columns:
        concept_id, term, type_id, preferred_term, semantic_tag
    where preferred_term is the cleaned FSN (or fallback synonym) for each concept.
    """
    snomed_path = Path(snomed_dir)
    matches = sorted(snomed_path.glob("**/sct2_Description_*"))
    if not matches:
        raise FileNotFoundError(
            f"No sct2_Description_* file found under {snomed_dir}."
        )
    desc_file = matches[0]
    logger.info("Reading descriptions from %s", desc_file)

    chunks = []
    for chunk in pd.read_csv(
        desc_file,
        sep="\t",
        dtype=str,
        header=0,
        names=DESCRIPTION_COLS,
        chunksize=CHUNK_SIZE,
        low_memory=False,
    ):
        active = chunk[chunk["active"] == "1"][["conceptId", "typeId", "term"]]
        chunks.append(active)

    df = pd.concat(chunks, ignore_index=True)
    df = df.rename(columns={"conceptId": "concept_id"})
    logger.info("Loaded %d active descriptions", len(df))

    # --- Build preferred_term and semantic_tag per concept ---
    # FSN preferred terms (strip semantic tag, extract tag label)
    fsns = df[df["typeId"] == FSN_TYPE_ID].copy()
    fsns["preferred_term"] = fsns["term"].apply(_strip_semantic_tag)
    fsns["semantic_tag"] = fsns["term"].apply(_extract_semantic_tag)
    fsn_preferred = (
        fsns.groupby("concept_id")[["preferred_term", "semantic_tag"]]
        .first()
        .reset_index()
    )

    # Synonym fallback — first synonym per concept
    syns = df[df["typeId"] == SYN_TYPE_ID].copy()
    syn_preferred = (
        syns.groupby("concept_id")["term"]
        .first()
        .reset_index()
        .rename(columns={"term": "preferred_term"})
    )
    syn_preferred["semantic_tag"] = None

    # Outer merge: FSN wins where available, synonym fills the gap
    preferred = syn_preferred.merge(
        fsn_preferred, on="concept_id", how="outer", suffixes=("_syn", "_fsn")
    )
    preferred["preferred_term"] = preferred["preferred_term_fsn"].fillna(
        preferred["preferred_term_syn"]
    )
    preferred["semantic_tag"] = preferred["semantic_tag_fsn"]
    preferred = preferred[["concept_id", "preferred_term", "semantic_tag"]]

    df = df.merge(preferred, on="concept_id", how="left")
    logger.info("Preferred terms resolved for %d concepts", preferred.shape[0])
    return df[["concept_id", "term", "typeId", "preferred_term", "semantic_tag"]].rename(
        columns={"typeId": "type_id"}
    )


# ---------------------------------------------------------------------------
# 3. load_snomed_relationships
# ---------------------------------------------------------------------------

def load_snomed_relationships(snomed_dir: str) -> pd.DataFrame:
    """
    Load active IS_A relationships from the RF2 Relationship file.

    Returns DataFrame with columns: source_id, destination_id
    (semantics: source IS_A destination, i.e. child → parent)
    """
    snomed_path = Path(snomed_dir)
    matches = sorted(snomed_path.glob("**/sct2_Relationship_*"))
    if not matches:
        raise FileNotFoundError(
            f"No sct2_Relationship_* file found under {snomed_dir}."
        )
    rel_file = matches[0]
    logger.info("Reading relationships from %s", rel_file)

    chunks = []
    for chunk in pd.read_csv(
        rel_file,
        sep="\t",
        dtype=str,
        header=0,
        names=RELATIONSHIP_COLS,
        chunksize=CHUNK_SIZE,
        low_memory=False,
    ):
        filtered = chunk[
            (chunk["active"] == "1") & (chunk["typeId"] == IS_A_TYPE_ID)
        ][["sourceId", "destinationId"]]
        chunks.append(filtered)

    df = pd.concat(chunks, ignore_index=True)
    df = df.rename(columns={"sourceId": "source_id", "destinationId": "destination_id"})
    logger.info("Loaded %d active IS_A relationships", len(df))
    return df


# ---------------------------------------------------------------------------
# 4. filter_to_clinical_subset
# ---------------------------------------------------------------------------

def _bfs_descendants(root_id: str, parent_to_children: dict[str, list[str]]) -> set[str]:
    """BFS from root_id down the IS_A hierarchy; returns all descendant concept IDs."""
    visited: set[str] = set()
    queue: deque[str] = deque([root_id])
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        for child in parent_to_children.get(node, []):
            if child not in visited:
                queue.append(child)
    return visited


def filter_to_clinical_subset(
    concepts_df: pd.DataFrame,
    descriptions_df: pd.DataFrame,
    relationships_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Filter all three DataFrames to the clinically relevant subset.

    Keeps only concepts that are descendants of the four clinical hierarchy roots:
      - Clinical Finding (404684003)
      - Procedure (71388002)
      - Pharmaceutical/Biologic Product (373873005)
      - Body Structure (123037004)

    Prints per-root concept counts. Returns (concepts_df, descriptions_df, relationships_df).
    """
    # Build parent → [children] map from IS_A relationships
    parent_to_children: dict[str, list[str]] = defaultdict(list)
    for _, row in relationships_df.iterrows():
        parent_to_children[row["destination_id"]].append(row["source_id"])

    all_keep: set[str] = set()
    print("\nClinical subset BFS results:")
    for root_id, root_name in CLINICAL_ROOTS.items():
        descendants = _bfs_descendants(root_id, parent_to_children)
        descendants.add(root_id)  # include root itself
        print(f"  {root_name} ({root_id}): {len(descendants):,} concepts")
        all_keep |= descendants

    print(f"  Total unique concepts in clinical subset: {len(all_keep):,}")

    concepts_filtered = concepts_df[concepts_df["concept_id"].isin(all_keep)].copy()
    descriptions_filtered = descriptions_df[descriptions_df["concept_id"].isin(all_keep)].copy()
    relationships_filtered = relationships_df[
        relationships_df["source_id"].isin(all_keep) &
        relationships_df["destination_id"].isin(all_keep)
    ].copy()

    logger.info(
        "Subset: %d concepts, %d descriptions, %d relationships",
        len(concepts_filtered),
        len(descriptions_filtered),
        len(relationships_filtered),
    )
    return concepts_filtered, descriptions_filtered, relationships_filtered


# ---------------------------------------------------------------------------
# 5. build_synonym_lookup
# ---------------------------------------------------------------------------

def build_synonym_lookup(descriptions_df: pd.DataFrame) -> dict[str, list[str]]:
    """
    Build {lowercased_term: [concept_id, ...]} from all active FSNs and synonyms.

    Semantic tags are stripped from FSN terms before indexing.
    """
    lookup: dict[str, list[str]] = defaultdict(list)

    for _, row in descriptions_df.iterrows():
        concept_id = row["concept_id"]
        term = row["term"]
        type_id = row["type_id"]

        # Strip semantic tag for FSNs so "breast cancer" matches "Breast cancer (disorder)"
        if type_id == FSN_TYPE_ID:
            term = _strip_semantic_tag(term)

        key = term.lower().strip()
        if key and concept_id not in lookup[key]:
            lookup[key].append(concept_id)

    logger.info("Built synonym lookup with %d unique terms", len(lookup))
    return dict(lookup)


# ---------------------------------------------------------------------------
# 6. export_for_neo4j
# ---------------------------------------------------------------------------

def export_for_neo4j(
    concepts_df: pd.DataFrame,
    descriptions_df: pd.DataFrame,
    relationships_df: pd.DataFrame,
    output_dir: str | Path | None = None,
) -> None:
    """
    Export three CSVs and a JSON synonym lookup for Neo4j loading.

    Output files (in output_dir, default data/processed/):
      snomed_concepts.csv      — concept_id, preferred_term, semantic_tag
      snomed_synonyms.csv      — concept_id, synonym  (one row per term)
      snomed_relationships.csv — source_id, destination_id, type
      snomed_synonym_lookup.json
    """
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent / "data" / "processed"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- snomed_concepts.csv ---
    # One row per concept: the canonical preferred_term and semantic_tag
    preferred = (
        descriptions_df[["concept_id", "preferred_term", "semantic_tag"]]
        .drop_duplicates(subset="concept_id")
        .dropna(subset=["preferred_term"])
    )
    # Merge with active concept list so we only export known-active concepts
    concepts_out = concepts_df[["concept_id"]].merge(preferred, on="concept_id", how="left")
    concept_path = output_dir / "snomed_concepts.csv"
    concepts_out.to_csv(concept_path, index=False)
    logger.info("Wrote %d rows to %s", len(concepts_out), concept_path)

    # --- snomed_synonyms.csv ---
    # All terms (FSN stripped + synonyms), one per row
    syns_rows: list[dict[str, Any]] = []
    for _, row in descriptions_df.iterrows():
        term = row["term"]
        if row["type_id"] == FSN_TYPE_ID:
            term = _strip_semantic_tag(term)
        syns_rows.append({"concept_id": row["concept_id"], "synonym": term})
    synonyms_df = pd.DataFrame(syns_rows).drop_duplicates()
    syn_path = output_dir / "snomed_synonyms.csv"
    synonyms_df.to_csv(syn_path, index=False)
    logger.info("Wrote %d rows to %s", len(synonyms_df), syn_path)

    # --- snomed_relationships.csv ---
    rels_out = relationships_df[["source_id", "destination_id"]].copy()
    rels_out["relationship_type"] = "IS_A"
    rel_path = output_dir / "snomed_relationships.csv"
    rels_out.to_csv(rel_path, index=False)
    logger.info("Wrote %d rows to %s", len(rels_out), rel_path)

    # --- snomed_synonym_lookup.json ---
    lookup = build_synonym_lookup(descriptions_df)
    lookup_path = output_dir / "snomed_synonym_lookup.json"
    lookup_path.write_text(json.dumps(lookup, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote synonym lookup (%d terms) to %s", len(lookup), lookup_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    project_root = Path(__file__).resolve().parent.parent
    snomed_dir = str(project_root / "data" / "snomed")
    output_dir = project_root / "data" / "processed"

    print("Phase 3: SNOMED CT RF2 parser")
    print(f"Reading from : {snomed_dir}")
    print(f"Writing to   : {output_dir}\n")

    t_total = time.monotonic()

    # Step 1 — Load concepts
    t0 = time.monotonic()
    print("Step 1/6 — Loading concepts …")
    concepts_df = load_snomed_concepts(snomed_dir)
    print(f"  {len(concepts_df):,} active concepts  ({time.monotonic()-t0:.1f}s)\n")

    # Step 2 — Load descriptions
    t0 = time.monotonic()
    print("Step 2/6 — Loading descriptions …")
    descriptions_df = load_snomed_descriptions(snomed_dir)
    print(f"  {len(descriptions_df):,} active descriptions  ({time.monotonic()-t0:.1f}s)\n")

    # Step 3 — Load relationships
    t0 = time.monotonic()
    print("Step 3/6 — Loading IS_A relationships …")
    relationships_df = load_snomed_relationships(snomed_dir)
    print(f"  {len(relationships_df):,} IS_A relationships  ({time.monotonic()-t0:.1f}s)\n")

    # Step 4 — Filter to clinical subset
    t0 = time.monotonic()
    print("Step 4/6 — Filtering to clinical subset …")
    concepts_df, descriptions_df, relationships_df = filter_to_clinical_subset(
        concepts_df, descriptions_df, relationships_df
    )
    print(f"  Filter complete  ({time.monotonic()-t0:.1f}s)\n")

    # Step 5 — Build synonym lookup (informational; export_for_neo4j also calls this)
    t0 = time.monotonic()
    print("Step 5/6 — Building synonym lookup …")
    lookup = build_synonym_lookup(descriptions_df)
    print(f"  {len(lookup):,} unique terms indexed  ({time.monotonic()-t0:.1f}s)\n")

    # Step 6 — Export
    t0 = time.monotonic()
    print("Step 6/6 — Exporting CSVs and JSON …")
    export_for_neo4j(concepts_df, descriptions_df, relationships_df, output_dir)
    print(f"  Export complete  ({time.monotonic()-t0:.1f}s)\n")

    elapsed = time.monotonic() - t_total
    print(f"SNOMED CT parsing complete in {elapsed:.1f}s total.")
