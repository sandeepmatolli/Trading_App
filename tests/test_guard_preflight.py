"""Pure offline comparisons against the actual request-budget and guard settings helpers."""
from datetime import datetime
import json
from zoneinfo import ZoneInfo

import pytest

from guard_preflight import main
from screening.guard_preflight import build_guard_preflight

NOW = datetime(2026, 10, 3, 17, 30, tzinfo=ZoneInfo("Asia/Kolkata"))


def report(cap, count=2057, batch_size=1, batches=1):
    return build_guard_preflight(count, batch_size=batch_size, max_batches=batches,
                                 max_calls_override=cap, as_of=NOW)


def test_default_baseline_is_sourced_from_existing_request_budget():
    r = report(0)
    assert r["baseline_candle_calls_per_symbol"] == 16
    assert r["baseline_candle_calls_largest_child"] == 16
    assert r["cap_comparison"] == "PER_CHILD_HARD_CAP_DISABLED"
    assert r["mode"] == "OFFLINE_GUARD_PREFLIGHT_NO_PROVIDER_REQUESTS"


def test_cap_below_candle_baseline_is_flagged():
    r = report(15)
    assert r["cap_comparison"] == "BELOW_BASELINE_CANDLE_CALLS_IF_ALL_REACH_BOTH_FETCHES"


def test_one_symbol_cap_sixteen_is_below_optional_lookup_estimate():
    r = report(16)
    assert r["baseline_plus_optional_lookup_largest_child"] == 17
    assert r["cap_comparison"] == "BELOW_BASELINE_PLUS_OPTIONAL_LOOKUP_PER_SYMBOL"


def test_at_initial_estimate_is_not_called_sufficient():
    r = report(17)
    assert r["cap_comparison"] == "AT_OR_ABOVE_INITIAL_ESTIMATE_NOT_PROVEN_SUFFICIENT"
    assert any("Gap repairs" in s for s in r["limitations"])


def test_largest_child_not_full_queue_or_full_invocation():
    r = report(60, count=2057, batch_size=4, batches=2)
    assert r["planned_symbols_this_invocation"] == 8
    assert r["planned_child_launches"] == 2
    assert r["largest_planned_child_symbols"] == 4
    assert r["baseline_candle_calls_largest_child"] == 64
    assert r["baseline_plus_optional_lookup_largest_child"] == 68
    assert r["cap_comparison"] == "BELOW_BASELINE_CANDLE_CALLS_IF_ALL_REACH_BOTH_FETCHES"


def test_short_queue_one_partial_child():
    r = report(60, count=3, batch_size=4, batches=2)
    assert r["planned_child_launches"] == 1
    assert r["largest_planned_child_symbols"] == 3
    assert r["baseline_candle_calls_largest_child"] == 48


@pytest.mark.parametrize("cap", [True, -1, 100001, 1.25, "16"])
def test_invalid_hypothetical_caps_stop(cap):
    with pytest.raises(ValueError, match="max_calls_override"):
        build_guard_preflight(1, max_calls_override=cap, as_of=NOW)


def test_reads_actual_guard_env_without_sdk_authentication(monkeypatch):
    monkeypatch.setenv("GROWW_SDK_MIN_START_GAP_SECONDS", "1.5")
    monkeypatch.setenv("GROWW_SDK_MAX_CALLS_PER_CHILD", "18")
    monkeypatch.setenv("GROWW_SDK_CALL_TELEMETRY", "true")
    r = build_guard_preflight(1, as_of=NOW)
    assert r["settings_source"] == "LOCAL_ENV_GUARD_SETTINGS"
    assert r["configured_min_start_gap_seconds"] == 1.5
    assert r["configured_telemetry"] is True
    assert r["configured_or_hypothetical_max_calls_per_child"] == 18


def test_invalid_actual_env_stops_before_any_provider_request(monkeypatch):
    monkeypatch.setenv("GROWW_SDK_MAX_CALLS_PER_CHILD", "invalid")
    with pytest.raises(ValueError, match="Invalid Groww SDK pacing"):
        build_guard_preflight(1, as_of=NOW)


def test_hypothetical_override_never_modifies_local_env(monkeypatch):
    monkeypatch.setenv("GROWW_SDK_MAX_CALLS_PER_CHILD", "5")
    r = report(99)
    assert r["settings_source"] == "HYPOTHETICAL_OVERRIDE_NO_ENV_MODIFICATION"
    assert r["configured_min_start_gap_seconds"] is None
    assert r["configured_or_hypothetical_max_calls_per_child"] == 99
    assert __import__("os").getenv("GROWW_SDK_MAX_CALLS_PER_CHILD") == "5"


def test_cli_prints_json_without_writing_or_fetching(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert main(["--symbol-count", "2057", "--batch-size", "4", "--max-batches", "2", "--max-calls", "64"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["largest_planned_child_symbols"] == 4
    assert parsed["cap_comparison"] == "BELOW_BASELINE_PLUS_OPTIONAL_LOOKUP_PER_SYMBOL"
    assert list(tmp_path.iterdir()) == []


def test_cli_counts_queue_and_rejects_duplicate_queue(capsys, tmp_path):
    queue = tmp_path / "queue.txt"
    queue.write_text("AAA\nBBB\n", encoding="utf-8")
    assert main(["--queue", str(queue), "--max-calls", "34", "--batch-size", "2"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["queue_symbols"] == 2
    assert parsed["baseline_plus_optional_lookup_largest_child"] == 34
    queue.write_text("AAA\naaa\n", encoding="utf-8")
    assert main(["--queue", str(queue)]) == 2
    assert "GUARD PREFLIGHT STOP" in capsys.readouterr().out
