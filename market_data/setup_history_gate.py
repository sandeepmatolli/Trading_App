"""Conservative requested-lookback check for the fixed 75-minute setup feed.

The existing density ratio describes completeness only between its first and
last returned setup bars. It must not, by itself, prove that the *requested*
lookback was fetched. This guard uses real Daily trading-session dates as its
reference, not fabricated business days or stock-exchange holiday guesses.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from config import MARKET_DATA_CANDIDATE_SETUP_USABLE_EXPECTED_RATIO

IST = ZoneInfo("Asia/Kolkata")


def apply_requested_setup_history_gate(
    quality: Dict,
    daily: pd.DataFrame,
    setup_75m: pd.DataFrame,
    requested_days: int,
    *,
    as_of: Optional[datetime] = None,
) -> Dict:
    """Annotate one market quality report; never convert invalid data to valid.

    This is an extra CANDIDATE-only check. If Daily shows trading sessions
    within the requested lookback but the setup feed omits whole early
    sessions, count them in the denominator. Source-only setup dates are also
    counted, preventing an impossible ratio above 100% where the Daily feed
    missed a day. The existing valid/reasons DATA_REJECT gate stays untouched.
    """
    if requested_days < 1:
        raise ValueError("requested_days must be positive")
    if not isinstance(quality, dict):
        raise TypeError("quality must be a dict")
    now = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.now(tz=IST)
    now = now.tz_localize(IST) if now.tzinfo is None else now.tz_convert(IST)
    cutoff = (now - pd.Timedelta(days=requested_days)).normalize()
    final_day = now.normalize()

    def _local(frame: pd.DataFrame) -> pd.Series:
        if frame is None or frame.empty or "ts" not in frame.columns:
            return pd.Series(dtype="datetime64[ns, Asia/Kolkata]")
        return pd.to_datetime(frame["ts"], errors="coerce", utc=True).dt.tz_convert(IST)

    daily_ts = _local(daily)
    setup_ts = _local(setup_75m)
    daily_days = set(daily_ts.dt.normalize().dropna())
    setup_days = set(setup_ts.dt.normalize().dropna())
    expected_days = {d for d in (daily_days | setup_days) if cutoff <= d <= final_day}
    expected_windows = 5 * len(expected_days)

    usable_slots = set()
    if setup_75m is not None and not setup_75m.empty and "ts" in setup_75m.columns:
        for i, ts in setup_ts.items():
            if pd.isna(ts):
                continue
            day = ts.normalize()
            if day not in expected_days:
                continue
            minutes = ts.hour * 60 + ts.minute - (9 * 60 + 15)
            if minutes < 0 or minutes % 75 != 0 or minutes // 75 not in range(5):
                continue
            row = setup_75m.loc[i]
            if "is_usable" in setup_75m.columns:
                usable = pd.notna(row["is_usable"]) and bool(row["is_usable"])
            elif "source_bars" in setup_75m.columns:
                usable = pd.notna(row["source_bars"]) and float(row["source_bars"]) >= 3
            else:
                usable = True
            if usable:
                usable_slots.add((day, minutes // 75))

    usable = len(usable_slots)
    ratio = usable / expected_windows if expected_windows else 0.0
    quality["requested_setup_lookback_days"] = requested_days
    quality["requested_setup_reference_sessions"] = len(expected_days)
    quality["requested_setup_expected_windows"] = expected_windows
    quality["requested_setup_usable_windows"] = usable
    quality["requested_setup_usable_ratio"] = round(ratio, 4)
    quality["requested_setup_reference"] = "union_of_real_daily_and_setup_sessions_within_requested_lookback"
    threshold = MARKET_DATA_CANDIDATE_SETUP_USABLE_EXPECTED_RATIO
    if expected_windows == 0 or ratio < threshold:
        blocker = (
            "Requested-lookback 75m coverage is below candidate-grade: "
            f"{usable}/{expected_windows} windows ({ratio:.1%}); minimum {threshold:.1%}. "
            "The returned setup feed may be truncated even if its observed-span ratio looks complete."
        )
        blockers = quality.setdefault("candidate_blockers", [])
        if blocker not in blockers:
            blockers.append(blocker)
        features = quality.setdefault("degraded_features", [])
        if "requested_setup_history_completeness" not in features:
            features.append("requested_setup_history_completeness")
        quality["candidate_eligible"] = False
        quality["degraded"] = True
    return quality
