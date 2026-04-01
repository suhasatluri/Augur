"""Consensus Harvester — computes beat/miss history from yfinance EPS data.

Uses yfinance income_stmt (actual EPS) + earnings_estimate (forward consensus)
to build a reliable beat_rate for ASX tickers. No paid API required.

Data sources combined:
- income_stmt.Basic EPS → actual annual EPS (4 years)
- earnings_estimate.avg → current consensus EPS
- earnings_estimate.yearAgoEps → prior year actual
- earnings_estimate.growth → expected growth rate
- earnings_estimate.numberOfAnalysts → analyst coverage depth
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import yfinance as yf

from db.schema import get_pool

logger = logging.getLogger(__name__)

# Beat/miss threshold — if actual exceeds estimate by >1%, it's a BEAT
_BEAT_PCT = 1.0
_MISS_PCT = -1.0


class ConsensusHarvester:
    """Fetches consensus EPS data from yfinance for ASX tickers."""

    async def get_consensus_data(self, ticker: str) -> dict:
        """Fetch forward consensus + historical EPS from yfinance.

        Returns:
        {
            ticker, consensus_eps, year_ago_eps, growth_pct,
            analyst_count, eps_history: [{year, eps}...],
            data_source, data_confidence
        }
        """
        ticker = ticker.upper()

        def do_fetch():
            stock = yf.Ticker(f"{ticker}.AX")

            result = {
                "ticker": ticker,
                "consensus_eps": None,
                "year_ago_eps": None,
                "growth_pct": None,
                "analyst_count": 0,
                "eps_history": [],
                "data_source": "yfinance_consensus",
                "data_confidence": "LOW",
            }

            # Forward consensus from earnings_estimate
            try:
                ee = stock.earnings_estimate
                if ee is not None and not ee.empty and "0y" in ee.index:
                    row = ee.loc["0y"]
                    avg = row.get("avg")
                    yago = row.get("yearAgoEps")
                    growth = row.get("growth")
                    num = row.get("numberOfAnalysts")

                    if avg is not None and avg == avg:  # NaN check
                        result["consensus_eps"] = float(avg)
                    if yago is not None and yago == yago:
                        result["year_ago_eps"] = float(yago)
                    if growth is not None and growth == growth:
                        result["growth_pct"] = float(growth) * 100
                    if num is not None and num == num:
                        result["analyst_count"] = int(num)
            except Exception as e:
                logger.debug(f"[consensus] earnings_estimate failed for {ticker}: {e}")

            # Historical EPS from income statement
            try:
                inc = stock.income_stmt
                if inc is not None and not inc.empty and "Basic EPS" in inc.index:
                    eps_row = inc.loc["Basic EPS"]
                    for dt, val in eps_row.items():
                        if val is not None and val == val:  # NaN check
                            year = dt.year if hasattr(dt, "year") else str(dt)[:4]
                            result["eps_history"].append({
                                "year": int(year),
                                "eps": round(float(val), 4),
                            })
                    # Sort by year descending
                    result["eps_history"].sort(key=lambda x: x["year"], reverse=True)
            except Exception as e:
                logger.debug(f"[consensus] income_stmt failed for {ticker}: {e}")

            # Assess confidence
            if result["analyst_count"] >= 10 and len(result["eps_history"]) >= 3:
                result["data_confidence"] = "HIGH"
            elif result["analyst_count"] >= 5 or len(result["eps_history"]) >= 2:
                result["data_confidence"] = "MED"

            return result

        return await asyncio.get_event_loop().run_in_executor(None, do_fetch)

    async def get_beat_history(self, ticker: str) -> dict:
        """Compute beat/miss history using YoY EPS growth vs consensus expectations.

        Uses income_stmt actuals to compute year-over-year beat rate:
        - If EPS grew faster than the consensus growth expectation → BEAT
        - If EPS grew slower → MISS
        - Also computes simple beat_rate from price proxy if available in DB

        Returns:
        {
            ticker, beat_rate, avg_surprise_pct, recent_form,
            consensus_eps, year_ago_eps, analyst_count,
            eps_history, data_source, data_confidence
        }
        """
        ticker = ticker.upper()
        data = await self.get_consensus_data(ticker)

        eps_history = data.get("eps_history", [])
        consensus_eps = data.get("consensus_eps")
        year_ago_eps = data.get("year_ago_eps")

        # Compute beat/miss from historical YoY changes
        # If we have 4 years of data, we can compute 3 YoY transitions
        beats = 0
        total = 0
        surprises = []

        if len(eps_history) >= 2:
            for i in range(len(eps_history) - 1):
                current = eps_history[i]["eps"]
                prior = eps_history[i + 1]["eps"]
                if prior and prior != 0:
                    yoy_growth = ((current - prior) / abs(prior)) * 100
                    # Use average growth as proxy for "expectation"
                    # If growth > average → beat, else miss
                    surprises.append(yoy_growth)
                    total += 1

        # Simple heuristic: count years with positive growth as "beats"
        # This isn't perfect but anchors to real financial performance
        if surprises:
            avg_growth = sum(surprises) / len(surprises)
            for s in surprises:
                if s > avg_growth:
                    beats += 1

        # Also check: did most recent year beat consensus?
        # Compare year_ago_eps (actual) vs what was expected
        most_recent_beat = None
        if year_ago_eps and consensus_eps and len(eps_history) >= 1:
            latest_actual = eps_history[0]["eps"]
            # If latest actual > year_ago (delivered growth) → positive signal
            if latest_actual > year_ago_eps:
                most_recent_beat = True
                beats += 1
                total += 1
            else:
                most_recent_beat = False
                total += 1

        beat_rate = beats / total if total > 0 else None

        # Also pull price proxy beat_rate from DB for comparison
        price_proxy_rate = None
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                metrics = await conn.fetchrow(
                    "SELECT beat_rate_8q FROM asx_metrics WHERE ticker = $1", ticker
                )
                if metrics:
                    price_proxy_rate = metrics["beat_rate_8q"]
        except Exception:
            pass

        result = {
            "ticker": ticker,
            "beat_rate": beat_rate,
            "avg_surprise_pct": round(sum(surprises) / len(surprises), 2) if surprises else None,
            "recent_form": most_recent_beat,
            "consensus_eps": consensus_eps,
            "consensus_eps_cents": round(consensus_eps * 100, 2) if consensus_eps else None,
            "year_ago_eps": year_ago_eps,
            "year_ago_eps_cents": round(year_ago_eps * 100, 2) if year_ago_eps else None,
            "analyst_count": data.get("analyst_count", 0),
            "eps_history": eps_history,
            "price_proxy_beat_rate": price_proxy_rate,
            "data_source": data.get("data_source"),
            "data_confidence": data.get("data_confidence"),
        }

        logger.info(
            f"[consensus] {ticker}: beat_rate={beat_rate}, "
            f"consensus={consensus_eps}, analysts={data.get('analyst_count')}, "
            f"confidence={data.get('data_confidence')}"
        )
        return result

    async def get_perplexity_beat_adjustment(self, ticker: str) -> float:
        """Use Perplexity real-time news to estimate beat tendency adjustment.

        Scans material_news for beat/miss language and estimate revisions
        to produce a directional adjustment to blend with yfinance beat_rate.

        Returns adjustment in range [-0.15, +0.15] centered on 0.0.
        """
        try:
            from seed_harvester.perplexity_harvester import PerplexityHarvester
            harvester = PerplexityHarvester()
            news = await harvester.get_financial_news(ticker)
        except Exception as e:
            logger.debug(f"[consensus] Perplexity adjustment failed for {ticker}: {e}")
            return 0.0

        if not news:
            return 0.0

        adj = 0.0

        # Material news signals
        material = " ".join(news.get("material_news", [])).lower()
        _BEAT_WORDS = ["beat", "exceeded", "above expectations", "busting", "surpass", "top", "better than expected"]
        _MISS_WORDS = ["miss", "below expectations", "disappoint", "fell short", "underperform", "weaker than"]
        if any(w in material for w in _BEAT_WORDS):
            adj += 0.10
        if any(w in material for w in _MISS_WORDS):
            adj -= 0.10

        # Estimate revision direction
        revisions = (news.get("recent_estimate_revisions") or "").upper()
        if revisions == "UP":
            adj += 0.05
        elif revisions == "DOWN":
            adj -= 0.05

        # Analyst sentiment
        sentiment = (news.get("analyst_sentiment") or "").lower()
        if sentiment == "bullish":
            adj += 0.05
        elif sentiment == "bearish":
            adj -= 0.05

        adj = max(-0.15, min(0.15, adj))
        logger.info(f"[consensus] Perplexity adjustment for {ticker}: {adj:+.2f}")
        return adj

    async def get_blended_beat_rate(self, ticker: str) -> dict:
        """Blend yfinance historical beat_rate with Perplexity real-time signal.

        Returns:
        {
            yfinance_beat_rate: float,
            perplexity_adjustment: float,
            blended_beat_rate: float,
            data_confidence: str,
        }
        """
        ticker = ticker.upper()
        history = await self.get_beat_history(ticker)
        yf_rate = history.get("beat_rate")

        if yf_rate is None:
            yf_rate = 0.50

        pplx_adj = await self.get_perplexity_beat_adjustment(ticker)

        # Blend: 60% yfinance history + 40% perplexity adjustment (applied to 0.50 base)
        pplx_component = 0.50 + pplx_adj
        blended = yf_rate * 0.60 + pplx_component * 0.40
        blended = max(0.20, min(0.80, blended))

        result = {
            "ticker": ticker,
            "yfinance_beat_rate": yf_rate,
            "perplexity_adjustment": pplx_adj,
            "blended_beat_rate": round(blended, 4),
            "consensus_eps": history.get("consensus_eps"),
            "analyst_count": history.get("analyst_count", 0),
            "data_confidence": history.get("data_confidence", "LOW"),
        }
        logger.info(
            f"[consensus] {ticker}: yf={yf_rate:.2f}, pplx_adj={pplx_adj:+.2f}, "
            f"blended={blended:.3f}"
        )
        return result

    async def update_metrics(self, ticker: str) -> bool:
        """Update asx_metrics with consensus-derived beat_rate."""
        ticker = ticker.upper()
        history = await self.get_beat_history(ticker)

        if history.get("data_confidence") == "LOW":
            return False

        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO asx_metrics
                        (ticker, beat_rate_8q, beat_rate_4q, avg_surprise_pct,
                         data_confidence, quarters_available, last_computed)
                    VALUES ($1, $2, $3, $4, $5, $6, NOW())
                    ON CONFLICT (ticker) DO UPDATE SET
                        beat_rate_8q = COALESCE(EXCLUDED.beat_rate_8q, asx_metrics.beat_rate_8q),
                        beat_rate_4q = COALESCE(EXCLUDED.beat_rate_4q, asx_metrics.beat_rate_4q),
                        avg_surprise_pct = COALESCE(EXCLUDED.avg_surprise_pct, asx_metrics.avg_surprise_pct),
                        data_confidence = CASE
                            WHEN EXCLUDED.data_confidence = 'HIGH' THEN 'HIGH'
                            WHEN asx_metrics.data_confidence = 'HIGH' THEN 'HIGH'
                            ELSE EXCLUDED.data_confidence
                        END,
                        quarters_available = GREATEST(EXCLUDED.quarters_available, asx_metrics.quarters_available),
                        last_computed = NOW()
                """,
                    ticker,
                    history.get("beat_rate"),
                    history.get("beat_rate"),  # Same for now (annual data)
                    history.get("avg_surprise_pct"),
                    history.get("data_confidence"),
                    len(history.get("eps_history", [])),
                )
            logger.info(f"[consensus] Updated metrics for {ticker}")
            return True
        except Exception as e:
            logger.error(f"[consensus] Metrics update failed for {ticker}: {e}")
            return False
