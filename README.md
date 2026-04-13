# Clinical Trial Eligibility Matcher

A system that matches patients to eligible clinical trials using a Neo4j knowledge graph, Snowflake for staging/analytics, and LLMs for intelligent eligibility criteria parsing.

## Architecture

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
     │            Snowflake                 │    ETL scripts
     │  Staging → Cleaned & Tagged          │─────────┘
     └──────────▲──────────────────────────┘
                │
    ┌───────────┴───────────────────────┐
    │          Data Sources             │
    │  ClinicalTrials.gov  SNOMED  Rx   │
    └───────────────────────────────────┘
```

## Setup

### Prerequisites

- Docker & Docker Compose
- Python 3.11+
- A Snowflake account (for full pipeline; not required for demo mode)
- OpenAI or Anthropic API key

### 1. Start Neo4j

```bash
cp .env.example .env
# Edit .env and set NEO4J_PASSWORD (and other credentials)
docker-compose up -d
```

Neo4j Browser will be available at http://localhost:7474.

### 2. Install Python dependencies

```bash
pip install -e ".[dev]"
# or with uv:
uv pip install -e ".[dev]"
```

### 3. Configure credentials

Edit `.env` with your actual credentials:

```
NEO4J_PASSWORD=your-secure-password
OPENAI_API_KEY=sk-...
SNOWFLAKE_ACCOUNT=...  # only needed for full pipeline
```

### 4. Run the demo (no Snowflake required)

```bash
python scripts/seed_demo_data.py
python -m uvicorn api.main:app --reload
streamlit run frontend/app.py
```

### 5. Run the full pipeline

```bash
# Fetch all trials from ClinicalTrials.gov (~50-80K trials, ~15 min)
python -m data_ingestion.fetch_trials

# Load into Snowflake staging
python -m snowflake_etl.load_staging

# Clean & tag therapeutic areas
python -m snowflake_etl.transform

# Export oncology trials to Neo4j
python -m snowflake_etl.export_for_neo4j --category oncology

# Parse eligibility criteria with LLM
python -m nlp.criteria_parser --category oncology

# Or run everything at once
python scripts/run_pipeline.py --category oncology
```

## Project Structure

```
clinical-trial-matcher/
├── config/           # Pydantic settings (loads .env)
├── data/             # Raw + processed data files
├── data_ingestion/   # ClinicalTrials.gov, SNOMED, RxNorm fetchers
├── snowflake_etl/    # Snowflake staging, transform, export
├── kg_builder/       # Neo4j graph loaders
├── nlp/              # LLM-based eligibility criteria parser
├── matcher/          # Core matching engine + patient schema
├── llm/              # Unified LLM provider interface
├── api/              # FastAPI backend
├── frontend/         # Streamlit UI
├── tests/            # Test suite
└── scripts/          # Pipeline runners & demo seeder
```

## Data Sources

- **ClinicalTrials.gov** — 50-80K active/recruiting trials via public API v2
- **SNOMED CT** — Disease ontology (requires UMLS license)
- **RxNorm** — Drug ontology (public, via NLM API)
