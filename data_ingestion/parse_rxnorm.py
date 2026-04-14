"""
Phase 3 (Prompt 4): RxNorm RRF parser.

Reads RXNCONSO.RRF and RXNREL.RRF from data/rxnorm/, filters to the drug
concepts relevant for clinical-trial matching (ingredients, brand names,
clinical drug forms), builds synonym and ingredient-mapping lookups, and
exports CSVs + JSON for Neo4j loading.

Usage:
    python -m data_ingestion.parse_rxnorm
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RRF column schemas
# ---------------------------------------------------------------------------

# RXNCONSO.RRF — pipe-delimited, no header, trailing pipe → 19 cols total
RXNCONSO_COLS = [
    "RXCUI", "LAT", "TS", "LUI", "STT", "SUI", "ISPREF",
    "RXAUI", "SAUI", "SCUI", "SDUI", "SAB", "TTY", "CODE",
    "STR", "SRL", "SUPPRESS", "CVF", "_trailing",
]

# RXNREL.RRF — pipe-delimited, no header, trailing pipe → 17 cols total
RXNREL_COLS = [
    "RXCUI1", "RXAUI1", "STYPE1", "REL",
    "RXCUI2", "RXAUI2", "STYPE2", "RELA",
    "RUI", "SRUI", "SAB", "SL", "RG", "DIR",
    "SUPPRESS", "CVF", "_trailing",
]

# Term types to keep
KEEP_TTY = {"IN", "BN", "PIN", "MIN", "SCD", "SBD"}

# Relationship types to keep
KEEP_RELA = {"has_ingredient", "tradename_of", "consists_of", "form_of"}

CHUNK_SIZE = 300_000


# ---------------------------------------------------------------------------
# 1. load_rxnorm_concepts
# ---------------------------------------------------------------------------

def load_rxnorm_concepts(rxnorm_dir: str) -> pd.DataFrame:
    """
    Load drug concepts from RXNCONSO.RRF.

    Filters to SAB=RXNORM, LAT=ENG, SUPPRESS≠O, and term types
    IN / BN / PIN / MIN / SCD / SBD.

    Returns DataFrame: rxcui, name, tty
    """
    path = Path(rxnorm_dir) / "RXNCONSO.RRF"
    if not path.exists():
        raise FileNotFoundError(f"RXNCONSO.RRF not found in {rxnorm_dir}")

    logger.info("Reading %s", path)
    chunks = []
    for chunk in pd.read_csv(
        path,
        sep="|",
        header=None,
        names=RXNCONSO_COLS,
        dtype=str,
        chunksize=CHUNK_SIZE,
        low_memory=False,
    ):
        filtered = chunk[
            (chunk["SAB"] == "RXNORM") &
            (chunk["LAT"] == "ENG") &
            (chunk["SUPPRESS"] != "O") &
            (chunk["TTY"].isin(KEEP_TTY))
        ][["RXCUI", "STR", "TTY"]]
        chunks.append(filtered)

    df = pd.concat(chunks, ignore_index=True)
    df = df.rename(columns={"RXCUI": "rxcui", "STR": "name", "TTY": "tty"})
    logger.info("Loaded %d RxNorm drug concepts", len(df))
    return df


# ---------------------------------------------------------------------------
# 2. load_rxnorm_relationships
# ---------------------------------------------------------------------------

def load_rxnorm_relationships(rxnorm_dir: str) -> pd.DataFrame:
    """
    Load drug relationships from RXNREL.RRF.

    Filters to RELA in: has_ingredient, tradename_of, consists_of, form_of.

    Returns DataFrame: rxcui1, rxcui2, relationship_type
    """
    path = Path(rxnorm_dir) / "RXNREL.RRF"
    if not path.exists():
        raise FileNotFoundError(f"RXNREL.RRF not found in {rxnorm_dir}")

    logger.info("Reading %s", path)
    chunks = []
    for chunk in pd.read_csv(
        path,
        sep="|",
        header=None,
        names=RXNREL_COLS,
        dtype=str,
        chunksize=CHUNK_SIZE,
        low_memory=False,
    ):
        filtered = chunk[
            chunk["RELA"].isin(KEEP_RELA) &
            chunk["RXCUI1"].notna() &
            chunk["RXCUI2"].notna()
        ][["RXCUI1", "RXCUI2", "RELA"]]
        chunks.append(filtered)

    df = pd.concat(chunks, ignore_index=True)
    df = df.rename(columns={
        "RXCUI1": "rxcui1",
        "RXCUI2": "rxcui2",
        "RELA": "relationship_type",
    })
    # Drop rows where either CUI is blank/NaN after string read
    df = df[df["rxcui1"].str.strip().ne("") & df["rxcui2"].str.strip().ne("")]
    logger.info("Loaded %d RxNorm relationships", len(df))
    return df


# ---------------------------------------------------------------------------
# 3. build_drug_synonym_lookup
# ---------------------------------------------------------------------------

def build_drug_synonym_lookup(concepts_df: pd.DataFrame) -> dict[str, list[str]]:
    """
    Build {lowercased_name: [rxcui, ...]} across all drug name variants.

    Covers brand names (BN), ingredients (IN), precise ingredients (PIN),
    semantic clinical drugs (SCD), semantic branded drugs (SBD), and
    multiple ingredients (MIN).
    """
    lookup: dict[str, list[str]] = defaultdict(list)
    for _, row in concepts_df.iterrows():
        key = str(row["name"]).lower().strip()
        rxcui = str(row["rxcui"]).strip()
        if key and rxcui and rxcui not in lookup[key]:
            lookup[key].append(rxcui)
    logger.info("Built drug synonym lookup with %d unique terms", len(lookup))
    return dict(lookup)


# ---------------------------------------------------------------------------
# 4. build_ingredient_mappings
# ---------------------------------------------------------------------------

def build_ingredient_mappings(
    concepts_df: pd.DataFrame,
    relationships_df: pd.DataFrame,
) -> dict[str, list[str]]:
    """
    For each non-ingredient concept, trace to its ingredient(s) via relationships.

    Uses has_ingredient, tradename_of, consists_of, and form_of edges.
    Returns {rxcui: [ingredient_rxcui, ...]}.

    Only ingredient concepts (TTY=IN or PIN) appear as values.
    """
    # Set of known ingredient CUIs
    ingredient_cuis = set(
        concepts_df[concepts_df["tty"].isin({"IN", "PIN"})]["rxcui"]
    )

    # Build adjacency for relevant relationship types (source → targets)
    adjacency: dict[str, list[str]] = defaultdict(list)
    for _, row in relationships_df.iterrows():
        adjacency[row["rxcui1"]].append(row["rxcui2"])

    mappings: dict[str, list[str]] = {}
    all_cuis = set(concepts_df["rxcui"])

    for cui in all_cuis:
        # BFS to find all reachable ingredient CUIs
        visited: set[str] = set()
        queue = [cui]
        found_ingredients: list[str] = []
        while queue:
            node = queue.pop()
            if node in visited:
                continue
            visited.add(node)
            if node in ingredient_cuis and node != cui:
                found_ingredients.append(node)
            else:
                queue.extend(adjacency.get(node, []))
        if found_ingredients:
            mappings[cui] = found_ingredients

    logger.info(
        "Built ingredient mappings for %d concepts (%d are ingredients)",
        len(mappings),
        len(ingredient_cuis),
    )
    return mappings


# ---------------------------------------------------------------------------
# 5. export_for_neo4j
# ---------------------------------------------------------------------------

def export_for_neo4j(
    concepts_df: pd.DataFrame,
    relationships_df: pd.DataFrame,
    output_dir: str | Path | None = None,
) -> None:
    """
    Export RxNorm data for Neo4j loading.

    Writes to output_dir (default data/processed/):
      rxnorm_concepts.csv        — rxcui, name, tty
      rxnorm_relationships.csv   — source_rxcui, target_rxcui, relationship_type
      rxnorm_synonym_lookup.json — {lowercased_name: [rxcui, ...]}
    """
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent / "data" / "processed"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- rxnorm_concepts.csv ---
    concept_path = output_dir / "rxnorm_concepts.csv"
    concepts_df.to_csv(concept_path, index=False)
    logger.info("Wrote %d rows to %s", len(concepts_df), concept_path)

    # --- rxnorm_relationships.csv ---
    rels_out = relationships_df.rename(columns={
        "rxcui1": "source_rxcui",
        "rxcui2": "target_rxcui",
    })
    rel_path = output_dir / "rxnorm_relationships.csv"
    rels_out.to_csv(rel_path, index=False)
    logger.info("Wrote %d rows to %s", len(rels_out), rel_path)

    # --- rxnorm_synonym_lookup.json ---
    lookup = build_drug_synonym_lookup(concepts_df)
    lookup_path = output_dir / "rxnorm_synonym_lookup.json"
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
    rxnorm_dir = str(project_root / "data" / "rxnorm")
    output_dir = project_root / "data" / "processed"

    print("Phase 3 (Prompt 4): RxNorm RRF parser")
    print(f"Reading from : {rxnorm_dir}")
    print(f"Writing to   : {output_dir}\n")

    t_total = time.monotonic()

    # Step 1 — Load concepts
    t0 = time.monotonic()
    print("Step 1/4 — Loading concepts …")
    concepts_df = load_rxnorm_concepts(rxnorm_dir)
    print(f"  {len(concepts_df):,} drug concepts  ({time.monotonic()-t0:.1f}s)")
    tty_counts = concepts_df["tty"].value_counts().to_dict()
    for tty, count in sorted(tty_counts.items(), key=lambda x: -x[1]):
        print(f"    {tty}: {count:,}")
    print()

    # Step 2 — Load relationships
    t0 = time.monotonic()
    print("Step 2/4 — Loading relationships …")
    relationships_df = load_rxnorm_relationships(rxnorm_dir)
    print(f"  {len(relationships_df):,} relationships  ({time.monotonic()-t0:.1f}s)")
    rel_counts = relationships_df["relationship_type"].value_counts().to_dict()
    for rel, count in sorted(rel_counts.items(), key=lambda x: -x[1]):
        print(f"    {rel}: {count:,}")
    print()

    # Step 3 — Build ingredient mappings
    t0 = time.monotonic()
    print("Step 3/4 — Building ingredient mappings …")
    ingredient_mappings = build_ingredient_mappings(concepts_df, relationships_df)
    print(f"  {len(ingredient_mappings):,} concepts mapped to ingredients  ({time.monotonic()-t0:.1f}s)\n")

    # Step 4 — Export
    t0 = time.monotonic()
    print("Step 4/4 — Exporting CSVs and JSON …")
    export_for_neo4j(concepts_df, relationships_df, output_dir)
    print(f"  Export complete  ({time.monotonic()-t0:.1f}s)\n")

    elapsed = time.monotonic() - t_total
    print(f"RxNorm parsing complete in {elapsed:.1f}s total.")
