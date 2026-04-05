"""Grafana Cloud observability — Loki logging + Prometheus metrics + remote push."""

import logging
import os
import threading
import time
import traceback
from functools import wraps

import requests as _requests

from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
    REGISTRY,
)

# ── Prometheus metrics ────────────────────────────────────────────────────────

simulation_total = Counter(
    "augur_simulations_total",
    "Total simulations run",
    ["ticker", "verdict", "cache_hit"],
)
simulation_duration = Histogram(
    "augur_simulation_duration_seconds",
    "Simulation duration in seconds",
    ["ticker", "cache_hit"],
    buckets=[60, 90, 120, 150, 180, 240, 300],
)
simulation_errors = Counter(
    "augur_simulation_errors_total",
    "Total simulation failures",
    ["ticker", "error_type"],
)
seed_quality_score = Histogram(
    "augur_seed_quality_score",
    "Seed quality score per simulation",
    ["ticker"],
    buckets=[0.1, 0.2, 0.4, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0],
)
active_simulations = Gauge(
    "augur_active_simulations",
    "Currently running simulations",
)
api_requests_total = Counter(
    "augur_api_requests_total",
    "Total API requests",
    ["endpoint", "method", "status"],
)


# ── Loki logging ─────────────────────────────────────────────────────────────

def setup_loki_logging():
    """Configure Loki log shipping. Falls back to console if not configured."""
    loki_url = os.getenv("GRAFANA_LOKI_URL")
    loki_user = os.getenv("GRAFANA_LOKI_USER")
    api_key = os.getenv("GRAFANA_API_KEY")

    if not all([loki_url, loki_user, api_key]):
        logging.warning("Grafana Loki not configured — falling back to console logging")
        return

    try:
        import logging_loki

        handler = logging_loki.LokiHandler(
            url=f"{loki_url}/loki/api/v1/push",
            tags={
                "application": "augur",
                "environment": os.getenv("ENVIRONMENT", "production"),
            },
            auth=(loki_user, api_key),
            version="1",
        )
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
        logging.info("Grafana Loki logging initialised")
    except Exception as e:
        logging.error(f"Failed to init Loki: {e}")


# ── Simulation tracking decorator ────────────────────────────────────────────

def start_metrics_push():
    """Push Prometheus metrics to Grafana Cloud every 60s via remote_write.

    Runs in a background daemon thread. No Grafana Alloy required.
    Requires GRAFANA_PROM_URL, GRAFANA_LOKI_USER, GRAFANA_API_KEY.
    """
    prom_url = os.getenv("GRAFANA_PROM_URL")
    loki_user = os.getenv("GRAFANA_LOKI_USER")
    api_key = os.getenv("GRAFANA_API_KEY")

    if not all([prom_url, loki_user, api_key]):
        logging.warning(
            "Grafana Prometheus push not configured — skipping. "
            "Set GRAFANA_PROM_URL, GRAFANA_LOKI_USER, GRAFANA_API_KEY"
        )
        return

    def push_loop():
        logging.info(f"Grafana metrics push started — pushing every 60s to {prom_url}")
        while True:
            try:
                data = generate_latest(REGISTRY)
                resp = _requests.post(
                    prom_url,
                    data=data,
                    headers={"Content-Type": "text/plain; version=0.0.4"},
                    auth=(loki_user, api_key),
                    timeout=15,
                )
                if resp.status_code in (200, 204):
                    logging.debug("Metrics pushed successfully")
                else:
                    logging.warning(
                        f"Metrics push failed: {resp.status_code} {resp.text[:200]}"
                    )
            except Exception as e:
                logging.error(f"Metrics push error: {e}")
            time.sleep(60)

    t = threading.Thread(target=push_loop, daemon=True)
    t.start()


# ── Simulation tracking decorator ────────────────────────────────────────────

def track_simulation(ticker: str):
    """Decorator that records metrics and logs for a simulation run."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.time()
            active_simulations.inc()
            cache_hit = "false"
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start

                verdict = getattr(result, "verdict", "unknown")
                if hasattr(result, "seed_cache_hit"):
                    cache_hit = str(result.seed_cache_hit).lower()
                quality = getattr(result, "seed_quality", 0)

                simulation_total.labels(
                    ticker=ticker, verdict=verdict, cache_hit=cache_hit
                ).inc()
                simulation_duration.labels(
                    ticker=ticker, cache_hit=cache_hit
                ).observe(duration)
                seed_quality_score.labels(ticker=ticker).observe(quality)

                logging.info(
                    "Simulation complete",
                    extra={
                        "ticker": ticker,
                        "verdict": verdict,
                        "duration_s": round(duration, 1),
                        "cache_hit": cache_hit,
                        "seed_quality": quality,
                    },
                )
                return result
            except Exception as e:
                duration = time.time() - start
                error_type = type(e).__name__
                simulation_errors.labels(
                    ticker=ticker, error_type=error_type
                ).inc()
                logging.error(
                    "Simulation failed",
                    extra={
                        "ticker": ticker,
                        "error": str(e),
                        "error_type": error_type,
                        "duration_s": round(duration, 1),
                        "traceback": traceback.format_exc(),
                    },
                )
                raise
            finally:
                active_simulations.dec()

        return wrapper

    return decorator
