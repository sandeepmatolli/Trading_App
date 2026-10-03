from datetime import datetime

from news.event_cache import EventCache


def _source(
    source_id,
    *,
    warnings=None,
    errors=None,
):
    warnings = list(
        warnings
        if warnings is not None
        else []
    )
    errors = list(
        errors
        if errors is not None
        else []
    )

    return {
        "source_id": source_id,
        "source_type": "official_test",
        "url": f"https://example.test/{source_id}",
        "required_for_candidate": True,
        "reliability": "PrimaryExchange",
        "available": not bool(errors),
        "candidate_eligible": not bool(errors),
        "entry_count": 1 if not errors else 0,
        "timestamped_entry_count": 1 if not errors else 0,
        "undated_entry_count": 0,
        "latest_item_at": "2026-09-28T10:00:00+05:30",
        "errors": errors,
        "warnings": warnings,
    }


def _bundle(now):
    stale_warning = (
        "Latest source timestamp is 35.0 day(s) old; "
        "candidate freshness maximum is 7 day(s)."
    )

    return {
        "available": True,
        "candidate_eligible": True,
        "degraded": True,
        "items": [],
        "sources": [
            _source(
                "nse_announcements"
            ),
            _source(
                "nse_corporate_actions"
            ),
            _source(
                "nse_financial_results",
                warnings=[stale_warning],
            ),
            _source(
                "nse_board_meetings"
            ),
        ],
        "errors": [],
        "warnings": [
            "nse_financial_results: "
            + stale_warning
        ],
        "candidate_blockers": [],
        "as_of": now.isoformat(),
        "event_lookback_days": 14,
        "source_max_stale_days": 7,
        "source_health": {
            "served_from_cache": False,
            "cache_age_seconds": 0.0,
            "attempt_count": 1,
            "total_elapsed_ms": 123.0,
            "last_attempt_at": now.isoformat(),
            "last_http_status": None,
            "last_error": None,
            "last_warning": (
                "nse_financial_results: "
                + stale_warning
            ),
            "next_retry_after": None,
        },
    }


def test_bundle_warning_is_not_copied_to_unrelated_sources(
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T16:31:10+05:30"
    )

    cache = EventCache(
        tmp_path / "cache.db"
    )

    cache.store_bundle(
        _bundle(now),
        retrieved_at=now,
    )

    announcements = cache.get_source_state(
        "nse_announcements"
    )
    corporate_actions = cache.get_source_state(
        "nse_corporate_actions"
    )
    financial_results = cache.get_source_state(
        "nse_financial_results"
    )
    board_meetings = cache.get_source_state(
        "nse_board_meetings"
    )

    assert announcements["last_warning"] is None
    assert corporate_actions["last_warning"] is None
    assert board_meetings["last_warning"] is None

    assert financial_results["last_warning"] == (
        "Latest source timestamp is 35.0 day(s) old; "
        "candidate freshness maximum is 7 day(s)."
    )


def test_bundle_error_is_not_copied_to_healthy_source(
    tmp_path,
):
    now = datetime.fromisoformat(
        "2026-09-28T16:31:10+05:30"
    )

    bundle = _bundle(now)

    bundle["sources"][1] = _source(
        "nse_corporate_actions",
        errors=["temporary source failure"],
    )
    bundle["errors"] = [
        "nse_corporate_actions: temporary source failure"
    ]
    bundle["source_health"][
        "last_error"
    ] = (
        "nse_corporate_actions: "
        "temporary source failure"
    )

    cache = EventCache(
        tmp_path / "cache.db"
    )

    cache.store_bundle(
        bundle,
        retrieved_at=now,
    )

    announcements = cache.get_source_state(
        "nse_announcements"
    )
    corporate_actions = cache.get_source_state(
        "nse_corporate_actions"
    )
    board_meetings = cache.get_source_state(
        "nse_board_meetings"
    )

    assert announcements["last_error"] is None
    assert board_meetings["last_error"] is None
    assert corporate_actions["last_error"] == (
        "temporary source failure"
    )