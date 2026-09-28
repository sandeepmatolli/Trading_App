from __future__ import annotations

from copy import deepcopy
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from market_data.groww_fetch import (
    build_daily_from_hourly,
    build_session_75m_from_15m,
    resample_daily_to_higher,
)


PRICE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
)


def _confirmed_adjustment_events(
    reconciliation: Optional[Dict],
) -> Tuple[List[Dict], List[str]]:
    """
    Convert CONFIRMED reconciliation matches into deterministic back-adjustment
    events.

    Safety rules:
    - only reconciliation status CONFIRMED is accepted;
    - only match status CONFIRMED is accepted;
    - an explicit ex_date is required;
    - factor must be > 0;
    - price history is adjusted strictly BEFORE ex_date.
    """
    profile = (
        reconciliation
        if isinstance(reconciliation, dict)
        else {}
    )

    warnings: List[str] = []

    if profile.get("status") != "CONFIRMED":
        return [], warnings

    events: List[Dict] = []

    for match in profile.get("matches", []) or []:
        if not isinstance(match, dict):
            continue

        if match.get("status") != "CONFIRMED":
            continue

        ex_date = match.get("ex_date")
        if not ex_date:
            warnings.append(
                "Confirmed corporate-action match skipped because ex_date "
                "was unavailable."
            )
            continue

        parsed_ex_date = pd.to_datetime(
            ex_date,
            errors="coerce",
        )
        if pd.isna(parsed_ex_date):
            warnings.append(
                "Confirmed corporate-action match skipped because ex_date "
                f"could not be parsed: {ex_date!r}."
            )
            continue

        try:
            factor = float(
                match.get("official_factor")
            )
        except (TypeError, ValueError):
            warnings.append(
                "Confirmed corporate-action match skipped because official "
                "factor was missing or invalid."
            )
            continue

        if factor <= 0:
            warnings.append(
                "Confirmed corporate-action match skipped because official "
                f"factor was not positive: {factor!r}."
            )
            continue

        events.append(
            {
                "ex_date": parsed_ex_date.date().isoformat(),
                "factor": factor,
                "source_id": match.get("source_id"),
                "source_type": match.get("source_type"),
                "action_type": match.get("action_type"),
                "ratio": match.get("ratio"),
                "title": match.get("title", ""),
                "boundary_ts": match.get("boundary_ts"),
                "confidence": match.get("confidence"),
                "reconciliation_status": match.get("status"),
            }
        )

    events.sort(
        key=lambda item: item["ex_date"]
    )

    return events, warnings


def _ensure_timestamp_series(
    frame: pd.DataFrame,
) -> pd.Series:
    if "ts" not in frame.columns:
        raise ValueError(
            "Corporate-action adjustment requires a 'ts' column."
        )

    ts = pd.to_datetime(
        frame["ts"],
        errors="coerce",
    )

    if ts.isna().any():
        raise ValueError(
            "Corporate-action adjustment found unparseable timestamps."
        )

    return ts


def apply_confirmed_price_adjustments(
    frame: Optional[pd.DataFrame],
    adjustment_events: Sequence[Dict],
) -> Tuple[pd.DataFrame, Dict]:
    """
    Return a NEW price-adjusted frame.

    Raw input is never mutated.

    Back-adjustment convention:
      for each confirmed event with ex_date D and factor F,
      multiply OHLC for all rows with trading date < D by F.

    Multiple events compound naturally. Rows before two later confirmed
    actions receive the product of both factors.

    V1 deliberately leaves volume/open_interest unchanged because their
    provider adjustment semantics are not yet independently verified.
    """
    if frame is None:
        return pd.DataFrame(), {
            "input_rows": 0,
            "output_rows": 0,
            "adjusted_rows": 0,
            "events_applied": 0,
        }

    adjusted = frame.copy(deep=True)

    if adjusted.empty or not adjustment_events:
        return adjusted, {
            "input_rows": int(len(adjusted)),
            "output_rows": int(len(adjusted)),
            "adjusted_rows": 0,
            "events_applied": 0,
        }

    missing_price = [
        column
        for column in PRICE_COLUMNS
        if column not in adjusted.columns
    ]
    if missing_price:
        raise ValueError(
            "Corporate-action adjustment missing OHLC columns: "
            + ", ".join(missing_price)
        )

    ts = _ensure_timestamp_series(adjusted)
    trading_dates = ts.dt.date

    cumulative_factor = pd.Series(
        1.0,
        index=adjusted.index,
        dtype="float64",
    )

    applied_event_count = 0

    for event in adjustment_events:
        ex_date = pd.Timestamp(
            event["ex_date"]
        ).date()
        factor = float(
            event["factor"]
        )

        mask = trading_dates < ex_date
        if mask.any():
            cumulative_factor.loc[mask] = (
                cumulative_factor.loc[mask]
                * factor
            )
            applied_event_count += 1

    adjusted_mask = cumulative_factor != 1.0

    for column in PRICE_COLUMNS:
        numeric = pd.to_numeric(
            adjusted[column],
            errors="coerce",
        )
        adjusted[column] = (
            numeric
            * cumulative_factor
        )

    adjusted["price_adjustment_factor"] = (
        cumulative_factor
    )
    adjusted["is_price_adjusted"] = (
        adjusted_mask
    )

    return adjusted, {
        "input_rows": int(len(frame)),
        "output_rows": int(len(adjusted)),
        "adjusted_rows": int(adjusted_mask.sum()),
        "events_applied": int(applied_event_count),
        "minimum_factor": float(
            cumulative_factor.min()
        ),
        "maximum_factor": float(
            cumulative_factor.max()
        ),
    }


