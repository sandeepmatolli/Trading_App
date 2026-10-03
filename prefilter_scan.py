"""Run cheap NSE fundamental/event pre-filter before Groww deep research.

Examples:
    python prefilter_scan.py
    python prefilter_scan.py --symbols AAATECH,20MICRONS,RELIANCE,TCS
    python prefilter_scan.py --max-deep-symbols 20 --run-main

By default this only writes a report and an optional research queue; it does
not call Groww or execute any trade. --run-main explicitly launches existing
manual-execution research main.py using just the selected NSE symbols.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List

from config import (
    DEFAULT_MAX_SYMBOLS,
    OUTPUT_DIR,
    PROJECT_ROOT,
    SCREENER_CSV_PATH,
    STRICT_CORE50_DEFAULT,
)
from fundamentals.csv_validator import validate_screener_csv
from news.persistent_sources import fetch_nse_announcements
from screening.pre_filter import scan_pre_filter, selected_symbols


def parse_symbols(value: str) -> List[str]:
    return list(dict.fromkeys(
        item.strip().upper() for item in value.split(",") if item.strip()
    ))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="NSE cheap pre-Groww research gate; no automatic orders"
    )
    parser.add_argument("--csv", default=str(SCREENER_CSV_PATH))
    parser.add_argument("--symbols", default="", help="Optional comma-separated NSE symbols")
    parser.add_argument("--strict-core50", action="store_true", default=STRICT_CORE50_DEFAULT)
    parser.add_argument("--max-deep-symbols", type=int, default=DEFAULT_MAX_SYMBOLS)
    parser.add_argument(
        "--skip-review", action="store_true",
        help="Do not include REVIEW_REQUIRED names in the research queue",
    )
    parser.add_argument(
        "--run-main", action="store_true",
        help="Explicitly start existing main.py for selected queue (manual research only)",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_deep_symbols < 1:
        raise ValueError("--max-deep-symbols must be greater than zero")
    df = validate_screener_csv(args.csv, strict_core50=args.strict_core50)
    bundle = fetch_nse_announcements()
    requested = parse_symbols(args.symbols) if args.symbols.strip() else None
    report = scan_pre_filter(df, bundle, requested_symbols=requested)
    selection = selected_symbols(
        report, max_symbols=args.max_deep_symbols, skip_review=args.skip_review,
    )
    report["selected_symbols"] = selection
    report["selection_policy"] = (
        "First eligible symbols in original Screener export order; NOT ranked"
    )
    report["skip_review"] = bool(args.skip_review)

    output = Path(OUTPUT_DIR)
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "prefilter_report.json"
    queue_path = output / "prefilter_selected_symbols.txt"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    queue_path.write_text("\n".join(selection) + ("\n" if selection else ""), encoding="utf-8")

    print("Pre-filter rows:", report["total_checked"])
    print("Breakdown:", report["counts"])
    if report["unknown_requested_symbols"]:
        print("Requested NSE symbols not in CSV:", report["unknown_requested_symbols"])
    print("Selected for deep research (CSV order, not ranked):", selection)
    print("Event source candidate eligible:", report["source_event_candidate_eligible"])
    print("Report:", report_path)
    print("Selected symbol queue:", queue_path)
    print("Important: REVIEW_REQUIRED names are not confirmed candidates; verify result periods and missing data.")
    print("This stage does not infer SMC trend, accept adjusted OHLCV, or place trades.")

    if not args.run_main:
        return 0
    if not selection:
        print("No selected symbols; main.py was NOT launched.")
        return 0
    command = [
        sys.executable,
        str(PROJECT_ROOT / "main.py"),
        "--csv", str(Path(args.csv).resolve()),
        "--symbols", ",".join(selection),
    ]
    print("Starting existing Groww/SMC research pipeline for selected symbols only.")
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())