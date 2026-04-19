from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import AsyncGenerator

import pytest

# Make project root importable regardless of how pytest is invoked
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import get_settings
from matcher.patient_schema import BiomarkerStatus, PatientProfile


# ---------------------------------------------------------------------------
# Neo4j connectivity guard
# ---------------------------------------------------------------------------


def _neo4j_reachable() -> bool:
    """Return True if the configured Neo4j instance responds to a ping."""
    try:
        from neo4j import GraphDatabase  # type: ignore[import-untyped]

        cfg = get_settings()
        driver = GraphDatabase.driver(cfg.neo4j.uri, auth=(cfg.neo4j.user, cfg.neo4j.password))
        driver.verify_connectivity()
        driver.close()
        return True
    except Exception:
        return False


requires_neo4j = pytest.mark.skipif(
    not _neo4j_reachable(),
    reason="Neo4j not reachable — start with: docker-compose up -d",
)

requires_snowflake = pytest.mark.skipif(
    get_settings().snowflake is None,
    reason="Snowflake credentials not configured in .env",
)


# ---------------------------------------------------------------------------
# Neo4j driver fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
async def neo4j_driver():
    """Session-scoped async Neo4j driver.  Closes after all tests finish."""
    from neo4j import AsyncGraphDatabase  # type: ignore[import-untyped]

    cfg = get_settings()
    driver = AsyncGraphDatabase.driver(cfg.neo4j.uri, auth=(cfg.neo4j.user, cfg.neo4j.password))
    yield driver
    await driver.close()


# ---------------------------------------------------------------------------
# MatchEngine fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
async def match_engine(neo4j_driver):
    """Session-scoped MatchEngine backed by the live Neo4j driver."""
    from matcher.match_engine import MatchEngine

    return MatchEngine(neo4j_driver)


# ---------------------------------------------------------------------------
# Patient profile fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def patient_her2_breast() -> PatientProfile:
    """52-year-old woman with HER2-positive breast cancer, HER2+, no prior therapy."""
    return PatientProfile(
        age=52,
        gender="Female",
        conditions=["427685000"],          # HER2-positive breast cancer
        condition_names=["HER2-positive breast cancer"],
        biomarkers=[BiomarkerStatus(name="HER2", status="positive")],
        therapeutic_area="oncology",
    )


@pytest.fixture
def patient_her2_breast_prior_trastu() -> PatientProfile:
    """55-year-old woman with HER2+ breast cancer who already received trastuzumab."""
    return PatientProfile(
        age=55,
        gender="Female",
        conditions=["408643008"],          # Metastatic breast cancer
        condition_names=["Metastatic breast cancer"],
        biomarkers=[BiomarkerStatus(name="HER2", status="positive")],
        prior_therapies=["224905"],        # trastuzumab
        prior_therapy_names=["trastuzumab"],
        therapeutic_area="oncology",
    )


@pytest.fixture
def patient_nsclc_egfr() -> PatientProfile:
    """64-year-old man with EGFR-mutant NSCLC, no prior TKI."""
    return PatientProfile(
        age=64,
        gender="Male",
        conditions=["254637007"],          # NSCLC
        condition_names=["Non-small cell lung cancer"],
        biomarkers=[BiomarkerStatus(name="EGFR", status="positive")],
        therapeutic_area="oncology",
    )


@pytest.fixture
def patient_nsclc_pd_l1() -> PatientProfile:
    """70-year-old with NSCLC, PD-L1 high, EGFR wild-type."""
    return PatientProfile(
        age=70,
        gender="Male",
        conditions=["254637007"],
        condition_names=["Non-small cell lung cancer"],
        biomarkers=[
            BiomarkerStatus(name="PD-L1", status="positive"),
            BiomarkerStatus(name="EGFR",  status="negative"),
        ],
        therapeutic_area="oncology",
    )


@pytest.fixture
def patient_too_young() -> PatientProfile:
    """15-year-old with breast cancer — should be excluded from adult-only trials."""
    return PatientProfile(
        age=15,
        gender="Female",
        conditions=["254837009"],          # Breast cancer
        condition_names=["Breast cancer"],
        therapeutic_area="oncology",
    )


@pytest.fixture
def patient_empty() -> PatientProfile:
    """Patient with no conditions — should produce few or no matches."""
    return PatientProfile()


@pytest.fixture
def patient_cardiology() -> PatientProfile:
    """60-year-old with heart failure — should NOT match oncology trials."""
    return PatientProfile(
        age=60,
        gender="Male",
        conditions=["84114007"],           # Heart failure
        condition_names=["Heart failure"],
        therapeutic_area="cardiology",
    )
