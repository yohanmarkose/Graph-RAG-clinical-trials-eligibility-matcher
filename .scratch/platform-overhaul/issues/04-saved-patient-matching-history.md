## Parent

Part of #3

## What to build

Let a physician run a match directly against a saved Patient, and record each run as a Match Run: a timestamped snapshot (not a live reference) of the top results, so a patient's match history stays coherent even after a referenced trial later disappears from the graph, per the Match Run definition in `CONTEXT.md` and ADR 0006.

## Acceptance criteria

- [ ] An authenticated physician can trigger a match run directly against one of their saved Patients
- [ ] Each match run is persisted as a Match Run record: timestamp + a snapshot (trial id, title, score, key match reasons) of the top results
- [ ] A physician can retrieve the match-run history for a given Patient, ordered by date
- [ ] Match-run history remains readable for a trial that has since been excluded from the graph (no broken/missing references)
- [ ] New endpoints are tested via the same API seam as the existing match tests, reusing the accounts fixtures from #2

## Blocked by

- #5 (Physician accounts: sign up, log in, manage own patients)
