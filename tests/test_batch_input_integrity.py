"""Offline coverage for code/snapshot drift during restartable research."""
from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from screening.batch_runner import (
    CODE_FILES,
    ScanSafetyError,
    create_or_resume_state,
    load_inputs,
    run_session,
    verify_input_integrity,
)

NOW = datetime(2026, 10, 3, 14, 30, tzinfo=ZoneInfo("Asia/Kolkata"))


def _fixtures(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    for name in CODE_FILES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# " + name + "\n", encoding="utf-8")
    csv = root / "stocks_screen.csv"
    csv.write_text("NSE Code\nAAA\nBBB\n", encoding="utf-8")
    out = root / "data" / "output"
    out.mkdir(parents=True)
    queue = out / "prefilter_selected_symbols.txt"
    queue.write_text("AAA\nBBB\n", encoding="utf-8")
    report = out / "prefilter_report.json"
    report.write_text(json.dumps({
        "stage": "fundamental_event_prefilter",
        "source_event_candidate_eligible": True,
        "event_cache": {"stale_fallback": False},
        "selected_symbols": ["AAA", "BBB"],
        "rows": [
            {"symbol": "AAA", "status": "ELIGIBLE_FOR_DEEP_SCAN"},
            {"symbol": "BBB", "status": "ELIGIBLE_FOR_DEEP_SCAN"},
        ],
    }), encoding="utf-8")
    for path in (queue, report):
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))
    inp = load_inputs(root, queue, report, csv, now=NOW)
    return root, out, queue, report, inp


def test_fingerprint_includes_transitive_first_party_engine_files():
    critical = {
        "fundamentals/csv_validator.py", "fundamentals/master_builder.py",
        "market_data/corporate_action_adjustment.py", "market_data/groww_auth.py",
        "news/cache_db.py", "news/corporate_action_cache.py",
        "news/corporate_action_reconciliation.py", "news/event_cache.py",
        "news/nse_historical_corporate_actions.py", "news/retry_utils.py",
        "requirements.txt", "prefilter_scan.py",
    }
    assert critical <= set(CODE_FILES)
    assert len(CODE_FILES) == len(set(CODE_FILES))


def test_resume_rejects_change_to_previously_untracked_corporate_action_helper(tmp_path):
    root, out, queue, report, inp = _fixtures(tmp_path)
    checkpoint = out / "larger_scan" / "current_state.json"
    create_or_resume_state(checkpoint, out / "larger_scan", inp, now=NOW)
    (root / "market_data" / "corporate_action_adjustment.py").write_text(
        "# changed adjustment algorithm\n", encoding="utf-8"
    )
    changed = load_inputs(root, queue, report, root / "stocks_screen.csv", now=NOW)
    with pytest.raises(ScanSafetyError, match="Checkpoint input changed"):
        create_or_resume_state(checkpoint, out / "larger_scan", changed, now=NOW)


def test_active_session_stops_before_second_child_when_source_changes(tmp_path):
    root, out, queue, report, inp = _fixtures(tmp_path)
    launches = []
    def child(command, log_path, timeout):
        symbol = command[-1]
        launches.append(symbol)
        (out / "shortlist.json").write_text(
            json.dumps([{"symbol": symbol, "decision": "WATCH", "risk_flags": []}]),
            encoding="utf-8",
        )
        log_path.write_text("mocked offline run\n", encoding="utf-8")
        if len(launches) == 1:
            (root / "news" / "event_cache.py").write_text("# modified during scan\n", encoding="utf-8")
        return 0
    result = run_session(
        root, out / "larger_scan" / "current_state.json", out / "larger_scan", inp,
        now_fn=lambda: NOW, execute=child, cooldown_seconds=0, max_batches=2,
    )
    assert not result["ok"] and result["completed"] == 1 and result["pending"] == 1
    assert launches == ["AAA"]
    assert "Code changed during active scan: news/event_cache.py" in result["error"]


def test_active_session_detects_queue_drift(tmp_path):
    root, out, queue, report, inp = _fixtures(tmp_path)
    queue.write_text("BBB\nAAA\n", encoding="utf-8")
    with pytest.raises(ScanSafetyError, match="Snapshot changed during active scan: queue"):
        verify_input_integrity(root, inp)