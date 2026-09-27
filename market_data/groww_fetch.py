from __future__ import annotations

import time
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from config import (
    GROWW_15M_REQUEST_CHUNK_DAYS,
    GROWW_EXCHANGE,
    GROWW_GAP_REPAIR_CHUNK_DAYS,
    GROWW_GAP_REPAIR_MAX_PASSES,
    GROWW_HOURLY_REQUEST_CHUNK_DAYS,
    GROWW_SEGMENT,
    MARKET_DATA_CANDIDATE_SETUP_USABLE_EXPECTED_RATIO,
    MARKET_DATA_MAX_AGE_DAYS,
    MARKET_DATA_MAX_DAILY_GAP_DAYS,
    MARKET_DATA_MIN_DAILY_COVERAGE_RATIO,
    MARKET_DATA_MIN_SETUP_BARS,
    MARKET_DATA_MIN_SETUP_USABLE_EXPECTED_RATIO,
    MARKET_DATA_RECENT_CONTINUITY_DAYS,
    MARKET_DATA_SETUP_MIN_SOURCE_BARS,
    NSE_SESSION_END,
    NSE_SESSION_START,
)


IST = ZoneInfo("Asia/Kolkata")


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


# Current Groww backtesting API limits. We still use smaller operational
# chunks configured in config.py for additional safety.
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


def _exchange_value(groww: Any) -> str:
    if GROWW_EXCHANGE == "NSE":
        return getattr(groww, "EXCHANGE_NSE", "NSE")
    if GROWW_EXCHANGE == "BSE":
        return getattr(groww, "EXCHANGE_BSE", "BSE")
    return GROWW_EXCHANGE


def _segment_value(groww: Any) -> str:
    if GROWW_SEGMENT == "CASH":
        return getattr(groww, "SEGMENT_CASH", "CASH")
    return GROWW_SEGMENT


def _interval_constant(groww: Any, interval_min: int):
    constant_name = INTERVAL_CONSTANT_NAMES.get(interval_min)
    if constant_name is None:
        raise ValueError(
            f"Unsupported interval_min={interval_min}. "
            f"Supported: {sorted(INTERVAL_CONSTANT_NAMES)}"
        )

    value = getattr(groww, constant_name, None)
    if value is None:
        raise AttributeError(
            f"Installed growwapi package does not expose {constant_name}. "
            "Upgrade growwapi."
        )
    return value


def _parse_clock(value: str) -> dt_time:
    try:
        hour, minute = value.split(":", 1)
        return dt_time(hour=int(hour), minute=int(minute))
    except Exception as exc:
        raise ValueError(f"Invalid HH:MM time value: {value!r}") from exc


SESSION_START = _parse_clock(NSE_SESSION_START)
SESSION_END = _parse_clock(NSE_SESSION_END)


def _format_api_time(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(IST)
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _build_windows(
    start: datetime,
    end: datetime,
    max_days: int,
) -> List[Tuple[datetime, datetime]]:
    if max_days <= 0:
        raise ValueError("max_days must be greater than zero.")
    if start >= end:
        raise ValueError("start must be earlier than end.")

    windows: List[Tuple[datetime, datetime]] = []
    cursor = start
    delta = timedelta(days=max_days)

    while cursor < end:
        chunk_end = min(cursor + delta, end)
        windows.append((cursor, chunk_end))
        if chunk_end >= end:
            break
        cursor = chunk_end + timedelta(seconds=1)

    return windows


def _empty_candles() -> pd.DataFrame:
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


def _empty_setup_75m() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "ts",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "open_interest",
            "source_bars",
            "expected_source_bars",
            "source_coverage_ratio",
            "is_usable",
            "window_index",
            "expected_interval_minutes",
        ]
    )


