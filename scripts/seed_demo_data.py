"""
Demo data seeder — writes 30 curated clinical trials directly into Neo4j.

No Snowflake or external data required. Run this to get a working demo
in under 60 seconds.

Usage:
    python scripts/seed_demo_data.py
    python scripts/seed_demo_data.py --clear   # wipe graph first
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Make project root importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neo4j import AsyncGraphDatabase  # type: ignore[import-untyped]

from config.settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("seed")

# ---------------------------------------------------------------------------
# SNOMED concepts  {concept_id, term, semantic_tag}
# ---------------------------------------------------------------------------

SNOMED_CONCEPTS = [
    # Oncology parents
    {"concept_id": "363346000", "term": "Malignant neoplastic disease", "semantic_tag": "disorder"},
    # Breast
    {"concept_id": "254837009", "term": "Breast cancer", "semantic_tag": "disorder"},
    {"concept_id": "427685000", "term": "HER2-positive breast cancer", "semantic_tag": "disorder"},
    {"concept_id": "408643008", "term": "Metastatic breast cancer", "semantic_tag": "disorder"},
    {"concept_id": "31509003",  "term": "Triple-negative breast cancer", "semantic_tag": "disorder"},
    {"concept_id": "396533008", "term": "ER-positive breast cancer", "semantic_tag": "disorder"},
    # Lung
    {"concept_id": "363358000", "term": "Lung cancer", "semantic_tag": "disorder"},
    {"concept_id": "254637007", "term": "Non-small cell lung cancer", "semantic_tag": "disorder"},
    {"concept_id": "423050000", "term": "EGFR-positive non-small cell lung cancer", "semantic_tag": "disorder"},
    # Colorectal
    {"concept_id": "363406005", "term": "Colorectal cancer", "semantic_tag": "disorder"},
    {"concept_id": "94260004",  "term": "Metastatic colorectal cancer", "semantic_tag": "disorder"},
    # Other oncology
    {"concept_id": "372244006", "term": "Melanoma", "semantic_tag": "disorder"},
    {"concept_id": "93143009",  "term": "Leukemia", "semantic_tag": "disorder"},
    {"concept_id": "399068003", "term": "Prostate cancer", "semantic_tag": "disorder"},
    # Cardiology
    {"concept_id": "84114007",  "term": "Heart failure", "semantic_tag": "disorder"},
    {"concept_id": "49436004",  "term": "Atrial fibrillation", "semantic_tag": "disorder"},
    {"concept_id": "38341003",  "term": "Hypertension", "semantic_tag": "disorder"},
    # Neurology
    {"concept_id": "49049000",  "term": "Parkinson disease", "semantic_tag": "disorder"},
    {"concept_id": "26929004",  "term": "Alzheimer disease", "semantic_tag": "disorder"},
    {"concept_id": "24700007",  "term": "Multiple sclerosis", "semantic_tag": "disorder"},
    # Other
    {"concept_id": "44054006",  "term": "Type 2 diabetes mellitus", "semantic_tag": "disorder"},
]

# IS_A edges  (child_id, parent_id)
SNOMED_IS_A = [
    ("254837009", "363346000"),  # Breast cancer IS_A Malignant neoplasm
    ("427685000", "254837009"),  # HER2+ breast cancer IS_A Breast cancer
    ("408643008", "254837009"),  # Metastatic breast cancer IS_A Breast cancer
    ("31509003",  "254837009"),  # Triple-negative IS_A Breast cancer
    ("396533008", "254837009"),  # ER+ IS_A Breast cancer
    ("363358000", "363346000"),  # Lung cancer IS_A Malignant neoplasm
    ("254637007", "363358000"),  # NSCLC IS_A Lung cancer
    ("423050000", "254637007"),  # EGFR+ NSCLC IS_A NSCLC
    ("363406005", "363346000"),  # Colorectal cancer IS_A Malignant neoplasm
    ("94260004",  "363406005"),  # Metastatic CRC IS_A Colorectal cancer
    ("372244006", "363346000"),  # Melanoma IS_A Malignant neoplasm
    ("93143009",  "363346000"),  # Leukemia IS_A Malignant neoplasm
    ("399068003", "363346000"),  # Prostate cancer IS_A Malignant neoplasm
]

# ---------------------------------------------------------------------------
# RxNorm drug concepts  {rxcui, name, tty}
# ---------------------------------------------------------------------------

RXNORM_CONCEPTS = [
    {"rxcui": "224905",   "name": "trastuzumab",                  "tty": "IN"},
    {"rxcui": "RX_HCP",   "name": "Herceptin",                    "tty": "BN"},
    {"rxcui": "1298065",  "name": "pertuzumab",                   "tty": "IN"},
    {"rxcui": "1369443",  "name": "ado-trastuzumab emtansine",    "tty": "IN"},
    {"rxcui": "1643507",  "name": "pembrolizumab",                "tty": "IN"},
    {"rxcui": "1547545",  "name": "nivolumab",                    "tty": "IN"},
    {"rxcui": "1859164",  "name": "atezolizumab",                 "tty": "IN"},
    {"rxcui": "151399",   "name": "oxaliplatin",                  "tty": "IN"},
    {"rxcui": "4492",     "name": "fluorouracil",                 "tty": "IN"},
    {"rxcui": "51499",    "name": "irinotecan",                   "tty": "IN"},
    {"rxcui": "352962",   "name": "erlotinib",                    "tty": "IN"},
    {"rxcui": "352707",   "name": "gefitinib",                    "tty": "IN"},
    {"rxcui": "1860477",  "name": "osimertinib",                  "tty": "IN"},
    {"rxcui": "41493",    "name": "tamoxifen",                    "tty": "IN"},
    {"rxcui": "72251",    "name": "letrozole",                    "tty": "IN"},
]

# RxNorm relationships  (source_rxcui, rel, target_rxcui)
RXNORM_RELS = [
    ("RX_HCP", "TRADENAME_OF", "224905"),   # Herceptin TRADENAME_OF trastuzumab
]

# ---------------------------------------------------------------------------
# Biomarker nodes
# ---------------------------------------------------------------------------

BIOMARKERS = ["HER2", "EGFR", "PD-L1", "BRCA1", "BRCA2", "ER", "PR", "ALK", "KRAS"]

# ---------------------------------------------------------------------------
# Trial data  — 30 entries
# Each entry:
#   nct_id, title, status, phase, sponsor, enrollment,
#   min_age, max_age, gender, therapeutic_area, url,
#   brief_summary,
#   conditions        : list of SNOMED concept_ids  (→ STUDIES_CONDITION via Condition nodes)
#   interventions     : list of {name, type}
#   inclusion_criteria: list of criterion dicts
#   exclusion_criteria: list of criterion dicts
#
# criterion dict keys:
#   raw_text, category
#   requires_conditions   : [concept_id, ...]
#   requires_biomarkers   : [{name, status}, ...]
#   requires_prior_drugs  : [rxcui, ...]
#   excludes_conditions   : [concept_id, ...]
#   excludes_biomarkers   : [{name, status}, ...]
#   excludes_prior_drugs  : [rxcui, ...]
# ---------------------------------------------------------------------------

TRIALS = [
    # ------------------------------------------------------------------ #
    #  ONCOLOGY — BREAST                                                  #
    # ------------------------------------------------------------------ #
    {
        "nct_id": "NCT00000001",
        "title": "Trastuzumab + Pertuzumab in HER2-Positive Early Breast Cancer",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Genentech",
        "enrollment": 600,
        "min_age": 18,
        "max_age": 75,
        "gender": "Female",
        "therapeutic_area": "oncology",
        "url": "https://clinicaltrials.gov/study/NCT00000001",
        "brief_summary": "Evaluates dual HER2 blockade with trastuzumab and pertuzumab as neoadjuvant therapy.",
        "conditions": ["427685000"],   # HER2+ breast cancer
        "interventions": [
            {"name": "trastuzumab", "type": "Drug"},
            {"name": "pertuzumab",  "type": "Drug"},
        ],
        "inclusion_criteria": [
            {
                "raw_text": "Histologically confirmed HER2-positive breast cancer",
                "category": "condition",
                "requires_conditions": ["427685000"],
            },
            {
                "raw_text": "HER2 overexpression (IHC 3+ or FISH amplified)",
                "category": "biomarker",
                "requires_biomarkers": [{"name": "HER2", "status": "positive"}],
            },
            {
                "raw_text": "Age 18-75 years",
                "category": "age",
            },
        ],
        "exclusion_criteria": [
            {
                "raw_text": "Prior treatment with trastuzumab or pertuzumab",
                "category": "prior_therapy",
                "excludes_prior_drugs": ["224905", "1298065"],
            },
            {
                "raw_text": "Known EGFR-activating mutation",
                "category": "biomarker",
                "excludes_biomarkers": [{"name": "EGFR", "status": "positive"}],
            },
        ],
    },
    {
        "nct_id": "NCT00000002",
        "title": "T-DM1 in HER2+ Metastatic Breast Cancer After Trastuzumab",
        "status": "RECRUITING",
        "phase": "Phase 2",
        "sponsor": "Roche",
        "enrollment": 200,
        "min_age": 18,
        "max_age": None,
        "gender": "Female",
        "therapeutic_area": "oncology",
        "url": "https://clinicaltrials.gov/study/NCT00000002",
        "brief_summary": "T-DM1 for patients with HER2+ metastatic breast cancer who progressed on trastuzumab.",
        "conditions": ["408643008"],   # Metastatic breast cancer
        "interventions": [{"name": "ado-trastuzumab emtansine", "type": "Drug"}],
        "inclusion_criteria": [
            {
                "raw_text": "HER2-positive metastatic breast cancer",
                "category": "condition",
                "requires_conditions": ["408643008"],
            },
            {
                "raw_text": "HER2 positive (IHC 3+)",
                "category": "biomarker",
                "requires_biomarkers": [{"name": "HER2", "status": "positive"}],
            },
            {
                "raw_text": "Prior trastuzumab-based therapy",
                "category": "prior_therapy",
                "requires_prior_drugs": ["224905"],
            },
        ],
        "exclusion_criteria": [
            {
                "raw_text": "Prior T-DM1 therapy",
                "category": "prior_therapy",
                "excludes_prior_drugs": ["1369443"],
            },
        ],
    },
    {
        "nct_id": "NCT00000003",
        "title": "Letrozole vs Tamoxifen in ER-Positive Early Breast Cancer",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Novartis",
        "enrollment": 800,
        "min_age": 18,
        "max_age": 80,
        "gender": "Female",
        "therapeutic_area": "oncology",
        "url": "https://clinicaltrials.gov/study/NCT00000003",
        "brief_summary": "Adjuvant endocrine therapy comparison in postmenopausal ER+ breast cancer.",
        "conditions": ["396533008"],   # ER+ breast cancer
        "interventions": [
            {"name": "letrozole",  "type": "Drug"},
            {"name": "tamoxifen",  "type": "Drug"},
        ],
        "inclusion_criteria": [
            {
                "raw_text": "ER-positive breast cancer",
                "category": "condition",
                "requires_conditions": ["396533008"],
            },
            {
                "raw_text": "ER positive (≥1% staining)",
                "category": "biomarker",
                "requires_biomarkers": [{"name": "ER", "status": "positive"}],
            },
        ],
        "exclusion_criteria": [
            {
                "raw_text": "HER2-positive disease",
                "category": "biomarker",
                "excludes_biomarkers": [{"name": "HER2", "status": "positive"}],
            },
        ],
    },
    {
        "nct_id": "NCT00000004",
        "title": "Pembrolizumab in Triple-Negative Breast Cancer (PD-L1 Selected)",
        "status": "RECRUITING",
        "phase": "Phase 2",
        "sponsor": "Merck",
        "enrollment": 150,
        "min_age": 18,
        "max_age": None,
        "gender": "Female",
        "therapeutic_area": "oncology",
        "url": "https://clinicaltrials.gov/study/NCT00000004",
        "brief_summary": "Pembrolizumab monotherapy in PD-L1-positive TNBC.",
        "conditions": ["31509003"],   # Triple-negative breast cancer
        "interventions": [{"name": "pembrolizumab", "type": "Biological"}],
        "inclusion_criteria": [
            {
                "raw_text": "Triple-negative breast cancer (ER-, PR-, HER2-)",
                "category": "condition",
                "requires_conditions": ["31509003"],
            },
            {
                "raw_text": "PD-L1 positive (CPS ≥ 10)",
                "category": "biomarker",
                "requires_biomarkers": [{"name": "PD-L1", "status": "positive"}],
            },
        ],
        "exclusion_criteria": [
            {
                "raw_text": "HER2-positive disease",
                "category": "biomarker",
                "excludes_biomarkers": [{"name": "HER2", "status": "positive"}],
            },
        ],
    },
    {
        "nct_id": "NCT00000005",
        "title": "Olaparib in BRCA1/2-Mutated Breast Cancer",
        "status": "ACTIVE_NOT_RECRUITING",
        "phase": "Phase 2",
        "sponsor": "AstraZeneca",
        "enrollment": 300,
        "min_age": 18,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "oncology",
        "url": "https://clinicaltrials.gov/study/NCT00000005",
        "brief_summary": "PARP inhibitor olaparib in germline BRCA1/2-mutated advanced breast cancer.",
        "conditions": ["254837009"],   # Breast cancer
        "interventions": [{"name": "olaparib", "type": "Drug"}],
        "inclusion_criteria": [
            {
                "raw_text": "Histologically confirmed breast cancer",
                "category": "condition",
                "requires_conditions": ["254837009"],
            },
            {
                "raw_text": "Germline BRCA1 or BRCA2 mutation",
                "category": "biomarker",
                "requires_biomarkers": [
                    {"name": "BRCA1", "status": "positive"},
                ],
            },
        ],
        "exclusion_criteria": [],
    },
    # ------------------------------------------------------------------ #
    #  ONCOLOGY — LUNG                                                    #
    # ------------------------------------------------------------------ #
    {
        "nct_id": "NCT00000006",
        "title": "Pembrolizumab First-Line in PD-L1 High NSCLC",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Merck",
        "enrollment": 500,
        "min_age": 18,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "oncology",
        "url": "https://clinicaltrials.gov/study/NCT00000006",
        "brief_summary": "First-line pembrolizumab in advanced NSCLC with PD-L1 TPS ≥ 50%.",
        "conditions": ["254637007"],   # NSCLC
        "interventions": [{"name": "pembrolizumab", "type": "Biological"}],
        "inclusion_criteria": [
            {
                "raw_text": "Stage IV NSCLC",
                "category": "condition",
                "requires_conditions": ["254637007"],
            },
            {
                "raw_text": "PD-L1 TPS ≥ 50%",
                "category": "biomarker",
                "requires_biomarkers": [{"name": "PD-L1", "status": "positive"}],
            },
        ],
        "exclusion_criteria": [
            {
                "raw_text": "Known EGFR sensitizing mutation",
                "category": "biomarker",
                "excludes_biomarkers": [{"name": "EGFR", "status": "positive"}],
            },
            {
                "raw_text": "ALK rearrangement",
                "category": "biomarker",
                "excludes_biomarkers": [{"name": "ALK", "status": "positive"}],
            },
        ],
    },
    {
        "nct_id": "NCT00000007",
        "title": "Osimertinib in EGFR-Mutant Advanced NSCLC",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "AstraZeneca",
        "enrollment": 556,
        "min_age": 18,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "oncology",
        "url": "https://clinicaltrials.gov/study/NCT00000007",
        "brief_summary": "Osimertinib vs platinum-based doublet in EGFR exon 19/21 mutant NSCLC.",
        "conditions": ["423050000"],   # EGFR+ NSCLC
        "interventions": [{"name": "osimertinib", "type": "Drug"}],
        "inclusion_criteria": [
            {
                "raw_text": "EGFR-mutant NSCLC (exon 19 deletion or L858R)",
                "category": "condition",
                "requires_conditions": ["254637007"],
            },
            {
                "raw_text": "EGFR activating mutation confirmed",
                "category": "biomarker",
                "requires_biomarkers": [{"name": "EGFR", "status": "positive"}],
            },
        ],
        "exclusion_criteria": [
            {
                "raw_text": "Prior EGFR-TKI therapy (erlotinib, gefitinib, or afatinib)",
                "category": "prior_therapy",
                "excludes_prior_drugs": ["352962", "352707"],
            },
        ],
    },
    {
        "nct_id": "NCT00000008",
        "title": "Crizotinib in ALK-Rearranged Advanced NSCLC",
        "status": "RECRUITING",
        "phase": "Phase 2",
        "sponsor": "Pfizer",
        "enrollment": 160,
        "min_age": 18,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "oncology",
        "url": "https://clinicaltrials.gov/study/NCT00000008",
        "brief_summary": "Crizotinib in treatment-naive ALK-positive NSCLC.",
        "conditions": ["254637007"],
        "interventions": [{"name": "crizotinib", "type": "Drug"}],
        "inclusion_criteria": [
            {
                "raw_text": "Advanced or metastatic NSCLC",
                "category": "condition",
                "requires_conditions": ["254637007"],
            },
            {
                "raw_text": "ALK rearrangement (FISH or IHC)",
                "category": "biomarker",
                "requires_biomarkers": [{"name": "ALK", "status": "positive"}],
            },
        ],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000009",
        "title": "Nivolumab Second-Line in Advanced NSCLC",
        "status": "RECRUITING",
        "phase": "Phase 2",
        "sponsor": "Bristol Myers Squibb",
        "enrollment": 272,
        "min_age": 18,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "oncology",
        "url": "https://clinicaltrials.gov/study/NCT00000009",
        "brief_summary": "Nivolumab after platinum-based chemotherapy in advanced NSCLC.",
        "conditions": ["254637007"],
        "interventions": [{"name": "nivolumab", "type": "Biological"}],
        "inclusion_criteria": [
            {
                "raw_text": "Advanced NSCLC with at least one prior line of platinum-based chemotherapy",
                "category": "condition",
                "requires_conditions": ["254637007"],
            },
        ],
        "exclusion_criteria": [],
    },
    # ------------------------------------------------------------------ #
    #  ONCOLOGY — COLORECTAL                                              #
    # ------------------------------------------------------------------ #
    {
        "nct_id": "NCT00000010",
        "title": "FOLFOX + Bevacizumab in KRAS Wild-Type Metastatic CRC",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Pfizer",
        "enrollment": 450,
        "min_age": 18,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "oncology",
        "url": "https://clinicaltrials.gov/study/NCT00000010",
        "brief_summary": "First-line FOLFOX + bevacizumab in KRAS wild-type metastatic CRC.",
        "conditions": ["94260004"],   # Metastatic CRC
        "interventions": [
            {"name": "oxaliplatin",   "type": "Drug"},
            {"name": "fluorouracil",  "type": "Drug"},
        ],
        "inclusion_criteria": [
            {
                "raw_text": "Metastatic colorectal cancer",
                "category": "condition",
                "requires_conditions": ["94260004"],
            },
            {
                "raw_text": "KRAS wild-type (codon 12/13)",
                "category": "biomarker",
                "requires_biomarkers": [{"name": "KRAS", "status": "negative"}],
            },
        ],
        "exclusion_criteria": [
            {
                "raw_text": "Prior oxaliplatin-based therapy",
                "category": "prior_therapy",
                "excludes_prior_drugs": ["151399"],
            },
        ],
    },
    {
        "nct_id": "NCT00000011",
        "title": "FOLFIRI Second-Line in Metastatic Colorectal Cancer",
        "status": "RECRUITING",
        "phase": "Phase 2",
        "sponsor": "Sanofi",
        "enrollment": 220,
        "min_age": 18,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "oncology",
        "url": "https://clinicaltrials.gov/study/NCT00000011",
        "brief_summary": "FOLFIRI in metastatic CRC previously treated with oxaliplatin.",
        "conditions": ["94260004"],
        "interventions": [
            {"name": "irinotecan",   "type": "Drug"},
            {"name": "fluorouracil", "type": "Drug"},
        ],
        "inclusion_criteria": [
            {
                "raw_text": "Metastatic colorectal cancer",
                "category": "condition",
                "requires_conditions": ["94260004"],
            },
            {
                "raw_text": "Prior oxaliplatin-based therapy required",
                "category": "prior_therapy",
                "requires_prior_drugs": ["151399"],
            },
        ],
        "exclusion_criteria": [
            {
                "raw_text": "Prior irinotecan therapy",
                "category": "prior_therapy",
                "excludes_prior_drugs": ["51499"],
            },
        ],
    },
    # ------------------------------------------------------------------ #
    #  ONCOLOGY — MELANOMA / LEUKEMIA / PROSTATE                         #
    # ------------------------------------------------------------------ #
    {
        "nct_id": "NCT00000012",
        "title": "Pembrolizumab + Ipilimumab in Advanced Melanoma",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Merck",
        "enrollment": 600,
        "min_age": 18,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "oncology",
        "url": "https://clinicaltrials.gov/study/NCT00000012",
        "brief_summary": "Dual checkpoint blockade in treatment-naive advanced melanoma.",
        "conditions": ["372244006"],
        "interventions": [{"name": "pembrolizumab", "type": "Biological"}],
        "inclusion_criteria": [
            {
                "raw_text": "Histologically confirmed unresectable or metastatic melanoma",
                "category": "condition",
                "requires_conditions": ["372244006"],
            },
            {
                "raw_text": "PD-L1 expression positive",
                "category": "biomarker",
                "requires_biomarkers": [{"name": "PD-L1", "status": "positive"}],
            },
        ],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000013",
        "title": "Nivolumab Monotherapy in Previously Treated Melanoma",
        "status": "ACTIVE_NOT_RECRUITING",
        "phase": "Phase 2",
        "sponsor": "Bristol Myers Squibb",
        "enrollment": 120,
        "min_age": 18,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "oncology",
        "url": "https://clinicaltrials.gov/study/NCT00000013",
        "brief_summary": "Nivolumab in ipilimumab-refractory advanced melanoma.",
        "conditions": ["372244006"],
        "interventions": [{"name": "nivolumab", "type": "Biological"}],
        "inclusion_criteria": [
            {
                "raw_text": "Advanced or metastatic melanoma",
                "category": "condition",
                "requires_conditions": ["372244006"],
            },
        ],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000014",
        "title": "Venetoclax + Azacitidine in Acute Myeloid Leukemia",
        "status": "RECRUITING",
        "phase": "Phase 2",
        "sponsor": "AbbVie",
        "enrollment": 430,
        "min_age": 18,
        "max_age": 75,
        "gender": "All",
        "therapeutic_area": "oncology",
        "url": "https://clinicaltrials.gov/study/NCT00000014",
        "brief_summary": "Venetoclax + azacitidine in newly diagnosed AML ineligible for intensive chemotherapy.",
        "conditions": ["93143009"],
        "interventions": [{"name": "venetoclax", "type": "Drug"}],
        "inclusion_criteria": [
            {
                "raw_text": "Newly diagnosed AML (leukemia)",
                "category": "condition",
                "requires_conditions": ["93143009"],
            },
        ],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000015",
        "title": "Enzalutamide in Metastatic Castration-Resistant Prostate Cancer",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Astellas",
        "enrollment": 870,
        "min_age": 18,
        "max_age": None,
        "gender": "Male",
        "therapeutic_area": "oncology",
        "url": "https://clinicaltrials.gov/study/NCT00000015",
        "brief_summary": "Enzalutamide vs placebo in mCRPC post-docetaxel.",
        "conditions": ["399068003"],
        "interventions": [{"name": "enzalutamide", "type": "Drug"}],
        "inclusion_criteria": [
            {
                "raw_text": "Metastatic castration-resistant prostate cancer",
                "category": "condition",
                "requires_conditions": ["399068003"],
            },
        ],
        "exclusion_criteria": [],
    },
    # ------------------------------------------------------------------ #
    #  CARDIOLOGY (5 trials)                                              #
    # ------------------------------------------------------------------ #
    {
        "nct_id": "NCT00000016",
        "title": "Sacubitril/Valsartan in Heart Failure with Reduced Ejection Fraction",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Novartis",
        "enrollment": 8000,
        "min_age": 18,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "cardiology",
        "url": "https://clinicaltrials.gov/study/NCT00000016",
        "brief_summary": "Sacubitril/valsartan vs enalapril in HFrEF (LVEF ≤40%).",
        "conditions": ["84114007"],
        "interventions": [{"name": "sacubitril/valsartan", "type": "Drug"}],
        "inclusion_criteria": [
            {
                "raw_text": "Symptomatic heart failure (NYHA II-IV)",
                "category": "condition",
                "requires_conditions": ["84114007"],
            },
        ],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000017",
        "title": "Apixaban in Atrial Fibrillation for Stroke Prevention",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Bristol Myers Squibb",
        "enrollment": 18000,
        "min_age": 18,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "cardiology",
        "url": "https://clinicaltrials.gov/study/NCT00000017",
        "brief_summary": "Apixaban vs warfarin for stroke prevention in non-valvular AF.",
        "conditions": ["49436004"],
        "interventions": [{"name": "apixaban", "type": "Drug"}],
        "inclusion_criteria": [
            {
                "raw_text": "Non-valvular atrial fibrillation",
                "category": "condition",
                "requires_conditions": ["49436004"],
            },
        ],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000018",
        "title": "Canagliflozin in Hypertension and Type 2 Diabetes",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Janssen",
        "enrollment": 10142,
        "min_age": 30,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "cardiology",
        "url": "https://clinicaltrials.gov/study/NCT00000018",
        "brief_summary": "Cardiovascular outcomes with canagliflozin in T2DM + hypertension.",
        "conditions": ["38341003", "44054006"],
        "interventions": [{"name": "canagliflozin", "type": "Drug"}],
        "inclusion_criteria": [
            {
                "raw_text": "Hypertension",
                "category": "condition",
                "requires_conditions": ["38341003"],
            },
        ],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000019",
        "title": "Dapagliflozin in Heart Failure with Preserved Ejection Fraction",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "AstraZeneca",
        "enrollment": 6263,
        "min_age": 40,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "cardiology",
        "url": "https://clinicaltrials.gov/study/NCT00000019",
        "brief_summary": "Dapagliflozin in HFpEF (LVEF ≥ 45%).",
        "conditions": ["84114007"],
        "interventions": [{"name": "dapagliflozin", "type": "Drug"}],
        "inclusion_criteria": [
            {
                "raw_text": "Heart failure with preserved ejection fraction",
                "category": "condition",
                "requires_conditions": ["84114007"],
            },
        ],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000020",
        "title": "Alirocumab in Coronary Artery Disease and LDL Management",
        "status": "ACTIVE_NOT_RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Sanofi",
        "enrollment": 18924,
        "min_age": 40,
        "max_age": 80,
        "gender": "All",
        "therapeutic_area": "cardiology",
        "url": "https://clinicaltrials.gov/study/NCT00000020",
        "brief_summary": "PCSK9 inhibitor alirocumab post-ACS in high LDL patients.",
        "conditions": ["84114007"],
        "interventions": [{"name": "alirocumab", "type": "Biological"}],
        "inclusion_criteria": [
            {
                "raw_text": "History of acute coronary syndrome",
                "category": "condition",
                "requires_conditions": ["84114007"],
            },
        ],
        "exclusion_criteria": [],
    },
    # ------------------------------------------------------------------ #
    #  NEUROLOGY (5 trials)                                               #
    # ------------------------------------------------------------------ #
    {
        "nct_id": "NCT00000021",
        "title": "Levodopa/Carbidopa Intestinal Gel in Advanced Parkinson Disease",
        "status": "RECRUITING",
        "phase": "Phase 2",
        "sponsor": "AbbVie",
        "enrollment": 354,
        "min_age": 30,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "neurology",
        "url": "https://clinicaltrials.gov/study/NCT00000021",
        "brief_summary": "Continuous infusion of levodopa-carbidopa in advanced PD.",
        "conditions": ["49049000"],
        "interventions": [{"name": "levodopa-carbidopa", "type": "Drug"}],
        "inclusion_criteria": [
            {
                "raw_text": "Diagnosis of idiopathic Parkinson disease",
                "category": "condition",
                "requires_conditions": ["49049000"],
            },
        ],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000022",
        "title": "Lecanemab in Early Alzheimer Disease",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Eisai",
        "enrollment": 1795,
        "min_age": 50,
        "max_age": 90,
        "gender": "All",
        "therapeutic_area": "neurology",
        "url": "https://clinicaltrials.gov/study/NCT00000022",
        "brief_summary": "Anti-amyloid antibody lecanemab in early Alzheimer disease.",
        "conditions": ["26929004"],
        "interventions": [{"name": "lecanemab", "type": "Biological"}],
        "inclusion_criteria": [
            {
                "raw_text": "Clinical diagnosis of Alzheimer disease",
                "category": "condition",
                "requires_conditions": ["26929004"],
            },
        ],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000023",
        "title": "Ocrelizumab in Relapsing Multiple Sclerosis",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Roche",
        "enrollment": 821,
        "min_age": 18,
        "max_age": 55,
        "gender": "All",
        "therapeutic_area": "neurology",
        "url": "https://clinicaltrials.gov/study/NCT00000023",
        "brief_summary": "Anti-CD20 therapy ocrelizumab in relapsing MS.",
        "conditions": ["24700007"],
        "interventions": [{"name": "ocrelizumab", "type": "Biological"}],
        "inclusion_criteria": [
            {
                "raw_text": "Relapsing-remitting multiple sclerosis",
                "category": "condition",
                "requires_conditions": ["24700007"],
            },
        ],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000024",
        "title": "Deep Brain Stimulation in Parkinson Disease Motor Fluctuations",
        "status": "ACTIVE_NOT_RECRUITING",
        "phase": "Phase 2",
        "sponsor": "Medtronic",
        "enrollment": 255,
        "min_age": 40,
        "max_age": 75,
        "gender": "All",
        "therapeutic_area": "neurology",
        "url": "https://clinicaltrials.gov/study/NCT00000024",
        "brief_summary": "Bilateral STN-DBS vs best medical therapy in advanced Parkinson disease.",
        "conditions": ["49049000"],
        "interventions": [{"name": "deep brain stimulation", "type": "Device"}],
        "inclusion_criteria": [
            {
                "raw_text": "Advanced Parkinson disease with motor fluctuations",
                "category": "condition",
                "requires_conditions": ["49049000"],
            },
        ],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000025",
        "title": "Erenumab in Chronic Migraine Prevention",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Amgen",
        "enrollment": 667,
        "min_age": 18,
        "max_age": 65,
        "gender": "All",
        "therapeutic_area": "neurology",
        "url": "https://clinicaltrials.gov/study/NCT00000025",
        "brief_summary": "CGRP antagonist erenumab in chronic migraine.",
        "conditions": ["49049000"],   # Reusing neurology parent for demo
        "interventions": [{"name": "erenumab", "type": "Biological"}],
        "inclusion_criteria": [],
        "exclusion_criteria": [],
    },
    # ------------------------------------------------------------------ #
    #  OTHER — ENDOCRINOLOGY / IMMUNOLOGY / PULMONOLOGY                  #
    # ------------------------------------------------------------------ #
    {
        "nct_id": "NCT00000026",
        "title": "Semaglutide for Glycemic Control in Type 2 Diabetes",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Novo Nordisk",
        "enrollment": 3183,
        "min_age": 18,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "endocrinology",
        "url": "https://clinicaltrials.gov/study/NCT00000026",
        "brief_summary": "GLP-1 receptor agonist semaglutide in type 2 diabetes on metformin.",
        "conditions": ["44054006"],
        "interventions": [{"name": "semaglutide", "type": "Drug"}],
        "inclusion_criteria": [
            {
                "raw_text": "Type 2 diabetes mellitus",
                "category": "condition",
                "requires_conditions": ["44054006"],
            },
        ],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000027",
        "title": "Baricitinib in Rheumatoid Arthritis Inadequate Response to MTX",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Eli Lilly",
        "enrollment": 527,
        "min_age": 18,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "immunology",
        "url": "https://clinicaltrials.gov/study/NCT00000027",
        "brief_summary": "JAK1/2 inhibitor baricitinib in active RA on methotrexate.",
        "conditions": ["38341003"],   # demo — would map to RA SNOMED in full pipeline
        "interventions": [{"name": "baricitinib", "type": "Drug"}],
        "inclusion_criteria": [],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000028",
        "title": "Nirmatrelvir/Ritonavir in High-Risk COVID-19 Outpatients",
        "status": "ACTIVE_NOT_RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Pfizer",
        "enrollment": 2246,
        "min_age": 18,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "infectious_disease",
        "url": "https://clinicaltrials.gov/study/NCT00000028",
        "brief_summary": "Oral antiviral nirmatrelvir/ritonavir (Paxlovid) vs placebo in COVID-19.",
        "conditions": ["44054006"],   # demo parent — would be COVID concept in full pipeline
        "interventions": [{"name": "nirmatrelvir/ritonavir", "type": "Drug"}],
        "inclusion_criteria": [],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000029",
        "title": "Tirzepatide for Weight Management in Obesity",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Eli Lilly",
        "enrollment": 2539,
        "min_age": 18,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "endocrinology",
        "url": "https://clinicaltrials.gov/study/NCT00000029",
        "brief_summary": "GIP/GLP-1 dual agonist tirzepatide for obesity (BMI ≥30).",
        "conditions": ["44054006"],
        "interventions": [{"name": "tirzepatide", "type": "Drug"}],
        "inclusion_criteria": [],
        "exclusion_criteria": [],
    },
    {
        "nct_id": "NCT00000030",
        "title": "Dupilumab in Moderate-to-Severe Asthma",
        "status": "RECRUITING",
        "phase": "Phase 3",
        "sponsor": "Regeneron",
        "enrollment": 1902,
        "min_age": 12,
        "max_age": None,
        "gender": "All",
        "therapeutic_area": "pulmonology",
        "url": "https://clinicaltrials.gov/study/NCT00000030",
        "brief_summary": "IL-4/IL-13 dupilumab in uncontrolled persistent asthma.",
        "conditions": ["44054006"],   # demo parent
        "interventions": [{"name": "dupilumab", "type": "Biological"}],
        "inclusion_criteria": [],
        "exclusion_criteria": [],
    },
]


# ---------------------------------------------------------------------------
# Cypher helpers
# ---------------------------------------------------------------------------


async def _clear_demo_data(session) -> None:
    log.info("Clearing existing demo data…")
    await session.run("MATCH (n) WHERE n.nct_id STARTS WITH 'NCT000000' DETACH DELETE n")
    await session.run(
        "MATCH (s:SNOMEDConcept) WHERE s.concept_id IN $ids DETACH DELETE s",
        ids=[c["concept_id"] for c in SNOMED_CONCEPTS],
    )
    await session.run(
        "MATCH (r:RxNormConcept) WHERE r.rxcui IN $ids DETACH DELETE r",
        ids=[c["rxcui"] for c in RXNORM_CONCEPTS],
    )
    await session.run(
        "MATCH (b:Biomarker) WHERE b.name IN $names DETACH DELETE b",
        names=BIOMARKERS,
    )
    log.info("Demo data cleared.")


async def _create_constraints(session) -> None:
    constraints = [
        "CREATE CONSTRAINT trial_nct_id IF NOT EXISTS FOR (t:Trial) REQUIRE t.nct_id IS UNIQUE",
        "CREATE CONSTRAINT snomed_id IF NOT EXISTS FOR (s:SNOMEDConcept) REQUIRE s.concept_id IS UNIQUE",
        "CREATE CONSTRAINT rxnorm_id IF NOT EXISTS FOR (r:RxNormConcept) REQUIRE r.rxcui IS UNIQUE",
        "CREATE INDEX trial_status IF NOT EXISTS FOR (t:Trial) ON (t.status)",
        "CREATE INDEX trial_therapeutic_area IF NOT EXISTS FOR (t:Trial) ON (t.therapeutic_area)",
        "CREATE INDEX biomarker_name IF NOT EXISTS FOR (b:Biomarker) ON (b.normalized_name)",
    ]
    for cql in constraints:
        await session.run(cql)
    log.info("Constraints / indexes ensured.")


async def _create_snomed(session) -> None:
    log.info("Creating %d SNOMED concepts…", len(SNOMED_CONCEPTS))
    await session.run(
        """
        UNWIND $concepts AS c
        MERGE (s:SNOMEDConcept {concept_id: c.concept_id})
        SET s.term = c.term, s.semantic_tag = c.semantic_tag
        """,
        concepts=SNOMED_CONCEPTS,
    )
    log.info("Creating %d IS_A relationships…", len(SNOMED_IS_A))
    for child_id, parent_id in SNOMED_IS_A:
        await session.run(
            """
            MATCH (child:SNOMEDConcept {concept_id: $child_id})
            MATCH (parent:SNOMEDConcept {concept_id: $parent_id})
            MERGE (child)-[:IS_A]->(parent)
            """,
            child_id=child_id,
            parent_id=parent_id,
        )


async def _create_rxnorm(session) -> None:
    log.info("Creating %d RxNorm concepts…", len(RXNORM_CONCEPTS))
    await session.run(
        """
        UNWIND $concepts AS c
        MERGE (r:RxNormConcept {rxcui: c.rxcui})
        SET r.name = c.name, r.tty = c.tty
        """,
        concepts=RXNORM_CONCEPTS,
    )
    for src_rxcui, rel_type, tgt_rxcui in RXNORM_RELS:
        await session.run(
            f"""
            MATCH (a:RxNormConcept {{rxcui: $src}})
            MATCH (b:RxNormConcept {{rxcui: $tgt}})
            MERGE (a)-[:{rel_type}]->(b)
            """,
            src=src_rxcui,
            tgt=tgt_rxcui,
        )


async def _create_biomarkers(session) -> None:
    log.info("Creating %d Biomarker nodes…", len(BIOMARKERS))
    await session.run(
        """
        UNWIND $names AS name
        MERGE (b:Biomarker {name: name})
        SET b.normalized_name = toLower(name)
        """,
        names=BIOMARKERS,
    )


async def _create_trials(session) -> None:
    log.info("Creating %d Trial nodes with criteria…", len(TRIALS))
    for trial in TRIALS:
        # ---- Trial node ----
        await session.run(
            """
            MERGE (t:Trial {nct_id: $nct_id})
            SET t.title             = $title,
                t.status            = $status,
                t.phase             = $phase,
                t.sponsor           = $sponsor,
                t.enrollment        = $enrollment,
                t.min_age           = $min_age,
                t.max_age           = $max_age,
                t.gender            = $gender,
                t.therapeutic_area  = $therapeutic_area,
                t.url               = $url,
                t.brief_summary     = $brief_summary
            """,
            nct_id=trial["nct_id"],
            title=trial["title"],
            status=trial["status"],
            phase=trial["phase"],
            sponsor=trial["sponsor"],
            enrollment=trial["enrollment"],
            min_age=trial.get("min_age"),
            max_age=trial.get("max_age"),
            gender=trial["gender"],
            therapeutic_area=trial["therapeutic_area"],
            url=trial["url"],
            brief_summary=trial["brief_summary"],
        )

        # ---- Condition nodes → STUDIES_CONDITION ----
        for concept_id in trial.get("conditions", []):
            await session.run(
                """
                MATCH (t:Trial {nct_id: $nct_id})
                MATCH (sc:SNOMEDConcept {concept_id: $concept_id})
                MERGE (cond:Condition {normalized_name: toLower(sc.term)})
                SET cond.name = sc.term, cond.snomed_id = sc.concept_id
                MERGE (t)-[:STUDIES_CONDITION]->(cond)
                MERGE (cond)-[:MAPS_TO_SNOMED]->(sc)
                """,
                nct_id=trial["nct_id"],
                concept_id=concept_id,
            )

        # ---- Intervention nodes → USES_INTERVENTION ----
        for interv in trial.get("interventions", []):
            await session.run(
                """
                MATCH (t:Trial {nct_id: $nct_id})
                MERGE (i:Intervention {normalized_name: toLower($name)})
                SET i.name = $name, i.type = $type
                MERGE (t)-[:USES_INTERVENTION]->(i)
                """,
                nct_id=trial["nct_id"],
                name=interv["name"],
                type=interv["type"],
            )
            # Link drug to RxNorm if we have a matching concept
            await session.run(
                """
                MATCH (i:Intervention {normalized_name: $norm_name})
                OPTIONAL MATCH (rx:RxNormConcept)
                WHERE toLower(rx.name) = $norm_name
                FOREACH (_ IN CASE WHEN rx IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (i)-[:MAPS_TO_RXNORM]->(rx)
                )
                """,
                norm_name=interv["name"].lower(),
            )

        # ---- Inclusion criteria ----
        for idx, crit in enumerate(trial.get("inclusion_criteria", [])):
            crit_id = f"{trial['nct_id']}_inc_{idx}"
            await _create_criterion(session, trial["nct_id"], crit_id, "inclusion", crit)

        # ---- Exclusion criteria ----
        for idx, crit in enumerate(trial.get("exclusion_criteria", [])):
            crit_id = f"{trial['nct_id']}_exc_{idx}"
            await _create_criterion(session, trial["nct_id"], crit_id, "exclusion", crit)


async def _create_criterion(session, nct_id: str, crit_id: str, crit_type: str, crit: dict) -> None:
    await session.run(
        """
        MATCH (t:Trial {nct_id: $nct_id})
        MERGE (cr:Criterion {id: $crit_id})
        SET cr.type         = $crit_type,
            cr.raw_text     = $raw_text,
            cr.category     = $category,
            cr.parsing_status = 'parsed'
        MERGE (t)-[:HAS_CRITERION {type: $crit_type}]->(cr)
        """,
        nct_id=nct_id,
        crit_id=crit_id,
        crit_type=crit_type,
        raw_text=crit.get("raw_text", ""),
        category=crit.get("category", "other"),
    )

    # REQUIRES_CONDITION
    for concept_id in crit.get("requires_conditions", []):
        await session.run(
            """
            MATCH (cr:Criterion {id: $crit_id})
            MATCH (sc:SNOMEDConcept {concept_id: $concept_id})
            MERGE (cr)-[:REQUIRES_CONDITION]->(sc)
            """,
            crit_id=crit_id,
            concept_id=concept_id,
        )

    # EXCLUDES_CONDITION
    for concept_id in crit.get("excludes_conditions", []):
        await session.run(
            """
            MATCH (cr:Criterion {id: $crit_id})
            MATCH (sc:SNOMEDConcept {concept_id: $concept_id})
            MERGE (cr)-[:EXCLUDES_CONDITION]->(sc)
            """,
            crit_id=crit_id,
            concept_id=concept_id,
        )

    # REQUIRES_BIOMARKER
    for bm in crit.get("requires_biomarkers", []):
        await session.run(
            """
            MATCH (cr:Criterion {id: $crit_id})
            MATCH (b:Biomarker {normalized_name: $bm_name})
            MERGE (cr)-[:REQUIRES_BIOMARKER {status: $status}]->(b)
            """,
            crit_id=crit_id,
            bm_name=bm["name"].lower(),
            status=bm["status"],
        )

    # EXCLUDES_BIOMARKER
    for bm in crit.get("excludes_biomarkers", []):
        await session.run(
            """
            MATCH (cr:Criterion {id: $crit_id})
            MATCH (b:Biomarker {normalized_name: $bm_name})
            MERGE (cr)-[:EXCLUDES_BIOMARKER {status: $status}]->(b)
            """,
            crit_id=crit_id,
            bm_name=bm["name"].lower(),
            status=bm["status"],
        )

    # REQUIRES_PRIOR_DRUG
    for rxcui in crit.get("requires_prior_drugs", []):
        await session.run(
            """
            MATCH (cr:Criterion {id: $crit_id})
            MATCH (rx:RxNormConcept {rxcui: $rxcui})
            MERGE (cr)-[:REQUIRES_PRIOR_DRUG]->(rx)
            """,
            crit_id=crit_id,
            rxcui=rxcui,
        )

    # EXCLUDES_PRIOR_DRUG
    for rxcui in crit.get("excludes_prior_drugs", []):
        await session.run(
            """
            MATCH (cr:Criterion {id: $crit_id})
            MATCH (rx:RxNormConcept {rxcui: $rxcui})
            MERGE (cr)-[:EXCLUDES_PRIOR_DRUG]->(rx)
            """,
            crit_id=crit_id,
            rxcui=rxcui,
        )


async def _verify(session) -> None:
    result = await session.run(
        """
        MATCH (t:Trial) RETURN t.therapeutic_area AS area, count(t) AS cnt
        ORDER BY cnt DESC
        """
    )
    log.info("== Demo data verification ==")
    async for row in result:
        log.info("  %-25s %d trials", row["area"], row["cnt"])

    for label, query in [
        ("Trials",       "MATCH (t:Trial) RETURN count(t) AS n"),
        ("SNOMED",       "MATCH (s:SNOMEDConcept) RETURN count(s) AS n"),
        ("RxNorm",       "MATCH (r:RxNormConcept) RETURN count(r) AS n"),
        ("Biomarkers",   "MATCH (b:Biomarker) RETURN count(b) AS n"),
        ("Criteria",     "MATCH (c:Criterion) RETURN count(c) AS n"),
        ("Conditions",   "MATCH (c:Condition) RETURN count(c) AS n"),
        ("Interventions","MATCH (i:Intervention) RETURN count(i) AS n"),
    ]:
        r = await session.run(query)
        row = await r.single()
        log.info("  %-15s %d nodes", label, row["n"])


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def seed_all(driver, clear: bool = False) -> None:
    async with driver.session() as session:
        if clear:
            await _clear_demo_data(session)
        await _create_constraints(session)
        await _create_snomed(session)
        await _create_rxnorm(session)
        await _create_biomarkers(session)
        await _create_trials(session)
        await _verify(session)
    log.info("Seed complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Neo4j with demo clinical trial data")
    parser.add_argument("--clear", action="store_true", help="Wipe existing demo data first")
    args = parser.parse_args()

    async def _run() -> None:
        cfg = get_settings()
        driver = AsyncGraphDatabase.driver(
            cfg.neo4j.uri,
            auth=(cfg.neo4j.user, cfg.neo4j.password),
        )
        try:
            await seed_all(driver, clear=args.clear)
        finally:
            await driver.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
