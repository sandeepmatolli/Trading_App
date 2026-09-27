# technical/smc_engine.py

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd


REQUIRED_OHLC = {"open", "high", "low", "close"}


def _validate_ohlc(
    df: pd.DataFrame,
    minimum_rows: int = 3,
) -> pd.DataFrame:
    missing = REQUIRED_OHLC - set(df.columns)
    if missing:
        raise ValueError(f"Missing OHLC columns: {sorted(missing)}")

    if len(df) < minimum_rows:
        raise ValueError(
            f"Need at least {minimum_rows} candles; received {len(df)}."
        )

    work = df.copy()
    for col in ("open", "high", "low", "close", "volume"):
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")

    work = work.dropna(
        subset=["open", "high", "low", "close"]
    ).reset_index(drop=True)

    if len(work) < minimum_rows:
        raise ValueError("Not enough valid OHLC candles after cleaning.")

    return work


def calculate_atr(
    df: pd.DataFrame,
    period: int = 14,
) -> pd.Series:
    work = _validate_ohlc(df, minimum_rows=2)

    previous_close = work["close"].shift(1)
    true_range = pd.concat(
        [
            work["high"] - work["low"],
            (work["high"] - previous_close).abs(),
            (work["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()


def confirmed_swings(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
) -> pd.DataFrame:
    """
    Non-repainting pivot highs/lows.

    A pivot at index i is only knowable after `right` later candles close.
    """
    if left < 1 or right < 1:
        raise ValueError("left and right must both be >= 1.")

    work = _validate_ohlc(
        df,
        minimum_rows=left + right + 1,
    )

    records: List[Dict] = []

    for i in range(left, len(work) - right):
        high_window = work["high"].iloc[i - left : i + right + 1]
        low_window = work["low"].iloc[i - left : i + right + 1]

        if work["high"].iloc[i] == high_window.max():
            records.append(
                {
                    "pivot_index": i,
                    "known_at_index": i + right,
                    "type": "high",
                    "price": float(work["high"].iloc[i]),
                }
            )

        if work["low"].iloc[i] == low_window.min():
            records.append(
                {
                    "pivot_index": i,
                    "known_at_index": i + right,
                    "type": "low",
                    "price": float(work["low"].iloc[i]),
                }
            )

    return pd.DataFrame(
        records,
        columns=[
            "pivot_index",
            "known_at_index",
            "type",
            "price",
        ],
    )


def _known_swings_before_last(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
) -> pd.DataFrame:
    swings = confirmed_swings(df, left=left, right=right)
    if swings.empty:
        return swings

    last_index = len(df) - 1
    return swings[
        swings["known_at_index"] <= last_index - 1
    ]


def detect_higher_tf_trend(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
) -> str:
    work = _validate_ohlc(
        df,
        minimum_rows=left + right + 3,
    )

    swings = confirmed_swings(
        work,
        left=left,
        right=right,
    )

    highs = swings[swings["type"] == "high"].tail(2)
    lows = swings[swings["type"] == "low"].tail(2)

    if len(highs) < 2 or len(lows) < 2:
        return "Neutral"

    high_1 = float(highs["price"].iloc[-2])
    high_2 = float(highs["price"].iloc[-1])
    low_1 = float(lows["price"].iloc[-2])
    low_2 = float(lows["price"].iloc[-1])

    if high_2 > high_1 and low_2 > low_1:
        return "Bullish"

    if high_2 < high_1 and low_2 < low_1:
        return "Bearish"

    return "Neutral"


def detect_bullish_BOS(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
) -> bool:
    work = _validate_ohlc(
        df,
        minimum_rows=left + right + 3,
    )

    swings = _known_swings_before_last(
        work,
        left=left,
        right=right,
    )

    highs = swings[swings["type"] == "high"]
    if highs.empty:
        return False

    level = float(highs.iloc[-1]["price"])
    previous_close = float(work["close"].iloc[-2])
    current_close = float(work["close"].iloc[-1])

    return previous_close <= level < current_close


def detect_bearish_BOS(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
) -> bool:
    work = _validate_ohlc(
        df,
        minimum_rows=left + right + 3,
    )

    swings = _known_swings_before_last(
        work,
        left=left,
        right=right,
    )

    lows = swings[swings["type"] == "low"]
    if lows.empty:
        return False

    level = float(lows.iloc[-1]["price"])
    previous_close = float(work["close"].iloc[-2])
    current_close = float(work["close"].iloc[-1])

    return previous_close >= level > current_close


def detect_bullish_CHOCH(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
) -> bool:
    work = _validate_ohlc(
        df,
        minimum_rows=left + right + 6,
    )

    prior_trend = detect_higher_tf_trend(
        work.iloc[:-1].reset_index(drop=True),
        left=left,
        right=right,
    )

    return (
        prior_trend == "Bearish"
        and detect_bullish_BOS(
            work,
            left=left,
            right=right,
        )
    )


def detect_bearish_CHOCH(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
) -> bool:
    work = _validate_ohlc(
        df,
        minimum_rows=left + right + 6,
    )

    prior_trend = detect_higher_tf_trend(
        work.iloc[:-1].reset_index(drop=True),
        left=left,
        right=right,
    )

    return (
        prior_trend == "Bullish"
        and detect_bearish_BOS(
            work,
            left=left,
            right=right,
        )
    )


def detect_liquidity_sweep(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
) -> Optional[Dict]:
    """
    Bullish sweep:
      trades below latest confirmed swing low and closes back above it.

    Bearish sweep:
      trades above latest confirmed swing high and closes back below it.
    """
    work = _validate_ohlc(
        df,
        minimum_rows=left + right + 3,
    )

    swings = _known_swings_before_last(
        work,
        left=left,
        right=right,
    )

    if swings.empty:
        return None

    candle = work.iloc[-1]

    lows = swings[swings["type"] == "low"]
    if not lows.empty:
        level = float(lows.iloc[-1]["price"])
        if (
            float(candle["low"]) < level
            and float(candle["close"]) > level
        ):
            return {
                "direction": "bullish",
                "level": level,
                "candle_index": len(work) - 1,
            }

    highs = swings[swings["type"] == "high"]
    if not highs.empty:
        level = float(highs.iloc[-1]["price"])
        if (
            float(candle["high"]) > level
            and float(candle["close"]) < level
        ):
            return {
                "direction": "bearish",
                "level": level,
                "candle_index": len(work) - 1,
            }

    return None


def find_fair_value_gaps(
    df: pd.DataFrame,
    min_gap: float = 0.0,
) -> List[Dict]:
    """
    Three-candle FVG convention.

    Bullish:
      candle 3 low > candle 1 high

    Bearish:
      candle 3 high < candle 1 low
    """
    work = _validate_ohlc(df, minimum_rows=3)
    gaps: List[Dict] = []

    for i in range(2, len(work)):
        first = work.iloc[i - 2]
        third = work.iloc[i]

        bullish_size = float(
            third["low"] - first["high"]
        )
        if bullish_size > min_gap:
            gaps.append(
                {
                    "direction": "bullish",
                    "created_at_index": i,
                    "low": float(first["high"]),
                    "high": float(third["low"]),
                    "size": bullish_size,
                }
            )

        bearish_size = float(
            first["low"] - third["high"]
        )
        if bearish_size > min_gap:
            gaps.append(
                {
                    "direction": "bearish",
                    "created_at_index": i,
                    "low": float(third["high"]),
                    "high": float(first["low"]),
                    "size": bearish_size,
                }
            )

    return gaps


def find_order_blocks(
    df: pd.DataFrame,
    atr_period: int = 14,
    displacement_atr: float = 1.0,
    break_lookback: int = 5,
) -> List[Dict]:
    """
    Strict, deterministic order-block candidate convention.

    Bullish:
      - bearish candle
      - next candle is bullish displacement
      - next close breaks prior lookback high

    Bearish is the inverse.
    """
    work = _validate_ohlc(
        df,
        minimum_rows=max(
            atr_period + 2,
            break_lookback + 2,
        ),
    )

    atr = calculate_atr(
        work,
        period=atr_period,
    )

    blocks: List[Dict] = []

    for i in range(
        break_lookback,
        len(work) - 1,
    ):
        current = work.iloc[i]
        next_candle = work.iloc[i + 1]
        current_atr = atr.iloc[i + 1]

        if pd.isna(current_atr) or current_atr <= 0:
            continue

        next_body = abs(
            float(next_candle["close"])
            - float(next_candle["open"])
        )

        if next_body < float(current_atr) * displacement_atr:
            continue

        prior_high = float(
            work["high"].iloc[
                i - break_lookback : i
            ].max()
        )
        prior_low = float(
            work["low"].iloc[
                i - break_lookback : i
            ].min()
        )

        bearish_current = (
            float(current["close"])
            < float(current["open"])
        )
        bullish_current = (
            float(current["close"])
            > float(current["open"])
        )

        if (
            bearish_current
            and float(next_candle["close"])
            > float(next_candle["open"])
            and float(next_candle["close"]) > prior_high
        ):
            blocks.append(
                {
                    "direction": "bullish",
                    "candle_index": i,
                    "known_at_index": i + 1,
                    "low": float(current["low"]),
                    "high": float(current["high"]),
                }
            )

        if (
            bullish_current
            and float(next_candle["close"])
            < float(next_candle["open"])
            and float(next_candle["close"]) < prior_low
        ):
            blocks.append(
                {
                    "direction": "bearish",
                    "candle_index": i,
                    "known_at_index": i + 1,
                    "low": float(current["low"]),
                    "high": float(current["high"]),
                }
            )

    return blocks


def _timeframe_profile(
    df: pd.DataFrame,
    prefix: str,
) -> Dict:
    if df is None or df.empty:
        return {
            f"{prefix}_trend": "InsufficientData",
            f"{prefix}_bos_bullish": False,
            f"{prefix}_bos_bearish": False,
            f"{prefix}_choch_bullish": False,
            f"{prefix}_choch_bearish": False,
            f"{prefix}_liquidity_sweep": None,
            f"{prefix}_fvgs": [],
            f"{prefix}_order_blocks": [],
        }

    result: Dict = {}

    try:
        result[f"{prefix}_trend"] = detect_higher_tf_trend(df)
    except ValueError:
        result[f"{prefix}_trend"] = "InsufficientData"

    for name, function in (
        ("bos_bullish", detect_bullish_BOS),
        ("bos_bearish", detect_bearish_BOS),
        ("choch_bullish", detect_bullish_CHOCH),
        ("choch_bearish", detect_bearish_CHOCH),
    ):
        try:
            result[f"{prefix}_{name}"] = bool(function(df))
        except ValueError:
            result[f"{prefix}_{name}"] = False

    try:
        result[f"{prefix}_liquidity_sweep"] = detect_liquidity_sweep(df)
    except ValueError:
        result[f"{prefix}_liquidity_sweep"] = None

    try:
        result[f"{prefix}_fvgs"] = find_fair_value_gaps(df)[-5:]
    except ValueError:
        result[f"{prefix}_fvgs"] = []

    try:
        result[f"{prefix}_order_blocks"] = find_order_blocks(df)[-5:]
    except ValueError:
        result[f"{prefix}_order_blocks"] = []

    return result


def build_technical_profile(
    daily: pd.DataFrame,
    setup_75m: pd.DataFrame,
    weekly: Optional[pd.DataFrame] = None,
    monthly: Optional[pd.DataFrame] = None,
    one_hour: Optional[pd.DataFrame] = None,
    data_quality: Optional[Dict] = None,
) -> Dict:
    """
    Build deterministic SMC evidence.

    V1 timeframe roles:
      Monthly -> macro context
      Weekly  -> primary swing structure
      Daily   -> setup structure
      75m     -> session-aligned refinement
      1H      -> entry/near-term context
    """
    profile: Dict = {}

    profile.update(
        _timeframe_profile(
            daily,
            "daily",
        )
    )
    profile.update(
        _timeframe_profile(
            setup_75m,
            "setup_75m",
        )
    )

    if weekly is not None:
        profile.update(
            _timeframe_profile(
                weekly,
                "weekly",
            )
        )

    if monthly is not None:
        profile.update(
            _timeframe_profile(
                monthly,
                "monthly",
            )
        )

    if one_hour is not None:
        profile.update(
            _timeframe_profile(
                one_hour,
                "one_hour",
            )
        )

    profile["data_quality"] = data_quality or {
        "valid": True,
        "reasons": [],
        "warnings": [],
    }

    return profile
