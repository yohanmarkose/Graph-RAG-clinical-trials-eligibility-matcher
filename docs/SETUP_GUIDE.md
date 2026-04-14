# Setup Guide — Running the Clinical Trial Matcher from a Fresh Clone

Assuming you have the same Snowflake credentials and an OpenAI API key.

## What's already done (shared via Snowflake)

All data lives in Snowflake and persists across clones:
- 86,620 trials in STAGING + CLEAN tables
- SNOMED/RxNorm in STAGING tables
- 1,374,771 split eligibility criteria
- ~1,166 Cortex-parsed criteria in TRACKING.PARSING_PROGRESS

You do NOT need to re-fetch from ClinicalTrials.gov, re-parse SNOMED/RxNorm, or re-run Cortex parsing.

## What you DO need to rebuild (local only)

- Neo4j graph (Docker volume — local to your machine)
- The CSV files in `data/processed/` (gitignored)

---

## Step-by-step setup

### 1. Prerequisites (one-time)

```bash
# Python 3.11+
python --version

# Install dependencies
pip install -e .
# or
pip install -r requirements.txt

# Docker (for Neo4j)
docker --version
```

### 2. Environment file

Copy and fill in your credentials:

```bash
cp .env.example .env
# Edit .env with:
#   NEO4J_PASSWORD=your_password
#   SNOWFLAKE_ACCOUNT=...
#   SNOWFLAKE_USER=...
#   SNOWFLAKE_PASSWORD=...
#   OPENAI_API_KEY=sk-...
```

### 3. Start Neo4j

```bash
docker compose up -d
```

Wait ~30 seconds for it to be healthy:
```bash
docker compose ps
```

### 4. Export data from Snowflake to CSVs (~30 seconds)

This pulls cleaned data from Snowflake into `data/processed/`:

```bash
# Export SNOMED and RxNorm from Snowflake staging to CSVs
python -c "
from snowflake_etl.load_staging import _get_conn, DATA_PROCESSED
import pandas as pd

conn = _get_conn()
cur = conn.cursor()

# SNOMED
for table, filename in [
    ('STAGING.RAW_SNOMED_CONCEPTS', 'snomed_concepts.csv'),
    ('STAGING.RAW_SNOMED_SYNONYMS', 'snomed_synonyms.csv'),
    ('STAGING.RAW_SNOMED_RELATIONSHIPS', 'snomed_relationships.csv'),
]:
    cur.execute(f'SELECT * FROM {table}')
    cols = [d[0].lower() for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    df.to_csv(DATA_PROCESSED / filename, index=False)
    print(f'{filename}: {len(df):,} rows')

# RxNorm
for table, filename in [
    ('STAGING.RAW_RXNORM_CONCEPTS', 'rxnorm_concepts.csv'),
    ('STAGING.RAW_RXNORM_RELATIONSHIPS', 'rxnorm_relationships.csv'),
]:
    cur.execute(f'SELECT * FROM {table}')
    cols = [d[0].lower() for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    df.to_csv(DATA_PROCESSED / filename, index=False)
    print(f'{filename}: {len(df):,} rows')

cur.close()
conn.close()
print('Done.')
"

# Export trials + parsed criteria from Snowflake
python -m snowflake_etl.export_for_neo4j --category oncology --parsed-only
```

You also need the synonym lookup JSON files for entity linking. Regenerate them:

```bash
python -c "
import json, pandas as pd
from pathlib import Path
DATA = Path('data/processed')

# SNOMED synonym lookup
df = pd.read_csv(DATA / 'snomed_synonyms.csv' if (DATA / 'snomed_synonyms.csv').exists() else DATA / 'snomed_concepts.csv', dtype=str).fillna('')
lookup = {}
if 'synonym' in df.columns:
    for _, row in df.iterrows():
        key = row['synonym'].lower().strip()
        if key:
            lookup.setdefault(key, []).append(row['concept_id'])
else:
    for _, row in df.iterrows():
        key = row['preferred_term'].lower().strip()
        if key:
            lookup.setdefault(key, []).append(row['concept_id'])
(DATA / 'snomed_synonym_lookup.json').write_text(json.dumps(lookup), encoding='utf-8')
print(f'snomed_synonym_lookup.json: {len(lookup):,} entries')

# RxNorm synonym lookup
df = pd.read_csv(DATA / 'rxnorm_concepts.csv', dtype=str).fillna('')
lookup = {}
for _, row in df.iterrows():
    key = row['name'].lower().strip()
    if key:
        lookup.setdefault(key, []).append(row['rxcui'])
(DATA / 'rxnorm_synonym_lookup.json').write_text(json.dumps(lookup), encoding='utf-8')
print(f'rxnorm_synonym_lookup.json: {len(lookup):,} entries')
"
```

### 5. Load SNOMED + RxNorm into Neo4j (~5-10 minutes)

```bash
python -m kg_builder.build_ontology
```

This creates 262K SNOMED concepts + 490K IS_A edges + 58K RxNorm concepts + 34K drug relationships.

### 6. Load trials into Neo4j (~1-2 minutes)

```bash
python -m kg_builder.load_trials --category oncology
```

This loads 24K trials + conditions + interventions + ~1,166 parsed criteria.

### 7. Run entity linking + graph enrichment (~12 minutes)

```bash
# Exact-match linking in Neo4j (Condition→SNOMED, Intervention→RxNorm)
python -m kg_builder.link_entities

# Enrich graph with parsed criteria edges (REQUIRES_CONDITION, etc.)
# This step uses RapidFuzz fuzzy matching internally — no separate fuzzy step needed
python -m nlp.criteria_parser --category oncology --enrich
```

Note: The `--enrich` step handles all entity linking for criteria (exact + fuzzy via RapidFuzz) in one go. No separate fuzzy matching command is required.

### 8. Start the app

```bash
# Terminal 1 — API
uvicorn api.main:app --reload

# Terminal 2 — Frontend
streamlit run frontend/app.py
```

Open http://localhost:8501 in your browser.

---

## Quick reference — what each step takes

| Step | Time | Needs internet | Needs Snowflake |
|------|------|---------------|----------------|
| Export from Snowflake | ~30s | No | Yes |
| Build ontology (SNOMED+RxNorm) | ~5-10 min | No | No |
| Load trials | ~1-2 min | No | No |
| Entity linking + enrichment | ~12 min | No | No |
| Start API + frontend | instant | No | No |

**Total from fresh clone to running app: ~20 minutes**

---

## What you do NOT need to re-run

| Step | Why not |
|------|---------|
| Fetch from ClinicalTrials.gov | Data is in Snowflake already |
| Parse SNOMED RF2 files | Data is in Snowflake already |
| Parse RxNorm RRF files | Data is in Snowflake already |
| Snowflake schema setup | Tables already exist |
| Snowflake staging load | Data already loaded |
| Snowflake transforms | CLEAN tables already populated |
| Cortex criteria parsing | 1,166 already parsed in TRACKING |

If you want to parse MORE criteria (beyond the 1,166), run:
```bash
python -m nlp.criteria_parser --category oncology --limit 5000 --warehouse-size XLARGE
```
