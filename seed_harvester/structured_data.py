"""Structured data fetcher — yfinance + stockanalysis.com hybrid approach."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


class StructuredDataFetcher:
    """Fetches structured financial data for ASX tickers."""

    async def get_ticker_data(self, ticker: str) -> dict:
        """Fetch all available data for a ticker. Returns raw values dict.

        Tries pre-scraped DB data first (asx_scraper), falls back to live yfinance + stockanalysis.
        Enriches with consensus data from yfinance earnings_estimate.
        Adds ASIC short interest and analyst target spread.
        """
        # Try pre-scraped data from asx_scraper tables
        db_data = await self.get_from_db(ticker)
        if db_data:
            logger.info(f"[structured] Using pre-scraped DB data for {ticker}")
            db_data = await self._enrich_consensus(ticker, db_data)
            db_data = await self._enrich_short_interest(ticker, db_data)
            db_data = await self._enrich_director_signal(ticker, db_data)
            return db_data

        data: dict = {"ticker": ticker, "source_yfinance": {}, "source_stockanalysis": {}}

        # --- Source 1: yfinance ---
        def _fetch_yfinance():
            stock = yf.Ticker(f"{ticker}.AX")
            info = stock.info or {}
            yf_fields = {
                "longName": info.get("longName"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "currentPrice": info.get("currentPrice"),
                "targetMeanPrice": info.get("targetMeanPrice"),
                "targetHighPrice": info.get("targetHighPrice"),
                "targetLowPrice": info.get("targetLowPrice"),
                "recommendationMean": info.get("recommendationMean"),
                "recommendationKey": info.get("recommendationKey"),
                "earningsGrowth": info.get("earningsGrowth"),
                "revenueGrowth": info.get("revenueGrowth"),
                "returnOnEquity": info.get("returnOnEquity"),
                "debtToEquity": info.get("debtToEquity"),
                "forwardPE": info.get("forwardPE"),
                "trailingPE": info.get("trailingPE"),
                "dividendYield": info.get("dividendYield"),
                "marketCap": info.get("marketCap"),
            }
            # Analyst target spread — measures analyst disagreement
            target_mean = info.get("targetMeanPrice", 0) or 0
            target_high = info.get("targetHighPrice", 0) or 0
            target_low = info.get("targetLowPrice", 0) or 0
            if target_mean > 0:
                spread = target_high - target_low
                yf_fields["analyst_spread_pct"] = round((spread / target_mean) * 100, 2)
            else:
                yf_fields["analyst_spread_pct"] = 0.0

            if yf_fields["analyst_spread_pct"] > 40:
                yf_fields["analyst_consensus_quality"] = "HIGH_DISAGREEMENT"
            elif yf_fields["analyst_spread_pct"] > 20:
                yf_fields["analyst_consensus_quality"] = "MODERATE_DISAGREEMENT"
            else:
                yf_fields["analyst_consensus_quality"] = "CONSENSUS"

            # Calendar — next earnings date
            try:
                cal = stock.calendar
                if cal is not None:
                    if isinstance(cal, dict):
                        ed = cal.get("Earnings Date")
                        if ed and isinstance(ed, list) and len(ed) > 0:
                            yf_fields["nextEarningsDate"] = str(ed[0])
                        elif ed:
                            yf_fields["nextEarningsDate"] = str(ed)
                    elif hasattr(cal, "to_dict"):
                        cal_dict = cal.to_dict()
                        ed = cal_dict.get("Earnings Date")
                        if ed:
                            yf_fields["nextEarningsDate"] = str(ed)
            except Exception:
                pass
            return yf_fields, info.get("longName", "N/A")

        try:
            yf_fields, long_name = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, _fetch_yfinance),
                timeout=30.0,
            )
            data["source_yfinance"] = yf_fields
            logger.info(f"[structured] yfinance OK for {ticker}: {long_name}")
        except asyncio.TimeoutError:
            logger.error(f"[structured] yfinance timed out for {ticker} (30s)")
        except Exception as e:
            logger.error(f"[structured] yfinance failed for {ticker}: {e}")

        # --- Source 2: stockanalysis.com beat/miss ---
        data["source_stockanalysis"] = await self._fetch_beat_miss(ticker)

        # --- Source 3: consensus enrichment ---
        data = await self._enrich_consensus(ticker, data)

        # --- Source 4: ASIC short interest ---
        data = await self._enrich_short_interest(ticker, data)

        # --- Source 5: Director transactions from Neon ---
        data = await self._enrich_director_signal(ticker, data)

        return data

    async def _enrich_short_interest(self, ticker: str, data: dict) -> dict:
        """Add ASIC short interest data if available."""
        try:
            from asx_scraper.sources.asic_short_interest import download_asic_data, get_short_interest

            def _fetch():
                asic_data = download_asic_data()
                return get_short_interest(ticker, asic_data)

            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, _fetch),
                timeout=15.0,
            )
            if result:
                data["source_asic_short"] = result
                logger.info(
                    f"[structured] ASIC short for {ticker}: "
                    f"{result['pct_shorted']}% ({result['signal']})"
                )
        except Exception as e:
            logger.debug(f"[structured] ASIC short interest failed for {ticker}: {e}")
        return data

    async def _enrich_director_signal(self, ticker: str, data: dict) -> dict:
        """Look up director transaction signal from Neon (pre-scraped via Appendix 3Y)."""
        try:
            from db.schema import get_pool
            from asx_scraper.sources.director_trades import compute_director_signal as _compute

            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT txn_type, value FROM director_transactions
                       WHERE ticker = $1 ORDER BY scraped_at DESC LIMIT 50""",
                    ticker.upper(),
                )
                if rows:
                    trades = [{"type": r["txn_type"], "value": float(r["value"] or 0)} for r in rows]
                    signal = _compute(trades)
                    data["source_director"] = signal
                    logger.info(
                        f"[structured] Director signal for {ticker}: "
                        f"{signal['signal']} (net=${signal['net_buy_value']:,.0f})"
                    )
        except Exception as e:
            logger.debug(f"[structured] Director signal lookup failed for {ticker}: {e}")
        return data

    async def _fetch_beat_miss(self, ticker: str) -> dict:
        """Scrape beat/miss history from stockanalysis.com earnings page."""
        result = {"beat_rate": None, "quarters_beat": None, "quarters_total": None, "raw_surprises": []}
        url = f"https://stockanalysis.com/stocks/{ticker.lower()}/financials/"

        try:
            import urllib.request
            import ssl
            ctx = ssl.create_default_context()
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            surprises: list[float] = []

            # Pattern 1: "Beat by X%" or "Missed by X%"
            beat_matches = re.findall(r'Beat\s+by\s+([\d.]+)%', html, re.IGNORECASE)
            miss_matches = re.findall(r'Miss(?:ed)?\s+by\s+([\d.]+)%', html, re.IGNORECASE)

            if beat_matches or miss_matches:
                for m in beat_matches:
                    surprises.append(float(m))
                for m in miss_matches:
                    surprises.append(-float(m))

            # Pattern 2: surprise percentage values near "surprise"
            if not surprises:
                surprise_matches = re.findall(r'surprise[^<]{0,50}?([+-]?\d+\.?\d*)%', html, re.IGNORECASE)
                for m in surprise_matches:
                    try:
                        surprises.append(float(m))
                    except ValueError:
                        pass

            # Pattern 3: EPS actual vs estimate
            if not surprises:
                eps_pattern = re.findall(
                    r'(?:actual|eps)[^<]{0,30}?(\d+\.?\d*)[^<]{0,50}?(?:estimate|expected)[^<]{0,30}?(\d+\.?\d*)',
                    html, re.IGNORECASE,
                )
                for actual, estimate in eps_pattern[:8]:
                    try:
                        a, e = float(actual), float(estimate)
                        if e > 0:
                            surprises.append(((a - e) / e) * 100)
                    except (ValueError, ZeroDivisionError):
                        pass

            if surprises:
                surprises = surprises[:8]
                beats = sum(1 for s in surprises if s > 0)
                result["quarters_beat"] = beats
                result["quarters_total"] = len(surprises)
                result["beat_rate"] = beats / len(surprises)
                result["raw_surprises"] = [round(s, 2) for s in surprises]
                logger.info(f"[structured] stockanalysis OK for {ticker}: beat_rate={result['beat_rate']:.2f} ({beats}/{len(surprises)})")
            else:
                logger.info(f"[structured] stockanalysis: no surprise data found in HTML for {ticker}")

        except Exception as e:
            logger.warning(f"[structured] stockanalysis scrape failed for {ticker}: {e}")

        return result

    async def _enrich_consensus(self, ticker: str, data: dict) -> dict:
        """Add blended beat_rate from ConsensusHarvester (yfinance + Perplexity)."""
        try:
            from asx_scraper.consensus_harvester import ConsensusHarvester
            harvester = ConsensusHarvester()
            blended = await harvester.get_blended_beat_rate(ticker)

            if blended.get("data_confidence") in ("HIGH", "MED"):
                sa = data.setdefault("source_stockanalysis", {})
                sa["beat_rate"] = blended["blended_beat_rate"]
                sa["beat_rate_source"] = "yfinance_consensus+perplexity"
                data["source_consensus"] = {
                    "consensus_eps": blended.get("consensus_eps"),
                    "analyst_count": blended.get("analyst_count"),
                    "yfinance_beat_rate": blended.get("yfinance_beat_rate"),
                    "perplexity_adjustment": blended.get("perplexity_adjustment"),
                    "blended_beat_rate": blended.get("blended_beat_rate"),
                    "data_confidence": blended.get("data_confidence"),
                }
                logger.info(
                    f"[structured] Consensus enrichment for {ticker}: "
                    f"blended={blended['blended_beat_rate']:.3f} "
                    f"(yf={blended['yfinance_beat_rate']:.2f}, "
                    f"pplx={blended['perplexity_adjustment']:+.2f})"
                )
        except Exception as e:
            logger.debug(f"[structured] Consensus enrichment failed for {ticker}: {e}")
        return data

    async def get_from_db(self, ticker: str) -> Optional[dict]:
        """Read pre-scraped data from Neon. Falls back to None if unavailable.

        Returns the same structure as get_ticker_data() but with real beat_rate
        from asx_metrics instead of stockanalysis.com scraping.
        """
        try:
            from db.schema import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                metrics = await conn.fetchrow(
                    "SELECT * FROM asx_metrics WHERE ticker = $1", ticker.upper()
                )
                if not metrics or metrics["data_confidence"] == "LOW":
                    return None

                company = await conn.fetchrow(
                    "SELECT * FROM asx_companies WHERE ticker = $1", ticker.upper()
                )

                # Build the same structure as get_ticker_data so compute_ticker_bias_score works
                data: dict = {
                    "ticker": ticker.upper(),
                    "source_yfinance": {},
                    "source_stockanalysis": {
                        "beat_rate": metrics["beat_rate_8q"],
                        "quarters_beat": None,
                        "quarters_total": metrics["quarters_available"],
                        "raw_surprises": [],
                    },
                    "source_asx_scraper": {
                        "beat_rate_8q": metrics["beat_rate_8q"],
                        "beat_rate_4q": metrics["beat_rate_4q"],
                        "avg_surprise_pct": metrics["avg_surprise_pct"],
                        "mgmt_credibility": metrics["mgmt_credibility_score"],
                        "data_confidence": metrics["data_confidence"],
                    },
                }

                if company:
                    data["source_yfinance"]["longName"] = company["company_name"]
                    data["source_yfinance"]["sector"] = company["sector"]
                    data["source_yfinance"]["industry"] = company["industry"]

                # Fetch company intel outlook
                try:
                    intel = await conn.fetchrow(
                        "SELECT combined_signals FROM asx_company_intel WHERE ticker = $1",
                        ticker.upper(),
                    )
                    if intel and intel["combined_signals"]:
                        import json as _json
                        signals = _json.loads(intel["combined_signals"])
                        data["source_company_intel"] = {
                            "overall_outlook": signals.get("overall_outlook"),
                            "margin_trend": signals.get("margin_trend"),
                            "cost_trend": signals.get("cost_trend"),
                        }
                except Exception as e:
                    logger.debug(f"[structured] Company intel lookup failed for {ticker}: {e}")

                # Still need yfinance for price/recommendation data
                try:
                    import yfinance as yf

                    def _fetch_yf_supplement():
                        stock = yf.Ticker(f"{ticker.upper()}.AX")
                        info = stock.info or {}
                        return {
                            "currentPrice": info.get("currentPrice"),
                            "targetMeanPrice": info.get("targetMeanPrice"),
                            "recommendationMean": info.get("recommendationMean"),
                            "earningsGrowth": info.get("earningsGrowth"),
                        }

                    supp = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(None, _fetch_yf_supplement),
                        timeout=30.0,
                    )
                    data["source_yfinance"].update(supp)
                except asyncio.TimeoutError:
                    logger.debug(f"[structured] yfinance supplement timed out for {ticker}")
                except Exception as e:
                    logger.debug(f"[structured] yfinance supplement failed for {ticker}: {e}")

                logger.info(
                    f"[structured] DB data for {ticker}: "
                    f"beat_rate={metrics['beat_rate_8q']}, "
                    f"confidence={metrics['data_confidence']}"
                )
                return data

        except Exception as e:
            logger.debug(f"[structured] DB lookup failed for {ticker}: {e}")
            return None

    def compute_ticker_bias_score(self, data: dict) -> tuple[float, dict]:
        """Compute ticker_bias_score from structured data. Returns (score, breakdown).

        6 components:
        1. Recommendation (25%) — analyst buy/hold/sell consensus
        2. Upside (20%) — target price vs current price
        3. Growth (15%) — earnings growth rate
        4. Beat rate (15%) — historical beat/miss from price proxy
        5. Short interest (15%) — ASIC short position signal (inverse)
        6. Director signal (10%) — net director buy/sell from Market Index
        """
        yf_data = data.get("source_yfinance", {})
        sa_data = data.get("source_stockanalysis", {})
        short_data = data.get("source_asic_short", {})
        director_data = data.get("source_director", {})

        breakdown = {}

        # 1. Recommendation component (25%)
        rec_mean = yf_data.get("recommendationMean")
        if rec_mean is not None and 1.0 <= rec_mean <= 5.0:
            rec_component = (5.0 - rec_mean) / 4.0
        else:
            rec_component = 0.5
        breakdown["rec_component"] = {"value": rec_component, "raw": rec_mean, "weight": 0.25}

        # 2. Upside component (20%)
        target = yf_data.get("targetMeanPrice")
        current = yf_data.get("currentPrice")
        if target is not None and current is not None and current > 0:
            upside = (target - current) / current
            upside_component = _clamp(0.5 + upside / 2)
        else:
            upside_component = 0.5
            upside = None
        breakdown["upside_component"] = {"value": upside_component, "raw_upside": upside, "weight": 0.20}

        # 3. Growth component (15%)
        eg = yf_data.get("earningsGrowth")
        if eg is not None:
            growth_component = _clamp(0.5 + eg / 2)
        else:
            growth_component = 0.5
        breakdown["growth_component"] = {"value": growth_component, "raw": eg, "weight": 0.15}

        # 4. Beat rate component (15%)
        _BANK_TICKERS = {"CBA", "WBC", "ANZ", "NAB", "MQG"}
        ticker = data.get("ticker", "").upper()
        beat_rate = sa_data.get("beat_rate")
        beat_rate_source = sa_data.get("beat_rate_source", "price_proxy")
        has_consensus = "consensus" in beat_rate_source

        if has_consensus and beat_rate is not None:
            beat_component = _clamp(beat_rate)
            beat_rate_raw = beat_rate
        elif ticker in _BANK_TICKERS and not has_consensus:
            logger.warning(
                f"[structured] Bank price proxy unreliable for {ticker} "
                f"(sell-the-news effect) — beat_rate fallback to 0.50"
            )
            beat_component = 0.5
            beat_rate_raw = f"{beat_rate} (overridden — bank sector, no consensus)"
        elif beat_rate is not None:
            beat_component = _clamp(beat_rate)
            beat_rate_raw = beat_rate
        else:
            beat_component = 0.5
            beat_rate_raw = beat_rate
        breakdown["beat_rate_component"] = {"value": beat_component, "raw": beat_rate_raw, "weight": 0.15}

        # 5. Short interest component (15%) — ASIC data, inverse signal
        short_score = short_data.get("signal_score", 0.5) if short_data else 0.5
        breakdown["short_interest_component"] = {
            "value": short_score,
            "raw_pct": short_data.get("pct_shorted") if short_data else None,
            "signal": short_data.get("signal") if short_data else "N/A",
            "weight": 0.15,
        }

        # 6. Director signal component (10%)
        director_score = director_data.get("signal_score", 0.5) if director_data else 0.5
        breakdown["director_component"] = {
            "value": director_score,
            "signal": director_data.get("signal") if director_data else "N/A",
            "raw_net": director_data.get("net_buy_value") if director_data else None,
            "weight": 0.10,
        }

        # Final score
        score = (
            rec_component * 0.25
            + upside_component * 0.20
            + growth_component * 0.15
            + beat_component * 0.15
            + short_score * 0.15
            + director_score * 0.10
        )
        score = _clamp(score, 0.20, 0.80)

        breakdown["final_score"] = score
        return score, breakdown


