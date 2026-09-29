## Parent

Part of #3

## What to build

A single pipeline-runner entry point that incrementally refreshes trial data and the graph, scheduled monthly via GitHub Actions with no standing infrastructure between runs, per ADR 0007 and ADR 0008.

## Acceptance criteria

- [ ] A single pipeline-runner entry point sequences fetch → transform → Cortex-parse → export → graph-load
- [ ] The fetch step filters by `lastUpdatePostDate` since the last recorded run, processing only new/updated trials
- [ ] Already-parsed trials are skipped using the existing parsing-progress tracking tables
- [ ] The graph-load step uses `MERGE`-based Cypher so existing data isn't wiped and search stays available throughout
- [ ] Trials whose status has moved outside `RECRUITING`/`ACTIVE_NOT_RECRUITING` are detected and excluded from the graph
- [ ] A GitHub Actions workflow on a monthly cron schedule invokes the entry point using repository secrets
- [ ] Integration tests against fixture API responses verify: an updated trial gets reprocessed, an unchanged trial doesn't, and an aged-out trial disappears from match results

## Blocked by

- #4 (Provision fresh Snowflake + Neo4j and reload current trial data)
