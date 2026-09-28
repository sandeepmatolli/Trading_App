import pandas as pd

from technical.smc_engine import (
    _filter_closed_period_end_bars,
    build_technical_profile,
    detect_liquidity_sweep,
    detect_price_discontinuities,
    filter_active_pois,
    filter_closed_daily_for_smc,
    filter_closed_one_hour_for_smc,
    filter_closed_setup_75m_for_smc,
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

def test_current_daily_is_excluded_before_session_close_and_allowed_after_close():
    daily = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2026-09-25 00:00:00+05:30",
                    "2026-09-28 00:00:00+05:30",
                ]
            ),
            "open": [100, 105],
            "high": [110, 111],
            "low": [95, 101],
            "close": [108, 109],
        }
    )

    intraday = filter_closed_daily_for_smc(
        daily,
        as_of=pd.Timestamp("2026-09-28 12:00:00+05:30"),
    )
    after_close = filter_closed_daily_for_smc(
        daily,
        as_of=pd.Timestamp("2026-09-28 15:31:00+05:30"),
    )

    assert len(intraday) == 1
    assert intraday.iloc[-1]["ts"].date().isoformat() == "2026-09-25"
    assert len(after_close) == 2


def test_75m_smc_uses_only_closed_and_usable_windows():
    setup = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2026-09-25 14:15:00+05:30",
                    "2026-09-28 09:15:00+05:30",
                    "2026-09-28 10:30:00+05:30",
                    "2026-09-28 11:45:00+05:30",
                ]
            ),
            "open": [100, 101, 102, 103],
            "high": [102, 103, 104, 105],
            "low": [99, 100, 101, 102],
            "close": [101, 102, 103, 104],
            "is_usable": [False, True, True, True],
            "expected_interval_minutes": [75, 75, 75, 75],
        }
    )

    closed = filter_closed_setup_75m_for_smc(
        setup,
        as_of=pd.Timestamp("2026-09-28 12:00:00+05:30"),
    )

    # Historical unusable row is excluded; today's 11:45-13:00 window is not
    # closed yet at 12:00. Only 09:15 and 10:30 are allowed into SMC.
    assert len(closed) == 2
    assert [ts.strftime("%H:%M") for ts in closed["ts"]] == [
        "09:15",
        "10:30",
    ]


def test_current_date_one_hour_is_withheld_while_session_is_open():
    one_hour = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2026-09-25 15:00:00+05:30",
                    "2026-09-28 10:00:00+05:30",
                    "2026-09-28 11:00:00+05:30",
                ]
            ),
            "open": [100, 101, 102],
            "high": [102, 103, 104],
            "low": [99, 100, 101],
            "close": [101, 102, 103],
            "expected_interval_minutes": [60, 60, 60],
        }
    )

    intraday = filter_closed_one_hour_for_smc(
        one_hour,
        as_of=pd.Timestamp("2026-09-28 12:00:00+05:30"),
    )
    after_close = filter_closed_one_hour_for_smc(
        one_hour,
        as_of=pd.Timestamp("2026-09-28 15:31:00+05:30"),
    )

    assert len(intraday) == 1
    assert len(after_close) == 3


def test_unfinished_week_and_month_are_not_used_for_smc():
    weekly = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2026-09-25 00:00:00+05:30",
                    "2026-10-02 00:00:00+05:30",
                ]
            ),
            "open": [100, 105],
            "high": [110, 111],
            "low": [95, 101],
            "close": [108, 109],
        }
    )
    monthly = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2026-08-31 00:00:00+05:30",
                    "2026-09-30 00:00:00+05:30",
                ]
            ),
            "open": [100, 105],
            "high": [110, 111],
            "low": [95, 101],
            "close": [108, 109],
        }
    )

    as_of = pd.Timestamp("2026-09-28 12:00:00+05:30")

    closed_weekly = _filter_closed_period_end_bars(weekly, as_of=as_of)
    closed_monthly = _filter_closed_period_end_bars(monthly, as_of=as_of)

    assert len(closed_weekly) == 1
    assert len(closed_monthly) == 1
    assert closed_weekly.iloc[-1]["ts"].date().isoformat() == "2026-09-25"
    assert closed_monthly.iloc[-1]["ts"].date().isoformat() == "2026-08-31"


def test_build_profile_reports_closed_bar_guard_and_drops_live_partial_bars():
    daily_dates = pd.bdate_range(
        end="2026-09-28",
        periods=80,
        tz="Asia/Kolkata",
    )
    daily = pd.DataFrame(
        {
            "ts": daily_dates,
            "open": [100 + i * 0.1 for i in range(80)],
            "high": [102 + i * 0.1 for i in range(80)],
            "low": [99 + i * 0.1 for i in range(80)],
            "close": [101 + i * 0.1 for i in range(80)],
            "volume": [1000] * 80,
        }
    )

    setup_rows = []
    setup_start = pd.Timestamp("2026-09-25 09:15:00+05:30")
    for i in range(5):
        start = setup_start + pd.Timedelta(minutes=75 * i)
        setup_rows.append(
            {
                "ts": start,
                "open": 100 + i,
                "high": 102 + i,
                "low": 99 + i,
                "close": 101 + i,
                "is_usable": True,
                "expected_interval_minutes": 75,
            }
        )

    for start, usable in (
        ("2026-09-28 09:15:00+05:30", True),
        ("2026-09-28 10:30:00+05:30", True),
        ("2026-09-28 11:45:00+05:30", False),
    ):
        setup_rows.append(
            {
                "ts": pd.Timestamp(start),
                "open": 110,
                "high": 112,
                "low": 109,
                "close": 111,
                "is_usable": usable,
                "expected_interval_minutes": 75,
            }
        )

    setup = pd.DataFrame(setup_rows)

    one_hour = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2026-09-25 14:00:00+05:30",
                    "2026-09-28 10:00:00+05:30",
                ]
            ),
            "open": [100, 101],
            "high": [102, 103],
            "low": [99, 100],
            "close": [101, 102],
            "expected_interval_minutes": [60, 60],
        }
    )

    profile = build_technical_profile(
        daily=daily,
        setup_75m=setup,
        one_hour=one_hour,
        data_quality={
            "valid": True,
            "candidate_eligible": True,
            "reasons": [],
            "warnings": [],
            "candidate_blockers": [],
        },
        as_of=pd.Timestamp("2026-09-28 12:00:00+05:30"),
    )

    guard = profile["closed_bar_guard"]

    assert guard["daily_input_rows"] == 80
    assert guard["daily_closed_rows"] == 79
    assert guard["daily_dropped_unclosed_or_future"] == 1
    assert guard["setup_75m_input_rows"] == 8
    assert guard["setup_75m_dropped_unusable"] == 1
    assert guard["setup_75m_closed_usable_rows"] == 7
    assert guard["one_hour_input_rows"] == 2
    assert guard["one_hour_closed_rows"] == 1

    # The SMC profile itself must match the filtered frames, not the raw input.
    assert profile["daily_analysis_rows"] == 79
    assert profile["setup_75m_analysis_rows"] == 7
    assert profile["one_hour_analysis_rows"] == 1

