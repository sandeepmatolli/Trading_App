from news.corporate_action_reconciliation import (
    extract_labeled_action_dates,
    reconcile_corporate_action_guard,
)


def _guard():
    return {
        "detected": True,
        "events": [
            {
                "previous_ts": (
                    "2026-09-24 00:00:00+05:30"
                ),
                "current_ts": (
                    "2026-09-25 00:00:00+05:30"
                ),
                "open_ratio": 0.503,
                "close_ratio": 0.501,
                "matched_common_ratio": 0.5,
                "likely_corporate_action": True,
            }
        ],
    }


def _event_profile(
    *,
    source_id="nse_corporate_actions",
    factor=0.5,
    title=(
        "Bonus 1:1 - Record Date 25-Sep-2026"
    ),
    published_at="2026-09-20T10:00:00+05:30",
):
    return {
        "available": True,
        "candidate_eligible": True,
        "corporate_actions": [
            {
                "title": title,
                "summary": "",
                "published_at": (
                    published_at
                ),
                "source_id": source_id,
                "link": (
                    "https://example.test/action"
                ),
                "action_type": "bonus",
                "ratio": "1:1",
                "theoretical_price_factor": (
                    factor
                ),
            }
        ],
    }


def _historical_result(
    *,
    factor=0.5,
    ex_date="2026-09-25",
    record_date="2026-09-25",
    complete=True,
    available=True,
):
    return {
        "requested": True,
        "available": available,
        "complete": complete,
        "status": (
            "AVAILABLE_COMPLETE"
            if complete
            else "UNAVAILABLE"
        ),
        "errors": (
            []
            if complete
            else ["source unavailable"]
        ),
        "warnings": [],
        "actions": (
            [
                {
                    "symbol": "EXAMPLE",
                    "title": "Bonus 1:1",
                    "purpose": "Bonus 1:1",
                    "summary": "",
                    "source_id": (
                        "nse_historical_corporate_actions"
                    ),
                    "source_type": (
                        "official_nse_historical_"
                        "corporate_actions_web_api"
                    ),
                    "action_type": "bonus",
                    "ratio": "1:1",
                    "theoretical_price_factor": (
                        factor
                    ),
                    "ex_date": ex_date,
                    "record_date": record_date,
                    "action_dates": [
                        {
                            "label": "ex date",
                            "date": ex_date,
                        },
                        {
                            "label": "record date",
                            "date": record_date,
                        },
                    ],
                }
            ]
            if available
            else []
        ),
    }


def test_extract_labeled_action_dates():
    result = (
        extract_labeled_action_dates(
            (
                "Bonus 1:1 - "
                "Record Date 25-Sep-2026"
            ),
            "Ex-Date: 24/09/2026",
        )
    )

    dates = {
        item["date"]
        for item in result
    }

    assert "2026-09-25" in dates
    assert "2026-09-24" in dates


def test_current_ca_feed_can_confirm():
    result = (
        reconcile_corporate_action_guard(
            "EXAMPLE",
            _guard(),
            _event_profile(),
        )
    )

    assert result["status"] == "CONFIRMED"
    assert result["confirmed_count"] == 1


def test_announcement_source_is_probable():
    result = (
        reconcile_corporate_action_guard(
            "EXAMPLE",
            _guard(),
            _event_profile(
                source_id=(
                    "nse_announcements"
                )
            ),
        )
    )

    assert result["status"] == "PROBABLE"
    assert result["probable_count"] == 1


def test_publication_only_is_probable():
    result = (
        reconcile_corporate_action_guard(
            "EXAMPLE",
            _guard(),
            _event_profile(
                title="Bonus 1:1",
            ),
        )
    )

    assert result["status"] == "PROBABLE"
    assert result["probable_count"] == 1


def test_wrong_factor_stays_unverified():
    result = (
        reconcile_corporate_action_guard(
            "EXAMPLE",
            _guard(),
            _event_profile(
                factor=0.2,
            ),
        )
    )

    assert result["status"] == (
        "UNVERIFIED_NO_COMPATIBLE_MATCH"
    )
    assert result["unverified_count"] == 1


def test_no_discontinuity_needs_no_confirmation():
    result = (
        reconcile_corporate_action_guard(
            "EXAMPLE",
            {
                "detected": False,
                "events": [],
            },
            _event_profile(),
        )
    )

    assert result["status"] == (
        "NO_DISCONTINUITY"
    )


def test_historical_official_action_confirms_old_boundary():
    result = (
        reconcile_corporate_action_guard(
            "EXAMPLE",
            _guard(),
            {
                "available": True,
                "candidate_eligible": True,
                "corporate_actions": [],
            },
            historical_action_result=(
                _historical_result()
            ),
        )
    )

    assert result["status"] == "CONFIRMED"
    assert result["confirmed_count"] == 1
    assert (
        result[
            "historical_action_candidate_count"
        ]
        == 1
    )

    match = result["matches"][0]

    assert match["source_id"] == (
        "nse_historical_corporate_actions"
    )
    assert match[
        "factor_compatible"
    ] is True
    assert match[
        "labelled_action_date_compatible"
    ] is True


def test_historical_source_failure_does_not_become_no_action():
    result = (
        reconcile_corporate_action_guard(
            "EXAMPLE",
            _guard(),
            {
                "available": True,
                "candidate_eligible": True,
                "corporate_actions": [],
            },
            historical_action_result={
                "requested": True,
                "available": False,
                "complete": False,
                "status": "UNAVAILABLE",
                "actions": [],
                "errors": [
                    "endpoint failed"
                ],
                "warnings": [],
            },
        )
    )

    assert result["status"] == (
        "UNVERIFIED_HISTORICAL_SOURCE_UNAVAILABLE"
    )
    assert result["unverified_count"] == 1
    assert result[
        "smc_guard_preserved"
    ] is True


def test_complete_historical_search_with_no_factor_is_unverified():
    result = (
        reconcile_corporate_action_guard(
            "EXAMPLE",
            _guard(),
            {
                "available": True,
                "candidate_eligible": True,
                "corporate_actions": [],
            },
            historical_action_result={
                "requested": True,
                "available": True,
                "complete": True,
                "status": "AVAILABLE_COMPLETE",
                "actions": [
                    {
                        "symbol": "EXAMPLE",
                        "source_id": (
                            "nse_historical_corporate_actions"
                        ),
                        "title": (
                            "Dividend - Rs 5 Per Share"
                        ),
                        "purpose": (
                            "Dividend - Rs 5 Per Share"
                        ),
                        "ex_date": "2026-09-25",
                        "record_date": "2026-09-25",
                    }
                ],
                "errors": [],
                "warnings": [],
            },
        )
    )

    assert result["status"] == (
        "UNVERIFIED_NO_OFFICIAL_MATCH"
    )
    assert result["unverified_count"] == 1


def test_dividend_without_factor_not_used_as_confirmation():
    result = (
        reconcile_corporate_action_guard(
            "EXAMPLE",
            _guard(),
            {
                "available": True,
                "candidate_eligible": True,
                "corporate_actions": [
                    {
                        "title": (
                            "Dividend Record Date "
                            "25-Sep-2026"
                        ),
                        "summary": "",
                        "published_at": (
                            "2026-09-20T10:00:00+05:30"
                        ),
                        "source_id": (
                            "nse_corporate_actions"
                        ),
                        "action_type": "dividend",
                        "theoretical_price_factor": None,
                    }
                ],
            },
        )
    )

    assert result["status"] == (
        "UNVERIFIED_NO_OFFICIAL_MATCH"
    )
