# fundamentals/master_builder.py

import sqlite3
import pandas as pd
from config import SQLITE_DB_PATH

def update_company_master(df: pd.DataFrame, db_path: str = SQLITE_DB_PATH):
    """
    Update the company master database with companies from the DataFrame.
    The master table is keyed by ISIN and stores name, NSE/BSE code, sector, industry.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    # Create table if not exists
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS companies (
        isin TEXT PRIMARY KEY,
        name TEXT,
        symbol_nse TEXT,
        symbol_bse TEXT,
        sector TEXT,
        industry TEXT
    )""")

    for _, row in df.iterrows():
        isin = row.get("ISIN") or row.get("isin")
        if pd.isna(isin):
            continue
        name = row.get("Name")
        symbol_nse = row.get("NSE Code")
        symbol_bse = row.get("BSE Code")
        sector = row.get("Sector")
        industry = row.get("Industry")

        # Insert or ignore existing
        cursor.execute("""
            INSERT OR IGNORE INTO companies(isin,name,symbol_nse,symbol_bse,sector,industry)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (isin, name, symbol_nse, symbol_bse, sector, industry))
        # Update any missing fields
        cursor.execute("""
            UPDATE companies
            SET name = COALESCE(name, ?),
                symbol_nse = COALESCE(symbol_nse, ?),
                symbol_bse = COALESCE(symbol_bse, ?),
                sector = COALESCE(sector, ?),
                industry = COALESCE(industry, ?)
            WHERE isin = ?
        """, (name, symbol_nse, symbol_bse, sector, industry, isin))

    conn.commit()
    conn.close()
    print("Company master database updated.")

if __name__ == "__main__":
    # Example usage: load the validated screener and update master
    df = pd.read_csv("fundamentals_validated.csv")
    update_company_master(df)
