"""Slow layer — deep fundamental analysis via Claude Sonnet. 7-day TTL."""

from __future__ import annotations

import json
import logging
from datetime import datetime

import anthropic

from seed_harvester.models import Seed, SeedType

logger = logging.getLogger(__name__)

SLOW_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

SLOW_LAYER_PROMPT = """You are an ASX equity research analyst. Analyse {ticker} ({company_name}) and produce structured earnings intelligence seeds.

Today's date: {current_date}
Next expected earnings report: {reporting_period}
ASX reporting convention: Australian companies report Half-Year (H1) and Full-Year (FY) results. Currency is AUD. Key filings: Appendix 4D (half-year), Appendix 4E (full-year), Appendix 3B (capital changes).

For the upcoming earnings report, provide seeds in these categories:
1. FINANCIAL — revenue/earnings estimates, margin trends, key financial metrics
2. GUIDANCE — management guidance, forward-looking statements, consensus expectations
3. SECTOR — sector tailwinds/headwinds, competitive positioning, market share shifts
4. MACRO — macro factors affecting this company (rates, commodities, FX, regulation)

CRITICAL RULES:
- Do NOT fabricate specific numbers (revenue figures, production volumes, percentages) unless you are highly confident they are accurate from well-known public information.
- When citing a specific figure, you MUST name the exact source document or data release it comes from.
- If you are reasoning from general knowledge rather than a specific data point, say so explicitly and use qualitative language ("likely higher", "trending down") instead of invented numbers.
- Prefer structural/thematic insights over fake precision.

For each seed, provide:
- content: A specific, actionable insight (1-2 sentences). Use qualitative language when you lack precise data.
- confidence: Calibrated score using this scale:
  0.1-0.3 = speculative inference, limited supporting evidence
  0.3-0.5 = reasonable hypothesis based on general trends
  0.5-0.7 = well-reasoned estimate supported by known public information
  0.7-0.9 = high confidence, backed by specific verifiable data
  (Do NOT use 0.9+ unless citing a concrete, verifiable fact)
- source: The specific public document, data release, or knowledge basis. Write "general knowledge" if not from a specific source.
- reasoning: Brief explanation of why this matters for earnings surprise prediction

Return ONLY a JSON array of objects with keys: seed_type, content, confidence, source, reasoning.
No markdown, no commentary — just the JSON array."""


async def harvest_slow(
    client: anthropic.AsyncAnthropic,
    ticker: str,
    company_name: str = "",
    reporting_period: str = "",
) -> list[Seed]:
    """Run deep analysis with Sonnet. Called on cache miss or force refresh."""

    if not company_name:
        company_name = ticker
    if not reporting_period:
        reporting_period = "next scheduled report (date unknown)"

    current_date = datetime.utcnow().strftime("%Y-%m-%d")
    prompt = SLOW_LAYER_PROMPT.format(
        ticker=ticker,
        company_name=company_name,
        current_date=current_date,
        reporting_period=reporting_period,
    )

    logger.info(f"[slow_layer] Harvesting {ticker} via Sonnet...")

    message = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    # Strip markdown fences if model wraps them
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
        raw = raw.strip()

    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        logger.error(f"[slow_layer] Failed to parse JSON for {ticker}: {raw[:200]}")
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
            logger.warning(f"[slow_layer] Skipping malformed seed: {e}")

    logger.info(f"[slow_layer] Harvested {len(seeds)} seeds for {ticker}")
    return seeds
