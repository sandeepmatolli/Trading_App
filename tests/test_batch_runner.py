"""Offline regression tests: no Groww, NSE, orders or child main.py execution."""
from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from screening.batch_runner import (
    CODE_FILES, ScanSafetyError, create_or_resume_state, load_inputs,
    run_session, validate_child_results,
)


NOW = datetime(2026, 10, 3, 14, 30, tzinfo=ZoneInfo("Asia/Kolkata"))


def setup_inputs(tmp_path: Path, symbols=None, *, event_ok=True):
    symbols = symbols or ["AAATECH", "RELIANCE", "TCS"]
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in CODE_FILES:
        file = repo / name
        file.parent.mkdir(exist_ok=True, parents=True)
        file.write_text(f"# {name}\n", encoding="utf-8")
    csv = repo / "stocks_screen.csv"
    csv.write_text("NSE Code\n" + "\n".join(symbols), encoding="utf-8")
    output = repo / "data" / "output"
    output.mkdir(parents=True)
    queue = output / "prefilter_selected_symbols.txt"
    queue.write_text("\n".join(symbols) + "\n", encoding="utf-8")
    report = output / "prefilter_report.json"
    report.write_text(json.dumps({
        "stage": "fundamental_event_prefilter",
        "source_event_candidate_eligible": event_ok,
        "event_cache": {"stale_fallback": False},
        "selected_symbols": symbols,
        "rows": [{"symbol": s, "status": "ELIGIBLE_FOR_DEEP_SCAN"} for s in symbols],
    }), encoding="utf-8")
    for path in (queue, report):
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))
    return repo, output, csv, queue, report


def inputs(repo, csv, queue, report):
    return load_inputs(repo, queue, report, csv, now=NOW)


def fake_main(repo, *, fail_symbol=None, returncode=0, malformed=False):
    launched = []
    def execute(cmd, log_path, timeout):
        symbols = cmd[-1].split(",")
        launched.append(symbols)
        log_path.write_text("mock offline run\n", encoding="utf-8")
        if returncode:
            return returncode
        path = repo / "data" / "output" / "shortlist.json"
        if malformed:
            path.write_text('not json', encoding="utf-8")
        else:
            path.write_text(json.dumps([
                {"symbol": s, "decision": "DATA_REJECT",
                 "risk_flags": ["Pipeline error: transient"]} if s == fail_symbol else
                {"symbol": s, "decision": "WATCH", "risk_flags": []}
                for s in symbols
            ]), encoding="utf-8")
        return 0
    return execute, launched


def test_input_rejects_unavailable_required_event_source(tmp_path):
    repo, out, csv, queue, report = setup_inputs(tmp_path, event_ok=False)
    with pytest.raises(ScanSafetyError, match="candidate-grade"):
        inputs(repo, csv, queue, report)


def test_input_rejects_queue_tampering_and_hard_reject(tmp_path):
    repo, out, csv, queue, report = setup_inputs(tmp_path)
    queue.write_text("RELIANCE\n", encoding="utf-8")
    os.utime(queue, (NOW.timestamp(), NOW.timestamp()))
    with pytest.raises(ScanSafetyError, match="Queue differs"):
        inputs(repo, csv, queue, report)
    queue.write_text("AAATECH\nRELIANCE\nTCS\n", encoding="utf-8")
    os.utime(queue, (NOW.timestamp(), NOW.timestamp()))
    obj = json.loads(report.read_text(encoding="utf-8"))
    obj["rows"][0]["status"] = "DEFER_FINANCIAL"
    report.write_text(json.dumps(obj), encoding="utf-8")
    os.utime(report, (NOW.timestamp(), NOW.timestamp()))
    with pytest.raises(ScanSafetyError, match="rejected/deferred"):
        inputs(repo, csv, queue, report)


