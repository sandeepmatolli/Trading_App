from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd


REQUIRED_OHLC = {"open", "high", "low", "close"}

# V1 SMC safety defaults.
#
# A >=30% close-to-next-open discontinuity is unusual enough that the engine
# must not let pre/post-boundary prices create fake long-horizon SMC signals.
# This is a safety guard, not proof that a corporate action occurred.
PRICE_DISCONTINUITY_THRESHOLD = 0.30

# Common price ratios seen after splits / bonus-style adjustments. A match is
# used only as a "likely corporate action" hint; it is never treated as an
# authoritative exchange-confirmed corporate action.
COMMON_ACTION_RATIOS = (
    0.10,
    0.20,
    0.25,
    1 / 3,
    0.40,
    0.50,
    2 / 3,
    0.75,
    1.25,
    4 / 3,
    1.50,
    2.00,
    2.50,
    3.00,
    4.00,
    5.00,
    10.00,
)

COMMON_ACTION_RATIO_TOLERANCE = 0.08

# Active-POI age limits are intentionally conservative engineering defaults.
# They are not claimed to be optimized trading parameters.
ACTIVE_POI_MAX_AGE_BARS = {
    "daily": 120,
    "weekly": 52,
    "monthly": 24,
    "setup_75m": 100,
    "one_hour": 120,
}


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


def _timestamp_at(
    df: pd.DataFrame,
    index: Optional[int],
) -> Optional[str]:
    if index is None or "ts" not in df.columns:
        return None
    if index < 0 or index >= len(df):
        return None

    value = pd.to_datetime(
        df.iloc[index]["ts"],
        errors="coerce",
    )
    if pd.isna(value):
        return None
    return str(value)


def _expected_interval_minutes(df: pd.DataFrame) -> Optional[int]:
    if "expected_interval_minutes" not in df.columns:
        return None

    values = pd.to_numeric(
        df["expected_interval_minutes"],
        errors="coerce",
    ).dropna()

    if values.empty:
        return None

    unique = sorted(set(int(value) for value in values if value > 0))
    if len(unique) != 1:
        return None
    return unique[0]


def _bars_are_time_contiguous(
    df: pd.DataFrame,
    start_index: int,
    end_index: int,
) -> bool:
    """
    Protect interval-sensitive patterns from crossing missing candles.

    If a frame has `expected_interval_minutes`, consecutive rows used by an
    FVG/order-block pattern must be exactly that far apart. Frames without
    this metadata retain the original behaviour.
    """
    interval_minutes = _expected_interval_minutes(df)
    if interval_minutes is None or "ts" not in df.columns:
        return True

    if start_index < 0 or end_index >= len(df) or start_index >= end_index:
        return False

    timestamps = pd.to_datetime(
        df["ts"],
        errors="coerce",
    )

    expected_seconds = interval_minutes * 60
    for index in range(start_index + 1, end_index + 1):
        previous = timestamps.iloc[index - 1]
        current = timestamps.iloc[index]
        if pd.isna(previous) or pd.isna(current):
            return False
        delta_seconds = (current - previous).total_seconds()
        if delta_seconds != expected_seconds:
            return False

    return True


def _nearest_common_action_ratio(
    ratio: float,
) -> Optional[float]:
    if ratio <= 0:
        return None

    nearest = min(
        COMMON_ACTION_RATIOS,
        key=lambda candidate: abs(ratio - candidate),
    )

    relative_error = abs(ratio - nearest) / nearest
    if relative_error <= COMMON_ACTION_RATIO_TOLERANCE:
        return float(nearest)

    return None


