"""Fast layer — real-time sentiment via Claude Haiku. 2-hour TTL with ASX announcement invalidation."""

from __future__ import annotations

import json
import logging
from datetime import datetime

import anthropic

from seed_harvester.models import Seed, SeedType  # noqa: F811 — Seed used in type hints

logger = logging.getLogger(__name__)

FAST_TTL_SECONDS = 2 * 60 * 60  # 2 hours

FAST_LAYER_PROMPT = """You are an ASX earnings sentiment analyst producing DELTA seeds — signals that add NEW information on top of existing fundamental analysis.

Ticker: {ticker}
Today's date: {current_date}
Next expected earnings report: {reporting_period}

The slow layer has already produced these fundamental seeds:
{slow_layer_summary}

Your job is to identify what has CHANGED or what ADDITIONAL sentiment signals exist that the fundamental analysis above does NOT cover. Do NOT repeat or rephrase the slow layer seeds.

Focus ONLY on:
1. SENTIMENT — analyst tone shifts, consensus revisions, notable commentary from brokers or management. Describe the DIRECTION and NATURE of sentiment, not fabricated numbers.
2. MACRO — any recent macro developments (last 1-2 weeks) that could shift the earnings outlook vs. what the slow layer already captured.

CRITICAL RULES:
- You do NOT have access to live market data. Do NOT fabricate specific trading volumes, short interest percentages, share prices, or social media metrics.
- Instead, reason about what TYPES of sentiment signals would matter and what directional pressures exist based on your knowledge.
- Frame insights as analytical hypotheses, not observed data points. Use language like "likely pressure from...", "consensus appears to be shifting toward...", "key risk factor to monitor:".
- If you cannot add meaningful delta beyond the slow layer, return fewer seeds rather than padding with noise.

Confidence calibration:
  0.1-0.3 = speculative, weak signal
  0.3-0.5 = reasonable inference from general trends
  0.5-0.7 = well-supported by known information
  0.7+ = only if citing a specific verifiable fact or event

Return ONLY a JSON array of objects with keys: seed_type, content, confidence, source, reasoning.
No markdown, no commentary — just the JSON array. Return an empty array [] if no meaningful delta exists."""


async def harvest_fast(
    client: anthropic.AsyncAnthropic,
    ticker: str,
    slow_seeds: list[Seed] | None = None,
    reporting_period: str = "",
) -> list[Seed]:
    """Run fast sentiment scan with Haiku. Called on cache miss or invalidation."""

    if not reporting_period:
        reporting_period = "next scheduled report (date unknown)"

    # Build slow layer summary for delta prompting
    if slow_seeds:
        summary_lines = []
        for s in slow_seeds:
            summary_lines.append(f"- [{s.seed_type.value.upper()}] {s.content}")
        slow_summary = "\n".join(summary_lines)
    else:
        slow_summary = "(no slow layer seeds available)"

    current_date = datetime.utcnow().strftime("%Y-%m-%d")
    prompt = FAST_LAYER_PROMPT.format(
        ticker=ticker,
        current_date=current_date,
        reporting_period=reporting_period,
        slow_layer_summary=slow_summary,
    )

    logger.info(f"[fast_layer] Harvesting {ticker} via Haiku...")

    message = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
        raw = raw.strip()

    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        logger.error(f"[fast_layer] Failed to parse JSON for {ticker}: {raw[:200]}")
        return []

    seeds: list[Seed] = []
    now = datetime.utcnow()
    for item in items:
        try:
            seed = Seed(
                ticker=ticker,
                seed_type=SeedType(item["seed_type"].lower()),
                content=item["content"],
                confidence=float(item["confidence"]),
                source=item.get("source", ""),
                reasoning=item.get("reasoning", ""),
                harvested_at=now,
            )
            seeds.append(seed)
        except (KeyError, ValueError) as e:
            logger.warning(f"[fast_layer] Skipping malformed seed: {e}")

    logger.info(f"[fast_layer] Harvested {len(seeds)} seeds for {ticker}")
    return seeds


async def check_asx_announcements(ticker: str) -> bool:
    """Check if there are new ASX announcements that should invalidate the fast cache.

    TODO: Integrate with ASX announcements API / RSS feed.
    For now returns False (no invalidation).
    """
    logger.debug(f"[fast_layer] Checking ASX announcements for {ticker} (stub)")
    return False
