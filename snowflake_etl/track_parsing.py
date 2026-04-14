"""
Phase 4 (Prompt 6): Track LLM parsing progress in Snowflake TRACKING tables.

Functions:
  1. get_unparsed_criteria()   — fetch pending criteria for a therapeutic area
  2. update_parsing_result()   — update one criterion's parsing result
  3. update_batch_results()    — batch-update multiple criteria
  4. get_parsing_stats()       — return {pending, parsed, failed, skipped, total}
  5. log_pipeline_run()        — insert into TRACKING.PIPELINE_RUNS

Usage:
    Imported by the NLP parsing pipeline (nlp/criteria_parser.py).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from config.settings import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _get_conn():
    cfg = get_settings()
    if cfg.snowflake is None:
        raise RuntimeError("Snowflake credentials not configured in .env")
    return cfg.snowflake.get_snowflake_connection()


# ---------------------------------------------------------------------------
# 1. get_unparsed_criteria
# ---------------------------------------------------------------------------

def get_unparsed_criteria(
    conn,
    therapeutic_area: str,
    batch_size: int = 500,
) -> list[dict]:
    """
    Fetch up to *batch_size* criteria that still have parsing_status = 'pending'
    for the given therapeutic_area.

    Returns a list of dicts with keys:
      criterion_id, nct_id, criterion_type, raw_text
    """
    sql = """
        SELECT ec.criterion_id, ec.nct_id, ec.criterion_type, ec.raw_text
        FROM CLEAN.ELIGIBILITY_CRITERIA ec
        JOIN TRACKING.PARSING_PROGRESS pp ON ec.criterion_id = pp.criterion_id
        WHERE pp.parsing_status = 'pending'
          AND ec.therapeutic_area = %s
        LIMIT %s
    """
    cur = conn.cursor()
    cur.execute(sql, (therapeutic_area, batch_size))
    columns = [desc[0].lower() for desc in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    cur.close()

    logger.info(
        "get_unparsed_criteria: fetched %d pending criteria for '%s'",
        len(rows),
        therapeutic_area,
    )
    return rows


# ---------------------------------------------------------------------------
# 2. update_parsing_result
# ---------------------------------------------------------------------------

def update_parsing_result(
    conn,
    criterion_id: str,
    status: str,
    parsed_json: dict | None,
    model: str,
    tokens: int,
    error: str | None = None,
) -> None:
    """
    Update TRACKING.PARSING_PROGRESS for a single criterion.

    Increments attempt_count and sets parsed_at to current UTC time.
    """
    sql = """
        UPDATE TRACKING.PARSING_PROGRESS
        SET parsing_status  = %s,
            parsed_json     = PARSE_JSON(%s),
            parsed_at       = %s,
            llm_model       = %s,
            llm_tokens_used = %s,
            error_message   = %s,
            attempt_count   = attempt_count + 1
        WHERE criterion_id = %s
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    parsed_str = json.dumps(parsed_json) if parsed_json is not None else None

    cur = conn.cursor()
    cur.execute(sql, (status, parsed_str, now, model, tokens, error, criterion_id))
    cur.close()

    logger.debug("Updated criterion %s -> %s", criterion_id, status)


# ---------------------------------------------------------------------------
# 3. update_batch_results
# ---------------------------------------------------------------------------

def update_batch_results(conn, results: list[dict]) -> None:
    """
    Batch-update many criteria at once.

    Each dict in *results* must have keys:
      criterion_id, status, parsed_json, model, tokens, error (optional)
    """
    if not results:
        return

    sql = """
        UPDATE TRACKING.PARSING_PROGRESS
        SET parsing_status  = %s,
            parsed_json     = PARSE_JSON(%s),
            parsed_at       = %s,
            llm_model       = %s,
            llm_tokens_used = %s,
            error_message   = %s,
            attempt_count   = attempt_count + 1
        WHERE criterion_id = %s
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    cur = conn.cursor()
    for r in results:
        parsed_str = (
            json.dumps(r["parsed_json"]) if r.get("parsed_json") is not None else None
        )
        cur.execute(sql, (
            r["status"],
            parsed_str,
            now,
            r["model"],
            r["tokens"],
            r.get("error"),
            r["criterion_id"],
        ))
    cur.close()

    logger.info("update_batch_results: updated %d criteria", len(results))


# ---------------------------------------------------------------------------
# 4. get_parsing_stats
# ---------------------------------------------------------------------------

def get_parsing_stats(
    conn,
    therapeutic_area: str | None = None,
) -> dict[str, int]:
    """
    Return parsing progress counts: {pending, parsed, failed, skipped, total}.

    Optionally filtered by therapeutic_area (via join with CLEAN.ELIGIBILITY_CRITERIA).
    """
    if therapeutic_area:
        sql = """
            SELECT pp.parsing_status, COUNT(*) AS cnt
            FROM TRACKING.PARSING_PROGRESS pp
            JOIN CLEAN.ELIGIBILITY_CRITERIA ec ON pp.criterion_id = ec.criterion_id
            WHERE ec.therapeutic_area = %s
            GROUP BY pp.parsing_status
        """
        params = (therapeutic_area,)
    else:
        sql = """
            SELECT parsing_status, COUNT(*) AS cnt
            FROM TRACKING.PARSING_PROGRESS
            GROUP BY parsing_status
        """
        params = ()

    cur = conn.cursor()
    cur.execute(sql, params)
    counts = {row[0]: row[1] for row in cur.fetchall()}
    cur.close()

    stats = {
        "pending":  counts.get("pending", 0),
        "parsed":   counts.get("parsed", 0),
        "failed":   counts.get("failed", 0),
        "skipped":  counts.get("skipped", 0),
    }
    stats["total"] = sum(stats.values())

    logger.info("Parsing stats%s: %s",
                f" ({therapeutic_area})" if therapeutic_area else "",
                stats)
    return stats


# ---------------------------------------------------------------------------
# 5. log_pipeline_run
# ---------------------------------------------------------------------------

def log_pipeline_run(
    conn,
    run_type: str,
    therapeutic_area: str,
    status: str,
    records_processed: int,
    records_succeeded: int,
    records_failed: int,
    error: str | None = None,
) -> str:
    """
    Insert a row into TRACKING.PIPELINE_RUNS.

    Returns the generated run_id.
    """
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    sql = """
        INSERT INTO TRACKING.PIPELINE_RUNS (
            run_id, run_type, therapeutic_area, status,
            started_at, completed_at,
            records_processed, records_succeeded, records_failed,
            error_message
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    cur = conn.cursor()
    cur.execute(sql, (
        run_id,
        run_type,
        therapeutic_area,
        status,
        now,
        now,
        records_processed,
        records_succeeded,
        records_failed,
        error,
    ))
    cur.close()

    logger.info(
        "Logged pipeline run %s: type=%s area=%s status=%s processed=%d",
        run_id, run_type, therapeutic_area, status, records_processed,
    )
    return run_id
