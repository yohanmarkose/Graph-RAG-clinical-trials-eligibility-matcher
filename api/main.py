"""FastAPI application entry point."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.dependencies import get_driver, lifespan
from api.routes import match, patient, stats, trials

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Clinical Trial Eligibility Matcher API",
    version="2.0.0",
    description=(
        "Match patients to clinical trials using Neo4j graph traversal, "
        "SNOMED/RxNorm entity linking, and LLM-powered scoring."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(match.router)
app.include_router(trials.router)
app.include_router(patient.router)
app.include_router(stats.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/", tags=["health"])
async def health_check(request: Request):
    """Verify API is running and Neo4j is reachable."""
    try:
        driver = get_driver(request)
        await driver.verify_connectivity()
        neo4j_status = "connected"
    except Exception:
        neo4j_status = "unreachable"
    return {"status": "ok", "neo4j": neo4j_status}


# ---------------------------------------------------------------------------
# Global error handler
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception at %s", request.url)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
