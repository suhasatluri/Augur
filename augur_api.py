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
from fastapi import Body, FastAPI, HTTPException, Header, Query, Request, Response
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
    allow_headers=["X-API-Key", "Content-Type", "X-Admin-Secret"],
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
# Earnings Calendar
# ---------------------------------------------------------------------------


@app.get("/calendar")
async def earnings_calendar(
    weeks: int = Query(26, ge=1, le=52),
    sector: Optional[str] = Query(None),
    show_past: bool = Query(False),
    search: Optional[str] = Query(None),
):
    """Upcoming ASX earnings dates. Public — no auth required.

    Returns entries grouped by date for the full /calendar page.
    Query params: weeks (default 26), sector filter, show_past (include last 30 days), search (ticker/company).
    """
    if not os.getenv("DATABASE_URL"):
        return {"calendar": {}, "sectors": [], "total_companies": 0, "last_updated": None, "disclaimer": ""}

    search_pattern = f"%{search}%" if search else None

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT c.ticker, c.expected_reporting_date, c.result_type,
                       c.confirmed, c.source, c.confidence, c.last_verified,
                       a.company_name, a.sector
                FROM asx_calendar c
                LEFT JOIN asx_companies a ON c.ticker = a.ticker
                WHERE (
                    ($3::boolean = TRUE AND c.expected_reporting_date >= CURRENT_DATE - 30)
                    OR
                    ($3::boolean = FALSE AND c.expected_reporting_date >= CURRENT_DATE)
                )
                AND c.expected_reporting_date <= CURRENT_DATE + ($1 * 7)
                AND ($2::text IS NULL OR a.sector = $2)
                AND ($4::text IS NULL OR c.ticker ILIKE $4 OR a.company_name ILIKE $4)
                ORDER BY c.expected_reporting_date ASC, c.ticker ASC
            """, weeks, sector, show_past, search_pattern)

            # Get distinct sectors for filter pills
            sector_rows = await conn.fetch("""
                SELECT DISTINCT a.sector FROM asx_calendar c
                JOIN asx_companies a ON c.ticker = a.ticker
                WHERE a.sector IS NOT NULL
                AND c.expected_reporting_date >= CURRENT_DATE
                ORDER BY a.sector
            """)

            last_verified = await conn.fetchval("""
                SELECT MAX(last_verified) FROM asx_calendar
                WHERE expected_reporting_date >= CURRENT_DATE
            """)
    except Exception as e:
        logger.error(f"[api] Calendar query failed: {e}")
        return {"calendar": {}, "sectors": [], "total_companies": 0, "last_updated": None, "disclaimer": ""}

    # Group by date
    calendar: dict[str, list[dict]] = {}
    for r in rows:
        date_key = r["expected_reporting_date"].isoformat()
        entry = {
            "ticker": r["ticker"],
            "company": r["company_name"] or f"{r['ticker']} Ltd",
            "report_type": r["result_type"],
            "sector": r["sector"],
            "source": r["source"],
            "confidence": r["confidence"] or "medium",
        }
        calendar.setdefault(date_key, []).append(entry)

    return {
        "calendar": calendar,
        "sectors": [r["sector"] for r in sector_rows],
        "total_companies": len(rows),
        "last_updated": last_verified.isoformat() if last_verified else None,
        "disclaimer": "Dates are sourced from Yahoo Finance and Perplexity Sonar. Always verify with the company's official ASX announcement.",
    }


# ---------------------------------------------------------------------------
# Admin Dashboard
# ---------------------------------------------------------------------------

_ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")
_admin_cache: dict[str, dict] = {}  # keyed by from_ts+to_ts


@app.get("/admin/stats")
async def admin_stats(
    x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret"),
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
):
    """Protected admin stats. Accepts ISO timestamps for time range filtering. 60s cache per range."""
    if not _ADMIN_SECRET or x_admin_secret != _ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    from datetime import timezone, timedelta
    import decimal

    # Parse time range — default last 30 days
    try:
        dt_from = datetime.fromisoformat(from_ts) if from_ts else datetime.now(timezone.utc) - timedelta(days=30)
        dt_to = datetime.fromisoformat(to_ts) if to_ts else datetime.now(timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid timestamp format. Use ISO 8601.")

    # Ensure timezone-aware
    if dt_from.tzinfo is None:
        dt_from = dt_from.replace(tzinfo=timezone.utc)
    if dt_to.tzinfo is None:
        dt_to = dt_to.replace(tzinfo=timezone.utc)

    # Cache check (per range key, 60s TTL)
    cache_key = f"{dt_from.isoformat()}_{dt_to.isoformat()}"
    cached = _admin_cache.get(cache_key)
    if cached and time.monotonic() < cached.get("_expires", 0):
        return cached["data"]

    def _row(row):
        return {k: (float(v) if isinstance(v, decimal.Decimal) else str(v) if hasattr(v, "isoformat") else v) for k, v in dict(row).items()}

    pool = await get_pool()

    async def _fetchrow(sql, *args):
        async with pool.acquire() as c:
            return await c.fetchrow(sql, *args)

    async def _fetch(sql, *args):
        async with pool.acquire() as c:
            return await c.fetch(sql, *args)

    # Date-filtered queries use $1/$2 params; tokens/recent/feedback are global
    totals, daily, top_tickers, token_breakdown, recent, feedback = await asyncio.gather(
        _fetchrow("""
            SELECT COUNT(*) AS total_simulations, COUNT(DISTINCT ticker) AS unique_tickers,
                   COALESCE(SUM(estimated_cost_usd), 0) + COALESCE(SUM(perplexity_cost_usd), 0) AS total_cost_usd,
                   COALESCE(SUM(input_tokens_sonnet + output_tokens_sonnet), 0) AS total_sonnet_tokens,
                   COALESCE(SUM(input_tokens_haiku + output_tokens_haiku), 0) AS total_haiku_tokens,
                   COALESCE(AVG(duration_seconds), 0) AS avg_duration_s,
                   COALESCE(AVG(estimated_cost_usd + COALESCE(perplexity_cost_usd, 0)), 0) AS avg_cost_usd,
                   COALESCE(AVG(seed_quality), 0) AS avg_seed_quality,
                   COUNT(*) FILTER (WHERE status = 'complete') AS completed,
                   COUNT(*) FILTER (WHERE status = 'failed') AS failed
            FROM simulations WHERE created_at >= $1 AND created_at <= $2""", dt_from, dt_to),
        _fetch("""
            SELECT DATE(created_at) AS date, COUNT(*) AS simulations,
                   COALESCE(SUM(estimated_cost_usd), 0) AS cost_usd
            FROM simulations WHERE created_at >= $1 AND created_at <= $2
            GROUP BY DATE(created_at) ORDER BY date DESC""", dt_from, dt_to),
        _fetch("""
            SELECT ticker, COUNT(*) AS simulations,
                   COALESCE(SUM(estimated_cost_usd), 0) AS total_cost,
                   COALESCE(AVG(estimated_cost_usd), 0) AS avg_cost,
                   COALESCE(AVG(seed_quality), 0) AS avg_quality,
                   MAX(created_at) AS last_run
            FROM simulations WHERE status = 'complete' AND created_at >= $1 AND created_at <= $2
            GROUP BY ticker ORDER BY simulations DESC LIMIT 20""", dt_from, dt_to),
        _fetchrow("""
            SELECT COALESCE(SUM(input_tokens_sonnet), 0) AS sonnet_in,
                   COALESCE(SUM(output_tokens_sonnet), 0) AS sonnet_out,
                   COALESCE(SUM(input_tokens_haiku), 0) AS haiku_in,
                   COALESCE(SUM(output_tokens_haiku), 0) AS haiku_out,
                   COALESCE(SUM(perplexity_requests), 0) AS perplexity_requests,
                   COALESCE(SUM(perplexity_prompt_tokens), 0) AS perplexity_prompt_tokens,
                   COALESCE(SUM(perplexity_completion_tokens), 0) AS perplexity_completion_tokens,
                   COALESCE(SUM(perplexity_cost_usd), 0) AS perplexity_cost_usd
            FROM simulations WHERE created_at >= $1 AND created_at <= $2""", dt_from, dt_to),
        _fetch("""
            SELECT ticker, status, estimated_cost_usd,
                   COALESCE(input_tokens_sonnet, 0) + COALESCE(output_tokens_sonnet, 0) AS sonnet_tokens,
                   COALESCE(input_tokens_haiku, 0) + COALESCE(output_tokens_haiku, 0) AS haiku_tokens,
                   COALESCE(perplexity_cost_usd, 0) AS perplexity_cost,
                   duration_seconds, seed_quality, convergence_score, rounds_completed, created_at
            FROM simulations WHERE created_at >= $1 AND created_at <= $2
            ORDER BY created_at DESC LIMIT 50""", dt_from, dt_to),
        _fetchrow("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE rating = 'positive') AS positive,
                   COUNT(*) FILTER (WHERE rating = 'negative') AS negative,
                   COUNT(*) FILTER (WHERE rating = 'neutral') AS unsure
            FROM feedback"""),
    )

    response = {
        "totals": _row(totals),
        "token_breakdown": _row(token_breakdown),
        "daily": [_row(r) for r in daily],
        "top_tickers": [_row(r) for r in top_tickers],
        "recent": [_row(r) for r in recent],
        "feedback": _row(feedback),
        "range": {"from": dt_from.isoformat(), "to": dt_to.isoformat()},
        "cached_at": datetime.utcnow().isoformat(),
    }

    _admin_cache[cache_key] = {"data": response, "_expires": time.monotonic() + 60.0}
    # Prune old cache entries
    if len(_admin_cache) > 20:
        oldest = min(_admin_cache, key=lambda k: _admin_cache[k].get("_expires", 0))
        _admin_cache.pop(oldest, None)

    return response


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def _decode_row(row) -> dict:
    """Convert asyncpg Record to JSON-friendly dict."""
    import decimal
    out = {}
    for k, v in dict(row).items():
        if isinstance(v, decimal.Decimal):
            out[k] = float(v)
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


