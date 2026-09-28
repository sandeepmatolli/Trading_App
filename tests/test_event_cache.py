from datetime import datetime, timedelta

from news.event_cache import EventCache
import news.persistent_sources as persistent_sources


def _source(
    source_id,
    *,
    available=True,
    candidate_eligible=True,
):
    return {
        "source_id": source_id,
        "source_type": "official_test",
        "url": f"https://example.test/{source_id}",
        "required_for_candidate": True,
        "reliability": "PrimaryExchange",
        "available": available,
        "candidate_eligible": candidate_eligible,
        "entry_count": 1,
        "timestamped_entry_count": 1,
        "undated_entry_count": 0,
        "latest_item_at": "2026-09-28T10:00:00+05:30",
        "errors": [],
        "warnings": [],
    }


def _bundle(now):
    shared_link = "https://example.test/filing/1"
    return {
        "available": True,
        "candidate_eligible": True,
        "degraded": False,
        "items": [
            {
                "source_id": "nse_announcements",
                "source_type": "official_nse_announcements",
                "source_url": "https://example.test/a",
                "reliability": "PrimaryExchange",
                "symbol_hint": "RELIANCE",
                "title": "Reliance Industries Limited",
                "summary": "General update",
                "link": shared_link,
                "published_at": "2026-09-28T10:00:00+05:30",
                "published_at_source": "published_parsed",
                "timestamp_verified": True,
                "corporate_action": None,
            },
            {
                "source_id": "nse_financial_results",
                "source_type": "official_nse_financial_results",
                "source_url": "https://example.test/r",
                "reliability": "PrimaryExchange",
                "symbol_hint": "RELIANCE",
                "title": "Reliance Industries Limited",
                "summary": "Financial results",
                "link": shared_link,
                "published_at": "2026-09-28T10:00:00+05:30",
                "published_at_source": "published_parsed",
                "timestamp_verified": True,
                "corporate_action": None,
            },
        ],
        "sources": [
            _source("nse_announcements"),
            _source("nse_corporate_actions"),
            _source("nse_financial_results"),
        ],
        "errors": [],
        "warnings": [],
        "candidate_blockers": [],
        "as_of": now.isoformat(),
        "event_lookback_days": 14,
        "source_max_stale_days": 7,
    }


def _install_clock(
    monkeypatch,
    now,
):
    clock = {"now": now}
    monkeypatch.setattr(
        persistent_sources,
        "_wall_clock_ist",
        lambda: clock["now"],
    )
    monkeypatch.setattr(
        persistent_sources.time,
        "sleep",
        lambda seconds: None,
    )
    return clock


def test_first_fetch_then_fresh_cache_hit(
    monkeypatch,
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    clock = _install_clock(
        monkeypatch,
        now,
    )
    cache = EventCache(
        tmp_path / "cache.db"
    )
    calls = []

    def fake_fetch(as_of=None):
        calls.append(True)
        return _bundle(now)

    monkeypatch.setattr(
        persistent_sources,
        "_fetch_nse_announcements_uncached",
        fake_fetch,
    )

    first = persistent_sources.fetch_nse_announcements(
        as_of=now,
        cache=cache,
        refresh_minutes=15,
    )

    clock["now"] = now + timedelta(minutes=5)
    second = persistent_sources.fetch_nse_announcements(
        as_of=now + timedelta(minutes=5),
        cache=cache,
        refresh_minutes=15,
    )

    assert len(calls) == 1
    assert first["cache"]["hit"] is False
    assert first["cache"]["refreshed"] is True
    assert first["source_health"]["attempt_count"] == 1
    assert second["cache"]["hit"] is True
    assert second["cache"]["refreshed"] is False
    assert second["source_health"]["served_from_cache"] is True
    assert second["source_health"]["attempt_count"] == 0
    assert second["source_health"]["origin_attempt_count"] == 1


def test_force_refresh_bypasses_fresh_event_cache(
    monkeypatch,
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    clock = _install_clock(
        monkeypatch,
        now,
    )
    cache = EventCache(
        tmp_path / "cache.db"
    )
    calls = []

    def fake_fetch(as_of=None):
        calls.append(True)
        return _bundle(now)

    monkeypatch.setattr(
        persistent_sources,
        "_fetch_nse_announcements_uncached",
        fake_fetch,
    )

    persistent_sources.fetch_nse_announcements(
        as_of=now,
        cache=cache,
        refresh_minutes=15,
    )

    clock["now"] = now + timedelta(minutes=1)
    refreshed = persistent_sources.fetch_nse_announcements(
        as_of=now + timedelta(minutes=1),
        cache=cache,
        refresh_minutes=15,
        force_refresh=True,
    )

    assert len(calls) == 2
    assert refreshed["cache"]["hit"] is False
    assert refreshed["cache"]["refreshed"] is True


def test_event_cache_preserves_cross_source_provenance(
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    cache = EventCache(
        tmp_path / "cache.db"
    )

    cache.store_bundle(
        _bundle(now),
        retrieved_at=now,
    )
    cache.store_bundle(
        _bundle(now),
        retrieved_at=now + timedelta(minutes=1),
    )

    assert cache.count_items() == 2


def test_event_cache_does_not_use_future_retrieval_for_earlier_as_of(
    tmp_path,
):
    retrieved = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    cache = EventCache(
        tmp_path / "cache.db"
    )
    cache.store_bundle(
        _bundle(retrieved),
        retrieved_at=retrieved,
    )

    result = cache.load_fresh_bundle(
        now=retrieved + timedelta(minutes=1),
        max_age_minutes=9999,
        not_after=retrieved - timedelta(days=1),
    )

    assert result is None


def test_stale_event_fallback_is_fail_closed(
    monkeypatch,
    tmp_path,
):
    old = datetime.fromisoformat(
        "2026-09-28T10:00:00+05:30"
    )
    now = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    _install_clock(
        monkeypatch,
        now,
    )
    cache = EventCache(
        tmp_path / "cache.db"
    )
    cache.store_bundle(
        _bundle(old),
        retrieved_at=old,
    )

    def fail_fetch(as_of=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        persistent_sources,
        "_fetch_nse_announcements_uncached",
        fail_fetch,
    )

    result = persistent_sources.fetch_nse_announcements(
        as_of=now,
        cache=cache,
        refresh_minutes=1,
    )

    assert result["cache"]["stale_fallback"] is True
    assert result["candidate_eligible"] is False
    assert result["candidate_blockers"]
    assert result["errors"]
    assert result["source_health"]["served_from_cache"] is True
    assert result["source_health"]["attempt_count"] == 3
    assert result["source_health"]["retried"] is True
    assert result["source_health"]["last_error"] == "network down"


def test_event_source_state_persists_health_telemetry(
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    cache = EventCache(
        tmp_path / "cache.db"
    )
    bundle = _bundle(now)
    bundle["source_health"] = {
        "served_from_cache": False,
        "cache_age_seconds": 0.0,
        "attempt_count": 2,
        "total_elapsed_ms": 123.4,
        "last_attempt_at": now.isoformat(),
        "last_http_status": 503,
        "last_error": "temporary failure",
        "last_warning": "retry succeeded",
        "next_retry_after": None,
    }

    cache.store_bundle(
        bundle,
        retrieved_at=now,
    )

    state = cache.get_source_state(
        "nse_announcements"
    )

    assert state is not None
    assert state["last_elapsed_ms"] == 123.4
    assert state["last_http_status"] == 503
    assert state["served_from_cache"] is False