def _candles_to_frame(candles) -> pd.DataFrame:
    if not candles:
        return _empty_candles()

    columns = [
        "ts",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_interest",
    ]

    normalized_rows = []
    for row in candles:
        values = list(row)
        if len(values) < 5:
            continue
        values = values[: len(columns)]
        while len(values) < len(columns):
            values.append(None)
        normalized_rows.append(values)

    if not normalized_rows:
        return _empty_candles()

    df = pd.DataFrame(normalized_rows, columns=columns)

    numeric_ts = pd.to_numeric(df["ts"], errors="coerce")
    numeric_ratio = numeric_ts.notna().mean()

    if numeric_ratio >= 0.90:
        max_abs = numeric_ts.abs().max()
        unit = "ms" if pd.notna(max_abs) and max_abs > 10_000_000_000 else "s"
        df["ts"] = pd.to_datetime(
            numeric_ts,
            unit=unit,
            errors="coerce",
            utc=True,
        ).dt.tz_convert(IST)
    else:
        parsed = pd.to_datetime(df["ts"], errors="coerce")
        if getattr(parsed.dt, "tz", None) is None:
            df["ts"] = parsed.dt.tz_localize(
                IST,
                ambiguous="NaT",
                nonexistent="shift_forward",
            )
        else:
            df["ts"] = parsed.dt.tz_convert(IST)

    for column in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_interest",
    ):
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(subset=["ts", "open", "high", "low", "close"]).copy()

    invalid_high = df["high"] < df[["open", "close", "low"]].max(axis=1)
    invalid_low = df["low"] > df[["open", "close", "high"]].min(axis=1)
    invalid = invalid_high | invalid_low

    if invalid.any():
        raise ValueError(
            f"Groww returned {int(invalid.sum())} invalid OHLC candle(s)."
        )

    return (
        df.sort_values("ts")
        .drop_duplicates(subset=["ts"], keep="last")
        .reset_index(drop=True)
    )


def _merge_frames(frames: List[pd.DataFrame]) -> pd.DataFrame:
    usable = [frame for frame in frames if frame is not None and not frame.empty]
    if not usable:
        return _empty_candles()

    return (
        pd.concat(usable, ignore_index=True)
        .sort_values("ts")
        .drop_duplicates(subset=["ts"], keep="last")
        .reset_index(drop=True)
    )


def resolve_groww_instrument(groww: Any, trading_symbol: str) -> Dict:
    symbol = str(trading_symbol).strip().upper()
    if not symbol:
        raise ValueError("trading_symbol cannot be empty.")

    exchange = _exchange_value(groww)

    if not hasattr(groww, "get_instrument_by_exchange_and_trading_symbol"):
        return {
            "exchange": exchange,
            "trading_symbol": symbol,
            "groww_symbol": f"{GROWW_EXCHANGE}-{symbol}",
            "segment": _segment_value(groww),
            "instrument_type": None,
            "series": None,
        }

    instrument = groww.get_instrument_by_exchange_and_trading_symbol(
        exchange=exchange,
        trading_symbol=symbol,
    )

    if not isinstance(instrument, dict):
        raise ValueError(
            f"Groww instrument lookup returned unexpected data for {symbol}."
        )

    groww_symbol = str(instrument.get("groww_symbol", "") or "").strip()
    if not groww_symbol:
        raise ValueError(
            f"Groww instrument lookup did not return groww_symbol for {symbol}."
        )

    segment = str(instrument.get("segment", "") or "").strip().upper()
    if segment and segment != GROWW_SEGMENT:
        raise ValueError(
            f"{symbol} resolved to segment={segment}, expected {GROWW_SEGMENT}."
        )

    return instrument


