# technical/smc_engine.py

import pandas as pd
import numpy as np

def detect_higher_tf_trend(df: pd.DataFrame) -> str:
    """
    Determine trend on the given timeframe.
    If recent highs/lows are ascending, return "Bullish", if descending "Bearish", else "Neutral".
    """
    # Simple method: compare last few swing points
    highs = df['high']
    lows = df['low']
    if highs.iloc[-1] > highs.iloc[-2] and lows.iloc[-1] > lows.iloc[-2]:
        return "Bullish"
    if highs.iloc[-1] < highs.iloc[-2] and lows.iloc[-1] < lows.iloc[-2]:
        return "Bearish"
    return "Neutral"

def detect_bullish_BOS(df: pd.DataFrame) -> bool:
    """
    Returns True if the last closed candle broke above the previous swing high.
    """
    # Identify last swing high (simple: max of last N highs)
    lookback = 5  # number of candles to consider for prior swing
    swing_high = df['high'].iloc[-(lookback+1):-1].max()
    return df['close'].iloc[-1] > swing_high

def detect_bearish_BOS(df: pd.DataFrame) -> bool:
    """
    Returns True if the last closed candle broke below the previous swing low.
    """
    lookback = 5
    swing_low = df['low'].iloc[-(lookback+1):-1].min()
    return df['close'].iloc[-1] < swing_low

def detect_bullish_CHOCH(df: pd.DataFrame) -> bool:
    """
    Detect bullish Change of Character (e.g., after a bearish run, price dips then flips).
    Simplified: if a candle closed below prior swing low and then next closes above a minor high.
    """
    if len(df) < 3:
        return False
    close2, close1, close0 = df['close'].iloc[-3], df['close'].iloc[-2], df['close'].iloc[-1]
    prev_low = df['low'].iloc[-4] if len(df) >= 4 else min(df['low'])
    prev_high = df['high'].iloc[-3]
    return (close1 < prev_low) and (close0 > prev_high)

def find_order_blocks(df: pd.DataFrame) -> list:
    """
    Identify bullish/bearish order blocks in df.
    A bullish OB is a bearish candle preceding a strong up move; bearish OB vice versa.
    Returns list of (OB_low, OB_high) zones.
    """
    obs = []
    for i in range(len(df)-1):
        # Bullish OB: red candle followed by a larger green body
        open_i, close_i = df['open'].iloc[i], df['close'].iloc[i]
        open_n, close_n = df['open'].iloc[i+1], df['close'].iloc[i+1]
        if close_i < open_i and close_n > open_n:
            # Bullish OB found
            obs.append((df['low'].iloc[i], df['high'].iloc[i]))
        # Bearish OB: green candle followed by larger red body
        if close_i > open_i and close_n < open_n:
            obs.append((df['low'].iloc[i], df['high'].iloc[i]))
    return obs

def find_fair_value_gaps(df: pd.DataFrame) -> list:
    """
    Find Fair Value Gaps (FVG) – price imbalances. 
    Check each 3-candle sequence for unfilled gaps.
    Returns list of (gap_low, gap_high).
    """
    fvg_list = []
    for i in range(len(df)-2):
        # If candle i+1 gap above i
        if df['low'].iloc[i+1] > df['high'].iloc[i]:
            fvg_list.append((df['high'].iloc[i], df['low'].iloc[i+1]))
        # If candle i+1 gap below i
        if df['high'].iloc[i+1] < df['low'].iloc[i]:
            fvg_list.append((df['high'].iloc[i+1], df['low'].iloc[i]))
    return fvg_list

# Example usage within the module
if __name__ == "__main__":
    # Dummy price DataFrame example
    dates = pd.date_range("2021-01-01", periods=10, freq='D')
    df = pd.DataFrame({
        "open": np.random.rand(10)*100 + 100,
        "high": np.random.rand(10)*100 + 150,
        "low": np.random.rand(10)*100 + 90,
        "close": np.random.rand(10)*100 + 100
    }, index=dates)
    trend = detect_higher_tf_trend(df)
    print("Trend:", trend)
    print("Bullish BOS?", detect_bullish_BOS(df))
    print("Bullish CHOCH?", detect_bullish_CHOCH(df))
    print("Order Blocks:", find_order_blocks(df))
    print("FVGs:", find_fair_value_gaps(df))