def build_adjusted_market_views(
    market: Dict,
    reconciliation: Optional[Dict],
) -> Dict:
    """
    Build a separate adjusted analysis bundle from the raw Groww bundle.

    Nothing in `market` is changed.

    Derived architecture:
      raw hourly_full
          -> adjusted hourly_full
          -> adjusted Daily
          -> adjusted Weekly / Monthly

      raw 15m
          -> adjusted 15m
          -> adjusted fixed 75m

      raw recent 1H
          -> adjusted recent 1H

    Market-data quality is preserved from the raw provider data because row
    presence/completeness is not changed by price back-adjustment.
    """
    events, warnings = _confirmed_adjustment_events(
        reconciliation
    )

    metadata = {
        "enabled": True,
        "applied": False,
        "policy": (
            "confirmed_ex_date_price_only_back_adjustment"
        ),
        "raw_provider_data_preserved": True,
        "volume_adjusted": False,
        "open_interest_adjusted": False,
        "adjustment_event_count": len(events),
        "adjustment_events": deepcopy(events),
        "warnings": list(warnings),
        "timeframes": {},
    }

    if not events:
        reason = (
            "No CONFIRMED corporate action with an explicit ex_date was "
            "available."
        )
        metadata["reason"] = reason

        return {
            "applied": False,
            "metadata": metadata,
            "hourly_full": market.get(
                "hourly_full",
                pd.DataFrame(),
            ).copy(deep=True),
            "hourly": market.get(
                "hourly",
                pd.DataFrame(),
            ).copy(deep=True),
            "fifteen_minute": market.get(
                "fifteen_minute",
                pd.DataFrame(),
            ).copy(deep=True),
            "setup_75m": market.get(
                "setup_75m",
                pd.DataFrame(),
            ).copy(deep=True),
            "daily": market.get(
                "daily",
                pd.DataFrame(),
            ).copy(deep=True),
            "weekly": market.get(
                "weekly",
                pd.DataFrame(),
            ).copy(deep=True),
            "monthly": market.get(
                "monthly",
                pd.DataFrame(),
            ).copy(deep=True),
            "quality": deepcopy(
                market.get("quality", {})
            ),
        }

    adjusted_hourly_full, hourly_full_meta = (
        apply_confirmed_price_adjustments(
            market.get("hourly_full"),
            events,
        )
    )

    adjusted_hourly, hourly_meta = (
        apply_confirmed_price_adjustments(
            market.get("hourly"),
            events,
        )
    )

    adjusted_fifteen, fifteen_meta = (
        apply_confirmed_price_adjustments(
            market.get("fifteen_minute"),
            events,
        )
    )

    adjusted_daily = build_daily_from_hourly(
        adjusted_hourly_full
    )
    adjusted_weekly = resample_daily_to_higher(
        adjusted_daily,
        "W-FRI",
    )
    adjusted_monthly = resample_daily_to_higher(
        adjusted_daily,
        "ME",
    )
    adjusted_setup_75m = (
        build_session_75m_from_15m(
            adjusted_fifteen
        )
    )

    # Re-annotate derived bars with whether any component source was adjusted.
    # Daily/75m already contain adjusted OHLC, so this flag is informational.
    for frame in (
        adjusted_daily,
        adjusted_weekly,
        adjusted_monthly,
        adjusted_setup_75m,
    ):
        if frame is not None and not frame.empty:
            frame["analysis_series"] = (
                "corporate_action_adjusted"
            )

    if (
        adjusted_hourly_full is not None
        and not adjusted_hourly_full.empty
    ):
        adjusted_hourly_full[
            "analysis_series"
        ] = "corporate_action_adjusted"

    if (
        adjusted_hourly is not None
        and not adjusted_hourly.empty
    ):
        adjusted_hourly[
            "analysis_series"
        ] = "corporate_action_adjusted"

    if (
        adjusted_fifteen is not None
        and not adjusted_fifteen.empty
    ):
        adjusted_fifteen[
            "analysis_series"
        ] = "corporate_action_adjusted"

    metadata["timeframes"] = {
        "hourly_full": hourly_full_meta,
        "hourly": hourly_meta,
        "fifteen_minute": fifteen_meta,
        "daily": {
            "input_rows": int(
                len(market.get(
                    "daily",
                    pd.DataFrame(),
                ))
            ),
            "output_rows": int(
                len(adjusted_daily)
            ),
        },
        "setup_75m": {
            "input_rows": int(
                len(market.get(
                    "setup_75m",
                    pd.DataFrame(),
                ))
            ),
            "output_rows": int(
                len(adjusted_setup_75m)
            ),
        },
        "weekly": {
            "output_rows": int(
                len(adjusted_weekly)
            ),
        },
        "monthly": {
            "output_rows": int(
                len(adjusted_monthly)
            ),
        },
    }

    total_adjusted_rows = sum(
        int(
            metadata["timeframes"][name].get(
                "adjusted_rows",
                0,
            )
        )
        for name in (
            "hourly_full",
            "hourly",
            "fifteen_minute",
        )
    )

    metadata["applied"] = (
        total_adjusted_rows > 0
    )
    metadata[
        "total_source_rows_price_adjusted"
    ] = total_adjusted_rows

    if not metadata["applied"]:
        metadata["warnings"].append(
            "A CONFIRMED corporate action existed, but no source rows fell "
            "strictly before its ex_date."
        )

    return {
        "applied": bool(
            metadata["applied"]
        ),
        "metadata": metadata,
        "hourly_full": adjusted_hourly_full,
        "hourly": adjusted_hourly,
        "fifteen_minute": adjusted_fifteen,
        "setup_75m": adjusted_setup_75m,
        "daily": adjusted_daily,
        "weekly": adjusted_weekly,
        "monthly": adjusted_monthly,
        "quality": deepcopy(
            market.get("quality", {})
        ),
    }
