"""Ensure the cheap pre-filter and the final V1 decision gate agree on finance deferral."""
from __future__ import annotations

import pandas as pd
import pytest

from ai.ai_validation import _is_financial_company, evaluate_candidate
from screening.pre_filter import (
    STATUS_DEFER,
    STATUS_PASS,
    STATUS_REJECT,
    evaluate_pre_filter,
    selected_symbols,
)


def _row(symbol="EXAMPLE", industry="Engineering", name="Example Industries"):
    return pd.Series({
        "NSE Code": symbol,
        "Name": name,
        "Industry": industry,
        "Industry Group": "Industrials",
        "Sector": "Industrials",
        "Debt to equity": 0.5,
        "Return on capital employed": 18.0,
        "Market Capitalization": 12000,
        "Result data stale": False,
        "Last result date parsed": pd.Timestamp("2026-09-01"),
    })


def _event(sentiment="Neutral"):
    return {
        "available": True,
        "candidate_eligible": True,
        "sentiment": sentiment,
        "event_type": "NoneFound",
        "event_count": 0,
        "undated_event_count": 0,
        "title": "Auditor resignation" if sentiment == "Negative" else "",
        "candidate_blockers": [],
    }


def _bullish():
    return {
        "data_quality": {"valid": True, "candidate_eligible": True},
        "daily_trend": "Bullish",
        "daily_bos_bullish": True,
        "daily_bos_bearish": False,
        "daily_choch_bearish": False,
    }


def _fund(row):
    return {
        "industry": row.get("Industry"),
        "industry_group": row.get("Industry Group"),
        "sector": row.get("Sector"),
        "roce": 18,
        "de_ratio": 0.5,
        "altman_z": 3.0,
        "piotroski_score": 8,
        "pledged_percentage": 0,
    }


@pytest.mark.parametrize("industry", [
    "Stock Broking",
    "Stockbroking",
    "Wealth Management",
    "Asset Management",
    "Investment Banking",
    "Capital Markets",
    "Portfolio Management",
    "Investment Services",
])
def test_financial_industry_is_deferred_in_pre_filter(industry):
    result = evaluate_pre_filter(_row(industry=industry), _event())
    assert result["status"] == STATUS_DEFER
    assert result["scan_eligible"] is False
    assert result["financial_sector_reason"] is not None


@pytest.mark.parametrize("symbol", ["360ONE", "5PAISA"])
def test_verified_symbol_is_deferred_even_with_opaque_industry_metadata(symbol):
    row = _row(symbol=symbol, industry="Diversified", name="Opaque name")
    result = evaluate_pre_filter(row, _event())
    assert result["status"] == STATUS_DEFER
    assert result["financial_sector_reason"] == "VERIFIED_FINANCIAL_SYMBOL:" + symbol
    assert result["scan_eligible"] is False


def test_capital_goods_industry_is_not_falsely_classified_as_financial():
    row = _row(symbol="CAPGOODS", industry="Capital Goods")
    assert evaluate_pre_filter(row, _event())["status"] == STATUS_PASS
    assert not _is_financial_company(_fund(row), symbol="CAPGOODS")


def test_company_name_alone_does_not_trigger_false_financial_inference():
    row = _row(symbol="TECHCO", industry="Software", name="Wealth Management Software Ltd")
    assert evaluate_pre_filter(row, _event())["status"] == STATUS_PASS


@pytest.mark.parametrize("symbol", ["360ONE", "5PAISA"])
def test_pre_filter_and_final_candidate_gate_both_defer_verified_financial_symbols(symbol):
    row = _row(symbol=symbol, industry="Diversified")
    pre = evaluate_pre_filter(row, _event())
    final = evaluate_candidate(symbol, _fund(row), _bullish(), _event())
    assert pre["status"] == STATUS_DEFER
    assert final["decision"] == "WATCH"
    assert final["financial_sector_model_deferred"] is True


def test_negative_financial_event_keeps_existing_explicit_hard_reject():
    row = _row(symbol="5PAISA", industry="Stock Broking")
    event = _event("Negative")
    assert evaluate_pre_filter(row, event)["status"] == STATUS_REJECT
    assert evaluate_candidate("5PAISA", _fund(row), _bullish(), event)["decision"] == "REJECT"


def test_deferred_financial_names_are_not_in_first_n_deep_scan_queue():
    rows = [
        evaluate_pre_filter(_row(symbol="360ONE", industry="Diversified"), _event()),
        evaluate_pre_filter(_row(symbol="5PAISA", industry="Stock Broking"), _event()),
        evaluate_pre_filter(_row(symbol="AAATECH"), _event()),
        evaluate_pre_filter(_row(symbol="TCS", industry="Software"), _event()),
    ]
    assert selected_symbols({"rows": rows}, max_symbols=2) == ["AAATECH", "TCS"]