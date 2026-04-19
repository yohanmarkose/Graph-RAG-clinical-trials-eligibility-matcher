"""
Snowflake transformations — STAGING → CLEAN → TRACKING.

Functions:
  1. classify_therapeutic_areas()     STAGING.RAW_TRIALS → CLEAN.TRIALS
  2. flatten_conditions()             STAGING.RAW_TRIALS → CLEAN.TRIAL_CONDITIONS
  3. flatten_interventions()          STAGING.RAW_TRIALS → CLEAN.TRIAL_INTERVENTIONS
  4. split_eligibility_criteria()     CLEAN.TRIALS → CLEAN.ELIGIBILITY_CRITERIA  (Python-side)
  5. initialize_parsing_tracking()    CLEAN.ELIGIBILITY_CRITERIA → TRACKING.PARSING_PROGRESS
  6. print_summary_stats()            console stats
  7. get_therapeutic_area_stats()     helper → dict

Usage:
    python -m snowflake_etl.transform
"""

from __future__ import annotations

import logging
import re
import sys
import time
import uuid
from typing import Any

import pandas as pd
from snowflake.connector.pandas_tools import write_pandas

from config.settings import get_settings

logger = logging.getLogger(__name__)

BATCH_SIZE = 5_000


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _get_conn():
    cfg = get_settings()
    if cfg.snowflake is None:
        raise RuntimeError("Snowflake credentials not configured in .env")
    return cfg.snowflake.get_snowflake_connection()


def _exec(conn, sql: str, params: dict | None = None) -> Any:
    cur = conn.cursor()
    try:
        cur.execute(sql, params or {})
        return cur
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# 1. classify_therapeutic_areas
# ---------------------------------------------------------------------------

_CLASSIFY_SQL = """
INSERT INTO CLEAN.TRIALS (
    nct_id, title, official_title, brief_summary, status, phase,
    enrollment, start_date, primary_completion_date, sponsor,
    gender, min_age, max_age, therapeutic_area, eligibility_criteria,
    conditions_count, interventions_count, criteria_char_length,
    has_eligibility_text, url
)
SELECT
    nct_id,
    title,
    official_title,
    brief_summary,
    status,
    phase,
    enrollment,
    TRY_TO_DATE(start_date, 'YYYY-MM-DD')               AS start_date,
    TRY_TO_DATE(primary_completion_date, 'YYYY-MM-DD')  AS primary_completion_date,
    sponsor,
    gender,
    min_age,
    max_age,
    CASE
        WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP
             '.*(cancer|carcinoma|tumor|tumour|neoplasm|lymphoma|leukemia|melanoma|sarcoma|myeloma|glioma|glioblastoma).*'
             THEN 'oncology'
        WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP
             '.*(heart|cardiac|cardiovascular|hypertension|coronary|arrhythmia|cardiomyopathy).*'
             THEN 'cardiology'
        WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP
             '.*(alzheimer|parkinson|epilepsy|multiple sclerosis|neuropathy|stroke|dementia|migraine).*'
             THEN 'neurology'
        WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP
             '.*(diabetes|thyroid|obesity|metabolic|insulin).*'
             THEN 'endocrinology'
        WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP
             '.*(lupus|rheumatoid|psoriasis|crohn|colitis|autoimmune).*'
             THEN 'immunology'
        WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP
             '.*(hiv|hepatitis|tuberculosis|malaria|covid|infection).*'
             THEN 'infectious_disease'
        WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP
             '.*(asthma|copd|pulmonary|respiratory|cystic fibrosis).*'
             THEN 'pulmonology'
        WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP
             '.*(depression|anxiety|schizophrenia|bipolar|ptsd|adhd).*'
             THEN 'psychiatry'
        ELSE 'other'
    END AS therapeutic_area,
    eligibility_criteria,
    ARRAY_SIZE(conditions)                              AS conditions_count,
    ARRAY_SIZE(interventions)                           AS interventions_count,
    LENGTH(eligibility_criteria)                        AS criteria_char_length,
    (eligibility_criteria IS NOT NULL
     AND LENGTH(eligibility_criteria) > 10)             AS has_eligibility_text,
    CONCAT('https://clinicaltrials.gov/study/', nct_id) AS url
FROM STAGING.RAW_TRIALS
WHERE nct_id NOT IN (SELECT nct_id FROM CLEAN.TRIALS)
"""


