import pandas as pd

from technical.smc_engine import (
    build_technical_profile,
    detect_liquidity_sweep,
    detect_price_discontinuities,
    filter_active_pois,
    find_fair_value_gaps,
    find_order_blocks,
)


def test_bullish_fvg_uses_three_candle_definition():
    df = pd.DataFrame(
        {
            "open": [100, 104, 111],
            "high": [105, 112, 116],
            "low": [99, 103, 108],
            "close": [104, 111, 115],
        }
    )

    gaps = find_fair_value_gaps(df)

    bullish = [item for item in gaps if item["direction"] == "bullish"]
    assert len(bullish) == 1
    assert bullish[0]["low"] == 105.0
    assert bullish[0]["high"] == 108.0


def test_no_false_adjacent_candle_fvg():
    df = pd.DataFrame(
        {
            "open": [100, 108, 103],
            "high": [105, 110, 106],
            "low": [99, 107, 102],
            "close": [104, 109, 105],
        }
    )

    gaps = find_fair_value_gaps(df)

    assert gaps == []


def test_75m_fvg_does_not_cross_missing_time_window():
    df = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2026-09-25 09:15:00+05:30",
                    "2026-09-25 10:30:00+05:30",
                    # 11:45 window is missing; next row jumps to 13:00.
                    "2026-09-25 13:00:00+05:30",
                ]
            ),
            "open": [100, 104, 111],
            "high": [105, 112, 116],
            "low": [99, 103, 108],
            "close": [104, 111, 115],
            "expected_interval_minutes": [75, 75, 75],
        }
    )

    gaps = find_fair_value_gaps(df)

    assert gaps == []


def test_75m_fvg_is_allowed_when_three_bars_are_contiguous():
    df = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2026-09-25 09:15:00+05:30",
                    "2026-09-25 10:30:00+05:30",
                    "2026-09-25 11:45:00+05:30",
                ]
            ),
            "open": [100, 104, 111],
            "high": [105, 112, 116],
            "low": [99, 103, 108],
            "close": [104, 111, 115],
            "expected_interval_minutes": [75, 75, 75],
        }
    )

    gaps = find_fair_value_gaps(df)

    bullish = [item for item in gaps if item["direction"] == "bullish"]
    assert len(bullish) == 1


def test_bullish_liquidity_sweep():
    df = pd.DataFrame(
        {
            "open": [105, 104, 103, 102, 103, 104, 103, 101],
            "high": [106, 105, 104, 103, 105, 106, 104, 104],
            "low": [103, 102, 100, 101, 102, 103, 102, 99],
            "close": [104, 103, 102, 102, 104, 105, 103, 102],
        }
    )

    sweep = detect_liquidity_sweep(df, left=2, right=2)

    assert sweep is not None
    assert sweep["direction"] == "bullish"
    assert sweep["level"] == 100.0


def test_fvg_lifecycle_partial_then_mitigated():
    df = pd.DataFrame(
        {
            "ts": pd.date_range(
                "2026-01-01",
                periods=5,
                freq="D",
            ),
            "open": [100, 104, 111, 111, 107],
            "high": [105, 112, 116, 114, 110],
            "low": [99, 103, 108, 106, 104],
            "close": [104, 111, 115, 110, 106],
        }
    )

    gaps = find_fair_value_gaps(df)
    bullish = [
        item
        for item in gaps
        if item["direction"] == "bullish"
        and item["created_at_index"] == 2
    ]

    assert len(bullish) == 1
    assert bullish[0]["status"] == "MITIGATED"
    assert bullish[0]["fill_percentage"] == 1.0
    assert bullish[0]["first_touch_index"] == 3
    assert bullish[0]["resolved_at_index"] == 4
    assert bullish[0]["is_active"] is False


def test_fvg_lifecycle_invalidated_on_close_through_far_edge():
    df = pd.DataFrame(
        {
            "open": [100, 104, 111, 106],
            "high": [105, 112, 116, 108],
            "low": [99, 103, 108, 101],
            "close": [104, 111, 115, 103],
        }
    )

    gaps = find_fair_value_gaps(df)
    bullish = [
        item
        for item in gaps
        if item["direction"] == "bullish"
        and item["created_at_index"] == 2
    ]

    assert len(bullish) == 1
    assert bullish[0]["status"] == "INVALIDATED"
    assert bullish[0]["is_active"] is False


