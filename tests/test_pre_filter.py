import sys
import types

import pandas as pd
import pytest

from screening.pre_filter import (
    STATUS_PASS, STATUS_REVIEW, STATUS_REJECT, STATUS_DEFER,
    cap_bucket, evaluate_pre_filter, scan_pre_filter, selected_symbols,
)


def _row(**changes):
    data = {
        "NSE Code": "TEST", "Name": "Example Ltd",
        "Industry": "Engineering", "Industry Group": "Industrials", "Sector": "Capital Goods",
        "Debt to equity": 0.5, "Return on capital employed": 18,
        "Market Capitalization": 12000,
        "Result data stale": False,
        "Last result date parsed": pd.Timestamp("2026-06-01"),
    }
    data.update(changes)
    return pd.Series(data)


def _event(**changes):
    data = {
        "available": True, "candidate_eligible": True, "sentiment": "Neutral",
        "event_type": "NoneFound", "event_count": 0,
        "undated_event_count": 0, "candidate_blockers": [],
    }
    data.update(changes)
    return data


def test_regular_nonfinancial_passes_without_any_smc_claim():
    result = evaluate_pre_filter(_row(), _event())
    assert result["status"] == STATUS_PASS
    assert result["scan_eligible"] is True
    assert "trend" not in result


def test_hard_debt_gate_matches_existing_final_engine_strictly_above_3():
    assert evaluate_pre_filter(_row(**{"Debt to equity": 3}), _event())["status"] == STATUS_PASS
    result = evaluate_pre_filter(_row(**{"Debt to equity": 3.01}), _event())
    assert result["status"] == STATUS_REJECT
    assert "NONFINANCIAL_DEBT_EQUITY_ABOVE_3" in result["hard_reasons"]


def test_negative_matched_event_is_hard_reject():
    result = evaluate_pre_filter(_row(), _event(
        sentiment="Negative", title="Auditor resignation", event_count=1,
    ))
    assert result["status"] == STATUS_REJECT
    assert result["event"]["title"] == "Auditor resignation"


def test_financial_sector_is_deferred_not_generic_rejected():
    result = evaluate_pre_filter(_row(Industry="Banking"), _event())
    assert result["status"] == STATUS_DEFER
    assert result["scan_eligible"] is False


def test_missing_data_remains_review_not_false_hard_reject():
    result = evaluate_pre_filter(
        _row(**{"Debt to equity": pd.NA, "Return on capital employed": pd.NA}),
        _event(),
    )
    assert result["status"] == STATUS_REVIEW
    assert result["scan_eligible"] is True
    assert "DEBT_EQUITY_MISSING" in result["review_reasons"]


def test_stale_period_is_review_not_hard_reject():
    result = evaluate_pre_filter(_row(**{"Result data stale": True}), _event())
    assert result["status"] == STATUS_REVIEW
    assert "LAST_RESULT_PERIOD_STALE" in result["review_reasons"]


def test_unavailable_event_source_is_review():
    result = evaluate_pre_filter(_row(), _event(available=False, candidate_eligible=False))
    assert result["status"] == STATUS_REVIEW
    assert "OFFICIAL_EVENT_SOURCE_UNAVAILABLE" in result["review_reasons"]


def test_undated_matched_event_is_review_not_an_absence_of_event():
    result = evaluate_pre_filter(_row(), _event(
        candidate_eligible=False, undated_event_count=1,
    ))
    assert result["status"] == STATUS_REVIEW
    assert "UNDATED_MATCHED_EVENT" in result["review_reasons"]


def test_market_cap_bucket_boundaries_are_explicit_and_nonrejecting():
    assert cap_bucket(4999) == "BELOW_SCREENING_RANGE"
    assert cap_bucket(5000) == "SMALL"
    assert cap_bucket(25000) == "SMALL"
    assert cap_bucket(25000.01) == "MID"
    assert cap_bucket(100000) == "MID"
    assert cap_bucket(100000.01) == "LARGE"
    assert cap_bucket(None) == "UNKNOWN"
    assert evaluate_pre_filter(_row(**{"Market Capitalization": 100}), _event())["status"] == STATUS_PASS


def test_selection_keeps_csv_order_without_ranking_and_review_is_configurable():
    report = {"rows": [
        {"symbol": "AAA", "status": STATUS_REVIEW},
        {"symbol": "BBB", "status": STATUS_REJECT},
        {"symbol": "CCC", "status": STATUS_PASS},
        {"symbol": "DDD", "status": STATUS_PASS},
    ]}
    assert selected_symbols(report, max_symbols=2) == ["AAA", "CCC"]
    assert selected_symbols(report, max_symbols=2, skip_review=True) == ["CCC", "DDD"]
    with pytest.raises(ValueError):
        selected_symbols(report, max_symbols=0)


def test_scanner_dedupes_and_reports_unknown_symbols_without_mutating_df(monkeypatch):
    module = types.ModuleType("news.news_engine")
    def mock_profile(symbol, bundle, **kwargs):
        return _event()
    module.event_profile_for_symbol = mock_profile
    monkeypatch.setitem(sys.modules, "news.news_engine", module)
    a = _row(**{"NSE Code": "AAA"})
    b = _row(**{"NSE Code": "BBB"})
    df = pd.DataFrame([a, a, b])
    original = df.copy(deep=True)
    result = scan_pre_filter(df, {"available": True, "candidate_eligible": True},
                             requested_symbols=["AAA", "BBB", "UNKNOWN"])
    assert result["total_checked"] == 2
    assert result["unknown_requested_symbols"] == ["UNKNOWN"]
    assert result["counts"][STATUS_PASS] == 2
    pd.testing.assert_frame_equal(df, original)