def _request_candle_range(
    groww: Any,
    groww_symbol: str,
    interval_min: int,
    start: datetime,
    end: datetime,
    chunk_days: int,
    label: str = "historical",
) -> pd.DataFrame:
    documented_max = NEW_API_MAX_DAYS.get(interval_min)
    if documented_max is None:
        raise ValueError(f"No request limit configured for interval {interval_min}.")

    if chunk_days <= 0:
        raise ValueError("chunk_days must be positive.")

    safe_chunk_days = min(chunk_days, documented_max)
    windows = _build_windows(start, end, safe_chunk_days)

    print(
        f"{groww_symbol}: fetching {label} interval={interval_min} "
        f"from {start.date()} to {end.date()} in {len(windows)} request(s) "
        f"using <= {safe_chunk_days}-day chunks."
    )

    frames: List[pd.DataFrame] = []

    for index, (chunk_start, chunk_end) in enumerate(windows, start=1):
        response = groww.get_historical_candles(
            exchange=_exchange_value(groww),
            segment=_segment_value(groww),
            groww_symbol=groww_symbol,
            start_time=_format_api_time(chunk_start),
            end_time=_format_api_time(chunk_end),
            candle_interval=_interval_constant(groww, interval_min),
        )

        if not isinstance(response, dict):
            raise ValueError("Unexpected Groww historical-candles response type.")

        frame = _candles_to_frame(response.get("candles", []))
        frames.append(frame)

        if frame.empty:
            coverage = "0 candles"
        else:
            coverage = (
                f"{len(frame)} candles | "
                f"{frame['ts'].min()} -> {frame['ts'].max()}"
            )

        print(
            f"  Request {index}/{len(windows)}: "
            f"{chunk_start.date()} -> {chunk_end.date()} | {coverage}"
        )

        if index < len(windows):
            time.sleep(0.15)

    return _merge_frames(frames)


def fetch_candles(
    groww: Any,
    groww_symbol: str,
    interval_min: int,
    days_back: int,
    chunk_days: Optional[int] = None,
) -> pd.DataFrame:
    if days_back <= 0:
        raise ValueError("days_back must be positive.")

    if not hasattr(groww, "get_historical_candles"):
        raise AttributeError(
            "Installed Groww SDK does not provide get_historical_candles(). "
            "Upgrade growwapi."
        )

    documented_max = NEW_API_MAX_DAYS.get(interval_min)
    if documented_max is None:
        raise ValueError(f"No request limit configured for interval {interval_min}.")

    if chunk_days is None:
        chunk_days = documented_max

    end = datetime.now(IST)
    start = end - timedelta(days=days_back)

    result = _request_candle_range(
        groww=groww,
        groww_symbol=groww_symbol,
        interval_min=interval_min,
        start=start,
        end=end,
        chunk_days=chunk_days,
        label="history",
    )

    print(
        f"{groww_symbol}: received {len(result)} unique "
        f"interval={interval_min} candles."
    )
    return result


def _within_regular_session(timestamp: pd.Timestamp) -> bool:
    local_time = timestamp.tz_convert(IST).time()
    return SESSION_START <= local_time < SESSION_END


def _within_hourly_label_guard(timestamp: pd.Timestamp) -> bool:
    """
    Keep Groww 1H labels that plausibly belong to the NSE cash day.

    Groww documents candle timestamps but does not document whether every 1H
    timestamp is the opening or closing edge of its bucket. Live responses in
    this project have shown both 09:00 and 10:00 first labels. Because of that,
    do not apply the stricter 09:15 filter to 1H data. This broad guard avoids
    accidentally deleting the opening bucket while still excluding obvious
    after-hours timestamps.
    """
    local_time = timestamp.tz_convert(IST).time()
    return dt_time(9, 0) <= local_time <= SESSION_END


