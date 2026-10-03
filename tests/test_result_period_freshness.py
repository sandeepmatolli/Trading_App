from pathlib import Path

import pandas as pd

from fundamentals.csv_validator import (
    _parse_screener_result_period,
    _result_stale_mask,
    validate_screener_csv,
)


def test_month_only_cutoff_does_not_false_flag_same_month():
    raw = pd.Series(["202606.00", "202605.00", "202607.00"])
    parsed = _parse_screener_result_period(raw)
    actual = _result_stale_mask(raw, parsed, pd.Timestamp("2026-06-03"))
    assert actual.tolist() == [False, True, False]


def test_explicit_full_dates_keep_day_precision():
    raw = pd.Series(["01-06-2026", "03-06-2026", "04-06-2026"])
    parsed = _parse_screener_result_period(raw)
    actual = _result_stale_mask(raw, parsed, pd.Timestamp("2026-06-03"))
    assert actual.tolist() == [True, False, False]


def test_unknown_period_not_misrepresented_as_stale():
    raw = pd.Series(["", "unknown", None])
    parsed = _parse_screener_result_period(raw)
    actual = _result_stale_mask(raw, parsed, pd.Timestamp("2026-06-03"))
    assert actual.tolist() == [False, False, False]
    assert parsed.isna().all()


def test_validator_retains_aliases_numeric_conversion_and_raw_file(tmp_path: Path):
    source = tmp_path / "raw.csv"
    original = pd.DataFrame({
        "Name": ["Example Ltd"], "NSE Code": ["example"],
        "ISIN Code": ["INE000A01000"], "Current Price": ["1,234.50"],
        "Last result date": ["202606.00"],
    })
    original.to_csv(source, index=False)
    out = tmp_path / "validated.csv"
    df = validate_screener_csv(str(source), output_path=str(out))
    assert df.iloc[0]["NSE Code"] == "EXAMPLE"
    assert df.iloc[0]["ISIN"] == "INE000A01000"
    assert df.iloc[0]["Current Price"] == 1234.5
    assert df.iloc[0]["Last result date parsed"] == pd.Timestamp("2026-06-01")
    assert out.exists()
    assert source.read_bytes() != out.read_bytes()