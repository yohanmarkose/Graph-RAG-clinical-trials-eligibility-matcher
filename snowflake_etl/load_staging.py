"""
Load processed data into Snowflake STAGING tables.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd
from snowflake.connector.pandas_tools import write_pandas

from config.settings import get_settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"

BATCH_SIZE = 5_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_conn():
    cfg = get_settings()
    if cfg.snowflake is None:
        raise RuntimeError("Snowflake credentials not configured in .env")
    return cfg.snowflake.get_snowflake_connection()


def _write_batched(
    conn,
    df: pd.DataFrame,
    table: str,          # e.g. "STAGING.RAW_SNOMED_CONCEPTS"
    *,
    overwrite: bool = False,
) -> int:
    """
    Write a DataFrame to a Snowflake table using write_pandas in batches.

    Returns total rows written.
    """
    schema, tbl = table.split(".")
    total = 0
    for i in range(0, len(df), BATCH_SIZE):
        chunk = df.iloc[i : i + BATCH_SIZE]
        success, nchunks, nrows, _ = write_pandas(
            conn,
            chunk,
            tbl,
            schema=schema,
            database="CLINICAL_TRIALS",
            overwrite=(overwrite and i == 0),
            auto_create_table=False,
            quote_identifiers=False,
        )
        total += nrows
    return total


def _merge_trials(conn, df: pd.DataFrame) -> int:
    """
    Upsert trial rows into STAGING.RAW_TRIALS via a temp table + MERGE.

    VARIANT columns (conditions, interventions, locations, raw_json) are
    passed as JSON strings; PARSE_JSON() converts them inside the MERGE.
    """
    cursor = conn.cursor()

    # ---- write to temp table (overwrite each call) ----
    # Column names must be uppercase for Snowflake
    df_upload = df.copy()
    df_upload.columns = [c.upper() for c in df_upload.columns]

    schema = "STAGING"
    temp_tbl = "TEMP_RAW_TRIALS"

    write_pandas(
        conn,
        df_upload,
        temp_tbl,
        schema=schema,
        database="CLINICAL_TRIALS",
        overwrite=True,
        auto_create_table=True,
        quote_identifiers=False,
    )

    # ---- MERGE from temp into target ----
    merge_sql = """
    MERGE INTO STAGING.RAW_TRIALS t
    USING STAGING.TEMP_RAW_TRIALS s
    ON t.NCT_ID = s.NCT_ID
    WHEN MATCHED THEN UPDATE SET
        t.TITLE                   = s.TITLE,
        t.OFFICIAL_TITLE          = s.OFFICIAL_TITLE,
        t.BRIEF_SUMMARY           = s.BRIEF_SUMMARY,
        t.STATUS                  = s.STATUS,
        t.PHASE                   = s.PHASE,
        t.ENROLLMENT              = s.ENROLLMENT::INTEGER,
        t.START_DATE              = s.START_DATE,
        t.PRIMARY_COMPLETION_DATE = s.PRIMARY_COMPLETION_DATE,
        t.SPONSOR                 = s.SPONSOR,
        t.CONDITIONS              = TRY_PARSE_JSON(s.CONDITIONS),
        t.INTERVENTIONS           = TRY_PARSE_JSON(s.INTERVENTIONS),
        t.ELIGIBILITY_CRITERIA    = s.ELIGIBILITY_CRITERIA,
        t.GENDER                  = s.GENDER,
        t.MIN_AGE                 = s.MIN_AGE::INTEGER,
        t.MAX_AGE                 = s.MAX_AGE::INTEGER,
        t.LOCATIONS               = TRY_PARSE_JSON(s.LOCATIONS),
        t.THERAPEUTIC_AREA        = s.THERAPEUTIC_AREA
    WHEN NOT MATCHED THEN INSERT (
        NCT_ID, TITLE, OFFICIAL_TITLE, BRIEF_SUMMARY, STATUS, PHASE,
        ENROLLMENT, START_DATE, PRIMARY_COMPLETION_DATE, SPONSOR,
        CONDITIONS, INTERVENTIONS, ELIGIBILITY_CRITERIA,
        GENDER, MIN_AGE, MAX_AGE, LOCATIONS, THERAPEUTIC_AREA
    ) VALUES (
        s.NCT_ID, s.TITLE, s.OFFICIAL_TITLE, s.BRIEF_SUMMARY, s.STATUS, s.PHASE,
        s.ENROLLMENT::INTEGER, s.START_DATE, s.PRIMARY_COMPLETION_DATE, s.SPONSOR,
        TRY_PARSE_JSON(s.CONDITIONS), TRY_PARSE_JSON(s.INTERVENTIONS),
        s.ELIGIBILITY_CRITERIA,
        s.GENDER, s.MIN_AGE::INTEGER, s.MAX_AGE::INTEGER,
        TRY_PARSE_JSON(s.LOCATIONS), s.THERAPEUTIC_AREA
    )
    """
    cursor.execute(merge_sql)
    rows_affected = cursor.rowcount
    cursor.execute("DROP TABLE IF EXISTS STAGING.TEMP_RAW_TRIALS")
    cursor.close()
    return rows_affected


# ---------------------------------------------------------------------------
# 1. load_trials_to_snowflake
# ---------------------------------------------------------------------------

def load_trials_to_snowflake(
    processed_json_path: str | Path | None = None,
) -> int:
    """
    Load trials_processed.json into STAGING.RAW_TRIALS using MERGE on nct_id.

    VARIANT columns (conditions, interventions, locations) are serialised to
    JSON strings before upload; TRY_PARSE_JSON() converts them in Snowflake.

    Returns the number of rows upserted.
    """
    if processed_json_path is None:
        processed_json_path = DATA_PROCESSED / "trials_processed.json"
    processed_json_path = Path(processed_json_path)

    if not processed_json_path.exists():
        raise FileNotFoundError(
            f"trials_processed.json not found at {processed_json_path}.\n"
            "Run `python -m data_ingestion.fetch_trials` first to generate it."
        )

    logger.info("Reading %s …", processed_json_path)
    trials = json.loads(processed_json_path.read_text(encoding="utf-8"))
    logger.info("Loaded %d trials from JSON", len(trials))

    # Serialise list/dict columns to JSON strings for VARIANT upload
    rows = []
    for t in trials:
        rows.append({
            "nct_id":                   t.get("nct_id"),
            "title":                    t.get("title"),
            "official_title":           t.get("official_title"),
            "brief_summary":            t.get("brief_summary"),
            "status":                   t.get("status"),
            "phase":                    t.get("phase"),
            "enrollment":               t.get("enrollment"),
            "start_date":               t.get("start_date"),
            "primary_completion_date":  t.get("primary_completion_date"),
            "sponsor":                  t.get("sponsor"),
            "conditions":               json.dumps(t.get("conditions") or []),
            "interventions":            json.dumps(t.get("interventions") or []),
            "eligibility_criteria":     t.get("eligibility_criteria"),
            "gender":                   t.get("gender"),
            "min_age":                  t.get("min_age"),
            "max_age":                  t.get("max_age"),
            "locations":                json.dumps(t.get("locations") or []),
            "therapeutic_area":         t.get("therapeutic_area"),
        })

    df = pd.DataFrame(rows)

    t0 = time.monotonic()
    conn = _get_conn()
    try:
        total = 0
        for i in range(0, len(df), BATCH_SIZE):
            batch = df.iloc[i : i + BATCH_SIZE]
            merged = _merge_trials(conn, batch)
            total += merged
            logger.info(
                "Batch %d/%d — upserted %d rows (running total: %d)",
                i // BATCH_SIZE + 1,
                -(-len(df) // BATCH_SIZE),
                merged,
                total,
            )
    finally:
        conn.close()

    elapsed = time.monotonic() - t0
    print(f"  Trials loaded: {total:,} rows in {elapsed:.1f}s")
    return total


# ---------------------------------------------------------------------------
# 2. load_snomed_to_snowflake
# ---------------------------------------------------------------------------

def load_snomed_to_snowflake() -> None:
    """Load SNOMED CSVs into STAGING.RAW_SNOMED_* tables."""
    files = {
        "STAGING.RAW_SNOMED_CONCEPTS":      DATA_PROCESSED / "snomed_concepts.csv",
        "STAGING.RAW_SNOMED_SYNONYMS":      DATA_PROCESSED / "snomed_synonyms.csv",
        "STAGING.RAW_SNOMED_RELATIONSHIPS": DATA_PROCESSED / "snomed_relationships.csv",
    }

    conn = _get_conn()
    try:
        for table, path in files.items():
            if not path.exists():
                print(f"  SKIP {table} — {path.name} not found")
                continue
            t0 = time.monotonic()
            df = pd.read_csv(path, dtype=str).fillna("")
            # Uppercase columns for Snowflake
            df.columns = [c.upper() for c in df.columns]
            # Rename reserved keyword column; truncate long drug names
            if "TYPE" in df.columns:
                df = df.rename(columns={"TYPE": "RELATIONSHIP_TYPE"})
            if "NAME" in df.columns:
                df["NAME"] = df["NAME"].str[:1000]
            rows = _write_batched(conn, df, table, overwrite=True)
            print(f"  {table}: {rows:,} rows  ({time.monotonic()-t0:.1f}s)")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. load_rxnorm_to_snowflake
# ---------------------------------------------------------------------------

def load_rxnorm_to_snowflake() -> None:
    """Load RxNorm CSVs into STAGING.RAW_RXNORM_* tables."""
    files = {
        "STAGING.RAW_RXNORM_CONCEPTS":      DATA_PROCESSED / "rxnorm_concepts.csv",
        "STAGING.RAW_RXNORM_RELATIONSHIPS": DATA_PROCESSED / "rxnorm_relationships.csv",
    }

    conn = _get_conn()
    try:
        for table, path in files.items():
            if not path.exists():
                print(f"  SKIP {table} — {path.name} not found")
                continue
            t0 = time.monotonic()
            df = pd.read_csv(path, dtype=str).fillna("")
            df.columns = [c.upper() for c in df.columns]
            if "NAME" in df.columns:
                df["NAME"] = df["NAME"].str[:1000]
            rows = _write_batched(conn, df, table, overwrite=True)
            print(f"  {table}: {rows:,} rows  ({time.monotonic()-t0:.1f}s)")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("Phase 4: Loading staging data into Snowflake\n")

    print("Step 1/3 — Trials …")
    try:
        load_trials_to_snowflake()
    except FileNotFoundError as e:
        print(f"  SKIP: {e}")

    print("\nStep 2/3 — SNOMED CT …")
    load_snomed_to_snowflake()

    print("\nStep 3/3 — RxNorm …")
    load_rxnorm_to_snowflake()

    print("\nStaging load complete.")
