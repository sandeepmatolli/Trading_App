# main.py

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict

import pandas as pd

from ai.ai_validation import evaluate_candidate
from config import (
    DEFAULT_DAILY_DAYS,
    DEFAULT_FOUR_HOUR_DAYS,
    DEFAULT_MAX_SYMBOLS,
    DEFAULT_ONE_HOUR_DAYS,
    OUTPUT_DIR,
    SCREENER_CSV_PATH,
    STRICT_CORE50_DEFAULT,
    VALIDATED_CSV_PATH,
)
from fundamentals.csv_validator import validate_screener_csv
from fundamentals.master_builder import update_company_master
from fundamentals.screener_symbols import get_nse_symbols
from technical.smc_engine import build_technical_profile


def _fundamental_profile(row: pd.Series) -> Dict:
    return {
        "roce": row.get("Return on capital employed"),
        "roe": row.get("Return on equity"),
        "de_ratio": row.get("Debt to equity"),
        "altman_z": row.get("Altman Z Score"),
        "piotroski_score": row.get("Piotroski score"),
        "pledged_percentage": row.get("Pledged percentage"),
        "sales_growth_3y": row.get("Sales growth 3Years"),
        "profit_growth_3y": row.get("Profit growth 3Years"),
        "industry_group": row.get("Industry Group"),
        "industry": row.get("Industry"),
        "sector": row.get("Sector"),
    }


def _save_results(results) -> Path:
    destination = OUTPUT_DIR / "shortlist.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(results, indent=2, default=str),
        encoding="utf-8",
    )
    return destination


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NSE swing-trading research pipeline"
    )
    parser.add_argument(
        "--csv",
        default=str(SCREENER_CSV_PATH),
        help="Path to the Screener CSV export.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate fundamentals and update company master only.",
    )
    parser.add_argument(
        "--strict-core50",
        action="store_true",
        default=STRICT_CORE50_DEFAULT,
        help="Fail when any agreed Core-50 field is missing.",
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=DEFAULT_MAX_SYMBOLS,
        help="Maximum number of NSE symbols to process with Groww.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    df_fund = validate_screener_csv(
        args.csv,
        output_path=str(VALIDATED_CSV_PATH),
        strict_core50=args.strict_core50,
    )

    update_company_master(df_fund)

    symbols = get_nse_symbols(df_fund)
    if args.validate_only:
        print("Validation-only run completed successfully.")
        return

    if args.max_symbols <= 0:
        raise ValueError("--max-symbols must be greater than zero.")

    # Lazy import: validation-only mode does not need the Groww SDK.
    from market_data.groww_auth import get_groww_api
    from market_data.groww_fetch import fetch_candles, resample_ohlcv
    from news.news_engine import (
        event_profile_for_symbol,
        fetch_nse_announcements,
    )

    groww = get_groww_api()
    announcements = fetch_nse_announcements()

    indexed = (
        df_fund.assign(
            __nse=df_fund["NSE Code"].astype("string").str.strip().str.upper()
        )
        .dropna(subset=["__nse"])
        .drop_duplicates(subset=["__nse"], keep="first")
        .set_index("__nse")
    )

    results = []

    for symbol in symbols[: args.max_symbols]:
        print(f"\nProcessing {symbol} ...")

        try:
            daily = fetch_candles(
                groww,
                symbol,
                interval_min=1440,
                days_back=DEFAULT_DAILY_DAYS,
            )
            four_hour = fetch_candles(
                groww,
                symbol,
                interval_min=240,
                days_back=DEFAULT_FOUR_HOUR_DAYS,
            )
            one_hour = fetch_candles(
                groww,
                symbol,
                interval_min=60,
                days_back=DEFAULT_ONE_HOUR_DAYS,
            )

            if len(daily) < 30 or len(four_hour) < 30:
                print(
                    f"Skipping {symbol}: insufficient candle history "
                    f"(daily={len(daily)}, 4H={len(four_hour)})."
                )
                continue

            weekly = resample_ohlcv(daily, "W-FRI")
            monthly = resample_ohlcv(daily, "ME")

            tech_profile = build_technical_profile(
                daily=daily,
                four_hour=four_hour,
                weekly=weekly,
                monthly=monthly,
                one_hour=one_hour,
            )

            fund_row = indexed.loc[symbol]
            if isinstance(fund_row, pd.DataFrame):
                fund_row = fund_row.iloc[0]

            fund_profile = _fundamental_profile(fund_row)
            event_profile = event_profile_for_symbol(
                symbol,
                announcements,
            )

            result = evaluate_candidate(
                symbol,
                fund_profile,
                tech_profile,
                event_profile,
            )
            result["technical_profile"] = tech_profile
            result["event_profile"] = event_profile
            results.append(result)

            print(
                f"{symbol}: {result['decision']} | "
                f"risks={len(result['risk_flags'])} | "
                f"missing={len(result['missing_data'])}"
            )

        except Exception as exc:
            print(f"Failed processing {symbol}: {exc}")

    destination = _save_results(results)

    candidates = [
        item for item in results
        if item.get("decision") == "CANDIDATE"
    ]

    print("\nFinal research shortlist:")
    if not candidates:
        print("No CANDIDATE setups in this run.")
    else:
        for item in candidates:
            print(
                f"- {item['symbol']}: "
                + "; ".join(item["positive_evidence"])
            )

    print(f"Full run output saved to: {destination}")


if __name__ == "__main__":
    main()
