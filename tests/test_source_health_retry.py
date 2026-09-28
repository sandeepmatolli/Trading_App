from datetime import datetime
import sqlite3

from news.corporate_action_cache import (
    CorporateActionCache,
)
from news.event_cache import EventCache
import news.persistent_sources as persistent_sources
from news.retry_utils import (
    bounded_backoff_seconds,
)


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
        "entry_count": 1 if available else 0,
        "timestamped_entry_count": 1 if available else 0,
        "undated_entry_count": 0,
        "latest_item_at": (
            "2026-09-28T10:00:00+05:30"
            if available
            else None
        ),
        "errors": (
            []
            if available
            else ["temporary source failure"]
        ),
        "warnings": [],
    }


def _event_bundle(
    now,
    *,
    available=True,
    candidate_eligible=True,
):
    sources = [
        _source(
            "nse_announcements",
            available=available,
            candidate_eligible=candidate_eligible,
        ),
        _source(
            "nse_corporate_actions",
            available=available,
            candidate_eligible=candidate_eligible,
        ),
    ]

    return {
        "available": available,
        "candidate_eligible": candidate_eligible,
        "degraded": not candidate_eligible,
        "items": [],
        "sources": sources,
        "errors": (
            []
            if available
            else ["temporary source failure"]
        ),
        "warnings": [],
        "candidate_blockers": (
            []
            if candidate_eligible
            else ["source not candidate-grade"]
        ),
        "as_of": now.isoformat(),
        "event_lookback_days": 14,
        "source_max_stale_days": 7,
    }


def _guard():
    return {
        "detected": True,
        "events": [
            {
                "current_ts": "2024-10-28T00:00:00+05:30",
                "previous_ts": "2024-10-25T00:00:00+05:30",
                "open_ratio": 0.503445,
                "close_ratio": 0.502448,
                "matched_common_ratio": 0.5,
                "likely_corporate_action": True,
            }
        ],
    }


def _historical_result(
    *,
    complete,
):
    return {
        "requested": True,
        "available": bool(complete),
        "complete": bool(complete),
        "status": (
            "AVAILABLE_COMPLETE"
            if complete
            else "UNAVAILABLE"
        ),
        "symbol": "RELIANCE",
        "source_id": "nse_historical_corporate_actions",
        "source_url": "https://example.test/ca",
        "boundary_dates": ["2024-10-28"],
        "windows": [],
        "actions": [],
        "action_count": 0,
        "errors": (
            []
            if complete
            else ["temporary historical failure"]
        ),
        "warnings": [],
    }


def _install_clock(
    monkeypatch,
    now,
):
    monkeypatch.setattr(
        persistent_sources,
        "_wall_clock_ist",
        lambda: now,
    )
    monkeypatch.setattr(
        persistent_sources.time,
        "sleep",
        lambda seconds: None,
    )


