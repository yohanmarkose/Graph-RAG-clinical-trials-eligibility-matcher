## Parent

Part of #3

## What to build

Extend the match response with a concrete, verifiable explanation: the structured list of matched/excluded criteria and the graph traversal path behind a trial's score, alongside the existing LLM-generated prose explanation.

## Acceptance criteria

- [ ] The match response includes a structured list of matched and excluded criteria behind each trial's score
- [ ] The match response includes the graph traversal path that produced the score
- [ ] The existing LLM-generated prose explanation is retained alongside the new structured fields
- [ ] `tests/test_api.py`'s `/match` tests are extended to assert the new fields are present and well-formed

## Blocked by

None (can start immediately)