def test_input_requires_todays_prefilter_queue(tmp_path):
    repo, out, csv, queue, report = setup_inputs(tmp_path)
    os.utime(queue, (NOW.timestamp() - 86400, NOW.timestamp() - 86400))
    with pytest.raises(ScanSafetyError, match="not generated today"):
        inputs(repo, csv, queue, report)


def test_one_batch_and_resume_preserves_completed_and_csv_order(tmp_path):
    repo, out, csv, queue, report = setup_inputs(tmp_path)
    inp = inputs(repo, csv, queue, report)
    scan_root = out / "larger_scan"
    state_path = scan_root / "current_state.json"
    execute, calls = fake_main(repo)
    first = run_session(repo, state_path, scan_root, inp, now_fn=lambda: NOW,
                        execute=execute, sleep=lambda n: None, cooldown_seconds=0)
    assert first["ok"] is True and first["completed"] == 1 and calls == [["AAATECH"]]
    second = run_session(repo, state_path, scan_root, inp, now_fn=lambda: NOW,
                         execute=execute, sleep=lambda n: None, cooldown_seconds=0,
                         max_batches=2)
    assert second["completed"] == 3 and second["pending"] == 0
    assert calls == [["AAATECH"], ["RELIANCE"], ["TCS"]]
    report_obj = json.loads(Path(second["merged_path"]).read_text(encoding="utf-8"))
    assert [item["symbol"] for item in report_obj["results"]] == inp["symbols"]
    assert [item["decision"] for item in report_obj["results"]] == ["WATCH"] * 3
    assert len(json.loads(state_path.read_text(encoding="utf-8"))["batches"]) == 3


def test_failed_child_does_not_mark_symbol_complete(tmp_path):
    repo, out, csv, queue, report = setup_inputs(tmp_path)
    inp = inputs(repo, csv, queue, report)
    root = out / "larger_scan"
    execute, calls = fake_main(repo, returncode=1)
    result = run_session(repo, root / "current_state.json", root, inp,
                         now_fn=lambda: NOW, execute=execute, cooldown_seconds=0)
    assert result["ok"] is False and result["completed"] == 0
    assert result["pending"] == 3 and calls == [["AAATECH"]]


def test_main_pipeline_exception_is_retryable_but_quality_data_reject_is_final(tmp_path):
    repo, out, csv, queue, report = setup_inputs(tmp_path)
    inp = inputs(repo, csv, queue, report)
    root = out / "larger_scan"
    execute, calls = fake_main(repo, fail_symbol="AAATECH")
    result = run_session(repo, root / "current_state.json", root, inp,
                         now_fn=lambda: NOW, execute=execute, cooldown_seconds=0)
    assert result["ok"] is False and result["completed"] == 0
    clean, _ = fake_main(repo)
    result = run_session(repo, root / "current_state.json", root, inp,
                         now_fn=lambda: NOW, execute=clean, cooldown_seconds=0)
    assert result["ok"] is True and result["completed"] == 1
    assert calls == [["AAATECH"]]


def test_malformed_output_remains_pending(tmp_path):
    repo, out, csv, queue, report = setup_inputs(tmp_path)
    inp = inputs(repo, csv, queue, report)
    root = out / "larger_scan"
    execute, calls = fake_main(repo, malformed=True)
    result = run_session(repo, root / "current_state.json", root, inp,
                         now_fn=lambda: NOW, execute=execute, cooldown_seconds=0)
    assert not result["ok"] and result["completed"] == 0
    assert "Unsafe child results" in result["error"]


def test_exhausted_retries_halt_instead_of_looping(tmp_path):
    repo, out, csv, queue, report = setup_inputs(tmp_path)
    inp = inputs(repo, csv, queue, report)
    root = out / "larger_scan"
    execute, calls = fake_main(repo, returncode=1)
    for _ in range(2):
        result = run_session(repo, root / "current_state.json", root, inp,
                             now_fn=lambda: NOW, execute=execute, cooldown_seconds=0,
                             max_attempts=2)
        assert not result["ok"]
    result = run_session(repo, root / "current_state.json", root, inp,
                         now_fn=lambda: NOW, execute=execute, cooldown_seconds=0,
                         max_attempts=2)
    assert "Max attempts reached" in result["error"] and len(calls) == 2


