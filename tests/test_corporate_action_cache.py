from datetime import datetime, timedelta

from news.corporate_action_cache import (
    CorporateActionCache,
)
import news.persistent_sources as persistent_sources


def _guard(
    current_ts="2024-10-28T00:00:00+05:30",
):
    return {
        "detected": True,
        "events": [
            {
                "current_ts": current_ts,
                "previous_ts": "2024-10-25T00:00:00+05:30",
                "open_ratio": 0.503445,
                "close_ratio": 0.502448,
                "matched_common_ratio": 0.5,
                "likely_corporate_action": True,
            }
        ],
    }


def _complete_result(
    *,
    actions=None,
):
    actions = list(
        actions
        if actions is not None
        else [
            {
                "symbol": "RELIANCE",
                "purpose": "Bonus 1:1",
                "title": "Bonus 1:1",
                "action_type": "bonus",
                "ratio": "1:1",
                "theoretical_price_factor": 0.5,
                "ex_date": "2024-10-28",
                "record_date": "2024-10-28",
                "source_id": "nse_historical_corporate_actions",
                "source_url": "https://example.test/ca",
            }
        ]
    )

    return {
        "requested": True,
        "available": True,
        "complete": True,
        "status": "AVAILABLE_COMPLETE",
        "symbol": "RELIANCE",
        "source_id": "nse_historical_corporate_actions",
        "source_url": "https://example.test/ca",
        "boundary_dates": ["2024-10-28"],
        "windows": [],
        "actions": actions,
        "action_count": len(actions),
        "errors": [],
        "warnings": [],
        "retrieved_at": "2026-09-28T14:00:00+05:30",
    }


def test_complete_historical_query_is_reused(
    monkeypatch,
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    cache = CorporateActionCache(
        tmp_path / "cache.db"
    )
    calls = []

    def fake_fetch(symbol, guard, **kwargs):
        calls.append(True)
        return _complete_result()

    monkeypatch.setattr(
        persistent_sources,
        "_fetch_historical_actions_uncached",
        fake_fetch,
    )

    first = (
        persistent_sources.fetch_historical_actions_for_discontinuities(
            "RELIANCE",
            _guard(),
            as_of=now,
            cache=cache,
            refresh_days=30,
        )
    )
    second = (
        persistent_sources.fetch_historical_actions_for_discontinuities(
            "RELIANCE",
            _guard(),
            as_of=now + timedelta(days=1),
            cache=cache,
            refresh_days=30,
        )
    )

    assert len(calls) == 1
    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert second["cache"][
        "complete_result_reused"
    ] is True


def test_complete_zero_action_query_is_reused(
    monkeypatch,
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    cache = CorporateActionCache(
        tmp_path / "cache.db"
    )
    calls = []

    def fake_fetch(symbol, guard, **kwargs):
        calls.append(True)
        return _complete_result(
            actions=[]
        )

    monkeypatch.setattr(
        persistent_sources,
        "_fetch_historical_actions_uncached",
        fake_fetch,
    )

    first = (
        persistent_sources.fetch_historical_actions_for_discontinuities(
            "RELIANCE",
            _guard(),
            as_of=now,
            cache=cache,
            refresh_days=30,
        )
    )
    second = (
        persistent_sources.fetch_historical_actions_for_discontinuities(
            "RELIANCE",
            _guard(),
            as_of=now + timedelta(days=1),
            cache=cache,
            refresh_days=30,
        )
    )

    assert first["complete"] is True
    assert first["action_count"] == 0
    assert second["action_count"] == 0
    assert len(calls) == 1


def test_incomplete_historical_query_is_never_reused(
    monkeypatch,
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    cache = CorporateActionCache(
        tmp_path / "cache.db"
    )
    calls = []

    def fake_fetch(symbol, guard, **kwargs):
        calls.append(True)
        return {
            "requested": True,
            "available": False,
            "complete": False,
            "status": "UNAVAILABLE",
            "symbol": symbol,
            "source_id": "nse_historical_corporate_actions",
            "source_url": "https://example.test/ca",
            "boundary_dates": ["2024-10-28"],
            "windows": [],
            "actions": [],
            "action_count": 0,
            "errors": ["failed"],
            "warnings": [],
        }

    monkeypatch.setattr(
        persistent_sources,
        "_fetch_historical_actions_uncached",
        fake_fetch,
    )

    persistent_sources.fetch_historical_actions_for_discontinuities(
        "RELIANCE",
        _guard(),
        as_of=now,
        cache=cache,
        refresh_days=30,
    )
    second = (
        persistent_sources.fetch_historical_actions_for_discontinuities(
            "RELIANCE",
            _guard(),
            as_of=now + timedelta(minutes=1),
            cache=cache,
            refresh_days=30,
        )
    )

    assert len(calls) == 2
    assert second["cache"]["hit"] is False


def test_historical_cache_expiry_triggers_refresh(
    monkeypatch,
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    cache = CorporateActionCache(
        tmp_path / "cache.db"
    )
    calls = []

    def fake_fetch(symbol, guard, **kwargs):
        calls.append(True)
        return _complete_result()

    monkeypatch.setattr(
        persistent_sources,
        "_fetch_historical_actions_uncached",
        fake_fetch,
    )

    persistent_sources.fetch_historical_actions_for_discontinuities(
        "RELIANCE",
        _guard(),
        as_of=now,
        cache=cache,
        refresh_days=1,
    )
    refreshed = (
        persistent_sources.fetch_historical_actions_for_discontinuities(
            "RELIANCE",
            _guard(),
            as_of=now + timedelta(days=2),
            cache=cache,
            refresh_days=1,
        )
    )

    assert len(calls) == 2
    assert refreshed["cache"]["refreshed"] is True


def test_different_boundary_uses_different_cache_key(
    monkeypatch,
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    cache = CorporateActionCache(
        tmp_path / "cache.db"
    )
    calls = []

    def fake_fetch(symbol, guard, **kwargs):
        calls.append(True)
        return _complete_result()

    monkeypatch.setattr(
        persistent_sources,
        "_fetch_historical_actions_uncached",
        fake_fetch,
    )

    first = (
        persistent_sources.fetch_historical_actions_for_discontinuities(
            "RELIANCE",
            _guard("2024-10-28T00:00:00+05:30"),
            as_of=now,
            cache=cache,
            refresh_days=30,
        )
    )
    second = (
        persistent_sources.fetch_historical_actions_for_discontinuities(
            "RELIANCE",
            _guard("2025-01-01T00:00:00+05:30"),
            as_of=now,
            cache=cache,
            refresh_days=30,
        )
    )

    assert len(calls) == 2
    assert first["cache"]["query_key"] != second["cache"]["query_key"]