def classify_therapeutic_areas(conn=None) -> int:
    """Populate CLEAN.TRIALS from STAGING.RAW_TRIALS (insert new rows only)."""
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_CLASSIFY_SQL)
        n = cur.rowcount
        cur.close()
        logger.info("classify_therapeutic_areas: inserted %d rows into CLEAN.TRIALS", n)
        return n
    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# 2. flatten_conditions
# ---------------------------------------------------------------------------

_FLATTEN_CONDITIONS_SQL = """
INSERT INTO CLEAN.TRIAL_CONDITIONS (nct_id, condition_name, condition_normalized)
SELECT
    t.nct_id,
    f.value::STRING                  AS condition_name,
    LOWER(TRIM(f.value::STRING))     AS condition_normalized
FROM STAGING.RAW_TRIALS t,
     LATERAL FLATTEN(input => t.conditions) f
WHERE t.nct_id NOT IN (
    SELECT DISTINCT nct_id FROM CLEAN.TRIAL_CONDITIONS
)
"""


def flatten_conditions(conn=None) -> int:
    """Flatten conditions VARIANT array into CLEAN.TRIAL_CONDITIONS."""
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_FLATTEN_CONDITIONS_SQL)
        n = cur.rowcount
        cur.close()
        logger.info("flatten_conditions: inserted %d rows into CLEAN.TRIAL_CONDITIONS", n)
        return n
    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# 3. flatten_interventions
# ---------------------------------------------------------------------------

_FLATTEN_INTERVENTIONS_SQL = """
INSERT INTO CLEAN.TRIAL_INTERVENTIONS (
    nct_id, intervention_name, intervention_type, intervention_normalized
)
SELECT
    t.nct_id,
    f.value:name::STRING                      AS intervention_name,
    f.value:type::STRING                      AS intervention_type,
    LOWER(TRIM(f.value:name::STRING))         AS intervention_normalized
FROM STAGING.RAW_TRIALS t,
     LATERAL FLATTEN(input => t.interventions) f
WHERE t.nct_id NOT IN (
    SELECT DISTINCT nct_id FROM CLEAN.TRIAL_INTERVENTIONS
)
"""


def flatten_interventions(conn=None) -> int:
    """Flatten interventions VARIANT array into CLEAN.TRIAL_INTERVENTIONS."""
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_FLATTEN_INTERVENTIONS_SQL)
        n = cur.rowcount
        cur.close()
        logger.info("flatten_interventions: inserted %d rows into CLEAN.TRIAL_INTERVENTIONS", n)
        return n
    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# 4. split_eligibility_criteria  (Python-side)
# ---------------------------------------------------------------------------

# Patterns that mark section headers (not criteria themselves)
_INCLUSION_RE = re.compile(r"inclusion criteria", re.IGNORECASE)
_EXCLUSION_RE = re.compile(r"exclusion criteria", re.IGNORECASE)
_SKIP_RE = re.compile(
    r"^\s*$"                        # blank
    r"|^[\-=_*]{3,}\s*$"           # divider lines
    r"|^\s*note\s*:",               # "Note:" headers
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^[\d]+[\.\)]\s*|^[-•*·]\s*")


