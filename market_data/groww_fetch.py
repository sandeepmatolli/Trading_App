# market_data/groww_fetch.py

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
from growwapi import GrowwAPI

from config import (
    GROWW_EXCHANGE,
    GROWW_SEGMENT,
)


IST = ZoneInfo("Asia/Kolkata")


# ============================================================
# GROWW CANDLE INTERVAL CONSTANTS
# ============================================================

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
    43200: "CANDLE_INTERVAL_MONTH",
}


# ============================================================
# CURRENT GROWW HISTORICAL-CANDLE REQUEST LIMITS
# ============================================================
#
# Current get_historical_candles/backtesting endpoint:
#
# 1, 2, 3, 5 minute  -> 30 days/request
# 10, 15, 30 minute  -> 90 days/request
# 1H, 4H, D, W, M    -> 180 days/request
#
# We intentionally use a slightly smaller safe window so that
# inclusive timestamps do not accidentally exceed Groww's limit.
# ============================================================

NEW_API_MAX_DAYS: Dict[int, int] = {
    1: 29,
    2: 29,
    3: 29,
    5: 29,

    10: 89,
    15: 89,
    30: 89,

    60: 179,
    240: 179,
    1440: 179,
    10080: 179,
    43200: 179,
}


# ============================================================
# LEGACY API LIMITS
# ============================================================
#
# get_historical_candle_data is deprecated.
# These values exist only as compatibility fallback.
# ============================================================

LEGACY_API_MAX_DAYS: Dict[int, int] = {
    1: 6,
    5: 14,
    10: 29,
    60: 149,
    240: 364,
    1440: 1079,
    10080: 3650,
}


# ============================================================
# HELPERS
# ============================================================

def _exchange_value(groww: GrowwAPI) -> str:
    """
    Convert our config exchange into the Groww SDK constant.
    """

    if GROWW_EXCHANGE == "NSE":
        return getattr(
            groww,
            "EXCHANGE_NSE",
            "NSE",
        )

    if GROWW_EXCHANGE == "BSE":
        return getattr(
            groww,
            "EXCHANGE_BSE",
            "BSE",
        )

    return GROWW_EXCHANGE


def _segment_value(groww: GrowwAPI) -> str:
    """
    Convert our config segment into the Groww SDK constant.
    """

    if GROWW_SEGMENT == "CASH":
        return getattr(
            groww,
            "SEGMENT_CASH",
            "CASH",
        )

    return GROWW_SEGMENT


def _interval_constant(
    groww: GrowwAPI,
    interval_min: int,
):
    """
    Return the Groww SDK candle interval constant.
    """

    constant_name = INTERVAL_CONSTANT_NAMES.get(
        interval_min
    )

    if constant_name is None:
        raise ValueError(
            f"Unsupported interval_min={interval_min}. "
            f"Supported values: "
            f"{sorted(INTERVAL_CONSTANT_NAMES.keys())}"
        )

    value = getattr(
        groww,
        constant_name,
        None,
    )

    if value is None:
        raise AttributeError(
            f"Installed growwapi package does not expose "
            f"{constant_name}. "
            f"Installed SDK may be outdated."
        )

    return value


def _format_api_time(
    value: datetime,
) -> str:
    """
    Groww expects:
        YYYY-MM-DD HH:MM:SS

    We operate using Indian Standard Time.
    """

    if value.tzinfo is not None:
        value = value.astimezone(IST)

    return value.strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _build_windows(
    start: datetime,
    end: datetime,
    max_days: int,
) -> List[Tuple[datetime, datetime]]:
    """
    Split a large historical period into Groww-safe windows.

    Example:

        requested:
            730 days

        Groww maximum:
            180 days/request

        result:
            multiple <=179-day requests

    Windows are chronological.
    """

    if max_days <= 0:
        raise ValueError(
            "max_days must be greater than zero."
        )

    if start >= end:
        raise ValueError(
            "Historical start time must be before end time."
        )

    windows: List[
        Tuple[datetime, datetime]
    ] = []

    cursor = start

    max_delta = timedelta(
        days=max_days
    )

    while cursor < end:

        chunk_end = min(
            cursor + max_delta,
            end,
        )

        windows.append(
            (
                cursor,
                chunk_end,
            )
        )

        if chunk_end >= end:
            break

        # Prevent exact timestamp overlap.
        cursor = (
            chunk_end
            + timedelta(seconds=1)
        )

    return windows


# ============================================================
# CANDLE NORMALIZATION
# ============================================================

