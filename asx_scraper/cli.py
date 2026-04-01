"""CLI entry point for the ASX scraper.

Usage:
    python3 -m asx_scraper BHP                # Scrape single ticker
    python3 -m asx_scraper BHP CBA CSL        # Scrape multiple tickers
    python3 -m asx_scraper --asx100           # Scrape full ASX 100
    python3 -m asx_scraper --show BHP         # Show stored data for a ticker
    python3 -m asx_scraper --metrics-only BHP # Recompute metrics only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from asx_scraper.orchestrator import ScraperOrchestrator
from asx_scraper.metrics_computer import MetricsComputer
from db.schema import ensure_schema


class _JSONEncoder(json.JSONEncoder):
    """Handle date/datetime/UUID serialisation."""
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if hasattr(obj, "hex"):  # UUID
            return str(obj)
        return super().default(obj)


def _print_ticker_summary(summary: dict):
    """Pretty-print results for a single ticker."""
    t = summary["ticker"]
    name = summary.get("company_name", "")
    print(f"\n  {'='*55}")
    print(f"  {t} — {name}")
    print(f"  {'='*55}")
    print(f"  Announcements found:     {summary['announcements_found']}")
    print(f"  Quarters extracted:      {summary['quarters_extracted']}")
    print(f"  Price reactions updated: {summary['price_reactions_updated']}")
    print(f"  Beat rate (8q):          {summary.get('beat_rate')}")
    print(f"  Data confidence:         {summary.get('data_confidence')}")
    print(f"  Duration:                {summary.get('duration_s', 0):.1f}s")

    if summary.get("extracted_records"):
        print(f"\n  Extracted earnings:")
        print(f"    {'Period':<15} {'Date':<12} {'Rev($m)':<10} {'NPAT($m)':<10} {'EPS(c)':<8} {'Div(c)':<8} {'Conf'}")
        print(f"    {'-'*15} {'-'*12} {'-'*10} {'-'*10} {'-'*8} {'-'*8} {'-'*6}")
        for r in summary["extracted_records"]:
            rev = r.get("revenue_aud_m")
            npat = r.get("npat_aud_m")
            eps = r.get("eps_basic_cents")
            div = r.get("dividend_cents")
            print(
                f"    {str(r.get('period', '')):<15} "
                f"{str(r.get('reporting_date', '')):<12} "
                f"{f'{rev:,.0f}' if rev else '—':>10} "
                f"{f'{npat:,.0f}' if npat else '—':>10} "
                f"{f'{eps:.1f}' if eps else '—':>8} "
                f"{f'{div:.0f}' if div else '—':>8} "
                f"{r.get('data_confidence', '?')}"
            )

    if summary["errors"]:
        print(f"\n  Errors:")
        for e in summary["errors"]:
            print(f"    - {e}")
    print()


def _print_show(ticker: str, data: dict):
    """Pretty-print stored data for --show."""
    print(f"\n  {'='*60}")
    print(f"  STORED DATA: {ticker}")
    print(f"  {'='*60}")

    c = data.get("company")
    if c:
        print(f"\n  Company:")
        print(f"    Name:           {c.get('company_name')}")
        print(f"    Sector:         {c.get('sector')}")
        print(f"    Industry:       {c.get('industry')}")
        print(f"    Market cap:     {c.get('market_cap_aud')}")
        print(f"    FY end:         {c.get('fiscal_year_end')}")
    else:
        print(f"\n  Company: (no data)")

    earnings = data.get("earnings", [])
    if earnings:
        print(f"\n  Earnings ({len(earnings)} records):")
        print(f"    {'Period':<15} {'Date':<12} {'EPS(c)':<10} {'Beat/Miss':<10} {'Surp%':<8} {'Conf':<6} {'Src'}")
        print(f"    {'-'*15} {'-'*12} {'-'*10} {'-'*10} {'-'*8} {'-'*6} {'-'*10}")
        for e in earnings:
            eps = e.get("eps_basic_cents")
            eps_str = f"{eps:.1f}" if eps else "—"
            surp = e.get("surprise_pct")
            surp_str = f"{surp:+.1f}" if surp else "—"
            print(
                f"    {str(e.get('period', '')):<15} "
                f"{str(e.get('reporting_date', '')):<12} "
                f"{eps_str:<10} "
                f"{str(e.get('beat_miss', '')):<10} "
                f"{surp_str:<8} "
                f"{str(e.get('data_confidence', '')):<6} "
                f"{str(e.get('data_source', ''))}"
            )
    else:
        print(f"\n  Earnings: (no data)")

    m = data.get("metrics")
    if m:
        print(f"\n  Metrics:")
        print(f"    Beat rate (8q):     {m.get('beat_rate_8q')}")
        print(f"    Beat rate (4q):     {m.get('beat_rate_4q')}")
        print(f"    Avg surprise:       {m.get('avg_surprise_pct')}")
        print(f"    Credibility:        {m.get('mgmt_credibility_score')}")
        print(f"    Confidence:         {m.get('data_confidence')}")
        print(f"    Quarters available: {m.get('quarters_available')}")
    else:
        print(f"\n  Metrics: (not computed)")

    commentary = data.get("commentary", [])
    if commentary:
        print(f"\n  Commentary ({len(commentary)} quotes):")
        for q in commentary[:5]:
            print(f"    [{q.get('quote_type', '?')}] \"{q.get('quote', '')[:80]}...\"")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="ASX Data Scraper — fetch earnings data for ASX companies"
    )
    parser.add_argument("tickers", nargs="*", help="Tickers to scrape")
    parser.add_argument("--asx100", action="store_true", help="Scrape all ASX 100")
    parser.add_argument("--show", type=str, help="Show stored data for a ticker")
    parser.add_argument("--metrics-only", type=str, help="Recompute metrics only (comma-separated tickers)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")

    async def run():
        await ensure_schema()
        orchestrator = ScraperOrchestrator()

        if args.show:
            data = await orchestrator.show_ticker(args.show)
            _print_show(args.show.upper(), data)
            return

        if args.metrics_only:
            tickers = [t.strip().upper() for t in args.metrics_only.split(",")]
            computer = MetricsComputer()
            results = await computer.compute_all(tickers)
            for r in results:
                print(json.dumps(r, indent=2, cls=_JSONEncoder))
            return

        if args.asx100:
            report = await orchestrator.scrape_asx100()
            print(f"\n  ASX 100 Scrape Complete")
            print(f"  Success: {report['success']}/{report['total']}")
            print(f"  Failed:  {report['failed']}")
            print(f"  Duration: {report['duration_s']:.0f}s")
            return

        if not args.tickers:
            parser.print_help()
            return

        tickers = [t.upper() for t in args.tickers]
        if len(tickers) == 1:
            summary = await orchestrator.scrape_ticker(tickers[0])
            _print_ticker_summary(summary)
        else:
            report = await orchestrator.scrape_batch(tickers, delay=5.0)
            for result in report["results"]:
                _print_ticker_summary(result)
            print(f"  Batch complete: {report['success']}/{report['total']} OK, {report['duration_s']:.0f}s")

    asyncio.run(run())


if __name__ == "__main__":
    main()
