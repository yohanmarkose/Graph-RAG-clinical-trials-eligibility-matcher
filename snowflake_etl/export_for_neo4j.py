"""
Export filtered data from Snowflake CLEAN/TRACKING tables
to CSVs ready for Neo4j bulk import.

"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import pandas as pd

from config.settings import get_settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "processed"


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _get_conn():
    cfg = get_settings()
    if cfg.snowflake is None:
        raise RuntimeError("Snowflake credentials not configured in .env")
    return cfg.snowflake.get_snowflake_connection()


# ---------------------------------------------------------------------------
# 1. export_trials
# ---------------------------------------------------------------------------

def export_trials(
    conn,
    therapeutic_area: str | None,
    output_dir: str | Path,
) -> int:
    """
    Export CLEAN.TRIALS, optionally filtered by therapeutic_area.
    Saves to neo4j_trials.csv.  Returns count of exported rows.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sql = """
        SELECT *
        FROM CLEAN.TRIALS
        WHERE (%s IS NULL OR therapeutic_area = %s)
    """
    cur = conn.cursor()
    cur.execute(sql, (therapeutic_area, therapeutic_area))
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    cur.close()

    df = pd.DataFrame(rows, columns=columns)
    out_path = output_dir / "neo4j_trials.csv"
    df.to_csv(out_path, index=False)
    logger.info("Exported %d trials to %s", len(df), out_path)
    return len(df)


# ---------------------------------------------------------------------------
# 2. export_conditions
# ---------------------------------------------------------------------------

def export_conditions(
    conn,
    therapeutic_area: str | None,
    output_dir: str | Path,
) -> int:
    """
    Export CLEAN.TRIAL_CONDITIONS for trials matching the therapeutic_area.
    Saves to neo4j_trial_conditions.csv.  Returns row count.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sql = """
        SELECT DISTINCT tc.*
        FROM CLEAN.TRIAL_CONDITIONS tc
        JOIN CLEAN.TRIALS t ON tc.nct_id = t.nct_id
        WHERE (%s IS NULL OR t.therapeutic_area = %s)
    """
    cur = conn.cursor()
    cur.execute(sql, (therapeutic_area, therapeutic_area))
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    cur.close()

    df = pd.DataFrame(rows, columns=columns)
    out_path = output_dir / "neo4j_trial_conditions.csv"
    df.to_csv(out_path, index=False)
    logger.info("Exported %d conditions to %s", len(df), out_path)
    return len(df)


# ---------------------------------------------------------------------------
# 3. export_interventions
# ---------------------------------------------------------------------------

def export_interventions(
    conn,
    therapeutic_area: str | None,
    output_dir: str | Path,
) -> int:
    """
    Export CLEAN.TRIAL_INTERVENTIONS for trials matching the therapeutic_area.
    Saves to neo4j_trial_interventions.csv.  Returns row count.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sql = """
        SELECT DISTINCT ti.*
        FROM CLEAN.TRIAL_INTERVENTIONS ti
        JOIN CLEAN.TRIALS t ON ti.nct_id = t.nct_id
        WHERE (%s IS NULL OR t.therapeutic_area = %s)
    """
    cur = conn.cursor()
    cur.execute(sql, (therapeutic_area, therapeutic_area))
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    cur.close()

    df = pd.DataFrame(rows, columns=columns)
    out_path = output_dir / "neo4j_trial_interventions.csv"
    df.to_csv(out_path, index=False)
    logger.info("Exported %d interventions to %s", len(df), out_path)
    return len(df)


# ---------------------------------------------------------------------------
# 4. export_criteria
# ---------------------------------------------------------------------------