def test_price_discontinuity_guard_flags_half_price_regime():
    daily = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2026-01-01",
                    "2026-01-02",
                    "2026-01-03",
                    "2026-01-04",
                ]
            ),
            "open": [98, 101, 51, 52],
            "high": [102, 104, 53, 54],
            "low": [97, 100, 50, 51],
            "close": [100, 102, 52, 53],
        }
    )

    events = detect_price_discontinuities(daily)

    assert len(events) == 1
    assert events[0]["boundary_index"] == 2
    assert events[0]["likely_corporate_action"] is True
    assert abs(events[0]["open_ratio"] - 0.5) < 0.02


def test_technical_profile_truncates_long_horizon_smc_after_discontinuity():
    dates = pd.date_range(
        "2025-01-01",
        periods=30,
        freq="D",
    )

    pre = list(range(100, 115))
    post = list(range(55, 70))
    closes = pre + post

    daily = pd.DataFrame(
        {
            "ts": dates,
            "open": [
                value - 1
                for value in closes
            ],
            "high": [
                value + 2
                for value in closes
            ],
            "low": [
                value - 2
                for value in closes
            ],
            "close": closes,
            "volume": [1000] * 30,
        }
    )

    # Make the first post-boundary open about half the previous close.
    daily.loc[15, "open"] = 56
    daily.loc[15, "high"] = 58
    daily.loc[15, "low"] = 54
    daily.loc[15, "close"] = 57

    setup = pd.DataFrame(
        {
            "ts": pd.date_range(
                "2025-01-16",
                periods=30,
                freq="75min",
            ),
            "open": [60] * 30,
            "high": [62] * 30,
            "low": [59] * 30,
            "close": [61] * 30,
            "expected_interval_minutes": [75] * 30,
        }
    )

    profile = build_technical_profile(
        daily=daily,
        setup_75m=setup,
        weekly=None,
        monthly=None,
        one_hour=None,
        data_quality={
            "valid": True,
            "candidate_eligible": True,
            "reasons": [],
            "warnings": [],
            "candidate_blockers": [],
        },
    )

    guard = profile["corporate_action_guard"]

    assert guard["detected"] is True
    assert guard["history_truncated_for_smc"] is True
    assert guard["safe_daily_rows"] == 15
    assert profile["daily_analysis_rows"] == 15

    # Monthly analysis is rebuilt from only the post-boundary regime, so the
    # pre/post scale change cannot manufacture a giant monthly FVG.
    for fvg in profile["monthly_fvgs"]:
        assert fvg["size"] < 30


def _order_block_fixture():
    rows = []

    # Stable history for ATR / lookback.
    for i in range(15):
        base = 100 + (i * 0.1)
        rows.append(
            {
                "open": base,
                "high": base + 1.0,
                "low": base - 1.0,
                "close": base + 0.2,
            }
        )

    # Bearish candle -> bullish displacement breaking prior highs.
    rows.append(
        {
            "open": 102.0,
            "high": 102.5,
            "low": 100.5,
            "close": 101.0,
        }
    )
    rows.append(
        {
            "open": 101.0,
            "high": 106.0,
            "low": 100.8,
            "close": 105.5,
        }
    )

    # Later revisit, but no close below the bullish OB low.
    rows.append(
        {
            "open": 105.0,
            "high": 105.5,
            "low": 101.5,
            "close": 102.0,
        }
    )

    return pd.DataFrame(rows)


def test_order_block_lifecycle_marks_revisit_as_mitigated():
    df = _order_block_fixture()

    blocks = find_order_blocks(
        df,
        atr_period=5,
        displacement_atr=1.0,
        break_lookback=5,
    )

    bullish = [
        item
        for item in blocks
        if item["direction"] == "bullish"
    ]

    assert bullish
    latest = bullish[-1]
    assert latest["status"] == "MITIGATED"
    assert latest["is_active"] is True
    assert latest["is_fresh"] is False
    assert latest["first_touch_index"] == 17


def test_active_poi_filter_excludes_resolved_fvg():
    df = pd.DataFrame(
        {
            "open": [100, 104, 111, 111, 107],
            "high": [105, 112, 116, 114, 110],
            "low": [99, 103, 108, 106, 104],
            "close": [104, 111, 115, 110, 106],
        }
    )

    fvgs = find_fair_value_gaps(df)

    active = filter_active_pois(
        df=df,
        fvgs=fvgs,
        order_blocks=[],
        max_age_bars=100,
        limit=10,
    )

    assert not any(
        poi["type"] == "FVG"
        and poi["created_at_index"] == 2
        for poi in active
    )
