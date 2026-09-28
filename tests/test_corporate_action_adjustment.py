import pandas as pd

from market_data.corporate_action_adjustment import (
    apply_confirmed_price_adjustments,
    build_adjusted_market_views,
)


def _price_frame(
    dates,
    values,
):
    return pd.DataFrame(
        {
            "ts": pd.to_datetime(dates),
            "open": values,
            "high": [
                value + 2
                for value in values
            ],
            "low": [
                value - 2
                for value in values
            ],
            "close": [
                value + 1
                for value in values
            ],
            "volume": [1000] * len(values),
            "open_interest": [None] * len(values),
        }
    )


def _confirmed_reconciliation():
    return {
        "status": "CONFIRMED",
        "matches": [
            {
                "status": "CONFIRMED",
                "confidence": "HIGH",
                "official_factor": 0.5,
                "ex_date": "2024-10-28",
                "record_date": "2024-10-28",
                "source_id": (
                    "nse_historical_corporate_actions"
                ),
                "source_type": (
                    "official_nse_historical_"
                    "corporate_actions_web_api"
                ),
                "action_type": "bonus",
                "ratio": "1:1",
                "title": "Bonus 1:1",
                "boundary_ts": (
                    "2024-10-28T00:00:00+05:30"
                ),
            }
        ],
    }


def test_adjustment_is_strictly_before_ex_date():
    frame = _price_frame(
        [
            "2024-10-25 09:00:00+05:30",
            "2024-10-28 09:00:00+05:30",
            "2024-10-29 09:00:00+05:30",
        ],
        [200.0, 100.0, 102.0],
    )

    adjusted, meta = (
        apply_confirmed_price_adjustments(
            frame,
            [
                {
                    "ex_date": "2024-10-28",
                    "factor": 0.5,
                }
            ],
        )
    )

    assert adjusted.loc[0, "open"] == 100.0
    assert adjusted.loc[1, "open"] == 100.0
    assert adjusted.loc[2, "open"] == 102.0
    assert adjusted.loc[0, "price_adjustment_factor"] == 0.5
    assert adjusted.loc[1, "price_adjustment_factor"] == 1.0
    assert meta["adjusted_rows"] == 1


def test_raw_frame_is_never_mutated():
    frame = _price_frame(
        [
            "2024-10-25 09:00:00+05:30",
            "2024-10-28 09:00:00+05:30",
        ],
        [200.0, 100.0],
    )
    original = frame.copy(deep=True)

    adjusted, _ = (
        apply_confirmed_price_adjustments(
            frame,
            [
                {
                    "ex_date": "2024-10-28",
                    "factor": 0.5,
                }
            ],
        )
    )

    pd.testing.assert_frame_equal(
        frame,
        original,
    )
    assert adjusted.loc[0, "open"] == 100.0
    assert frame.loc[0, "open"] == 200.0


def test_multiple_confirmed_actions_compound():
    frame = _price_frame(
        [
            "2024-01-01",
            "2024-07-01",
            "2025-01-01",
        ],
        [400.0, 200.0, 100.0],
    )

    adjusted, meta = (
        apply_confirmed_price_adjustments(
            frame,
            [
                {
                    "ex_date": "2024-06-01",
                    "factor": 0.5,
                },
                {
                    "ex_date": "2024-12-01",
                    "factor": 0.5,
                },
            ],
        )
    )

    assert adjusted.loc[0, "open"] == 100.0
    assert adjusted.loc[1, "open"] == 100.0
    assert adjusted.loc[2, "open"] == 100.0
    assert meta["events_applied"] == 2


def test_volume_is_not_modified():
    frame = _price_frame(
        [
            "2024-10-25",
            "2024-10-28",
        ],
        [200.0, 100.0],
    )

    adjusted, _ = (
        apply_confirmed_price_adjustments(
            frame,
            [
                {
                    "ex_date": "2024-10-28",
                    "factor": 0.5,
                }
            ],
        )
    )

    assert list(adjusted["volume"]) == [
        1000,
        1000,
    ]


def test_unconfirmed_reconciliation_does_not_adjust_market():
    hourly_full = _price_frame(
        [
            "2024-10-25 09:00:00+05:30",
            "2024-10-28 09:00:00+05:30",
        ],
        [200.0, 100.0],
    )

    market = {
        "hourly_full": hourly_full,
        "hourly": hourly_full.copy(),
        "fifteen_minute": pd.DataFrame(),
        "daily": pd.DataFrame(),
        "weekly": pd.DataFrame(),
        "monthly": pd.DataFrame(),
        "setup_75m": pd.DataFrame(),
        "quality": {
            "valid": True,
        },
    }

    result = build_adjusted_market_views(
        market,
        {
            "status": "PROBABLE",
            "matches": [],
        },
    )

    assert result["applied"] is False
    pd.testing.assert_frame_equal(
        result["hourly_full"],
        hourly_full,
    )


def test_confirmed_bonus_rebuilds_daily_without_price_jump():
    rows = []

    for date_text, base in (
        ("2024-10-25", 200.0),
        ("2024-10-28", 100.0),
    ):
        for hour in (
            9,
            10,
            11,
            12,
            13,
            14,
        ):
            ts = pd.Timestamp(
                f"{date_text} {hour:02d}:00:00",
                tz="Asia/Kolkata",
            )
            rows.append(
                {
                    "ts": ts,
                    "open": base,
                    "high": base + 2,
                    "low": base - 2,
                    "close": base + 1,
                    "volume": 1000,
                    "open_interest": None,
                }
            )

    hourly_full = pd.DataFrame(rows)

    market = {
        "hourly_full": hourly_full,
        "hourly": hourly_full.copy(),
        "fifteen_minute": pd.DataFrame(),
        "daily": pd.DataFrame(),
        "weekly": pd.DataFrame(),
        "monthly": pd.DataFrame(),
        "setup_75m": pd.DataFrame(),
        "quality": {
            "valid": True,
            "candidate_eligible": True,
        },
    }

    result = build_adjusted_market_views(
        market,
        _confirmed_reconciliation(),
    )

    assert result["applied"] is True
    daily = result["daily"]

    assert len(daily) == 2
    assert daily.iloc[0]["open"] == 100.0
    assert daily.iloc[1]["open"] == 100.0
    assert result["metadata"][
        "raw_provider_data_preserved"
    ] is True
    assert result["metadata"][
        "volume_adjusted"
    ] is False


def test_confirmed_match_without_ex_date_is_not_applied():
    reconciliation = _confirmed_reconciliation()
    reconciliation["matches"][0]["ex_date"] = None

    frame = _price_frame(
        [
            "2024-10-25",
            "2024-10-28",
        ],
        [200.0, 100.0],
    )

    market = {
        "hourly_full": frame,
        "hourly": frame.copy(),
        "fifteen_minute": pd.DataFrame(),
        "daily": pd.DataFrame(),
        "weekly": pd.DataFrame(),
        "monthly": pd.DataFrame(),
        "setup_75m": pd.DataFrame(),
        "quality": {},
    }

    result = build_adjusted_market_views(
        market,
        reconciliation,
    )

    assert result["applied"] is False
    assert result["metadata"]["warnings"]
