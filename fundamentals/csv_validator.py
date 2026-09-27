import pandas as pd
from datetime import datetime

# Canonical Core-50 fields
CORE_COLUMNS = [
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
]

# Map alternate Screener column names to canonical names
COLUMN_SYNONYMS = {
    # Valuation
    "Price to Earning": "Price to Earnings",
    "Price to Earnings ratio": "Price to Earnings",
    "Price/Earnings": "Price to Earnings",
    "P/E": "Price to Earnings",
    "Price to Book": "Price to Book Value",
    "Price to Book ratio": "Price to Book Value",
    "P/B": "Price to Book Value",
    # Profitability
    "ROCE": "Return on Capital Employed",
    "Return on capital employed": "Return on Capital Employed",
    "ROE": "Return on Equity",
    "OPM": "Operating Profit Margin",
    "Operating profit margin": "Operating Profit Margin",
    # Growth
    "Sales Latest Quarter": "Sales (TTM)",
    "Sales Last Year": "Sales (TTM)",
    "Sales": "Sales (TTM)",
    "Profit Latest Quarter": "Profit growth 3Years",
    # Solvency/Efficiency
    "Debt/Equity": "Debt to Equity",
    "D/E": "Debt to Equity",
    "Working Capital Days": "Working Capital (Days)",
    "Debtor Days": "Debtor days",
    "Change in Debtor days 3Years": "Change in Debtor days (3Years)",
    "Change in Debtor days (3 Years)": "Change in Debtor days (3Years)",
    # Shareholding
    "Promoter Holding": "Promoter holding",
    "Change in Promoter Holding": "Change in promoter holding",
    "Promoters Holding": "Promoter holding",
    "Promoters stake": "Promoter holding",
    "FII Holding": "FII holding",
    "FII Holding (%)": "FII holding",
    "Change in FII Holding": "Change in FII holding",
    "Change in FII Holding (%)": "Change in FII holding",
    "DII Holding": "DII holding",
    "DII Holding (%)": "DII holding",
    "Change in DII Holding": "Change in DII holding",
    "Change in DII Holding (%)": "Change in DII holding",
    "Pledged": "Pledged percentage",
    "Pledged (%)": "Pledged percentage",
    # Dates
    "Last Result Date": "Last quarterly result date",
    "TTM Result Date": "Last quarterly result date",
    "Last Quarterly Result Date": "Last quarterly result date",
    "Result Date": "Last quarterly result date",
    "Quarterly Result Date": "Last quarterly result date",
    "Annual result date": "Last annual result date",
    "Annual Result Date": "Last annual result date",
    # Others (if any)
    # Add more synonyms as needed
}

def validate_screener_csv(file_path: str) -> pd.DataFrame:
    """
    Load and validate the Screener CSV.
    - Renames known synonyms to canonical column names.
    - Checks for missing essential columns.
    - Converts date fields to datetime.
    - Warns about stale data.
    Returns a cleaned DataFrame.
    """
    df = pd.read_csv(file_path, encoding='utf-8')
    # Strip whitespace from headers
    df.columns = df.columns.str.strip()
    
    # Rename any known synonyms to canonical names
    rename_map = {}
    for col in df.columns:
        if col in COLUMN_SYNONYMS:
            rename_map[col] = COLUMN_SYNONYMS[col]
    if rename_map:
        df = df.rename(columns=rename_map)
        print(f"Info: Renamed columns: {rename_map}")
    
    # Check for missing columns
    missing = [col for col in CORE_COLUMNS if col not in df.columns]
    # Treat ISIN, Sector, Last quarterly result date as optional
    optional = {"ISIN", "Sector", "Last quarterly result date"}
    critical_missing = [col for col in missing if col not in optional]
    if critical_missing:
        raise ValueError(f"Missing required columns in Screener CSV: {critical_missing}")
    elif missing:
        print(f"Note: Optional columns missing: {missing}")

    # Warn if extra columns are present
    extras = [col for col in df.columns if col not in CORE_COLUMNS]
    if extras:
        print(f"Warning: Ignoring unexpected columns: {extras}")

    # Parse date columns
    for date_col in ["Last annual result date", "Last quarterly result date"]:
        if date_col in df.columns:
            df[date_col] = pd.to_datetime(df[date_col], errors='coerce')

    # Freshness check: warn if quarterly results are older than 4 months
    today = pd.Timestamp.today()
    if "Last quarterly result date" in df.columns:
        stale_mask = df["Last quarterly result date"] < (today - pd.DateOffset(months=4))
        if stale_mask.any():
            count = stale_mask.sum()
            print(f"Warning: {count} companies have quarterly results older than 4 months.")
    
    # Check for missing NSE Code
    if "NSE Code" in df.columns and df["NSE Code"].isnull().any():
        print("Warning: Some rows have missing NSE Code; they may need mapping via ISIN/BSE.")
    
    # Drop duplicate companies by ISIN if present
    if "ISIN" in df.columns and df["ISIN"].notnull().any():
        if df.duplicated(subset=["ISIN"]).any():
            df = df.drop_duplicates(subset=["ISIN"])
            print("Note: Duplicate rows dropped based on ISIN.")

    # Save cleaned data for reference (optional)
    df.to_csv("fundamentals_validated.csv", index=False)
    print(f"Validated {len(df)} companies from Screener CSV.")
    return df

if __name__ == "__main__":
    validate_screener_csv("stocks_screen.csv")
