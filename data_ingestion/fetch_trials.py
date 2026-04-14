"""
Phase 2: ClinicalTrials.gov data ingestion pipeline.

Fetches ALL active/recruiting trials (50-80K) from the ClinicalTrials.gov V2 API,
parses and normalises each record, classifies by therapeutic area, and saves results.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import requests

from config.settings import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLINICAL_TRIALS_ENDPOINT = "/studies"
PAGE_SIZE = 100
REQUEST_DELAY = 0.5          # seconds between pages
MAX_RETRIES = 3
BACKOFF_BASE = 2.0           # seconds; delay = BACKOFF_BASE ** attempt

FIELDS = ",".join([
    "NCTId",
    "BriefTitle",
    "OfficialTitle",
    "BriefSummary",
    "OverallStatus",
    "Phase",
    "EnrollmentInfo",
    "StartDate",
    "PrimaryCompletionDate",
    "LeadSponsorName",
    "Condition",
    "InterventionName",
    "InterventionType",
    "EligibilityCriteria",
    "Sex",
    "MinimumAge",
    "MaximumAge",
    "LocationCity",
    "LocationState",
    "LocationCountry",
])

THERAPEUTIC_AREA_KEYWORDS: dict[str, list[str]] = {
    "oncology": [
        "cancer", "carcinoma", "tumor", "tumour", "neoplasm",
        "lymphoma", "leukemia", "leukaemia", "melanoma", "sarcoma",
        "myeloma", "glioma", "glioblastoma", "mesothelioma",
        "neuroblastoma",
    ],
    "cardiology": [
        "heart", "cardiac", "cardiovascular", "hypertension", "atrial",
        "coronary", "arrhythmia", "cardiomyopathy", "heart failure",
        "myocardial",
    ],
    "neurology": [
        "alzheimer", "parkinson", "epilepsy", "seizure",
        "multiple sclerosis", "neuropathy", "stroke", "dementia",
        "migraine", "als", "amyotrophic",
    ],
    "immunology": [
        "lupus", "rheumatoid", "psoriasis", "crohn", "colitis",
        "autoimmune", "inflammatory bowel",
    ],
    "endocrinology": [
        "diabetes", "thyroid", "obesity", "metabolic", "insulin",
    ],
    "infectious_disease": [
        "hiv", "hepatitis", "tuberculosis", "malaria", "covid",
        "influenza", "infection",
    ],
    "pulmonology": [
        "asthma", "copd", "pulmonary", "respiratory", "lung disease",
        "cystic fibrosis",
    ],
    "psychiatry": [
        "depression", "anxiety", "schizophrenia", "bipolar", "ptsd",
        "adhd", "ocd",
    ],
    "hematology": [
        "anemia", "hemophilia", "sickle cell", "thrombocytopenia",
        "myelodysplastic",
    ],
    "rare_disease": ["orphan", "rare disease"],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_age(age_str: str | None) -> int | None:
    """Convert '18 Years' / '6 Months' / 'N/A' / None → integer years or None."""
    if not age_str or age_str.strip().upper() in ("N/A", ""):
        return None
    m = re.search(r"(\d+)", age_str)
    if not m:
        return None
    value = int(m.group(1))
    if "month" in age_str.lower():
        value = value // 12
    return value


def _normalise_phase(phases: list[str] | str | None) -> str | None:
    """Convert ['PHASE2', 'PHASE3'] → 'Phase 2/Phase 3'."""
    if not phases:
        return None
    if isinstance(phases, str):
        phases = [phases]
    readable = []
    for p in phases:
        p = p.strip().upper()
        if p == "PHASE1":
            readable.append("Phase 1")
        elif p == "PHASE2":
            readable.append("Phase 2")
        elif p == "PHASE3":
            readable.append("Phase 3")
        elif p == "PHASE4":
            readable.append("Phase 4")
        elif p in ("NA", "N/A", "NOT_APPLICABLE"):
            readable.append("N/A")
        elif p == "EARLY_PHASE1":
            readable.append("Early Phase 1")
        else:
            readable.append(p.title())
    return "/".join(readable) if readable else None


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def parse_trial_record(raw: dict) -> dict:
    """Extract and normalise a single trial from a V2 API study object."""
    proto = raw.get("protocolSection") or {}

    id_mod = proto.get("identificationModule") or {}
    desc_mod = proto.get("descriptionModule") or {}
    status_mod = proto.get("statusModule") or {}
    design_mod = proto.get("designModule") or {}
    sponsor_mod = proto.get("sponsorCollaboratorsModule") or {}
    cond_mod = proto.get("conditionsModule") or {}
    arms_mod = proto.get("armsInterventionsModule") or {}
    elig_mod = proto.get("eligibilityModule") or {}
    loc_mod = proto.get("contactsLocationsModule") or {}

    # Interventions
    raw_interventions = arms_mod.get("interventions") or []
    interventions = [
        {
            "name": iv.get("name"),
            "type": iv.get("type"),
        }
        for iv in raw_interventions
    ]

    # Locations
    raw_locations = loc_mod.get("locations") or []
    locations = [
        {
            "city": loc.get("city"),
            "state": loc.get("state"),
            "country": loc.get("country"),
        }
        for loc in raw_locations
    ]

    # Enrollment
    enroll_info = design_mod.get("enrollmentInfo") or {}
    enrollment = enroll_info.get("count")
    if enrollment is not None:
        try:
            enrollment = int(enrollment)
        except (ValueError, TypeError):
            enrollment = None

    # Phases
    phase_raw = design_mod.get("phases")
    phase = _normalise_phase(phase_raw)

    # Sponsor
    lead_sponsor = sponsor_mod.get("leadSponsor") or {}

    return {
        "nct_id": id_mod.get("nctId"),
        "title": id_mod.get("briefTitle"),
        "official_title": id_mod.get("officialTitle"),
        "brief_summary": desc_mod.get("briefSummary"),
        "status": status_mod.get("overallStatus"),
        "phase": phase,
        "enrollment": enrollment,
        "start_date": (status_mod.get("startDateStruct") or {}).get("date"),
        "primary_completion_date": (
            status_mod.get("primaryCompletionDateStruct") or {}
        ).get("date"),
        "sponsor": lead_sponsor.get("name"),
        "conditions": cond_mod.get("conditions") or [],
        "interventions": interventions,
        "eligibility_criteria": elig_mod.get("eligibilityCriteria"),
        "gender": elig_mod.get("sex"),
        "min_age": _parse_age(elig_mod.get("minimumAge")),
        "max_age": _parse_age(elig_mod.get("maximumAge")),
        "locations": locations,
    }


def classify_therapeutic_area(conditions: list[str]) -> str:
    """Return the first matching therapeutic area keyword group, or 'other'."""
    combined = " ".join(conditions).lower()
    for area, keywords in THERAPEUTIC_AREA_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                return area
    return "other"


def fetch_all_trials(output_dir: Path | None = None) -> Path:
    """
    Fetch every RECRUITING / ACTIVE_NOT_RECRUITING trial from the V2 API.

    Pages are saved as  output_dir/trials_page_{N}.json while fetching,
    then merged into output_dir/all_trials.json.

    Returns the path to all_trials.json.
    """
    settings = get_settings()
    base_url = settings.pipeline.clinicaltrials_base_url

    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
    output_dir.mkdir(parents=True, exist_ok=True)

    all_studies: list[dict] = []
    page_num = 0
    next_page_token: str | None = None

    params: dict[str, Any] = {
        "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING",
        "pageSize": PAGE_SIZE,
        "fields": FIELDS,
        "format": "json",
    }

    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    while True:
        if next_page_token:
            params["pageToken"] = next_page_token
        elif "pageToken" in params:
            del params["pageToken"]

        # Retry loop
        for attempt in range(MAX_RETRIES):
            try:
                response = session.get(
                    f"{base_url}{CLINICAL_TRIALS_ENDPOINT}",
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
                    "Request error on attempt %d/%d: %s — retrying in %.1fs",
                    attempt + 1, MAX_RETRIES, exc, wait,
                )
                time.sleep(wait)

        studies = data.get("studies") or []
        page_num += 1
        all_studies.extend(studies)

        # Save this page
        page_path = output_dir / f"trials_page_{page_num}.json"
        page_path.write_text(json.dumps(studies, indent=2), encoding="utf-8")

        logger.info(
            "Fetched page %d, total trials so far: %d",
            page_num,
            len(all_studies),
        )

        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

        time.sleep(REQUEST_DELAY)

    session.close()
    merged_path = output_dir / "all_trials.json"
    merged_path.write_text(json.dumps(all_studies, indent=2), encoding="utf-8")
    logger.info(
        "All pages fetched. Total: %d trials saved to %s",
        len(all_studies),
        merged_path,
    )
    return merged_path


def process_all_trials(
    raw_path: Path | None = None,
    output_path: Path | None = None,
) -> list[dict]:
    """
    Load all_trials.json, parse + classify each record, save to trials_processed.json.

    Returns the list of processed trial dicts.
    """
    project_root = Path(__file__).resolve().parent.parent

    if raw_path is None:
        raw_path = project_root / "data" / "raw" / "all_trials.json"
    if output_path is None:
        output_path = project_root / "data" / "processed" / "trials_processed.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Loading raw trials from %s", raw_path)
    raw_studies = json.loads(raw_path.read_text(encoding="utf-8"))

    processed: list[dict] = []
    for raw in raw_studies:
        trial = parse_trial_record(raw)
        trial["therapeutic_area"] = classify_therapeutic_area(trial["conditions"])
        processed.append(trial)

    output_path.write_text(json.dumps(processed, indent=2), encoding="utf-8")
    logger.info("Saved %d processed trials to %s", len(processed), output_path)

    # ---------- summary stats ----------
    from collections import Counter

    area_counts = Counter(t["therapeutic_area"] for t in processed)
    phase_counts = Counter(t.get("phase") or "Unknown" for t in processed)
    elig_lengths = [
        len(t["eligibility_criteria"])
        for t in processed
        if t.get("eligibility_criteria")
    ]
    missing_elig = sum(1 for t in processed if not t.get("eligibility_criteria"))

    print(f"\n{'='*60}")
    print(f"Total trials processed : {len(processed):,}")
    print(f"\nTrials per therapeutic area:")
    for area, count in area_counts.most_common():
        print(f"  {area:<25} {count:>6,}")
    print(f"\nPhase distribution:")
    for phase, count in phase_counts.most_common():
        print(f"  {phase:<25} {count:>6,}")
    if elig_lengths:
        avg_len = sum(elig_lengths) / len(elig_lengths)
        print(f"\nAvg eligibility text length : {avg_len:,.0f} chars")
    print(f"Trials missing eligibility  : {missing_elig:,}")
    print(f"{'='*60}\n")

    return processed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("Phase 2: ClinicalTrials.gov ingestion pipeline")
    print("Step 1/2 — Fetching all trials (this will take ~10-15 minutes)...")
    t0 = time.monotonic()
    fetch_all_trials()
    elapsed = time.monotonic() - t0
    print(f"Fetch complete in {elapsed/60:.1f} min")

    print("\nStep 2/2 — Parsing, classifying, and saving...")
    process_all_trials()
    print("Pipeline complete.")
