# fundamentals/screener_symbols.py

import pandas as pd

def get_nse_symbols(csv_path: str) -> list:
    """
    Read the validated CSV and return a list of unique NSE symbols.
    """
    df = pd.read_csv(csv_path)
    symbols = df["NSE Code"].dropna().astype(str).str.strip().unique().tolist()
    print(f"Extracted {len(symbols)} NSE symbols.")
    return symbols

if __name__ == "__main__":
    symbols = get_nse_symbols("fundamentals_validated.csv")
    print(symbols[:10], "...")
