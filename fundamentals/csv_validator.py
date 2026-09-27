# fundamentals/csv_validator.py

import pandas as pd
from datetime import datetime

# List required Screener columns (Core-50). Example subset:
REQUIRED_COLUMNS = [
    "Name", "NSE Code", "BSE Code", "ISIN", "Industry", "Sector",
    "Market Capitalization", "Price to Earnings", "Price to Book Value",
    "Return on Capital Employed", "Return on Equity", "Operating Profit Margin",
    "Sales growth 3Years", "Profit growth 3Years",
    "Sales growth 5Years", "Profit growth 5Years",
    "Debt to Equity", "Interest Coverage Ratio",
    "Sales (TTM)", "Cash from operations last year", "Free cash flow last year",
    "Working Capital (Days)", "Cash Conversion Cycle",
    "Debtor days", "Change in Debtor days (3Years)",
    "Promoter holding", "Change in promoter holding",
    "FII holding", "Change in FII holding",
    "DII holding", "Change in DII holding",
    "Pledged percentage",
    "Number of equity shares", "Number of equity shares preceding year",
    "Last annual result date", "Last quarterly result date",
    "Piotroski score", "Altman Z Score"
    # (Add any other needed fields)
]

def validate_screener_csv(file_path: str) -> pd.DataFrame:
    """
    Load and validate the Screener CSV.
    Checks for missing columns, converts date fields, and warns about stale data.
    Returns a cleaned DataFrame.
    """
    df = pd.read_csv(file_path)
    # Check for required columns
    missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required columns in Screener CSV: {missing_cols}")
    # Warn if extra columns (they will be ignored)
    extra_cols = [col for col in df.columns if col not in REQUIRED_COLUMNS]
    if extra_cols:
        print(f"Warning: Ignoring unexpected columns: {extra_cols}")

    # Parse date columns
    for date_col in ["Last annual result date", "Last quarterly result date"]:
        if date_col in df.columns:
            df[date_col] = pd.to_datetime(df[date_col], errors='coerce')

    # Check data freshness (example: quarterly results not older than 4 months)
    today = pd.Timestamp.today()
    if "Last quarterly result date" in df.columns:
        stale_mask = df["Last quarterly result date"] < (today - pd.DateOffset(months=4))
        if stale_mask.any():
            count = stale_mask.sum()
            print(f"Warning: {count} companies have quarterly results older than 4 months.")

    # Check NSE Code presence
    if df["NSE Code"].isnull().any():
        print("Warning: Some rows have missing NSE Code; they need mapping via ISIN/BSE.")

    # Drop duplicate companies by ISIN (if any)
    if df.duplicated(subset=["ISIN"]).any():
        df = df.drop_duplicates(subset=["ISIN"])
        print("Note: Duplicate rows dropped based on ISIN.")

    # Save the cleaned data to a new CSV (or DB)
    df.to_csv("fundamentals_validated.csv", index=False)
    print(f"Validated {len(df)} companies from the Screener CSV.")
    return df

if __name__ == "__main__":
    # Example usage:
    validated_df = validate_screener_csv("stocks_screen.csv")
