"""Record finished daily sessions and prepare tomorrow's fresh prefiltered queue.

No automatic trades, Groww requests, background work, or reuse of old NSE
announcements as live evidence. Always run the pre-filter afresh each IST day.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from config import OUTPUT_DIR, PROJECT_ROOT, SCREENER_CSV_PATH
from screening.batch_runner import ScanSafetyError
from screening.campaign import historical_summary, prepare_day, record_session


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Historical multi-day research progress; no orders")
    parser.add_argument("operation", choices=("prepare", "record", "summary"))
    parser.add_argument("--ledger", type=Path, default=OUTPUT_DIR / "campaign" / "campaign.json")
    parser.add_argument("--report", type=Path, default=OUTPUT_DIR / "prefilter_report.json")
    parser.add_argument("--queue", type=Path, default=OUTPUT_DIR / "prefilter_selected_symbols.txt")
    parser.add_argument("--csv", type=Path, default=SCREENER_CSV_PATH)
    parser.add_argument("--state", type=Path, default=OUTPUT_DIR / "larger_scan" / "current_state.json")
    args = parser.parse_args(argv)
    try:
        if args.operation == "prepare":
            result = prepare_day(PROJECT_ROOT, args.ledger, args.report.resolve(), args.queue.resolve(),
                                 args.csv.resolve(), OUTPUT_DIR / "campaign")
            print("Current-day eligible work remaining:", result["pending"])
            print("Archived historical records (not current signals):", result["historical"])
            if result["pending"]:
                print("Batch input report:", result["report"])
                print("Batch input queue:", result["queue"])
                print("Start new daily session using batch_scan.py --new-run --report <report> --queue <queue>")
            else:
                print("No pending eligible symbols in the refreshed queue. No batch should run.")
        elif args.operation == "record":
            result = record_session(args.ledger, args.state)
            print("Recorded session:", result["session_id"])
            print("New historical records:", result["newly_recorded"])
            print("Total historical records:", result["historical_total"])
            print("Ledger:", result["ledger_path"])
        else:
            result = historical_summary(args.ledger)
            print(result)
    except ScanSafetyError as exc:
        print("CAMPAIGN SAFETY STOP:", exc)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())