"""Unit tests for db.retention — uses an AsyncMock conn (no real DB).

Establishes the asyncpg-style conn mock pattern reused by other tests
that exercise async DB code paths.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from db.retention import cleanup_old_data, compress_round_results, run_all


def make_conn(execute_returns):
    """Build an AsyncMock conn whose .execute() returns values in order.

    asyncpg's conn.execute() returns the command tag string, e.g. "DELETE 5".
    """
    conn = AsyncMock()
    if isinstance(execute_returns, list):
        conn.execute.side_effect = execute_returns
    else:
        conn.execute.return_value = execute_returns
    return conn


class TestCleanupOldData:
    @pytest.mark.asyncio
    async def test_parses_delete_counts(self):
        conn = make_conn(["DELETE 7", "DELETE 3"])
        result = await cleanup_old_data(conn)
        assert result == {"failed_deleted": 7, "batch_deleted": 3}
        assert conn.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_zero_counts(self):
        conn = make_conn(["DELETE 0", "DELETE 0"])
        result = await cleanup_old_data(conn)
        assert result == {"failed_deleted": 0, "batch_deleted": 0}

    @pytest.mark.asyncio
    async def test_empty_string_treated_as_zero(self):
        conn = make_conn(["", ""])
        result = await cleanup_old_data(conn)
        assert result == {"failed_deleted": 0, "batch_deleted": 0}

    @pytest.mark.asyncio
    async def test_failed_query_targets_failed_and_timeout_status(self):
        conn = make_conn(["DELETE 1", "DELETE 0"])
        await cleanup_old_data(conn)
        first_sql = conn.execute.await_args_list[0].args[0]
        assert "DELETE FROM simulations" in first_sql
        assert "'failed'" in first_sql
        assert "'timeout'" in first_sql
        assert "7 days" in first_sql

    @pytest.mark.asyncio
    async def test_batch_query_targets_batch_prefix_and_24h(self):
        conn = make_conn(["DELETE 0", "DELETE 1"])
        await cleanup_old_data(conn)
        second_sql = conn.execute.await_args_list[1].args[0]
        assert "id LIKE 'batch-%'" in second_sql
        assert "24 hours" in second_sql

    @pytest.mark.asyncio
    async def test_does_not_touch_complete_simulations(self):
        conn = make_conn(["DELETE 0", "DELETE 0"])
        await cleanup_old_data(conn)
        for call in conn.execute.await_args_list:
            sql = call.args[0]
            # Must never delete by status='complete'
            assert "'complete'" not in sql

    @pytest.mark.asyncio
    async def test_does_not_touch_protected_tables(self):
        conn = make_conn(["DELETE 0", "DELETE 0"])
        await cleanup_old_data(conn)
        for call in conn.execute.await_args_list:
            sql = call.args[0]
            assert "asx_earnings" not in sql
            assert "asx_metrics" not in sql
            assert "asx_company_intel" not in sql


class TestCompressRoundResults:
    @pytest.mark.asyncio
    async def test_parses_update_count(self):
        conn = make_conn("UPDATE 42")
        count = await compress_round_results(conn)
        assert count == 42

    @pytest.mark.asyncio
    async def test_zero_updates(self):
        conn = make_conn("UPDATE 0")
        assert await compress_round_results(conn) == 0

    @pytest.mark.asyncio
    async def test_empty_response(self):
        conn = make_conn("")
        assert await compress_round_results(conn) == 0

    @pytest.mark.asyncio
    async def test_only_targets_complete_old_sims(self):
        conn = make_conn("UPDATE 5")
        await compress_round_results(conn)
        sql = conn.execute.await_args.args[0]
        assert "UPDATE round_results" in sql
        assert "SET reasoning = ''" in sql
        assert "status = 'complete'" in sql
        assert "24 hours" in sql

    @pytest.mark.asyncio
    async def test_skips_already_compressed(self):
        # Guard against re-compressing rows already nulled out
        conn = make_conn("UPDATE 0")
        await compress_round_results(conn)
        sql = conn.execute.await_args.args[0]
        assert "reasoning != ''" in sql


class TestRunAll:
    @pytest.mark.asyncio
    async def test_merges_results_from_both_steps(self):
        conn = make_conn(["DELETE 4", "DELETE 9", "UPDATE 100"])
        result = await run_all(conn)
        assert result == {
            "failed_deleted": 4,
            "batch_deleted": 9,
            "reasoning_compressed": 100,
        }
        assert conn.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_runs_cleanup_before_compression(self):
        conn = make_conn(["DELETE 0", "DELETE 0", "UPDATE 0"])
        await run_all(conn)
        sqls = [c.args[0] for c in conn.execute.await_args_list]
        assert "DELETE FROM simulations" in sqls[0]
        assert "DELETE FROM simulations" in sqls[1]
        assert "UPDATE round_results" in sqls[2]
