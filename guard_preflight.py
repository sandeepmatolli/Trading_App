"""CLI: offline Groww guard-versus-fetch-budget preview; never starts research."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from screening.batch_runner import ScanSafetyError
from screening.guard_preflight import build_guard_preflight
from screening.request_budget import queue_size


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline per-child Groww cap preflight; no SDK calls or orders")
    count = parser.add_mutually_exclusive_group(required=True)
    count.add_argument("--symbol-count", type=int)
    count.add_argument("--queue", type=Path)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--max-batches", type=int, default=1)
    parser.add_argument("--max-calls", type=int, default=None,
                        help="Hypothetical cap; omit to read local guard settings (no authentication)")
    args = parser.parse_args(argv)
    try:
        n = queue_size(args.queue) if args.queue is not None else args.symbol_count
        output = build_guard_preflight(
            n, batch_size=args.batch_size, max_batches=args.max_batches,
            max_calls_override=args.max_calls,
        )
    except (ValueError, ScanSafetyError, OSError) as exc:
        print("GUARD PREFLIGHT STOP:", exc)
        return 2
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