def test_event_transient_failure_retries_then_succeeds(
    monkeypatch,
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    _install_clock(
        monkeypatch,
        now,
    )
    calls = []

    def fake_fetch(as_of=None):
        calls.append(True)
        if len(calls) == 1:
            return _event_bundle(
                now,
                available=False,
                candidate_eligible=False,
            )
        return _event_bundle(
            now,
            available=True,
            candidate_eligible=True,
        )

    monkeypatch.setattr(
        persistent_sources,
        "_fetch_nse_announcements_uncached",
        fake_fetch,
    )

    result = persistent_sources.fetch_nse_announcements(
        as_of=now,
        cache=EventCache(
            tmp_path / "cache.db"
        ),
        force_refresh=True,
    )

    assert len(calls) == 2
    assert result["available"] is True
    assert result["source_health"]["attempt_count"] == 2
    assert result["source_health"]["retried"] is True
    assert result["source_health"]["next_retry_after"] is None


def test_event_stale_but_available_source_does_not_retry(
    monkeypatch,
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    _install_clock(
        monkeypatch,
        now,
    )
    calls = []

    def fake_fetch(as_of=None):
        calls.append(True)
        return _event_bundle(
            now,
            available=True,
            candidate_eligible=False,
        )

    monkeypatch.setattr(
        persistent_sources,
        "_fetch_nse_announcements_uncached",
        fake_fetch,
    )

    result = persistent_sources.fetch_nse_announcements(
        as_of=now,
        cache=EventCache(
            tmp_path / "cache.db"
        ),
        force_refresh=True,
    )

    assert len(calls) == 1
    assert result["available"] is True
    assert result["candidate_eligible"] is False
    assert result["source_health"]["attempt_count"] == 1


def test_event_final_unavailable_sets_next_retry_after(
    monkeypatch,
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    _install_clock(
        monkeypatch,
        now,
    )
    calls = []

    def fake_fetch(as_of=None):
        calls.append(True)
        return _event_bundle(
            now,
            available=False,
            candidate_eligible=False,
        )

    monkeypatch.setattr(
        persistent_sources,
        "_fetch_nse_announcements_uncached",
        fake_fetch,
    )

    result = persistent_sources.fetch_nse_announcements(
        as_of=now,
        cache=EventCache(
            tmp_path / "cache.db"
        ),
        force_refresh=True,
    )

    assert len(calls) == 3
    assert result["candidate_eligible"] is False
    assert result["source_health"]["attempt_count"] == 3
    assert result["source_health"]["next_retry_after"] is not None


def test_historical_partial_retries_then_completes(
    monkeypatch,
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    _install_clock(
        monkeypatch,
        now,
    )
    calls = []

    def fake_fetch(symbol, guard, **kwargs):
        calls.append(True)
        return _historical_result(
            complete=len(calls) >= 2
        )

    monkeypatch.setattr(
        persistent_sources,
        "_fetch_historical_actions_uncached",
        fake_fetch,
    )

    result = (
        persistent_sources.fetch_historical_actions_for_discontinuities(
            "RELIANCE",
            _guard(),
            as_of=now,
            cache=CorporateActionCache(
                tmp_path / "cache.db"
            ),
            force_refresh=True,
        )
    )

    assert len(calls) == 2
    assert result["complete"] is True
    assert result["source_health"]["attempt_count"] == 2
    assert result["source_health"]["retried"] is True


def test_historical_final_incomplete_sets_next_retry_after(
    monkeypatch,
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T14:00:00+05:30"
    )
    _install_clock(
        monkeypatch,
        now,
    )
    calls = []

    def fake_fetch(symbol, guard, **kwargs):
        calls.append(True)
        return _historical_result(
            complete=False
        )

    monkeypatch.setattr(
        persistent_sources,
        "_fetch_historical_actions_uncached",
        fake_fetch,
    )

    result = (
        persistent_sources.fetch_historical_actions_for_discontinuities(
            "RELIANCE",
            _guard(),
            as_of=now,
            cache=CorporateActionCache(
                tmp_path / "cache.db"
            ),
            force_refresh=True,
        )
    )

    assert len(calls) == 3
    assert result["complete"] is False
    assert result["source_health"]["next_retry_after"] is not None


def test_bounded_backoff_is_deterministic_and_capped():
    assert bounded_backoff_seconds(
        1,
        base_seconds=0.5,
        max_seconds=2.0,
    ) == 0.5
    assert bounded_backoff_seconds(
        2,
        base_seconds=0.5,
        max_seconds=2.0,
    ) == 1.0
    assert bounded_backoff_seconds(
        3,
        base_seconds=0.5,
        max_seconds=2.0,
    ) == 2.0
    assert bounded_backoff_seconds(
        4,
        base_seconds=0.5,
        max_seconds=2.0,
    ) == 2.0


def test_event_cache_migrates_existing_source_state_schema(
    tmp_path,
):
    db_path = tmp_path / "event-old.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE event_source_state (
            source_id TEXT PRIMARY KEY,
            source_type TEXT,
            source_url TEXT,
            required_for_candidate INTEGER NOT NULL DEFAULT 0,
            reliability TEXT,
            last_attempt_at TEXT,
            last_success_at TEXT,
            latest_item_at TEXT,
            available INTEGER NOT NULL DEFAULT 0,
            candidate_eligible INTEGER NOT NULL DEFAULT 0,
            entry_count INTEGER NOT NULL DEFAULT 0,
            timestamped_entry_count INTEGER NOT NULL DEFAULT 0,
            undated_entry_count INTEGER NOT NULL DEFAULT 0,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            errors_json TEXT NOT NULL DEFAULT '[]',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    connection.close()

    EventCache(db_path)

    connection = sqlite3.connect(db_path)
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(event_source_state)"
        ).fetchall()
    }
    connection.close()

    assert "last_http_status" in columns
    assert "last_elapsed_ms" in columns
    assert "served_from_cache" in columns
    assert "cache_age_seconds" in columns


def test_corporate_action_cache_migrates_existing_query_schema(
    tmp_path,
):
    db_path = tmp_path / "ca-old.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE corporate_action_query_cache (
            query_key TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            boundary_dates_json TEXT NOT NULL,
            padding_days INTEGER NOT NULL,
            source_url TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            available INTEGER,
            complete INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            action_count INTEGER NOT NULL DEFAULT 0,
            result_json TEXT NOT NULL
        )
        """
    )
    connection.commit()
    connection.close()

    CorporateActionCache(db_path)

    connection = sqlite3.connect(db_path)
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(corporate_action_query_cache)"
        ).fetchall()
    }
    connection.close()

    assert "attempt_count" in columns
    assert "consecutive_failures" in columns
    assert "served_from_cache" in columns
    assert "cache_age_seconds" in columns