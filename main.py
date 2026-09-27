from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

import pandas as pd

from ai.ai_validation import evaluate_candidate
from config import (
    DEFAULT_HOURLY_HISTORY_DAYS,
    DEFAULT_MAX_SYMBOLS,
    DEFAULT_SETUP_15M_DAYS,
    OUTPUT_DIR,
    SCREENER_CSV_PATH,
    STRICT_CORE50_DEFAULT,
    VALIDATED_CSV_PATH,
)
from fundamentals.csv_validator import validate_screener_csv
from fundamentals.master_builder import update_company_master
from fundamentals.screener_symbols import get_nse_symbols
from technical.smc_engine import build_technical_profile


def _fundamental_profile(
    row: pd.Series,
) -> Dict:
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


def _save_results(
    results: List[Dict],
) -> Path:
    destination = OUTPUT_DIR / "shortlist.json"

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination.write_text(
        json.dumps(
            results,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    return destination


def _parse_symbol_argument(
    value: str,
) -> List[str]:
    if not value.strip():
        return []

    return [
        item.strip().upper()
        for item in value.split(",")
        if item.strip()
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NSE swing-trading research pipeline"
    )

    parser.add_argument(
        "--csv",
        default=str(SCREENER_CSV_PATH),
        help="Path to Screener CSV export.",
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
        help="Maximum symbols to process when --symbols is not supplied.",
    )

    parser.add_argument(
        "--symbols",
        default="",
        help=(
            "Optional comma-separated NSE symbols. "
            "Example: --symbols 20MICRONS,RELIANCE"
        ),
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
    all_symbols = get_nse_symbols(df_fund)

    if args.validate_only:
        print("Validation-only run completed successfully.")
        return

    if args.max_symbols <= 0:
        raise ValueError("--max-symbols must be greater than zero.")

    requested = _parse_symbol_argument(args.symbols)

    if requested:
        available = set(all_symbols)
        unknown = [symbol for symbol in requested if symbol not in available]

        if unknown:
            print(
                "Warning: symbols not found in Screener NSE universe: "
                f"{unknown}"
            )

        symbols = [symbol for symbol in requested if symbol in available]
    else:
        symbols = all_symbols[: args.max_symbols]

    if not symbols:
        raise ValueError("No NSE symbols selected for analysis.")

    from market_data.groww_auth import get_groww_api
    from market_data.groww_fetch import fetch_market_timeframes
    from news.news_engine import (
        event_profile_for_symbol,
        fetch_nse_announcements,
    )

    groww = get_groww_api()
    news_bundle = fetch_nse_announcements()

    indexed = (
        df_fund.assign(
            __nse=(
                df_fund["NSE Code"]
                .astype("string")
                .str.strip()
                .str.upper()
            )
        )
        .dropna(subset=["__nse"])
        .drop_duplicates(subset=["__nse"], keep="first")
        .set_index("__nse")
    )

    results: List[Dict] = []

    for symbol in symbols:
        print(f"\nProcessing {symbol} ...")

        try:
            market = fetch_market_timeframes(
                groww,
                trading_symbol=symbol,
                hourly_history_days=DEFAULT_HOURLY_HISTORY_DAYS,
                setup_15m_days=DEFAULT_SETUP_15M_DAYS,
            )

            quality = market["quality"]

            print(
                f"{symbol}: market-data quality "
                f"valid={quality['valid']} | "
                f"candidate_eligible={quality.get('candidate_eligible')} | "
                f"daily={quality['daily_rows']} | "
                f"75m={quality['setup_75m_rows']} | "
                f"usable75m={quality.get('setup_75m_usable_rows')} | "
                f"usable_ratio={quality.get('setup_75m_usable_expected_ratio')} | "
                f"max_gap={quality['max_daily_gap_days']}"
            )

            if quality.get("valid", False):
                tech_profile = build_technical_profile(
                    daily=market["daily"],
                    setup_75m=market["setup_75m"],
                    weekly=market["weekly"],
                    monthly=market["monthly"],
                    one_hour=market["hourly"],
                    data_quality=quality,
                )
            else:
                tech_profile = {"data_quality": quality}

            fund_row = indexed.loc[symbol]

            if isinstance(fund_row, pd.DataFrame):
                fund_row = fund_row.iloc[0]

            fund_profile = _fundamental_profile(fund_row)

            event_profile = event_profile_for_symbol(
                symbol,
                news_bundle,
            )

            result = evaluate_candidate(
                symbol,
                fund_profile,
                tech_profile,
                event_profile,
            )

            result["market_data_quality"] = quality
            result["instrument"] = market["instrument"]
            result["event_profile"] = event_profile

            if quality.get("valid", False):
                result["technical_profile"] = tech_profile

            results.append(result)

            print(
                f"{symbol}: {result['decision']} | "
                f"risks={len(result['risk_flags'])} | "
                f"missing={len(result['missing_data'])}"
            )

        except Exception as exc:
            print(f"Failed processing {symbol}: {exc}")

            results.append(
                {
                    "symbol": symbol,
                    "decision": "DATA_REJECT",
                    "positive_evidence": [],
                    "risk_flags": [f"Pipeline error: {exc}"],
                    "missing_data": [],
                }
            )

    destination = _save_results(results)

    candidates = [
        item
        for item in results
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

    print("Full run output saved to: " f"{destination}")


if __name__ == "__main__":
    main()