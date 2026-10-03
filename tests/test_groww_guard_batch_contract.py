"""Offline boundary tests: Groww SDK cap -> fetcher -> batch checkpoint.

No real provider, credentials, network, orders, or edits to existing strategy code.
A fake child writes main.py's documented Pipeline error row shape to the
existing runner's expected local shortlist path.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from market_data.groww_auth import GuardedGrowwClient, GrowwSDKCallLimitReached
from market_data.groww_fetch import fetch_candles, resolve_groww_instrument
from screening.batch_runner import (
    CODE_FILES, _is_pipeline_failure, load_inputs, run_session,
    validate_child_results,
)

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


class FakeGroww:
    EXCHANGE_NSE = "NSE"
    SEGMENT_CASH = "CASH"
    CANDLE_INTERVAL_HOUR_1 = "60m"
    CANDLE_INTERVAL_MIN_15 = "15m"

    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def get_instrument_by_exchange_and_trading_symbol(self, **kwargs):
        self.calls.append("lookup")
        return {"groww_symbol": "NSE-AAA", "segment": "CASH"}

    def get_historical_candles(self, **kwargs):
        self.calls.append("candles")
        if self.fail:
            raise RuntimeError("simulated SDK failure")
        return {"candles": []}


def test_cap_failure_from_real_fetch_entrypoint_stops_before_second_candle():
    sdk = FakeGroww()
    guard = GuardedGrowwClient(sdk, max_calls=2)
    assert resolve_groww_instrument(guard, "AAA")["groww_symbol"] == "NSE-AAA"
    assert fetch_candles(guard, "NSE-AAA", 60, days_back=1, chunk_days=1).empty
    with pytest.raises(GrowwSDKCallLimitReached, match="BEFORE request"):
        fetch_candles(guard, "NSE-AAA", 15, days_back=1, chunk_days=1)
    assert sdk.calls == ["lookup", "candles"]
    assert guard.request_stats()["attempted"] == 2


def test_failed_sdk_attempt_consumes_cap_and_cannot_be_quality_completed(tmp_path):
    sdk = FakeGroww(fail=True)
    guard = GuardedGrowwClient(sdk, max_calls=2)
    resolve_groww_instrument(guard, "AAA")
    with pytest.raises(RuntimeError, match="simulated SDK failure"):
        fetch_candles(guard, "NSE-AAA", 60, days_back=1, chunk_days=1)
    with pytest.raises(GrowwSDKCallLimitReached):
        fetch_candles(guard, "NSE-AAA", 15, days_back=1, chunk_days=1)
    stats = guard.request_stats()
    assert stats["attempted"] == 2 and stats["failed"] == 1
    assert sdk.calls == ["lookup", "candles"]
    # The main.py per-symbol exception shape is retryable, never a final quality reject.
    row = {"symbol": "AAA", "decision": "DATA_REJECT", "risk_flags": [
        "Pipeline error: Configured Groww SDK calls-per-child ceiling reached BEFORE request"
    ]}
    file = tmp_path / "child.json"
    file.write_text(json.dumps([row]), encoding="utf-8")
    assert _is_pipeline_failure(validate_child_results(file, ["AAA"])[0]) is True
    assert _is_pipeline_failure({"symbol": "AAA", "decision": "DATA_REJECT", "risk_flags": ["Insufficient daily history"]}) is False


def _inputs(tmp_path: Path, symbols=("AAA",)):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in CODE_FILES:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# immutable fixture\n", encoding="utf-8")
    csv = repo / "stocks_screen.csv"
    csv.write_text("NSE Code\n" + "\n".join(symbols) + "\n", encoding="utf-8")
    out = repo / "data" / "output"
    out.mkdir(parents=True)
    queue = out / "prefilter_selected_symbols.txt"
    queue.write_text("\n".join(symbols) + "\n", encoding="utf-8")
    report = out / "prefilter_report.json"
    report.write_text(json.dumps({
        "stage": "fundamental_event_prefilter",
        "source_event_candidate_eligible": True,
        "event_cache": {"stale_fallback": False},
        "selected_symbols": list(symbols),
        "rows": [{"symbol": symbol, "status": "ELIGIBLE_FOR_DEEP_SCAN"} for symbol in symbols],
    }), encoding="utf-8")
    for path in (queue, report):
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))
    return repo, out, load_inputs(repo, queue, report, csv, now=NOW)


def test_exhausted_guard_row_is_archived_but_not_marked_complete(tmp_path):
    repo, out, inputs = _inputs(tmp_path)
    observed = []

    def fake_child(command, log_path, timeout):
        sdk = FakeGroww()
        guard = GuardedGrowwClient(sdk, max_calls=1)
        symbol = command[-1]
        try:
            resolve_groww_instrument(guard, symbol)
            fetch_candles(guard, "NSE-AAA", 60, days_back=1, chunk_days=1)
            raise AssertionError("Guard unexpectedly allowed over-cap request")
        except GrowwSDKCallLimitReached as exc:
            # Reproduce main.py's caught-exception output; no real subprocess or API.
            row = {"symbol": symbol, "decision": "DATA_REJECT", "risk_flags": [f"Pipeline error: {exc}"]}
        (out / "shortlist.json").write_text(json.dumps([row]), encoding="utf-8")
        log_path.write_text("simulated offline main output\n", encoding="utf-8")
        observed.append((sdk.calls, guard.request_stats()["attempted"]))
        return 0

    root = out / "larger_scan"
    result = run_session(repo, root / "current_state.json", root, inputs,
                         execute=fake_child, cooldown_seconds=0, now_fn=lambda: NOW)
    assert result["ok"] is False and result["completed"] == 0 and result["pending"] == 1
    assert "Pipeline error" in result["error"]
    assert observed == [(["lookup"], 1)]
    state = json.loads((root / "current_state.json").read_text(encoding="utf-8"))
    assert state["attempts"]["AAA"] == 1 and "AAA" not in state["completed"]
    archived = json.loads((Path(state["session_dir"]) / "batch_0001" / "results.json").read_text(encoding="utf-8"))
    assert archived[0]["decision"] == "DATA_REJECT" and _is_pipeline_failure(archived[0])
    merged = json.loads((Path(state["session_dir"]) / "merged_report.json").read_text(encoding="utf-8"))
    assert merged["results"] == [] and merged["pending_symbols"] == ["AAA"]


def test_guard_budget_is_new_for_each_child_not_an_account_quota(tmp_path):
    repo, out, inputs = _inputs(tmp_path, ("AAA", "BBB"))
    sdk_calls = []

    def fake_child(command, log_path, timeout):
        # A separate main.py child process creates its own per-child SDK wrapper.
        sdk = FakeGroww()
        guard = GuardedGrowwClient(sdk, max_calls=1)
        guard.get_historical_candles(groww_symbol="NSE-AAA")
        sdk_calls.append((command[-1], guard.request_stats()["attempted"]))
        symbol = command[-1]
        (out / "shortlist.json").write_text(json.dumps([
            {"symbol": symbol, "decision": "WATCH", "risk_flags": []}
        ]), encoding="utf-8")
        log_path.write_text("offline\n", encoding="utf-8")
        return 0

    root = out / "larger_scan"
    result = run_session(repo, root / "current_state.json", root, inputs,
                         execute=fake_child, max_batches=2, cooldown_seconds=0,
                         now_fn=lambda: NOW)
    assert result["ok"] is True and result["completed"] == 2 and result["pending"] == 0
    assert sdk_calls == [("AAA", 1), ("BBB", 1)]