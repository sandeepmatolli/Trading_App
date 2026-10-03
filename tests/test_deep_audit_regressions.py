"""Offline checks of the repo-wide scanner/campaign and setup-history gates."""
from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from market_data.setup_history_gate import apply_requested_setup_history_gate
from screening.batch_runner import CODE_FILES, ScanSafetyError, load_inputs, run_session
from screening.campaign import prepare_day, record_session, verify_previous_campaign_checkpoint

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 3, 16, 0, tzinfo=IST)


def daily_setup(daily_sessions=60, setup_sessions=20):
    dates = list(pd.bdate_range(end=pd.Timestamp("2026-10-02", tz=IST), periods=daily_sessions))
    daily = pd.DataFrame({"ts": dates})
    subset = dates[-setup_sessions:]
    setup = pd.DataFrame([
        {"ts": d + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(minutes=75*i), "is_usable": True}
        for d in subset for i in range(5)
    ])
    return daily, setup


def test_older_requested_setup_sessions_do_not_disappear_from_denominator():
    daily, setup = daily_setup(60, 20)
    quality = {"valid": True, "candidate_eligible": True,
               "setup_75m_usable_expected_ratio": 1.0, "candidate_blockers": []}
    out = apply_requested_setup_history_gate(quality, daily, setup, 89, as_of=NOW)
    assert out["valid"] is True
    assert out["candidate_eligible"] is False
    assert out["requested_setup_reference_sessions"] >= 40
    assert out["requested_setup_usable_ratio"] < .65
    assert out["setup_75m_usable_expected_ratio"] == 1.0  # original observed-span metric
    assert "Requested-lookback" in out["candidate_blockers"][0]


def test_real_complete_requested_history_still_candidate_grade():
    daily, setup = daily_setup(60, 60)
    q = {"valid": True, "candidate_eligible": True, "candidate_blockers": []}
    out = apply_requested_setup_history_gate(q, daily, setup, 89, as_of=NOW)
    assert out["candidate_eligible"] is True
    assert out["requested_setup_usable_ratio"] == 1.0


def test_unchanged_prior_quality_failure_must_not_become_valid():
    daily, setup = daily_setup(60, 20)
    q = {"valid": False, "candidate_eligible": False, "reasons": ["recent gap"], "candidate_blockers": []}
    out = apply_requested_setup_history_gate(q, daily, setup, 89, as_of=NOW)
    assert out["valid"] is False and out["candidate_eligible"] is False
    assert out["reasons"] == ["recent gap"]


def test_nominal_shorter_lookback_cannot_create_ratio_above_one():
    daily, setup = daily_setup(60, 20)
    q = {"valid": True, "candidate_eligible": True, "candidate_blockers": []}
    out = apply_requested_setup_history_gate(q, daily, setup, 1, as_of=NOW)
    assert out["requested_setup_usable_ratio"] <= 1


def _campaign_scenario(tmp_path):
    root=tmp_path/'repo';root.mkdir()
    for name in CODE_FILES:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# stub\n')
    csv=root/'stocks_screen.csv';csv.write_text('NSE Code\nAAA\nBBB\n')
    out=root/'data'/'output';out.mkdir(parents=True)
    report=out/'prefilter_report.json'; queue=out/'prefilter_selected_symbols.txt'
    report.write_text(json.dumps({'stage':'fundamental_event_prefilter','source_event_candidate_eligible':True,
        'skip_review':True,'event_cache':{'stale_fallback':False},'selected_symbols':['AAA','BBB'],
        'rows':[{'symbol':s,'status':'ELIGIBLE_FOR_DEEP_SCAN'} for s in ['AAA','BBB']]}))
    queue.write_text('AAA\nBBB\n')
    for p in [report,queue]:os.utime(p,(NOW.timestamp(),NOW.timestamp()))
    campaign_root=out/'campaign';inp=load_inputs(root,queue,report,csv,now=NOW)
    plan=prepare_day(root,campaign_root/'campaign.json',report,queue,csv,campaign_root,now=NOW)
    campaign_inputs=load_inputs(root,Path(plan['queue']),Path(plan['report']),csv,now=NOW)
    state=out/'larger_scan'/'current_state.json'
    def child(cmd,log,timeout):
        (out/'shortlist.json').write_text(json.dumps([{'symbol':cmd[-1],'decision':'WATCH','risk_flags':[]}]))
        log.write_text('mock\n');return 0
    run_session(root,state,out/'larger_scan',campaign_inputs,now_fn=lambda:NOW,execute=child,cooldown_seconds=0)
    return root,out,csv,report,queue,campaign_root,state,child,campaign_inputs


def test_unrecorded_previous_campaign_blocks_new_plan(tmp_path):
    root,out,csv,report,queue,campaign_root,state,child,inp=_campaign_scenario(tmp_path)
    with pytest.raises(ScanSafetyError,match='unrecorded completed research'):
        prepare_day(root,campaign_root/'campaign.json',report,queue,csv,campaign_root,now=NOW)
    with pytest.raises(ScanSafetyError,match='unrecorded completed research'):
        verify_previous_campaign_checkpoint(campaign_root/'campaign.json',state,campaign_root)
    record_session(campaign_root/'campaign.json',state)
    p=prepare_day(root,campaign_root/'campaign.json',report,queue,csv,campaign_root,now=NOW)
    assert p['pending']==1


def test_newly_completed_rows_must_be_recorded_after_prior_partial_record(tmp_path):
    root,out,csv,report,queue,campaign_root,state,child,inp=_campaign_scenario(tmp_path)
    record_session(campaign_root/'campaign.json',state)
    run_session(root,state,out/'larger_scan',inp,now_fn=lambda:NOW,execute=child,cooldown_seconds=0)
    with pytest.raises(ScanSafetyError,match='unrecorded completed research'):
        verify_previous_campaign_checkpoint(campaign_root/'campaign.json',state,campaign_root)
    record_session(campaign_root/'campaign.json',state)
    verify_previous_campaign_checkpoint(campaign_root/'campaign.json',state,campaign_root)


def test_non_campaign_exploratory_session_is_not_hijacked(tmp_path):
    root,out,csv,report,queue,campaign_root,state,child,inp=_campaign_scenario(tmp_path)
    obj=json.loads(state.read_text());obj['report_path']=str(report);state.write_text(json.dumps(obj))
    verify_previous_campaign_checkpoint(campaign_root/'campaign.json',state,campaign_root)


def test_record_rejects_concurrent_batch_runner(tmp_path):
    root,out,csv,report,queue,campaign_root,state,child,inp=_campaign_scenario(tmp_path)
    lock=out/'larger_scan'/'.runner.lock';lock.write_text('in progress')
    try:
        with pytest.raises(ScanSafetyError,match='batch runner is active'):
            record_session(campaign_root/'campaign.json',state)
    finally:
        lock.unlink()
    record_session(campaign_root/'campaign.json',state)