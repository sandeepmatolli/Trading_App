# tests/test_smc_engine.py

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


def test_bullish_liquidity_sweep():
    df = pd.DataFrame(
        {
            "open":  [105, 104, 103, 102, 103, 104, 103, 101],
            "high":  [106, 105, 104, 103, 105, 106, 104, 104],
            "low":   [103, 102, 100, 101, 102, 103, 102, 99],
            "close": [104, 103, 102, 102, 104, 105, 103, 102],
        }
    )

    sweep = detect_liquidity_sweep(df, left=2, right=2)

    assert sweep is not None
    assert sweep["direction"] == "bullish"
    assert sweep["level"] == 100.0
