"""History audit tests use fabricated offline child results, never live endpoints."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from screening.batch_runner import CODE_FILES, ScanSafetyError, load_inputs, run_session
from screening.campaign import load_ledger, prepare_day, record_session
from screening.campaign_history import build_campaign_history_report

NOW = datetime(2026, 10, 3, 17, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def scenario(tmp_path, *, symbols=("AAA", "BBB"), decisions=None):
    decisions = decisions or {}
    root = tmp_path / "repo"; root.mkdir()
    for name in CODE_FILES:
        p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("# stub\n", encoding="utf-8")
    csv = root / "stocks_screen.csv"; csv.write_text("NSE Code\n" + "\n".join(symbols) + "\n", encoding="utf-8")
    out = root / "data" / "output"; out.mkdir(parents=True)
    report, queue = out / "prefilter_report.json", out / "prefilter_selected_symbols.txt"
    report.write_text(json.dumps({"stage":"fundamental_event_prefilter", "source_event_candidate_eligible":True,
                                  "event_cache":{"stale_fallback":False}, "skip_review":True,
                                  "selected_symbols":list(symbols),
                                  "rows":[{"symbol":s,"status":"ELIGIBLE_FOR_DEEP_SCAN"} for s in symbols]}), encoding="utf-8")
    queue.write_text("\n".join(symbols)+"\n", encoding="utf-8")
    for p in (report, queue): os.utime(p, (NOW.timestamp(), NOW.timestamp()))
    camp = out / "campaign"; ledger = camp / "campaign.json"
    plan = prepare_day(root, ledger, report, queue, csv, camp, now=NOW)
    inp = load_inputs(root, Path(plan["queue"]), Path(plan["report"]), csv, now=NOW)
    state = out / "larger_scan" / "current_state.json"
    def child(command, log_path, timeout):
        sym = command[-1]
        (out / "shortlist.json").write_text(json.dumps([{"symbol":sym,"decision":decisions.get(sym,"WATCH"),"risk_flags":[]}]))
        log_path.write_text("offline\n"); return 0
    x = run_session(root, state, state.parent, inp, now_fn=lambda:NOW, execute=child, cooldown_seconds=0)
    assert x["completed"] == 1
    record_session(ledger, state)
    return root,out,csv,report,queue,camp,ledger,state,inp,child


def report_for(ledger, camp, **kwargs):
    return build_campaign_history_report(ledger, camp, now=NOW + timedelta(days=1), **kwargs)


def test_historical_report_verifies_partial_campaign_and_never_calls_live(tmp_path):
    root,out,csv,report,queue,camp,ledger,state,inp,child=scenario(tmp_path)
    result = report_for(ledger, camp)
    body=result["report"]
    assert body["session_count"] == 1 and body["historical_record_count"] == 1
    assert body["sessions"][0]["queue_count_at_last_merge"] == 2
    assert body["sessions"][0]["pending_at_last_merge"] == 1
    assert body["items"][0]["review_status"] == "HISTORICAL_RESEARCH_ONLY"
    assert "NOT_CURRENT_SIGNALS" in body["status"]
    assert Path(result["path"]).exists()


def test_historical_candidate_always_requires_revalidation(tmp_path):
    root,out,csv,report,queue,camp,ledger,state,inp,child=scenario(tmp_path, decisions={"AAA":"CANDIDATE"})
    body=report_for(ledger,camp)["report"]
    assert body["historical_candidate_count_revalidation_required"] == 1
    assert body["items"][0]["review_status"] == "HISTORICAL_MANUAL_REVALIDATION_REQUIRED"


def test_report_survives_shared_checkpoint_pointer_replacement(tmp_path):
    root,out,csv,report,queue,camp,ledger,state,inp,child=scenario(tmp_path)
    old_sid=json.loads(state.read_text())["session_id"]
    plan=prepare_day(root,ledger,report,queue,csv,camp,now=NOW+timedelta(minutes=5))
    new_inp=load_inputs(root,Path(plan["queue"]),Path(plan["report"]),csv,now=NOW+timedelta(minutes=5))
    x=run_session(root,state,state.parent,new_inp,new_run=True,now_fn=lambda:NOW+timedelta(minutes=5),execute=child,cooldown_seconds=0)
    assert x["completed"] == 1 and json.loads(state.read_text())["session_id"] != old_sid
    record_session(ledger,state)
    body=report_for(ledger,camp)["report"]
    assert body["session_count"] == 2 and body["historical_record_count"] == 2
    assert {x["symbol"] for x in body["items"]} == {"AAA","BBB"}


def test_edited_merged_research_fails_without_overwriting_report(tmp_path):
    root,out,csv,report,queue,camp,ledger,state,inp,child=scenario(tmp_path)
    first=report_for(ledger,camp)
    output=Path(first["path"]); original=output.read_bytes()
    folder=Path(json.loads(state.read_text())["session_dir"])
    merged=folder/"merged_report.json"; obj=json.loads(merged.read_text());obj["results"][0]["decision"]="REJECT";merged.write_text(json.dumps(obj))
    with pytest.raises(ScanSafetyError,match="Merged report"):
        report_for(ledger,camp)
    assert output.read_bytes() == original


def test_edited_child_archive_fails(tmp_path):
    root,out,csv,report,queue,camp,ledger,state,inp,child=scenario(tmp_path)
    folder=Path(json.loads(state.read_text())["session_dir"])
    (folder/"batch_0001"/"results.json").write_text(json.dumps([{"symbol":"AAA","decision":"REJECT"}]))
    with pytest.raises(ScanSafetyError,match="Archived child result mismatch"):
        report_for(ledger,camp)


def test_unrecorded_later_batch_blocks_history_report(tmp_path):
    root,out,csv,report,queue,camp,ledger,state,inp,child=scenario(tmp_path)
    x=run_session(root,state,state.parent,inp,now_fn=lambda:NOW,execute=child,cooldown_seconds=0)
    assert x["completed"]==2
    with pytest.raises(ScanSafetyError,match="Merged report and recorded campaign manifest disagree"):
        report_for(ledger,camp)
    record_session(ledger,state)
    assert report_for(ledger,camp)["report"]["historical_record_count"]==2


def test_orphan_ledger_entry_fails(tmp_path):
    root,out,csv,report,queue,camp,ledger,state,inp,child=scenario(tmp_path)
    obj=json.loads(ledger.read_text());obj["entries"]["ZZZ"]={"source_session_id":"missing","historical_only":True};ledger.write_text(json.dumps(obj))
    with pytest.raises(ScanSafetyError,match="orphan entries"):
        report_for(ledger,camp)


def test_report_only_writes_inside_campaign_reports(tmp_path):
    root,out,csv,report,queue,camp,ledger,state,inp,child=scenario(tmp_path)
    with pytest.raises(ScanSafetyError,match="directly under campaign/reports"):
        report_for(ledger,camp,output_path=ledger)
    assert load_ledger(ledger)["entries"]["AAA"]["historical_only"] is True


def test_runner_lock_blocks_historical_reporting(tmp_path):
    root,out,csv,report,queue,camp,ledger,state,inp,child=scenario(tmp_path)
    lock=state.parent/".runner.lock";lock.write_text("active")
    try:
        with pytest.raises(ScanSafetyError,match="scanner is active"):
            report_for(ledger,camp)
    finally:
        lock.unlink()


def test_malformed_completed_row_fails_closed(tmp_path):
    root,out,csv,report,queue,camp,ledger,state,inp,child=scenario(tmp_path)
    folder=Path(json.loads(state.read_text())["session_dir"])
    merged=folder/"merged_report.json"; obj=json.loads(merged.read_text())
    obj["results"][0]["decision"]=["CANDIDATE"]
    merged.write_text(json.dumps(obj))
    with pytest.raises(ScanSafetyError,match="Invalid historical research row"):
        report_for(ledger,camp)
