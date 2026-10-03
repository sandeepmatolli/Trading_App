"""Offline integrity/anti-signal regression coverage for same-day research digest."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from screening.batch_runner import CODE_FILES, ScanSafetyError, load_inputs, run_session
from screening.campaign import record_session
from screening.research_digest import build_same_day_digest

NOW = datetime(2026, 10, 3, 16, 30, tzinfo=ZoneInfo("Asia/Kolkata"))


def case(tmp_path, decision="WATCH"):
    repo = tmp_path / "repo"; repo.mkdir()
    for name in CODE_FILES:
        p = repo / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("# stub\n")
    csv = repo / "stocks_screen.csv"; csv.write_text("NSE Code\nAAA\n")
    out = repo / "data" / "output"; out.mkdir(parents=True)
    report, queue = out / "prefilter_report.json", out / "prefilter_selected_symbols.txt"
    report.write_text(json.dumps({"stage":"fundamental_event_prefilter", "source_event_candidate_eligible":True,
                                  "event_cache":{"stale_fallback":False}, "selected_symbols":["AAA"],
                                  "skip_review":True, "rows":[{"symbol":"AAA","status":"ELIGIBLE_FOR_DEEP_SCAN"}]}))
    queue.write_text("AAA\n")
    for p in [report,queue]: os.utime(p,(NOW.timestamp(),NOW.timestamp()))
    inp = load_inputs(repo,queue,report,csv,now=NOW)
    state, camp = out / "larger_scan" / "current_state.json", out / "campaign"
    from screening.campaign import prepare_day
    plan = prepare_day(repo,camp / "campaign.json",report,queue,csv,camp,now=NOW)
    cinp = load_inputs(repo,Path(plan["queue"]),Path(plan["report"]),csv,now=NOW)
    def child(cmd,log,timeout):
        row={"symbol":"AAA","decision":decision,"risk_flags":[],
             "market_data_quality":{"valid":True,"candidate_eligible":True},
             "event_profile":{"candidate_eligible":True},
             "technical_profile":{"closed_bar_guard":{"enabled":True}}}
        (out/"shortlist.json").write_text(json.dumps([row]));log.write_text("mock\n");return 0
    scan=run_session(repo,state,out/"larger_scan",cinp,now_fn=lambda:NOW,execute=child,cooldown_seconds=0)
    assert scan["completed"]==1
    return out,state,camp


def test_audited_same_day_watch_is_archived_not_signal(tmp_path):
    out,state,camp=case(tmp_path)
    record_session(camp/"campaign.json",state)
    d=build_same_day_digest(state,camp/"campaign.json",camp,now=NOW)
    x=d["digest"]
    assert x["completed_count"]==1 and x["pending_count"]==0
    assert x["items"][0]["review_status"]=="RESEARCH_ARCHIVE_ONLY"
    assert "NOT_LIVE_SIGNALS" in x["status"]
    assert Path(d["path"]).exists()


def test_candidate_never_promoted_to_a_trade_signal(tmp_path):
    out,state,camp=case(tmp_path,"CANDIDATE")
    record_session(camp/"campaign.json",state)
    x=build_same_day_digest(state,camp/"campaign.json",camp,now=NOW)["digest"]
    assert x["candidate_review_count"]==1
    assert x["items"][0]["candidate_evidence_present_at_scan"] is True
    assert x["items"][0]["review_status"]=="MANUAL_REVALIDATION_REQUIRED"


def test_unrecorded_campaign_cannot_create_digest(tmp_path):
    out,state,camp=case(tmp_path)
    with pytest.raises(ScanSafetyError,match="recorded in campaign ledger"):
        build_same_day_digest(state,camp/"campaign.json",camp,now=NOW)


def test_older_day_cannot_be_misrepresented_as_today(tmp_path):
    out,state,camp=case(tmp_path)
    record_session(camp/"campaign.json",state)
    with pytest.raises(ScanSafetyError,match="Only today's IST"):
        build_same_day_digest(state,camp/"campaign.json",camp,now=NOW+timedelta(days=1))


def test_tampered_archive_fails_without_creating_digest(tmp_path):
    out,state,camp=case(tmp_path)
    record_session(camp/"campaign.json",state)
    obj=json.loads(state.read_text())
    archive=Path(obj["session_dir"])/"batch_0001"/"results.json"
    archive.write_text(json.dumps([{"symbol":"AAA","decision":"REJECT"}]))
    with pytest.raises(ScanSafetyError,match="Archived child result differs"):
        build_same_day_digest(state,camp/"campaign.json",camp,now=NOW)
    assert not list((camp/"digests").glob("*.json")) if (camp/"digests").exists() else True


def test_active_runner_blocks_digest(tmp_path):
    out,state,camp=case(tmp_path)
    record_session(camp/"campaign.json",state)
    lock=out/"larger_scan"/".runner.lock";lock.write_text("pid=1")
    with pytest.raises(ScanSafetyError,match="Batch runner is active"):
        build_same_day_digest(state,camp/"campaign.json",camp,now=NOW)


def test_tampered_checkpoint_fails_ledger_attestation(tmp_path):
    out,state,camp=case(tmp_path)
    record_session(camp/"campaign.json",state)
    obj=json.loads(state.read_text());obj["completed"]["AAA"]["decision"]="CANDIDATE"
    state.write_text(json.dumps(obj))
    with pytest.raises(ScanSafetyError,match="checkpoint and merged-report"):
        build_same_day_digest(state,camp/"campaign.json",camp,now=NOW)