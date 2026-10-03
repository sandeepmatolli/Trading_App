"""A caught pipeline error must not poison a later valid archive for that symbol."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import os
from screening.batch_runner import CODE_FILES, load_inputs, run_session
from screening.campaign import load_ledger, record_session

NOW = datetime(2026, 10, 3, 16, 0, tzinfo=ZoneInfo('Asia/Kolkata'))


def _setup(tmp_path, symbols):
    root = tmp_path / 'root'; root.mkdir()
    for name in CODE_FILES:
        path = root / name; path.parent.mkdir(parents=True,exist_ok=True);path.write_text('# '+name+'\n',encoding='utf-8')
    csv = root / 'stocks_screen.csv'; csv.write_text('NSE Code\n'+'\n'.join(symbols),encoding='utf-8')
    out = root/'data'/'output';out.mkdir(parents=True)
    queue=out/'prefilter_selected_symbols.txt';queue.write_text('\n'.join(symbols)+'\n',encoding='utf-8')
    report=out/'prefilter_report.json';report.write_text(json.dumps({'stage':'fundamental_event_prefilter','source_event_candidate_eligible':True,'skip_review':True,'event_cache':{'stale_fallback':False},'selected_symbols':symbols,'rows':[{'symbol':s,'status':'ELIGIBLE_FOR_DEEP_SCAN'} for s in symbols]}),encoding='utf-8')
    for p in [queue, report]: os.utime(p,(NOW.timestamp(),NOW.timestamp()))
    return root,out,load_inputs(root,queue,report,csv,now=NOW)


def test_caught_pipeline_failure_then_retry_valid_watch_is_recordable(tmp_path):
    root,out,inp=_setup(tmp_path,['AAA'])
    state=out/'larger_scan'/'current_state.json'
    run_count=[0]
    def child(cmd,log,timeout):
        run_count[0]+=1
        if run_count[0]==1:
            row={'symbol':'AAA','decision':'DATA_REJECT','risk_flags':['Pipeline error: temporary Groww failure']}
        else:
            row={'symbol':'AAA','decision':'WATCH','risk_flags':[]}
        (out/'shortlist.json').write_text(json.dumps([row]),encoding='utf-8');log.write_text('mock\n',encoding='utf-8');return 0
    first=run_session(root,state,out/'larger_scan',inp,now_fn=lambda:NOW,execute=child,cooldown_seconds=0)
    assert first['ok'] is False and first['completed']==0
    second=run_session(root,state,out/'larger_scan',inp,now_fn=lambda:NOW,execute=child,cooldown_seconds=0)
    assert second['ok'] is True and second['completed']==1
    result=record_session(out/'campaign'/'campaign.json',state)
    entry=load_ledger(out/'campaign'/'campaign.json')['entries']['AAA']
    assert result['newly_recorded']==1 and entry['decision_at_scan']=='WATCH' and entry['batch_number']==2


def test_partial_multisymbol_pipeline_failure_does_not_block_later_audit(tmp_path):
    root,out,inp=_setup(tmp_path,['AAA','BBB','CCC'])
    state=out/'larger_scan'/'current_state.json'
    n=[0]
    def child(cmd,log,timeout):
        n[0]+=1
        names=cmd[-1].split(',')
        rows=[{'symbol':s,'decision':'DATA_REJECT','risk_flags':['Pipeline error: retryable']} if s=='BBB' and n[0]==1 else {'symbol':s,'decision':'WATCH','risk_flags':[]} for s in names]
        (out/'shortlist.json').write_text(json.dumps(rows),encoding='utf-8');log.write_text('mock\n',encoding='utf-8');return 0
    a=run_session(root,state,out/'larger_scan',inp,now_fn=lambda:NOW,execute=child,cooldown_seconds=0,batch_size=2)
    assert a['completed']==1 and a['pending']==2
    b=run_session(root,state,out/'larger_scan',inp,now_fn=lambda:NOW,execute=child,cooldown_seconds=0,batch_size=2)
    assert b['completed']==3 and b['pending']==0
    v=record_session(out/'campaign'/'campaign.json',state)
    assert v['newly_recorded']==3
    led=load_ledger(out/'campaign'/'campaign.json')
    assert led['entries']['BBB']['batch_number']==2


def test_quality_data_reject_stays_recordable(tmp_path):
    root,out,inp=_setup(tmp_path,['AAA']);state=out/'larger_scan'/'current_state.json'
    def child(cmd,log,timeout):
        (out/'shortlist.json').write_text(json.dumps([{'symbol':'AAA','decision':'DATA_REJECT','risk_flags':['Sparse 75m coverage']}]),encoding='utf-8');log.write_text('mock\n',encoding='utf-8');return 0
    res=run_session(root,state,out/'larger_scan',inp,now_fn=lambda:NOW,execute=child,cooldown_seconds=0)
    assert res['completed']==1
    result=record_session(out/'campaign'/'campaign.json',state)
    assert result['newly_recorded']==1
    assert load_ledger(out/'campaign'/'campaign.json')['entries']['AAA']['decision_at_scan']=='DATA_REJECT'