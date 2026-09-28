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


def _clean_optional_text(value) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    return str(value).strip()


def _company_aliases(
    row: pd.Series,
    instrument: Dict,
) -> List[str]:
    values: List[str] = []

    for column in (
        "Name",
        "Company Name",
        "Company",
        "Stock Name",
    ):
        text = _clean_optional_text(
            row.get(column)
        )
        if text:
            values.append(text)

    instrument_name = _clean_optional_text(
        instrument.get("name")
    )
    if instrument_name:
        values.append(instrument_name)

    return list(dict.fromkeys(values))


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


def _build_smc_profile(
    market: Dict,
    quality: Dict,
) -> Dict:
    if not quality.get(
        "valid",
        False,
    ):
        return {
            "data_quality": quality
        }

    return build_technical_profile(
        daily=market["daily"],
        setup_75m=market["setup_75m"],
        weekly=market["weekly"],
        monthly=market["monthly"],
        one_hour=market["hourly"],
        data_quality=quality,
    )


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
        raise ValueError(
            "--max-symbols must be greater than zero."
        )

    requested = _parse_symbol_argument(
        args.symbols
    )

    if requested:
        available = set(all_symbols)
        unknown = [
            symbol
            for symbol in requested
            if symbol not in available
        ]

        if unknown:
            print(
                "Warning: symbols not found in Screener NSE universe: "
                f"{unknown}"
            )

        symbols = [
            symbol
            for symbol in requested
            if symbol in available
        ]
    else:
        symbols = all_symbols[
            : args.max_symbols
        ]

    if not symbols:
        raise ValueError(
            "No NSE symbols selected for analysis."
        )

    from market_data.corporate_action_adjustment import (
        build_adjusted_market_views,
    )
    from market_data.groww_auth import get_groww_api
    from market_data.groww_fetch import (
        fetch_market_timeframes,
    )
    from news.corporate_action_reconciliation import (
        reconcile_corporate_action_guard,
    )
    from news.news_engine import (
        event_profile_for_symbol,
        fetch_nse_announcements,
    )
    from news.nse_historical_corporate_actions import (
        fetch_historical_actions_for_discontinuities,
    )

    groww = get_groww_api()
    news_bundle = (
        fetch_nse_announcements()
    )

    print(
        "NSE event evidence: "
        f"available={news_bundle.get('available')} | "
        f"candidate_eligible={news_bundle.get('candidate_eligible')} | "
        f"items={len(news_bundle.get('items', []))} | "
        f"blockers={len(news_bundle.get('candidate_blockers', []))}"
    )

    for source in news_bundle.get(
        "sources",
        [],
    ):
        print(
            f"  {source.get('source_id')}: "
            f"available={source.get('available')} | "
            f"candidate_eligible={source.get('candidate_eligible')} | "
            f"entries={source.get('entry_count')} | "
            f"latest={source.get('latest_item_at')}"
        )

    indexed = (
        df_fund.assign(
            __nse=(
                df_fund["NSE Code"]
                .astype("string")
                .str.strip()
                .str.upper()
            )
        )
        .dropna(
            subset=["__nse"]
        )
        .drop_duplicates(
            subset=["__nse"],
            keep="first",
        )
        .set_index("__nse")
    )

    results: List[Dict] = []

    for symbol in symbols:
        print(
            f"\nProcessing {symbol} ..."
        )

        try:
            market = fetch_market_timeframes(
                groww,
                trading_symbol=symbol,
                hourly_history_days=DEFAULT_HOURLY_HISTORY_DAYS,
                setup_15m_days=DEFAULT_SETUP_15M_DAYS,
            )

            quality = market[
                "quality"
            ]

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

            raw_tech_profile = (
                _build_smc_profile(
                    market,
                    quality,
                )
            )

            if quality.get(
                "valid",
                False,
            ):
                closed_guard = (
                    raw_tech_profile.get(
                        "closed_bar_guard",
                        {},
                    )
                )

                print(
                    f"{symbol}: SMC closed bars "
                    f"daily={closed_guard.get('daily_closed_rows')}/"
                    f"{closed_guard.get('daily_input_rows')} | "
                    f"75m={closed_guard.get('setup_75m_closed_usable_rows')}/"
                    f"{closed_guard.get('setup_75m_input_rows')} | "
                    f"1H={closed_guard.get('one_hour_closed_rows')}/"
                    f"{closed_guard.get('one_hour_input_rows')} | "
                    f"weekly={closed_guard.get('weekly_closed_rows')} | "
                    f"monthly={closed_guard.get('monthly_closed_rows')}"
                )

            fund_row = indexed.loc[
                symbol
            ]

            if isinstance(
                fund_row,
                pd.DataFrame,
            ):
                fund_row = (
                    fund_row.iloc[0]
                )

            fund_profile = (
                _fundamental_profile(
                    fund_row
                )
            )

            aliases = (
                _company_aliases(
                    fund_row,
                    market["instrument"],
                )
            )

            company_name = (
                aliases[0]
                if aliases
                else ""
            )

            event_profile = (
                event_profile_for_symbol(
                    symbol,
                    news_bundle,
                    company_name=company_name,
                    aliases=aliases,
                )
            )

            print(
                f"{symbol}: event evidence "
                f"available={event_profile.get('available')} | "
                f"candidate_eligible={event_profile.get('candidate_eligible')} | "
                f"matches={event_profile.get('event_count')} | "
                f"sentiment={event_profile.get('sentiment')} | "
                f"type={event_profile.get('event_type')}"
            )

            raw_corporate_guard = (
                raw_tech_profile.get(
                    "corporate_action_guard",
                    {},
                )
            )

            historical_ca = (
                fetch_historical_actions_for_discontinuities(
                    symbol=symbol,
                    corporate_action_guard=raw_corporate_guard,
                )
            )

            if historical_ca.get(
                "requested"
            ):
                print(
                    f"{symbol}: historical NSE corporate actions "
                    f"status={historical_ca.get('status')} | "
                    f"available={historical_ca.get('available')} | "
                    f"complete={historical_ca.get('complete')} | "
                    f"actions={len(historical_ca.get('actions', []))}"
                )

            corporate_action_reconciliation = (
                reconcile_corporate_action_guard(
                    symbol=symbol,
                    corporate_action_guard=raw_corporate_guard,
                    event_profile=event_profile,
                    historical_action_result=historical_ca,
                )
            )

            print(
                f"{symbol}: corporate-action reconciliation "
                f"status={corporate_action_reconciliation.get('status')} | "
                f"discontinuities="
                f"{corporate_action_reconciliation.get('discontinuity_count')} | "
                f"official_actions="
                f"{corporate_action_reconciliation.get('official_action_candidate_count')} | "
                f"historical_actions="
                f"{corporate_action_reconciliation.get('historical_action_candidate_count')} | "
                f"confirmed="
                f"{corporate_action_reconciliation.get('confirmed_count')} | "
                f"probable="
                f"{corporate_action_reconciliation.get('probable_count')} | "
                f"unverified="
                f"{corporate_action_reconciliation.get('unverified_count')}"
            )

            adjusted_market = (
                build_adjusted_market_views(
                    market,
                    corporate_action_reconciliation,
                )
            )

            adjustment_metadata = (
                adjusted_market[
                    "metadata"
                ]
            )

            analysis_tech_profile = (
                raw_tech_profile
            )
            analysis_series = (
                "raw_guarded"
            )
            adjustment_accepted = False
            residual_discontinuity = None

            if (
                quality.get(
                    "valid",
                    False,
                )
                and adjusted_market.get(
                    "applied",
                    False,
                )
            ):
                candidate_adjusted_profile = (
                    _build_smc_profile(
                        adjusted_market,
                        quality,
                    )
                )

                adjusted_guard = (
                    candidate_adjusted_profile.get(
                        "corporate_action_guard",
                        {},
                    )
                )

                residual_discontinuity = bool(
                    adjusted_guard.get(
                        "detected",
                        False,
                    )
                )

                if not residual_discontinuity:
                    analysis_tech_profile = (
                        candidate_adjusted_profile
                    )
                    analysis_series = (
                        "corporate_action_adjusted"
                    )
                    adjustment_accepted = True
                else:
                    analysis_series = (
                        "raw_guarded_fallback"
                    )
                    adjustment_metadata[
                        "warnings"
                    ].append(
                        "Adjusted SMC still detected a suspicious price "
                        "discontinuity. Raw guarded SMC profile was retained."
                    )

            adjustment_metadata[
                "accepted_for_smc"
            ] = adjustment_accepted

            adjustment_metadata[
                "analysis_series"
            ] = analysis_series

            adjustment_metadata[
                "residual_discontinuity_after_adjustment"
            ] = residual_discontinuity

            adjustment_metadata[
                "raw_discontinuity_detected"
            ] = bool(
                raw_corporate_guard.get(
                    "detected",
                    False,
                )
            )

            analysis_tech_profile[
                "analysis_series"
            ] = analysis_series

            analysis_tech_profile[
                "price_adjustment"
            ] = adjustment_metadata

            analysis_tech_profile[
                "raw_corporate_action_guard"
            ] = raw_corporate_guard

            analysis_tech_profile[
                "historical_corporate_actions"
            ] = historical_ca

            analysis_tech_profile[
                "corporate_action_reconciliation"
            ] = (
                corporate_action_reconciliation
            )

            print(
                f"{symbol}: price-series analysis "
                f"series={analysis_series} | "
                f"adjustment_applied={adjusted_market.get('applied')} | "
                f"accepted_for_smc={adjustment_accepted} | "
                f"source_rows_adjusted="
                f"{adjustment_metadata.get('total_source_rows_price_adjusted', 0)} | "
                f"residual_discontinuity={residual_discontinuity}"
            )

            if adjustment_accepted:
                adjusted_closed_guard = (
                    analysis_tech_profile.get(
                        "closed_bar_guard",
                        {},
                    )
                )

                print(
                    f"{symbol}: adjusted SMC history "
                    f"daily={analysis_tech_profile.get('daily_analysis_rows')} | "
                    f"weekly={analysis_tech_profile.get('weekly_analysis_rows')} | "
                    f"monthly={analysis_tech_profile.get('monthly_analysis_rows')} | "
                    f"closed_daily={adjusted_closed_guard.get('daily_closed_rows')}"
                )

            result = evaluate_candidate(
                symbol,
                fund_profile,
                analysis_tech_profile,
                event_profile,
            )

            result[
                "market_data_quality"
            ] = quality

            result[
                "instrument"
            ] = market[
                "instrument"
            ]

            result[
                "event_profile"
            ] = event_profile

            result[
                "historical_corporate_actions"
            ] = historical_ca

            result[
                "corporate_action_reconciliation"
            ] = (
                corporate_action_reconciliation
            )

            result[
                "price_adjustment"
            ] = adjustment_metadata

            result[
                "analysis_series"
            ] = analysis_series

            if quality.get(
                "valid",
                False,
            ):
                result[
                    "technical_profile"
                ] = analysis_tech_profile

            results.append(
                result
            )

            historical_warnings = (
                result.get(
                    "historical_warnings",
                    [],
                )
            )

            print(
                f"{symbol}: {result['decision']} | "
                f"risks={len(result['risk_flags'])} | "
                f"missing={len(result['missing_data'])} | "
                f"historical_warnings={len(historical_warnings)}"
            )

            for warning in historical_warnings:
                print(
                    "  Historical data warning: "
                    + warning
                )

        except Exception as exc:
            print(
                f"Failed processing {symbol}: {exc}"
            )

            results.append(
                {
                    "symbol": symbol,
                    "decision": "DATA_REJECT",
                    "positive_evidence": [],
                    "risk_flags": [
                        f"Pipeline error: {exc}"
                    ],
                    "missing_data": [],
                    "historical_warnings": [],
                    "historical_context_degraded": False,
                    "candidate_eligibility_reason": [
                        "Pipeline execution failed before candidate "
                        "evaluation: "
                        + str(exc)
                    ],
                }
            )

    destination = _save_results(
        results
    )

    candidates = [
        item
        for item in results
        if item.get(
            "decision"
        )
        == "CANDIDATE"
    ]

    print(
        "\nFinal research shortlist:"
    )

    if not candidates:
        print(
            "No CANDIDATE setups in this run."
        )
    else:
        for item in candidates:
            print(
                f"- {item['symbol']}: "
                + "; ".join(
                    item[
                        "positive_evidence"
                    ]
                )
            )

    print(
        "Full run output saved to: "
        f"{destination}"
    )


if __name__ == "__main__":
    main()
