from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from market_data.groww_fetch import (
    _repair_hourly_gaps,
    build_daily_from_hourly,
    build_session_75m_from_15m,
    validate_market_data,
)


IST = ZoneInfo("Asia/Kolkata")


def _recent_business_dates(days: int) -> list[pd.Timestamp]:
    end = pd.Timestamp.now(tz=IST).normalize()
    return list(pd.bdate_range(end=end, periods=days, tz=IST))


def _hourly_fixture(days: int = 80) -> pd.DataFrame:
    rows = []
    dates = _recent_business_dates(days)

    for trading_index, session in enumerate(dates):
        for hour in [9, 10, 11, 12, 13, 14]:
            ts = session.replace(hour=hour, minute=0)
            base = 100 + trading_index
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

    return pd.DataFrame(rows)


def _fifteen_minute_fixture(
    days: int = 20,
    include_close_marker: bool = False,
) -> pd.DataFrame:
    rows = []
    dates = _recent_business_dates(days)

    for trading_index, session in enumerate(dates):
        start = session.replace(hour=9, minute=15)

        for index in range(25):
            base = 200 + trading_index + index / 100
            rows.append(
                {
                    "ts": start + timedelta(minutes=15 * index),
                    "open": base,
                    "high": base + 1,
                    "low": base - 1,
                    "close": base + 0.5,
                    "volume": 100,
                    "open_interest": pd.NA,
                }
            )

        if include_close_marker:
            marker_ts = session.replace(hour=15, minute=30)
            rows.append(
                {
                    "ts": marker_ts,
                    "open": 999,
                    "high": 999,
                    "low": 999,
                    "close": 999,
                    "volume": pd.NA,
                    "open_interest": pd.NA,
                }
            )

    return pd.DataFrame(rows)


def _daily_frame_from_dates(dates: list[pd.Timestamp]) -> pd.DataFrame:
    rows = []
    for index, ts in enumerate(dates):
        base = 100 + index / 10
        rows.append(
            {
                "ts": ts.normalize(),
                "open": base,
                "high": base + 2,
                "low": base - 1,
                "close": base + 1,
                "volume": 1000,
                "open_interest": pd.NA,
                "source_bars": 6,
            }
        )
    return pd.DataFrame(rows)


def test_daily_is_derived_from_hourly_and_keeps_0900_label():
    hourly = _hourly_fixture(days=80)
    daily = build_daily_from_hourly(hourly)

    assert len(daily) == 80
    assert (daily["source_bars"] == 6).all()


def test_75m_has_five_fixed_bars_per_complete_day():
    fifteen = _fifteen_minute_fixture(days=10)
    setup = build_session_75m_from_15m(fifteen)

    assert len(setup) == 50
    assert (setup["source_bars"] == 5).all()
    assert (setup["is_usable"] == True).all()  # noqa: E712

    first_day = setup.iloc[:5]
    assert [ts.strftime("%H:%M") for ts in first_day["ts"]] == [
        "09:15",
        "10:30",
        "11:45",
        "13:00",
        "14:15",
    ]


def test_sparse_15m_does_not_shift_75m_windows():
    session = _recent_business_dates(1)[0]
    full = _fifteen_minute_fixture(days=1)

    missing_ts = session.replace(hour=9, minute=30)
    sparse = full[full["ts"] != missing_ts].reset_index(drop=True)

    setup = build_session_75m_from_15m(sparse)

    assert len(setup) == 5
    assert setup.iloc[0]["ts"].strftime("%H:%M") == "09:15"
    assert setup.iloc[0]["source_bars"] == 4
    assert setup.iloc[1]["ts"].strftime("%H:%M") == "10:30"
    assert setup.iloc[1]["source_bars"] == 5


def test_1530_nan_marker_is_excluded_from_75m():
    fifteen = _fifteen_minute_fixture(days=1, include_close_marker=True)
    setup = build_session_75m_from_15m(fifteen)

    assert len(setup) == 5
    assert setup.iloc[-1]["ts"].strftime("%H:%M") == "14:15"
    assert setup.iloc[-1]["high"] < 999


