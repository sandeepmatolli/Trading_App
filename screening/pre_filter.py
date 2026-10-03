"""Fundamental/event-only pre-filter. No Groww access or trade execution.

This stage is not a buy/ranking model. It eliminates only the current decision
engine's known hard rejections and defers unsupported financial-sector names.
Unknown/incomplete/old inputs remain REVIEW_REQUIRED and may still proceed to
manual deep research. All other names retain original Screener CSV ordering.
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

import pandas as pd

from ai.ai_validation import FINANCIAL_KEYWORDS

STATUS_PASS = "ELIGIBLE_FOR_DEEP_SCAN"
STATUS_REVIEW = "REVIEW_REQUIRED"
STATUS_REJECT = "HARD_REJECT"
STATUS_DEFER = "DEFER_FINANCIAL"


def _text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _number(value) -> Optional[float]:
    if value is None:
        return None
    try:
        num = pd.to_numeric(value, errors="coerce")
        if pd.isna(num):
            return None
        return float(num)
    except (TypeError, ValueError):
        return None


def _bool(value) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def cap_bucket(value) -> str:
    """Custom *research* cap ranges, in ₹ crore; no cap is a hard gate."""
    cap = _number(value)
    if cap is None or cap <= 0:
        return "UNKNOWN"
    if cap < 5000:
        return "BELOW_SCREENING_RANGE"
    if cap <= 25000:
        return "SMALL"
    if cap <= 100000:
        return "MID"
    return "LARGE"


def evaluate_pre_filter(row: pd.Series, event_profile: Dict) -> Dict:
    """Mirror only cheap, explicit final-gate rules. Never infer SMC trend."""
    symbol = _text(row.get("NSE Code")).upper()
    company = _text(row.get("Name"))
    industry_text = " ".join(
        _text(row.get(key))
        for key in ("Industry", "Industry Group", "Sector")
    ).lower()
    financial = any(keyword in industry_text for keyword in FINANCIAL_KEYWORDS)
    de = _number(row.get("Debt to equity"))
    roce = _number(row.get("Return on capital employed"))
    market_cap = _number(row.get("Market Capitalization"))

    hard_reasons: List[str] = []
    review_reasons: List[str] = []
    informational: List[str] = []

    event_available = bool(event_profile.get("available", False))
    event_eligible = bool(event_profile.get("candidate_eligible", event_available))
    sentiment = event_profile.get("sentiment", "Unknown")
    if event_available and sentiment == "Negative":
        # Same material-negative hard-reject logic as ai.ai_validation.
        hard_reasons.append("MATCHED_NEGATIVE_MATERIAL_EVENT")
    if not financial and de is not None and de > 3:
        hard_reasons.append("NONFINANCIAL_DEBT_EQUITY_ABOVE_3")

    if _bool(row.get("Result data stale")):
        review_reasons.append("LAST_RESULT_PERIOD_STALE")
    parsed_period = row.get("Last result date parsed")
    if not _text(parsed_period):
        review_reasons.append("LAST_RESULT_PERIOD_UNKNOWN")
    if roce is None and not financial:
        review_reasons.append("ROCE_MISSING")
    if de is None and not financial:
        review_reasons.append("DEBT_EQUITY_MISSING")
    if market_cap is None:
        review_reasons.append("MARKET_CAP_MISSING")
    if not event_available:
        review_reasons.append("OFFICIAL_EVENT_SOURCE_UNAVAILABLE")
    elif not event_eligible:
        review_reasons.append("OFFICIAL_EVENT_EVIDENCE_NOT_CANDIDATE_GRADE")
    if int(event_profile.get("undated_event_count", 0) or 0) > 0:
        review_reasons.append("UNDATED_MATCHED_EVENT")
    if not company:
        informational.append("COMPANY_NAME_MISSING_SYMBOL_ONLY_EVENT_MATCHING")

    # The final decision engine takes explicit hard rejects even for the
    # financial model, but financial names are not eligible under V1. Keep
    # hard negative event visible as the primary reason when present.
    if hard_reasons:
        status = STATUS_REJECT
    elif financial:
        status = STATUS_DEFER
        informational.append("DEDICATED_FINANCIAL_MODEL_NOT_IMPLEMENTED")
    elif review_reasons:
        status = STATUS_REVIEW
    else:
        status = STATUS_PASS

    return {
        "symbol": symbol,
        "company": company,
        "status": status,
        "scan_eligible": status in (STATUS_PASS, STATUS_REVIEW),
        "market_cap_cr": market_cap,
        "cap_bucket": cap_bucket(market_cap),
        "debt_to_equity": de,
        "roce": roce,
        "result_period": _text(parsed_period),
        "hard_reasons": list(dict.fromkeys(hard_reasons)),
        "review_reasons": list(dict.fromkeys(review_reasons)),
        "informational": informational,
        "event": {
            "available": event_available,
            "candidate_eligible": event_eligible,
            "sentiment": sentiment,
            "event_type": event_profile.get("event_type"),
            "event_count": int(event_profile.get("event_count", 0) or 0),
            "undated_event_count": int(event_profile.get("undated_event_count", 0) or 0),
            "title": _text(event_profile.get("title")),
            "source": _text(event_profile.get("source")),
            "candidate_blockers": list(event_profile.get("candidate_blockers", []) or []),
        },
    }


def scan_pre_filter(
    df: pd.DataFrame,
    event_bundle: Dict,
    *,
    requested_symbols: Optional[List[str]] = None,
) -> Dict:
    """Scan NSE rows in original input order; fetch no market data.

    Event matching here has only Screener company-name and NSE-symbol hints;
    the later Groww-enriched deep scan remains authoritative and may find
    additional company aliases or events.
    """
    from news.news_engine import event_profile_for_symbol

    if "NSE Code" not in df.columns:
        raise ValueError("Validated DataFrame requires NSE Code.")
    requested = None
    if requested_symbols is not None:
        requested = {
            str(x).strip().upper() for x in requested_symbols if str(x).strip()
        }

    rows: List[Dict] = []
    seen = set()
    for _, row in df.iterrows():
        symbol = _text(row.get("NSE Code")).upper()
        if not symbol or symbol in seen:
            continue
        if requested is not None and symbol not in requested:
            continue
        seen.add(symbol)
        company = _text(row.get("Name"))
        aliases = [company] if company else []
        event_profile = event_profile_for_symbol(
            symbol, event_bundle, company_name=company, aliases=aliases,
        )
        rows.append(evaluate_pre_filter(row, event_profile))

    if requested is not None:
        unknown = sorted(requested - seen)
    else:
        unknown = []

    counts = Counter(item["status"] for item in rows)
    return {
        "schema_version": 1,
        "stage": "fundamental_event_prefilter",
        "policy": "conservative_no_technical_inference_original_csv_order",
        "source_event_available": bool(event_bundle.get("available", False)),
        "source_event_candidate_eligible": bool(event_bundle.get("candidate_eligible", False)),
        "event_cache": event_bundle.get("cache", {}),
        "event_source_health": event_bundle.get("source_health", {}),
        "total_checked": len(rows),
        "counts": {
            STATUS_PASS: counts[STATUS_PASS],
            STATUS_REVIEW: counts[STATUS_REVIEW],
            STATUS_REJECT: counts[STATUS_REJECT],
            STATUS_DEFER: counts[STATUS_DEFER],
        },
        "unknown_requested_symbols": unknown,
        "rows": rows,
    }


def selected_symbols(
    report: Dict,
    *,
    max_symbols: int = 20,
    skip_review: bool = False,
) -> List[str]:
    if max_symbols < 1:
        raise ValueError("max_symbols must be greater than zero")
    allowed = {STATUS_PASS}
    if not skip_review:
        allowed.add(STATUS_REVIEW)
    # First N in original CSV order, not an investment recommendation/ranking.
    return [
        row["symbol"] for row in report.get("rows", [])
        if row.get("status") in allowed
    ][:max_symbols]