def _candles_to_frame(
    candles,
) -> pd.DataFrame:
    """
    Convert Groww candle arrays into a normalized DataFrame.

    Expected candle shape:

        timestamp
        open
        high
        low
        close
        volume
        optional open_interest
    """

    columns = [
        "ts",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_interest",
    ]

    if not candles:
        return pd.DataFrame(
            columns=columns
        )

    max_len = max(
        len(row)
        for row in candles
    )

    if max_len < 5:
        raise ValueError(
            "Groww returned malformed candle data."
        )

    actual_columns = columns[
        :min(max_len, len(columns))
    ]

    normalized_rows = []

    for row in candles:

        values = list(row)[
            :len(actual_columns)
        ]

        while len(values) < len(actual_columns):
            values.append(None)

        normalized_rows.append(
            values
        )

    df = pd.DataFrame(
        normalized_rows,
        columns=actual_columns,
    )

    if "volume" not in df.columns:
        df["volume"] = pd.NA

    if "open_interest" not in df.columns:
        df["open_interest"] = pd.NA


    # --------------------------------------------------------
    # TIMESTAMP NORMALIZATION
    # --------------------------------------------------------

    numeric_ts = pd.to_numeric(
        df["ts"],
        errors="coerce",
    )

    numeric_ratio = (
        numeric_ts.notna().sum()
        / max(len(df), 1)
    )

    if numeric_ratio > 0.90:

        max_abs = numeric_ts.abs().max()

        if (
            pd.notna(max_abs)
            and max_abs > 10_000_000_000
        ):
            unit = "ms"
        else:
            unit = "s"

        parsed_ts = pd.to_datetime(
            numeric_ts,
            unit=unit,
            errors="coerce",
            utc=True,
        )

        df["ts"] = (
            parsed_ts.dt.tz_convert(
                IST
            )
        )

    else:

        parsed_ts = pd.to_datetime(
            df["ts"],
            errors="coerce",
        )

        try:

            if parsed_ts.dt.tz is None:

                parsed_ts = (
                    parsed_ts.dt.tz_localize(
                        IST,
                        ambiguous="NaT",
                        nonexistent="shift_forward",
                    )
                )

            else:

                parsed_ts = (
                    parsed_ts.dt.tz_convert(
                        IST
                    )
                )

        except AttributeError:

            # Defensive fallback if pandas returns a
            # non-standard datetime representation.

            parsed_ts = pd.to_datetime(
                df["ts"],
                errors="coerce",
                utc=True,
            ).dt.tz_convert(
                IST
            )

        df["ts"] = parsed_ts


    # --------------------------------------------------------
    # NUMERIC NORMALIZATION
    # --------------------------------------------------------

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_interest",
    ]

    for column in numeric_columns:

        if column in df.columns:

            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            )


    # --------------------------------------------------------
    # REQUIRED VALUES
    # --------------------------------------------------------

    df = df.dropna(
        subset=[
            "ts",
            "open",
            "high",
            "low",
            "close",
        ]
    )


    # --------------------------------------------------------
    # OHLC VALIDATION
    # --------------------------------------------------------

    invalid_high = (
        df["high"]
        <
        df[
            [
                "open",
                "close",
                "low",
            ]
        ].max(axis=1)
    )

    invalid_low = (
        df["low"]
        >
        df[
            [
                "open",
                "close",
                "high",
            ]
        ].min(axis=1)
    )

    invalid = (
        invalid_high
        | invalid_low
    )

    if invalid.any():

        bad_count = int(
            invalid.sum()
        )

        raise ValueError(
            f"Groww returned "
            f"{bad_count} invalid OHLC candles."
        )


    # --------------------------------------------------------
    # SORT / DEDUPLICATE
    # --------------------------------------------------------

    df = (
        df
        .sort_values("ts")
        .drop_duplicates(
            subset=["ts"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    return df


def _merge_frames(
    frames: List[pd.DataFrame],
) -> pd.DataFrame:
    """
    Merge several historical chunks safely.
    """

    non_empty = [
        frame
        for frame in frames
        if frame is not None
        and not frame.empty
    ]

    if not non_empty:

        return _candles_to_frame(
            []
        )

    merged = pd.concat(
        non_empty,
        ignore_index=True,
    )

    merged = (
        merged
        .sort_values("ts")
        .drop_duplicates(
            subset=["ts"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    return merged


# ============================================================
# CURRENT GROWW API
# ============================================================

def _fetch_new_api_chunk(
    groww: GrowwAPI,
    symbol: str,
    interval_min: int,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """
    Make one Groww get_historical_candles request.
    """

    response = groww.get_historical_candles(
        exchange=_exchange_value(
            groww
        ),
        segment=_segment_value(
            groww
        ),
        groww_symbol=(
            f"{GROWW_EXCHANGE}-{symbol}"
        ),
        start_time=_format_api_time(
            start
        ),
        end_time=_format_api_time(
            end
        ),
        candle_interval=_interval_constant(
            groww,
            interval_min,
        ),
    )

    if not isinstance(
        response,
        dict,
    ):
        raise ValueError(
            "Unexpected Groww historical "
            "response type."
        )

    candles = response.get(
        "candles",
        [],
    )

    return _candles_to_frame(
        candles
    )


def _fetch_new_api(
    groww: GrowwAPI,
    symbol: str,
    interval_min: int,
    days_back: int,
) -> pd.DataFrame:
    """
    Fetch the requested total history from the current Groww API.

    Large requests are split into Groww-safe windows.
    """

    max_days = NEW_API_MAX_DAYS.get(
        interval_min
    )

    if max_days is None:
        raise ValueError(
            f"No request-duration configuration "
            f"for interval {interval_min}."
        )

    end = datetime.now(
        IST
    )

    start = (
        end
        - timedelta(
            days=days_back
        )
    )

    windows = _build_windows(
        start=start,
        end=end,
        max_days=max_days,
    )

    print(
        f"{symbol}: fetching "
        f"{interval_min}-minute candles "
        f"for {days_back} days "
        f"in {len(windows)} request(s)."
    )

    frames: List[
        pd.DataFrame
    ] = []

    for index, (
        chunk_start,
        chunk_end,
    ) in enumerate(
        windows,
        start=1,
    ):

        print(
            f"  Request "
            f"{index}/{len(windows)}: "
            f"{chunk_start.date()} "
            f"-> "
            f"{chunk_end.date()}"
        )

        frame = _fetch_new_api_chunk(
            groww=groww,
            symbol=symbol,
            interval_min=interval_min,
            start=chunk_start,
            end=chunk_end,
        )

        frames.append(
            frame
        )

        # Small pause between chunks.
        # This is defensive pacing rather than
        # relying on undocumented historical-data
        # rate behaviour.
        if index < len(windows):
            time.sleep(
                0.15
            )

    result = _merge_frames(
        frames
    )

    print(
        f"{symbol}: received "
        f"{len(result)} unique candles "
        f"for interval={interval_min}."
    )

    return result


# ============================================================
# LEGACY GROWW API FALLBACK
# ============================================================

def _fetch_legacy_chunk(
    groww: GrowwAPI,
    symbol: str,
    interval_min: int,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """
    One deprecated historical request.

    Retained only for compatibility with older SDK versions.
    """

    response = (
        groww.get_historical_candle_data(
            trading_symbol=symbol,
            exchange=_exchange_value(
                groww
            ),
            segment=_segment_value(
                groww
            ),
            start_time=_format_api_time(
                start
            ),
            end_time=_format_api_time(
                end
            ),
            interval_in_minutes=interval_min,
        )
    )

    if not isinstance(
        response,
        dict,
    ):
        raise ValueError(
            "Unexpected Groww legacy "
            "historical response type."
        )

    return _candles_to_frame(
        response.get(
            "candles",
            [],
        )
    )


def _fetch_legacy_api(
    groww: GrowwAPI,
    symbol: str,
    interval_min: int,
    days_back: int,
) -> pd.DataFrame:
    """
    Compatibility fallback for the deprecated Groww method.
    """

    max_days = LEGACY_API_MAX_DAYS.get(
        interval_min
    )

    if max_days is None:

        raise ValueError(
            f"Legacy Groww API does not have "
            f"a configured limit for "
            f"interval={interval_min}."
        )

    end = datetime.now(
        IST
    )

    start = (
        end
        - timedelta(
            days=days_back
        )
    )

    windows = _build_windows(
        start=start,
        end=end,
        max_days=max_days,
    )

    frames: List[
        pd.DataFrame
    ] = []

    for index, (
        chunk_start,
        chunk_end,
    ) in enumerate(
        windows,
        start=1,
    ):

        frame = _fetch_legacy_chunk(
            groww=groww,
            symbol=symbol,
            interval_min=interval_min,
            start=chunk_start,
            end=chunk_end,
        )

        frames.append(
            frame
        )

        if index < len(windows):
            time.sleep(
                0.15
            )

    return _merge_frames(
        frames
    )


# ============================================================
# PUBLIC FETCH FUNCTION
# ============================================================

def fetch_candles(
    groww: GrowwAPI,
    symbol: str,
    interval_min: int,
    days_back: int,
) -> pd.DataFrame:
    """
    Fetch historical OHLCV for one NSE/BSE instrument.

    Current Groww API request-size limits are handled
    automatically by splitting large date ranges into
    multiple requests and merging the responses.

    Example:

        fetch_candles(
            groww,
            "RELIANCE",
            interval_min=1440,
            days_back=730,
        )

    730 daily days will be fetched in multiple chunks
    rather than one invalid request.
    """

    symbol = str(
        symbol
    ).strip().upper()

    if not symbol:
        raise ValueError(
            "symbol cannot be empty."
        )

    if days_back <= 0:
        raise ValueError(
            "days_back must be greater than zero."
        )

    if interval_min not in INTERVAL_CONSTANT_NAMES:
        raise ValueError(
            f"Unsupported interval_min={interval_min}. "
            f"Supported intervals: "
            f"{sorted(INTERVAL_CONSTANT_NAMES.keys())}"
        )

    # --------------------------------------------------------
    # PREFERRED CURRENT API
    # --------------------------------------------------------

    if hasattr(
        groww,
        "get_historical_candles",
    ):

        return _fetch_new_api(
            groww=groww,
            symbol=symbol,
            interval_min=interval_min,
            days_back=days_back,
        )


    # --------------------------------------------------------
    # DEPRECATED FALLBACK
    # --------------------------------------------------------

    if hasattr(
        groww,
        "get_historical_candle_data",
    ):

        print(
            "Warning: installed Groww SDK "
            "does not expose "
            "get_historical_candles(). "
            "Using deprecated historical API."
        )

        return _fetch_legacy_api(
            groww=groww,
            symbol=symbol,
            interval_min=interval_min,
            days_back=days_back,
        )


    raise AttributeError(
        "Installed growwapi package does not provide "
        "a supported historical-candle method."
    )


# ============================================================
# RESAMPLING
# ============================================================

def resample_ohlcv(
    df: pd.DataFrame,
    rule: str,
) -> pd.DataFrame:
    """
    Resample completed OHLCV candles.

    Examples:

        Weekly:
            W-FRI

        Monthly:
            ME

    The method preserves:

        Open   -> first
        High   -> maximum
        Low    -> minimum
        Close  -> last
        Volume -> sum
    """

    if df.empty:
        return df.copy()

    required = {
        "ts",
        "open",
        "high",
        "low",
        "close",
    }

    missing = (
        required
        - set(df.columns)
    )

    if missing:
        raise ValueError(
            f"Cannot resample OHLCV. "
            f"Missing columns: "
            f"{sorted(missing)}"
        )

    work = (
        df.copy()
        .dropna(
            subset=["ts"]
        )
        .sort_values("ts")
    )

    work = work.set_index(
        "ts"
    )

    aggregation = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }

    if "volume" in work.columns:
        aggregation[
            "volume"
        ] = "sum"

    if (
        "open_interest"
        in work.columns
    ):
        aggregation[
            "open_interest"
        ] = "last"

    result = (
        work
        .resample(rule)
        .agg(aggregation)
    )

    result = result.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    result = (
        result
        .reset_index()
        .sort_values("ts")
        .reset_index(drop=True)
    )

    return result


# ============================================================
# MANUAL MODULE TEST
# ============================================================

if __name__ == "__main__":

    from market_data.groww_auth import (
        get_groww_api,
    )

    api = get_groww_api()

    daily = fetch_candles(
        api,
        symbol="RELIANCE",
        interval_min=1440,
        days_back=730,
    )

    print(
        "\nDaily candles:"
    )

    print(
        daily.tail()
    )

    print(
        "\nTotal daily candles:",
        len(daily),
    )

    if not daily.empty:

        print(
            "From:",
            daily["ts"].min(),
        )

        print(
            "To:",
            daily["ts"].max(),
        )

    weekly = resample_ohlcv(
        daily,
        "W-FRI",
    )

    monthly = resample_ohlcv(
        daily,
        "ME",
    )

    print(
        "Weekly candles:",
        len(weekly),
    )

    print(
        "Monthly candles:",
        len(monthly),
    )