def test_duplicate_15m_timestamp_does_not_double_count_source_bars():
    fifteen = _fifteen_minute_fixture(days=1)
    duplicate = fifteen.iloc[[0]].copy()
    duplicate["close"] = duplicate["close"] + 10
    combined = pd.concat([fifteen, duplicate], ignore_index=True)

    setup = build_session_75m_from_15m(combined)

    assert len(setup) == 5
    assert setup.iloc[0]["source_bars"] == 5


def test_sparse_daily_history_is_rejected():
    hourly = _hourly_fixture(days=5)
    daily = build_daily_from_hourly(hourly)
    fifteen = _fifteen_minute_fixture(days=20)
    setup = build_session_75m_from_15m(fifteen)

    quality = validate_market_data(
        hourly=hourly,
        daily=daily,
        setup_75m=setup,
        requested_history_days=730,
        fifteen_minute=fifteen,
        setup_history_days=30,
    )

    assert quality["valid"] is False
    assert any("Daily history too sparse" in reason for reason in quality["reasons"])


def test_good_recent_data_is_candidate_eligible():
    hourly = _hourly_fixture(days=80)
    daily = build_daily_from_hourly(hourly)
    fifteen = _fifteen_minute_fixture(days=20, include_close_marker=True)
    setup = build_session_75m_from_15m(fifteen)

    quality = validate_market_data(
        hourly=hourly,
        daily=daily,
        setup_75m=setup,
        requested_history_days=120,
        fifteen_minute=fifteen,
        setup_history_days=30,
    )

    assert quality["valid"] is True
    assert quality["candidate_eligible"] is True
    assert quality["source_15m_marker_rows"] == 20
    assert quality["setup_75m_usable_rows"] == 100
    assert quality["expected_setup_sessions"] == 20
    assert quality["expected_setup_75m_windows"] == 100
    assert quality["setup_75m_window_coverage_ratio"] == 1.0
    assert quality["setup_75m_usable_expected_ratio"] == 1.0



def test_setup_ratios_never_exceed_one_when_requested_cutoff_is_shorter_than_observed_setup_span():
    hourly = _hourly_fixture(days=80)
    daily = build_daily_from_hourly(hourly)
    fifteen = _fifteen_minute_fixture(days=20)
    setup = build_session_75m_from_15m(fifteen)

    # Deliberately pass a shorter nominal lookback than the observed setup
    # frame. The denominator must be derived from the actual setup span plus
    # Daily session dates, not from an intraday cutoff that can under-count the
    # first session and produce impossible ratios above 1.0.
    quality = validate_market_data(
        hourly=hourly,
        daily=daily,
        setup_75m=setup,
        requested_history_days=120,
        fifteen_minute=fifteen,
        setup_history_days=1,
    )

    assert quality["expected_setup_sessions"] == 20
    assert quality["expected_setup_75m_windows"] == 100
    assert quality["setup_75m_window_coverage_ratio"] == 1.0
    assert quality["setup_75m_usable_expected_ratio"] == 1.0
    assert quality["setup_75m_window_coverage_ratio"] <= 1.0
    assert quality["setup_75m_usable_expected_ratio"] <= 1.0

def test_low_75m_source_completeness_blocks_candidate():
    hourly = _hourly_fixture(days=80)
    daily = build_daily_from_hourly(hourly)
    fifteen = _fifteen_minute_fixture(days=20)

    # Keep only one source candle in each fixed 75m window.
    local = fifteen["ts"].dt.tz_convert(IST)
    keep_times = {"09:15", "10:30", "11:45", "13:00", "14:15"}
    thin = fifteen[
        local.dt.strftime("%H:%M").isin(keep_times)
    ].reset_index(drop=True)
    setup = build_session_75m_from_15m(thin)

    quality = validate_market_data(
        hourly=hourly,
        daily=daily,
        setup_75m=setup,
        requested_history_days=120,
        fifteen_minute=thin,
        setup_history_days=30,
    )

    assert quality["valid"] is False
    assert quality["setup_75m_usable_rows"] == 0
    assert any("Usable 75m coverage is too low" in reason for reason in quality["reasons"])