def test_resume_fails_closed_when_csv_or_code_changed(tmp_path):
    repo, out, csv, queue, report = setup_inputs(tmp_path)
    inp = inputs(repo, csv, queue, report)
    root = out / "larger_scan"
    create_or_resume_state(root / "current_state.json", root, inp, now=NOW)
    csv.write_text("different\n", encoding="utf-8")
    modified = inputs(repo, csv, queue, report)
    with pytest.raises(ScanSafetyError, match="Checkpoint input changed"):
        create_or_resume_state(root / "current_state.json", root, modified, now=NOW)
    (repo / "main.py").write_text("updated logic\n", encoding="utf-8")
    modified2 = inputs(repo, csv, queue, report)
    with pytest.raises(ScanSafetyError, match="Checkpoint input changed"):
        create_or_resume_state(root / "current_state.json", root, modified2, now=NOW)


def test_same_day_enforced_at_resume(tmp_path):
    repo, out, csv, queue, report = setup_inputs(tmp_path)
    inp = inputs(repo, csv, queue, report)
    root = out / "larger_scan"
    runner, calls = fake_main(repo)
    result = run_session(repo, root / "current_state.json", root, inp,
                         now_fn=lambda: NOW, execute=runner, cooldown_seconds=0)
    next_day = datetime(2026, 10, 4, 10, tzinfo=ZoneInfo("Asia/Kolkata"))
    result2 = run_session(repo, root / "current_state.json", root, inp,
                          now_fn=lambda: next_day, execute=runner, cooldown_seconds=0)
    assert result2["ok"] is False and "date rolled over" in result2["error"]
    assert len(calls) == 1


def test_child_result_requires_exact_symbols(tmp_path):
    file = tmp_path / "result.json"
    file.write_text(json.dumps([{"symbol": "TCS", "decision": "WATCH"}]), encoding="utf-8")
    with pytest.raises(ScanSafetyError):
        validate_child_results(file, ["RELIANCE"])
    with pytest.raises(ScanSafetyError):
        validate_child_results(file, ["RELIANCE", "TCS"])


def test_partial_multisymbol_batch_checkpoints_only_good_results(tmp_path):
    repo, out, csv, queue, report = setup_inputs(tmp_path)
    inp = inputs(repo, csv, queue, report)
    root = out / "larger_scan"
    execute, calls = fake_main(repo, fail_symbol="RELIANCE")
    result = run_session(repo, root / "current_state.json", root, inp,
                         now_fn=lambda: NOW, execute=execute, cooldown_seconds=0,
                         batch_size=2)
    assert result["ok"] is False
    assert result["completed"] == 1 and result["pending"] == 2
    assert calls == [["AAATECH", "RELIANCE"]]
    state = json.loads((root / "current_state.json").read_text(encoding="utf-8"))
    assert list(state["completed"]) == ["AAATECH"]
    assert state["attempts"]["RELIANCE"] == 1


def test_outer_batch_cooldown_is_applied_between_starts(tmp_path):
    from datetime import timedelta
    repo, out, csv, queue, report = setup_inputs(tmp_path)
    inp = inputs(repo, csv, queue, report)
    root = out / "larger_scan"
    execute, calls = fake_main(repo)
    clock = [NOW]
    delays = []
    def sleep(seconds):
        delays.append(seconds)
        clock[0] += timedelta(seconds=seconds)
    result = run_session(repo, root / "current_state.json", root, inp,
                         now_fn=lambda: clock[0], execute=execute, sleep=sleep,
                         cooldown_seconds=30, max_batches=2)
    assert result["completed"] == 2
    assert delays == [30]