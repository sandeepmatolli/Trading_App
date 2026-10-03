"""Isolated offline log-audit tests; no credentials, child processes, SDK or network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from guard_log_audit import main
from screening.guard_log_audit import GuardLogAuditError, audit_guard_logs

SID = "20261003T120000_abcdef12"


def fixture(tmp_path: Path, logs=("",), *, returns=None):
    root = tmp_path / "larger_scan"
    root.mkdir()

    session = root / SID
    session.mkdir()

    returns = returns if returns is not None else [0] * len(logs)
    batches = []

    for n, (content, ret) in enumerate(zip(logs, returns), start=1):
        child = session / f"batch_{n:04d}"
        child.mkdir()

        log = child / "main.log"
        log.write_text(content, encoding="utf-8")

        batches.append({
            "number": n,
            "log": str(log),
            "returncode": ret,
        })

    state = root / "current_state.json"
    state.write_text(
        json.dumps({
            "schema_version": 1,
            "session_id": SID,
            "session_dir": str(session),
            "attempt_sequence": len(batches),
            "batches": batches,
        }),
        encoding="utf-8",
    )

    return state, session


def line(
    method="get_historical_candles",
    attempt=1,
    wait="0.000",
    cap="disabled",
):
    return (
        f"Groww SDK gate: method={method} attempt={attempt} "
        f"wait_seconds={wait} per_child_cap={cap}\n"
    )


def test_good_two_children_counted_separately(tmp_path):
    a = (
        line("get_instrument_by_exchange_and_trading_symbol", 1, cap="17")
        + line(attempt=2, wait="1.000", cap="17")
    )
    b = line(attempt=1, cap="disabled")

    state, _ = fixture(tmp_path, ("irrelevant output\n" + a, b))
    result = audit_guard_logs(state)

    assert result["batches_audited"] == 2
    assert result["sum_logged_guarded_attempt_starts_across_children"] == 3
    assert [
        x["logged_guarded_attempt_starts"] for x in result["children"]
    ] == [2, 1]
    assert result["children"][0]["reported_per_child_cap"] == 17
    assert result["children"][1]["reported_per_child_cap"] is None


def test_missing_telemetry_is_unobservable_not_zero_calls(tmp_path):
    state, _ = fixture(
        tmp_path,
        ("Groww authentication configured using access token.\n",),
    )

    result = audit_guard_logs(state)

    assert result["batches_without_observable_telemetry"] == 1
    assert (
        result["children"][0]["observation"]
        == "NO_TELEMETRY_OBSERVED_NOT_ZERO_CALLS"
    )
    assert "NOT evidence of zero" in result["limitations"][1]


@pytest.mark.parametrize("content", [
    line(attempt=2),
    line() + line(attempt=1),
    line(cap="2") + line(attempt=2, cap="3"),
    line(cap="1") + line(attempt=2, cap="1"),
    "Groww SDK gate: method=get_historical_candles attempt=1\n",
    "Groww SDK gate: method=other_method attempt=1 wait_seconds=0.000 per_child_cap=disabled\n",
    line(wait="99.000"),
    line(cap="100001"),
])
def test_malformed_or_inconsistent_telemetry_stops(tmp_path, content):
    state, _ = fixture(tmp_path, (content,))

    with pytest.raises(GuardLogAuditError):
        audit_guard_logs(state)


def test_active_runner_lock_stops(tmp_path):
    state, _ = fixture(tmp_path, (line(),))
    (state.parent / ".runner.lock").write_text(
        "pid=123\n",
        encoding="utf-8",
    )

    with pytest.raises(GuardLogAuditError, match="Runner lock"):
        audit_guard_logs(state)


def test_missing_log_stops(tmp_path):
    state, session = fixture(tmp_path, (line(),))
    (session / "batch_0001" / "main.log").unlink()

    with pytest.raises(GuardLogAuditError, match="missing"):
        audit_guard_logs(state)


def test_recorded_external_log_path_is_rejected(tmp_path):
    state, _ = fixture(tmp_path, (line(),))

    value = json.loads(state.read_text(encoding="utf-8"))
    value["batches"][0]["log"] = str(tmp_path / "outside.log")
    state.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(GuardLogAuditError, match="differs"):
        audit_guard_logs(state)


def test_unrecorded_attempt_is_rejected(tmp_path):
    state, _ = fixture(tmp_path, (line(),))

    value = json.loads(state.read_text(encoding="utf-8"))
    value["attempt_sequence"] = 2
    state.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(GuardLogAuditError, match="unrecorded"):
        audit_guard_logs(state)


def test_session_dir_must_match_checkpoint_sibling(tmp_path):
    state, _ = fixture(tmp_path, (line(),))

    value = json.loads(state.read_text(encoding="utf-8"))
    value["session_dir"] = str(tmp_path / "untrusted")
    state.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(GuardLogAuditError, match="sibling"):
        audit_guard_logs(state)


def test_empty_valid_checkpoint_has_no_batches(tmp_path):
    state, _ = fixture(tmp_path, ())

    result = audit_guard_logs(state)

    assert result["batches_audited"] == 0
    assert result["children"] == []


def test_cli_is_json_and_read_only(tmp_path, capsys):
    state, _ = fixture(tmp_path, (line(),), returns=[1])
    before = state.read_bytes()

    assert main(["--state", str(state)]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["children"][0]["child_returncode"] == 1
    assert state.read_bytes() == before


def test_cli_stops_on_missing_checkpoint(tmp_path, capsys):
    assert main(["--state", str(tmp_path / "missing.json")]) == 2
    assert "GUARD LOG AUDIT STOP" in capsys.readouterr().out


@pytest.mark.parametrize("part", ("session", "batch", "log"))
def test_redirected_archive_or_log_path_is_rejected(
    tmp_path,
    monkeypatch,
    part,
):
    """Portable simulated redirect; works on Windows without symlink rights."""
    state, session = fixture(tmp_path, (line(),))

    target = {
        "session": session,
        "batch": session / "batch_0001",
        "log": session / "batch_0001" / "main.log",
    }[part]

    other = tmp_path / "unrelated"
    original = Path.resolve

    def redirected_resolve(self, *args, **kwargs):
        if self == target:
            return other
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected_resolve)

    with pytest.raises(GuardLogAuditError, match="redirected"):
        audit_guard_logs(state)