def _split_criteria_text(
    text: str,
    nct_id: str,
    therapeutic_area: str | None,
) -> list[dict]:
    """
    Split a raw eligibility text block into individual criterion dicts.

    Tracks inclusion/exclusion state via section headers.
    Strips bullet/number prefixes.  Skips blank and header-only lines.
    """
    criteria: list[dict] = []
    state: str | None = None
    sentence_index = 0

    for line in text.splitlines():
        stripped = line.strip()

        # Section header detection
        if _INCLUSION_RE.search(stripped):
            state = "inclusion"
            continue
        if _EXCLUSION_RE.search(stripped):
            state = "exclusion"
            continue

        # Skip until we've seen a section header
        if state is None:
            continue

        # Skip blank / divider / note lines
        if _SKIP_RE.match(stripped):
            continue

        # Strip leading bullet / number prefix
        cleaned = _BULLET_RE.sub("", stripped).strip()

        if len(cleaned) < 5:
            continue

        criteria.append({
            "CRITERION_ID":     str(uuid.uuid4()),
            "NCT_ID":           nct_id,
            "CRITERION_TYPE":   state,
            "RAW_TEXT":         cleaned,
            "SENTENCE_INDEX":   sentence_index,
            "THERAPEUTIC_AREA": therapeutic_area or "other",
        })
        sentence_index += 1

    return criteria


def split_eligibility_criteria(conn=None) -> int:
    """
    Read trials with eligibility text from CLEAN.TRIALS, split each into
    individual criterion sentences, and write to CLEAN.ELIGIBILITY_CRITERIA.

    Only processes trials not already present in CLEAN.ELIGIBILITY_CRITERIA.
    Returns total criteria rows inserted.
    """
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()

    try:
        cur = conn.cursor()

        # Load trials that haven't been split yet
        cur.execute("""
            SELECT nct_id, eligibility_criteria, therapeutic_area
            FROM CLEAN.TRIALS
            WHERE has_eligibility_text = TRUE
              AND nct_id NOT IN (
                  SELECT DISTINCT nct_id FROM CLEAN.ELIGIBILITY_CRITERIA
              )
        """)
        rows = cur.fetchall()
        cur.close()

        if not rows:
            logger.info("split_eligibility_criteria: no new trials to process")
            return 0

        logger.info("Splitting eligibility criteria for %d trials …", len(rows))

        all_criteria: list[dict] = []
        for nct_id, elig_text, therapeutic_area in rows:
            if not elig_text:
                continue
            all_criteria.extend(
                _split_criteria_text(elig_text, nct_id, therapeutic_area)
            )

        if not all_criteria:
            return 0

        df = pd.DataFrame(all_criteria)
        total = 0
        schema, tbl = "CLEAN", "ELIGIBILITY_CRITERIA"
        for i in range(0, len(df), BATCH_SIZE):
            chunk = df.iloc[i : i + BATCH_SIZE]
            _, _, nrows, _ = write_pandas(
                conn,
                chunk,
                tbl,
                schema=schema,
                database="CLINICAL_TRIALS",
                overwrite=False,
                auto_create_table=False,
                quote_identifiers=False,
            )
            total += nrows

        logger.info("split_eligibility_criteria: inserted %d criterion rows", total)
        return total

    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# 5. initialize_parsing_tracking
# ---------------------------------------------------------------------------

