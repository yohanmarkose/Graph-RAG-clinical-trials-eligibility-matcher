"""
Phase 2 — Demo fetcher for quick development testing.

Fetches a limited batch of N trials for a specific condition (default: breast cancer)
and processes them the same way as the full pipeline.

Usage:
    python -m data_ingestion.fetch_trials_demo
    python -m data_ingestion.fetch_trials_demo --condition "lung cancer" --limit 200
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

from config.settings import get_settings
from data_ingestion.fetch_trials import (
    FIELDS,
    classify_therapeutic_area,
    parse_trial_record,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
REQUEST_DELAY = 0.5
MAX_RETRIES = 3
BACKOFF_BASE = 2.0


def fetch_demo_trials(
    condition: str = "breast cancer",
    limit: int = 500,
    output_dir: Path | None = None,
) -> Path:
    """
    Fetch up to `limit` trials for the given `condition` from the V2 API.

    Saves raw results to  output_dir/demo_trials_{condition_slug}.json
    and returns the file path.
    """
    settings = get_settings()
    base_url = settings.pipeline.clinicaltrials_base_url

    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
    output_dir.mkdir(parents=True, exist_ok=True)

    all_studies: list[dict] = []
    next_page_token: str | None = None
    page_num = 0

    params: dict[str, Any] = {
        "query.cond": condition,
        "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING",
        "pageSize": min(PAGE_SIZE, limit),
        "fields": FIELDS,
        "format": "json",
    }

    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    while len(all_studies) < limit:
        if next_page_token:
            params["pageToken"] = next_page_token
        elif "pageToken" in params:
            del params["pageToken"]

        for attempt in range(MAX_RETRIES):
            try:
                response = session.get(
                    f"{base_url}/studies",
                    params=params,
                    timeout=30,
                )
                response.raise_for_status()
                data = response.json()
                break
            except requests.RequestException as exc:
                if attempt == MAX_RETRIES - 1:
                    logger.error("Failed after %d retries: %s", MAX_RETRIES, exc)
                    raise
                wait = BACKOFF_BASE ** (attempt + 1)
                logger.warning(
                    "Attempt %d/%d failed: %s — retrying in %.1fs",
                    attempt + 1, MAX_RETRIES, exc, wait,
                )
                time.sleep(wait)

        studies = data.get("studies") or []
        all_studies.extend(studies)
        page_num += 1

        logger.info(
            "Demo page %d fetched — trials so far: %d / %d",
            page_num, len(all_studies), limit,
        )

        next_page_token = data.get("nextPageToken")
        if not next_page_token or not studies:
            break

        time.sleep(REQUEST_DELAY)

    session.close()

    # Trim to requested limit
    all_studies = all_studies[:limit]

    slug = condition.lower().replace(" ", "_")
    out_path = output_dir / f"demo_trials_{slug}.json"
    out_path.write_text(json.dumps(all_studies, indent=2), encoding="utf-8")
    logger.info("Saved %d demo trials to %s", len(all_studies), out_path)
    return out_path


def process_demo_trials(raw_path: Path, output_path: Path | None = None) -> list[dict]:
    """Parse, classify, and save demo trials; print a summary."""
    if output_path is None:
        slug = raw_path.stem  # e.g. demo_trials_breast_cancer
        processed_dir = raw_path.parent.parent / "processed"
        processed_dir.mkdir(parents=True, exist_ok=True)
        output_path = processed_dir / f"{slug}_processed.json"

    raw_studies = json.loads(raw_path.read_text(encoding="utf-8"))
    processed: list[dict] = []
    for raw in raw_studies:
        trial = parse_trial_record(raw)
        trial["therapeutic_area"] = classify_therapeutic_area(trial["conditions"])
        processed.append(trial)

    output_path.write_text(json.dumps(processed, indent=2), encoding="utf-8")

    # Quick summary
    with_elig = sum(1 for t in processed if t.get("eligibility_criteria"))
    phases = {}
    for t in processed:
        p = t.get("phase") or "Unknown"
        phases[p] = phases.get(p, 0) + 1

    print(f"\n{'='*55}")
    print(f"Demo trials processed  : {len(processed)}")
    print(f"With eligibility text  : {with_elig}")
    print(f"Phase distribution     : {phases}")
    print(f"Output                 : {output_path}")
    print(f"{'='*55}\n")

    return processed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Fetch a small demo batch of trials.")
    parser.add_argument(
        "--condition",
        default="breast cancer",
        help="Condition to search (default: 'breast cancer')",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum number of trials to fetch (default: 500)",
    )
    args = parser.parse_args()

    print(f"Demo fetch: '{args.condition}', limit={args.limit}")
    t0 = time.monotonic()
    raw_path = fetch_demo_trials(args.condition, args.limit)
    elapsed = time.monotonic() - t0
    print(f"Fetched in {elapsed:.1f}s")

    process_demo_trials(raw_path)
    print("Demo pipeline complete.")
