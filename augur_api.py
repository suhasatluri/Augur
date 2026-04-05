"""Augur API — FastAPI application for ASX earnings prediction."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from monitoring.grafana import (
    setup_loki_logging,
    api_requests_total,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

load_dotenv()

from asx200 import is_valid_asx_ticker
from db.schema import get_pool, ensure_schema
from pipeline import run_full_pipeline
from prediction_synthesiser.models import DISCLAIMER

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Augur API",
    description="ASX Earnings Surprise Predictor — Swarm Intelligence Platform",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://augur.vercel.app",
        "http://localhost:3000",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["X-API-Key", "Content-Type"],
)


@app.middleware("http")
async def track_requests(request: Request, call_next):
    response = await call_next(request)
    api_requests_total.labels(
        endpoint=request.url.path,
        method=request.method,
        status=str(response.status_code),
    ).inc()
    return response

# ---------------------------------------------------------------------------
# In-memory state (V1 — Redis in V2)
# ---------------------------------------------------------------------------

# Background jobs: job_id → {"status", "simulation_id", "ticker", "task", "result", "error"}
_jobs: dict[str, dict] = {}

# Rate limiting: api_key → list of timestamps
_rate_limits: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 3600  # 1 hour

# Valid API keys (loaded from env, comma-separated)
_api_keys: set[str] = set()


def _load_api_keys() -> None:
    raw = os.getenv("AUGUR_API_KEYS", "")
    if raw:
        _api_keys.update(k.strip() for k in raw.split(",") if k.strip())
    # If no keys configured, use a default dev key
    if not _api_keys:
        _api_keys.add("augur-dev-key")
        logger.warning("[api] No AUGUR_API_KEYS set — using default dev key: augur-dev-key")


# ---------------------------------------------------------------------------
# Auth + Rate Limiting
# ---------------------------------------------------------------------------

def _check_api_key(api_key: Optional[str]) -> str:
    """Validate API key. Returns the key or raises 401."""
    if not api_key or api_key not in _api_keys:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
    return api_key


def _check_rate_limit(api_key: str) -> None:
    """Enforce rate limit. Raises 429 if exceeded."""
    now = time.time()
    # Prune old entries
    _rate_limits[api_key] = [
        t for t in _rate_limits[api_key] if now - t < RATE_LIMIT_WINDOW
    ]
    if len(_rate_limits[api_key]) >= RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {RATE_LIMIT_MAX} simulations per hour",
        )
    _rate_limits[api_key].append(now)


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class SimulateRequest(BaseModel):
    ticker: str
    reporting_date: str = ""


class SimulateResponse(BaseModel):
    job_id: str
    simulation_id: str
    status: str = "queued"
    estimated_minutes: int = 3
    disclaimer: str = DISCLAIMER


class SimulationStatusResponse(BaseModel):
    job_id: str
    simulation_id: str
    ticker: str
    status: str
    reporting_date: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    disclaimer: str = DISCLAIMER


class SimulationListItem(BaseModel):
    simulation_id: str
    ticker: str
    status: str
    verdict: Optional[str] = None
    created_at: Optional[str] = None
    disclaimer: str = DISCLAIMER


class HealthResponse(BaseModel):
    status: str
    neon_connected: bool
    active_jobs: int
    version: str = "1.0.0"


# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------

async def _run_job(job_id: str, simulation_id: str, ticker: str, reporting_date: str) -> None:
    """Background task: run the full pipeline and update job state."""
    _jobs[job_id]["status"] = "running"
    pool = await get_pool()

    try:
        # Update simulation status
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE simulations SET status = 'pending' WHERE id = $1",
                simulation_id,
            )

        report = await run_full_pipeline(simulation_id, ticker, reporting_date)

        _jobs[job_id]["status"] = "complete"
        _jobs[job_id]["result"] = report.model_dump(mode="json")
        logger.info(f"[api] Job {job_id} complete — verdict: {report.verdict}")

    except Exception as e:
        logger.exception(f"[api] Job {job_id} failed: {e}")
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)

        # Update simulation status to failed
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE simulations SET status = 'failed' WHERE id = $1",
                    simulation_id,
                )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

_schema_ensured = False


async def _ensure_schema_once() -> None:
    """Lazy schema setup — called on first request that needs DB, not on startup."""
    global _schema_ensured
    if _schema_ensured or not os.getenv("DATABASE_URL"):
        return
    try:
        await ensure_schema()
        _schema_ensured = True
        logger.info("[api] Neon schema ensured (lazy)")
    except Exception as e:
        logger.error(f"[api] Failed to ensure schema: {e}")


async def _cleanup_stale_simulations() -> None:
    """Mark simulations stuck in 'running' for >10 minutes as 'failed'."""
    if not os.getenv("DATABASE_URL"):
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE simulations
                SET status = 'failed', completed_at = NOW()
                WHERE status IN ('running', 'negotiating', 'forging')
                  AND created_at < NOW() - INTERVAL '10 minutes'
            """)
            if result and result != "UPDATE 0":
                logger.info(f"[api] Stale simulation cleanup: {result}")
    except Exception as e:
        logger.warning(f"[api] Stale cleanup failed: {e}")


@app.on_event("startup")
async def startup() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    setup_loki_logging()
    _load_api_keys()
    await _cleanup_stale_simulations()
    logger.info("[api] App started — schema will be ensured on first DB request")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/simulate", response_model=SimulateResponse)
