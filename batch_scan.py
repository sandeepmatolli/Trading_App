"""Bounded, restartable research batches over a validated pre-filter queue.

First regenerate the selected queue with:
    python prefilter_scan.py --max-deep-symbols 2968 --skip-review
Then (defaults to one symbol / one batch per invocation):
    python batch_scan.py
    python batch_scan.py  # resume using same current-day report, CSV and code

Start a separate fresh session ONLY after explicitly regenerating its inputs:
    python batch_scan.py --new-run
"""
from __future__ import annotations

import argparse
from pathlib import Path

from config import OUTPUT_DIR, PROJECT_ROOT, SCREENER_CSV_PATH
from screening.batch_runner import ScanSafetyError, ist_now, load_inputs, run_session


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bounded pre-filtered NSE deep research; no orders")
    parser.add_argument("--queue", type=Path, default=OUTPUT_DIR / "prefilter_selected_symbols.txt")
    parser.add_argument("--report", type=Path, default=OUTPUT_DIR / "prefilter_report.json")
    parser.add_argument("--csv", type=Path, default=SCREENER_CSV_PATH)
    parser.add_argument("--batch-size", type=int, default=1, help="1..4 symbols per main.py launch")
    parser.add_argument("--max-batches", type=int, default=1, help="Bounded launches this invocation")
    parser.add_argument("--cooldown-seconds", type=float, default=30,
                        help="Minimum gap between batch start times (not per-API throttling)")
    parser.add_argument("--timeout-seconds", type=float, default=900)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--new-run", action="store_true",
                        help="Archive previous session folder and start new checkpoint pointer")
    args = parser.parse_args(argv)
    root = PROJECT_ROOT / "data" / "output" / "larger_scan"
    try:
        inputs = load_inputs(PROJECT_ROOT, args.queue.resolve(), args.report.resolve(),
                             args.csv.resolve(), now=ist_now())
        outcome = run_session(
            PROJECT_ROOT, root / "current_state.json", root, inputs,
            batch_size=args.batch_size, max_batches=args.max_batches,
            cooldown_seconds=args.cooldown_seconds, timeout_seconds=args.timeout_seconds,
            max_attempts=args.max_attempts, new_run=args.new_run,
        )
    except ScanSafetyError as exc:
        print("SCAN SAFETY STOP:", exc)
        return 2
    print(f"Session: {outcome['session_id']}")
    print(f"Progress: {outcome['completed']}/{outcome['total']} complete; {outcome['pending']} pending")
    print("Checkpoint:", outcome["state_path"])
    print("Merged research:", outcome["merged_path"])
    if outcome["error"]:
        print("SCAN STOP:", outcome["error"])
        return 2
    print("No orders were placed. Child main.py may fetch multiple Groww chunks per symbol.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())