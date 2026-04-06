"""Unit tests for augur_api — auth, rate limiting, validation, job lifecycle.

Uses FastAPI TestClient + monkeypatched get_pool (no real DB, no real pipeline).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Configure API keys + admin secret BEFORE importing augur_api
os.environ.setdefault("AUGUR_API_KEYS", "test-key-1,test-key-2")
os.environ.setdefault("ADMIN_SECRET", "admin-secret-xyz")
os.environ.pop("DATABASE_URL", None)  # default to no-DB; individual tests opt in

import augur_api  # noqa: E402


# ---------- helpers ----------

def make_mock_pool(conn=None):
    """Build a fake asyncpg pool whose acquire() yields the given conn."""
    if conn is None:
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="INSERT 1")
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchval = AsyncMock(return_value=1)
        conn.fetchrow = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = AsyncMock()
    pool.acquire = _acquire
    return pool, conn


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset module-level state between tests."""
    augur_api._jobs.clear()
    augur_api._rate_limits.clear()
    augur_api._api_keys.clear()
    augur_api._api_keys.update({"test-key-1", "test-key-2"})
    augur_api._ADMIN_SECRET = "admin-secret-xyz"
    augur_api._schema_ensured = True  # skip lazy schema setup
    yield
    augur_api._jobs.clear()
    augur_api._rate_limits.clear()


@pytest.fixture
def client():
    return TestClient(augur_api.app)


# ============================================================
# Auth helper unit tests (pure)
# ============================================================

