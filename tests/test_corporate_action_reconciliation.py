from news.corporate_action_reconciliation import (
    extract_labeled_action_dates,
    reconcile_corporate_action_guard,
)


def _guard():
    return {
        "detected": True,
        "events": [
            {
                "previous_ts": "2026-09-24 00:00:00+05:30",
                "current_ts": "2026-09-25 00:00:00+05:30",
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
    title="Bonus 1:1 - Record Date 25-Sep-2026",
    published_at="2026-09-20T10:00:00+05:30",
):
    return {
        "available": True,
        "candidate_eligible": True,
        "corporate_actions": [
            {
                "title": title,
                "summary": "",
                "published_at": published_at,
                "source_id": source_id,
                "link": "https://example.test/action",
                "action_type": "bonus",
                "ratio": "1:1",
                "theoretical_price_factor": factor,
            }
        ],
    }


def test_extract_labeled_action_dates():
    result = extract_labeled_action_dates(
        "Bonus 1:1 - Record Date 25-Sep-2026",
        "Ex-Date: 24/09/2026",
    )

    dates = {
        item["date"]
        for item in result
    }

    assert "2026-09-25" in dates
    assert "2026-09-24" in dates


def test_exact_factor_and_labelled_date_from_ca_feed_confirms():
    result = reconcile_corporate_action_guard(
        "EXAMPLE",
        _guard(),
        _event_profile(),
    )

    assert result["status"] == "CONFIRMED"
    assert result["confirmed_count"] == 1
    assert result["probable_count"] == 0
    assert result["changes_price_history"] is False
    assert result["smc_guard_preserved"] is True

    match = result["matches"][0]
    assert match["factor_compatible"] is True
    assert match["labelled_action_date_compatible"] is True
    assert match["source_id"] == "nse_corporate_actions"


def test_announcement_source_with_labelled_date_is_probable_not_confirmed():
    result = reconcile_corporate_action_guard(
        "EXAMPLE",
        _guard(),
        _event_profile(
            source_id="nse_announcements"
        ),
    )

    assert result["status"] == "PROBABLE"
    assert result["confirmed_count"] == 0
    assert result["probable_count"] == 1


def test_factor_match_and_near_publication_without_labelled_date_is_probable():
    result = reconcile_corporate_action_guard(
        "EXAMPLE",
        _guard(),
        _event_profile(
            title="Bonus 1:1",
            published_at="2026-09-20T10:00:00+05:30",
        ),
    )

    assert result["status"] == "PROBABLE"
    assert result["probable_count"] == 1
    assert (
        result["matches"][0][
            "labelled_action_date_compatible"
        ]
        is False
    )
    assert (
        result["matches"][0][
            "publication_compatible"
        ]
        is True
    )


def test_wrong_factor_stays_unverified():
    result = reconcile_corporate_action_guard(
        "EXAMPLE",
        _guard(),
        _event_profile(
            factor=0.2,
        ),
    )

    assert result["status"] == "UNVERIFIED_NO_COMPATIBLE_MATCH"
    assert result["confirmed_count"] == 0
    assert result["probable_count"] == 0
    assert result["unverified_count"] == 1


def test_no_official_factor_match_does_not_disprove_corporate_action():
    result = reconcile_corporate_action_guard(
        "RELIANCE",
        _guard(),
        {
            "available": True,
            "candidate_eligible": True,
            "corporate_actions": [],
        },
    )

    assert (
        result["status"]
        == "UNVERIFIED_NO_OFFICIAL_MATCH_IN_CURRENT_FEED"
    )
    assert result["unverified_count"] == 1
    assert "not proof" in result["feed_window_limitation"]


def test_no_discontinuity_needs_no_confirmation():
    result = reconcile_corporate_action_guard(
        "EXAMPLE",
        {
            "detected": False,
            "events": [],
        },
        _event_profile(),
    )

    assert result["status"] == "NO_DISCONTINUITY"
    assert result["confirmed_count"] == 0
    assert result["unverified_count"] == 0


def test_dividend_without_factor_is_not_used_as_split_bonus_confirmation():
    result = reconcile_corporate_action_guard(
        "EXAMPLE",
        _guard(),
        {
            "available": True,
            "candidate_eligible": True,
            "corporate_actions": [
                {
                    "title": "Dividend Record Date 25-Sep-2026",
                    "summary": "",
                    "published_at": "2026-09-20T10:00:00+05:30",
                    "source_id": "nse_corporate_actions",
                    "action_type": "dividend",
                    "theoretical_price_factor": None,
                }
            ],
        },
    )

    assert (
        result["status"]
        == "UNVERIFIED_NO_OFFICIAL_MATCH_IN_CURRENT_FEED"
    )
