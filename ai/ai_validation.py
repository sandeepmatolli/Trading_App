# ai/ai_validation.py

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


def _is_financial_company(fund_profile: Dict) -> bool:
    text = " ".join(
        str(fund_profile.get(key, "") or "")
        for key in ("industry", "industry_group", "sector")
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

    This function intentionally returns CANDIDATE/WATCH/REJECT rather than
    placing or recommending an automatic order. Human approval remains required.
    """
    positive: List[str] = []
    risks: List[str] = []
    missing_data: List[str] = []

    is_financial = _is_financial_company(fund_profile)

    roce = _number(fund_profile.get("roce"))
    debt_to_equity = _number(fund_profile.get("de_ratio"))
    altman_z = _number(fund_profile.get("altman_z"))
    piotroski = _number(fund_profile.get("piotroski_score"))
    pledged = _number(fund_profile.get("pledged_percentage"))

    if roce is None:
        missing_data.append("ROCE")
    elif roce >= 15:
        positive.append("ROCE >= 15%")
    elif roce < 10 and not is_financial:
        risks.append("ROCE < 10%")

    if debt_to_equity is None:
        missing_data.append("Debt to equity")
    elif not is_financial:
        if debt_to_equity <= 1:
            positive.append("Debt to equity <= 1")
        elif debt_to_equity > 2:
            risks.append("Debt to equity > 2")

    if altman_z is None:
        missing_data.append("Altman Z Score")
    elif not is_financial and altman_z < 1.8:
        risks.append("Altman Z Score < 1.8")

    if piotroski is None:
        missing_data.append("Piotroski score")
    elif piotroski >= 7:
        positive.append("Piotroski score >= 7")

    if pledged is not None and pledged > 20:
        risks.append("Promoter pledge > 20%")

    if tech_profile.get("monthly_trend") == "Bullish":
        positive.append("Monthly structure bullish")

    if tech_profile.get("weekly_trend") == "Bullish":
        positive.append("Weekly structure bullish")

    if tech_profile.get("daily_trend") == "Bullish":
        positive.append("Daily structure bullish")

    bullish_structure_event = bool(
        tech_profile.get("daily_bos_bullish")
        or tech_profile.get("daily_choch_bullish")
        or tech_profile.get("four_hour_bos_bullish")
        or tech_profile.get("four_hour_choch_bullish")
    )
    if bullish_structure_event:
        positive.append("Bullish BOS/CHOCH evidence")

    sweep = tech_profile.get("four_hour_liquidity_sweep")
    if sweep and sweep.get("direction") == "bullish":
        positive.append("4H bullish liquidity sweep")

    if tech_profile.get("daily_bos_bearish"):
        risks.append("Daily bearish BOS")
    if tech_profile.get("daily_choch_bearish"):
        risks.append("Daily bearish CHOCH")

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

    hard_reject = sentiment == "Negative" or (
        not is_financial
        and debt_to_equity is not None
        and debt_to_equity > 3
    )

    if hard_reject:
        decision = "REJECT"
    elif (
        tech_profile.get("daily_trend") == "Bullish"
        and bullish_structure_event
        and not any(
            item in risks
            for item in ("Daily bearish BOS", "Daily bearish CHOCH")
        )
    ):
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


if __name__ == "__main__":
    result = evaluate_candidate(
        "EXAMPLE",
        {
            "roce": 18,
            "de_ratio": 0.4,
            "altman_z": 3.1,
            "piotroski_score": 8,
            "pledged_percentage": 0,
            "industry": "Industrials",
        },
        {
            "weekly_trend": "Bullish",
            "daily_trend": "Bullish",
            "daily_bos_bullish": True,
            "daily_choch_bullish": False,
            "four_hour_bos_bullish": True,
            "four_hour_choch_bullish": False,
            "four_hour_liquidity_sweep": None,
            "daily_bos_bearish": False,
            "daily_choch_bearish": False,
        },
        {"sentiment": "Neutral", "title": ""},
    )
    print(result)