@app.get("/admin/calibration")
async def admin_calibration(
    x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret"),
):
    """Calibration tracker — Augur predictions vs actual outcomes."""
    if not _ADMIN_SECRET or x_admin_secret != _ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    pool = await get_pool()
    async with pool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT
                COUNT(*) AS total_predictions,
                COUNT(*) FILTER (WHERE actual_beat IS NOT NULL) AS validated,
                COUNT(*) FILTER (
                    WHERE actual_beat IS NULL AND report_date >= CURRENT_DATE
                ) AS pending_future,
                COUNT(*) FILTER (
                    WHERE actual_beat IS NULL AND report_date < CURRENT_DATE
                ) AS awaiting_result,
                ROUND(AVG(brier_score)::numeric, 4) AS avg_brier_score,
                COUNT(*) FILTER (
                    WHERE (actual_beat = TRUE AND augur_probability >= 0.5)
                    OR (actual_beat = FALSE AND augur_probability < 0.5)
                ) AS correct_direction,
                COUNT(*) FILTER (WHERE actual_beat IS NOT NULL) AS total_scored
            FROM calibration
        """)

        pending = await conn.fetch("""
            SELECT ticker, report_date, days_before_report,
                   augur_probability, augur_verdict, simulated_at
            FROM calibration
            WHERE actual_beat IS NULL AND report_date >= CURRENT_DATE
            ORDER BY report_date ASC
            LIMIT 30
        """)

        validated = await conn.fetch("""
            SELECT ticker, report_date, augur_probability, augur_verdict,
                   actual_beat, actual_eps, consensus_eps, eps_surprise_pct,
                   brier_score, result_source, days_before_report
            FROM calibration
            WHERE actual_beat IS NOT NULL
            ORDER BY report_date DESC
            LIMIT 50
        """)

        buckets = await conn.fetch("""
            SELECT ROUND(augur_probability * 10) / 10 AS probability_bucket,
                   COUNT(*) AS count,
                   AVG(CASE WHEN actual_beat THEN 1.0 ELSE 0.0 END) AS actual_beat_rate,
                   AVG(brier_score) AS avg_brier
            FROM calibration
            WHERE actual_beat IS NOT NULL
            GROUP BY probability_bucket
            ORDER BY probability_bucket
        """)

    total_scored = stats["total_scored"] or 0
    correct = stats["correct_direction"] or 0
    accuracy = round(correct / total_scored * 100, 1) if total_scored > 0 else None

    return {
        "summary": {
            **_decode_row(stats),
            "accuracy_pct": accuracy,
            "random_baseline_brier": 0.25,
        },
        "pending": [_decode_row(r) for r in pending],
        "validated": [_decode_row(r) for r in validated],
        "calibration_curve": [_decode_row(r) for r in buckets],
    }


@app.post("/admin/calibration/{ticker}/result")
async def set_calibration_result(
    ticker: str,
    x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret"),
    actual_beat: bool = Body(...),
    actual_eps: Optional[float] = Body(None),
    consensus_eps: Optional[float] = Body(None),
    notes: Optional[str] = Body(None),
):
    """Manual override for cases where yfinance is wrong or unavailable."""
    if not _ADMIN_SECRET or x_admin_secret != _ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, augur_probability
            FROM calibration
            WHERE ticker = $1 AND actual_beat IS NULL
            ORDER BY report_date DESC
            LIMIT 1
        """, ticker.upper())

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"{ticker} not found in calibration or already scored",
            )

        p = float(row["augur_probability"])
        outcome = 1.0 if actual_beat else 0.0
        brier = round((p - outcome) ** 2, 6)

        eps_surprise = None
        if actual_eps is not None and consensus_eps is not None and consensus_eps != 0:
            eps_surprise = round(
                (actual_eps - consensus_eps) / abs(consensus_eps) * 100, 4
            )

        await conn.execute("""
            UPDATE calibration SET
                actual_beat        = $1,
                actual_eps         = $2,
                consensus_eps      = $3,
                eps_surprise_pct   = $4,
                result_source      = 'manual',
                result_verified_at = NOW(),
                brier_score        = $5,
                notes              = $6
            WHERE id = $7
        """, actual_beat, actual_eps, consensus_eps, eps_surprise, brier, notes, row["id"])

    return {"ticker": ticker.upper(), "actual_beat": actual_beat, "brier_score": brier}
