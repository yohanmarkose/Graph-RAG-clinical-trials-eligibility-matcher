## Parent

Part of #3

## What to build

Replace the Streamlit UI with a React SPA for the anonymous/demo flow, preserving today's functionality and adding the new concrete match-explanation display, calling the existing FastAPI backend directly (no new backend runtime).

## Acceptance criteria

- [ ] A React SPA replaces the Streamlit app for the anonymous/demo flow, calling the FastAPI backend
- [ ] Structured-input and free-text patient entry modes are both present
- [ ] The quick-demo buttons and ReKnoS multi-hop reasoning toggle are preserved
- [ ] Ranked match results display with visual match-confidence indicators, not just a numeric score
- [ ] The concrete match-explanation (matched/excluded criteria + graph path) is displayed alongside the LLM prose explanation
- [ ] Component/integration tests (render + interaction + assertion against a mocked API) cover the core input → match → results flow

## Blocked by

- #6 (Match explanation: concrete criteria + graph path alongside LLM prose)
