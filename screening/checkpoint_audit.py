"""Read-only consistency audit for a settled batch-runner checkpoint.

Verifies checkpoint, merged report, and immutable child-result correspondence.
Does not refresh stale data, change any file, or authorize an investment decision.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Dict


class CheckpointAuditError(ValueError):
    """Existing local research evidence cannot be safely reconciled."""


_SESSION = re.compile(r"^\d{8}T\d{6}_[0-9a-f]{8}$")
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&._-]{0,29}$")
_DECISIONS = {"DATA_REJECT", "REJECT", "WATCH", "CANDIDATE"}
_MAX_BYTES = 32 * 1024 * 1024


def _stop(message: str) -> None:
    raise CheckpointAuditError(message)


def _plain_path(path: Path, label: str) -> None:
    """Reject redirects in expected archive paths; not an anti-TOCTOU guarantee."""
    try:
        if path.is_symlink() or path.resolve() != path:
            _stop(f"Redirected {label} is not accepted")
    except (OSError, RuntimeError) as exc:
        raise CheckpointAuditError(f"Cannot check {label}: {exc}") from exc


def _json(path: Path, label: str) -> Any:
    try:
        if not path.is_file():
            _stop(f"Missing {label}: {path}")
        if path.stat().st_size > _MAX_BYTES:
            _stop(f"{label} exceeds 32 MiB audit limit")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, CheckpointAuditError):
            raise
        raise CheckpointAuditError(f"Unreadable {label}: {exc}") from exc


def _pipeline_failure(row: Dict[str, Any]) -> bool:
    return row.get("decision") == "DATA_REJECT" and any(
        str(flag).startswith("Pipeline error:")
        for flag in row.get("risk_flags", []) or []
    )


def audit_checkpoint(state_path: Path) -> Dict[str, Any]:
    """Validate one settled session without mutating or re-running its research."""
    state_path = Path(state_path).resolve()

    if (state_path.parent / ".runner.lock").exists():
        _stop("Runner lock present; verify the scan has stopped before auditing")

    state = _json(state_path, "checkpoint")
    if (
        not isinstance(state, dict)
        or type(state.get("schema_version")) is not int
        or state["schema_version"] != 1
    ):
        _stop("Unsupported checkpoint schema")

    sid, day = state.get("session_id"), state.get("date_ist")

    if not isinstance(sid, str) or not _SESSION.fullmatch(sid):
        _stop("Malformed session ID")

    if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        _stop("Malformed checkpoint IST date")

    folder = state_path.parent / sid
    _plain_path(folder, "session directory")

    if (
        not isinstance(state.get("session_dir"), str)
        or Path(state["session_dir"]).resolve() != folder
    ):
        _stop("Session archive does not match the checkpoint sibling")

    symbols, completed, attempts, failures = (
        state.get("symbols"),
        state.get("completed"),
        state.get("attempts"),
        state.get("failures"),
    )
    batches, seq = state.get("batches"), state.get("attempt_sequence")

    if (
        not isinstance(symbols, list)
        or not symbols
        or any(
            not isinstance(s, str) or not _SYMBOL.fullmatch(s)
            for s in symbols
        )
        or len(set(symbols)) != len(symbols)
    ):
        _stop("Checkpoint queue contains invalid or repeated symbols")

    if not all(isinstance(x, dict) for x in (completed, attempts, failures)):
        _stop("Malformed completed, attempts or failures mapping")

    queue = set(symbols)

    if (
        not set(completed).issubset(queue)
        or not set(attempts).issubset(queue)
        or not set(failures).issubset(queue)
    ):
        _stop("Checkpoint refers to a symbol outside its queue")

    if not isinstance(batches, list) or type(seq) is not int or seq != len(batches):
        _stop("Checkpoint has an incomplete, unrecorded or malformed batch attempt")

    observed = Counter()
    archived_final = {}
    pipeline_failures = 0
    unsafe_children = 0

    for index, batch in enumerate(batches, start=1):
        if (
            not isinstance(batch, dict)
            or type(batch.get("number")) is not int
            or batch["number"] != index
        ):
            _stop("Nonconsecutive batch numbering")

        requested = batch.get("symbols")

        if (
            not isinstance(requested, list)
            or not requested
            or any(
                not isinstance(s, str) or s not in queue
                for s in requested
            )
            or len(requested) != len(set(requested))
        ):
            _stop(f"Invalid requested symbols in batch {index}")

        if type(batch.get("returncode")) is not int:
            _stop(f"Missing return code in batch {index}")

        observed.update(requested)

        child = folder / f"batch_{index:04d}"
        _plain_path(child, "batch directory")

        archive = child / "results.json"
        _plain_path(archive, "child results")

        if batch["returncode"] != 0:
            if archive.exists():
                _stop(
                    f"Nonzero-return batch {index} unexpectedly "
                    "has a results archive"
                )
            continue

        if not archive.exists():
            if str(batch.get("error") or "").startswith("Unsafe child results:"):
                unsafe_children += 1
                continue

            _stop(
                f"Successful child {index} lacks its "
                "immutable results archive"
            )

        rows = _json(archive, f"batch {index} results")

        if not isinstance(rows, list) or len(rows) != len(requested):
            _stop(f"Unexpected results length for batch {index}")

        actual = []

        for row in rows:
            if not isinstance(row, dict) or row.get("decision") not in _DECISIONS:
                _stop(f"Invalid decision in batch {index}")

            symbol = row.get("symbol")

            if (
                not isinstance(symbol, str)
                or symbol not in requested
                or symbol in actual
            ):
                _stop(f"Wrong or repeated result symbol in batch {index}")

            actual.append(symbol)

            if _pipeline_failure(row):
                pipeline_failures += 1
                continue

            if symbol in archived_final:
                _stop(f"Repeated completed research evidence for {symbol}")

            archived_final[symbol] = row

        if set(actual) != set(requested):
            _stop(f"Archived rows differ from requested batch {index}")

    if attempts != dict(observed):
        _stop("Attempt counters disagree with recorded batch membership")

    if set(archived_final) != set(completed):
        _stop("Completed mapping differs from archived completed rows")

    if any(
        completed[s] != row or _pipeline_failure(completed[s])
        for s, row in archived_final.items()
    ):
        _stop("Completed decision differs from archived child evidence")

    pending = [s for s in symbols if s not in completed]

    expected_failures = {
        s: failures[s]
        for s in pending
        if s in failures
    }

    merged_path = folder / "merged_report.json"
    _plain_path(merged_path, "merged report")

    merged = _json(merged_path, "merged report")

    if not isinstance(merged, dict):
        _stop("Merged report must be a JSON object")

    counts = dict(
        Counter(
            completed[s]["decision"]
            for s in symbols
            if s in completed
        )
    )

    checks = {
        "schema_version": 1,
        "session_id": sid,
        "date_ist": day,
        "queue_count": len(symbols),
        "completed_count": len(completed),
        "results": [
            completed[s]
            for s in symbols
            if s in completed
        ],
        "pending_symbols": pending,
        "unresolved_failures": expected_failures,
        "decision_counts": counts,
    }

    for key, expected in checks.items():
        if merged.get(key) != expected:
            _stop(f"Merged report disagrees with checkpoint: {key}")

    return {
        "schema_version": 1,
        "mode": "OFFLINE_SETTLED_CHECKPOINT_AUDIT_NO_PROVIDER_REQUESTS",
        "status": (
            "CONSISTENT_ALL_COMPLETED"
            if not pending
            else "CONSISTENT_WITH_PENDING"
        ),
        "session_id": sid,
        "date_ist": day,
        "queue_count": len(symbols),
        "completed_count": len(completed),
        "pending_count": len(pending),
        "recorded_child_attempts": len(batches),
        "archived_pipeline_error_rows": pipeline_failures,
        "unsafe_children_without_results_archive": unsafe_children,
        "decision_counts_at_scan": counts,
        "warning": (
            "Historical research consistency only; no live price/event "
            "verification, scan or trade. A pending symbol is not a "
            "completed decision."
        ),
    }


def main(argv=None) -> int:
    """Explicit offline CLI; importing this module never starts an audit."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Audit settled batch evidence without API calls or orders"
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=(
            Path(__file__).resolve().parents[1]
            / "data"
            / "output"
            / "larger_scan"
            / "current_state.json"
        ),
    )

    args = parser.parse_args(argv)

    try:
        result = audit_checkpoint(args.state)
    except (CheckpointAuditError, OSError, ValueError) as exc:
        print("CHECKPOINT AUDIT STOP:", exc)
        return 2

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())