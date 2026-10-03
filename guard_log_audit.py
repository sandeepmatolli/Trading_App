"""Offline CLI for the existing batch scanner's optional Groww SDK gate logs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from screening.guard_log_audit import GuardLogAuditError, audit_guard_logs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only local Groww gate log audit; no SDK or orders")
    parser.add_argument("--state", type=Path,
                        default=Path(__file__).resolve().parent / "data" / "output" / "larger_scan" / "current_state.json")
    args = parser.parse_args(argv)
    try:
        result = audit_guard_logs(args.state)
    except (GuardLogAuditError, OSError, ValueError) as exc:
        print("GUARD LOG AUDIT STOP:", exc)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())