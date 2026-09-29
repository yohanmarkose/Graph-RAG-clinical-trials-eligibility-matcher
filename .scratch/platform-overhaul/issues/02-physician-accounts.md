## Parent

Part of #3

## What to build

Physicians can create an account, log in, and manage their own saved Patients — a new operational data domain backed by a dedicated Postgres store, separate from Snowflake and Neo4j, per ADR 0002. Authentication is delegated to a managed provider (Auth0) per ADR 0003.

## Acceptance criteria

- [ ] A physician can sign up and log in via the managed auth provider
- [ ] An authenticated physician can create, view, edit, and list their own Patients (age, gender, conditions, biomarkers, prior therapies, ECOG status) via new API endpoints
- [ ] Patient data is stored in a new, dedicated Postgres database
- [ ] A physician cannot see or modify another physician's patients (isolation enforced at the API layer, per ADR 0005)
- [ ] New endpoints are tested via the existing `httpx.AsyncClient` + `ASGITransport` seam used in `tests/test_api.py`, with a `requires_postgres`-style skip guard mirroring `requires_snowflake`/`requires_neo4j`

## Blocked by

None (can start immediately)
