import pandas as pd

from technical.smc_engine import (
    detect_liquidity_sweep,
    find_fair_value_gaps,
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