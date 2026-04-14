"""
Phase 8 (Prompt 10): Parse eligibility criteria using Snowflake Cortex.

Uses Cortex COMPLETE() to parse free-text criteria into structured JSON
directly inside Snowflake — no external LLM API required.

Pipeline:
  1. Select a batch of unparsed criteria (filtered by therapeutic area)
  2. Run Cortex COMPLETE() over the batch in a single SQL statement
  3. Update TRACKING.PARSING_PROGRESS with results
  4. Repeat until done or limit reached

Resumable: tracks progress in Snowflake so you can stop and restart.

Usage:
    python -m nlp.criteria_parser --category oncology                    # parse
    python -m nlp.criteria_parser --category oncology --limit 25000      # parse up to 25K
    python -m nlp.criteria_parser --stats                                # show progress
    python -m nlp.criteria_parser --stats --category oncology            # oncology progress
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone

from neo4j import AsyncGraphDatabase

from config.settings import get_settings
from nlp.prompts import CRITERIA_PARSE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Cortex model to use — llama3.1-8b is cheap and fast for structured extraction
DEFAULT_MODEL = "llama3.3-70b"

# How many criteria to parse in a single SQL query.
# Larger = more efficient, but longer per query (and risk of timeout).
SQL_BATCH_SIZE = 500

# The system prompt embedded in each Cortex call
_SYSTEM_PROMPT = (
    "You are a clinical trial eligibility criteria parser. "
    "Given a single eligibility criterion, return ONLY a valid JSON object "
    "(no markdown fences, no explanation) with these fields: "
    '{"category": "age|gender|condition|biomarker|prior_therapy|lab_value|performance_status|other", '
    '"conditions": [{"name": "...", "snomed_hint": "..."}], '
    '"biomarkers": [{"name": "...", "status": "positive|negative|any"}], '
    '"drugs": [{"name": "...", "role": "required|excluded"}], '
    '"age_constraint": {"min": null, "max": null}, '
    '"gender_constraint": null, '
    '"lab_values": [{"name": "...", "operator": "<=|>=|=|<|>", "value": 0}], '
    '"logic": "plain English description of the rule", '
    '"confidence": 0.0}. '
    "Only populate fields explicitly stated. Set category to 'other' and confidence low if vague."
)


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _get_conn():
    cfg = get_settings()
    if cfg.snowflake is None:
        raise RuntimeError("Snowflake credentials not configured in .env")
    return cfg.snowflake.get_snowflake_connection()


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> dict | None:
    """Try to extract a JSON object from LLM output (may have markdown fences)."""
    if not raw:
        return None
    # Strip markdown fences
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    # Find the first { ... } block
    m = _JSON_RE.search(cleaned)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Scale warehouse
# ---------------------------------------------------------------------------

def _set_warehouse_size(conn, size: str) -> None:
    """Resize the warehouse (e.g. 'XLARGE', 'XSMALL')."""
    cur = conn.cursor()
    cur.execute(f"ALTER WAREHOUSE COMPUTE_WH SET WAREHOUSE_SIZE = '{size}'")
    cur.close()
    print(f"  Warehouse scaled to {size}.")


# ---------------------------------------------------------------------------
# Core parsing function
# ---------------------------------------------------------------------------

def parse_batch_cortex(
    conn,
    therapeutic_area: str,
    batch_size: int = SQL_BATCH_SIZE,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Parse one batch of unparsed criteria using Snowflake Cortex.

    Returns: {parsed: int, failed: int, remaining: int}
    """
    cur = conn.cursor()

    # Fetch a batch of unparsed criterion IDs + text
    cur.execute(
        """
        SELECT ec.criterion_id, ec.raw_text
        FROM CLEAN.ELIGIBILITY_CRITERIA ec
        JOIN TRACKING.PARSING_PROGRESS pp ON ec.criterion_id = pp.criterion_id
        WHERE pp.parsing_status = 'pending'
          AND ec.therapeutic_area = %s
        LIMIT %s
        """,
        (therapeutic_area, batch_size),
    )
    rows = cur.fetchall()

    if not rows:
        # Check remaining
        cur.execute(
            """
            SELECT COUNT(*) FROM TRACKING.PARSING_PROGRESS pp
            JOIN CLEAN.ELIGIBILITY_CRITERIA ec ON pp.criterion_id = ec.criterion_id
            WHERE pp.parsing_status = 'pending' AND ec.therapeutic_area = %s
            """,
            (therapeutic_area,),
        )
        remaining = cur.fetchone()[0]
        cur.close()
        return {"parsed": 0, "failed": 0, "remaining": remaining}

    criterion_ids = [r[0] for r in rows]
    raw_texts = {r[0]: r[1] for r in rows}

    # Build a temp table with the batch so we can run Cortex on it
    # Using a CTE with VALUES is cleaner for small batches
    cur.execute(
        """
        CREATE OR REPLACE TEMPORARY TABLE _PARSE_BATCH (
            criterion_id VARCHAR(50),
            raw_text TEXT
        )
        """,
    )

    # Insert batch rows
    cur.executemany(
        "INSERT INTO _PARSE_BATCH (criterion_id, raw_text) VALUES (%s, %s)",
        rows,
    )

    # Run Cortex COMPLETE over the batch
    cur.execute(
        f"""
        SELECT
            b.criterion_id,
            b.raw_text,
            SNOWFLAKE.CORTEX.COMPLETE(
                '{model}',
                CONCAT(
                    '{_SYSTEM_PROMPT.replace("'", "''")} Criterion: ',
                    b.raw_text
                )
            ) AS llm_response
        FROM _PARSE_BATCH b
        """,
    )
    results = cur.fetchall()

    # Process results and update tracking
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    parsed_count = 0
    failed_count = 0

    for criterion_id, raw_text, llm_response in results:
        parsed = _extract_json(llm_response)
        if parsed is not None:
            status = "parsed"
            parsed_json_str = json.dumps(parsed)
            error_msg = None
            parsed_count += 1
        else:
            status = "failed"
            parsed_json_str = None
            error_msg = f"Could not extract JSON from: {llm_response[:200]}"
            failed_count += 1

        cur.execute(
            """
            UPDATE TRACKING.PARSING_PROGRESS
            SET parsing_status  = %s,
                parsed_json     = PARSE_JSON(%s),
                parsed_at       = %s,
                llm_model       = %s,
                llm_tokens_used = 0,
                error_message   = %s,
                attempt_count   = attempt_count + 1
            WHERE criterion_id = %s
            """,
            (status, parsed_json_str, now, model, error_msg, criterion_id),
        )

    # Clean up
    cur.execute("DROP TABLE IF EXISTS _PARSE_BATCH")

    # Get remaining count
    cur.execute(
        """
        SELECT COUNT(*) FROM TRACKING.PARSING_PROGRESS pp
        JOIN CLEAN.ELIGIBILITY_CRITERIA ec ON pp.criterion_id = ec.criterion_id
        WHERE pp.parsing_status = 'pending' AND ec.therapeutic_area = %s
        """,
        (therapeutic_area,),
    )
    remaining = cur.fetchone()[0]
    cur.close()

    return {"parsed": parsed_count, "failed": failed_count, "remaining": remaining}


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_parsing_pipeline(
    therapeutic_area: str,
    limit: int | None = None,
    model: str = DEFAULT_MODEL,
    warehouse_size: str = "XLARGE",
) -> None:
    """
    Run the full Cortex parsing pipeline:
      1. Scale warehouse up
      2. Loop: parse batches until done or limit reached
      3. Scale warehouse back down
      4. Log pipeline run
    """
    conn = _get_conn()

    try:
        # Scale up
        _set_warehouse_size(conn, warehouse_size)

        total_parsed = 0
        total_failed = 0
        batch_num = 0
        pipeline_start = time.monotonic()

        # Get initial total for progress display
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM TRACKING.PARSING_PROGRESS pp
            JOIN CLEAN.ELIGIBILITY_CRITERIA ec ON pp.criterion_id = ec.criterion_id
            WHERE pp.parsing_status = 'pending' AND ec.therapeutic_area = %s
            """,
            (therapeutic_area,),
        )
        initial_pending = cur.fetchone()[0]
        cur.close()

        target = min(limit, initial_pending) if limit else initial_pending
        print(f"  Parsing {target:,} criteria with Cortex ({model}) …\n")

        while True:
            # Check if we've hit the limit
            if limit and total_parsed + total_failed >= limit:
                print(f"\n  Reached limit of {limit:,}.")
                break

            # Determine batch size (don't overshoot limit)
            batch_sz = SQL_BATCH_SIZE
            if limit:
                batch_sz = min(batch_sz, limit - total_parsed - total_failed)

            t0 = time.monotonic()
            result = parse_batch_cortex(
                conn, therapeutic_area, batch_size=batch_sz, model=model,
            )
            elapsed = time.monotonic() - t0

            if result["parsed"] == 0 and result["failed"] == 0:
                print(f"\n  No more pending criteria for '{therapeutic_area}'.")
                break

            total_parsed += result["parsed"]
            total_failed += result["failed"]
            batch_num += 1

            rate = (result["parsed"] + result["failed"]) / elapsed if elapsed > 0 else 0
            pct = (total_parsed + total_failed) / target * 100 if target > 0 else 100
            print(
                f"  Batch {batch_num}: "
                f"+{result['parsed']} parsed, +{result['failed']} failed "
                f"({elapsed:.1f}s, {rate:.1f}/s) | "
                f"Progress: {total_parsed + total_failed:,}/{target:,} ({pct:.1f}%) | "
                f"Remaining: {result['remaining']:,}"
            )

        total_elapsed = time.monotonic() - pipeline_start

        # Log pipeline run
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO TRACKING.PIPELINE_RUNS (
                run_id, run_type, therapeutic_area, status,
                started_at, completed_at,
                records_processed, records_succeeded, records_failed,
                error_message
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id, "cortex_parsing", therapeutic_area, "completed",
                now, now,
                total_parsed + total_failed, total_parsed, total_failed,
                None,
            ),
        )
        cur.close()

        print(f"\n{'='*60}")
        print(f"Cortex parsing complete in {total_elapsed:.1f}s")
        print(f"  Model           : {model}")
        print(f"  Therapeutic area: {therapeutic_area}")
        print(f"  Parsed          : {total_parsed:,}")
        print(f"  Failed          : {total_failed:,}")
        print(f"  Pipeline run    : {run_id}")
        print(f"{'='*60}\n")

    finally:
        # Always scale back down
        try:
            _set_warehouse_size(conn, "XSMALL")
        except Exception:
            print("  WARNING: Could not scale warehouse back to XSMALL. Do it manually.")
        conn.close()


# ---------------------------------------------------------------------------
# parse_single_criterion (LLM provider based — for non-Cortex use)
# ---------------------------------------------------------------------------

async def parse_single_criterion(llm, criterion_text: str) -> dict:
    """
    Parse a single criterion using an LLMProvider instance.

    Returns a validated dict, or a fallback {category: 'other', confidence: 0}
    on failure.
    """
    try:
        raw = await llm.complete(
            system_prompt=CRITERIA_PARSE_SYSTEM_PROMPT,
            user_prompt=f"Criterion: {criterion_text}",
            temperature=0.0,
            max_tokens=500,
        )
        parsed = _extract_json(raw)
        if parsed is not None:
            return parsed
    except Exception as exc:
        logger.warning("LLM parse failed for '%s': %s", criterion_text[:80], exc)

    return {"category": "other", "confidence": 0.0, "logic": criterion_text}


# ---------------------------------------------------------------------------
# enrich_graph_with_parsed_criteria
# ---------------------------------------------------------------------------

async def enrich_graph_with_parsed_criteria(
    therapeutic_area: str = "oncology",
) -> None:
    """
    Read parsed_json from Criterion nodes in Neo4j and create typed edges:
      - inclusion + condition → REQUIRES_CONDITION → SNOMEDConcept (via name match)
      - exclusion + condition → EXCLUDES_CONDITION → SNOMEDConcept
      - inclusion + drug     → REQUIRES_PRIOR_DRUG → RxNormConcept
      - exclusion + drug     → EXCLUDES_PRIOR_DRUG → RxNormConcept
      - biomarker            → REQUIRES_BIOMARKER / EXCLUDES_BIOMARKER → Biomarker node

    Uses the entity linker for fuzzy matching condition/drug names to ontology.
    """
    from nlp.entity_linker import EntityLinker

    cfg = get_settings()
    driver = AsyncGraphDatabase.driver(
        cfg.neo4j.uri,
        auth=(cfg.neo4j.user, cfg.neo4j.password),
    )
    linker = EntityLinker()

    try:
        async with driver.session() as session:
            result = await session.run("RETURN 1 AS n")
            await result.single()
        print("Connected to Neo4j.\n")

        # Fetch all parsed criteria
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (t:Trial)-[:HAS_CRITERION]->(c:Criterion)
                WHERE c.parsed_json IS NOT NULL AND c.parsed_json <> ''
                RETURN c.id AS criterion_id, c.type AS crit_type,
                       c.parsed_json AS parsed_json, t.nct_id AS nct_id
                """
            )
            records = [r async for r in result]

        print(f"  Found {len(records):,} parsed criteria to enrich.\n")

        condition_edges = 0
        drug_edges = 0
        biomarker_edges = 0
        skipped = 0

        for rec in records:
            pj = rec["parsed_json"]
            if isinstance(pj, str):
                try:
                    pj = json.loads(pj)
                except json.JSONDecodeError:
                    skipped += 1
                    continue

            if not isinstance(pj, dict):
                skipped += 1
                continue

            crit_type = rec["crit_type"]  # 'inclusion' or 'exclusion'
            criterion_id = rec["criterion_id"]

            # --- Conditions → SNOMED ---
            for cond in pj.get("conditions") or []:
                cond_name = cond.get("name", "").strip()
                if not cond_name:
                    continue

                hits = linker.link_condition_to_snomed(cond_name, threshold=80.0)
                if not hits:
                    continue

                best = hits[0]
                rel_type = (
                    "REQUIRES_CONDITION" if crit_type == "inclusion"
                    else "EXCLUDES_CONDITION"
                )

                async with driver.session() as session:
                    await session.run(
                        f"""
                        MATCH (cr:Criterion {{id: $crit_id}})
                        MATCH (sc:SNOMEDConcept {{concept_id: $concept_id}})
                        MERGE (cr)-[:{rel_type} {{score: $score, method: $method}}]->(sc)
                        """,
                        crit_id=criterion_id,
                        concept_id=best["concept_id"],
                        score=best["score"],
                        method=best["method"],
                    )
                condition_edges += 1

            # --- Drugs → RxNorm ---
            for drug in pj.get("drugs") or []:
                drug_name = drug.get("name", "").strip()
                if not drug_name:
                    continue

                hits = linker.link_drug_to_rxnorm(drug_name, threshold=80.0)
                if not hits:
                    continue

                best = hits[0]
                role = drug.get("role", "required")
                rel_type = (
                    "EXCLUDES_PRIOR_DRUG" if role == "excluded" or crit_type == "exclusion"
                    else "REQUIRES_PRIOR_DRUG"
                )

                async with driver.session() as session:
                    await session.run(
                        f"""
                        MATCH (cr:Criterion {{id: $crit_id}})
                        MATCH (rx:RxNormConcept {{rxcui: $rxcui}})
                        MERGE (cr)-[:{rel_type} {{score: $score, method: $method}}]->(rx)
                        """,
                        crit_id=criterion_id,
                        rxcui=best["rxcui"],
                        score=best["score"],
                        method=best["method"],
                    )
                drug_edges += 1

            # --- Biomarkers → Biomarker nodes ---
            for bm in pj.get("biomarkers") or []:
                bm_name = bm.get("name", "").strip()
                bm_status = bm.get("status", "any")
                if not bm_name:
                    continue

                rel_type = (
                    "REQUIRES_BIOMARKER" if crit_type == "inclusion"
                    else "EXCLUDES_BIOMARKER"
                )

                async with driver.session() as session:
                    await session.run(
                        f"""
                        MERGE (b:Biomarker {{normalized_name: toLower($bm_name)}})
                        SET b.name = $bm_name
                        WITH b
                        MATCH (cr:Criterion {{id: $crit_id}})
                        MERGE (cr)-[:{rel_type} {{status: $status}}]->(b)
                        """,
                        bm_name=bm_name,
                        crit_id=criterion_id,
                        status=bm_status,
                    )
                biomarker_edges += 1

        print(f"{'='*60}")
        print(f"Graph enrichment complete")
        print(f"  REQUIRES/EXCLUDES_CONDITION edges : {condition_edges:,}")
        print(f"  REQUIRES/EXCLUDES_PRIOR_DRUG edges: {drug_edges:,}")
        print(f"  REQUIRES/EXCLUDES_BIOMARKER edges : {biomarker_edges:,}")
        print(f"  Skipped (bad JSON)                : {skipped:,}")
        print(f"{'='*60}\n")

    finally:
        await driver.close()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def print_parsing_stats(therapeutic_area: str | None = None) -> None:
    """Print parsing progress stats."""
    conn = _get_conn()
    cur = conn.cursor()

    if therapeutic_area:
        cur.execute(
            """
            SELECT pp.parsing_status, COUNT(*) AS cnt
            FROM TRACKING.PARSING_PROGRESS pp
            JOIN CLEAN.ELIGIBILITY_CRITERIA ec ON pp.criterion_id = ec.criterion_id
            WHERE ec.therapeutic_area = %s
            GROUP BY pp.parsing_status
            ORDER BY cnt DESC
            """,
            (therapeutic_area,),
        )
        print(f"\nParsing stats for '{therapeutic_area}':")
    else:
        cur.execute(
            """
            SELECT parsing_status, COUNT(*) AS cnt
            FROM TRACKING.PARSING_PROGRESS
            GROUP BY parsing_status
            ORDER BY cnt DESC
            """,
        )
        print("\nParsing stats (all areas):")

    total = 0
    for status, cnt in cur.fetchall():
        print(f"  {status:<12} : {cnt:>10,}")
        total += cnt
    print(f"  {'total':<12} : {total:>10,}")

    # Recent pipeline runs
    cur.execute(
        """
        SELECT run_id, run_type, therapeutic_area, status,
               records_processed, records_succeeded, records_failed, started_at
        FROM TRACKING.PIPELINE_RUNS
        ORDER BY started_at DESC
        LIMIT 5
        """,
    )
    runs = cur.fetchall()
    if runs:
        print(f"\nRecent pipeline runs:")
        for r in runs:
            print(f"  {r[7]} | {r[1]} | {r[2]} | {r[3]} | "
                  f"{r[4]:,} processed ({r[5]:,} ok, {r[6]:,} fail)")

    cur.close()
    conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse eligibility criteria using Snowflake Cortex.",
    )
    parser.add_argument(
        "--category",
        default="oncology",
        help='Therapeutic area to parse. Default: oncology',
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max criteria to parse (default: all pending in category).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Cortex model to use. Default: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--warehouse-size",
        default="XLARGE",
        help="Warehouse size during parsing. Default: XLARGE",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show parsing progress stats and exit.",
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Enrich Neo4j graph with edges from parsed criteria.",
    )
    args = parser.parse_args()

    if args.stats:
        cat = None if args.category.lower() == "all" else args.category
        print_parsing_stats(cat)
        return

    if args.enrich:
        import asyncio
        asyncio.run(enrich_graph_with_parsed_criteria(args.category))
        return

    run_parsing_pipeline(
        therapeutic_area=args.category,
        limit=args.limit,
        model=args.model,
        warehouse_size=args.warehouse_size,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print("Phase 8: Cortex-based criteria parsing\n")
    main()
