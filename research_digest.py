"""Read-only same-IST-day archive digest after `campaign_scan.py record`.

Does not fetch market data, call official feeds, rank stocks or place trades.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from config import OUTPUT_DIR
from screening.batch_runner import ScanSafetyError
from screening.research_digest import build_same_day_digest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit current-day campaign research; not a live signal")
    parser.add_argument("--state", type=Path, default=OUTPUT_DIR / "larger_scan" / "current_state.json")
    parser.add_argument("--ledger", type=Path, default=OUTPUT_DIR / "campaign" / "campaign.json")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        result = build_same_day_digest(
            args.state, args.ledger, OUTPUT_DIR / "campaign", output_path=args.output,
        )
    except ScanSafetyError as exc:
        print("RESEARCH DIGEST SAFETY STOP:", exc)
        return 2
    value = result["digest"]
    print("Digest:", result["path"])
    print("Session:", value["session_id"], "IST day:", value["date_ist"])
    print("Completed:", value["completed_count"], "Pending:", value["pending_count"])
    print("Archived decisions:", value["decision_counts_at_scan"])
    print("Candidate research requiring fresh manual revalidation:", value["candidate_review_count"])
    print("Research only. No live prices/events verified and no orders placed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())