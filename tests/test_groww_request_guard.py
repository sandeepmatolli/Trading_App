"""Offline tests: no real Groww SDK, network access, credentials, or sleeps."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import sys
import types
from pathlib import Path

import pytest


class FakeSDK:
    EXCHANGE_NSE = "NSE"
    SEGMENT_CASH = "CASH"
    CANDLE_INTERVAL_HOUR_1 = "1hour"
    calls = []

    def __init__(self, token):
        self.token = token

    @staticmethod
    def get_access_token(*, api_key, secret):
        return "ephemeral-token"

    def get_instrument_by_exchange_and_trading_symbol(self, **kwargs):
        self.calls.append("instrument")
        return {"groww_symbol": "NSE-AAA", "segment": "CASH"}

    def get_historical_candles(self, **kwargs):
        self.calls.append("candles")
        return {"candles": []}


def load_auth(monkeypatch):
    mock_sdk = types.ModuleType("growwapi")
    mock_sdk.GrowwAPI = FakeSDK
    mock_config = types.ModuleType("config")
    mock_config.GROWW_ACCESS_TOKEN = "local-test-token"
    mock_config.GROWW_API_KEY = ""
    mock_config.GROWW_API_SECRET = ""
    monkeypatch.setitem(sys.modules, "growwapi", mock_sdk)
    monkeypatch.setitem(sys.modules, "config", mock_config)
    location = Path(__file__).resolve().parents[1] / "market_data" / "groww_auth.py"
    spec = spec_from_file_location("_isolated_groww_auth_under_test", location)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    FakeSDK.calls = []
    return module


def test_default_wrapper_preserves_constants_and_method_shapes(monkeypatch):
    a = load_auth(monkeypatch)
    for name in ("GROWW_SDK_MIN_START_GAP_SECONDS", "GROWW_SDK_MAX_CALLS_PER_CHILD", "GROWW_SDK_CALL_TELEMETRY"):
        monkeypatch.delenv(name, raising=False)
    client = a.get_groww_api()
    assert client.EXCHANGE_NSE == "NSE"
    assert client.CANDLE_INTERVAL_HOUR_1 == "1hour"
    assert client.get_instrument_by_exchange_and_trading_symbol(trading_symbol="AAA")["groww_symbol"] == "NSE-AAA"
    assert client.get_historical_candles(groww_symbol="NSE-AAA") == {"candles": []}
    assert client.request_stats()["attempted"] == 2
    assert client.request_stats()["configured_max_calls"] == 0
    assert FakeSDK.calls == ["instrument", "candles"]


def test_gap_applies_across_methods_and_attempts_and_counts(monkeypatch):
    a = load_auth(monkeypatch)
    instant = [10.0]
    waits = []

    def fake_sleep(delta):
        waits.append(round(delta, 3))
        instant[0] += delta

    sdk = FakeSDK("secret")
    client = a.GuardedGrowwClient(sdk, min_start_gap_seconds=1.5, clock=lambda: instant[0], sleep=fake_sleep)
    client.get_historical_candles(secret="never print")
    client.get_instrument_by_exchange_and_trading_symbol(secret="never print")
    instant[0] += 0.5
    client.get_historical_candles()
    assert waits == [1.5, 1.0]
    assert client.request_stats()["by_method"] == {"get_historical_candles": 2, "get_instrument_by_exchange_and_trading_symbol": 1}


def test_limit_fails_before_network_even_after_provider_failure(monkeypatch):
    a = load_auth(monkeypatch)
    sdk = FakeSDK("token")

    def fail(**kwargs):
        FakeSDK.calls.append("failure")
        raise RuntimeError("provider error")

    sdk.get_historical_candles = fail
    client = a.GuardedGrowwClient(sdk, max_calls=1)
    with pytest.raises(RuntimeError, match="provider error"):
        client.get_historical_candles()
    with pytest.raises(a.GrowwSDKCallLimitReached, match="BEFORE request"):
        client.get_instrument_by_exchange_and_trading_symbol()
    assert FakeSDK.calls == ["failure"]
    assert client.request_stats()["attempted"] == 1
    assert client.request_stats()["failed"] == 1


def test_optional_missing_method_remains_missing(monkeypatch):
    a = load_auth(monkeypatch)
    obj = types.SimpleNamespace(get_historical_candles=lambda **kw: {"candles": []})
    p = a.GuardedGrowwClient(obj)
    assert not hasattr(p, "get_instrument_by_exchange_and_trading_symbol")
    assert p.get_historical_candles() == {"candles": []}


@pytest.mark.parametrize("gap,limit", [(-1,0), (float('nan'),0), (float('inf'),0), (61,0), (0,-1), (0,True), (True,0), (0,100001)])
def test_invalid_config_fails_before_calls(monkeypatch,gap,limit):
    a=load_auth(monkeypatch)
    with pytest.raises(ValueError):
        a.GuardedGrowwClient(FakeSDK("token"), min_start_gap_seconds=gap, max_calls=limit)
    assert FakeSDK.calls == []


def test_invalid_environment_stops_before_authentication(monkeypatch):
    a=load_auth(monkeypatch)
    monkeypatch.setenv("GROWW_SDK_MIN_START_GAP_SECONDS", "nan")
    with pytest.raises(ValueError):
        a.get_groww_api()
    monkeypatch.setenv("GROWW_SDK_MIN_START_GAP_SECONDS", "0")
    monkeypatch.setenv("GROWW_SDK_MAX_CALLS_PER_CHILD", "not-a-number")
    with pytest.raises(ValueError):
        a.get_groww_api()


def test_configured_proxy_and_metadata_logging_never_print_secrets(monkeypatch,capsys):
    a=load_auth(monkeypatch)
    monkeypatch.setenv("GROWW_SDK_MIN_START_GAP_SECONDS", "0")
    monkeypatch.setenv("GROWW_SDK_MAX_CALLS_PER_CHILD", "2")
    monkeypatch.setenv("GROWW_SDK_CALL_TELEMETRY", "true")
    p=a.get_groww_api()
    p.get_historical_candles(secret="SUPER_PRIVATE_TOKEN", trading_symbol="AAA")
    out=capsys.readouterr().out
    assert "SUPER_PRIVATE_TOKEN" not in out and "local-test-token" not in out and "AAA" not in out
    assert "attempt=1" in out and "per_child_cap=2" in out
    assert "SUPER_PRIVATE_TOKEN" not in str(p.request_stats())


def test_rate_limit_exception_does_not_get_logged_as_a_request(monkeypatch):
    a=load_auth(monkeypatch)
    p=a.GuardedGrowwClient(FakeSDK("secret"),max_calls=1,log_calls=True)
    p.get_historical_candles()
    with pytest.raises(a.GrowwSDKCallLimitReached):p.get_historical_candles()
    assert p.request_stats()["attempted"] == 1
    assert FakeSDK.calls == ["candles"]