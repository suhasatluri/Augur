"""Slow layer — deep fundamental analysis via Claude Sonnet. 7-day TTL.

Fetches structured data from yfinance FIRST, then uses Claude Sonnet
to generate qualitative seeds grounded in real financial data.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

import anthropic

from seed_harvester.models import Seed, SeedType
from seed_harvester.structured_data import StructuredDataFetcher

logger = logging.getLogger(__name__)

SLOW_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

SLOW_LAYER_PROMPT = """You are an ASX equity research analyst. Analyse {ticker} ({company_name}) and produce structured earnings intelligence seeds.

Today's date: {current_date}
Next expected earnings report: {reporting_period}
Sector: {sector} / {industry}
ASX reporting convention: Australian companies report Half-Year (H1) and Full-Year (FY) results. Currency is AUD. Key filings: Appendix 4D (half-year), Appendix 4E (full-year), Appendix 3B (capital changes).

=== VERIFIED FINANCIAL DATA (from yfinance — treat as factual) ===
{structured_data_block}

=== YOUR TASK ===
Using the verified data above as your foundation, provide seeds in these categories:
1. FINANCIAL — interpret the numbers above: what do the PE ratios, growth rates, and analyst targets imply for earnings surprise?
2. GUIDANCE — what does the analyst consensus (recommendation={recommendation_key}, target upside={upside_pct}) suggest about management credibility and forward expectations?
3. SECTOR — sector-specific tailwinds/headwinds affecting {sector} companies on the ASX
4. MACRO — macro factors affecting this company (rates, commodities, FX, regulation)

CRITICAL RULES:
- USE the verified financial data above. Do not ignore it or contradict it.
- For FINANCIAL and GUIDANCE seeds, your insights must be GROUNDED in the data provided.
- For SECTOR and MACRO seeds, you may use general knowledge but be explicit about it.
- Do NOT fabricate specific numbers beyond what is provided above.
- Prefer structural/thematic insights over fake precision.

For each seed, provide:
- content: A specific, actionable insight (1-2 sentences) grounded in the data.
- confidence: Calibrated score:
  0.1-0.3 = speculative inference
  0.3-0.5 = reasonable hypothesis
  0.5-0.7 = well-reasoned, supported by the data above
  0.7-0.9 = high confidence, directly from verified data
- source: "yfinance" if based on the data above, "general knowledge" otherwise.
- reasoning: Brief explanation of why this matters for earnings surprise prediction

