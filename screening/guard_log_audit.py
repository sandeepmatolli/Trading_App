"""Read-only, conservative audit of existing Groww SDK gate log lines.

Only logged, *attempted starts* of the two guarded SDK methods are observable.
No authentication, live source access, provider quota inference, or file writes.
A missing telemetry line is NOT evidence that no provider calls occurred.
"""
from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
import re
from typing import Any, Dict, List


class GuardLogAuditError(ValueError):
    """The local checkpoint/log evidence cannot safely be summarized."""


_SESSION = re.compile(r"^\d{8}T\d{6}_[0-9a-f]{8}$")
_GATE = re.compile(
    r"^Groww SDK gate: method=(get_historical_candles|"
    r"get_instrument_by_exchange_and_trading_symbol) "
    r"attempt=([1-9]\d*) wait_seconds=(\d+\.\d{3}) "
    r"per_child_cap=(disabled|[1-9]\d*)$"
)
_MAX_LOG_BYTES = 32 * 1024 * 1024


def _fail(message: str) -> None:
    raise GuardLogAuditError(message)


def _read_object(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise GuardLogAuditError(f"Cannot read checkpoint JSON: {exc}") from exc
    if not isinstance(value, dict):
        _fail("Checkpoint must be a JSON object")
    return value


def _require_unredirected(path: Path, label: str) -> None:
    """Reject a redirected path before opening it (including Windows junctions).

    Parent paths must already be anchored under the resolved checkpoint parent.
    This protects against accidental or misleading archive/log link traversal;
    like any filesystem check, it is not a defense against a concurrent actor
    changing the filesystem between checking a path and reading the file.
    """
    try:
        if path.is_symlink() or path.resolve() != path:
            _fail(f"{label} is redirected; symbolic links/junctions are not accepted")
    except (OSError, RuntimeError) as exc:
        raise GuardLogAuditError(f"Cannot validate {label}: {exc}") from exc


def _audit_one(log_path: Path) -> Dict[str, Any]:
    try:
        if not log_path.is_file():
            _fail("Expected child main.log is missing")
        if log_path.stat().st_size > _MAX_LOG_BYTES:
            _fail("Child log exceeds the conservative 32 MiB offline audit limit")
        content = log_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GuardLogAuditError(f"Cannot read child log: {exc}") from exc

    by_method: Counter = Counter()
    cap: Any = None
    attempt = 0
    total_wait = 0.0

    for line_number, raw in enumerate(content.splitlines(), start=1):
        line = raw.strip()

        if not line.startswith("Groww SDK gate:"):
            continue

        match = _GATE.fullmatch(line)
        if match is None:
            _fail(f"Malformed gate telemetry at child log line {line_number}")

        method, number, wait_text, cap_text = match.groups()
        number = int(number)

        if number != attempt + 1:
            _fail(f"Gate attempts are not consecutive at child log line {line_number}")

        next_cap = None if cap_text == "disabled" else int(cap_text)

        if next_cap is not None and next_cap > 100000:
            _fail("Gate cap exceeds implemented SDK guard range")

        if attempt and next_cap != cap:
            _fail("Gate cap changed within one child log")

        if next_cap is not None and number > next_cap:
            _fail("A logged attempt exceeds its reported per-child ceiling")

        wait = float(wait_text)
        if not math.isfinite(wait) or wait > 60.001:
            _fail("Logged wait is outside the SDK guard's supported range")

        cap = next_cap
        total_wait += wait
        attempt = number
        by_method[method] += 1

    return {
        "observation": (
            "LOGGED_ATTEMPT_STARTS_ONLY"
            if attempt
            else "NO_TELEMETRY_OBSERVED_NOT_ZERO_CALLS"
        ),
        "logged_guarded_attempt_starts": attempt,
        "by_method": dict(sorted(by_method.items())),
        "reported_per_child_cap": cap if attempt else "UNOBSERVABLE",
        "logged_wait_seconds_sum": round(total_wait, 3),
    }


def audit_guard_logs(state_path: Path) -> Dict[str, Any]:
    """Read only expected checkpoint-attested child logs, not arbitrary paths.

    Raises on an active runner, an incomplete attempt record, redirected paths,
    missing logs, duplicate/non-consecutive attempts or malformed gate lines.
    Does not report successes, SDK-internal retries or provider request totals.
    """
    state_path = Path(state_path).resolve()
    state = _read_object(state_path)

    if type(state.get("schema_version")) is not int or state["schema_version"] != 1:
        _fail("Unsupported checkpoint schema")

    session_id = state.get("session_id")
    if not isinstance(session_id, str) or not _SESSION.fullmatch(session_id):
        _fail("Invalid session ID")

    # Do not resolve expected paths before checking them.
    # Resolving first could conceal a symbolic-link or junction redirect.
    expected_root = state_path.parent / session_id
    _require_unredirected(expected_root, "Session directory")

    raw_dir = state.get("session_dir")
    if not isinstance(raw_dir, str) or Path(raw_dir).resolve() != expected_root:
        _fail("Checkpoint session directory is not its expected sibling")

    if (state_path.parent / ".runner.lock").exists():
        _fail("Runner lock present: active/incomplete scan; do not audit a moving log")

    batches = state.get("batches")
    seq = state.get("attempt_sequence")

    if not isinstance(batches, list) or type(seq) is not int or seq != len(batches):
        _fail("Checkpoint has an unrecorded/in-flight attempt or malformed batch list")

    audited: List[Dict[str, Any]] = []
    combined: Counter = Counter()

    for expected_number, batch in enumerate(batches, start=1):
        if (
            not isinstance(batch, dict)
            or type(batch.get("number")) is not int
            or batch["number"] != expected_number
        ):
            _fail("Batch numbers must be consecutive and match checkpoint order")

        child_dir = expected_root / f"batch_{expected_number:04d}"
        _require_unredirected(child_dir, "Batch directory")

        log_path = child_dir / "main.log"
        _require_unredirected(log_path, "Child log path")

        recorded = batch.get("log")
        if not isinstance(recorded, str) or Path(recorded).resolve() != log_path:
            _fail("Recorded child log path differs from the expected batch path")

        ret = batch.get("returncode")
        if type(ret) is not int:
            _fail("Missing or malformed child return code")

        one = _audit_one(log_path)
        combined.update(one["by_method"])

        audited.append({
            "batch_number": expected_number,
            "child_returncode": ret,
            **one,
        })

    return {
        "schema_version": 1,
        "mode": "OFFLINE_GROWW_GUARD_LOG_AUDIT_NO_PROVIDER_REQUESTS",
        "session_id": session_id,
        "batches_audited": len(audited),
        "batches_without_observable_telemetry": sum(
            one["logged_guarded_attempt_starts"] == 0 for one in audited
        ),
        "sum_logged_guarded_attempt_starts_across_children": sum(combined.values()),
        "sum_logged_by_method": dict(sorted(combined.items())),
        "children": audited,
        "limitations": [
            "Counts printed guarded SDK attempt starts only; not success, actual provider hits or account quota usage.",
            "Missing telemetry is unobservable, NOT evidence of zero calls (telemetry may be disabled).",
            "Access-token acquisition, SDK-internal retries, other SDK methods and other processes are excluded.",
            "The checkpoint/logs are local operator-maintained evidence, not a cryptographic provider audit.",
            "No authentication, network access, scan, checkpoint mutation or trade is performed.",
        ],
    }