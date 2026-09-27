# tests/test_market_data.py

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from market_data.groww_fetch import (
    build_daily_from_hourly,
    build_session_75m_from_15m,
    validate_market_data,
)


IST = ZoneInfo("Asia/Kolkata")


def _hourly_fixture(days: int = 80) -> pd.DataFrame:
    rows = []
    start = datetime(2026, 1, 1, tzinfo=IST)

    trading_days = 0
    current = start

    while trading_days < days:
        if current.weekday() < 5:
            for hour, minute in [
                (9, 15),
                (10, 15),
                (11, 15),
                (12, 15),
                (13, 15),
                (14, 15),
            ]:
                ts = current.replace(
                    hour=hour,
                    minute=minute,
                )
                base = 100 + trading_days
                rows.append(
                    {
                        "ts": ts,
                        "open": base,
                        "high": base + 2,
                        "low": base - 1,
                        "close": base + 1,
                        "volume": 1000,
                        "open_interest": pd.NA,
                    }
                )
            trading_days += 1

        current += timedelta(days=1)

    return pd.DataFrame(rows)


def _fifteen_minute_fixture(days: int = 10) -> pd.DataFrame:
    rows = []
    current = datetime(
        2026,
        9,
        1,
        tzinfo=IST,
    )
    trading_days = 0

    while trading_days < days:
        if current.weekday() < 5:
            ts = current.replace(
                hour=9,
                minute=15,
            )

            for index in range(25):
                base = 200 + index
                rows.append(
                    {
                        "ts": ts
                        + timedelta(
                            minutes=15 * index
                        ),
                        "open": base,
                        "high": base + 1,
                        "low": base - 1,
                        "close": base + 0.5,
                        "volume": 100,
                        "open_interest": pd.NA,
                    }
                )

            trading_days += 1

        current += timedelta(days=1)

    return pd.DataFrame(rows)


def test_daily_is_derived_from_hourly():
    hourly = _hourly_fixture(
        days=80
    )

    daily = (
        build_daily_from_hourly(
            hourly
        )
    )

    assert len(daily) == 80
    assert (
        daily["source_bars"] == 6
    ).all()


def test_75m_has_five_bars_per_complete_day():
    fifteen = (
        _fifteen_minute_fixture(
            days=10
        )
    )

    setup = (
        build_session_75m_from_15m(
            fifteen
        )
    )

    assert len(setup) == 50
    assert (
        setup["source_bars"] == 5
    ).all()


def test_sparse_daily_history_is_rejected():
    hourly = _hourly_fixture(
        days=5
    )

    daily = (
        build_daily_from_hourly(
            hourly
        )
    )

    fifteen = (
        _fifteen_minute_fixture(
            days=10
        )
    )

    setup = (
        build_session_75m_from_15m(
            fifteen
        )
    )

    quality = validate_market_data(
        hourly=hourly,
        daily=daily,
        setup_75m=setup,
        requested_history_days=730,
    )

    assert quality["valid"] is False
    assert quality["reasons"]


def test_large_daily_gap_is_reported():
    hourly = _hourly_fixture(days=80)

    # Remove a block of trading sessions to simulate a provider hole.
    hourly = hourly.copy()
    local_dates = hourly["ts"].dt.tz_convert(IST).dt.date
    unique_dates = sorted(local_dates.unique())
    removed_dates = set(unique_dates[25:45])
    hourly = hourly[~local_dates.isin(removed_dates)].reset_index(drop=True)

    daily = build_daily_from_hourly(hourly)
    setup = build_session_75m_from_15m(_fifteen_minute_fixture(days=10))

    quality = validate_market_data(
        hourly=hourly,
        daily=daily,
        setup_75m=setup,
        requested_history_days=120,
    )

    assert quality["valid"] is False
    assert quality["large_daily_gaps"]
    assert quality["large_daily_gaps"][0]["gap_days"] > 14
