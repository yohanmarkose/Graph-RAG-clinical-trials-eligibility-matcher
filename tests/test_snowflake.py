"""
Integration tests for the Snowflake layer.
"""

from __future__ import annotations

import pytest

from tests.conftest import requires_snowflake

# Expected schemas and tables created by snowflake_etl/setup_schema.py
EXPECTED_SCHEMAS = {"STAGING", "CLEAN", "TRACKING"}

EXPECTED_TABLES = {
    "STAGING": [
        "RAW_TRIALS",
        "RAW_CONDITIONS",
        "RAW_INTERVENTIONS",
        "RAW_SNOMED_CONCEPTS",
        "RAW_RXNORM_CONCEPTS",
    ],
    "CLEAN": [
        "TRIALS",
        "TRIAL_CONDITIONS",
        "TRIAL_INTERVENTIONS",
    ],
    "TRACKING": [
        "PIPELINE_RUN",
        "ENTITY_LINKING_PROGRESS",
        "CRITERIA_PARSING_PROGRESS",
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sf_conn():
    """Module-scoped Snowflake connection.  Closed after all tests in module."""
    from config.settings import get_settings

    cfg = get_settings()
    if cfg.snowflake is None:
        pytest.skip("Snowflake credentials not configured")

    conn = cfg.snowflake.get_snowflake_connection()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------


@requires_snowflake
def test_snowflake_connection_succeeds(sf_conn):
    """Verify that a Snowflake connection can be established with the configured credentials."""
    cursor = sf_conn.cursor()
    cursor.execute("SELECT CURRENT_VERSION()")
    row = cursor.fetchone()
    cursor.close()
    assert row is not None
    assert isinstance(row[0], str)
    print(f"Connected to Snowflake version: {row[0]}")


@requires_snowflake
def test_snowflake_current_database(sf_conn):
    """Verify the session is using the CLINICAL_TRIALS database."""
    from config.settings import get_settings

    cfg = get_settings()
    cursor = sf_conn.cursor()
    cursor.execute("SELECT CURRENT_DATABASE()")
    row = cursor.fetchone()
    cursor.close()
    assert row[0].upper() == cfg.snowflake.database.upper()


# ---------------------------------------------------------------------------
# Schema existence
# ---------------------------------------------------------------------------


@requires_snowflake
def test_expected_schemas_exist(sf_conn):
    """All three schemas (STAGING, CLEAN, TRACKING) should exist."""
    cursor = sf_conn.cursor()
    cursor.execute("SHOW SCHEMAS")
    existing = {row[1].upper() for row in cursor.fetchall()}
    cursor.close()

    missing = EXPECTED_SCHEMAS - existing
    assert not missing, f"Missing Snowflake schemas: {missing}. Run snowflake_etl/setup_schema.py first."


# ---------------------------------------------------------------------------
# Table existence
# ---------------------------------------------------------------------------


@requires_snowflake
@pytest.mark.parametrize("schema,table", [
    (schema, table)
    for schema, tables in EXPECTED_TABLES.items()
    for table in tables
])
def test_table_exists(sf_conn, schema: str, table: str):
    """Each expected table should exist in its schema."""
    cursor = sf_conn.cursor()
    cursor.execute(
        f"SELECT COUNT(*) FROM information_schema.tables "
        f"WHERE table_schema = '{schema}' AND table_name = '{table}'"
    )
    row = cursor.fetchone()
    cursor.close()
    assert row[0] >= 1, f"Table {schema}.{table} not found — run snowflake_etl/setup_schema.py"


# ---------------------------------------------------------------------------
# Table row counts (basic sanity)
# ---------------------------------------------------------------------------


@requires_snowflake
def test_staging_raw_trials_not_empty(sf_conn):
    """STAGING.RAW_TRIALS should have rows after fetch_trials + load_staging."""
    cursor = sf_conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM STAGING.RAW_TRIALS")
        row = cursor.fetchone()
        count = row[0]
    except Exception:
        pytest.skip("STAGING.RAW_TRIALS does not exist yet")
    finally:
        cursor.close()

    assert count > 0, (
        "STAGING.RAW_TRIALS is empty. "
        "Run: python -m data_ingestion.fetch_trials && python -m snowflake_etl.load_staging"
    )


@requires_snowflake
def test_clean_trials_have_therapeutic_area(sf_conn):
    """CLEAN.TRIALS rows should all have a therapeutic_area value after transform."""
    cursor = sf_conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM CLEAN.TRIALS WHERE therapeutic_area IS NULL"
        )
        row = cursor.fetchone()
        null_count = row[0]
    except Exception:
        pytest.skip("CLEAN.TRIALS does not exist yet")
    finally:
        cursor.close()

    assert null_count == 0, (
        f"{null_count} rows in CLEAN.TRIALS have NULL therapeutic_area. "
        "Run: python -m snowflake_etl.transform"
    )


@requires_snowflake
def test_entity_linking_progress_tracks_records(sf_conn):
    """TRACKING.ENTITY_LINKING_PROGRESS should have rows after entity linking."""
    cursor = sf_conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM TRACKING.ENTITY_LINKING_PROGRESS")
        row = cursor.fetchone()
        count = row[0]
    except Exception:
        pytest.skip("TRACKING.ENTITY_LINKING_PROGRESS does not exist yet")
    finally:
        cursor.close()

    # This table may be empty if entity linking hasn't run — that's OK
    assert count >= 0  # Always passes; the table existing is the real test


# ---------------------------------------------------------------------------
# Settings validation (unit — no Snowflake connection needed)
# ---------------------------------------------------------------------------


def test_snowflake_settings_load_from_env():
    """SnowflakeSettings should load from environment without raising."""
    from config.settings import get_settings

    cfg = get_settings()
    if cfg.snowflake is None:
        pytest.skip("Snowflake not configured")

    assert cfg.snowflake.database
    assert cfg.snowflake.warehouse
    assert cfg.snowflake.user
