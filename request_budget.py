
"""Offline dry run for the existing scanner's baseline Groww request footprint."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from screening.batch_runner import ScanSafetyError
from screening.request_budget import estimate_request_budget, queue_size


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Read-only request-window estimate; no provider calls or orders")
    count = p.add_mutually_exclusive_group(required=True)
    count.add_argument("--symbol-count", type=int, help="Count only, for a hypothetical budget")
    count.add_argument("--queue", type=Path, help="Count symbols in a queue, without treating it as approved")
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--max-batches", type=int, default=1)
    a = p.parse_args(argv)
    try:
        size = queue_size(a.queue) if a.queue is not None else a.symbol_count
        result = estimate_request_budget(size, batch_size=a.batch_size, max_batches=a.max_batches)
    except (ScanSafetyError, ValueError, OSError) as exc:
        print("REQUEST BUDGET STOP:", exc)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
