# market_data/groww_fetch.py

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict

import pandas as pd
from growwapi import GrowwAPI

from config import GROWW_EXCHANGE, GROWW_SEGMENT


INTERVAL_CONSTANT_NAMES: Dict[int, str] = {
    1: "CANDLE_INTERVAL_MIN_1",
    2: "CANDLE_INTERVAL_MIN_2",
    3: "CANDLE_INTERVAL_MIN_3",
    5: "CANDLE_INTERVAL_MIN_5",
    10: "CANDLE_INTERVAL_MIN_10",
    15: "CANDLE_INTERVAL_MIN_15",
    30: "CANDLE_INTERVAL_MIN_30",
    60: "CANDLE_INTERVAL_HOUR_1",
    240: "CANDLE_INTERVAL_HOUR_4",
    1440: "CANDLE_INTERVAL_DAY",
    10080: "CANDLE_INTERVAL_WEEK",
}


def _exchange_value(groww: GrowwAPI) -> str:
    if GROWW_EXCHANGE == "NSE":
        return getattr(groww, "EXCHANGE_NSE", "NSE")
    if GROWW_EXCHANGE == "BSE":
        return getattr(groww, "EXCHANGE_BSE", "BSE")
    return GROWW_EXCHANGE


def _segment_value(groww: GrowwAPI) -> str:
    if GROWW_SEGMENT == "CASH":
        return getattr(groww, "SEGMENT_CASH", "CASH")
    return GROWW_SEGMENT


def _interval_constant(groww: GrowwAPI, interval_min: int):
    name = INTERVAL_CONSTANT_NAMES.get(interval_min)
    if name is None:
        raise ValueError(
            f"Unsupported interval_min={interval_min}. "
            f"Supported values: {sorted(INTERVAL_CONSTANT_NAMES)}"
        )

    value = getattr(groww, name, None)
    if value is None:
        raise AttributeError(
            f"Installed growwapi package does not expose {name}. "
            "Upgrade growwapi or use the legacy fallback."
        )
    return value


def _candles_to_frame(candles) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(
            columns=[
                "ts",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "open_interest",
            ]
        )

    max_len = max(len(row) for row in candles)
    columns = [
        "ts",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_interest",
    ][:max_len]

    normalized = [list(row)[: len(columns)] for row in candles]
    df = pd.DataFrame(normalized, columns=columns)

    if "open_interest" not in df.columns:
        df["open_interest"] = pd.NA

    if pd.api.types.is_numeric_dtype(df["ts"]):
        numeric_ts = pd.to_numeric(df["ts"], errors="coerce")
        max_abs = numeric_ts.abs().max()

        # Legacy responses document epoch seconds. Some APIs may return
        # milliseconds, so detect the scale rather than guessing.
        unit = "ms" if pd.notna(max_abs) and max_abs > 10_000_000_000 else "s"
        df["ts"] = pd.to_datetime(
            numeric_ts,
            unit=unit,
            errors="coerce",
            utc=True,
        ).dt.tz_convert("Asia/Kolkata")
    else:
        parsed = pd.to_datetime(df["ts"], errors="coerce")
        if parsed.dt.tz is None:
            df["ts"] = parsed.dt.tz_localize(
                "Asia/Kolkata",
                ambiguous="NaT",
                nonexistent="shift_forward",
            )
        else:
            df["ts"] = parsed.dt.tz_convert("Asia/Kolkata")

    for col in ["open", "high", "low", "close", "volume", "open_interest"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = (
        df.dropna(subset=["ts", "open", "high", "low", "close"])
        .sort_values("ts")
        .drop_duplicates(subset=["ts"], keep="last")
        .reset_index(drop=True)
    )

    invalid = (
        (df["high"] < df[["open", "close", "low"]].max(axis=1))
        | (df["low"] > df[["open", "close", "high"]].min(axis=1))
    )
    if invalid.any():
        bad = int(invalid.sum())
        raise ValueError(f"Groww returned {bad} invalid OHLC candles.")

    return df


def _fetch_new_api(
    groww: GrowwAPI,
    symbol: str,
    interval_min: int,
    days_back: int,
) -> pd.DataFrame:
    end = datetime.now()
    start = end - timedelta(days=days_back)

    response = groww.get_historical_candles(
        exchange=_exchange_value(groww),
        segment=_segment_value(groww),
        groww_symbol=f"{GROWW_EXCHANGE}-{symbol}",
        start_time=start.strftime("%Y-%m-%d %H:%M:%S"),
        end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
        candle_interval=_interval_constant(groww, interval_min),
    )
    return _candles_to_frame(response.get("candles", []))


def _fetch_legacy_api(
    groww: GrowwAPI,
    symbol: str,
    interval_min: int,
    days_back: int,
) -> pd.DataFrame:
    end = datetime.now()
    start = end - timedelta(days=days_back)

    response = groww.get_historical_candle_data(
        trading_symbol=symbol,
        exchange=_exchange_value(groww),
        segment=_segment_value(groww),
        start_time=start.strftime("%Y-%m-%d %H:%M:%S"),
        end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
        interval_in_minutes=interval_min,
    )
    return _candles_to_frame(response.get("candles", []))


def fetch_candles(
    groww: GrowwAPI,
    symbol: str,
    interval_min: int,
    days_back: int,
) -> pd.DataFrame:
    """
    Fetch historical OHLCV.

    The current Groww method get_historical_candles is used when available.
    The older get_historical_candle_data method is retained only as a
    compatibility fallback.
    """
    symbol = str(symbol).strip().upper()
    if not symbol:
        raise ValueError("symbol cannot be empty.")
    if days_back <= 0:
        raise ValueError("days_back must be positive.")

    if hasattr(groww, "get_historical_candles"):
        return _fetch_new_api(
            groww,
            symbol=symbol,
            interval_min=interval_min,
            days_back=days_back,
        )

    if hasattr(groww, "get_historical_candle_data"):
        print(
            "Warning: Installed Groww SDK does not expose "
            "get_historical_candles; using deprecated historical method."
        )
        return _fetch_legacy_api(
            groww,
            symbol=symbol,
            interval_min=interval_min,
            days_back=days_back,
        )

    raise AttributeError(
        "The installed growwapi package does not provide a supported "
        "historical-candle method."
    )


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """
    Resample already-completed OHLCV candles to a higher timeframe.

    Examples:
      rule='W-FRI' for weekly
      rule='ME' for month-end
    """
    if df.empty:
        return df.copy()

    work = df.copy()
    work = work.dropna(subset=["ts"]).sort_values("ts")
    work = work.set_index("ts")

    aggregation = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    if "open_interest" in work.columns:
        aggregation["open_interest"] = "last"

    result = work.resample(rule).agg(aggregation)
    result = result.dropna(subset=["open", "high", "low", "close"])
    return result.reset_index()


if __name__ == "__main__":
    from market_data.groww_auth import get_groww_api

    api = get_groww_api()
    sample = fetch_candles(
        api,
        symbol="RELIANCE",
        interval_min=1440,
        days_back=30,
    )
    print(sample.tail())
