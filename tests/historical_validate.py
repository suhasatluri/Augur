"""Historical validation pipeline — compare Augur predictions to actual earnings outcomes.

Usage:
    python3 tests/historical_validate.py --dry-run          # show which rows would be tested
    python3 tests/historical_validate.py                     # run simulations and compare
    python3 tests/historical_validate.py --season AUG2024   # filter to one season
    python3 tests/historical_validate.py --ticker BHP,CBA   # filter to specific tickers
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "asx100_earnings_history.csv"

# Map Augur verdicts to simplified outcomes for comparison
VERDICT_TO_OUTCOME = {
    "BEAT": "BEAT",
    "LIKELY BEAT": "BEAT",
    "MISS": "MISS",
    "LIKELY MISS": "MISS",
    "INLINE": "INLINE",
    "TOO CLOSE TO CALL": "INLINE",
}

SKIP_RESULTS = {"UNKNOWN"}


@dataclass
class ValidationRow:
    ticker: str
    company_name: str
    reporting_date: str
    season: str
    actual_result: str
    actual_eps: str
    consensus_eps: str
    surprise_pct: str
    currency: str
    notes: str
    source_url: str

    @property
    def is_usable(self) -> bool:
        return self.actual_result not in SKIP_RESULTS


@dataclass
class ValidationResult:
    row: ValidationRow
    augur_verdict: str = ""
    augur_confidence: float = 0.0
    mapped_verdict: str = ""
    correct: bool = False
    error: str = ""
    duration_s: float = 0.0


@dataclass
class ValidationSummary:
    total_rows: int = 0
    skipped_rows: int = 0
    tested_rows: int = 0
    correct: int = 0
    incorrect: int = 0
    errors: int = 0
    results: list[ValidationResult] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        if self.tested_rows - self.errors == 0:
            return 0.0
        return self.correct / (self.tested_rows - self.errors)

    def print_summary(self):
        print("\n" + "=" * 70)
        print("  AUGUR HISTORICAL VALIDATION SUMMARY")
        print("=" * 70)
        print(f"  Total CSV rows:     {self.total_rows}")
        print(f"  Skipped (UNKNOWN):  {self.skipped_rows}")
        print(f"  Tested:             {self.tested_rows}")
        print(f"  Correct:            {self.correct}")
        print(f"  Incorrect:          {self.incorrect}")
        print(f"  Errors:             {self.errors}")
        if self.tested_rows - self.errors > 0:
            print(f"  Accuracy:           {self.accuracy:.1%}")
        print("=" * 70)

        if self.results:
            print(f"\n  {'Ticker':<8} {'Season':<10} {'Actual':<10} {'Augur':<18} {'Match':<8} {'Time'}")
            print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*18} {'-'*8} {'-'*8}")
            for r in self.results:
                if r.error:
                    match_str = "ERROR"
                    verdict_str = r.error[:18]
                else:
                    match_str = "YES" if r.correct else "NO"
                    verdict_str = f"{r.augur_verdict} ({r.augur_confidence:.0%})"
                print(
                    f"  {r.row.ticker:<8} {r.row.season:<10} {r.row.actual_result:<10} "
                    f"{verdict_str:<18} {match_str:<8} {r.duration_s:.1f}s"
                )
        print()


def load_csv(
    path: Path,
    season_filter: str | None = None,
    ticker_filter: list[str] | None = None,
) -> list[ValidationRow]:
    """Load and filter the historical earnings CSV."""
    if not path.exists():
        print(f"ERROR: CSV not found at {path}")
        sys.exit(1)

    rows: list[ValidationRow] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            row = ValidationRow(
                ticker=raw["ticker"].strip(),
                company_name=raw["company_name"].strip(),
                reporting_date=raw["reporting_date"].strip(),
                season=raw["season"].strip(),
                actual_result=raw["actual_result"].strip(),
                actual_eps=raw.get("actual_eps", "").strip(),
                consensus_eps=raw.get("consensus_eps", "").strip(),
                surprise_pct=raw.get("surprise_pct", "").strip(),
                currency=raw.get("currency", "").strip(),
                notes=raw.get("notes", "").strip(),
                source_url=raw.get("source_url", "").strip(),
            )
            if season_filter and row.season != season_filter:
                continue
            if ticker_filter and row.ticker not in ticker_filter:
                continue
            rows.append(row)
    return rows


def dry_run(rows: list[ValidationRow]):
    """Show which rows would be tested without running simulations."""
    usable = [r for r in rows if r.is_usable]
    skipped = [r for r in rows if not r.is_usable]

    print("\n" + "=" * 70)
    print("  DRY RUN — Historical Validation")
    print("=" * 70)
    print(f"\n  Total rows: {len(rows)}")
    print(f"  Usable (would be tested): {len(usable)}")
    print(f"  Skipped (UNKNOWN): {len(skipped)}")

    if usable:
        print(f"\n  ROWS TO TEST:")
        print(f"  {'Ticker':<8} {'Season':<10} {'Actual':<10} {'Date':<12} {'Currency':<8} {'Notes'}")
        print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*12} {'-'*8} {'-'*30}")
        for r in usable:
            notes = r.notes[:30] if r.notes else ""
            print(
                f"  {r.ticker:<8} {r.season:<10} {r.actual_result:<10} "
                f"{r.reporting_date:<12} {r.currency:<8} {notes}"
            )

    if skipped:
        print(f"\n  SKIPPED ROWS:")
        for r in skipped:
            print(f"  {r.ticker:<8} {r.season:<10} — UNKNOWN result, no ground truth")

    print()


async def run_simulation(row: ValidationRow) -> ValidationResult:
    """Run a single Augur simulation and compare to actual result."""
    from pipeline import run_full_pipeline

    result = ValidationResult(row=row)
    sim_id = f"validate-{row.ticker}-{row.season}-{int(time.time())}"

    try:
        start = time.monotonic()
        report = await run_full_pipeline(
            simulation_id=sim_id,
            ticker=row.ticker,
            reporting_date=row.reporting_date,
        )
        result.duration_s = time.monotonic() - start

        # Extract verdict from report
        result.augur_verdict = report.verdict
        result.augur_confidence = report.confidence

        # Map to simplified outcome
        result.mapped_verdict = VERDICT_TO_OUTCOME.get(
            report.verdict.upper(), report.verdict.upper()
        )

        # Compare
        result.correct = result.mapped_verdict == row.actual_result

    except Exception as e:
        result.error = str(e)[:100]
        result.duration_s = time.monotonic() - start

    return result


async def run_validation(rows: list[ValidationRow]) -> ValidationSummary:
    """Run simulations for all usable rows and produce summary."""
    summary = ValidationSummary(total_rows=len(rows))

    usable = [r for r in rows if r.is_usable]
    skipped = [r for r in rows if not r.is_usable]
    summary.skipped_rows = len(skipped)
    summary.tested_rows = len(usable)

    for row in usable:
        print(f"  Running: {row.ticker} ({row.season})...", end=" ", flush=True)
        result = await run_simulation(row)

        if result.error:
            summary.errors += 1
            print(f"ERROR: {result.error}")
        elif result.correct:
            summary.correct += 1
            print(f"CORRECT ({result.augur_verdict}, {result.duration_s:.1f}s)")
        else:
            summary.incorrect += 1
            print(
                f"WRONG — Augur={result.augur_verdict}, Actual={row.actual_result} "
                f"({result.duration_s:.1f}s)"
            )

        summary.results.append(result)

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Validate Augur predictions against historical earnings outcomes"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show which rows would be tested without running simulations",
    )
    parser.add_argument(
        "--season",
        type=str,
        default=None,
        help="Filter to a specific season (e.g., AUG2024, FEB2025)",
    )
    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="Comma-separated list of tickers to test (e.g., BHP,CBA)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    ticker_filter = None
    if args.ticker:
        ticker_filter = [t.strip().upper() for t in args.ticker.split(",")]

    rows = load_csv(CSV_PATH, season_filter=args.season, ticker_filter=ticker_filter)

    if not rows:
        print("No rows match the filters.")
        sys.exit(0)

    if args.dry_run:
        dry_run(rows)
        return

    # Full validation run
    print("\n  Starting Augur Historical Validation...")
    print(f"  WARNING: This will run {sum(1 for r in rows if r.is_usable)} simulations.")
    print(f"  Estimated time: ~{sum(1 for r in rows if r.is_usable) * 170 // 60} minutes\n")

    summary = asyncio.run(run_validation(rows))
    summary.print_summary()


if __name__ == "__main__":
    main()
