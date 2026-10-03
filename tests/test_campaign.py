"""Offline campaign tests; no network, Groww, or orders."""
from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from screening.batch_runner import CODE_FILES, ScanSafetyError, load_inputs, run_session
from screening.campaign import (
    historical_summary, load_ledger, prepare_day, record_session,
)

NOW = datetime.now(ZoneInfo("Asia/Kolkata"))


def fixture(tmp_path, *, symbols=None, skip_review=True, event_ok=True):
    symbols = symbols or ["AAA", "BBB", "CCC"]
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in CODE_FILES:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# " + name + "\n", encoding="utf-8")
    csv = repo / "stocks_screen.csv"
    csv.write_text("NSE Code\n" + "\n".join(symbols), encoding="utf-8")
    out = repo / "data" / "output"
    out.mkdir(parents=True)
    report, queue = out / "prefilter_report.json", out / "prefilter_selected_symbols.txt"
    report.write_text(json.dumps({"stage": "fundamental_event_prefilter",
                                  "source_event_candidate_eligible": event_ok,
                                  "skip_review": skip_review,
                                  "event_cache": {"stale_fallback": False},
                                  "selected_symbols": symbols,
                                  "rows": [{"symbol": s, "status": "ELIGIBLE_FOR_DEEP_SCAN"} for s in symbols]}), encoding="utf-8")
    queue.write_text("\n".join(symbols) + "\n", encoding="utf-8")
    for path in (report, queue):
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))
    ledger = out / "campaign" / "campaign.json"
    return repo, out, csv, report, queue, ledger


def run_one(repo, out, csv, report, queue):
    args = load_inputs(repo, queue, report, csv, now=NOW)
    def child(cmd, log, timeout):
        s = cmd[-1]
        (out / "shortlist.json").write_text(json.dumps([{"symbol": s, "decision": "WATCH", "risk_flags": []}]), encoding="utf-8")
        log.write_text("mock\n", encoding="utf-8")
        return 0
    result = run_session(repo, out / "larger_scan" / "current_state.json", out / "larger_scan", args,
                         now_fn=lambda: NOW, execute=child, cooldown_seconds=0)
    assert result["completed"] == 1
    return out / "larger_scan" / "current_state.json"


def test_capture_is_audited_idempotent_and_historical(tmp_path):
    repo, out, csv, report, queue, ledger = fixture(tmp_path)
    state = run_one(repo, out, csv, report, queue)
    a = record_session(ledger, state)
    b = record_session(ledger, state)
    assert a["newly_recorded"] == 1 and b["newly_recorded"] == 0
    entry = load_ledger(ledger)["entries"]["AAA"]
    assert entry["historical_only"] is True and entry["decision_at_scan"] == "WATCH"
    assert entry["source_date_ist"] == NOW.date().isoformat()
    assert historical_summary(ledger)["historical_records"] == 1


def test_capture_refuses_tampered_checkpoint_or_missing_archive(tmp_path):
    repo, out, csv, report, queue, ledger = fixture(tmp_path)
    state = run_one(repo, out, csv, report, queue)
    d = json.loads(state.read_text(encoding="utf-8"))
    d["completed"]["AAA"]["decision"] = "CANDIDATE"
    state.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ScanSafetyError, match="Merged report content disagrees"):
        record_session(ledger, state)
    d["completed"]["AAA"]["decision"] = "WATCH"
    state.write_text(json.dumps(d), encoding="utf-8")
    (Path(d["session_dir"]) / "batch_0001" / "results.json").unlink()
    with pytest.raises(ScanSafetyError, match="archived child results"):
        record_session(ledger, state)


def test_prepare_daily_input_excludes_only_archived_and_preserves_original(tmp_path):
    repo, out, csv, report, queue, ledger = fixture(tmp_path)
    state = run_one(repo, out, csv, report, queue)
    record_session(ledger, state)
    source = report.read_bytes()
    result = prepare_day(repo, ledger, report, queue, csv, out / "campaign", now=NOW)
    assert result["pending"] == 2 and result["historical"] == 1
    assert report.read_bytes() == source
    new = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
    assert new["selected_symbols"] == ["BBB", "CCC"]
    assert new["campaign_provenance"]["excluded_historical_count"] == 1
    assert Path(result["queue"]).read_text(encoding="utf-8") == "BBB\nCCC\n"
    plan = json.loads((out / "campaign" / "current_plan.json").read_text(encoding="utf-8"))
    assert plan["queue"] == result["queue"] and plan["report"] == result["report"]
    x = load_inputs(repo, Path(result["queue"]), Path(result["report"]), csv, now=NOW)
    assert x["symbols"] == ["BBB", "CCC"]


def test_prepare_requires_fresh_and_skip_review(tmp_path):
    repo, out, csv, report, queue, ledger = fixture(tmp_path, skip_review=False)
    with pytest.raises(ScanSafetyError, match="--skip-review"):
        prepare_day(repo, ledger, report, queue, csv, out / "campaign", now=NOW)
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["skip_review"] = True
    report.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(report, (NOW.timestamp() - 86400, NOW.timestamp() - 86400))
    with pytest.raises(ScanSafetyError, match="not generated today"):
        prepare_day(repo, ledger, report, queue, csv, out / "campaign", now=NOW)


def test_prepare_blocks_unhealthy_event_source(tmp_path):
    repo, out, csv, report, queue, ledger = fixture(tmp_path, event_ok=False)
    with pytest.raises(ScanSafetyError, match="candidate-grade"):
        prepare_day(repo, ledger, report, queue, csv, out / "campaign", now=NOW)


def test_prepare_no_pending_does_not_create_runnable_empty_queue(tmp_path):
    repo, out, csv, report, queue, ledger = fixture(tmp_path, symbols=["AAA"])
    state = run_one(repo, out, csv, report, queue)
    record_session(ledger, state)
    (out / "campaign" / "current_plan.json").write_text("stale", encoding="utf-8")
    result = prepare_day(repo, ledger, report, queue, csv, out / "campaign", now=NOW)
    assert result["pending"] == 0 and result["queue"] is None
    assert not (out / "campaign" / "current_plan.json").exists()


def test_record_refuses_conflicting_later_result_for_same_symbol(tmp_path):
    repo, out, csv, report, queue, ledger = fixture(tmp_path)
    state = run_one(repo, out, csv, report, queue)
    record_session(ledger, state)
    # A deliberate second daily session scanning AAA cannot overwrite archive.
    args = load_inputs(repo, queue, report, csv, now=NOW)
    def child(cmd, log, timeout):
        (out / "shortlist.json").write_text(json.dumps([{"symbol": "AAA", "decision": "REJECT", "risk_flags": []}]), encoding="utf-8")
        log.write_text("mock\n", encoding="utf-8")
        return 0
    run_session(repo, state, out / "larger_scan", args, new_run=True, now_fn=lambda: NOW,
                execute=child, cooldown_seconds=0)
    with pytest.raises(ScanSafetyError, match="already recorded"):
        record_session(ledger, state)