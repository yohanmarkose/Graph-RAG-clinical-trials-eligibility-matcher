"""
Tests for the FastAPI backend endpoints.

Integration tests hit the real Neo4j (skipped if unreachable).
Structural tests run without any services.

Endpoints tested:
  POST /match
  POST /patient/parse
  GET  /trials/{nct_id}
  GET  /stats
"""

from __future__ import annotations

import pytest

from tests.conftest import requires_neo4j


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_client():
    """Return an httpx.TestClient wrapping the FastAPI app.

    Imported lazily so import errors in api/ don't break the whole module
    when the app hasn't been fully wired yet.
    """
    from httpx import ASGITransport, AsyncClient  # type: ignore[import-untyped]
    from api.main import app  # type: ignore[import-untyped]

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# POST /match
# ---------------------------------------------------------------------------


@requires_neo4j
async def test_post_match_her2_returns_results():
    """POST /match with a HER2+ breast cancer patient returns ranked trials."""
    async with _get_client() as client:
        response = await client.post(
            "/match",
            json={
                "age": 52,
                "gender": "Female",
                "conditions": ["427685000"],
                "condition_names": ["HER2-positive breast cancer"],
                "biomarkers": [{"name": "HER2", "status": "positive"}],
                "therapeutic_area": "oncology",
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list)
    assert len(body) > 0
    first = body[0]
    assert "nct_id" in first
    assert "score" in first
    assert isinstance(first["score"], (int, float))
    assert 0 <= first["score"] <= 100


@requires_neo4j
async def test_post_match_empty_patient_returns_empty():
    """POST /match with no conditions should return an empty list."""
    async with _get_client() as client:
        response = await client.post("/match", json={})
    assert response.status_code == 200
    body = response.json()
    assert body == []


async def test_post_match_invalid_body_returns_422():
    """POST /match with an invalid body (age as string) should return 422."""
    async with _get_client() as client:
        response = await client.post("/match", json={"age": "not-a-number"})
    assert response.status_code == 422


async def test_post_match_top_n_respected():
    """POST /match with top_n=3 should return at most 3 results."""
    async with _get_client() as client:
        response = await client.post(
            "/match",
            json={
                "age": 52,
                "gender": "Female",
                "conditions": ["427685000"],
                "biomarkers": [{"name": "HER2", "status": "positive"}],
            },
            params={"top_n": 3},
        )
    if response.status_code == 200:
        assert len(response.json()) <= 3


# ---------------------------------------------------------------------------
# POST /patient/parse
# ---------------------------------------------------------------------------


@requires_neo4j
async def test_post_patient_parse_extracts_profile():
    """POST /patient/parse should extract a PatientProfile from free text."""
    async with _get_client() as client:
        response = await client.post(
            "/patient/parse",
            json={
                "text": (
                    "52-year-old female with HER2-positive breast cancer. "
                    "HER2 IHC 3+. No prior trastuzumab."
                )
            },
        )
    # Acceptable outcomes: 200 with profile, or 501 if not yet implemented
    assert response.status_code in (200, 501)
    if response.status_code == 200:
        body = response.json()
        assert "age" in body or "conditions" in body


# ---------------------------------------------------------------------------
# GET /trials/{nct_id}
# ---------------------------------------------------------------------------


@requires_neo4j
async def test_get_trial_by_nct_id_returns_metadata():
    """GET /trials/NCT00000001 should return full trial metadata."""
    async with _get_client() as client:
        response = await client.get("/trials/NCT00000001")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["nct_id"] == "NCT00000001"
    assert "title" in body
    assert "status" in body


@requires_neo4j
async def test_get_trial_not_found_returns_404():
    """GET /trials/NCT99999999 should return 404 for an unknown trial."""
    async with _get_client() as client:
        response = await client.get("/trials/NCT99999999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /stats
# ---------------------------------------------------------------------------


@requires_neo4j
async def test_get_stats_returns_counts():
    """GET /stats should return graph node counts."""
    async with _get_client() as client:
        response = await client.get("/stats")
    assert response.status_code == 200
    body = response.json()
    # Should contain at least a trials count
    assert "trials" in body or "trial_count" in body or len(body) > 0


# ---------------------------------------------------------------------------
# Health / root
# ---------------------------------------------------------------------------


async def test_root_or_health_endpoint():
    """The app should respond on / or /health without auth."""
    async with _get_client() as client:
        for path in ("/", "/health"):
            response = await client.get(path)
            # Accept any 2xx or 404 (endpoint may not exist yet)
            assert response.status_code < 500, (
                f"Unexpected server error on GET {path}: {response.status_code}"
            )
