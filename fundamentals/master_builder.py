# fundamentals/master_builder.py

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from config import SQLITE_DB_PATH


EXPECTED_COLUMNS = {
    "company_key",
    "isin",
    "name",
    "symbol_nse",
    "symbol_bse",
    "industry_group",
    "industry",
    "sector",
    "updated_at",
}


def _clean_text(value) -> Optional[str]:
    if value is None or pd.isna(value):
        return None

    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return None
    return text


def _company_key(row: pd.Series) -> Optional[str]:
    isin = _clean_text(row.get("ISIN"))
    if isin:
        return f"ISIN:{isin.upper()}"

    nse = _clean_text(row.get("NSE Code"))
    if nse:
        return f"NSE:{nse.upper()}"

    bse = _clean_text(row.get("BSE Code"))
    if bse:
        return f"BSE:{bse}"

    return None


def _ensure_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()

    existing = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='companies'"
    ).fetchone()

    if existing:
        columns = {
            row[1]
            for row in cursor.execute("PRAGMA table_info(companies)").fetchall()
        }
        if not EXPECTED_COLUMNS.issubset(columns):
            legacy_name = (
                "companies_legacy_"
                + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            )
            cursor.execute(
                f'ALTER TABLE companies RENAME TO "{legacy_name}"'
            )
            print(
                "Note: Existing company table used an older schema and was "
                f"preserved as {legacy_name}."
            )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS companies (
            company_key TEXT PRIMARY KEY,
            isin TEXT,
            name TEXT,
            symbol_nse TEXT,
            symbol_bse TEXT,
            industry_group TEXT,
            industry TEXT,
            sector TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_companies_nse
        ON companies(symbol_nse)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_companies_isin
        ON companies(isin)
        """
    )


def update_company_master(
    df: pd.DataFrame,
    db_path: str = str(SQLITE_DB_PATH),
) -> int:
    """
    Upsert Screener identity metadata into SQLite.

    ISIN is preferred for the company key. NSE Code is the safe fallback.
    Sector is optional; Industry Group and Industry are retained separately.
    """
    destination = Path(db_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    updated_at = datetime.now(timezone.utc).isoformat()
    upserted = 0
    skipped = 0

    with sqlite3.connect(destination) as conn:
        _ensure_schema(conn)
        cursor = conn.cursor()

        for _, row in df.iterrows():
            key = _company_key(row)
            if key is None:
                skipped += 1
                continue

            isin = _clean_text(row.get("ISIN"))
            if isin:
                isin = isin.upper()

            symbol_nse = _clean_text(row.get("NSE Code"))
            if symbol_nse:
                symbol_nse = symbol_nse.upper()

            values = (
                key,
                isin,
                _clean_text(row.get("Name")),
                symbol_nse,
                _clean_text(row.get("BSE Code")),
                _clean_text(row.get("Industry Group")),
                _clean_text(row.get("Industry")),
                _clean_text(row.get("Sector")),
                updated_at,
            )

            cursor.execute(
                """
                INSERT INTO companies (
                    company_key,
                    isin,
                    name,
                    symbol_nse,
                    symbol_bse,
                    industry_group,
                    industry,
                    sector,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_key) DO UPDATE SET
                    isin = excluded.isin,
                    name = excluded.name,
                    symbol_nse = excluded.symbol_nse,
                    symbol_bse = excluded.symbol_bse,
                    industry_group = excluded.industry_group,
                    industry = excluded.industry,
                    sector = excluded.sector,
                    updated_at = excluded.updated_at
                """,
                values,
            )
            upserted += 1

        conn.commit()

    print(
        f"Company master updated: {upserted} rows upserted, "
        f"{skipped} rows skipped."
    )
    return upserted


if __name__ == "__main__":
    from config import VALIDATED_CSV_PATH

    frame = pd.read_csv(VALIDATED_CSV_PATH, low_memory=False)
    update_company_master(frame)