def detect_price_discontinuities(
    daily: pd.DataFrame,
    threshold: float = PRICE_DISCONTINUITY_THRESHOLD,
) -> List[Dict]:
    """
    Detect suspicious Daily price discontinuities.

    This deliberately does NOT claim that a corporate action occurred.
    It detects a large previous-close -> next-open price discontinuity and
    adds a "likely corporate action" hint only when both the open ratio and
    close ratio are close to a common split/bonus-style ratio.

    The result is used to stop long-horizon SMC calculations from mixing
    incompatible pre/post price scales.
    """
    if threshold <= 0 or threshold >= 1:
        raise ValueError("threshold must be between 0 and 1.")

    if daily is None or daily.empty or len(daily) < 2:
        return []

    work = _validate_ohlc(daily, minimum_rows=2)

    events: List[Dict] = []

    for index in range(1, len(work)):
        previous_close = float(work.iloc[index - 1]["close"])
        current_open = float(work.iloc[index]["open"])
        current_close = float(work.iloc[index]["close"])

        if previous_close <= 0 or current_open <= 0 or current_close <= 0:
            continue

        open_ratio = current_open / previous_close
        close_ratio = current_close / previous_close
        open_change_pct = (open_ratio - 1.0) * 100.0

        if abs(open_ratio - 1.0) < threshold:
            continue

        matched_open_ratio = _nearest_common_action_ratio(open_ratio)
        matched_close_ratio = _nearest_common_action_ratio(close_ratio)

        likely_corporate_action = bool(
            matched_open_ratio is not None
            and matched_close_ratio is not None
            and abs(matched_open_ratio - matched_close_ratio) <= 0.05
        )

        events.append(
            {
                "boundary_index": index,
                "previous_index": index - 1,
                "previous_ts": _timestamp_at(work, index - 1),
                "current_ts": _timestamp_at(work, index),
                "previous_close": previous_close,
                "current_open": current_open,
                "current_close": current_close,
                "open_ratio": round(open_ratio, 6),
                "close_ratio": round(close_ratio, 6),
                "open_change_pct": round(open_change_pct, 4),
                "matched_common_ratio": matched_open_ratio,
                "likely_corporate_action": likely_corporate_action,
                "classification": "suspicious_price_discontinuity",
            }
        )

    return events


def _latest_safe_daily_segment(
    daily: pd.DataFrame,
    discontinuities: List[Dict],
) -> pd.DataFrame:
    if daily is None or daily.empty or not discontinuities:
        return daily.copy()

    latest_boundary_index = max(
        int(item["boundary_index"])
        for item in discontinuities
    )

    return (
        daily.iloc[latest_boundary_index:]
        .reset_index(drop=True)
        .copy()
    )


def _filter_frame_from_date(
    df: Optional[pd.DataFrame],
    start_ts: Optional[pd.Timestamp],
) -> Optional[pd.DataFrame]:
    if df is None:
        return None
    if df.empty or start_ts is None or "ts" not in df.columns:
        return df.copy()

    timestamps = pd.to_datetime(
        df["ts"],
        errors="coerce",
    )

    if getattr(timestamps.dt, "tz", None) is None:
        cutoff = pd.Timestamp(start_ts).tz_localize(None)
    else:
        cutoff = pd.Timestamp(start_ts)
        if cutoff.tzinfo is None:
            cutoff = cutoff.tz_localize(timestamps.dt.tz)
        else:
            cutoff = cutoff.tz_convert(timestamps.dt.tz)

    return (
        df[timestamps >= cutoff]
        .reset_index(drop=True)
        .copy()
    )


def _resample_daily_for_smc(
    daily: pd.DataFrame,
    rule: str,
) -> pd.DataFrame:
    if daily is None or daily.empty:
        return pd.DataFrame()

    work = daily.copy()
    if "ts" not in work.columns:
        return pd.DataFrame()

    work["ts"] = pd.to_datetime(
        work["ts"],
        errors="coerce",
    )
    work = work.dropna(subset=["ts"]).sort_values("ts")

    if work.empty:
        return pd.DataFrame()

    work = work.set_index("ts")

    aggregation = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }

    if "volume" in work.columns:
        aggregation["volume"] = "sum"

    result = (
        work.resample(rule)
        .agg(aggregation)
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )

    return result


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
                "candle_ts": _timestamp_at(work, len(work) - 1),
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
                "candle_ts": _timestamp_at(work, len(work) - 1),
            }

    return None


