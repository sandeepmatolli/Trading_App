
"""Pure offline request-budget checks; no SDK calls, credentials or market access."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from market_data.groww_fetch import NEW_API_MAX_DAYS, _build_windows
from screening.batch_runner import ScanSafetyError
from screening.request_budget import estimate_request_budget, queue_size
from request_budget import main

AS_OF = datetime(2026, 10, 3, 16, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def test_exact_lookback_uses_actual_fetch_window_builder_and_caps():
    d = estimate_request_budget(2057, batch_size=1, max_batches=1, as_of=AS_OF)
    from datetime import timedelta
    expected_h = len(_build_windows(AS_OF-timedelta(days=730), AS_OF, min(60, NEW_API_MAX_DAYS[60])))
    expected_s = len(_build_windows(AS_OF-timedelta(days=89), AS_OF, min(30, NEW_API_MAX_DAYS[15])))
    assert d["hourly"]["baseline_requests_per_symbol"] == expected_h
    assert d["setup_15m"]["baseline_requests_per_symbol"] == expected_s
    assert d["baseline_candle_requests_this_invocation"] == expected_h+expected_s
    assert d["baseline_candle_requests_full_queue"] == 2057*(expected_h+expected_s)
    assert d["mode"] == "OFFLINE_ESTIMATE_NO_PROVIDER_REQUESTS"
    assert "NOT a ceiling" in d["warning"]


def test_bounded_batches_are_estimated_not_the_whole_queue():
    d = estimate_request_budget(2057,batch_size=4,max_batches=2,as_of=AS_OF)
    assert d["planned_symbols_this_invocation"] == 8
    assert d["planned_child_launches"] == 2
    assert d["baseline_candle_requests_this_invocation"] == 8*d["baseline_candle_requests_per_symbol"]


def test_short_queue_requires_only_one_partial_batch():
    d=estimate_request_budget(3,batch_size=4,max_batches=8,as_of=AS_OF)
    assert d["planned_symbols_this_invocation"] == 3 and d["planned_child_launches"] == 1


def test_configured_chunk_is_capped_by_fetcher_limit():
    d=estimate_request_budget(1,hourly_days=730,setup_days=89,hourly_chunk_days=9999,setup_chunk_days=9999,as_of=AS_OF)
    assert d["hourly"]["effective_chunk_days"] == NEW_API_MAX_DAYS[60]
    assert d["setup_15m"]["effective_chunk_days"] == NEW_API_MAX_DAYS[15]


@pytest.mark.parametrize("kwargs",[{"symbol_count":0},{"symbol_count":True},{"symbol_count":1,"batch_size":5},{"symbol_count":1,"max_batches":0},{"symbol_count":1,"setup_days":0},{"symbol_count":1,"hourly_chunk_days":-1},{"symbol_count":1,"as_of":datetime(2026,10,3)}])
def test_invalid_inputs_fail_closed(kwargs):
    with pytest.raises(ValueError): estimate_request_budget(**kwargs)


def test_queue_count_checks_duplicates_and_symbol_shape(tmp_path):
    p=tmp_path/"symbols.txt"
    p.write_text("AAA\nBBB\n",encoding="utf-8")
    assert queue_size(p)==2
    p.write_text("AAA\naaa\n",encoding="utf-8")
    with pytest.raises(ScanSafetyError,match="unique"):queue_size(p)
    p.write_text("AAA\nBAD SYMBOL\n",encoding="utf-8")
    with pytest.raises(ScanSafetyError,match="invalid"):queue_size(p)


def test_cli_is_read_only_and_describes_limits(capsys,tmp_path):
    assert main(["--symbol-count","8","--batch-size","4","--max-batches","1"]) == 0
    output=capsys.readouterr().out
    assert '"planned_symbols_this_invocation": 4' in output
    assert "NO_PROVIDER_REQUESTS" in output
    assert not list(tmp_path.iterdir())


def test_cli_invalid_queue_stops_without_network(capsys,tmp_path):
    assert main(["--queue",str(tmp_path/"missing.txt")]) == 2
    assert "REQUEST BUDGET STOP" in capsys.readouterr().out