async def simulate(
    body: SimulateRequest,
    x_api_key: Optional[str] = Header(None),
):
    """Start a new earnings prediction simulation."""
    await _ensure_schema_once()
    key = _check_api_key(x_api_key)
    _check_rate_limit(key)

    ticker = body.ticker.upper().strip()
    if not is_valid_asx_ticker(ticker):
        raise HTTPException(
            status_code=422,
            detail=f"'{ticker}' is not in the ASX 200 list. Only ASX 200 tickers are supported.",
        )

    simulation_id = f"sim-{uuid.uuid4().hex[:8]}"
    job_id = f"job-{uuid.uuid4().hex[:8]}"

    # Parse reporting_date
    reporting_date_val = None
    if body.reporting_date:
        try:
            reporting_date_val = date.fromisoformat(body.reporting_date)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid reporting_date format: '{body.reporting_date}'. Use YYYY-MM-DD.",
            )

    # Create simulation record in Neon
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO simulations (id, ticker, reporting_date, status)
                   VALUES ($1, $2, $3, 'pending')""",
                simulation_id,
                ticker,
                reporting_date_val,
            )
    except Exception as e:
        logger.error(f"[api] Failed to create simulation: {e}")
        raise HTTPException(status_code=500, detail="Failed to create simulation record")

    # Register job and launch background task
    _jobs[job_id] = {
        "status": "queued",
        "simulation_id": simulation_id,
        "ticker": ticker,
        "reporting_date": body.reporting_date or None,
        "result": None,
        "error": None,
    }

    asyncio.create_task(_run_job(job_id, simulation_id, ticker, body.reporting_date))

    return SimulateResponse(
        job_id=job_id,
        simulation_id=simulation_id,
        status="queued",
        estimated_minutes=3,
    )


@app.get("/simulation/{job_id}", response_model=SimulationStatusResponse)
async def get_simulation(
    job_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """Get simulation status and results."""
    _check_api_key(x_api_key)

    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return SimulationStatusResponse(
        job_id=job_id,
        simulation_id=job["simulation_id"],
        ticker=job["ticker"],
        status=job["status"],
        reporting_date=job.get("reporting_date"),
        result=job.get("result"),
        error=job.get("error"),
    )


@app.get("/simulations", response_model=list[SimulationListItem])
async def list_simulations(
    x_api_key: Optional[str] = Header(None),
):
    """List last 10 simulations."""
    _check_api_key(x_api_key)

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, ticker, status, created_at
                   FROM simulations
                   ORDER BY created_at DESC
                   LIMIT 10"""
            )
    except Exception as e:
        logger.error(f"[api] Failed to list simulations: {e}")
        raise HTTPException(status_code=500, detail="Database query failed")

    items = []
    for r in rows:
        # Try to get verdict from in-memory jobs
        verdict = None
        for job in _jobs.values():
            if job["simulation_id"] == r["id"] and job.get("result"):
                verdict = job["result"].get("verdict")
                break

        items.append(SimulationListItem(
            simulation_id=r["id"],
            ticker=r["ticker"],
            status=r["status"],
            verdict=verdict,
            created_at=r["created_at"].isoformat() if r["created_at"] else None,
        ))

    return items


class ActivityItem(BaseModel):
    ticker: str
    count: int
    last_verdict: Optional[str] = None


@app.get("/activity", response_model=list[ActivityItem])
async def activity(period: str = "today"):
    """Top tickers by simulation count. Public — no auth required.

    Query param: period = "today" (default) or "week".
    """
    if not os.getenv("DATABASE_URL"):
        return []

    cutoff = "CURRENT_DATE" if period != "week" else "CURRENT_DATE - INTERVAL '7 days'"

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(f"""
                SELECT s.ticker,
                       COUNT(*) as count,
                       (SELECT s2.status FROM simulations s2
                        WHERE s2.ticker = s.ticker
                        ORDER BY s2.created_at DESC LIMIT 1) as last_status
                FROM simulations s
                WHERE s.created_at >= {cutoff}
                GROUP BY s.ticker
                ORDER BY count DESC
                LIMIT 6
            """)
    except Exception as e:
        logger.error(f"[api] Activity query failed: {e}")
        return []

    items = []
    for r in rows:
        # Try to get verdict from in-memory jobs for the latest sim of this ticker
        verdict = None
        for job in reversed(list(_jobs.values())):
            if job["ticker"] == r["ticker"] and job.get("result"):
                verdict = job["result"].get("verdict")
                break

        items.append(ActivityItem(
            ticker=r["ticker"],
            count=r["count"],
            last_verdict=verdict,
        ))

    return items


@app.get("/metrics")
async def metrics(request: Request):
    """Prometheus metrics endpoint for Grafana scraping. Requires bearer token."""
    auth = request.headers.get("Authorization", "")
    api_key_header = request.headers.get("X-Metrics-Token", "")

    expected_token = os.getenv("METRICS_SCRAPE_TOKEN")

    if not expected_token:
        raise HTTPException(status_code=403, detail="Metrics endpoint not configured")

    bearer = auth.replace("Bearer ", "").strip()
    provided = bearer or api_key_header.strip()

    if provided != expected_token:
        raise HTTPException(status_code=403, detail="Forbidden")

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check for Railway. Always returns 200 — even without DB."""
    neon_ok = False
    if os.getenv("DATABASE_URL"):
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            neon_ok = True
        except Exception:
            pass

    active = sum(1 for j in _jobs.values() if j["status"] in ("queued", "running"))

    return HealthResponse(
        status="ok" if neon_ok else "degraded",
        neon_connected=neon_ok,
        active_jobs=active,
    )
