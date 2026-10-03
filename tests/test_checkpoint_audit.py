"""Pure offline checkpoint evidence tests; never imports/runs the live scanner."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from screening.checkpoint_audit import main
from screening.checkpoint_audit import CheckpointAuditError, audit_checkpoint


SID = "20261003T120000_abcdef12"
DAY = "2026-10-03"

WATCH = {
    "symbol": "AAA",
    "decision": "WATCH",
    "risk_flags": [],
}

ERROR = {
    "symbol": "BBB",
    "decision": "DATA_REJECT",
    "risk_flags": ["Pipeline error: offline failure"],
}


def write(path, value):
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def fixture(tmp_path, *, error=True):
    root = tmp_path / "larger_scan"
    root.mkdir()

    session = root / SID
    child = session / "batch_0001"
    child.mkdir(parents=True)

    rows = (
        [WATCH, ERROR]
        if error
        else [
            WATCH,
            {"symbol": "BBB", "decision": "REJECT", "risk_flags": []},
        ]
    )

    write(child / "results.json", rows)
    (child / "main.log").write_text("test log\n", encoding="utf-8")

    completed = {
        r["symbol"]: r
        for r in rows
        if not (r["symbol"] == "BBB" and error)
    }

    failures = (
        {"BBB": "Main caught a pipeline exception; see batch log."}
        if error
        else {}
    )

    state = {
        "schema_version": 1,
        "session_id": SID,
        "date_ist": DAY,
        "session_dir": str(session),
        "symbols": ["AAA", "BBB"],
        "attempts": {"AAA": 1, "BBB": 1},
        "completed": completed,
        "failures": failures,
        "attempt_sequence": 1,
        "batches": [
            {
                "number": 1,
                "symbols": ["AAA", "BBB"],
                "returncode": 0,
                "log": str(child / "main.log"),
                "error": "Pipeline error for BBB" if error else None,
            }
        ],
    }

    merged = {
        "schema_version": 1,
        "session_id": SID,
        "date_ist": DAY,
        "queue_count": 2,
        "completed_count": len(completed),
        "results": list(completed.values()),
        "pending_symbols": ["BBB"] if error else [],
        "unresolved_failures": failures,
        "decision_counts": (
            {"WATCH": 1}
            if error
            else {"WATCH": 1, "REJECT": 1}
        ),
    }

    state_path = root / "current_state.json"

    write(state_path, state)
    write(session / "merged_report.json", merged)

    return state_path, session


def test_pending_pipeline_exception_is_not_completed(tmp_path):
    path, _ = fixture(tmp_path)

    result = audit_checkpoint(path)

    assert (
        result["status"],
        result["completed_count"],
        result["pending_count"],
    ) == ("CONSISTENT_WITH_PENDING", 1, 1)

    assert result["archived_pipeline_error_rows"] == 1


def test_all_completed_reconciles_archive_and_merged(tmp_path):
    path, _ = fixture(tmp_path, error=False)

    result = audit_checkpoint(path)

    assert result["status"] == "CONSISTENT_ALL_COMPLETED"
    assert result["decision_counts_at_scan"] == {
        "WATCH": 1,
        "REJECT": 1,
    }


def test_unarchived_completed_row_rejected(tmp_path):
    path, session = fixture(tmp_path)
    (session / "batch_0001" / "results.json").unlink()

    with pytest.raises(CheckpointAuditError, match="lacks its immutable"):
        audit_checkpoint(path)


def test_tampered_completed_row_rejected(tmp_path):
    path, _ = fixture(tmp_path)

    state = json.loads(path.read_text())
    state["completed"]["AAA"]["decision"] = "CANDIDATE"
    write(path, state)

    with pytest.raises(
        CheckpointAuditError,
        match="archived child evidence",
    ):
        audit_checkpoint(path)


def test_tampered_merged_report_rejected(tmp_path):
    path, session = fixture(tmp_path)

    merged = session / "merged_report.json"
    value = json.loads(merged.read_text())
    value["pending_symbols"] = []

    write(merged, value)

    with pytest.raises(CheckpointAuditError, match="pending_symbols"):
        audit_checkpoint(path)


def test_partial_unrecorded_attempt_rejected(tmp_path):
    path, _ = fixture(tmp_path)

    state = json.loads(path.read_text())
    state["attempt_sequence"] = 2
    write(path, state)

    with pytest.raises(CheckpointAuditError, match="incomplete"):
        audit_checkpoint(path)


def test_attempt_counter_drift_rejected(tmp_path):
    path, _ = fixture(tmp_path)

    state = json.loads(path.read_text())
    state["attempts"]["BBB"] = 9
    write(path, state)

    with pytest.raises(CheckpointAuditError, match="Attempt counters"):
        audit_checkpoint(path)


def test_active_lock_rejected(tmp_path):
    path, _ = fixture(tmp_path)
    (path.parent / ".runner.lock").write_text(
        "active",
        encoding="utf-8",
    )

    with pytest.raises(CheckpointAuditError, match="Runner lock"):
        audit_checkpoint(path)


@pytest.mark.parametrize(
    "returncode,error_prefix",
    [
        (125, "main.py exited 125"),
        (0, "Unsafe child results: missing"),
    ],
)
def test_failed_child_may_legitimately_lack_results_archive(
    tmp_path,
    returncode,
    error_prefix,
):
    path, session = fixture(tmp_path)
    (session / "batch_0001" / "results.json").unlink()

    state = json.loads(path.read_text())
    state["completed"] = {}
    state["failures"] = {
        "AAA": "failed",
        "BBB": "failed",
    }
    state["batches"][0]["returncode"] = returncode
    state["batches"][0]["error"] = error_prefix

    write(path, state)

    merged = session / "merged_report.json"

    write(
        merged,
        {
            "schema_version": 1,
            "session_id": SID,
            "date_ist": DAY,
            "queue_count": 2,
            "completed_count": 0,
            "results": [],
            "pending_symbols": ["AAA", "BBB"],
            "unresolved_failures": state["failures"],
            "decision_counts": {},
        },
    )

    result = audit_checkpoint(path)

    assert result["pending_count"] == 2
    assert result["unsafe_children_without_results_archive"] == (
        1 if returncode == 0 else 0
    )


def test_invalid_child_archive_row_rejected(tmp_path):
    path, session = fixture(tmp_path)

    write(
        session / "batch_0001" / "results.json",
        [WATCH, WATCH],
    )

    with pytest.raises(
        CheckpointAuditError,
        match="repeated result symbol",
    ):
        audit_checkpoint(path)


def test_session_dir_redirected_rejected(tmp_path):
    path, _ = fixture(tmp_path)

    value = json.loads(path.read_text())
    value["session_dir"] = str(tmp_path / "other")

    write(path, value)

    with pytest.raises(CheckpointAuditError, match="sibling"):
        audit_checkpoint(path)


def test_cli_readonly(tmp_path, capsys):
    path, session = fixture(tmp_path)

    files = [
        path,
        session / "merged_report.json",
        session / "batch_0001" / "results.json",
    ]

    before = [file.read_bytes() for file in files]

    assert main(["--state", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["pending_count"] == 1

    after = [file.read_bytes() for file in files]

    assert before == after


def test_missing_checkpoint_cli_stops(tmp_path, capsys):
    assert main(["--state", str(tmp_path / "none.json")]) == 2

    assert "CHECKPOINT AUDIT STOP" in capsys.readouterr().out