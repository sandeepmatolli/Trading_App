
"""Offline integration tests through the real market_data.groww_fetch entrypoints.

No authentication, provider access, real network, Groww responses, or trades.
Fake SDK methods return only empty-candle payloads, so this tests request
routing, cap and pacing rather than candle quality or exchange availability.
"""
from __future__ import annotations

from market_data.groww_auth import GuardedGrowwClient, GrowwSDKCallLimitReached
from market_data.groww_fetch import fetch_candles, resolve_groww_instrument
import pytest


class FakeGroww:
    EXCHANGE_NSE = "NSE"
    SEGMENT_CASH = "CASH"
    CANDLE_INTERVAL_HOUR_1 = "60m"
    CANDLE_INTERVAL_MIN_15 = "15m"

    def __init__(self):
        self.calls = []
        self.fail_candles = False

    def get_instrument_by_exchange_and_trading_symbol(self, **kwargs):
        self.calls.append(("instrument", kwargs))
        return {"groww_symbol": "NSE-AAA", "segment": "CASH"}

    def get_historical_candles(self, **kwargs):
        self.calls.append(("candles", kwargs))
        if self.fail_candles:
            raise RuntimeError("fake-provider-failure")
        return {"candles": []}


def test_real_fetcher_routes_lookup_and_both_intervals_through_one_guard():
    sdk = FakeGroww()
    client = GuardedGrowwClient(sdk, max_calls=3)
    instrument = resolve_groww_instrument(client, "aaa")
    assert instrument["groww_symbol"] == "NSE-AAA"
    assert fetch_candles(client, "NSE-AAA", 60, days_back=1, chunk_days=1).empty
    assert fetch_candles(client, "NSE-AAA", 15, days_back=1, chunk_days=1).empty
    stats = client.request_stats()
    assert stats["attempted"] == stats["succeeded"] == 3
    assert stats["failed"] == 0
    assert stats["by_method"] == {
        "get_instrument_by_exchange_and_trading_symbol": 1,
        "get_historical_candles": 2,
    }
    assert [name for name, _ in sdk.calls] == ["instrument", "candles", "candles"]
    candle_kwargs = [kwargs for kind, kwargs in sdk.calls if kind == "candles"]
    assert [x["candle_interval"] for x in candle_kwargs] == ["60m", "15m"]
    assert all(x["exchange"] == "NSE" and x["segment"] == "CASH" for x in candle_kwargs)


def test_cap_stops_next_fetch_before_fake_provider_receives_it():
    sdk = FakeGroww()
    client = GuardedGrowwClient(sdk, max_calls=2)
    resolve_groww_instrument(client, "AAA")
    fetch_candles(client, "NSE-AAA", 60, days_back=1, chunk_days=1)
    with pytest.raises(GrowwSDKCallLimitReached, match="BEFORE request"):
        fetch_candles(client, "NSE-AAA", 15, days_back=1, chunk_days=1)
    assert [name for name, _ in sdk.calls] == ["instrument", "candles"]
    assert client.request_stats()["attempted"] == 2


def test_fetcher_provider_failure_counts_as_attempt_and_does_not_silently_retry():
    sdk = FakeGroww()
    sdk.fail_candles = True
    client = GuardedGrowwClient(sdk, max_calls=1)
    with pytest.raises(RuntimeError, match="fake-provider-failure"):
        fetch_candles(client, "NSE-AAA", 60, days_back=1, chunk_days=1)
    assert client.request_stats()["attempted"] == 1
    assert client.request_stats()["failed"] == 1
    with pytest.raises(GrowwSDKCallLimitReached):
        fetch_candles(client, "NSE-AAA", 15, days_back=1, chunk_days=1)
    assert len(sdk.calls) == 1


def test_real_fetcher_pacing_applies_between_instrument_and_candles():
    sdk = FakeGroww()
    clock = [10.0]
    waits = []

    def fake_sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    client = GuardedGrowwClient(
        sdk, min_start_gap_seconds=1.25, clock=lambda: clock[0], sleep=fake_sleep,
    )
    resolve_groww_instrument(client, "AAA")
    fetch_candles(client, "NSE-AAA", 60, days_back=1, chunk_days=1)
    fetch_candles(client, "NSE-AAA", 15, days_back=1, chunk_days=1)
    assert waits == [1.25, 1.25]
    assert client.request_stats()["attempted"] == 3


def test_missing_optional_lookup_preserves_fetcher_fallback():
    class CandlesOnly:
        EXCHANGE_NSE = "NSE"
        SEGMENT_CASH = "CASH"
        CANDLE_INTERVAL_HOUR_1 = "60m"
        def __init__(self): self.calls = 0
        def get_historical_candles(self, **kwargs):
            self.calls += 1
            return {"candles": []}

    sdk = CandlesOnly()
    client = GuardedGrowwClient(sdk, max_calls=1)
    assert not hasattr(client, "get_instrument_by_exchange_and_trading_symbol")
    assert resolve_groww_instrument(client, "aaa")["groww_symbol"] == "NSE-AAA"
    assert client.request_stats()["attempted"] == 0
    assert fetch_candles(client, "NSE-AAA", 60, days_back=1, chunk_days=1).empty
    assert sdk.calls == 1
    assert client.request_stats()["attempted"] == 1