def _annotate_fvg_lifecycle(
    work: pd.DataFrame,
    zone: Dict,
) -> Dict:
    result = dict(zone)

    created_index = int(result["created_at_index"])
    direction = str(result["direction"])
    zone_low = float(result["low"])
    zone_high = float(result["high"])
    width = max(zone_high - zone_low, 1e-12)

    first_touch_index: Optional[int] = None
    resolved_index: Optional[int] = None
    fill_percentage = 0.0
    status = "ACTIVE"

    for index in range(created_index + 1, len(work)):
        candle = work.iloc[index]
        candle_low = float(candle["low"])
        candle_high = float(candle["high"])
        candle_close = float(candle["close"])

        overlap = (
            candle_low <= zone_high
            and candle_high >= zone_low
        )

        if overlap and first_touch_index is None:
            first_touch_index = index

        if direction == "bullish":
            if candle_low < zone_high:
                penetration = zone_high - max(candle_low, zone_low)
                fill_percentage = max(
                    fill_percentage,
                    min(1.0, penetration / width),
                )

            if candle_close < zone_low:
                status = "INVALIDATED"
                resolved_index = index
                fill_percentage = 1.0
                break

            if candle_low <= zone_low:
                status = "MITIGATED"
                resolved_index = index
                fill_percentage = 1.0
                break

        else:
            if candle_high > zone_low:
                penetration = min(candle_high, zone_high) - zone_low
                fill_percentage = max(
                    fill_percentage,
                    min(1.0, penetration / width),
                )

            if candle_close > zone_high:
                status = "INVALIDATED"
                resolved_index = index
                fill_percentage = 1.0
                break

            if candle_high >= zone_high:
                status = "MITIGATED"
                resolved_index = index
                fill_percentage = 1.0
                break

    if status == "ACTIVE" and fill_percentage > 0:
        status = "PARTIALLY_FILLED"

    result.update(
        {
            "created_at_ts": _timestamp_at(work, created_index),
            "status": status,
            "is_active": status in {"ACTIVE", "PARTIALLY_FILLED"},
            "fill_percentage": round(fill_percentage, 4),
            "first_touch_index": first_touch_index,
            "first_touch_ts": _timestamp_at(work, first_touch_index),
            "resolved_at_index": resolved_index,
            "resolved_at_ts": _timestamp_at(work, resolved_index),
            "age_bars": int(len(work) - 1 - created_index),
        }
    )

    return result


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

    Lifecycle:
      ACTIVE            -> never revisited
      PARTIALLY_FILLED  -> revisited but far edge not reached
      MITIGATED         -> far edge touched
      INVALIDATED       -> candle closes beyond the far edge

    When interval metadata is present, FVGs are not allowed to cross missing
    75m/1H buckets or overnight session gaps.
    """
    work = _validate_ohlc(df, minimum_rows=3)
    gaps: List[Dict] = []

    for i in range(2, len(work)):
        if not _bars_are_time_contiguous(work, i - 2, i):
            continue

        first = work.iloc[i - 2]
        third = work.iloc[i]

        bullish_size = float(third["low"] - first["high"])
        if bullish_size > min_gap:
            gaps.append(
                _annotate_fvg_lifecycle(
                    work,
                    {
                        "direction": "bullish",
                        "created_at_index": i,
                        "low": float(first["high"]),
                        "high": float(third["low"]),
                        "size": bullish_size,
                    },
                )
            )

        bearish_size = float(first["low"] - third["high"])
        if bearish_size > min_gap:
            gaps.append(
                _annotate_fvg_lifecycle(
                    work,
                    {
                        "direction": "bearish",
                        "created_at_index": i,
                        "low": float(third["high"]),
                        "high": float(first["low"]),
                        "size": bearish_size,
                    },
                )
            )

    return gaps


def _annotate_order_block_lifecycle(
    work: pd.DataFrame,
    block: Dict,
) -> Dict:
    result = dict(block)

    direction = str(result["direction"])
    known_at_index = int(result["known_at_index"])
    zone_low = float(result["low"])
    zone_high = float(result["high"])

    first_touch_index: Optional[int] = None
    invalidated_index: Optional[int] = None

    # Start after the displacement/confirmation candle.
    for index in range(known_at_index + 1, len(work)):
        candle = work.iloc[index]
        candle_low = float(candle["low"])
        candle_high = float(candle["high"])
        candle_close = float(candle["close"])

        overlap = (
            candle_low <= zone_high
            and candle_high >= zone_low
        )

        if overlap and first_touch_index is None:
            first_touch_index = index

        if direction == "bullish" and candle_close < zone_low:
            invalidated_index = index
            break

        if direction == "bearish" and candle_close > zone_high:
            invalidated_index = index
            break

    if invalidated_index is not None:
        status = "INVALIDATED"
    elif first_touch_index is not None:
        status = "MITIGATED"
    else:
        status = "ACTIVE"

    result.update(
        {
            "created_at_ts": _timestamp_at(
                work,
                int(result["candle_index"]),
            ),
            "known_at_ts": _timestamp_at(work, known_at_index),
            "status": status,
            "is_active": status != "INVALIDATED",
            "is_fresh": status == "ACTIVE",
            "first_touch_index": first_touch_index,
            "first_touch_ts": _timestamp_at(work, first_touch_index),
            "invalidated_at_index": invalidated_index,
            "invalidated_at_ts": _timestamp_at(work, invalidated_index),
            "age_bars": int(
                len(work) - 1 - int(result["candle_index"])
            ),
        }
    )

    return result


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

    Lifecycle:
      ACTIVE      -> zone has not been revisited
      MITIGATED   -> zone has been revisited but not invalidated
      INVALIDATED -> price closed beyond the far edge

    If interval metadata exists, the current and displacement candles must be
    time-contiguous so a missing source bucket cannot manufacture an OB.
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
        if not _bars_are_time_contiguous(work, i, i + 1):
            continue

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

        bearish_current = float(current["close"]) < float(current["open"])
        bullish_current = float(current["close"]) > float(current["open"])

        if (
            bearish_current
            and float(next_candle["close"]) > float(next_candle["open"])
            and float(next_candle["close"]) > prior_high
        ):
            blocks.append(
                _annotate_order_block_lifecycle(
                    work,
                    {
                        "direction": "bullish",
                        "candle_index": i,
                        "known_at_index": i + 1,
                        "low": float(current["low"]),
                        "high": float(current["high"]),
                    },
                )
            )

        if (
            bullish_current
            and float(next_candle["close"]) < float(next_candle["open"])
            and float(next_candle["close"]) < prior_low
        ):
            blocks.append(
                _annotate_order_block_lifecycle(
                    work,
                    {
                        "direction": "bearish",
                        "candle_index": i,
                        "known_at_index": i + 1,
                        "low": float(current["low"]),
                        "high": float(current["high"]),
                    },
                )
            )

    return blocks


def _poi_distance_pct(
    current_price: float,
    low: float,
    high: float,
) -> float:
    if current_price <= 0:
        return 0.0

    if low <= current_price <= high:
        return 0.0

    if current_price < low:
        distance = low - current_price
    else:
        distance = current_price - high

    return (distance / current_price) * 100.0


def filter_active_pois(
    df: pd.DataFrame,
    fvgs: List[Dict],
    order_blocks: List[Dict],
    max_age_bars: Optional[int] = None,
    limit: int = 5,
) -> List[Dict]:
    """
    Return current/relevant POIs only.

    FVGs:
      ACTIVE / PARTIALLY_FILLED are eligible.

    Order blocks:
      ACTIVE and MITIGATED remain valid; freshness is exposed separately.

    POIs older than `max_age_bars` are filtered out when a limit is supplied.
    Remaining zones are sorted by freshness, distance to current price, and
    recency.
    """
    if limit <= 0:
        return []

    work = _validate_ohlc(df, minimum_rows=1)
    current_price = float(work["close"].iloc[-1])

    pois: List[Dict] = []

    for zone in fvgs:
        if not zone.get("is_active", False):
            continue

        age_bars = int(zone.get("age_bars", 0))
        if max_age_bars is not None and age_bars > max_age_bars:
            continue

        low = float(zone["low"])
        high = float(zone["high"])

        pois.append(
            {
                "type": "FVG",
                "direction": zone["direction"],
                "status": zone["status"],
                "is_fresh": zone["status"] == "ACTIVE",
                "low": low,
                "high": high,
                "created_at_index": zone["created_at_index"],
                "created_at_ts": zone.get("created_at_ts"),
                "age_bars": age_bars,
                "fill_percentage": zone.get("fill_percentage"),
                "distance_pct": round(
                    _poi_distance_pct(
                        current_price,
                        low,
                        high,
                    ),
                    4,
                ),
            }
        )

    for block in order_blocks:
        if not block.get("is_active", False):
            continue

        age_bars = int(block.get("age_bars", 0))
        if max_age_bars is not None and age_bars > max_age_bars:
            continue

        low = float(block["low"])
        high = float(block["high"])

        pois.append(
            {
                "type": "ORDER_BLOCK",
                "direction": block["direction"],
                "status": block["status"],
                "is_fresh": bool(block.get("is_fresh", False)),
                "low": low,
                "high": high,
                "created_at_index": block["candle_index"],
                "created_at_ts": block.get("created_at_ts"),
                "known_at_index": block["known_at_index"],
                "known_at_ts": block.get("known_at_ts"),
                "age_bars": age_bars,
                "distance_pct": round(
                    _poi_distance_pct(
                        current_price,
                        low,
                        high,
                    ),
                    4,
                ),
            }
        )

    pois.sort(
        key=lambda item: (
            0 if item.get("is_fresh", False) else 1,
            float(item.get("distance_pct", 0.0)),
            int(item.get("age_bars", 0)),
        )
    )

    return pois[:limit]


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
            f"{prefix}_active_pois": [],
            f"{prefix}_analysis_rows": 0,
            f"{prefix}_analysis_start_ts": None,
        }

    result: Dict = {}

    result[f"{prefix}_analysis_rows"] = int(len(df))
    result[f"{prefix}_analysis_start_ts"] = _timestamp_at(df, 0)

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
        fvgs = find_fair_value_gaps(df)
        result[f"{prefix}_fvgs"] = fvgs[-5:]
    except ValueError:
        fvgs = []
        result[f"{prefix}_fvgs"] = []

    try:
        order_blocks = find_order_blocks(df)
        result[f"{prefix}_order_blocks"] = order_blocks[-5:]
    except ValueError:
        order_blocks = []
        result[f"{prefix}_order_blocks"] = []

    try:
        result[f"{prefix}_active_pois"] = filter_active_pois(
            df=df,
            fvgs=fvgs,
            order_blocks=order_blocks,
            max_age_bars=ACTIVE_POI_MAX_AGE_BARS.get(prefix),
            limit=5,
        )
    except ValueError:
        result[f"{prefix}_active_pois"] = []

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

    Corporate-action safety:
      - detect suspicious Daily price discontinuities;
      - do not claim they are confirmed corporate actions;
      - when a discontinuity is present, current SMC structure is calculated
        only from the latest post-boundary price regime;
      - Weekly/Monthly are rebuilt from that safe Daily segment so a split/
        bonus-style repricing cannot manufacture giant long-horizon FVGs.
    """
    profile: Dict = {}

    discontinuities = detect_price_discontinuities(daily)
    safe_daily = _latest_safe_daily_segment(
        daily,
        discontinuities,
    )

    latest_boundary_ts: Optional[pd.Timestamp] = None
    if discontinuities:
        raw_boundary = discontinuities[-1].get("current_ts")
        if raw_boundary:
            parsed_boundary = pd.to_datetime(
                raw_boundary,
                errors="coerce",
            )
            if not pd.isna(parsed_boundary):
                latest_boundary_ts = parsed_boundary

    if discontinuities:
        safe_weekly = _resample_daily_for_smc(
            safe_daily,
            "W-FRI",
        )
        safe_monthly = _resample_daily_for_smc(
            safe_daily,
            "ME",
        )
    else:
        safe_weekly = (
            weekly.copy()
            if weekly is not None
            else _resample_daily_for_smc(
                safe_daily,
                "W-FRI",
            )
        )
        safe_monthly = (
            monthly.copy()
            if monthly is not None
            else _resample_daily_for_smc(
                safe_daily,
                "ME",
            )
        )

    safe_setup_75m = _filter_frame_from_date(
        setup_75m,
        latest_boundary_ts,
    )
    safe_one_hour = _filter_frame_from_date(
        one_hour,
        latest_boundary_ts,
    )

    profile["corporate_action_guard"] = {
        "detected": bool(discontinuities),
        "method": (
            "suspicious_daily_price_discontinuity_guard; "
            "not an authoritative corporate-action feed"
        ),
        "threshold_pct": PRICE_DISCONTINUITY_THRESHOLD * 100.0,
        "events": discontinuities,
        "event_count": len(discontinuities),
        "likely_corporate_action_count": sum(
            1
            for item in discontinuities
            if item.get("likely_corporate_action")
        ),
        "latest_boundary_ts": (
            str(latest_boundary_ts)
            if latest_boundary_ts is not None
            else None
        ),
        "history_truncated_for_smc": bool(discontinuities),
        "safe_daily_rows": int(len(safe_daily)),
    }

    profile.update(
        _timeframe_profile(
            safe_daily,
            "daily",
        )
    )
    profile.update(
        _timeframe_profile(
            safe_setup_75m,
            "setup_75m",
        )
    )
    profile.update(
        _timeframe_profile(
            safe_weekly,
            "weekly",
        )
    )
    profile.update(
        _timeframe_profile(
            safe_monthly,
            "monthly",
        )
    )

    if safe_one_hour is not None:
        profile.update(
            _timeframe_profile(
                safe_one_hour,
                "one_hour",
            )
        )

    profile["data_quality"] = data_quality or {
        "valid": True,
        "candidate_eligible": True,
        "reasons": [],
        "warnings": [],
        "candidate_blockers": [],
    }

    return profile