def test_off_grid_15m_timestamp_prevents_candidate_grade():
    hourly = _hourly_fixture(days=80)
    daily = build_daily_from_hourly(hourly)
    fifteen = _fifteen_minute_fixture(days=20)

    off_grid = fifteen.iloc[[0]].copy()
    off_grid["ts"] = off_grid["ts"] + pd.Timedelta(minutes=5)
    fifteen = pd.concat([fifteen, off_grid], ignore_index=True)
    setup = build_session_75m_from_15m(fifteen)

    quality = validate_market_data(
        hourly=hourly,
        daily=daily,
        setup_75m=setup,
        requested_history_days=120,
        fifteen_minute=fifteen,
        setup_history_days=30,
    )

    assert quality["valid"] is True
    assert quality["candidate_eligible"] is False
    assert quality["source_15m_off_grid_rows"] == 1


def test_old_daily_gap_is_warning_not_fatal_when_recent_window_is_clean():
    dates = _recent_business_dates(320)
    # Create an old >14 calendar-day gap by removing ten old business days.
    del dates[20:30]
    daily = _daily_frame_from_dates(dates)

    hourly = _hourly_fixture(days=80)
    fifteen = _fifteen_minute_fixture(days=20)
    setup = build_session_75m_from_15m(fifteen)

    quality = validate_market_data(
        hourly=hourly,
        daily=daily,
        setup_75m=setup,
        requested_history_days=500,
        fifteen_minute=fifteen,
        setup_history_days=30,
    )

    assert quality["valid"] is True
    assert quality["older_large_daily_gaps"]
    assert not quality["recent_large_daily_gaps"]
    assert any("Older Daily history" in warning for warning in quality["warnings"])


def test_recent_daily_gap_is_fatal():
    dates = _recent_business_dates(100)
    del dates[-30:-15]
    daily = _daily_frame_from_dates(dates)

    hourly = _hourly_fixture(days=80)
    fifteen = _fifteen_minute_fixture(days=20)
    setup = build_session_75m_from_15m(fifteen)

    quality = validate_market_data(
        hourly=hourly,
        daily=daily,
        setup_75m=setup,
        requested_history_days=120,
        fifteen_minute=fifteen,
        setup_history_days=30,
    )

    assert quality["valid"] is False
    assert quality["recent_large_daily_gaps"]
    assert any("Recent Daily history" in reason for reason in quality["reasons"])


def test_stale_daily_data_is_fatal():
    dates = list(
        pd.bdate_range(
            end=pd.Timestamp.now(tz=IST).normalize() - pd.Timedelta(days=30),
            periods=80,
            tz=IST,
        )
    )
    daily = _daily_frame_from_dates(dates)
    hourly = _hourly_fixture(days=80)
    fifteen = _fifteen_minute_fixture(days=20)
    setup = build_session_75m_from_15m(fifteen)

    quality = validate_market_data(
        hourly=hourly,
        daily=daily,
        setup_75m=setup,
        requested_history_days=120,
        fifteen_minute=fifteen,
        setup_history_days=30,
    )

    assert quality["valid"] is False
    assert any("Latest Daily candle" in reason for reason in quality["reasons"])


class _EmptyRepairGroww:
    EXCHANGE_NSE = "NSE"
    SEGMENT_CASH = "CASH"
    CANDLE_INTERVAL_HOUR_1 = "1hour"

    def get_historical_candles(self, **kwargs):
        return {"candles": []}


def test_zero_result_gap_repair_does_not_fabricate_candles():
    hourly = _hourly_fixture(days=80).copy()
    local_dates = hourly["ts"].dt.tz_convert(IST).dt.date
    unique_dates = sorted(local_dates.unique())
    removed_dates = set(unique_dates[25:45])
    with_gap = hourly[~local_dates.isin(removed_dates)].reset_index(drop=True)

    repaired = _repair_hourly_gaps(
        _EmptyRepairGroww(),
        "NSE-TEST",
        with_gap,
    )

    assert len(repaired) == len(with_gap)
    assert repaired["ts"].equals(with_gap["ts"])