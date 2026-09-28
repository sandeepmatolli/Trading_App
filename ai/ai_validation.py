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


def _historical_warnings(
    data_quality: Dict,
) -> List[str]:
    """
    Return explicit old-history warnings without turning them into
    fundamental/company risk flags.
    """
    explicit = data_quality.get("historical_warnings")
    if isinstance(explicit, list) and explicit:
        return [str(item) for item in explicit]

    warnings: List[str] = []

    for gap in data_quality.get("older_large_daily_gaps", []) or []:
        previous_daily = str(gap.get("previous_daily", "")).strip()
        next_daily = str(gap.get("next_daily", "")).strip()
        gap_days = gap.get("gap_days")

        if gap_days is None:
            continue

        if previous_daily and next_daily:
            warnings.append(
                "Older Daily history gap: "
                f"{previous_daily} -> {next_daily} "
                f"({int(gap_days)} calendar days)."
            )
        else:
            warnings.append(
                f"Older Daily history contains a {int(gap_days)}-day gap."
            )

    if warnings:
        return warnings

    for warning in data_quality.get("warnings", []) or []:
        text = str(warning)
        if (
            "Older Daily history" in text
            or "historical" in text.lower()
        ):
            warnings.append(text)

    return warnings


def _candidate_reason_for_data_reject(
    data_quality: Dict,
) -> List[str]:
    reasons = data_quality.get(
        "reasons",
        ["Unknown data-quality failure."],
    )
    return [
        "Market data failed validation: " + str(reason)
        for reason in reasons
    ]


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

    Event evidence has TWO gates:
      available          -> the primary official source was reachable
      candidate_eligible -> required official sources were reachable + fresh

    A source can therefore be available but still be too stale/degraded to
    permit CANDIDATE.

    This function does not place orders.
    """
    positive: List[str] = []
    risks: List[str] = []
    missing_data: List[str] = []

    data_quality = tech_profile.get("data_quality", {})
    historical_warnings = _historical_warnings(data_quality)
    historical_context_degraded = bool(historical_warnings)

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
            "historical_warnings": historical_warnings,
            "historical_context_degraded": historical_context_degraded,
            "candidate_eligibility_reason": _candidate_reason_for_data_reject(
                data_quality
            ),
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

    event_available = bool(
        event_profile.get("available", False)
    )
    event_candidate_eligible = bool(
        event_profile.get(
            "candidate_eligible",
            event_available,
        )
    )

    if not event_available:
        missing_data.append(
            "News/corporate-announcement source unavailable."
        )
        sentiment = "Unknown"
    else:
        sentiment = event_profile.get(
            "sentiment",
            "Neutral",
        )

        if not event_candidate_eligible:
            blockers = event_profile.get(
                "candidate_blockers",
                [],
            )

            if blockers:
                for blocker in blockers:
                    missing_data.append(
                        "Event-source candidate gate: "
                        + str(blocker)
                    )
            else:
                missing_data.append(
                    "Event source is available but not candidate-grade."
                )

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
        event_candidate_eligible
        and not is_financial
        and candidate_data_eligible
    )

    if hard_reject:
        decision = "REJECT"
    elif bullish_context and evidence_complete:
        decision = "CANDIDATE"
    else:
        decision = "WATCH"

    candidate_eligibility_reason: List[str] = []

    if decision == "CANDIDATE":
        candidate_eligibility_reason.extend(
            [
                "Market data is valid and candidate-grade.",
                "Daily trend is Bullish.",
                "Bullish Daily/75m BOS/CHOCH evidence is present.",
                "No bearish Daily BOS/CHOCH is present.",
                "Official event evidence is available and candidate-grade.",
                "Generic non-financial fundamental model is applicable.",
            ]
        )
    elif decision == "REJECT":
        if sentiment == "Negative":
            candidate_eligibility_reason.append(
                "Rejected because a negative material event is present."
            )

        if (
            not is_financial
            and debt_to_equity is not None
            and debt_to_equity > 3
        ):
            candidate_eligibility_reason.append(
                "Rejected because debt to equity is above 3 for a "
                "non-financial company."
            )
    else:
        if not candidate_data_eligible:
            blockers = data_quality.get("candidate_blockers", [])
            if blockers:
                candidate_eligibility_reason.extend(
                    [
                        "Market data is not candidate-grade: " + str(blocker)
                        for blocker in blockers
                    ]
                )
            else:
                candidate_eligibility_reason.append(
                    "Market data is usable for research but not "
                    "candidate-grade."
                )

        if tech_profile.get("daily_trend") != "Bullish":
            candidate_eligibility_reason.append(
                "Daily trend is not Bullish."
            )

        if not bullish_structure_event:
            candidate_eligibility_reason.append(
                "No bullish Daily/75m BOS/CHOCH confirmation is present."
            )

        if bearish_daily:
            candidate_eligibility_reason.append(
                "Bearish Daily BOS/CHOCH is present."
            )

        if not event_available:
            candidate_eligibility_reason.append(
                "News/corporate-announcement evidence is unavailable."
            )
        elif not event_candidate_eligible:
            candidate_eligibility_reason.append(
                "Official event evidence is available but not "
                "candidate-grade/fresh."
            )

        if is_financial:
            candidate_eligibility_reason.append(
                "Dedicated financial-sector model is not implemented."
            )

    return {
        "symbol": symbol,
        "decision": decision,
        "positive_evidence": positive,
        "risk_flags": risks,
        "missing_data": missing_data,
        "historical_warnings": historical_warnings,
        "historical_context_degraded": historical_context_degraded,
        "candidate_eligibility_reason": candidate_eligibility_reason,
        "financial_sector_model_deferred": is_financial,
        "event_source_candidate_eligible": event_candidate_eligible,
    }
