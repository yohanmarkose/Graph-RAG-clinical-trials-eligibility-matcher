from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from neo4j import AsyncGraphDatabase

from config.settings import get_settings
from llm.explainer import MatchExplainer
from llm.provider import get_llm_provider
from matcher.match_engine import MatchEngine
from nlp.entity_linker import EntityLinker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Create shared resources on startup; close them on shutdown."""
    cfg = get_settings()

    logger.info("Connecting to Neo4j at %s …", cfg.neo4j.uri)
    driver = AsyncGraphDatabase.driver(
        cfg.neo4j.uri,
        auth=(cfg.neo4j.user, cfg.neo4j.password),
    )

    llm = get_llm_provider(cfg)
    linker = EntityLinker()

    # Conditionally initialise the ReKnoS candidate finder
    reknos_finder = None
    if cfg.reknos.enabled:
        try:
            from matcher.reknos_finder import ReKnoSCandidateFinder
            from matcher.super_relations import ClinicalTrialsKGInterface

            kg_interface = ClinicalTrialsKGInterface(driver)
            reknos_finder = ReKnoSCandidateFinder(
                kg=kg_interface,
                llm=llm,
                N=cfg.reknos.width,
                L=cfg.reknos.depth,
                use_stop_check=cfg.reknos.use_stop_check,
            )
            logger.info(
                "ReKnoS initialised (N=%d, L=%d, stop_check=%s)",
                cfg.reknos.width,
                cfg.reknos.depth,
                cfg.reknos.use_stop_check,
            )
        except Exception:
            logger.exception("Failed to initialise ReKnoS — continuing without it")

    engine = MatchEngine(driver, reknos_finder=reknos_finder)
    explainer = MatchExplainer(llm, entity_linker=linker)

    app.state.driver = driver
    app.state.match_engine = engine
    app.state.explainer = explainer
    app.state.entity_linker = linker

    logger.info("API startup complete.")
    yield

    logger.info("Shutting down — closing Neo4j driver …")
    await driver.close()


# ---------------------------------------------------------------------------
# Dependency functions
# ---------------------------------------------------------------------------


def get_driver(request: Request):
    return request.app.state.driver


def get_match_engine(request: Request) -> MatchEngine:
    return request.app.state.match_engine


def get_explainer(request: Request) -> MatchExplainer:
    return request.app.state.explainer


def get_entity_linker(request: Request) -> EntityLinker:
    return request.app.state.entity_linker