Return ONLY a JSON array of objects with keys: seed_type, content, confidence, source, reasoning.
No markdown, no commentary — just the JSON array."""


def _build_structured_data_block(yf_data: dict, extra_data: Optional[dict] = None) -> str:
    """Format yfinance + market signals data as a readable block for the prompt."""
    lines = []

    price = yf_data.get("currentPrice")
    target = yf_data.get("targetMeanPrice")
    if price:
        lines.append(f"Current Price: A${price:.2f}")
    if target and price:
        upside = ((target - price) / price) * 100
        lines.append(f"Analyst Target Price: A${target:.2f} ({upside:+.1f}% upside)")

    rec = yf_data.get("recommendationMean")
    rec_key = yf_data.get("recommendationKey")
    if rec:
        lines.append(f"Analyst Consensus: {rec:.2f}/5.0 ({rec_key})")

    eg = yf_data.get("earningsGrowth")
    if eg is not None:
        lines.append(f"Earnings Growth: {eg:+.1%}")

    rg = yf_data.get("revenueGrowth")
    if rg is not None:
        lines.append(f"Revenue Growth: {rg:+.1%}")

    roe = yf_data.get("returnOnEquity")
    if roe is not None:
        lines.append(f"Return on Equity: {roe:.1%}")

    de = yf_data.get("debtToEquity")
    if de is not None:
        lines.append(f"Debt/Equity: {de:.1f}")

    fpe = yf_data.get("forwardPE")
    tpe = yf_data.get("trailingPE")
    if fpe and tpe:
        lines.append(f"Forward PE: {fpe:.1f}  |  Trailing PE: {tpe:.1f}")
    elif fpe:
        lines.append(f"Forward PE: {fpe:.1f}")

    dy = yf_data.get("dividendYield")
    if dy is not None:
        lines.append(f"Dividend Yield: {dy:.2f}%")

    mcap = yf_data.get("marketCap")
    if mcap:
        if mcap >= 1e12:
            lines.append(f"Market Cap: A${mcap/1e12:.1f}T")
        elif mcap >= 1e9:
            lines.append(f"Market Cap: A${mcap/1e9:.1f}B")
        else:
            lines.append(f"Market Cap: A${mcap/1e6:.0f}M")

    ned = yf_data.get("nextEarningsDate")
    if ned:
        lines.append(f"Next Earnings Date: {ned}")

    # Market Index financials
    if extra_data:
        mi_fin = extra_data.get("source_mi_financials", {})
        if mi_fin:
            npat = mi_fin.get("npat_m")
            npat_prior = mi_fin.get("npat_prior_m")
            rev = mi_fin.get("revenue_m")
            if npat is not None:
                growth_str = ""
                if npat_prior and npat_prior != 0:
                    g = ((npat - npat_prior) / abs(npat_prior)) * 100
                    growth_str = f" ({g:+.1f}% YoY)"
                lines.append(f"NPAT: ${npat:,.0f}M{growth_str} (Market Index)")
            if rev is not None:
                lines.append(f"Revenue: ${rev:,.0f}M (Market Index)")

    # Market signals from ASIC + director trades
    if extra_data:
        short = extra_data.get("source_asic_short", {})
        director = extra_data.get("source_director", {})
        spread_pct = yf_data.get("analyst_spread_pct")
        consensus_q = yf_data.get("analyst_consensus_quality")

        if short:
            pct = short.get("pct_shorted", 0)
            sig = short.get("signal", "UNKNOWN")
            lines.append(f"Short Interest: {pct:.2f}% [{sig}] (ASIC data)")

        if director:
            net = director.get("net_buy_value", 0)
            sig = director.get("signal", "NEUTRAL")
            buys = director.get("buy_count", 0)
            sells = director.get("sell_count", 0)
            net_str = f"+${net:,.0f}" if net > 0 else f"-${abs(net):,.0f}"
            lines.append(f"Director Trades: {buys} buys, {sells} sells, net {net_str} [{sig}]")

        if spread_pct is not None and spread_pct > 0:
            hi = yf_data.get("targetHighPrice")
            lo = yf_data.get("targetLowPrice")
            range_str = f"${lo:.2f}–${hi:.2f} " if lo and hi else ""
            lines.append(f"Analyst Target Range: {range_str}(spread {spread_pct:.0f}%) [{consensus_q or 'N/A'}]")

    return "\n".join(lines) if lines else "(no structured data available)"


async def harvest_slow(
    client: anthropic.AsyncAnthropic,
    ticker: str,
    company_name: str = "",
    reporting_period: str = "",
) -> tuple[list[Seed], Optional[dict], Optional[float]]:
    """Run deep analysis with Sonnet, grounded in yfinance data.

    Returns: (seeds, structured_data_dict, ticker_bias_score)
    """
    # --- Step 1: Fetch structured data from yfinance ---
    fetcher = StructuredDataFetcher()
    structured_data = await fetcher.get_ticker_data(ticker)
    bias_score, bias_breakdown = fetcher.compute_ticker_bias_score(structured_data)

    yf_data = structured_data.get("source_yfinance", {})

    # Use yfinance data for company_name, sector, reporting_period
    if not company_name:
        company_name = yf_data.get("longName") or ticker
    sector = yf_data.get("sector") or "Unknown"
    industry = yf_data.get("industry") or "Unknown"
    if not reporting_period:
        ned = yf_data.get("nextEarningsDate")
        reporting_period = ned if ned else "next scheduled report (date unknown)"

    # Compute upside string for prompt
    price = yf_data.get("currentPrice")
    target = yf_data.get("targetMeanPrice")
    if price and target:
        upside_pct = f"{((target - price) / price) * 100:+.1f}%"
    else:
        upside_pct = "N/A"
    rec_key = yf_data.get("recommendationKey") or "N/A"

    # --- Step 2: Build prompt with structured data ---
    current_date = datetime.utcnow().strftime("%Y-%m-%d")
    structured_block = _build_structured_data_block(yf_data, extra_data=structured_data)

    prompt = SLOW_LAYER_PROMPT.format(
        ticker=ticker,
        company_name=company_name,
        current_date=current_date,
        reporting_period=reporting_period,
        sector=sector,
        industry=industry,
        structured_data_block=structured_block,
        recommendation_key=rec_key,
        upside_pct=upside_pct,
    )

    # --- Step 3: Call Sonnet ---
    logger.info(f"[slow_layer] Harvesting {ticker} via Sonnet (bias_score={bias_score:.3f})...")

    message = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
        timeout=120.0,
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
        logger.error(f"[slow_layer] Failed to parse JSON for {ticker}: {raw[:200]}")
        return [], structured_data, bias_score

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

    logger.info(f"[slow_layer] Harvested {len(seeds)} seeds for {ticker} (bias={bias_score:.3f})")
    return seeds, structured_data, bias_score
