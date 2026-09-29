## Problem Statement

The clinical-trials eligibility matcher works, but several things are now blocking it from being a usable, maintainable, real system rather than a one-off prototype:

- The Snowflake credentials backing the data pipeline are stale, blocking any further data work.
- The Neo4j graph lives on an instance that needs to be rebuilt.
- Trial data was loaded once and never refreshed, so results silently go stale as ClinicalTrials.gov data changes underneath the app.
- The Streamlit UI works but isn't visually polished or intuitive, and gives physicians no way to save a patient's profile or revisit past matches — every session starts from a blank form.
- When a trial is suggested as a match, physicians only get an AI-generated prose explanation, with no concrete evidence (which criteria matched, which didn't) they can verify against.
- There's no deployment story: the app only runs locally.

## Solution

Migrate to a fresh Snowflake account and a fresh Neo4j instance and reload all current ClinicalTrials.gov data using the existing pipeline and graph schema. Replace the Streamlit UI with a React frontend on top of the existing (extended) FastAPI backend, redesigned for clarity, and add physician accounts so physicians can save, edit, and revisit patients and their match history. Extend match results with a concrete criteria/graph-path explanation alongside the existing LLM prose. Add a monthly, incremental, GitHub-Actions-driven refresh pipeline that keeps trial data and the graph current without standing infrastructure or full reprocessing. Deploy the whole system on a cost-optimized, low-ops, mixed-vendor stack suited to low/demo-level traffic.

## User Stories

**Data migration**

1. As the project owner, I want to connect the ETL pipeline to a brand-new Snowflake account, so that data loading isn't blocked by expired credentials.
2. As the project owner, I want to run a full fetch of all current `RECRUITING`/`ACTIVE_NOT_RECRUITING` trials from ClinicalTrials.gov into the new Snowflake account, so that the system has a complete, current dataset to match against.
3. As the project owner, I want to rebuild the Neo4j graph on a fresh instance using the existing schema and loaders, so that graph queries work again without redesigning the data model.

**Physician accounts and saved patients**

4. As a physician, I want to create an account and log in, so that my saved patients and match history are private to me.
5. As a physician, I want to add a new patient with their clinical profile (age, gender, conditions, biomarkers, prior therapies, ECOG status), so that I don't have to re-enter their details every time I want to check trial matches.
6. As a physician, I want to edit an existing patient's profile, so that I can keep it up to date as their clinical picture changes.
7. As a physician, I want to see only the patients I've added, so that I never see another physician's patient data.
8. As a physician, I want to run a match for one of my saved patients directly, so that I don't have to re-type their profile into a one-off form.
9. As a physician, I want to see a history of past match runs for a patient, including the date and the top trial results at that time, so that I can track how matches have changed as new trials become available.
10. As a physician, I want past match history to stay readable even if a trial referenced in it later becomes unavailable, so that I have a stable historical record of what was matched at the time.

**Match explanation**

11. As a physician, I want a natural-language explanation of why a trial matched, so that I understand it without reading raw eligibility criteria.
12. As a physician, I want to see the concrete list of matched and excluded criteria and the graph path behind a match score, so that I can verify the reasoning instead of trusting AI prose blindly.

**UI/UX**

13. As a physician, I want a modern, visually clear interface for entering patient details and reviewing matches, so that I can use the tool efficiently in a clinical setting.
14. As a physician, I want visual indicators of match confidence (e.g. score bars/badges), so that I can triage results quickly without reading every number.
15. As a physician, I want the structured-input and free-text patient entry modes from today's app preserved in the new UI, so that I can use whichever is faster for a given situation.
16. As a physician, I want the quick-demo buttons and the ReKnoS multi-hop reasoning toggle preserved in the new UI, so that the exploratory/demo capabilities of the current app aren't lost in the rewrite.

**Monthly refresh pipeline**

17. As the project owner, I want a scheduled job to run monthly via GitHub Actions, so that trial data stays current without manual intervention.
18. As the project owner, I want the monthly refresh to only fetch and parse trials that are new or updated since the last run, so that I don't pay for redundant LLM parsing every month.
19. As the project owner, I want the monthly refresh to merge new/changed data into the existing Neo4j graph without rebuilding it, so that search stays available throughout the refresh.
20. As the project owner, I want trials whose status moves outside `RECRUITING`/`ACTIVE_NOT_RECRUITING` to be excluded from the graph during the refresh, so that physicians are never matched to a trial that's no longer accepting patients.
21. As the project owner, I want the monthly refresh to run without any standing infrastructure between runs, so that it doesn't add ongoing hosting cost.

**Deployment**

22. As the project owner, I want the backend deployed on a scale-to-zero, pay-per-use platform, so that I'm not paying for idle compute.
23. As the project owner, I want the frontend deployed on a static host with git-push deploys, so that releasing UI changes is simple and free at this scale.
24. As the project owner, I want physician/patient data stored in a managed Postgres instance separate from Snowflake and Neo4j, so that operational account data doesn't mix into analytical or graph stores.
25. As the project owner, I want authentication handled by a managed provider, so that I don't have to build and secure my own password/session system.
26. As the project owner, I want overall monthly hosting cost to stay low, so that running this project long-term is sustainable on a personal budget.

**Data policy**

27. As the project owner, I want the system to explicitly treat all patient data as synthetic/de-identified, so that I don't have to build HIPAA-grade compliance controls for a portfolio project.

## Implementation Decisions

- **Accounts**: a new backend module owns physician authentication and patient management, introducing `Physician` and `Patient` entities backed by a new, dedicated Postgres database (Neon or Supabase) — separate from Snowflake (analytical/reference data) and Neo4j (graph), per ADR 0002. Authentication is delegated to a managed provider (Auth0), per ADR 0003, rather than a custom user table.
- **Match Run history**: each match run against a saved Patient is persisted as a `Match Run` record storing a denormalized snapshot (trial ID, title, score, key match reasons) rather than a live reference to the graph, so history stays coherent after a trial is later excluded from the graph.
- **Authorization boundary**: each Physician can only read/write their own Patients — no shared roster or admin role (ADR 0005). Patients are never written into Neo4j (ADR 0006); the graph stays a stateless reference/matching store.
- **API layer**: extended with authenticated endpoints for physician profile, patient CRUD, patient match-history retrieval, and running a match against a saved patient (wrapping the existing match logic and persisting a Match Run snapshot on completion). Existing unauthenticated demo endpoints can remain for anonymous/demo use.
- **Match explanation payload**: the match response is extended to include a structured list of matched/excluded criteria and the graph traversal path behind the score, alongside the existing LLM-generated prose summary.
- **Frontend**: full rewrite from Streamlit to a React SPA calling the existing (extended) FastAPI backend directly — no new backend runtime is introduced. Preserves the current structured-input/free-text dual entry modes, demo buttons, and ReKnoS toggle; adds physician login, patient management, and match-history screens; redesigned for a cleaner, more visual presentation with match-confidence indicators.
- **Data pipeline**: a single new pipeline-runner entry point sequences fetch → transform → Cortex parse → export → graph-load. It adds incremental behavior: filters ClinicalTrials.gov fetches by `lastUpdatePostDate` since the last recorded run, reuses the existing parsing-progress tracking tables to skip already-parsed trials, and switches the graph-load step to `MERGE`-based Cypher instead of `CREATE`-based. It also adds logic to detect trials whose status has moved outside `RECRUITING`/`ACTIVE_NOT_RECRUITING` and exclude them from the graph (ADR 0008).
- **Scheduling**: a GitHub Actions workflow on a monthly cron trigger invokes the pipeline-runner entry point directly, authenticating to Snowflake/Neo4j Aura via repository secrets — no orchestrator service runs between refreshes (ADR 0007).
- **Infrastructure**: new Snowflake account; new Neo4j AuraDB instance (schema unchanged); new managed Postgres instance for physician/patient data; Auth0 tenant for authentication; Cloud Run for the API; Cloudflare Pages or Vercel for the React frontend (ADR 0004).
- **Data policy**: no encryption-at-rest or audit-logging work is added; the system is documented (ADR 0001) as handling synthetic/de-identified patient data only.

## Testing Decisions

- Good tests here assert externally observable behavior — HTTP responses, pipeline outputs, graph/query results reachable only through the API — not internal implementation details, consistent with the existing test suite's style.
- **Accounts, patient CRUD, match history, and match-explanation fields** are tested through the existing API seam: `httpx.AsyncClient` + `ASGITransport` against the FastAPI app, the same pattern `tests/test_api.py` already uses for `/match` and `/patient/parse`. Authenticated-request fixtures are added to `tests/conftest.py` alongside the existing `patient_*` fixtures. A new `requires_postgres`-style skip guard, mirroring the existing `requires_snowflake`/`requires_neo4j` guards, gates any test needing a live Postgres instance.
- **Match-explanation** changes are verified by asserting the extended `/match` response includes the new matched/excluded-criteria and graph-path fields, alongside the score/rank assertions `test_api.py` already makes.
- **The monthly refresh pipeline** gets its own integration tests against the new pipeline-runner entry point: given a fixture set of ClinicalTrials.gov API responses (including one trial with an updated `lastUpdatePostDate` and one whose status has moved out of scope), assert that only the changed trial is re-fetched/re-parsed, that the graph reflects a merge rather than a full replace, and that the aged-out trial no longer appears in subsequent match results. Prior art: `tests/test_snowflake.py`'s schema/table-existence assertions and its `pytest.mark.skipif`-based guard pattern.
- **Frontend** tests are component/integration tests (render + user interaction + assertion) against a mocked API client. There's no existing JS test tooling in this repo, so this establishes the pattern for future frontend work rather than following prior art.
- **Deployment/infrastructure** changes are not covered by automated tests; verified manually via smoke-test after each deploy.

## Out of Scope

- Real PHI/HIPAA-grade compliance (encryption at rest, audit logging, BAA-eligible hosting) — deferred per ADR 0001.
- Shared or cross-physician patient visibility, or an admin role — rejected per ADR 0005.
- Representing Patients in the Neo4j graph — rejected per ADR 0006.
- Redesigning the Neo4j graph schema or the underlying matching/scoring algorithm.
- Apache Airflow or any always-on orchestrator for the refresh pipeline — rejected per ADR 0007.
- General codebase restructuring for readability — a stated goal, but tracked separately from this spec.
- Patient-facing accounts or logins — only Physicians get accounts here.
- Any features beyond what's listed above; further improvements were mentioned but explicitly deferred.

## Further Notes

- This spec bundles several previously separate efforts (data migration, Neo4j rebuild, frontend rewrite, physician accounts, explainability, monthly pipeline, deployment) that were deliberately grilled together in one session. `/to-tickets` should split this into tracer-bullet tickets with explicit blocking edges — e.g. the Snowflake/Neo4j migration and initial full load likely blocks the monthly incremental-refresh work and the frontend's ability to demo against real data, while physician-account/Postgres work can largely proceed in parallel with the data migration.
- Assumption to confirm during implementation: the current Streamlit app's demo buttons, ReKnoS toggle, and dual structured/free-text input modes are assumed to carry over into the React rewrite as feature parity, since removing them was never discussed. Flag if that's not the intent.
- Neo4j AuraDB's free-tier node/relationship limits still need direct verification against the actual graph size (flagged in ADR 0004) before committing to a specific Aura tier.
- Relevant background: `CONTEXT.md` (project glossary: Physician, Patient, Match Run, Match Explanation) and `docs/adr/0001`–`0008`, both produced during the `/grill-with-docs` session that preceded this spec.