def export_criteria(
    conn,
    therapeutic_area: str | None,
    output_dir: str | Path,
    parsed_only: bool = False,
) -> int:
    """
    Export CLEAN.ELIGIBILITY_CRITERIA joined with TRACKING.PARSING_PROGRESS
    (to include parsed_json where available).
    Saves to neo4j_criteria.csv.  Returns row count.

    If *parsed_only* is True, only export criteria that have been successfully
    parsed (parsing_status = 'parsed').  This keeps the Neo4j load fast.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if parsed_only:
        sql = """
            SELECT ec.*, pp.parsing_status, pp.parsed_json
            FROM CLEAN.ELIGIBILITY_CRITERIA ec
            JOIN TRACKING.PARSING_PROGRESS pp ON ec.criterion_id = pp.criterion_id
            WHERE pp.parsing_status = 'parsed'
              AND (%s IS NULL OR ec.therapeutic_area = %s)
        """
    else:
        sql = """
            SELECT ec.*, pp.parsing_status, pp.parsed_json
            FROM CLEAN.ELIGIBILITY_CRITERIA ec
            LEFT JOIN TRACKING.PARSING_PROGRESS pp ON ec.criterion_id = pp.criterion_id
            WHERE (%s IS NULL OR ec.therapeutic_area = %s)
        """
    cur = conn.cursor()
    cur.execute(sql, (therapeutic_area, therapeutic_area))
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    cur.close()

    df = pd.DataFrame(rows, columns=columns)
    out_path = output_dir / "neo4j_criteria.csv"
    df.to_csv(out_path, index=False)
    logger.info("Exported %d criteria to %s", len(df), out_path)
    return len(df)


# ---------------------------------------------------------------------------
# 5. export_entity_links
# ---------------------------------------------------------------------------

def export_entity_links(
    conn,
    output_dir: str | Path,
) -> int:
    """
    Export TRACKING.ENTITY_LINKING_PROGRESS.
    Saves to neo4j_entity_links.csv.  Returns row count.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sql = "SELECT * FROM TRACKING.ENTITY_LINKING_PROGRESS"
    cur = conn.cursor()
    cur.execute(sql)
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    cur.close()

    df = pd.DataFrame(rows, columns=columns)
    out_path = output_dir / "neo4j_entity_links.csv"
    df.to_csv(out_path, index=False)
    logger.info("Exported %d entity links to %s", len(df), out_path)
    return len(df)


# ---------------------------------------------------------------------------
# 6. export_all
# ---------------------------------------------------------------------------

def export_all(
    therapeutic_area: str | None = None,
    output_dir: str | Path | None = None,
    parsed_only: bool = False,
) -> None:
    """
    Connect to Snowflake and run all exports.
    Prints a summary of counts.

    If *parsed_only* is True, only export criteria that have been Cortex-parsed.
    """
    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_DIR
    output_dir = Path(output_dir)

    conn = _get_conn()
    try:
        t0 = time.monotonic()

        n_trials = export_trials(conn, therapeutic_area, output_dir)
        n_conditions = export_conditions(conn, therapeutic_area, output_dir)
        n_interventions = export_interventions(conn, therapeutic_area, output_dir)
        n_criteria = export_criteria(
            conn, therapeutic_area, output_dir, parsed_only=parsed_only,
        )
        n_links = export_entity_links(conn, output_dir)

        elapsed = time.monotonic() - t0
        area_label = therapeutic_area or "ALL"

        print(f"\n{'='*60}")
        print(f"Snowflake -> Neo4j CSV export complete  ({elapsed:.1f}s)")
        print(f"  Therapeutic area : {area_label}")
        print(f"  Parsed only      : {parsed_only}")
        print(f"  Trials           : {n_trials:>8,}")
        print(f"  Conditions       : {n_conditions:>8,}")
        print(f"  Interventions    : {n_interventions:>8,}")
        print(f"  Criteria         : {n_criteria:>8,}")
        print(f"  Entity links     : {n_links:>8,}")
        print(f"  Output directory : {output_dir}")
        print(f"{'='*60}\n")

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 7. CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export filtered Snowflake data to CSVs for Neo4j import.",
    )
    parser.add_argument(
        "--category",
        default="all",
        help='Therapeutic area to export (e.g. "oncology"). '
             'Use "all" to export everything. Default: all',
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=f"Output directory for CSVs. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--parsed-only",
        action="store_true",
        help="Only export criteria that have been successfully parsed.",
    )
    args = parser.parse_args()

    therapeutic_area = None if args.category.lower() == "all" else args.category
    export_all(
        therapeutic_area=therapeutic_area,
        output_dir=args.output_dir,
        parsed_only=args.parsed_only,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print("Phase 4: Snowflake export for Neo4j\n")
    main()
