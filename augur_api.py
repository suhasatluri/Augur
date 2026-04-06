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

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

load_dotenv()

# Sentry error tracking
_sentry_dsn = os.getenv("SENTRY_DSN_BACKEND")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[
            FastApiIntegration(transaction_style="url"),
            AsyncioIntegration(),
            LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR),
        ],
        traces_sample_rate=0.1,
        release=os.getenv("RAILWAY_GIT_COMMIT_SHA", "unknown")[:8],
        environment=os.getenv("RAILWAY_ENVIRONMENT", "production"),
        send_default_pii=False,
    )

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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
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


class FeedbackRequest(BaseModel):
    rating: str
    comment: Optional[str] = None
    email: Optional[str] = None
    simulation_id: Optional[str] = None
    ticker: Optional[str] = None
    verdict: Optional[str] = None
    page: Optional[str] = "results"


@app.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    """Accept user feedback from the frontend. No auth required."""
    if req.rating not in ("positive", "negative", "neutral"):
        raise HTTPException(status_code=400, detail="Invalid rating value")

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO feedback (simulation_id, ticker, verdict, rating, comment, email, page)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                req.simulation_id,
                req.ticker,
                req.verdict,
                req.rating,
                req.comment,
                req.email,
                req.page,
            )
    except Exception as e:
        logger.error(f"[api] Failed to store feedback: {e}")
        raise HTTPException(status_code=500, detail="Failed to store feedback")

    logger.info("Feedback received", extra={"ticker": req.ticker, "rating": req.rating, "page": req.page})
    return {"status": "ok"}


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


# ---------------------------------------------------------------------------
# Admin Dashboard
# ---------------------------------------------------------------------------

_ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")
_admin_cache: dict = {"data": None, "expires_at": 0.0}

_SQL_TOTALS = """
    SELECT COUNT(*) AS total_simulations,
           COUNT(DISTINCT ticker) AS unique_tickers,
           COALESCE(SUM(estimated_cost_usd), 0) AS total_cost_usd,
           COALESCE(SUM(input_tokens_sonnet + output_tokens_sonnet), 0) AS total_sonnet_tokens,
           COALESCE(SUM(input_tokens_haiku + output_tokens_haiku), 0) AS total_haiku_tokens,
           COALESCE(AVG(duration_seconds), 0) AS avg_duration_s,
           COALESCE(AVG(estimated_cost_usd), 0) AS avg_cost_usd,
           COALESCE(AVG(seed_quality), 0) AS avg_seed_quality,
           COUNT(*) FILTER (WHERE status = 'complete') AS completed,
           COUNT(*) FILTER (WHERE status = 'failed') AS failed
    FROM simulations"""
_SQL_DAILY = """
    SELECT DATE(created_at) AS date, COUNT(*) AS simulations,
           COALESCE(SUM(estimated_cost_usd), 0) AS cost_usd
    FROM simulations WHERE created_at > NOW() - INTERVAL '30 days'
    GROUP BY DATE(created_at) ORDER BY date DESC"""
_SQL_TOP_TICKERS = """
    SELECT ticker, COUNT(*) AS simulations,
           COALESCE(SUM(estimated_cost_usd), 0) AS total_cost,
           COALESCE(AVG(estimated_cost_usd), 0) AS avg_cost,
           COALESCE(AVG(seed_quality), 0) AS avg_quality,
           MAX(created_at) AS last_run
    FROM simulations WHERE status = 'complete'
    GROUP BY ticker ORDER BY simulations DESC LIMIT 20"""
_SQL_TOKENS = """
    SELECT COALESCE(SUM(input_tokens_sonnet), 0) AS sonnet_in,
           COALESCE(SUM(output_tokens_sonnet), 0) AS sonnet_out,
           COALESCE(SUM(input_tokens_haiku), 0) AS haiku_in,
           COALESCE(SUM(output_tokens_haiku), 0) AS haiku_out
    FROM simulations"""
_SQL_RECENT = """
    SELECT ticker, status, estimated_cost_usd,
           COALESCE(input_tokens_sonnet, 0) + COALESCE(output_tokens_sonnet, 0) AS sonnet_tokens,
           COALESCE(input_tokens_haiku, 0) + COALESCE(output_tokens_haiku, 0) AS haiku_tokens,
           duration_seconds, seed_quality, convergence_score, rounds_completed, created_at
    FROM simulations ORDER BY created_at DESC LIMIT 50"""
_SQL_FEEDBACK = """
    SELECT COUNT(*) AS total,
           COUNT(*) FILTER (WHERE rating = 'positive') AS positive,
           COUNT(*) FILTER (WHERE rating = 'negative') AS negative,
           COUNT(*) FILTER (WHERE rating = 'neutral') AS unsure
    FROM feedback"""


@app.get("/admin/stats")
async def admin_stats(x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret")):
    """Protected admin stats endpoint. 60s in-memory cache, parallel queries."""
    if not _ADMIN_SECRET or x_admin_secret != _ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Cache check
    now = time.monotonic()
    if _admin_cache["data"] is not None and now < _admin_cache["expires_at"]:
        return _admin_cache["data"]

    import decimal

    def _row(row):
        return {k: (float(v) if isinstance(v, decimal.Decimal) else str(v) if hasattr(v, "isoformat") else v) for k, v in dict(row).items()}

    # Parallel queries via separate pool connections
    pool = await get_pool()

    async def _fetchrow(sql):
        async with pool.acquire() as c:
            return await c.fetchrow(sql)

    async def _fetch(sql):
        async with pool.acquire() as c:
            return await c.fetch(sql)

    totals, daily, top_tickers, token_breakdown, recent, feedback = await asyncio.gather(
        _fetchrow(_SQL_TOTALS),
        _fetch(_SQL_DAILY),
        _fetch(_SQL_TOP_TICKERS),
        _fetchrow(_SQL_TOKENS),
        _fetch(_SQL_RECENT),
        _fetchrow(_SQL_FEEDBACK),
    )

    response = {
        "totals": _row(totals),
        "token_breakdown": _row(token_breakdown),
        "daily": [_row(r) for r in daily],
        "top_tickers": [_row(r) for r in top_tickers],
        "recent": [_row(r) for r in recent],
        "feedback": _row(feedback),
        "cached_at": datetime.utcnow().isoformat(),
    }

    _admin_cache["data"] = response
    _admin_cache["expires_at"] = time.monotonic() + 60.0
    return response
