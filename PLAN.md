# Clinical Trial Eligibility Matcher — Complete Implementation Plan (v2)

## Changes from v1
- **Snowflake** added as staging, cleaning, tracking, and analytics layer
- **All trials** ingested (no condition filter) — 50-80K active/recruiting trials
- **Category-based filtering** at every expensive step — process oncology first, expand later
- Prompts renumbered to accommodate new phases

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Neo4j Graph Schema](#2-neo4j-graph-schema)
3. [Phase 1: Project Setup & Infrastructure](#phase-1-project-setup--infrastructure)
4. [Phase 2: Data Ingestion — ClinicalTrials.gov](#phase-2-data-ingestion--clinicaltrialsgov)
5. [Phase 3: Data Ingestion — SNOMED CT & RxNorm](#phase-3-data-ingestion--snomed-ct--rxnorm)
6. [Phase 4: Snowflake Staging & Cleaning](#phase-4-snowflake-staging--cleaning)
7. [Phase 5: Knowledge Graph — Ontology Backbone](#phase-5-knowledge-graph--ontology-backbone)
8. [Phase 6: Knowledge Graph — Trial Loading](#phase-6-knowledge-graph--trial-loading)
9. [Phase 7: NLP — LLM Provider & Entity Linking](#phase-7-nlp--llm-provider--entity-linking)
10. [Phase 8: NLP — Eligibility Criteria Parsing](#phase-8-nlp--eligibility-criteria-parsing)
11. [Phase 9: Matching Engine](#phase-9-matching-engine)
12. [Phase 10: LLM Explanation Layer](#phase-10-llm-explanation-layer)
13. [Phase 11: FastAPI Backend](#phase-11-fastapi-backend)
14. [Phase 12: Streamlit Frontend](#phase-12-streamlit-frontend)
15. [Phase 13: End-to-End Testing & Demo Seed](#phase-13-end-to-end-testing--demo-seed)
16. [Phase 14: Polish & Deployment](#phase-14-polish--deployment)
17. [Matching Algorithm Deep Dive](#matching-algorithm-deep-dive)
18. [Data Pipeline Details](#data-pipeline-details)
19. [Credentials & Configuration Checklist](#credentials--configuration-checklist)

---

## 1. Architecture Overview

```
┌─────────────┐     ┌──────────────────┐     ┌───────────────┐
│ Streamlit UI │────▶│  FastAPI Backend  │────▶│  Neo4j Graph  │
│  (frontend/) │◀────│    (api/)         │◀────│   Database    │
└─────────────┘     └────────┬─────────┘     └───────────────┘
                             │                       ▲
                     ┌───────▼────────┐              │
                     │   LLM Provider  │              │
                     │ (OpenAI/Claude) │              │
                     └────────────────┘              │
                                                     │
     ┌─────────────────────────────────────┐         │
     │            Snowflake                 │         │
     │  ┌───────────┐  ┌────────────────┐  │    ETL scripts
     │  │  Staging   │  │   Cleaned &    │  │─────────┘
     │  │  (raw)     │──│   Tagged       │  │
     │  └───────────┘  └────────────────┘  │
     └──────────▲──────────────────────────┘
                │
    ┌───────────┴───────────────────────┐
    │          Data Sources             │
    │  ClinicalTrials.gov  SNOMED  Rx   │
    └───────────────────────────────────┘

Data Flow (full pipeline):
1. ClinicalTrials.gov API (ALL trials) ──▶ data/raw/ JSON ──▶ Snowflake staging
2. SNOMED CT RF2 files ──▶ Python parser ──▶ Snowflake staging
3. RxNorm RRF files ──▶ Python parser ──▶ Snowflake staging
4. Snowflake: clean, deduplicate, tag by therapeutic area
5. Snowflake (filtered by category) ──▶ Python ETL ──▶ Neo4j
6. LLM parsing: Snowflake tracks progress, stores parsed results
7. Neo4j: final knowledge graph for matching

Data Flow (demo shortcut):
1. scripts/seed_demo_data.py ──▶ Neo4j directly (30 curated trials)
```

### Directory Structure

```
clinical-trial-matcher/
├── docker-compose.yml
├── .env                          # All credentials (gitignored)
├── .env.example                  # Template with placeholders
├── pyproject.toml
├── README.md
├── PLAN.md                       # This file
│
├── config/
│   └── settings.py               # Pydantic Settings loader
│
├── data/
│   ├── raw/                      # Raw JSON from ClinicalTrials.gov
│   ├── snomed/                   # SNOMED RF2 files (gitignored)
│   ├── rxnorm/                   # RxNorm RRF files (gitignored)
│   └── processed/                # Intermediate processed CSVs
│
├── data_ingestion/
│   ├── __init__.py
│   ├── fetch_trials.py           # ClinicalTrials.gov harvester (ALL trials)
│   ├── fetch_trials_demo.py      # Small batch fetcher for testing
│   ├── parse_snomed.py           # SNOMED RF2 parser
│   └── parse_rxnorm.py          # RxNorm RRF parser
│
├── snowflake_etl/
│   ├── __init__.py
│   ├── setup_schema.py           # Create Snowflake tables/views
│   ├── load_staging.py           # Load raw data into Snowflake
│   ├── transform.py              # Clean, deduplicate, tag therapeutic areas
│   ├── export_for_neo4j.py       # Export filtered data from Snowflake → CSV → Neo4j
│   └── track_parsing.py          # Track LLM parsing progress
│
├── kg_builder/
│   ├── __init__.py
│   ├── load_snomed.py            # Load SNOMED into Neo4j
│   ├── load_rxnorm.py            # Load RxNorm into Neo4j
│   ├── load_trials.py            # Load trials into Neo4j
│   └── link_entities.py          # Entity linking pipeline
│
├── nlp/
│   ├── __init__.py
│   ├── criteria_parser.py        # LLM-based eligibility parser
│   ├── entity_linker.py          # Condition/drug entity linker
│   └── prompts.py                # LLM prompt templates
│
├── matcher/
│   ├── __init__.py
│   ├── patient_schema.py         # Patient profile Pydantic model
│   ├── match_engine.py           # Core matching algorithm
│   └── scorer.py                 # Scoring and ranking logic
│
├── llm/
│   ├── __init__.py
│   ├── provider.py               # Unified LLM interface (OpenAI/Anthropic)
│   └── explainer.py              # Match explanation generator
│
├── api/
│   ├── __init__.py
│   ├── main.py                   # FastAPI app
│   ├── routes/
│   │   ├── match.py              # POST /match
│   │   ├── trials.py             # GET /trials/{nct_id}
│   │   ├── patient.py            # POST /patient/parse
│   │   └── stats.py              # GET /stats
│   └── dependencies.py           # Neo4j driver, LLM client DI
│
├── frontend/
│   └── app.py                    # Streamlit application
│
├── tests/
│   ├── test_ingestion.py
│   ├── test_snowflake.py
│   ├── test_matcher.py
│   ├── test_parser.py
│   └── test_api.py
│
└── scripts/
    ├── seed_demo_data.py         # Quick seed for demo (no Snowflake needed)
    ├── run_pipeline.py           # End-to-end pipeline runner with --category flag
    └── demo.sh                   # One-command demo launcher
```

---

## 2. Neo4j Graph Schema

### Node Types

```
(:Trial {
    nct_id: STRING,           // "NCT03602079"
    title: STRING,
    brief_summary: STRING,
    phase: STRING,            // "Phase 2", "Phase 3"
    status: STRING,           // "Recruiting", "Active, not recruiting"
    enrollment: INTEGER,
    start_date: DATE,
    primary_completion_date: DATE,
    sponsor: STRING,
    min_age: INTEGER,         // Parsed from eligibility (e.g., 18)
    max_age: INTEGER,         // Parsed from eligibility (e.g., 75)
    gender: STRING,           // "All", "Female", "Male"
    therapeutic_area: STRING, // "oncology", "cardiology", "neurology", etc.
    url: STRING               // Link to ClinicalTrials.gov
})

(:Condition {
    name: STRING,             // "HER2-positive Breast Cancer"
    snomed_id: STRING,        // Linked SNOMED concept ID (nullable)
    normalized_name: STRING   // Lowercased, cleaned version
})

(:Intervention {
    name: STRING,             // "Trastuzumab"
    type: STRING,             // "Drug", "Biological", "Procedure"
    rxnorm_id: STRING,        // Linked RxNorm concept ID (nullable)
    normalized_name: STRING
})

(:Criterion {
    id: STRING,               // UUID
    type: STRING,             // "inclusion" or "exclusion"
    raw_text: STRING,         // Original free-text criterion sentence
    category: STRING,         // "age", "gender", "condition", "biomarker",
                              // "prior_therapy", "lab_value", "other"
    parsed_json: STRING,      // JSON string of structured extraction
    parsing_status: STRING    // "pending", "parsed", "failed"
})

(:SNOMEDConcept {
    concept_id: STRING,       // SNOMED CT concept ID e.g. "254837009"
    term: STRING,             // Preferred term
    semantic_tag: STRING      // "disorder", "finding", "procedure"
})

(:RxNormConcept {
    rxcui: STRING,            // RxNorm concept unique identifier
    name: STRING,             // "trastuzumab"
    tty: STRING               // Term type: "IN" (ingredient), "BN" (brand name), etc.
})

(:Biomarker {
    name: STRING,             // "HER2", "EGFR", "PD-L1", "BRCA1"
    normalized_name: STRING
})

(:Patient {                   // Runtime node — created per query, deleted after
    patient_id: STRING,
    age: INTEGER,
    gender: STRING
})
```

### Relationship Types

```
// Trial → Condition/Intervention linkage
(:Trial)-[:STUDIES_CONDITION]->(:Condition)
(:Trial)-[:USES_INTERVENTION]->(:Intervention)
(:Trial)-[:HAS_CRITERION {type: "inclusion"|"exclusion"}]->(:Criterion)

// Criterion → structured links
(:Criterion)-[:REQUIRES_CONDITION]->(:SNOMEDConcept)
(:Criterion)-[:EXCLUDES_CONDITION]->(:SNOMEDConcept)
(:Criterion)-[:REQUIRES_PRIOR_DRUG]->(:RxNormConcept)
(:Criterion)-[:EXCLUDES_PRIOR_DRUG]->(:RxNormConcept)
(:Criterion)-[:REQUIRES_BIOMARKER {status: "positive"|"negative"}]->(:Biomarker)
(:Criterion)-[:EXCLUDES_BIOMARKER {status: "positive"|"negative"}]->(:Biomarker)

// Ontology backbone
(:SNOMEDConcept)-[:IS_A]->(:SNOMEDConcept)
(:Condition)-[:MAPS_TO_SNOMED]->(:SNOMEDConcept)
(:Intervention)-[:MAPS_TO_RXNORM]->(:RxNormConcept)
(:RxNormConcept)-[:HAS_INGREDIENT]->(:RxNormConcept)
(:RxNormConcept)-[:TRADENAME_OF]->(:RxNormConcept)

// Patient (runtime)
(:Patient)-[:HAS_CONDITION]->(:SNOMEDConcept)
(:Patient)-[:TOOK_DRUG]->(:RxNormConcept)
(:Patient)-[:HAS_BIOMARKER {status: "positive"|"negative"}]->(:Biomarker)
```

### Neo4j Indexes

```cypher
CREATE CONSTRAINT trial_nct_id IF NOT EXISTS FOR (t:Trial) REQUIRE t.nct_id IS UNIQUE;
CREATE INDEX trial_status IF NOT EXISTS FOR (t:Trial) ON (t.status);
CREATE INDEX trial_phase IF NOT EXISTS FOR (t:Trial) ON (t.phase);
CREATE INDEX trial_therapeutic_area IF NOT EXISTS FOR (t:Trial) ON (t.therapeutic_area);
CREATE CONSTRAINT snomed_id IF NOT EXISTS FOR (s:SNOMEDConcept) REQUIRE s.concept_id IS UNIQUE;
CREATE CONSTRAINT rxnorm_id IF NOT EXISTS FOR (r:RxNormConcept) REQUIRE r.rxcui IS UNIQUE;
CREATE INDEX condition_name IF NOT EXISTS FOR (c:Condition) ON (c.normalized_name);
CREATE INDEX intervention_name IF NOT EXISTS FOR (i:Intervention) ON (i.normalized_name);
CREATE INDEX criterion_type IF NOT EXISTS FOR (cr:Criterion) ON (cr.type);
CREATE INDEX criterion_parsing_status IF NOT EXISTS FOR (cr:Criterion) ON (cr.parsing_status);
CREATE INDEX biomarker_name IF NOT EXISTS FOR (b:Biomarker) ON (b.normalized_name);
```

---

## Phase 1: Project Setup & Infrastructure

### Claude Code Prompt 1

```
I'm building a Clinical Trial Eligibility Matcher. Set up the full project infrastructure.

Create the following directory structure at the project root `clinical-trial-matcher/`:

```
clinical-trial-matcher/
├── docker-compose.yml
├── .env.example
├── .gitignore
├── pyproject.toml
├── README.md
├── config/
│   └── settings.py
├── data/
│   ├── raw/
│   │   └── .gitkeep
│   ├── snomed/
│   │   └── .gitkeep
│   ├── rxnorm/
│   │   └── .gitkeep
│   └── processed/
│       └── .gitkeep
├── data_ingestion/
│   └── __init__.py
├── snowflake_etl/
│   └── __init__.py
├── kg_builder/
│   └── __init__.py
├── nlp/
│   └── __init__.py
├── matcher/
│   └── __init__.py
├── llm/
│   └── __init__.py
├── api/
│   ├── __init__.py
│   └── routes/
│       └── __init__.py
├── frontend/
│   └── .gitkeep
├── tests/
│   └── __init__.py
└── scripts/
    └── .gitkeep
```

**docker-compose.yml** should define:
- `neo4j` service using `neo4j:5.26-community` image
  - Ports: 7474 (browser), 7687 (bolt)
  - Environment: NEO4J_AUTH=neo4j/${NEO4J_PASSWORD} (from .env), NEO4J_PLUGINS=["apoc"]
  - Volume: `neo4j_data:/data` and `neo4j_plugins:/plugins`
  - Memory settings: NEO4J_server_memory_heap_initial__size=512m, NEO4J_server_memory_heap_max__size=1G

**pyproject.toml** should use Python 3.11+ and include these dependencies:
- neo4j (async driver)
- httpx (async HTTP client)
- pydantic and pydantic-settings
- fastapi and uvicorn
- openai (for GPT-4o mini)
- anthropic (for Claude — flexible LLM provider)
- snowflake-connector-python[pandas] (Snowflake connector with pandas support)
- rapidfuzz (string matching)
- pandas
- streamlit
- python-dotenv
- pytest and pytest-asyncio

**.env.example** with placeholders:
```
# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-neo4j-password-here

# Snowflake
SNOWFLAKE_ACCOUNT=your-account-id
SNOWFLAKE_USER=your-username
SNOWFLAKE_PASSWORD=your-password
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=CLINICAL_TRIALS
SNOWFLAKE_SCHEMA=PUBLIC
SNOWFLAKE_ROLE=SYSADMIN

# LLM
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-XXXXX
ANTHROPIC_API_KEY=sk-ant-XXXXX

# Data APIs (no auth needed)
CLINICALTRIALS_BASE_URL=https://clinicaltrials.gov/api/v2
RXNORM_BASE_URL=https://rxnav.nlm.nih.gov/REST

# Pipeline
DEFAULT_THERAPEUTIC_AREA=oncology
BATCH_SIZE=500
LLM_CONCURRENCY=10

# Application
API_HOST=0.0.0.0
API_PORT=8000
LOG_LEVEL=INFO
```

**config/settings.py** should use pydantic-settings BaseSettings to load all env vars from .env with type validation. Group into nested models: Neo4jSettings, SnowflakeSettings, LLMSettings, PipelineSettings, APISettings. Include:
- A `get_snowflake_connection()` method that returns a snowflake.connector connection
- A `therapeutic_area` property from PipelineSettings (default: "oncology")
- A `batch_size` property from PipelineSettings (default: 500)

**.gitignore** should include: .env, data/snomed/*, data/rxnorm/*, data/raw/*.json, __pycache__, .venv, neo4j_data, *.pyc

The **README.md** should have a brief project description, setup instructions (docker-compose up, pip install, copy .env.example to .env), and a placeholder architecture diagram section.

After creating all files, verify the structure is correct and docker-compose.yml is valid YAML.
```

---

## Phase 2: Data Ingestion — ClinicalTrials.gov

### Claude Code Prompt 2 — Fetch ALL Trials

```
In the clinical-trial-matcher project, create the ClinicalTrials.gov data ingestion pipeline. This fetches ALL active/recruiting trials, not just one condition.

Create `data_ingestion/fetch_trials.py` with the following:

1. An async function `fetch_all_trials()` that queries the ClinicalTrials.gov V2 API:
   - Endpoint: `https://clinicaltrials.gov/api/v2/studies`
   - Query params:
     - `filter.overallStatus`: "RECRUITING|ACTIVE_NOT_RECRUITING" (NO condition filter — we want everything)
     - `pageSize`: 100 (max per request)
     - `fields`: "NCTId,BriefTitle,OfficialTitle,BriefSummary,OfficialTitle,OverallStatus,Phase,EnrollmentInfo,StartDate,PrimaryCompletionDate,LeadSponsorName,Condition,InterventionName,InterventionType,EligibilityCriteria,Sex,MinimumAge,MaximumAge,LocationCity,LocationState,LocationCountry"
     - `pageToken`: for pagination
   - Use httpx.AsyncClient with timeout=30s and retry logic (3 retries with exponential backoff, 0.5s delay between requests)
   - Paginate through ALL results using the `nextPageToken` from each response
   - Save each page of results as `data/raw/trials_page_{N}.json`
   - After all pages fetched, merge into a single `data/raw/all_trials.json`
   - Log progress: "Fetched page {N}, total trials so far: {count}"
   - Expected: 50,000-80,000 trials total. This will take ~10-15 minutes.

2. A function `parse_trial_record(raw: dict) -> dict` that extracts and normalizes a single trial:
   - nct_id: from protocolSection.identificationModule.nctId
   - title: from protocolSection.identificationModule.briefTitle
   - official_title: from protocolSection.identificationModule.officialTitle
   - brief_summary: from protocolSection.descriptionModule.briefSummary
   - status: from protocolSection.statusModule.overallStatus
   - phase: from protocolSection.designModule.phases (join if list, e.g. ["PHASE2", "PHASE3"] → "Phase 2/Phase 3")
   - enrollment: from protocolSection.designModule.enrollmentInfo.count
   - start_date: from protocolSection.statusModule.startDateStruct.date
   - primary_completion_date: from protocolSection.statusModule.primaryCompletionDateStruct.date
   - sponsor: from protocolSection.sponsorCollaboratorsModule.leadSponsor.name
   - conditions: list from protocolSection.conditionsModule.conditions
   - interventions: list of {name, type} from protocolSection.armsInterventionsModule.interventions
   - eligibility_criteria: from protocolSection.eligibilityModule.eligibilityCriteria (full text block)
   - gender: from protocolSection.eligibilityModule.sex
   - min_age: parse integer from protocolSection.eligibilityModule.minimumAge (e.g., "18 Years" → 18, "N/A" → None)
   - max_age: parse integer from protocolSection.eligibilityModule.maximumAge
   - locations: list of {city, state, country} from protocolSection.contactsLocationsModule.locations

3. A function `classify_therapeutic_area(conditions: list[str]) -> str` that assigns a therapeutic area based on condition keywords:
   ```python
   THERAPEUTIC_AREA_KEYWORDS = {
       "oncology": ["cancer", "carcinoma", "tumor", "tumour", "neoplasm", "lymphoma", "leukemia", "leukaemia", "melanoma", "sarcoma", "myeloma", "glioma", "glioblastoma", "mesothelioma", "neuroblastoma"],
       "cardiology": ["heart", "cardiac", "cardiovascular", "hypertension", "atrial", "coronary", "arrhythmia", "cardiomyopathy", "heart failure", "myocardial"],
       "neurology": ["alzheimer", "parkinson", "epilepsy", "seizure", "multiple sclerosis", "neuropathy", "stroke", "dementia", "migraine", "als", "amyotrophic"],
       "immunology": ["lupus", "rheumatoid", "psoriasis", "crohn", "colitis", "autoimmune", "inflammatory bowel"],
       "endocrinology": ["diabetes", "thyroid", "obesity", "metabolic", "insulin"],
       "infectious_disease": ["hiv", "hepatitis", "tuberculosis", "malaria", "covid", "influenza", "infection"],
       "pulmonology": ["asthma", "copd", "pulmonary", "respiratory", "lung disease", "cystic fibrosis"],
       "psychiatry": ["depression", "anxiety", "schizophrenia", "bipolar", "ptsd", "adhd", "ocd"],
       "hematology": ["anemia", "hemophilia", "sickle cell", "thrombocytopenia", "myelodysplastic"],
       "rare_disease": ["orphan", "rare disease"]
   }
   ```
   - Check each condition against keywords (case-insensitive)
   - Return the first matching area, or "other" if none match
   - Note: oncology keywords should be checked FIRST as they're the highest priority

4. A function `process_all_trials()` that:
   - Loads `data/raw/all_trials.json`
   - Runs parse_trial_record on each
   - Adds therapeutic_area field via classify_therapeutic_area
   - Saves to `data/processed/trials_processed.json`
   - Prints summary stats:
     - Total trials
     - Trials per therapeutic area
     - Phase distribution
     - Avg eligibility criteria text length
     - Trials with missing eligibility text

5. A `__main__` block that runs the full pipeline: fetch → parse → classify → save

Also create `data_ingestion/fetch_trials_demo.py` — a simpler version that fetches a limited batch of N trials (default 500) for a SPECIFIC condition (default "breast cancer"), for quick testing during development.

Handle edge cases: missing fields should default to None, empty intervention lists, age parsing for "N/A" or missing values.

Use the config from `config/settings.py` for the base URL.


---

## Phase 3: Data Ingestion — SNOMED CT & RxNorm

### Claude Code Prompt 3 — SNOMED CT Parser

```
In the clinical-trial-matcher project, create the SNOMED CT data parser.

I have a UMLS license and have downloaded the SNOMED CT RF2 release files into `data/snomed/`. The key files are:
- `sct2_Concept_Full_*.txt` (or Snapshot) — concept IDs and active status
- `sct2_Description_Full_*.txt` (or Snapshot) — terms/synonyms for each concept
- `sct2_Relationship_Full_*.txt` (or Snapshot) — IS_A and other relationships between concepts

Create `data_ingestion/parse_snomed.py` with:

1. `load_snomed_concepts(snomed_dir: str) -> pd.DataFrame`:
   - Glob for the Concept file (pattern: `**/sct2_Concept_*`)
   - Read tab-delimited, dtype=str for all columns
   - Filter to active="1" only
   - Return DataFrame with columns: concept_id, active

2. `load_snomed_descriptions(snomed_dir: str) -> pd.DataFrame`:
   - Glob for the Description file
   - Read tab-delimited, dtype=str
   - Filter to active="1"
   - Return DataFrame with: concept_id, term, type_id
   - type_id 900000000000003001 = Fully Specified Name (FSN)
   - type_id 900000000000013009 = Synonym
   - Create a `preferred_term` column: pick the FSN for each concept, strip the semantic tag (e.g., "Breast cancer (disorder)" → "Breast cancer"), fallback to first synonym
   - Extract `semantic_tag` from FSN parenthetical (e.g., "disorder", "finding", "procedure")

3. `load_snomed_relationships(snomed_dir: str) -> pd.DataFrame`:
   - Glob for the Relationship file
   - Read tab-delimited, dtype=str
   - Filter to active="1" and typeId="116680003" (IS_A relationship only)
   - Return DataFrame with: source_id, destination_id (child IS_A parent)

4. `filter_to_clinical_subset(concepts_df, descriptions_df, relationships_df) -> tuple`:
   - Starting from these root concept IDs:
     - Clinical Finding: 404684003
     - Procedure: 71388002
     - Pharmaceutical/Biologic Product: 373873005
     - Body Structure: 123037004
   - Use BFS/DFS on the IS_A relationships to collect ALL descendant concept IDs from each root
   - Filter all three DataFrames to only include concepts in this set
   - This should yield ~50-100K concepts out of ~350K total
   - Print: how many concepts per hierarchy root

5. `build_synonym_lookup(descriptions_df) -> dict`:
   - Build a dict: {lowercased_term: [concept_id, ...]}
   - Include all active synonyms and FSNs (with semantic tags stripped)
   - This will be used later for entity linking

6. `export_for_neo4j(concepts_df, descriptions_df, relationships_df, output_dir)`:
   - Save three CSVs to `data/processed/`:
     - `snomed_concepts.csv`: concept_id, preferred_term, semantic_tag
     - `snomed_synonyms.csv`: concept_id, synonym (one row per synonym)
     - `snomed_relationships.csv`: source_id, destination_id, type (always "IS_A")
   - Also save `snomed_synonym_lookup.json` — the full lookup dict for entity linking

7. `__main__` block that runs the full pipeline. Print timing for each step.

Note: The RF2 files can be large (millions of rows). The filtering step is crucial to keep the graph manageable. Use chunked reading if memory is a concern (>4GB files).
```

### Claude Code Prompt 4 — RxNorm Parser

```
In the clinical-trial-matcher project, create the RxNorm data parser.

I've downloaded the RxNorm Full Monthly Release (RRF format) into `data/rxnorm/`. The key files are:
- `RXNCONSO.RRF` — concepts and names (pipe-delimited, no header)
  Columns: RXCUI|LAT|TS|LUI|STT|SUI|ISPREF|RXAUI|SAUI|SCUI|SDUI|SAB|TTY|CODE|STR|SRL|SUPPRESS|CVF
- `RXNREL.RRF` — relationships (pipe-delimited, no header)
  Columns: RXCUI1|RXAUI1|STYPE1|REL|RXCUI2|RXAUI2|STYPE2|RELA|RUI|SRUI|SAB|SL|RG|DIR|SUPPRESS|CVF

Create `data_ingestion/parse_rxnorm.py` with:

1. `load_rxnorm_concepts(rxnorm_dir: str) -> pd.DataFrame`:
   - Read RXNCONSO.RRF (sep="|", no header, assign column names manually)
   - Handle trailing pipe (there's an extra empty column at the end)
   - Filter to: SAB="RXNORM", LAT="ENG", SUPPRESS!="O"
   - Keep term types (TTY): IN (Ingredient), BN (Brand Name), PIN (Precise Ingredient), MIN (Multiple Ingredients), SCD (Semantic Clinical Drug), SBD (Semantic Branded Drug)
   - Return DataFrame: rxcui, name (STR), tty

2. `load_rxnorm_relationships(rxnorm_dir: str) -> pd.DataFrame`:
   - Read RXNREL.RRF (same pipe-delimited format)
   - Filter to RELA in: "has_ingredient", "tradename_of", "consists_of", "form_of"
   - Return DataFrame: rxcui1, rxcui2, relationship_type (RELA)

3. `build_drug_synonym_lookup(concepts_df) -> dict`:
   - Build: {lowercased_name: [rxcui, ...]}
   - Include all name variants (brand names, ingredients, clinical drug names)
   - E.g., "herceptin" → RXCUI for trastuzumab brand, "trastuzumab" → ingredient RXCUI

4. `build_ingredient_mappings(concepts_df, relationships_df) -> dict`:
   - For each brand name or clinical drug, trace to its ingredient(s) via relationships
   - Return: {rxcui: [ingredient_rxcui, ...]}

5. `export_for_neo4j(concepts_df, relationships_df, output_dir)`:
   - Save to `data/processed/`:
     - `rxnorm_concepts.csv`: rxcui, name, tty
     - `rxnorm_relationships.csv`: source_rxcui, target_rxcui, relationship_type
     - `rxnorm_synonym_lookup.json`: full lookup dict for entity linking

6. `__main__` block to run the pipeline with timing.

Use dtype=str for all IDs to avoid integer overflow issues.
```

---

## Phase 4: Snowflake Staging & Cleaning

### Claude Code Prompt 5 — Snowflake Schema & Staging

```
In the clinical-trial-matcher project, create the Snowflake ETL layer. This is the central staging, cleaning, and tracking layer for all data.

Create `snowflake_etl/setup_schema.py`:

A script that connects to Snowflake and creates the complete schema. Use the snowflake-connector-python library and read credentials from config/settings.py.

Create these Snowflake objects:

```sql
-- Database and schema (idempotent)
CREATE DATABASE IF NOT EXISTS CLINICAL_TRIALS;
USE DATABASE CLINICAL_TRIALS;
CREATE SCHEMA IF NOT EXISTS STAGING;
CREATE SCHEMA IF NOT EXISTS CLEAN;
CREATE SCHEMA IF NOT EXISTS TRACKING;

-- ============ STAGING TABLES (raw data) ============

-- Raw trial data from ClinicalTrials.gov
CREATE TABLE IF NOT EXISTS STAGING.RAW_TRIALS (
    nct_id VARCHAR(20) PRIMARY KEY,
    title VARCHAR(2000),
    official_title VARCHAR(4000),
    brief_summary TEXT,
    status VARCHAR(100),
    phase VARCHAR(100),
    enrollment INTEGER,
    start_date VARCHAR(50),
    primary_completion_date VARCHAR(50),
    sponsor VARCHAR(500),
    conditions VARIANT,          -- JSON array of condition strings
    interventions VARIANT,       -- JSON array of {name, type} objects
    eligibility_criteria TEXT,   -- Full free-text eligibility block
    gender VARCHAR(20),
    min_age INTEGER,
    max_age INTEGER,
    locations VARIANT,           -- JSON array of {city, state, country}
    raw_json VARIANT,            -- Complete raw API response for this trial
    ingested_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Raw SNOMED concepts
CREATE TABLE IF NOT EXISTS STAGING.RAW_SNOMED_CONCEPTS (
    concept_id VARCHAR(20) PRIMARY KEY,
    preferred_term VARCHAR(1000),
    semantic_tag VARCHAR(100)
);

-- Raw SNOMED synonyms
CREATE TABLE IF NOT EXISTS STAGING.RAW_SNOMED_SYNONYMS (
    concept_id VARCHAR(20),
    synonym VARCHAR(1000)
);

-- Raw SNOMED relationships
CREATE TABLE IF NOT EXISTS STAGING.RAW_SNOMED_RELATIONSHIPS (
    source_id VARCHAR(20),
    destination_id VARCHAR(20),
    relationship_type VARCHAR(50)
);

-- Raw RxNorm concepts
CREATE TABLE IF NOT EXISTS STAGING.RAW_RXNORM_CONCEPTS (
    rxcui VARCHAR(20) PRIMARY KEY,
    name VARCHAR(1000),
    tty VARCHAR(20)
);

-- Raw RxNorm relationships
CREATE TABLE IF NOT EXISTS STAGING.RAW_RXNORM_RELATIONSHIPS (
    source_rxcui VARCHAR(20),
    target_rxcui VARCHAR(20),
    relationship_type VARCHAR(100)
);

-- ============ CLEAN TABLES (transformed) ============

-- Cleaned trials with therapeutic area classification
CREATE TABLE IF NOT EXISTS CLEAN.TRIALS (
    nct_id VARCHAR(20) PRIMARY KEY,
    title VARCHAR(2000),
    official_title VARCHAR(4000),
    brief_summary TEXT,
    status VARCHAR(100),
    phase VARCHAR(100),
    enrollment INTEGER,
    start_date DATE,
    primary_completion_date DATE,
    sponsor VARCHAR(500),
    gender VARCHAR(20),
    min_age INTEGER,
    max_age INTEGER,
    therapeutic_area VARCHAR(100),
    eligibility_criteria TEXT,
    conditions_count INTEGER,
    interventions_count INTEGER,
    criteria_char_length INTEGER,
    has_eligibility_text BOOLEAN,
    url VARCHAR(500),
    cleaned_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Flattened: one row per trial-condition pair
CREATE TABLE IF NOT EXISTS CLEAN.TRIAL_CONDITIONS (
    nct_id VARCHAR(20),
    condition_name VARCHAR(1000),
    condition_normalized VARCHAR(1000),  -- lowercased, trimmed
    snomed_concept_id VARCHAR(20),       -- NULL until entity linking
    link_confidence FLOAT,               -- NULL until entity linking
    link_method VARCHAR(50)              -- "exact", "fuzzy", "llm", NULL
);

-- Flattened: one row per trial-intervention pair
CREATE TABLE IF NOT EXISTS CLEAN.TRIAL_INTERVENTIONS (
    nct_id VARCHAR(20),
    intervention_name VARCHAR(1000),
    intervention_type VARCHAR(100),
    intervention_normalized VARCHAR(1000),
    rxnorm_rxcui VARCHAR(20),           -- NULL until entity linking
    link_confidence FLOAT,
    link_method VARCHAR(50)
);

-- Individual eligibility criteria sentences (split from the full text block)
CREATE TABLE IF NOT EXISTS CLEAN.ELIGIBILITY_CRITERIA (
    criterion_id VARCHAR(50) PRIMARY KEY,   -- UUID
    nct_id VARCHAR(20),
    criterion_type VARCHAR(20),              -- "inclusion" or "exclusion"
    raw_text TEXT,
    sentence_index INTEGER,                  -- Order within the trial's criteria
    therapeutic_area VARCHAR(100)             -- Copied from trial for easy filtering
);

-- ============ TRACKING TABLES (LLM parsing progress) ============

-- Track which criteria have been parsed by the LLM
CREATE TABLE IF NOT EXISTS TRACKING.PARSING_PROGRESS (
    criterion_id VARCHAR(50) PRIMARY KEY,
    nct_id VARCHAR(20),
    parsing_status VARCHAR(20) DEFAULT 'pending',  -- pending, parsed, failed, skipped
    parsed_json VARIANT,                             -- Structured extraction result
    parsed_at TIMESTAMP_NTZ,
    llm_model VARCHAR(100),
    llm_tokens_used INTEGER,
    error_message TEXT,
    attempt_count INTEGER DEFAULT 0
);

-- Track which conditions/drugs have been entity-linked
CREATE TABLE IF NOT EXISTS TRACKING.ENTITY_LINKING_PROGRESS (
    entity_text VARCHAR(1000),
    entity_type VARCHAR(20),          -- "condition" or "drug"
    linked_id VARCHAR(20),            -- SNOMED concept_id or RxNorm rxcui
    linked_term VARCHAR(1000),
    confidence FLOAT,
    method VARCHAR(50),               -- "exact", "fuzzy", "llm"
    linked_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Pipeline run log
CREATE TABLE IF NOT EXISTS TRACKING.PIPELINE_RUNS (
    run_id VARCHAR(50) PRIMARY KEY,
    run_type VARCHAR(50),             -- "full_ingest", "parse_criteria", "entity_link"
    therapeutic_area VARCHAR(100),    -- Which category was processed
    status VARCHAR(20),               -- "running", "completed", "failed"
    started_at TIMESTAMP_NTZ,
    completed_at TIMESTAMP_NTZ,
    records_processed INTEGER,
    records_succeeded INTEGER,
    records_failed INTEGER,
    error_message TEXT
);
```

The script should:
- Connect using settings from config/settings.py
- Run all CREATE statements idempotently
- Print confirmation for each table created
- Handle connection errors gracefully

Create `snowflake_etl/load_staging.py`:

1. `def load_trials_to_snowflake(processed_json_path: str)`:
   - Read `data/processed/trials_processed.json`
   - Load into STAGING.RAW_TRIALS using pandas + write_pandas (from snowflake.connector.pandas_tools)
   - Use batch inserts of 5000 rows
   - Handle VARIANT columns (conditions, interventions, locations) by converting to JSON strings
   - Use MERGE on nct_id for idempotent upserts (so re-runs don't create duplicates)
   - Print: total rows loaded, time taken

2. `def load_snomed_to_snowflake()`:
   - Read from `data/processed/snomed_concepts.csv`, `snomed_synonyms.csv`, `snomed_relationships.csv`
   - Load into corresponding STAGING tables
   - Print row counts

3. `def load_rxnorm_to_snowflake()`:
   - Same pattern for RxNorm CSVs

4. `__main__` block that runs all three loads

Create `snowflake_etl/transform.py`:

1. `def classify_therapeutic_areas()`:
   - Run SQL to populate CLEAN.TRIALS from STAGING.RAW_TRIALS:
   ```sql
   INSERT INTO CLEAN.TRIALS
   SELECT
       nct_id, title, official_title, brief_summary, status, phase,
       enrollment,
       TRY_TO_DATE(start_date, 'YYYY-MM-DD') AS start_date,
       TRY_TO_DATE(primary_completion_date, 'YYYY-MM-DD') AS primary_completion_date,
       sponsor, gender, min_age, max_age,
       -- Therapeutic area classification
       CASE
           WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP '.*(cancer|carcinoma|tumor|tumour|neoplasm|lymphoma|leukemia|melanoma|sarcoma|myeloma|glioma|glioblastoma).*' THEN 'oncology'
           WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP '.*(heart|cardiac|cardiovascular|hypertension|coronary|arrhythmia|cardiomyopathy).*' THEN 'cardiology'
           WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP '.*(alzheimer|parkinson|epilepsy|multiple sclerosis|neuropathy|stroke|dementia|migraine).*' THEN 'neurology'
           WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP '.*(diabetes|thyroid|obesity|metabolic|insulin).*' THEN 'endocrinology'
           WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP '.*(lupus|rheumatoid|psoriasis|crohn|colitis|autoimmune).*' THEN 'immunology'
           WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP '.*(hiv|hepatitis|tuberculosis|malaria|covid|infection).*' THEN 'infectious_disease'
           WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP '.*(asthma|copd|pulmonary|respiratory|cystic fibrosis).*' THEN 'pulmonology'
           WHEN LOWER(ARRAY_TO_STRING(conditions, ' ')) REGEXP '.*(depression|anxiety|schizophrenia|bipolar|ptsd|adhd).*' THEN 'psychiatry'
           ELSE 'other'
       END AS therapeutic_area,
       eligibility_criteria,
       ARRAY_SIZE(conditions) AS conditions_count,
       ARRAY_SIZE(interventions) AS interventions_count,
       LENGTH(eligibility_criteria) AS criteria_char_length,
       (eligibility_criteria IS NOT NULL AND LENGTH(eligibility_criteria) > 10) AS has_eligibility_text,
       CONCAT('https://clinicaltrials.gov/study/', nct_id) AS url
   FROM STAGING.RAW_TRIALS;
   ```

2. `def flatten_conditions()`:
   - Flatten the VARIANT conditions array into CLEAN.TRIAL_CONDITIONS:
   ```sql
   INSERT INTO CLEAN.TRIAL_CONDITIONS (nct_id, condition_name, condition_normalized)
   SELECT
       t.nct_id,
       f.value::STRING AS condition_name,
       LOWER(TRIM(f.value::STRING)) AS condition_normalized
   FROM STAGING.RAW_TRIALS t,
   LATERAL FLATTEN(input => t.conditions) f;
   ```

3. `def flatten_interventions()`:
   - Same pattern for interventions into CLEAN.TRIAL_INTERVENTIONS

4. `def split_eligibility_criteria()`:
   - For each trial, split the eligibility_criteria text into individual sentences
   - Classify each as "inclusion" or "exclusion" based on section headers
   - Insert into CLEAN.ELIGIBILITY_CRITERIA with UUIDs
   - Copy therapeutic_area from the trial for easy filtering
   - This is done in Python (reading from Snowflake, processing, writing back) since the text splitting logic is complex:
     ```python
     # Read trials with eligibility text
     # For each trial:
     #   Split on newlines
     #   Track state: after "Inclusion Criteria:" → inclusion, after "Exclusion Criteria:" → exclusion
     #   Skip empty lines, header lines, numbered prefixes
     #   Generate UUID for each criterion
     #   Write back to Snowflake
     ```

5. `def initialize_parsing_tracking()`:
   - For all criteria in CLEAN.ELIGIBILITY_CRITERIA that aren't in TRACKING.PARSING_PROGRESS, insert with status='pending'
   - This sets up the tracking table for LLM parsing

6. `def print_summary_stats()`:
   - Total trials by therapeutic area
   - Total conditions (unique)
   - Total interventions (unique)
   - Total criteria sentences
   - Criteria per therapeutic area
   - Trials with/without eligibility text

7. `__main__` block that runs all transforms in sequence

Add a helper function `get_therapeutic_area_stats() -> dict` that returns counts by area — used by the pipeline runner to show what's available.


### Claude Code Prompt 6 — Snowflake Export for Neo4j


In the clinical-trial-matcher project, create the Snowflake → Neo4j export pipeline. This reads cleaned data from Snowflake (filtered by therapeutic area) and exports it as CSVs ready for Neo4j bulk import.

Create `snowflake_etl/export_for_neo4j.py`:

1. `def export_trials(conn, therapeutic_area: str | None, output_dir: str)`:
   - Query CLEAN.TRIALS, optionally filtered by therapeutic_area
   - If therapeutic_area is None, export ALL trials
   - Save as `data/processed/neo4j_trials.csv`
   - Return count of exported trials
   ```sql
   SELECT * FROM CLEAN.TRIALS
   WHERE (:therapeutic_area IS NULL OR therapeutic_area = :therapeutic_area)
   ```

2. `def export_conditions(conn, therapeutic_area: str | None, output_dir: str)`:
   - Query CLEAN.TRIAL_CONDITIONS for the matching trials
   - Save as `data/processed/neo4j_trial_conditions.csv`
   ```sql
   SELECT DISTINCT tc.* FROM CLEAN.TRIAL_CONDITIONS tc
   JOIN CLEAN.TRIALS t ON tc.nct_id = t.nct_id
   WHERE (:therapeutic_area IS NULL OR t.therapeutic_area = :therapeutic_area)
   ```

3. `def export_interventions(conn, therapeutic_area: str | None, output_dir: str)`:
   - Same pattern for interventions
   - Save as `data/processed/neo4j_trial_interventions.csv`

4. `def export_criteria(conn, therapeutic_area: str | None, output_dir: str)`:
   - Export from CLEAN.ELIGIBILITY_CRITERIA
   - Join with TRACKING.PARSING_PROGRESS to include parsed_json where available
   - Save as `data/processed/neo4j_criteria.csv`
   ```sql
   SELECT ec.*, pp.parsing_status, pp.parsed_json
   FROM CLEAN.ELIGIBILITY_CRITERIA ec
   LEFT JOIN TRACKING.PARSING_PROGRESS pp ON ec.criterion_id = pp.criterion_id
   WHERE (:therapeutic_area IS NULL OR ec.therapeutic_area = :therapeutic_area)
   ```

5. `def export_entity_links(conn, output_dir: str)`:
   - Export TRACKING.ENTITY_LINKING_PROGRESS
   - Save as `data/processed/neo4j_entity_links.csv`

6. `def export_all(therapeutic_area: str | None = None)`:
   - Connect to Snowflake
   - Run all exports
   - Print summary: X trials, Y conditions, Z interventions, W criteria exported for area "{therapeutic_area}"

7. Add CLI argument parsing:
   ```
   python -m snowflake_etl.export_for_neo4j --category oncology
   python -m snowflake_etl.export_for_neo4j --category all
   ```

Create `snowflake_etl/track_parsing.py`:

1. `def get_unparsed_criteria(conn, therapeutic_area: str, batch_size: int = 500) -> list[dict]`:
   - Query TRACKING.PARSING_PROGRESS for pending criteria in the given area:
   ```sql
   SELECT ec.criterion_id, ec.nct_id, ec.criterion_type, ec.raw_text
   FROM CLEAN.ELIGIBILITY_CRITERIA ec
   JOIN TRACKING.PARSING_PROGRESS pp ON ec.criterion_id = pp.criterion_id
   WHERE pp.parsing_status = 'pending'
     AND ec.therapeutic_area = :therapeutic_area
   LIMIT :batch_size
   ```

2. `def update_parsing_result(conn, criterion_id: str, status: str, parsed_json: dict | None, model: str, tokens: int, error: str | None)`:
   - Update TRACKING.PARSING_PROGRESS for one criterion
   - Increment attempt_count

3. `def update_batch_results(conn, results: list[dict])`:
   - Batch update many criteria at once (more efficient)

4. `def get_parsing_stats(conn, therapeutic_area: str | None = None) -> dict`:
   - Return: {pending, parsed, failed, skipped, total} counts
   - Optionally filtered by therapeutic area

5. `def log_pipeline_run(conn, run_type: str, therapeutic_area: str, status: str, records_processed: int, records_succeeded: int, records_failed: int, error: str | None)`:
   - Insert into TRACKING.PIPELINE_RUNS
---

## Phase 5: Knowledge Graph — Ontology Backbone

### Claude Code Prompt 7 — Load SNOMED & RxNorm into Neo4j

```
In the clinical-trial-matcher project, create the knowledge graph builder that loads SNOMED CT and RxNorm into Neo4j.

Prerequisites: Phase 3 outputs exist at:
- `data/processed/snomed_concepts.csv` (concept_id, preferred_term, semantic_tag)
- `data/processed/snomed_synonyms.csv` (concept_id, synonym)
- `data/processed/snomed_relationships.csv` (source_id, destination_id, type)
- `data/processed/rxnorm_concepts.csv` (rxcui, name, tty)
- `data/processed/rxnorm_relationships.csv` (source_rxcui, target_rxcui, relationship_type)

Create `kg_builder/load_snomed.py`:

1. `async def create_indexes(driver)`:
   - Create all uniqueness constraints and indexes:
   ```cypher
   CREATE CONSTRAINT trial_nct_id IF NOT EXISTS FOR (t:Trial) REQUIRE t.nct_id IS UNIQUE;
   CREATE INDEX trial_status IF NOT EXISTS FOR (t:Trial) ON (t.status);
   CREATE INDEX trial_phase IF NOT EXISTS FOR (t:Trial) ON (t.phase);
   CREATE INDEX trial_therapeutic_area IF NOT EXISTS FOR (t:Trial) ON (t.therapeutic_area);
   CREATE CONSTRAINT snomed_id IF NOT EXISTS FOR (s:SNOMEDConcept) REQUIRE s.concept_id IS UNIQUE;
   CREATE CONSTRAINT rxnorm_id IF NOT EXISTS FOR (r:RxNormConcept) REQUIRE r.rxcui IS UNIQUE;
   CREATE INDEX condition_name IF NOT EXISTS FOR (c:Condition) ON (c.normalized_name);
   CREATE INDEX intervention_name IF NOT EXISTS FOR (i:Intervention) ON (i.normalized_name);
   CREATE INDEX criterion_type IF NOT EXISTS FOR (cr:Criterion) ON (cr.type);
   CREATE INDEX criterion_parsing_status IF NOT EXISTS FOR (cr:Criterion) ON (cr.parsing_status);
   CREATE INDEX biomarker_name IF NOT EXISTS FOR (b:Biomarker) ON (b.normalized_name);
   ```

2. `async def load_snomed_concepts(driver, csv_path)`:
   - Read the CSV in chunks of 5000
   - For each chunk, run a batch Cypher UNWIND:
     ```cypher
     UNWIND $batch AS row
     MERGE (s:SNOMEDConcept {concept_id: row.concept_id})
     SET s.term = row.preferred_term, s.semantic_tag = row.semantic_tag
     ```
   - Print progress every 10K nodes

3. `async def load_snomed_relationships(driver, csv_path)`:
   - Batch UNWIND to create IS_A relationships:
     ```cypher
     UNWIND $batch AS row
     MATCH (child:SNOMEDConcept {concept_id: row.source_id})
     MATCH (parent:SNOMEDConcept {concept_id: row.destination_id})
     MERGE (child)-[:IS_A]->(parent)
     ```

4. `async def verify_snomed(driver)`:
   - Run validation queries:
     - Total SNOMEDConcept nodes
     - Total IS_A relationships
     - Test: find ancestors of "Malignant neoplasm of breast" (254837009) up to 3 hops
     - Print results

Create `kg_builder/load_rxnorm.py`:

1. `async def load_rxnorm_concepts(driver, csv_path)`:
   - Create (:RxNormConcept) nodes with rxcui, name, tty

2. `async def load_rxnorm_relationships(driver, csv_path)`:
   - Create typed relationships:
     - "has_ingredient" → [:HAS_INGREDIENT]
     - "tradename_of" → [:TRADENAME_OF]

3. `async def verify_rxnorm(driver)`:
   - Test: find "trastuzumab" → its brand names → their ingredients

Create `kg_builder/build_ontology.py` — orchestrator:
- Connects to Neo4j using settings
- Runs: create_indexes → load_snomed → load_rxnorm → verify both
- Uses async neo4j driver
- Prints timing for each step

Use batch sizes of 5000 for all UNWIND operations. Use MERGE not CREATE so re-runs are idempotent.


---

## Phase 6: Knowledge Graph — Trial Loading

### Claude Code Prompt 8 — Load Trials (from Snowflake Export)

```
In the clinical-trial-matcher project, create the trial loader that ingests Snowflake-exported trial data into Neo4j.

Prerequisites:
- Neo4j running with SNOMED and RxNorm loaded (Phase 5)
- Snowflake export CSVs in `data/processed/`:
  - `neo4j_trials.csv`
  - `neo4j_trial_conditions.csv`
  - `neo4j_trial_interventions.csv`
  - `neo4j_criteria.csv`

These were generated by `snowflake_etl/export_for_neo4j.py --category <area>` and contain only trials matching the selected therapeutic area.

Create `kg_builder/load_trials.py`:

1. `async def load_trial_nodes(driver, csv_path: str)`:
   - Read neo4j_trials.csv
   - Batch UNWIND with chunks of 500:
     ```cypher
     UNWIND $batch AS t
     MERGE (trial:Trial {nct_id: t.nct_id})
     SET trial.title = t.title,
         trial.brief_summary = t.brief_summary,
         trial.phase = t.phase,
         trial.status = t.status,
         trial.enrollment = t.enrollment,
         trial.start_date = t.start_date,
         trial.sponsor = t.sponsor,
         trial.min_age = t.min_age,
         trial.max_age = t.max_age,
         trial.gender = t.gender,
         trial.therapeutic_area = t.therapeutic_area,
         trial.url = t.url
     ```
   - Print progress and total

2. `async def load_conditions(driver, csv_path: str)`:
   - Read neo4j_trial_conditions.csv
   - Collect unique conditions
   - Create (:Condition) nodes with MERGE:
     ```cypher
     UNWIND $batch AS c
     MERGE (cond:Condition {normalized_name: c.condition_normalized})
     SET cond.name = c.condition_name
     ```
   - Then create relationships:
     ```cypher
     UNWIND $batch AS c
     MATCH (t:Trial {nct_id: c.nct_id})
     MATCH (cond:Condition {normalized_name: c.condition_normalized})
     MERGE (t)-[:STUDIES_CONDITION]->(cond)
     ```
   - If snomed_concept_id is not null, also create MAPS_TO_SNOMED edge:
     ```cypher
     MATCH (cond:Condition {normalized_name: c.condition_normalized})
     MATCH (sc:SNOMEDConcept {concept_id: c.snomed_concept_id})
     MERGE (cond)-[:MAPS_TO_SNOMED]->(sc)
     ```

3. `async def load_interventions(driver, csv_path: str)`:
   - Same pattern for interventions → (:Intervention) + USES_INTERVENTION + MAPS_TO_RXNORM

4. `async def load_criteria(driver, csv_path: str)`:
   - Read neo4j_criteria.csv
   - Create (:Criterion) nodes:
     ```cypher
     UNWIND $batch AS cr
     MERGE (c:Criterion {id: cr.criterion_id})
     SET c.type = cr.criterion_type,
         c.raw_text = cr.raw_text,
         c.parsing_status = COALESCE(cr.parsing_status, 'pending'),
         c.parsed_json = cr.parsed_json
     ```
   - Link to trials:
     ```cypher
     UNWIND $batch AS cr
     MATCH (t:Trial {nct_id: cr.nct_id})
     MATCH (c:Criterion {id: cr.criterion_id})
     MERGE (t)-[:HAS_CRITERION {type: cr.criterion_type}]->(c)
     ```

5. `async def run_trial_loader(category: str | None = None)`:
   - If CSVs don't exist, prompt user to run snowflake_etl.export_for_neo4j first
   - Run steps 1-4 in sequence
   - Print stats: total trials, conditions, interventions, criteria loaded

Create `kg_builder/link_entities.py`:

1. `async def link_conditions_to_snomed(driver)`:
   - For each (:Condition) without a MAPS_TO_SNOMED edge:
     a. Exact match: condition.normalized_name against SNOMEDConcept.term (lowered)
     b. Skip fuzzy matching for now (Phase 7 handles it with RapidFuzz)
   - Create edges where matched
   - Print: matched / total

2. `async def link_interventions_to_rxnorm(driver)`:
   - Same pattern for Intervention → RxNormConcept

3. CLI: `python -m kg_builder.load_trials --category oncology`
```

---

## Phase 7: NLP — LLM Provider & Entity Linking

### Claude Code Prompt 9 — LLM Provider + Entity Linker

```
In the clinical-trial-matcher project, create the unified LLM provider and the entity linking pipeline.

Create `llm/provider.py`:

1. Abstract base class `LLMProvider`:
   ```python
   async def complete(self, system_prompt: str, user_prompt: str, temperature: float = 0.0, max_tokens: int = 2000) -> str
   ```

2. `OpenAIProvider`:
   - Uses `openai` async client
   - Model from settings (default: "gpt-4o-mini")
   - Implements complete() via chat.completions.create
   - Retry logic: 3 retries with exponential backoff (1s, 2s, 4s)
   - Handles rate limit errors (429) specifically

3. `AnthropicProvider`:
   - Uses `anthropic` async client
   - Model from settings (default: "claude-sonnet-4-20250514")
   - Same retry logic

4. Factory: `get_llm_provider(settings) -> LLMProvider`

5. `CachedLLMProvider` wrapper:
   - In-memory dict: (hash(system+user prompt)) → response
   - `save_cache(path)` / `load_cache(path)` for JSON persistence
   - Falls through on miss
   - Useful during development to avoid re-calling LLM on same criteria

Create `nlp/entity_linker.py`:

1. `class EntityLinker`:
   - __init__ takes paths to synonym lookups (from Phase 3):
     - `data/processed/snomed_synonym_lookup.json`
     - `data/processed/rxnorm_synonym_lookup.json`
   - Loads both into memory

2. `def link_condition_to_snomed(self, condition_name: str, threshold: float = 85.0) -> list[dict]`:
   - Step 1: Exact match (lowered) against SNOMED synonym lookup
   - Step 2: If no exact match, use rapidfuzz.process.extractBests against all SNOMED terms, with score_cutoff=threshold
   - Return list of {concept_id, term, score, method ("exact" or "fuzzy")}
   - Limit to top 3 matches

3. `def link_drug_to_rxnorm(self, drug_name: str, threshold: float = 85.0) -> list[dict]`:
   - Same pattern against RxNorm synonyms
   - Return list of {rxcui, name, score, method}

4. `async def link_with_llm_fallback(self, text: str, entity_type: str, llm: LLMProvider) -> dict | None`:
   - When fuzzy matching confidence is below threshold
   - Ask LLM: "What is the most likely SNOMED concept for '{text}'? Return JSON: {concept_name, concept_id_hint}"
   - Try to match the LLM's suggestion against the synonym lookup
   - Return best match or None

5. `async def link_all_conditions_in_snowflake(self, sf_conn, llm: LLMProvider, therapeutic_area: str)`:
   - Query CLEAN.TRIAL_CONDITIONS where snomed_concept_id IS NULL and therapeutic_area matches
   - For each unique condition_normalized:
     - Try entity linking (exact → fuzzy → LLM fallback)
     - Update CLEAN.TRIAL_CONDITIONS with the result
     - Also insert into TRACKING.ENTITY_LINKING_PROGRESS
   - Print: X conditions linked out of Y total

6. `async def link_all_interventions_in_snowflake(self, sf_conn, llm: LLMProvider, therapeutic_area: str)`:
   - Same pattern for drugs → RxNorm

7. RapidFuzz optimization note: for large synonym lists (100K+), pre-build a list of (term, concept_id) tuples and use `process.extract` with `score_cutoff` to avoid scanning the entire list for every query.


---

## Phase 8: NLP — Eligibility Criteria Parsing

### Claude Code Prompt 10 — Criteria Parser (Snowflake-tracked)

```
In the clinical-trial-matcher project, create the NLP pipeline that parses free-text eligibility criteria into structured data. This uses Snowflake to track progress so parsing is resumable and category-filterable.

Create `nlp/prompts.py`:

Define prompt templates as Python string constants:

1. `CRITERIA_PARSE_SYSTEM_PROMPT`:
```
You are a clinical trial eligibility criteria parser. Given a single eligibility criterion sentence from a clinical trial, extract structured information.

Return ONLY a JSON object (no markdown, no explanation) with these fields:
{
  "category": "age|gender|condition|biomarker|prior_therapy|lab_value|performance_status|other",
  "conditions": [{"name": "...", "snomed_hint": "..."}],
  "biomarkers": [{"name": "...", "status": "positive|negative|any"}],
  "drugs": [{"name": "...", "role": "required|excluded"}],
  "age_constraint": {"min": null, "max": null},
  "gender_constraint": null,
  "lab_values": [{"name": "...", "operator": "<=|>=|=|<|>", "value": ...}],
  "logic": "description of the logical rule in plain English",
  "confidence": 0.0-1.0
}

Rules:
- Only populate fields that are explicitly stated in the criterion
- For conditions, provide the most specific medical term
- snomed_hint is your best guess at what SNOMED concept this maps to
- For drugs, include both generic and brand names if recognizable
- confidence should reflect how certain you are about the extraction
- If the criterion is too vague or administrative, set category to "other" and confidence low
```

2. `PATIENT_PARSE_SYSTEM_PROMPT`:
```
You are a medical NLP system. Given a free-text description of a patient, extract a structured patient profile.

Return ONLY a JSON object:
{
  "age": integer or null,
  "gender": "male"|"female"|"other"|null,
  "conditions": [{"name": "...", "snomed_hint": "..."}],
  "biomarkers": [{"name": "...", "status": "positive|negative"}],
  "prior_therapies": [{"drug_name": "...", "rxnorm_hint": "..."}],
  "lab_values": [{"name": "...", "value": ..., "unit": "..."}],
  "ecog_status": integer or null
}
```

3. `EXPLANATION_SYSTEM_PROMPT`:
```
You are a clinical trial matching assistant. Given a patient profile and a matched clinical trial with its eligibility criteria, generate a clear, concise explanation of why this trial matches or doesn't match the patient.

Format your response as:
- A 1-2 sentence summary of the match
- A bullet list of inclusion criteria with ✓ (met), ✗ (not met), or ? (unknown) for each
- A bullet list of exclusion criteria (✓ = NOT triggered, ✗ = patient IS excluded)
- A brief note on anything the patient should confirm with their physician

Keep language accessible. Be precise about why criteria are met or not.
```

Create `nlp/criteria_parser.py`:

1. `async def parse_single_criterion(llm: LLMProvider, criterion_text: str) -> dict`:
   - Calls the LLM with CRITERIA_PARSE_SYSTEM_PROMPT
   - Parses JSON response
   - Validates with Pydantic model `ParsedCriterion`
   - Returns validated dict, or fallback {category: "other", confidence: 0} on failure

2. `async def parse_batch_from_snowflake(llm: LLMProvider, sf_conn, therapeutic_area: str, batch_size: int = 500)`:
   - Uses `snowflake_etl.track_parsing.get_unparsed_criteria()` to get the next batch
   - Parses each criterion with asyncio.Semaphore(settings.llm_concurrency) for rate limiting
   - After each result, updates Snowflake via `track_parsing.update_batch_results()`
   - Returns: {parsed: N, failed: M, remaining: R}

3. `async def run_parsing_pipeline(therapeutic_area: str)`:
   - Loop: get batch → parse → update → repeat until no more pending
   - Print progress every batch: "Parsed {X}/{total}, {Y} failed, {Z} remaining"
   - Save LLM cache periodically
   - Log pipeline run to TRACKING.PIPELINE_RUNS

4. `async def enrich_graph_with_parsed_criteria(driver, sf_conn, therapeutic_area: str)`:
   - After parsing is complete, read all parsed results from Snowflake
   - For each parsed criterion:
     - Update the (:Criterion) node in Neo4j with parsed_json
     - Use the entity linker to resolve conditions → SNOMED, drugs → RxNorm
     - Create graph edges:
       - REQUIRES_CONDITION / EXCLUDES_CONDITION → SNOMEDConcept
       - REQUIRES_PRIOR_DRUG / EXCLUDES_PRIOR_DRUG → RxNormConcept
       - REQUIRES_BIOMARKER / EXCLUDES_BIOMARKER → Biomarker
   - Track entity links in Snowflake

5. CLI:
   ```
   python -m nlp.criteria_parser --category oncology          # parse oncology criteria
   python -m nlp.criteria_parser --category oncology --enrich # parse + create graph edges
   python -m nlp.criteria_parser --stats                      # show parsing progress
   python -m nlp.criteria_parser --stats --category oncology  # show progress for oncology
   ```

Include proper error handling: if LLM returns unparseable JSON, log the error, mark as failed in Snowflake, and continue. Don't let one bad criterion crash the whole batch.
```

---

## Phase 9: Matching Engine

### Claude Code Prompt 11 — Patient Schema + Matching Algorithm

```
In the clinical-trial-matcher project, create the core matching engine.

Create `matcher/patient_schema.py`:

```python
from pydantic import BaseModel, Field
from typing import Optional

class BiomarkerStatus(BaseModel):
    name: str                          # "HER2", "EGFR", "PD-L1"
    status: str                        # "positive", "negative"

class LabValue(BaseModel):
    name: str                          # "ECOG", "Hemoglobin"
    value: float
    unit: Optional[str] = None

class PatientProfile(BaseModel):
    age: Optional[int] = None
    gender: Optional[str] = None
    conditions: list[str] = Field(default_factory=list)          # SNOMED concept IDs
    condition_names: list[str] = Field(default_factory=list)     # Free-text names (display)
    biomarkers: list[BiomarkerStatus] = Field(default_factory=list)
    prior_therapies: list[str] = Field(default_factory=list)     # RxNorm CUIs
    prior_therapy_names: list[str] = Field(default_factory=list) # Free-text names (display)
    lab_values: list[LabValue] = Field(default_factory=list)
    ecog_status: Optional[int] = None
    therapeutic_area: Optional[str] = None                       # Optional filter
```

Create `matcher/match_engine.py`:

`MatchEngine` class with Neo4j async driver in constructor.

**Stage 1: Coarse Condition Filter**
```python
async def find_candidate_trials(self, patient: PatientProfile) -> list[str]:
```
- Cypher: find trials sharing conditions with patient via SNOMED IS_A (0..3 hops):
  ```cypher
  UNWIND $patient_conditions AS cond_id
  MATCH (pc:SNOMEDConcept {concept_id: cond_id})
  MATCH (pc)-[:IS_A*0..3]->(ancestor:SNOMEDConcept)
  MATCH (criterion:Criterion)-[:REQUIRES_CONDITION]->(ancestor)
  MATCH (trial:Trial)-[:HAS_CRITERION]->(criterion)
  WHERE trial.status IN ['RECRUITING', 'ACTIVE_NOT_RECRUITING']
  AND ($therapeutic_area IS NULL OR trial.therapeutic_area = $therapeutic_area)
  RETURN DISTINCT trial.nct_id AS nct_id
  ```
- Also match via Condition nodes:
  ```cypher
  MATCH (trial:Trial)-[:STUDIES_CONDITION]->(c:Condition)-[:MAPS_TO_SNOMED]->(sc:SNOMEDConcept)
  WHERE sc.concept_id IN $patient_conditions
     OR EXISTS {
       MATCH (pc:SNOMEDConcept)-[:IS_A*1..3]->(sc)
       WHERE pc.concept_id IN $patient_conditions
     }
  AND ($therapeutic_area IS NULL OR trial.therapeutic_area = $therapeutic_area)
  RETURN DISTINCT trial.nct_id
  ```
- Union results, return list of NCT IDs

**Stage 2: Exclusion Filter**
```python
async def apply_exclusions(self, patient: PatientProfile, candidate_nct_ids: list[str]) -> list[str]:
```
- For each candidate, check:
  - Age: trial.min_age <= patient.age <= trial.max_age
  - Gender: trial.gender in ["All", "ALL", patient.gender]
  - Excluded conditions (via EXCLUDES_CONDITION edges)
  - Excluded drugs (via EXCLUDES_PRIOR_DRUG edges)
  - Excluded biomarkers (via EXCLUDES_BIOMARKER edges)
- Return filtered NCT IDs

**Stage 3: Inclusion Scoring**
```python
async def score_trials(self, patient: PatientProfile, filtered_nct_ids: list[str]) -> list[dict]:
```
Score 0-100:
- Condition match (40 pts): matched_required / total_required × 40. Direct = full, ancestor = 75%, descendant = 50%
- Biomarker match (25 pts): all required matched = 25, partial = proportional
- Prior therapy (15 pts): required + have = 15, not required = 10, required + missing = 0
- Demographics (10 pts): age in range = 5, gender match = 5
- Trial quality (10 pts): Phase 3 = 5, Phase 2 = 3, Phase 1 = 1. Recruiting = 3. Enrollment >100 = 2

Return list of {nct_id, score, score_breakdown, matched_criteria, unmatched_criteria}

**Stage 4: Rank and Return**
```python
async def match(self, patient: PatientProfile, top_n: int = 10) -> list[dict]:
```
- Orchestrates all stages
- Fetches full trial metadata for top N
- Returns MatchResult dicts

Create `matcher/scorer.py` with scoring helper functions.

All functions should have type hints and docstrings.


---

## Phase 10: LLM Explanation Layer

### Claude Code Prompt 12 — Match Explainer

```
In the clinical-trial-matcher project, create the LLM-powered explanation generator.

Create `llm/explainer.py`:

1. `class MatchExplainer`:
   - __init__ takes LLMProvider and EntityLinker instances

2. `async def explain_match(self, patient: PatientProfile, trial: dict, match_result: dict) -> str`:
   - Constructs prompt with EXPLANATION_SYSTEM_PROMPT
   - User prompt includes: patient summary, trial info, parsed criteria, match breakdown
   - Returns explanation string

3. `async def explain_matches(self, patient: PatientProfile, matches: list[dict]) -> list[dict]`:
   - Parallel with asyncio.Semaphore(5)
   - Adds "explanation" field to each match
   - Returns enriched matches

4. `async def parse_free_text_patient(self, text: str) -> PatientProfile`:
   - Uses PATIENT_PARSE_SYSTEM_PROMPT
   - Parses JSON → attempts SNOMED/RxNorm resolution via entity linker
   - Returns populated PatientProfile

Example explanation output:

**Match Summary:** This Phase 3 trial studies a new HER2-targeted therapy for metastatic breast cancer — strong match for your profile (Score: 87/100).

**Inclusion Criteria:**
✓ HER2-positive breast cancer — Matches your condition
✓ Prior trastuzumab therapy — You have received trastuzumab
✓ Age 18-75 — You are 58
✓ Female — Matches
? ECOG ≤ 2 — Not provided in your profile

**Exclusion Criteria:**
✓ No active brain metastases — Not indicated
✓ No prior T-DM1 — Not in your history

**Note:** Please confirm ECOG status with your physician. This is a screening tool — consult your care team.

```
---

## Phase 11: FastAPI Backend

### Claude Code Prompt 13 — API Layer
```

In the clinical-trial-matcher project, create the FastAPI backend.

Create `api/dependencies.py`:
- Lifespan context manager:
  - Startup: create Neo4j driver, LLM provider, EntityLinker, MatchEngine, MatchExplainer
  - Store in app.state
  - Shutdown: close Neo4j driver
- Dependency functions: get_driver(), get_match_engine(), get_explainer(), get_entity_linker()

Create `api/main.py`:
- FastAPI app, title="Clinical Trial Eligibility Matcher API", version="2.0.0"
- CORS middleware (allow all origins for demo)
- Include all route routers
- GET / → {"status": "ok", "neo4j": "connected"}

Create `api/routes/match.py`:
- `POST /match`:
  - Body: PatientProfile
  - Query params: top_n (default 10), include_explanations (default true), therapeutic_area (optional filter)
  - Calls match_engine.match() → explainer.explain_matches()
  - Response:
    ```python
    class TrialMatch(BaseModel):
        nct_id: str
        title: str
        phase: str
        status: str
        sponsor: str
        score: float
        score_breakdown: dict
        matched_criteria: list[str]
        unmatched_criteria: list[str]
        explanation: Optional[str] = None
        url: str

    class MatchResponse(BaseModel):
        patient_summary: str
        total_candidates: int
        total_after_exclusions: int
        matches: list[TrialMatch]
        query_time_ms: float
        therapeutic_area: Optional[str] = None
    ```

Create `api/routes/trials.py`:
- `GET /trials/{nct_id}`: full trial with criteria, conditions, interventions, SNOMED/RxNorm links

Create `api/routes/patient.py`:
- `POST /patient/parse`: free text → PatientProfile

Create `api/routes/stats.py`:
- `GET /stats`: graph stats + trial counts by therapeutic area + phase distribution

All endpoints: error handling, logging, response timing.

Create `scripts/run_api.py` — starts uvicorn on port 8000.
```

---

## Phase 12: Streamlit Frontend

### Claude Code Prompt 14 — Streamlit Demo UI

```
In the clinical-trial-matcher project, create the Streamlit frontend.

Create `frontend/app.py`:

**1. Sidebar — Patient Profile Input**

Top of sidebar: therapeutic area selector (selectbox):
- Options: "All", "Oncology", "Cardiology", "Neurology", "Endocrinology", "Immunology", "Infectious Disease", "Pulmonology", "Psychiatry", "Other"
- This filters which trials are searched

Two input modes (radio toggle):

a. "Structured Input":
   - Age: number (default 58)
   - Gender: selectbox
   - Conditions: multiselect with common options grouped by therapeutic area:
     - Oncology: Breast Cancer, Lung Cancer, Colorectal Cancer, Melanoma, Prostate Cancer, Leukemia
     - Cardiology: Heart Failure, Atrial Fibrillation, Coronary Artery Disease
     - Neurology: Alzheimer's, Parkinson's, Multiple Sclerosis, Epilepsy
     - etc.
   - Biomarkers: dynamic rows (name + positive/negative)
   - Prior Therapies: text input (comma-separated)
   - ECOG: selectbox (0-4, Unknown)

b. "Free Text":
   - Text area with pre-filled example
   - Calls POST /patient/parse
   - Shows extracted profile for confirmation

**2. Main Area — Results**

- "Find Matching Trials" button
- Summary bar: "Found X candidates → Y after exclusions → showing top Z"
- Query time display

- Trial cards (st.expander for each):
  - Score badge (colored: green >80, yellow 50-80, red <50)
  - Title, NCT ID (linked), Phase | Status | Sponsor
  - Score breakdown bar chart
  - LLM explanation (highlighted box)
  - "View Full Criteria" sub-expander

**3. Demo Examples** (top of page, 3 buttons):
- "HER2+ Breast Cancer" → age 58, female, breast cancer, HER2+, prior trastuzumab
- "Early-Stage NSCLC" → age 65, male, NSCLC, EGFR+, no prior therapy
- "Colorectal Cancer" → age 52, female, colorectal cancer, prior oxaliplatin + fluorouracil

**4. Footer**
- Disclaimer: "For research/educational purposes only. Not medical advice."
- Link to ClinicalTrials.gov

Config: API base URL from environment or st.secrets (default http://localhost:8000).
Use st.spinner, st.cache_data, requests for API calls, handle errors gracefully.

Create `frontend/components.py` with helper functions for rendering.

```
---

## Phase 13: End-to-End Testing & Demo Seed

### Claude Code Prompt 15 — Tests + Demo Seed

In the clinical-trial-matcher project, create the test suite and demo seed script.

Create `scripts/seed_demo_data.py`:

This creates a compelling demo dataset DIRECTLY in Neo4j, without requiring Snowflake or the full pipeline. It's the fast path for demos.

1. Create ~20 SNOMED concepts with IS_A hierarchy:
   - Breast cancer (254837009) IS_A Malignant neoplasm (363346000)
   - HER2-positive breast cancer IS_A Breast cancer
   - Metastatic breast cancer IS_A Breast cancer
   - Non-small cell lung cancer IS_A Lung cancer IS_A Malignant neoplasm
   - EGFR-positive NSCLC IS_A NSCLC
   - Colorectal cancer, metastatic colorectal cancer
   - Melanoma, Leukemia, Prostate cancer
   - Plus parent concepts

2. Create ~15 RxNorm drug concepts with relationships:
   - trastuzumab (IN), Herceptin (BN) → TRADENAME_OF → trastuzumab
   - pertuzumab, T-DM1 (ado-trastuzumab emtansine)
   - pembrolizumab, nivolumab, atezolizumab
   - oxaliplatin, fluorouracil, irinotecan
   - erlotinib, gefitinib, osimertinib
   - tamoxifen, letrozole

3. Create Biomarker nodes: HER2, EGFR, PD-L1, BRCA1, BRCA2, ER, PR, ALK, KRAS

4. Create ~30 realistic trial nodes across multiple therapeutic areas:
   - ~15 oncology (breast, lung, colorectal, melanoma)
   - ~5 cardiology
   - ~5 neurology
   - ~5 other
   Each with:
   - Realistic NCT IDs (NCT00000001 through NCT00000030)
   - 5-10 eligibility criteria each (pre-parsed)
   - Proper REQUIRES/EXCLUDES edges
   - Mix of phases and statuses
   - therapeutic_area property set

5. Verify with example query

Create test files:

`tests/test_matcher.py`:
1. HER2+ breast cancer patient → HER2 trials rank high
2. Age exclusion works
3. Prior trastuzumab → excluded from right trials, included in others
4. NSCLC EGFR+ → matches lung trials, not breast
5. Empty profile → no/low matches
6. therapeutic_area filter works

`tests/test_parser.py`:
- Criterion text → correct extraction

`tests/test_api.py`:
- POST /match, POST /patient/parse, GET /stats, GET /trials/{nct_id}

`tests/test_snowflake.py`:
- Test Snowflake connection, schema exists, table counts

Create `scripts/run_pipeline.py`:
- CLI with flags:
  ```
  python scripts/run_pipeline.py --demo          # seed demo data, skip Snowflake
  python scripts/run_pipeline.py --full --category oncology   # full pipeline, oncology only
  python scripts/run_pipeline.py --full --category all        # full pipeline, everything
  python scripts/run_pipeline.py --stats          # show current data stats
  ```
- Steps for --demo: check Neo4j → seed data → run tests → start API
- Steps for --full: check Neo4j + Snowflake → fetch trials → parse SNOMED/RxNorm → load Snowflake → transform → export → load Neo4j → parse criteria → link entities → run tests → start API


---

## Phase 14: Polish & Deployment

### Claude Code Prompt 16 — Final Polish

```
In the clinical-trial-matcher project, add final polish:

1. Update `README.md` with:
   - Project description and motivation
   - Architecture diagram (Mermaid):
     ```
     ClinicalTrials.gov → Snowflake (staging) → Neo4j (graph)
     SNOMED/RxNorm → Snowflake → Neo4j
     Patient → FastAPI → Neo4j matching → LLM explanation → Streamlit
     ```
   - Two quick start paths:
     - Demo (5 minutes): docker-compose up → seed → run
     - Full pipeline: setup Snowflake → ingest → transform → load → parse → run
   - Configuration guide
   - Example query and output
   - Category filtering explanation
   - Data sources and licenses
   - Disclaimer

2. Update docker-compose.yml with optional services for API and Streamlit

3. Create scripts/demo.sh:
   ```bash
   #!/bin/bash
   echo "Starting Clinical Trial Matcher Demo..."
   docker-compose up -d neo4j
   echo "Waiting for Neo4j to start..."
   sleep 15
   pip install -e . --quiet
   python scripts/seed_demo_data.py
   python scripts/run_api.py &
   sleep 3
   streamlit run frontend/app.py
   ```

4. Create Makefile:
   - setup: install deps
   - neo4j: start Neo4j container
   - seed: run demo seed
   - api: start FastAPI
   - frontend: start Streamlit
   - test: run tests
   - demo: one-command demo
   - snowflake-setup: create Snowflake schema
   - ingest: run full data ingestion
   - pipeline: run full pipeline with category flag
   - clean: remove containers and data

5. Ensure all Python files have docstrings, type hints, proper logging.
```

---

## Matching Algorithm Deep Dive

### Query Flow: "58-year-old female with HER2+ breast cancer, prior trastuzumab"

**Step 1: Profile Construction**
```json
{
  "age": 58,
  "gender": "female",
  "conditions": ["254837009"],
  "condition_names": ["breast cancer"],
  "biomarkers": [{"name": "HER2", "status": "positive"}],
  "prior_therapies": ["RXCUI_trastuzumab"],
  "prior_therapy_names": ["trastuzumab"],
  "ecog_status": null,
  "therapeutic_area": "oncology"
}
```

**Step 2: Coarse Filter** → ~50-200 candidates (for oncology, fewer for specific subtypes)

**Step 3: Exclusion** → ~30-100 remain

**Step 4: Scoring (0-100)**

| Component | Max | Calculation |
|---|---|---|
| Condition match | 40 | (matched / required) × 40 |
| Biomarker match | 25 | All required matched = 25, partial = proportional |
| Prior therapy | 15 | Required + have = 15 |
| Demographics | 10 | Age in range = 5, gender = 5 |
| Trial quality | 10 | Phase 3=5, Recruiting=3, Enrollment>100=2 |

**Step 5: Top 10 + LLM explanations**

---

## Data Pipeline Details

### ClinicalTrials.gov API

- **Endpoint:** `GET https://clinicaltrials.gov/api/v2/studies`
- **No condition filter** — fetches ALL recruiting/active trials
- **Rate limiting:** 0.5s delay between pages, exponential backoff on errors
- **Expected volume:** 50-80K trials, ~500-800 pages, ~10-15 min total fetch time
- **Response:** see Phase 2 prompt for full structure

### SNOMED CT RF2

- **Files:** Concept, Description, Relationship (tab-delimited)
- **Filter:** 4 hierarchies → ~50-100K concepts
- **Key IDs:** IS_A type = 116680003, FSN type = 900000000000003001

### RxNorm RRF

- **Files:** RXNCONSO.RRF, RXNREL.RRF (pipe-delimited with trailing pipe)
- **Filter:** SAB=RXNORM, TTY in (IN, BN, PIN, MIN, SCD, SBD)
- **Key relationships:** has_ingredient, tradename_of

### Data Flow Through Snowflake

```
1. Raw APIs → data/raw/ (JSON files on disk)
2. Python parsers → data/processed/ (CSVs)
3. load_staging.py → STAGING.* tables (raw data in Snowflake)
4. transform.py → CLEAN.* tables (deduplicated, classified, split)
5. LLM parsing → TRACKING.PARSING_PROGRESS (progress tracked)
6. export_for_neo4j.py --category X → data/processed/neo4j_*.csv
7. load_trials.py → Neo4j (final graph)
```

Each step is idempotent and resumable. If the LLM parser crashes at criterion #10,000, you restart and it picks up at #10,001 because Snowflake tracks what's been parsed.

---

## Credentials & Configuration Checklist

| Credential | Where to Get It | .env Variable | Required? |
|---|---|---|---|
| **Neo4j Password** | You choose (any string) | `NEO4J_PASSWORD=changeme123` | Yes |
| **Snowflake Account** | https://signup.snowflake.com (free 30-day trial, $400 credit) | `SNOWFLAKE_ACCOUNT=xxxxx` | Yes (for full pipeline) |
| **Snowflake User/Pass** | Set during Snowflake signup | `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD` | Yes (for full pipeline) |
| **OpenAI API Key** | https://platform.openai.com/api-keys | `OPENAI_API_KEY=sk-XXXXX` | Yes (if using OpenAI) |
| **Anthropic API Key** | https://console.anthropic.com → API Keys | `ANTHROPIC_API_KEY=sk-ant-XXXXX` | Yes (if using Anthropic) |
| **LLM Provider** | Your choice | `LLM_PROVIDER=openai` or `anthropic` | Yes |
| **LLM Model** | Your choice | `LLM_MODEL=gpt-4o-mini` | Yes |
| **UMLS License** | https://uts.nlm.nih.gov/uts/ (free) → Download SNOMED + RxNorm | N/A (file download) | Yes (for full pipeline) |
| **ClinicalTrials.gov** | No key needed | Preset in .env | N/A |
| **RxNorm API** | No key needed | Preset in .env | N/A |

### Complete `.env` file:

```bash
# ---- Neo4j ----
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=changeme123

# ---- Snowflake (not needed for demo mode) ----
SNOWFLAKE_ACCOUNT=your-account-id
SNOWFLAKE_USER=your-username
SNOWFLAKE_PASSWORD=your-password
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=CLINICAL_TRIALS
SNOWFLAKE_SCHEMA=PUBLIC
SNOWFLAKE_ROLE=SYSADMIN

# ---- LLM ----
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-XXXXX
ANTHROPIC_API_KEY=sk-ant-XXXXX

# ---- Data APIs (no auth) ----
CLINICALTRIALS_BASE_URL=https://clinicaltrials.gov/api/v2
RXNORM_BASE_URL=https://rxnav.nlm.nih.gov/REST

# ---- Pipeline ----
DEFAULT_THERAPEUTIC_AREA=oncology
BATCH_SIZE=500
LLM_CONCURRENCY=10

# ---- Application ----
API_HOST=0.0.0.0
API_PORT=8000
LOG_LEVEL=INFO
```

### Setup Steps:

**Demo path (no Snowflake needed):**
1. Install Docker Desktop, Python 3.11+
2. Clone repo, `cp .env.example .env`, fill in Neo4j password + LLM API key
3. `docker-compose up -d neo4j`
4. `pip install -e .`
5. `python scripts/seed_demo_data.py`
6. `python scripts/run_api.py` (terminal 1)
7. `streamlit run frontend/app.py` (terminal 2)
8. Open http://localhost:8501

**Full pipeline:**
1. All demo steps above PLUS:
2. Sign up for Snowflake trial → fill in Snowflake credentials in .env
3. Download SNOMED + RxNorm from UMLS → place in data/snomed/ and data/rxnorm/
4. `python -m snowflake_etl.setup_schema`
5. `python -m data_ingestion.fetch_trials` (fetches ALL trials, ~15 min)
6. `python -m data_ingestion.parse_snomed`
7. `python -m data_ingestion.parse_rxnorm`
8. `python -m snowflake_etl.load_staging`
9. `python -m snowflake_etl.transform`
10. `python -m nlp.criteria_parser --category oncology` (parses criteria, ~6 hours for oncology)
11. `python -m snowflake_etl.export_for_neo4j --category oncology`
12. `python -m kg_builder.load_trials --category oncology`
13. Start API + frontend

Or use: `python scripts/run_pipeline.py --full --category oncology`

---

## Cost Estimates

| Category | Trials | LLM Calls | Estimated Cost (GPT-4o mini) | Time |
|---|---|---|---|---|
| Demo seed | 30 | 0 | $0 | Instant |
| Oncology only | ~15-20K | ~250K | ~$30-50 | ~6 hours |
| + Cardiology | +10-15K | ~200K | +$20-30 | ~4 hours |
| + Neurology | +5-8K | ~100K | +$15 | ~3 hours |
| All categories | ~50-80K | ~1.2M | ~$150-250 | ~24 hours |
| Snowflake | — | — | Free trial ($400 credit) | — |

You can start with the demo, validate everything works, then progressively expand one category at a time.

---

## Appendix: Snowflake Useful Queries

```sql
-- How many trials per therapeutic area?
SELECT therapeutic_area, COUNT(*) AS trial_count
FROM CLEAN.TRIALS
GROUP BY therapeutic_area
ORDER BY trial_count DESC;

-- Parsing progress by area
SELECT ec.therapeutic_area, pp.parsing_status, COUNT(*) AS cnt
FROM CLEAN.ELIGIBILITY_CRITERIA ec
JOIN TRACKING.PARSING_PROGRESS pp ON ec.criterion_id = pp.criterion_id
GROUP BY ec.therapeutic_area, pp.parsing_status
ORDER BY ec.therapeutic_area, pp.parsing_status;

-- Entity linking coverage
SELECT entity_type, method, COUNT(*) AS linked, AVG(confidence) AS avg_confidence
FROM TRACKING.ENTITY_LINKING_PROGRESS
GROUP BY entity_type, method;

-- Find trials with the most criteria (complexity indicator)
SELECT nct_id, COUNT(*) AS criteria_count
FROM CLEAN.ELIGIBILITY_CRITERIA
GROUP BY nct_id
ORDER BY criteria_count DESC
LIMIT 20;

-- Recent pipeline runs
SELECT * FROM TRACKING.PIPELINE_RUNS
ORDER BY started_at DESC
LIMIT 10;
```

## Appendix: Neo4j Useful Queries

```cypher
// Count all nodes by type
MATCH (n) RETURN labels(n)[0] AS type, count(n) AS count ORDER BY count DESC;

// Trials by therapeutic area
MATCH (t:Trial) RETURN t.therapeutic_area AS area, count(t) AS count ORDER BY count DESC;

// Find all trials for a condition with SNOMED hierarchy
MATCH (s:SNOMEDConcept {concept_id: "254837009"})<-[:IS_A*0..3]-(descendant)
MATCH (c:Condition)-[:MAPS_TO_SNOMED]->(descendant)
MATCH (t:Trial)-[:STUDIES_CONDITION]->(c)
RETURN t.nct_id, t.title, t.phase;

// Find exclusion criteria for a specific drug
MATCH (r:RxNormConcept {name: "trastuzumab"})<-[:EXCLUDES_PRIOR_DRUG]-(cr:Criterion)<-[:HAS_CRITERION]-(t:Trial)
RETURN t.nct_id, t.title, cr.raw_text;

// Parsing status overview
MATCH (cr:Criterion) RETURN cr.parsing_status AS status, count(cr) AS count;
```