def _minutes_since_session_start(timestamp: pd.Timestamp) -> Optional[int]:
    local = timestamp.tz_convert(IST)
    session_start = local.replace(
        hour=SESSION_START.hour,
        minute=SESSION_START.minute,
        second=0,
        microsecond=0,
    )
    delta_minutes = int((local - session_start).total_seconds() // 60)
    if delta_minutes < 0:
        return None
    return delta_minutes


def _is_regular_15m_slot(timestamp: pd.Timestamp) -> bool:
    if not _within_regular_session(timestamp):
        return False
    minutes = _minutes_since_session_start(timestamp)
    return minutes is not None and minutes % 15 == 0


def build_daily_from_hourly(
    hourly: pd.DataFrame,
    min_source_bars: int = 1,
) -> pd.DataFrame:
    """
    Build Daily OHLCV from Groww 1H candles.

    A day is retained with at least one real 1H candle. Requiring four or more
    source bars created artificial Daily gaps for illiquid symbols where Groww
    simply omitted empty intraday buckets. Liquidity is assessed separately by
    the quality gate rather than by fabricating or discarding Daily candles.
    """
    if hourly.empty:
        return pd.DataFrame(
            columns=[
                "ts",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "open_interest",
                "source_bars",
            ]
        )

    work = hourly.copy().sort_values("ts")
    work = work[work["ts"].map(_within_hourly_label_guard)].copy()
    if work.empty:
        return pd.DataFrame()

    work["session_date"] = work["ts"].dt.tz_convert(IST).dt.date
    rows: List[Dict] = []

    for session_date, group in work.groupby("session_date", sort=True):
        group = group.sort_values("ts")
        if len(group) < min_source_bars:
            continue

        rows.append(
            {
                "ts": pd.Timestamp(session_date).tz_localize(IST),
                "open": float(group["open"].iloc[0]),
                "high": float(group["high"].max()),
                "low": float(group["low"].min()),
                "close": float(group["close"].iloc[-1]),
                "volume": float(pd.to_numeric(group["volume"], errors="coerce").fillna(0.0).sum()),
                "open_interest": (
                    group["open_interest"].dropna().iloc[-1]
                    if group["open_interest"].notna().any()
                    else pd.NA
                ),
                "source_bars": int(len(group)),
            }
        )

    return pd.DataFrame(rows)


def build_session_75m_from_15m(
    fifteen_minute: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build fixed, session-aligned 75-minute bars from real 15-minute candles.

    The five windows are anchored to the NSE regular session and never shift
    when a 15m candle is missing:
      09:15-10:30
      10:30-11:45
      11:45-13:00
      13:00-14:15
      14:15-15:30

    No missing 15m candle is fabricated. A window is emitted when it contains
    at least one real source candle. source_bars and is_usable make thin data
    explicit so the quality gate can decide whether SMC is trustworthy.
    """
    if fifteen_minute.empty:
        return _empty_setup_75m()

    work = (
        fifteen_minute.copy()
        .sort_values("ts")
        .drop_duplicates(subset=["ts"], keep="last")
        .reset_index(drop=True)
    )

    work = work[work["ts"].map(_is_regular_15m_slot)].copy()
    if work.empty:
        return _empty_setup_75m()

    work["session_date"] = work["ts"].dt.tz_convert(IST).dt.date
    work["minutes_since_open"] = work["ts"].map(_minutes_since_session_start)
    work["window_index"] = (work["minutes_since_open"] // 75).astype(int)
    work = work[(work["window_index"] >= 0) & (work["window_index"] <= 4)].copy()

    rows: List[Dict] = []

    for session_date, day in work.groupby("session_date", sort=True):
        day = day.sort_values("ts")

        for window_index in range(5):
            chunk = day[day["window_index"] == window_index].sort_values("ts")
            if chunk.empty:
                continue

            window_start = pd.Timestamp(session_date).tz_localize(IST) + pd.Timedelta(
                hours=SESSION_START.hour,
                minutes=SESSION_START.minute + window_index * 75,
            )

            source_bars = int(len(chunk))
            expected_source_bars = 5

            rows.append(
                {
                    "ts": window_start,
                    "open": float(chunk["open"].iloc[0]),
                    "high": float(chunk["high"].max()),
                    "low": float(chunk["low"].min()),
                    "close": float(chunk["close"].iloc[-1]),
                    "volume": float(pd.to_numeric(chunk["volume"], errors="coerce").fillna(0.0).sum()),
                    "open_interest": (
                        chunk["open_interest"].dropna().iloc[-1]
                        if chunk["open_interest"].notna().any()
                        else pd.NA
                    ),
                    "source_bars": source_bars,
                    "expected_source_bars": expected_source_bars,
                    "source_coverage_ratio": source_bars / expected_source_bars,
                    "is_usable": source_bars >= MARKET_DATA_SETUP_MIN_SOURCE_BARS,
                    "window_index": window_index,
                    "expected_interval_minutes": 75,
                }
            )

    if not rows:
        return _empty_setup_75m()

    return (
        pd.DataFrame(rows)
        .sort_values("ts")
        .drop_duplicates(subset=["ts"], keep="last")
        .reset_index(drop=True)
    )


def resample_daily_to_higher(
    daily: pd.DataFrame,
    rule: str,
) -> pd.DataFrame:
    if daily.empty:
        return daily.copy()

    required = {"ts", "open", "high", "low", "close"}
    missing = required - set(daily.columns)
    if missing:
        raise ValueError(f"Cannot resample Daily candles. Missing: {sorted(missing)}")

    work = daily.copy().sort_values("ts").set_index("ts")

    aggregation = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    if "volume" in work.columns:
        aggregation["volume"] = "sum"
    if "open_interest" in work.columns:
        aggregation["open_interest"] = "last"

    result = work.resample(rule).agg(aggregation)
    result = result.dropna(subset=["open", "high", "low", "close"])
    return result.reset_index()


def _large_daily_gaps(
    daily: pd.DataFrame,
    threshold_days: int,
) -> List[Dict]:
    if daily.empty or len(daily) < 2:
        return []

    dates = (
        daily.sort_values("ts")["ts"]
        .dt.tz_convert(IST)
        .dt.normalize()
        .reset_index(drop=True)
    )

    gaps: List[Dict] = []
    for index in range(1, len(dates)):
        previous = dates.iloc[index - 1]
        current = dates.iloc[index]
        gap_days = int((current - previous).days)

        if gap_days > threshold_days:
            gaps.append(
                {
                    "previous_daily": str(previous),
                    "next_daily": str(current),
                    "gap_days": gap_days,
                    "repair_start": previous + pd.Timedelta(days=1),
                    "repair_end": current - pd.Timedelta(seconds=1),
                }
            )

    return gaps


def _max_gap_days(daily: pd.DataFrame) -> Optional[int]:
    gaps = _large_daily_gaps(daily, threshold_days=0)
    if not gaps:
        return None
    return max(int(item["gap_days"]) for item in gaps)


def _repair_hourly_gaps(
    groww: Any,
    groww_symbol: str,
    hourly: pd.DataFrame,
) -> pd.DataFrame:
    """
    Retry large holes with smaller 1H requests. No candles are fabricated.
    """
    repaired = hourly.copy()

    for pass_number in range(1, GROWW_GAP_REPAIR_MAX_PASSES + 1):
        daily = build_daily_from_hourly(repaired)
        gaps = _large_daily_gaps(
            daily,
            threshold_days=MARKET_DATA_MAX_DAILY_GAP_DAYS,
        )

        if not gaps:
            if pass_number > 1:
                print(
                    f"{groww_symbol}: gap repair completed; "
                    "no large Daily gaps remain."
                )
            break

        print(
            f"{groww_symbol}: gap-repair pass {pass_number} found "
            f"{len(gaps)} suspicious Daily gap(s)."
        )

        before_count = len(repaired)
        before_max_gap = _max_gap_days(daily)
        repair_frames: List[pd.DataFrame] = [repaired]

        for gap_index, gap in enumerate(gaps, start=1):
            repair_start = gap["repair_start"].to_pydatetime()
            repair_end = gap["repair_end"].to_pydatetime()

            print(
                f"  Gap {gap_index}/{len(gaps)}: "
                f"{gap['previous_daily']} -> {gap['next_daily']} "
                f"({gap['gap_days']} calendar days)"
            )

            refill = _request_candle_range(
                groww=groww,
                groww_symbol=groww_symbol,
                interval_min=60,
                start=repair_start,
                end=repair_end,
                chunk_days=GROWW_GAP_REPAIR_CHUNK_DAYS,
                label="gap-repair",
            )
            repair_frames.append(refill)

        repaired = _merge_frames(repair_frames)
        after_daily = build_daily_from_hourly(repaired)
        after_max_gap = _max_gap_days(after_daily)

        print(
            f"{groww_symbol}: gap-repair pass {pass_number} added "
            f"{len(repaired) - before_count} unique 1H candle(s); "
            f"max Daily gap {before_max_gap} -> {after_max_gap}."
        )

        if len(repaired) == before_count and after_max_gap == before_max_gap:
            print(
                f"{groww_symbol}: Groww returned no additional candles for "
                "the missing range(s)."
            )
            break

    return repaired


def _count_15m_markers(fifteen_minute: pd.DataFrame) -> int:
    if fifteen_minute.empty:
        return 0

    local = fifteen_minute["ts"].dt.tz_convert(IST)
    times = local.dt.time
    return int((times >= SESSION_END).sum())


def _count_15m_off_grid_rows(fifteen_minute: pd.DataFrame) -> int:
    if fifteen_minute.empty:
        return 0

    count = 0
    for timestamp in fifteen_minute["ts"]:
        if _within_regular_session(timestamp) and not _is_regular_15m_slot(timestamp):
            count += 1
    return count


def validate_market_data(
    hourly: pd.DataFrame,
    daily: pd.DataFrame,
    setup_75m: pd.DataFrame,
    requested_history_days: int,
    fifteen_minute: Optional[pd.DataFrame] = None,
    setup_history_days: Optional[int] = None,
) -> Dict:
    """
    Validate market data with separate fatal and degradable conditions.

    valid=False:
      SMC must not run and the symbol becomes DATA_REJECT.

    valid=True, candidate_eligible=False:
      SMC may run for research, but AI validation must keep the symbol on WATCH.

    Old historical gaps outside MARKET_DATA_RECENT_CONTINUITY_DAYS are warnings,
    not automatic fatal errors. Recent gaps remain fatal.
    """
    reasons: List[str] = []
    warnings: List[str] = []
    candidate_blockers: List[str] = []
    degraded_features: List[str] = []

    expected_weekdays = max(1, int(requested_history_days * 5 / 7))
    minimum_daily_rows = max(
        60,
        int(expected_weekdays * MARKET_DATA_MIN_DAILY_COVERAGE_RATIO),
    )

    daily_count = len(daily)
    hourly_count = len(hourly)
    setup_count = len(setup_75m)

    if daily_count < minimum_daily_rows:
        reasons.append(
            f"Daily history too sparse: {daily_count} rows; "
            f"minimum expected {minimum_daily_rows}."
        )

    now = pd.Timestamp.now(tz=IST)

    latest_daily = None
    latest_age_days = None
    if not daily.empty:
        latest_daily = daily["ts"].max()
        latest_age_days = int(
            (now.normalize() - latest_daily.tz_convert(IST).normalize()).days
        )
        if latest_age_days > MARKET_DATA_MAX_AGE_DAYS:
            reasons.append(f"Latest Daily candle is {latest_age_days} day(s) old.")
    else:
        reasons.append("No Daily candles constructed.")

    all_large_gaps = _large_daily_gaps(
        daily,
        threshold_days=MARKET_DATA_MAX_DAILY_GAP_DAYS,
    )

    recent_cutoff = now.normalize() - pd.Timedelta(
        days=MARKET_DATA_RECENT_CONTINUITY_DAYS
    )
    recent_large_gaps: List[Dict] = []
    old_large_gaps: List[Dict] = []

    for gap in all_large_gaps:
        next_daily = pd.Timestamp(gap["next_daily"])
        if next_daily.tzinfo is None:
            next_daily = next_daily.tz_localize(IST)
        else:
            next_daily = next_daily.tz_convert(IST)

        if next_daily >= recent_cutoff:
            recent_large_gaps.append(gap)
        else:
            old_large_gaps.append(gap)

    if recent_large_gaps:
        worst_recent_gap = max(int(item["gap_days"]) for item in recent_large_gaps)
        reasons.append(
            f"Recent Daily history has a {worst_recent_gap}-day gap; "
            f"maximum allowed is {MARKET_DATA_MAX_DAILY_GAP_DAYS}."
        )

    if old_large_gaps:
        worst_old_gap = max(int(item["gap_days"]) for item in old_large_gaps)
        warnings.append(
            f"Older Daily history contains a {worst_old_gap}-day gap outside "
            f"the recent {MARKET_DATA_RECENT_CONTINUITY_DAYS}-day continuity window."
        )
        degraded_features.append("older_historical_continuity")

    if daily_count and hourly_count < daily_count * 2:
        warnings.append(
            "Hourly/Daily row ratio is low; this symbol may be illiquid or "
            "Groww may omit empty 1H buckets."
        )
        degraded_features.append("hourly_density")

    if setup_history_days is None:
        setup_history_days = 89

    setup_cutoff = now - pd.Timedelta(days=setup_history_days)
    recent_daily = daily[daily["ts"] >= setup_cutoff].copy() if not daily.empty else daily
    expected_setup_windows = int(len(recent_daily) * 5)

    usable_setup_bars = 0
    if not setup_75m.empty:
        if "is_usable" in setup_75m.columns:
            usable_setup_bars = int(setup_75m["is_usable"].fillna(False).sum())
        elif "source_bars" in setup_75m.columns:
            usable_setup_bars = int(
                (setup_75m["source_bars"] >= MARKET_DATA_SETUP_MIN_SOURCE_BARS).sum()
            )
        else:
            usable_setup_bars = setup_count

    setup_window_coverage_ratio = (
        setup_count / expected_setup_windows if expected_setup_windows > 0 else 0.0
    )
    setup_usable_expected_ratio = (
        usable_setup_bars / expected_setup_windows
        if expected_setup_windows > 0
        else 0.0
    )

    latest_setup = None
    latest_setup_age_days = None
    if not setup_75m.empty:
        latest_setup = setup_75m["ts"].max()
        latest_setup_age_days = int(
            (now.normalize() - latest_setup.tz_convert(IST).normalize()).days
        )
        if latest_setup_age_days > MARKET_DATA_MAX_AGE_DAYS:
            reasons.append(
                f"Latest 75m setup candle is {latest_setup_age_days} day(s) old."
            )
    else:
        reasons.append("No 75m setup candles constructed.")

    if usable_setup_bars < MARKET_DATA_MIN_SETUP_BARS:
        reasons.append(
            f"Insufficient usable 75m setup history: {usable_setup_bars} bars; "
            f"minimum {MARKET_DATA_MIN_SETUP_BARS}."
        )

    if (
        expected_setup_windows > 0
        and setup_usable_expected_ratio < MARKET_DATA_MIN_SETUP_USABLE_EXPECTED_RATIO
    ):
        reasons.append(
            "Usable 75m coverage is too low: "
            f"{setup_usable_expected_ratio:.1%}; minimum "
            f"{MARKET_DATA_MIN_SETUP_USABLE_EXPECTED_RATIO:.1%}."
        )

    if (
        expected_setup_windows > 0
        and setup_usable_expected_ratio
        < MARKET_DATA_CANDIDATE_SETUP_USABLE_EXPECTED_RATIO
    ):
        candidate_blockers.append(
            "75m source completeness is below candidate-grade: "
            f"{setup_usable_expected_ratio:.1%} < "
            f"{MARKET_DATA_CANDIDATE_SETUP_USABLE_EXPECTED_RATIO:.1%}."
        )
        degraded_features.append("setup_75m_completeness")

    partial_75m_bars = 0
    if not setup_75m.empty and "source_bars" in setup_75m.columns:
        partial_75m_bars = int((setup_75m["source_bars"] < 5).sum())
        if partial_75m_bars:
            warnings.append(
                f"{partial_75m_bars} of {setup_count} 75m bars are partial "
                "because one or more 15m source candles were absent."
            )

    source_15m_rows = int(len(fifteen_minute)) if fifteen_minute is not None else None
    marker_15m_rows = (
        _count_15m_markers(fifteen_minute)
        if fifteen_minute is not None
        else None
    )
    off_grid_15m_rows = (
        _count_15m_off_grid_rows(fifteen_minute)
        if fifteen_minute is not None
        else None
    )

    if off_grid_15m_rows:
        warnings.append(
            f"Ignored {off_grid_15m_rows} regular-session 15m row(s) that were "
            "not aligned to a 15-minute grid."
        )
        candidate_blockers.append("15m source contains off-grid timestamps.")
        degraded_features.append("source_timestamp_alignment")

    max_gap = _max_gap_days(daily)
    valid = not reasons
    candidate_eligible = valid and not candidate_blockers

    return {
        "valid": valid,
        "candidate_eligible": candidate_eligible,
        "degraded": bool(warnings or candidate_blockers),
        "reasons": reasons,
        "warnings": warnings,
        "candidate_blockers": candidate_blockers,
        "degraded_features": sorted(set(degraded_features)),
        "hourly_rows": hourly_count,
        "daily_rows": daily_count,
        "setup_75m_rows": setup_count,
        "setup_75m_usable_rows": usable_setup_bars,
        "expected_setup_75m_windows": expected_setup_windows,
        "setup_75m_window_coverage_ratio": round(setup_window_coverage_ratio, 4),
        "setup_75m_usable_expected_ratio": round(setup_usable_expected_ratio, 4),
        "setup_75m_partial_rows": partial_75m_bars,
        "setup_75m_min_source_bars": MARKET_DATA_SETUP_MIN_SOURCE_BARS,
        "source_15m_rows": source_15m_rows,
        "source_15m_marker_rows": marker_15m_rows,
        "source_15m_off_grid_rows": off_grid_15m_rows,
        "latest_daily": str(latest_daily) if latest_daily is not None else None,
        "latest_daily_age_days": latest_age_days,
        "latest_setup_75m": str(latest_setup) if latest_setup is not None else None,
        "latest_setup_75m_age_days": latest_setup_age_days,
        "max_daily_gap_days": max_gap,
        "large_daily_gaps": [
            {
                "previous_daily": item["previous_daily"],
                "next_daily": item["next_daily"],
                "gap_days": item["gap_days"],
            }
            for item in all_large_gaps
        ],
        "recent_large_daily_gaps": [
            {
                "previous_daily": item["previous_daily"],
                "next_daily": item["next_daily"],
                "gap_days": item["gap_days"],
            }
            for item in recent_large_gaps
        ],
        "older_large_daily_gaps": [
            {
                "previous_daily": item["previous_daily"],
                "next_daily": item["next_daily"],
                "gap_days": item["gap_days"],
            }
            for item in old_large_gaps
        ],
        "minimum_daily_rows_required": minimum_daily_rows,
    }


def fetch_market_timeframes(
    groww: Any,
    trading_symbol: str,
    hourly_history_days: int,
    setup_15m_days: int,
) -> Dict[str, Any]:
    instrument = resolve_groww_instrument(groww, trading_symbol)
    groww_symbol = str(instrument["groww_symbol"])

    hourly = fetch_candles(
        groww,
        groww_symbol=groww_symbol,
        interval_min=60,
        days_back=hourly_history_days,
        chunk_days=GROWW_HOURLY_REQUEST_CHUNK_DAYS,
    )

    hourly = _repair_hourly_gaps(
        groww=groww,
        groww_symbol=groww_symbol,
        hourly=hourly,
    )

    fifteen = fetch_candles(
        groww,
        groww_symbol=groww_symbol,
        interval_min=15,
        days_back=setup_15m_days,
        chunk_days=GROWW_15M_REQUEST_CHUNK_DAYS,
    )

    daily = build_daily_from_hourly(hourly)
    weekly = resample_daily_to_higher(daily, "W-FRI")
    monthly = resample_daily_to_higher(daily, "ME")
    setup_75m = build_session_75m_from_15m(fifteen)

    quality = validate_market_data(
        hourly=hourly,
        daily=daily,
        setup_75m=setup_75m,
        requested_history_days=hourly_history_days,
        fifteen_minute=fifteen,
        setup_history_days=setup_15m_days,
    )

    recent_cutoff = pd.Timestamp.now(tz=IST) - pd.Timedelta(days=setup_15m_days)
    recent_hourly = hourly[hourly["ts"] >= recent_cutoff].reset_index(drop=True).copy()
    if not recent_hourly.empty:
        recent_hourly["expected_interval_minutes"] = 60

    return {
        "instrument": instrument,
        "hourly": recent_hourly,
        "hourly_full": hourly,
        "fifteen_minute": fifteen,
        "setup_75m": setup_75m,
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "quality": quality,
    }


if __name__ == "__main__":
    from market_data.groww_auth import get_groww_api

    api = get_groww_api()
    bundle = fetch_market_timeframes(
        api,
        trading_symbol="20MICRONS",
        hourly_history_days=730,
        setup_15m_days=89,
    )

    print("\nInstrument:")
    print(bundle["instrument"])

    print("\nData quality:")
    print(bundle["quality"])

    for key in ("hourly", "setup_75m", "daily", "weekly", "monthly"):
        frame = bundle[key]
        print(f"\n{key}: {len(frame)} rows")
        print(frame.tail())