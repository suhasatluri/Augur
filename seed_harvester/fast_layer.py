"""Fast layer — real-time sentiment via Claude Haiku. 2-hour TTL with ASX announcement invalidation."""

from __future__ import annotations

import asyncio
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
{company_intel_section}
{perplexity_section}
Your job is to identify what has CHANGED or what ADDITIONAL sentiment signals exist that the fundamental analysis above does NOT cover. Do NOT repeat or rephrase the slow layer seeds.

Focus ONLY on:
1. SENTIMENT — analyst tone shifts, consensus revisions, notable commentary from brokers or management. Describe the DIRECTION and NATURE of sentiment, not fabricated numbers.
2. MACRO — any recent macro developments (last 1-2 weeks) that could shift the earnings outlook vs. what the slow layer already captured.
3. LEADING INDICATORS — if quarterly intelligence is provided above, assess how those operational signals translate to likely earnings direction.

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


async def _get_company_intel_section(ticker: str) -> str:
    """Fetch company intel from Neon and format as prompt section."""
    try:
        from db.schema import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT combined_signals, data_confidence FROM asx_company_intel WHERE ticker = $1",
                ticker.upper(),
            )
            if not row or not row["combined_signals"]:
                return ""

            signals = json.loads(row["combined_signals"])
            lines = ["\nQUARTERLY INTELLIGENCE (from company IR documents):"]

            outlook = signals.get("overall_outlook", "unknown")
            lines.append(f"Overall outlook: {outlook}")

            margin = signals.get("margin_trend", "UNKNOWN")
            cost = signals.get("cost_trend", "UNKNOWN")
            if margin != "UNKNOWN" or cost != "UNKNOWN":
                lines.append(f"Margin trend: {margin} | Cost trend: {cost}")

            if signals.get("guidance_update"):
                lines.append(f"Guidance: {signals['guidance_update']}")
            if signals.get("guidance_language"):
                lines.append(f"Management tone: {signals['guidance_language']}")

            indicators = signals.get("leading_indicators", [])
            if indicators:
                lines.append("Leading indicators:")
                for ind in indicators[:7]:
                    lines.append(f"  - {ind}")

            risks = signals.get("risks", [])
            if risks:
                lines.append("Risks flagged:")
                for r in risks[:3]:
                    lines.append(f"  - {r}")

            lines.append(f"Data confidence: {row['data_confidence']}")
            return "\n".join(lines)
    except Exception as e:
        logger.debug(f"[fast_layer] Company intel lookup failed for {ticker}: {e}")
        return ""


async def _get_perplexity_section(ticker: str, reporting_period: str = "") -> str:
    """Fetch real-time financial news from Perplexity Sonar."""
    try:
        from seed_harvester.perplexity_harvester import PerplexityHarvester
        harvester = PerplexityHarvester()
        news = await harvester.get_financial_news(ticker, reporting_period)
        return harvester.to_seed_context(news)
    except Exception as e:
        logger.debug(f"[fast_layer] Perplexity failed for {ticker}: {e}")
        return ""


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

    # Fetch company intel and Perplexity news in parallel
    company_intel_coro = _get_company_intel_section(ticker)
    perplexity_coro = _get_perplexity_section(ticker, reporting_period)

    company_intel_section, perplexity_section = await asyncio.gather(
        company_intel_coro, perplexity_coro, return_exceptions=True,
    )
    if isinstance(company_intel_section, Exception):
        company_intel_section = ""
    if isinstance(perplexity_section, Exception):
        perplexity_section = ""

    current_date = datetime.utcnow().strftime("%Y-%m-%d")
    prompt = FAST_LAYER_PROMPT.format(
        ticker=ticker,
        current_date=current_date,
        reporting_period=reporting_period,
        slow_layer_summary=slow_summary,
        company_intel_section=company_intel_section,
        perplexity_section=perplexity_section,
    )

    logger.info(f"[fast_layer] Harvesting {ticker} via Haiku...")

    message = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        timeout=60.0,
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
