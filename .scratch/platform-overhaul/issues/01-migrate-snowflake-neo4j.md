## Parent

Part of #3

## What to build

Point the existing ETL pipeline at a brand-new Snowflake account and a fresh Neo4j instance, and run the existing fetch → stage → transform → Cortex-parse → export → graph-load pipeline end to end, so the system has a complete, current snapshot of ClinicalTrials.gov recruiting/active trials to match against.

## Acceptance criteria

- [ ] New Snowflake account is configured and `snowflake_etl/setup_schema.py` runs cleanly against it, creating the `STAGING`/`CLEAN`/`TRACKING` schemas
- [ ] A full fetch of current `RECRUITING`/`ACTIVE_NOT_RECRUITING` trials completes into `STAGING.RAW_TRIALS`, and transform/Cortex-parsing populates `CLEAN.TRIALS` and `ELIGIBILITY_CRITERIA`
- [ ] A fresh Neo4j instance is provisioned and the existing ontology and trial/criteria loaders populate it from the migrated Snowflake data
- [ ] Existing `tests/test_snowflake.py` and the matcher/API tests pass against the new instances
- [ ] A known patient profile run through the existing MatchEngine returns real, current ranked trial results

## Blocked by

None (can start immediately)
