## Parent

Part of #3

## What to build

Deploy the finished API and React frontend to production, wired to the production Snowflake/Neo4j/Postgres/Auth0 instances, on the cost-optimized mixed-vendor stack from ADR 0004.

## Acceptance criteria

- [ ] The FastAPI backend is deployed to Cloud Run, configured against the production Snowflake/Neo4j/Postgres/Auth0 instances
- [ ] The React frontend is deployed to Cloudflare Pages or Vercel, configured to call the deployed backend
- [ ] End-to-end smoke test: a physician can sign up, log in, add a patient, run a match, and see results, entirely against the deployed production stack
- [ ] Deployment configuration (env vars/secrets) is documented for reproducibility

## Blocked by

- #4 (Provision fresh Snowflake + Neo4j and reload current trial data)
- #5 (Physician accounts: sign up, log in, manage own patients)
- #9 (React frontend: physician login, patients, match history)