def initialize_parsing_tracking(conn=None) -> int:
    """
    Insert 'pending' rows into TRACKING.PARSING_PROGRESS for every criterion
    in CLEAN.ELIGIBILITY_CRITERIA that isn't already tracked.

    Returns number of new tracking rows inserted.
    """
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO TRACKING.PARSING_PROGRESS (criterion_id, nct_id, parsing_status)
            SELECT criterion_id, nct_id, 'pending'
            FROM CLEAN.ELIGIBILITY_CRITERIA
            WHERE criterion_id NOT IN (
                SELECT criterion_id FROM TRACKING.PARSING_PROGRESS
            )
        """)
        n = cur.rowcount
        cur.close()
        logger.info("initialize_parsing_tracking: inserted %d tracking rows", n)
        return n
    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# 6. print_summary_stats
# ---------------------------------------------------------------------------

def print_summary_stats(conn=None) -> None:
    """Print key stats from the CLEAN and TRACKING schemas to stdout."""
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()

    try:
        cur = conn.cursor()

        # Totals
        cur.execute("""
            SELECT
                (SELECT COUNT(*) FROM CLEAN.TRIALS)              AS total_trials,
                (SELECT COUNT(DISTINCT condition_normalized)
                 FROM CLEAN.TRIAL_CONDITIONS)                     AS unique_conditions,
                (SELECT COUNT(DISTINCT intervention_normalized)
                 FROM CLEAN.TRIAL_INTERVENTIONS)                  AS unique_interventions,
                (SELECT COUNT(*) FROM CLEAN.ELIGIBILITY_CRITERIA) AS total_criteria,
                (SELECT COUNT(*) FROM TRACKING.PARSING_PROGRESS
                 WHERE parsing_status = 'pending')                AS pending_criteria
        """)
        row = cur.fetchone()
        print(f"\n{'='*60}")
        print(f"CLEAN.TRIALS                : {row[0]:>10,}")
        print(f"Unique conditions           : {row[1]:>10,}")
        print(f"Unique interventions        : {row[2]:>10,}")
        print(f"Eligibility criteria rows   : {row[3]:>10,}")
        print(f"Pending LLM parsing         : {row[4]:>10,}")

        # By therapeutic area
        cur.execute("""
            SELECT therapeutic_area, COUNT(*) AS cnt
            FROM CLEAN.TRIALS
            GROUP BY 1 ORDER BY 2 DESC
        """)
        print(f"\nTrials by therapeutic area:")
        for area, cnt in cur.fetchall():
            print(f"  {area:<25} {cnt:>7,}")

        # Criteria by therapeutic area
        cur.execute("""
            SELECT therapeutic_area, COUNT(*) AS cnt
            FROM CLEAN.ELIGIBILITY_CRITERIA
            GROUP BY 1 ORDER BY 2 DESC
        """)
        print(f"\nCriteria by therapeutic area:")
        for area, cnt in cur.fetchall():
            print(f"  {area:<25} {cnt:>7,}")

        # Trials with / without eligibility text
        cur.execute("""
            SELECT has_eligibility_text, COUNT(*) FROM CLEAN.TRIALS GROUP BY 1
        """)
        print(f"\nTrials with eligibility text:")
        for has_text, cnt in cur.fetchall():
            label = "yes" if has_text else "no"
            print(f"  {label}: {cnt:,}")
        print(f"{'='*60}\n")

        cur.close()

    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# 7. get_therapeutic_area_stats  (helper used by pipeline runner)
# ---------------------------------------------------------------------------

def get_therapeutic_area_stats(conn=None) -> dict[str, int]:
    """Return {therapeutic_area: trial_count} from CLEAN.TRIALS."""
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT therapeutic_area, COUNT(*) AS cnt
            FROM CLEAN.TRIALS
            GROUP BY 1 ORDER BY 2 DESC
        """)
        result = {row[0]: row[1] for row in cur.fetchall()}
        cur.close()
        return result
    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("Phase 4: Snowflake transform pipeline\n")
    conn = _get_conn()

    try:
        steps = [
            ("1/5 - Classifying therapeutic areas (STAGING -> CLEAN.TRIALS)",
             lambda: classify_therapeutic_areas(conn)),
            ("2/5 - Flattening conditions",
             lambda: flatten_conditions(conn)),
            ("3/5 - Flattening interventions",
             lambda: flatten_interventions(conn)),
            ("4/5 - Splitting eligibility criteria",
             lambda: split_eligibility_criteria(conn)),
            ("5/5 - Initialising parsing tracking",
             lambda: initialize_parsing_tracking(conn)),
        ]

        for label, fn in steps:
            t0 = time.monotonic()
            print(f"Step {label} ...")
            n = fn()
            print(f"  -> {n:,} rows  ({time.monotonic()-t0:.1f}s)\n")

        print_summary_stats(conn)

    finally:
        conn.close()

    print("Transform pipeline complete.")
