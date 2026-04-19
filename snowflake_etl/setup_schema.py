"""
Snowflake schema setup.

Creates the CLINICAL_TRIALS database with STAGING, CLEAN, and TRACKING schemas
and all tables idempotently
"""

from __future__ import annotations

import logging
import sys

from config.settings import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL statements — executed in order
# ---------------------------------------------------------------------------

_DDL: list[tuple[str, str]] = [
    # ---- database / schemas ----
    (
        "CREATE DATABASE IF NOT EXISTS CLINICAL_TRIALS",
        "database CLINICAL_TRIALS",
    ),
    (
        "USE DATABASE CLINICAL_TRIALS",
        None,                       # silent — just a context switch
    ),
    (
        "CREATE SCHEMA IF NOT EXISTS STAGING",
        "schema STAGING",
    ),
    (
        "CREATE SCHEMA IF NOT EXISTS CLEAN",
        "schema CLEAN",
    ),
    (
        "CREATE SCHEMA IF NOT EXISTS TRACKING",
        "schema TRACKING",
    ),

    # ---- STAGING tables ----
    (
        """
        CREATE TABLE IF NOT EXISTS STAGING.RAW_TRIALS (
            nct_id                  VARCHAR(20)   PRIMARY KEY,
            title                   VARCHAR(2000),
            official_title          VARCHAR(4000),
            brief_summary           TEXT,
            status                  VARCHAR(100),
            phase                   VARCHAR(100),
            enrollment              INTEGER,
            start_date              VARCHAR(50),
            primary_completion_date VARCHAR(50),
            sponsor                 VARCHAR(500),
            conditions              VARIANT,
            interventions           VARIANT,
            eligibility_criteria    TEXT,
            gender                  VARCHAR(20),
            min_age                 INTEGER,
            max_age                 INTEGER,
            locations               VARIANT,
            therapeutic_area        VARCHAR(100),
            raw_json                VARIANT,
            ingested_at             TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
        )
        """,
        "table STAGING.RAW_TRIALS",
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS STAGING.RAW_SNOMED_CONCEPTS (
            concept_id     VARCHAR(20) PRIMARY KEY,
            preferred_term VARCHAR(1000),
            semantic_tag   VARCHAR(100)
        )
        """,
        "table STAGING.RAW_SNOMED_CONCEPTS",
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS STAGING.RAW_SNOMED_SYNONYMS (
            concept_id VARCHAR(20),
            synonym    VARCHAR(1000)
        )
        """,
        "table STAGING.RAW_SNOMED_SYNONYMS",
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS STAGING.RAW_SNOMED_RELATIONSHIPS (
            source_id         VARCHAR(20),
            destination_id    VARCHAR(20),
            relationship_type VARCHAR(50)
        )
        """,
        "table STAGING.RAW_SNOMED_RELATIONSHIPS",
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS STAGING.RAW_RXNORM_CONCEPTS (
            rxcui VARCHAR(20) PRIMARY KEY,
            name  VARCHAR(1000),
            tty   VARCHAR(20)
        )
        """,
        "table STAGING.RAW_RXNORM_CONCEPTS",
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS STAGING.RAW_RXNORM_RELATIONSHIPS (
            source_rxcui      VARCHAR(20),
            target_rxcui      VARCHAR(20),
            relationship_type VARCHAR(100)
        )
        """,
        "table STAGING.RAW_RXNORM_RELATIONSHIPS",
    ),

    # ---- CLEAN tables ----
    (
        """
        CREATE TABLE IF NOT EXISTS CLEAN.TRIALS (
            nct_id                  VARCHAR(20) PRIMARY KEY,
            title                   VARCHAR(2000),
            official_title          VARCHAR(4000),
            brief_summary           TEXT,
            status                  VARCHAR(100),
            phase                   VARCHAR(100),
            enrollment              INTEGER,
            start_date              DATE,
            primary_completion_date DATE,
            sponsor                 VARCHAR(500),
            gender                  VARCHAR(20),
            min_age                 INTEGER,
            max_age                 INTEGER,
            therapeutic_area        VARCHAR(100),
            eligibility_criteria    TEXT,
            conditions_count        INTEGER,
            interventions_count     INTEGER,
            criteria_char_length    INTEGER,
            has_eligibility_text    BOOLEAN,
            url                     VARCHAR(500),
            cleaned_at              TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
        )
        """,
        "table CLEAN.TRIALS",
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS CLEAN.TRIAL_CONDITIONS (
            nct_id                VARCHAR(20),
            condition_name        VARCHAR(1000),
            condition_normalized  VARCHAR(1000),
            snomed_concept_id     VARCHAR(20),
            link_confidence       FLOAT,
            link_method           VARCHAR(50)
        )
        """,
        "table CLEAN.TRIAL_CONDITIONS",
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS CLEAN.TRIAL_INTERVENTIONS (
            nct_id                    VARCHAR(20),
            intervention_name         VARCHAR(1000),
            intervention_type         VARCHAR(100),
            intervention_normalized   VARCHAR(1000),
            rxnorm_rxcui              VARCHAR(20),
            link_confidence           FLOAT,
            link_method               VARCHAR(50)
        )
        """,
        "table CLEAN.TRIAL_INTERVENTIONS",
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS CLEAN.ELIGIBILITY_CRITERIA (
            criterion_id       VARCHAR(50) PRIMARY KEY,
            nct_id             VARCHAR(20),
            criterion_type     VARCHAR(20),
            raw_text           TEXT,
            sentence_index     INTEGER,
            therapeutic_area   VARCHAR(100)
        )
        """,
        "table CLEAN.ELIGIBILITY_CRITERIA",
    ),

    # ---- TRACKING tables ----
    (
        """
        CREATE TABLE IF NOT EXISTS TRACKING.PARSING_PROGRESS (
            criterion_id    VARCHAR(50) PRIMARY KEY,
            nct_id          VARCHAR(20),
            parsing_status  VARCHAR(20) DEFAULT 'pending',
            parsed_json     VARIANT,
            parsed_at       TIMESTAMP_NTZ,
            llm_model       VARCHAR(100),
            llm_tokens_used INTEGER,
            error_message   TEXT,
            attempt_count   INTEGER DEFAULT 0
        )
        """,
        "table TRACKING.PARSING_PROGRESS",
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS TRACKING.ENTITY_LINKING_PROGRESS (
            entity_text  VARCHAR(1000),
            entity_type  VARCHAR(20),
            linked_id    VARCHAR(20),
            linked_term  VARCHAR(1000),
            confidence   FLOAT,
            method       VARCHAR(50),
            linked_at    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
        )
        """,
        "table TRACKING.ENTITY_LINKING_PROGRESS",
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS TRACKING.PIPELINE_RUNS (
            run_id             VARCHAR(50) PRIMARY KEY,
            run_type           VARCHAR(50),
            therapeutic_area   VARCHAR(100),
            status             VARCHAR(20),
            started_at         TIMESTAMP_NTZ,
            completed_at       TIMESTAMP_NTZ,
            records_processed  INTEGER,
            records_succeeded  INTEGER,
            records_failed     INTEGER,
            error_message      TEXT
        )
        """,
        "table TRACKING.PIPELINE_RUNS",
    ),
]


# ---------------------------------------------------------------------------
# Main setup function
# ---------------------------------------------------------------------------

def setup_schema() -> None:
    """Connect to Snowflake and create all schema objects idempotently."""
    cfg = get_settings()
    if cfg.snowflake is None:
        print("ERROR: Snowflake credentials not configured in .env — aborting.")
        sys.exit(1)

    try:
        conn = cfg.snowflake.get_snowflake_connection()
    except Exception as exc:
        print(f"ERROR: Could not connect to Snowflake: {exc}")
        sys.exit(1)

    cursor = conn.cursor()
    created: list[str] = []

    try:
        for sql, label in _DDL:
            try:
                cursor.execute(sql.strip())
                if label:
                    print(f"  OK  {label}")
                    created.append(label)
            except Exception as exc:
                print(f"  FAIL {label or '(context)'}: {exc}")
                raise
    finally:
        cursor.close()
        conn.close()

    print(f"\nSchema setup complete — {len(created)} objects created/verified.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print("Phase 4: Snowflake schema setup\n")
    setup_schema()
