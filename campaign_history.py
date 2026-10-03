"""Offline historical campaign audit across days; never a live trading signal."""
from __future__ import annotations

import argparse
from pathlib import Path

from config import OUTPUT_DIR
from screening.batch_runner import ScanSafetyError
from screening.campaign_history import build_campaign_history_report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit recorded campaign research from all days; no live data or orders")
    parser.add_argument("--ledger", type=Path, default=OUTPUT_DIR / "campaign" / "campaign.json")
    parser.add_argument("--output", type=Path, default=None, help="Optional .json directly in data/output/campaign/reports")
    args = parser.parse_args(argv)
    try:
        result = build_campaign_history_report(args.ledger, OUTPUT_DIR / "campaign", output_path=args.output)
    except ScanSafetyError as exc:
        print("HISTORICAL CAMPAIGN SAFETY STOP:", exc)
        return 2
    value = result["report"]
    print("Historical report:", result["path"])
    print("Recorded sessions:", value["session_count"], "verified historical rows:", value["historical_record_count"])
    print("Archived decision counts:", value["decision_counts_at_scan"])
    print("Old candidates needing fresh manual revalidation:", value["historical_candidate_count_revalidation_required"])
    print("Not a current-day signal. No Groww/NSE requests or orders were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
