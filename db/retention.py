"""Neon retention policy — cleanup old simulations, compress round_results."""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


async def cleanup_old_data(conn) -> dict:
    """Delete expired simulations (CASCADE removes agents + round_results).

    Retention rules:
    - Failed/timeout simulations older than 7 days
    - Batch test simulations (id LIKE 'batch-%') older than 24 hours
    - Never deletes complete simulations
    - Never touches asx_earnings, asx_metrics, or asx_company_intel
    """
    failed = await conn.execute("""
        DELETE FROM simulations
        WHERE status IN ('failed', 'timeout')
        AND created_at < NOW() - INTERVAL '7 days'
    """)
    failed_count = int(failed.split()[-1]) if failed else 0

    batch = await conn.execute("""
        DELETE FROM simulations
        WHERE id LIKE 'batch-%'
        AND created_at < NOW() - INTERVAL '24 hours'
    """)
    batch_count = int(batch.split()[-1]) if batch else 0

    logger.info(
        f"[retention] Cleanup: {failed_count} failed sims, "
        f"{batch_count} batch sims deleted at {datetime.utcnow().isoformat()}"
    )
    return {"failed_deleted": failed_count, "batch_deleted": batch_count}


async def compress_round_results(conn) -> int:
    """Null out verbose reasoning text for old complete simulations.

    For complete simulations older than 24hr:
    Sets reasoning = '' on round_results.
    Keeps: round_number, probability, conviction_delta.
    Drops: verbose agent reasoning text.
    """
    result = await conn.execute("""
        UPDATE round_results
        SET reasoning = ''
        WHERE simulation_id IN (
            SELECT id FROM simulations
            WHERE status = 'complete'
            AND created_at < NOW() - INTERVAL '24 hours'
        )
        AND reasoning != ''
    """)
    count = int(result.split()[-1]) if result else 0
    logger.info(f"[retention] Compressed {count} round_results rows")
    return count


async def run_all(conn) -> dict:
    """Run full retention pipeline."""
    cleanup = await cleanup_old_data(conn)
    compressed = await compress_round_results(conn)
    return {**cleanup, "reasoning_compressed": compressed}
