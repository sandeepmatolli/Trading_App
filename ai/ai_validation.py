from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd


FINANCIAL_KEYWORDS = {
    "bank",
    "banking",
    "finance",
    "financial",
    "nbfc",
    "insurance",
    "housing finance",
}


def _number(value) -> Optional[float]:
    if value is None or pd.isna(value):
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_financial_company(
    fund_profile: Dict,
) -> bool:
    text = " ".join(
        str(fund_profile.get(key, "") or "")
        for key in (
            "industry",
            "industry_group",
            "sector",
        )
    ).lower()

    return any(keyword in text for keyword in FINANCIAL_KEYWORDS)


def evaluate_candidate(
    symbol: str,
    fund_profile: Dict,
    tech_profile: Dict,
    event_profile: Dict,
) -> Dict:
    """
    Cross-check independent evidence streams.

    Decisions:
      DATA_REJECT -> market data is not trustworthy enough for SMC
      REJECT      -> explicit material/fundamental risk
      CANDIDATE   -> complete evidence, candidate-grade data, bullish setup
      WATCH       -> incomplete, degraded, or not-yet-confirmed setup

    This function does not place orders.
    """
    positive: List[str] = []
    risks: List[str] = []
    missing_data: List[str] = []

    data_quality = tech_profile.get("data_quality", {})

    if not data_quality.get("valid", False):
        risks.extend(
            [
                "Market data: " + str(reason)
                for reason in data_quality.get(
                    "reasons",
                    ["Unknown data-quality failure."],
                )
            ]
        )

        return {
            "symbol": symbol,
            "decision": "DATA_REJECT",
            "positive_evidence": positive,
            "risk_flags": risks,
            "missing_data": missing_data,
            "financial_sector_model_deferred": False,
        }

    candidate_data_eligible = bool(
        data_quality.get(
            "candidate_eligible",
            data_quality.get("valid", False),
        )
    )

    if not candidate_data_eligible:
        blockers = data_quality.get("candidate_blockers", [])
        if blockers:
            for blocker in blockers:
                missing_data.append(
                    "Market-data candidate gate: " + str(blocker)
                )
        else:
            missing_data.append(
                "Market data is usable for research but not candidate-grade."
            )

    is_financial = _is_financial_company(fund_profile)

    roce = _number(fund_profile.get("roce"))
    debt_to_equity = _number(fund_profile.get("de_ratio"))
    altman_z = _number(fund_profile.get("altman_z"))
    piotroski = _number(fund_profile.get("piotroski_score"))
    pledged = _number(fund_profile.get("pledged_percentage"))

    if is_financial:
        missing_data.append(
            "Dedicated bank/NBFC/financial-sector model not implemented."
        )
    else:
        if roce is None:
            missing_data.append("ROCE")
        elif roce >= 15:
            positive.append("ROCE >= 15%")
        elif roce < 10:
            risks.append("ROCE < 10%")

        if debt_to_equity is None:
            missing_data.append("Debt to equity")
        elif debt_to_equity <= 1:
            positive.append("Debt to equity <= 1")
        elif debt_to_equity > 2:
            risks.append("Debt to equity > 2")

        if altman_z is None:
            missing_data.append("Altman Z Score")
        elif altman_z < 1.8:
            risks.append("Altman Z Score < 1.8")

    if piotroski is None:
        missing_data.append("Piotroski score")
    elif piotroski >= 7:
        positive.append("Piotroski score >= 7")

    if pledged is not None and pledged > 20:
        risks.append("Promoter pledge > 20%")

    for timeframe, label in (
        ("monthly", "Monthly"),
        ("weekly", "Weekly"),
        ("daily", "Daily"),
        ("setup_75m", "75m"),
        ("one_hour", "1H"),
    ):
        if tech_profile.get(f"{timeframe}_trend") == "Bullish":
            positive.append(f"{label} structure bullish")

    bullish_structure_event = bool(
        tech_profile.get("daily_bos_bullish")
        or tech_profile.get("daily_choch_bullish")
        or tech_profile.get("setup_75m_bos_bullish")
        or tech_profile.get("setup_75m_choch_bullish")
    )

    if bullish_structure_event:
        positive.append("Bullish BOS/CHOCH evidence")

    sweep = tech_profile.get("setup_75m_liquidity_sweep")
    if sweep and sweep.get("direction") == "bullish":
        positive.append("75m bullish liquidity sweep")

    if tech_profile.get("daily_bos_bearish"):
        risks.append("Daily bearish BOS")

    if tech_profile.get("daily_choch_bearish"):
        risks.append("Daily bearish CHOCH")

    event_available = bool(event_profile.get("available", False))

    if not event_available:
        missing_data.append(
            "News/corporate-announcement source unavailable."
        )
        sentiment = "Unknown"
    else:
        sentiment = event_profile.get("sentiment", "Neutral")

    if sentiment == "Negative":
        risks.append(
            "Negative material event: "
            + str(event_profile.get("title", "")).strip()
        )
    elif sentiment == "Positive":
        positive.append(
            "Positive corporate-development event: "
            + str(event_profile.get("title", "")).strip()
        )

    hard_reject = (
        sentiment == "Negative"
        or (
            not is_financial
            and debt_to_equity is not None
            and debt_to_equity > 3
        )
    )

    bearish_daily = bool(
        tech_profile.get("daily_bos_bearish")
        or tech_profile.get("daily_choch_bearish")
    )

    bullish_context = (
        tech_profile.get("daily_trend") == "Bullish"
        and bullish_structure_event
        and not bearish_daily
    )

    evidence_complete = (
        event_available
        and not is_financial
        and candidate_data_eligible
    )

    if hard_reject:
        decision = "REJECT"
    elif bullish_context and evidence_complete:
        decision = "CANDIDATE"
    else:
        decision = "WATCH"

    return {
        "symbol": symbol,
        "decision": decision,
        "positive_evidence": positive,
        "risk_flags": risks,
        "missing_data": missing_data,
        "financial_sector_model_deferred": is_financial,
    }