# --- Standalone test ---
if __name__ == "__main__":
    import asyncio
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    async def test():
        fetcher = StructuredDataFetcher()

        for ticker in ["BHP", "CSL", "XRO"]:
            data = await fetcher.get_ticker_data(ticker)
            score, breakdown = fetcher.compute_ticker_bias_score(data)

            yf = data["source_yfinance"]
            sa = data["source_stockanalysis"]

            print(f"\n{'='*60}")
            print(f"  {ticker} — {yf.get('longName', 'N/A')}")
            print(f"  Sector: {yf.get('sector', 'N/A')} / {yf.get('industry', 'N/A')}")
            print(f"{'='*60}")

            print(f"\n  --- yfinance raw values ---")
            print(f"  currentPrice:      {yf.get('currentPrice')}")
            print(f"  targetMeanPrice:   {yf.get('targetMeanPrice')}")
            print(f"  recommendationMean:{yf.get('recommendationMean')} ({yf.get('recommendationKey')})")
            print(f"  earningsGrowth:    {yf.get('earningsGrowth')}")
            print(f"  revenueGrowth:     {yf.get('revenueGrowth')}")
            print(f"  returnOnEquity:    {yf.get('returnOnEquity')}")
            print(f"  debtToEquity:      {yf.get('debtToEquity')}")
            print(f"  forwardPE:         {yf.get('forwardPE')}")
            print(f"  trailingPE:        {yf.get('trailingPE')}")
            print(f"  dividendYield:     {yf.get('dividendYield')}")
            print(f"  nextEarningsDate:  {yf.get('nextEarningsDate', 'N/A')}")

            print(f"\n  --- stockanalysis.com beat/miss ---")
            print(f"  beat_rate:         {sa.get('beat_rate')}")
            print(f"  quarters_beat:     {sa.get('quarters_beat')}")
            print(f"  quarters_total:    {sa.get('quarters_total')}")
            print(f"  raw_surprises:     {sa.get('raw_surprises', [])}")

            print(f"\n  --- Component scores ---")
            for name, comp in breakdown.items():
                if name == "final_score":
                    continue
                raw_str = ""
                for k, v in comp.items():
                    if k not in ("value", "weight"):
                        raw_str += f" {k}={v}"
                print(f"  {name:22s} = {comp['value']:.3f}  (weight={comp['weight']:.0%},{raw_str})")

            print(f"\n  TICKER BIAS SCORE: {score:.3f}")
            print(f"  {'─'*40}")

    asyncio.run(test())