class TestCheckApiKey:
    def test_valid_key_returns_key(self):
        assert augur_api._check_api_key("test-key-1") == "test-key-1"

    def test_missing_key_raises_401(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            augur_api._check_api_key(None)
        assert exc.value.status_code == 401

    def test_empty_key_raises_401(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            augur_api._check_api_key("")
        assert exc.value.status_code == 401

    def test_unknown_key_raises_401(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            augur_api._check_api_key("nope")
        assert exc.value.status_code == 401


class TestCheckRateLimit:
    def test_under_limit_passes(self):
        for _ in range(augur_api.RATE_LIMIT_MAX - 1):
            augur_api._check_rate_limit("test-key-1")
        # 10th call still within limit
        augur_api._check_rate_limit("test-key-1")

    def test_over_limit_raises_429(self):
        from fastapi import HTTPException
        for _ in range(augur_api.RATE_LIMIT_MAX):
            augur_api._check_rate_limit("test-key-1")
        with pytest.raises(HTTPException) as exc:
            augur_api._check_rate_limit("test-key-1")
        assert exc.value.status_code == 429

    def test_separate_keys_have_separate_buckets(self):
        for _ in range(augur_api.RATE_LIMIT_MAX):
            augur_api._check_rate_limit("test-key-1")
        # key-2 still has full quota
        augur_api._check_rate_limit("test-key-2")

    def test_old_entries_pruned(self):
        import time
        # Pre-seed bucket with timestamps from 2 hours ago
        old = time.time() - 7200
        augur_api._rate_limits["test-key-1"] = [old] * augur_api.RATE_LIMIT_MAX
        # Should not raise — all old entries pruned
        augur_api._check_rate_limit("test-key-1")
        assert len(augur_api._rate_limits["test-key-1"]) == 1


# ============================================================
# /health
# ============================================================

class TestHealth:
    def test_no_db_returns_degraded(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert body["neon_connected"] is False
        assert body["active_jobs"] == 0

    def test_active_jobs_counted(self, client):
        augur_api._jobs["job-x"] = {"status": "running", "simulation_id": "s",
                                     "ticker": "BHP", "result": None, "error": None}
        augur_api._jobs["job-y"] = {"status": "queued", "simulation_id": "s2",
                                     "ticker": "CBA", "result": None, "error": None}
        augur_api._jobs["job-done"] = {"status": "complete", "simulation_id": "s3",
                                        "ticker": "CSL", "result": {}, "error": None}
        r = client.get("/health")
        assert r.json()["active_jobs"] == 2


# ============================================================
# /simulate
# ============================================================

class TestSimulateEndpoint:
    def test_missing_api_key_401(self, client):
        r = client.post("/simulate", json={"ticker": "BHP"})
        assert r.status_code == 401

    def test_invalid_api_key_401(self, client):
        r = client.post("/simulate", json={"ticker": "BHP"},
                        headers={"X-API-Key": "wrong"})
        assert r.status_code == 401

    def test_invalid_ticker_422(self, client):
        with patch.object(augur_api, "is_valid_asx_ticker", return_value=False):
            r = client.post("/simulate", json={"ticker": "FAKE"},
                            headers={"X-API-Key": "test-key-1"})
        assert r.status_code == 422
        assert "ASX 200" in r.json()["detail"]

    def test_invalid_reporting_date_422(self, client):
        pool, _ = make_mock_pool()
        with patch.object(augur_api, "is_valid_asx_ticker", return_value=True), \
             patch.object(augur_api, "get_pool", AsyncMock(return_value=pool)):
            r = client.post(
                "/simulate",
                json={"ticker": "BHP", "reporting_date": "not-a-date"},
                headers={"X-API-Key": "test-key-1"},
            )
        assert r.status_code == 422
        assert "YYYY-MM-DD" in r.json()["detail"]

    def test_happy_path_creates_job(self, client):
        pool, conn = make_mock_pool()
        # Stub the background pipeline so _run_job doesn't actually run
        with patch.object(augur_api, "is_valid_asx_ticker", return_value=True), \
             patch.object(augur_api, "get_pool", AsyncMock(return_value=pool)), \
             patch.object(augur_api, "run_full_pipeline", AsyncMock()):
            r = client.post(
                "/simulate",
                json={"ticker": "bhp", "reporting_date": "2026-08-15"},
                headers={"X-API-Key": "test-key-1"},
            )

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "queued"
        assert body["job_id"].startswith("job-")
        assert body["simulation_id"].startswith("sim-")
        # Job registered with uppercased ticker
        assert body["job_id"] in augur_api._jobs
        job = augur_api._jobs[body["job_id"]]
        assert job["ticker"] == "BHP"
        # First execute call is the foreground INSERT (background task may also run)
        first_sql = conn.execute.await_args_list[0].args[0]
        assert "INSERT INTO simulations" in first_sql

    def test_db_failure_returns_500(self, client):
        pool, conn = make_mock_pool()
        conn.execute.side_effect = RuntimeError("neon down")
        with patch.object(augur_api, "is_valid_asx_ticker", return_value=True), \
             patch.object(augur_api, "get_pool", AsyncMock(return_value=pool)), \
             patch.object(augur_api, "run_full_pipeline", AsyncMock()):
            r = client.post("/simulate", json={"ticker": "BHP"},
                            headers={"X-API-Key": "test-key-1"})
        assert r.status_code == 500

    def test_rate_limit_kicks_in_at_11th_call(self, client):
        pool, _ = make_mock_pool()
        with patch.object(augur_api, "is_valid_asx_ticker", return_value=True), \
             patch.object(augur_api, "get_pool", AsyncMock(return_value=pool)), \
             patch.object(augur_api, "run_full_pipeline", AsyncMock()):
            for _ in range(augur_api.RATE_LIMIT_MAX):
                r = client.post("/simulate", json={"ticker": "BHP"},
                                headers={"X-API-Key": "test-key-1"})
                assert r.status_code == 200
            r = client.post("/simulate", json={"ticker": "BHP"},
                            headers={"X-API-Key": "test-key-1"})
        assert r.status_code == 429


# ============================================================
# /simulation/{job_id}
# ============================================================

class TestGetSimulation:
    def test_missing_key_401(self, client):
        r = client.get("/simulation/job-123")
        assert r.status_code == 401

    def test_unknown_job_404(self, client):
        r = client.get("/simulation/job-unknown",
                       headers={"X-API-Key": "test-key-1"})
        assert r.status_code == 404

    def test_returns_job_state(self, client):
        augur_api._jobs["job-abc"] = {
            "status": "complete",
            "simulation_id": "sim-1",
            "ticker": "BHP",
            "reporting_date": "2026-08-15",
            "result": {"verdict": "BEAT"},
            "error": None,
        }
        r = client.get("/simulation/job-abc",
                       headers={"X-API-Key": "test-key-1"})
        assert r.status_code == 200
        body = r.json()
        assert body["job_id"] == "job-abc"
        assert body["status"] == "complete"
        assert body["ticker"] == "BHP"
        assert body["result"]["verdict"] == "BEAT"


# ============================================================
# /admin/stats — auth only (full DB integration is out of scope here)
# ============================================================

class TestAdminStatsAuth:
    def test_missing_secret_401(self, client):
        r = client.get("/admin/stats")
        assert r.status_code == 401

    def test_wrong_secret_401(self, client):
        r = client.get("/admin/stats",
                       headers={"X-Admin-Secret": "wrong"})
        assert r.status_code == 401

    def test_empty_admin_secret_blocks_all(self, client):
        # Empty server-side secret must reject everything (not match empty header)
        augur_api._ADMIN_SECRET = ""
        r = client.get("/admin/stats", headers={"X-Admin-Secret": ""})
        assert r.status_code == 401

    def test_invalid_timestamp_format_400(self, client):
        r = client.get(
            "/admin/stats?from_ts=not-a-date",
            headers={"X-Admin-Secret": "admin-secret-xyz"},
        )
        assert r.status_code == 400


# ============================================================
# /feedback
# ============================================================

class TestFeedback:
    def test_invalid_rating_400(self, client):
        r = client.post("/feedback", json={"rating": "amazing"})
        assert r.status_code == 400

    def test_valid_rating_persisted(self, client):
        pool, conn = make_mock_pool()
        with patch.object(augur_api, "get_pool", AsyncMock(return_value=pool)):
            r = client.post("/feedback", json={
                "rating": "positive",
                "ticker": "BHP",
                "comment": "great",
            })
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
        sql = conn.execute.await_args.args[0]
        assert "INSERT INTO feedback" in sql
