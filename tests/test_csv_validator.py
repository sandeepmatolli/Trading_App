# tests/test_csv_validator.py

from pathlib import Path

import pandas as pd

from fundamentals.csv_validator import validate_screener_csv


def test_actual_screener_aliases_and_non_strict_mode(tmp_path: Path):
    source = tmp_path / "stocks.csv"
    output = tmp_path / "validated.csv"

    frame = pd.DataFrame(
        {
            "Name": ["Example Ltd"],
            "NSE Code": ["EXAMPLE"],
            "BSE Code": ["500001"],
            "ISIN Code": ["INE000A01000"],
            "Industry Group": ["Industrials"],
            "Industry": ["Engineering"],
            "Current Price": ["1,234.50"],
            "Market Capitalization": ["10,000"],
            "Return on capital employed": ["18.2"],
            "Debt to equity": ["0.4"],
            "Price to Earning": ["22.5"],
            "Price to book value": ["3.2"],
            "OPM": ["15.5"],
            "Last result date": ["202606.00"],
            "Last annual result date": ["202603.00"],
        }
    )
    frame.to_csv(source, index=False)

    result = validate_screener_csv(
        str(source),
        output_path=str(output),
        strict_core50=False,
    )

    assert "ISIN" in result.columns
    assert result.loc[0, "ISIN"] == "INE000A01000"
    assert result.loc[0, "Current Price"] == 1234.5
    assert pd.notna(result.loc[0, "Last result date parsed"])
    assert output.exists()


def test_strict_core50_rejects_missing_fields(tmp_path: Path):
    source = tmp_path / "stocks.csv"
    pd.DataFrame(
        {
            "Name": ["Example Ltd"],
            "NSE Code": ["EXAMPLE"],
        }
    ).to_csv(source, index=False)

    try:
        validate_screener_csv(
            str(source),
            strict_core50=True,
        )
    except ValueError as exc:
        assert "Strict Core-50 validation failed" in str(exc)
    else:
        raise AssertionError("Strict validation should have failed.")
