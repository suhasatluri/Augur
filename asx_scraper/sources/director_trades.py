"""Director trade extractor via ASX Appendix 3Y announcements.

Appendix 3Y = "Change of Director's Interest Notice" — filed by ASX-listed
companies whenever a director buys or sells shares on-market.

Uses the ASX Markit Digital API (free, no auth) to find announcements,
then Claude to extract structured trade data from the PDF.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import ssl
import urllib.request
from datetime import datetime
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)

APPENDIX_3Y_KEYWORDS = ["Appendix 3Y", "Appendix3Y", "Director's Interest", "Change of Director"]

ASX_PDF_BASE = "https://cdn-api.markitdigital.com/apiman-gateway/ASX/asx-research/1.0/file/"
ASX_PDF_TOKEN = "83ff96335c2d45a094df02a206a39ff4"

EXTRACTION_PROMPT = """Extract director share transaction details from this ASX Appendix 3Y filing.

Return a JSON array of transactions. Each transaction should have:
- director: full name of the director
- type: "Buy" or "Sell" (on-market trades only, exclude "Issued" options/grants)
- date: transaction date as "YYYY-MM-DD" if available, else the filing date
- amount: number of shares (integer, positive)
- price: price per share in AUD (float)
- value: total value in AUD (float, amount * price)
- notes: brief note about the transaction (e.g. "On-market purchase", "Indirect holding via trust")

Rules:
- Only include ON-MARKET Buy and Sell transactions
- Exclude option exercises, rights issues, dividend reinvestment plans
- If price is not stated, estimate from "consideration" field
- If multiple transactions in one filing, return all of them
- Return empty array [] if no on-market trades found

Return ONLY a JSON array. No markdown, no commentary."""


async def _download_pdf(url: str) -> Optional[bytes]:
    """Download a PDF from ASX announcements."""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })

    def do_fetch():
        with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
            return resp.read()

    try:
        return await asyncio.get_event_loop().run_in_executor(None, do_fetch)
    except Exception as e:
        logger.warning(f"PDF download failed: {url} — {e}")
        return None


async def get_director_announcements(ticker: str, count: int = 50) -> list[dict]:
    """Fetch Appendix 3Y announcements for a ticker via ASX Markit API."""
    from asx_scraper.asx_api import ASXMarketData

    api = ASXMarketData()
    all_anns = await api.get_announcements(ticker, count=count)

    results = []
    for ann in all_anns:
        headline = ann.get("headline", "")
        if any(kw.lower() in headline.lower() for kw in APPENDIX_3Y_KEYWORDS):
            results.append(ann)

    logger.info(f"[director] {ticker}: found {len(results)} Appendix 3Y filings out of {len(all_anns)} announcements")
    return results


async def extract_trades_from_pdf(pdf_bytes: bytes, ticker: str) -> list[dict]:
    """Use Claude to extract director trades from an Appendix 3Y PDF."""
    client = anthropic.AsyncAnthropic()

    # Truncate large PDFs
    if len(pdf_bytes) > 5_000_000:
        logger.warning(f"[director] PDF too large ({len(pdf_bytes)} bytes), skipping")
        return []

    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": EXTRACTION_PROMPT,
                    },
                ],
            }],
        )

        text = response.content[0].text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        trades = json.loads(text)
        if not isinstance(trades, list):
            return []

        # Add ticker to each trade
        for t in trades:
            t["ticker"] = ticker.upper()

        return trades

    except Exception as e:
        logger.error(f"[director] Claude extraction failed for {ticker}: {e}")
        return []


async def scrape_director_trades(ticker: str, max_filings: int = 10) -> list[dict]:
    """Full pipeline: find Appendix 3Y filings → download PDFs → extract trades.

    Returns list of trade dicts ready for DB insertion.
    Uses Haiku for extraction (~$0.001 per filing).
    """
    ticker = ticker.upper()
    announcements = await get_director_announcements(ticker)

    if not announcements:
        logger.info(f"[director] {ticker}: no Appendix 3Y filings found")
        return []

    # Limit to most recent filings
    announcements = announcements[:max_filings]
    all_trades = []

    for ann in announcements:
        doc_key = ann.get("document_key", "")
        if not doc_key:
            continue

        pdf_url = f"{ASX_PDF_BASE}{doc_key}?access_token={ASX_PDF_TOKEN}"
        logger.info(f"[director] {ticker}: downloading {ann['headline'][:60]}...")

        pdf_bytes = await _download_pdf(pdf_url)
        if not pdf_bytes:
            continue

        trades = await extract_trades_from_pdf(pdf_bytes, ticker)
        for t in trades:
            t["source_url"] = pdf_url
            t["filing_date"] = ann.get("date", "")
            t["scraped_at"] = datetime.now().isoformat()

        all_trades.extend(trades)
        await asyncio.sleep(0.5)  # Rate limit

    logger.info(f"[director] {ticker}: extracted {len(all_trades)} trades from {len(announcements)} filings")
    return all_trades


def compute_director_signal(trades: list[dict]) -> dict:
    """Compute net buy/sell signal from extracted director trades."""
    buys = [t for t in trades if t.get("type") == "Buy"]
    sells = [t for t in trades if t.get("type") == "Sell"]
    buy_val = sum(float(t.get("value", 0) or 0) for t in buys)
    sell_val = sum(float(t.get("value", 0) or 0) for t in sells)
    net = buy_val - sell_val

    if net > 1_000_000:
        sig, score = "STRONG_BUY", 0.75
    elif net > 100_000:
        sig, score = "BUY", 0.65
    elif net > -100_000:
        sig, score = "NEUTRAL", 0.50
    elif net > -1_000_000:
        sig, score = "SELL", 0.35
    else:
        sig, score = "STRONG_SELL", 0.20

    return {
        "net_buy_value": round(net, 2),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "total_trades": len(trades),
        "signal": sig,
        "signal_score": score,
    }


async def store_director_trades(trades: list[dict]) -> int:
    """Store extracted trades to Neon director_transactions table."""
    if not trades:
        return 0

    from db.schema import get_pool

    pool = await get_pool()
    stored = 0
    async with pool.acquire() as conn:
        for t in trades:
            try:
                await conn.execute(
                    """INSERT INTO director_transactions
                       (ticker, txn_date, director, txn_type, amount, price, value, notes)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                       ON CONFLICT DO NOTHING""",
                    t.get("ticker"),
                    t.get("date") or t.get("filing_date"),
                    t.get("director"),
                    t.get("type"),
                    int(t.get("amount", 0) or 0),
                    float(t.get("price", 0) or 0),
                    float(t.get("value", 0) or 0),
                    t.get("notes", ""),
                )
                stored += 1
            except Exception as e:
                logger.warning(f"[director] Failed to store trade: {e}")

    logger.info(f"[director] Stored {stored}/{len(trades)} trades to Neon")
    return stored
