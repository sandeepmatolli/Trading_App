# fundamentals/csv_validator.py

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

from config import RESULT_FRESHNESS_MONTHS


# These are the 50 analytical fields selected in Screener.
CORE_50_COLUMNS: List[str] = [
    "Market Capitalization",
    "Sales growth 5Years",
    "Profit growth 5Years",
    "Profit growth 3Years",
    "Sales growth 3Years",
    "Sales growth 5years median",
    "YOY Quarterly sales growth",
    "YOY Quarterly profit growth",
    "Return on capital employed",
    "Average return on capital employed 5Years",
    "Average return on equity 5Years",
    "OPM",
    "OPM 5Year",
    "Debt to equity",
    "Free cash flow 5years",
    "Price to Earning",
    "PEG Ratio",
    "EVEBITDA",
    "Promoter holding",
    "Average return on equity 3Years",
    "Interest Coverage Ratio",
    "Current ratio",
    "Price to Free Cash Flow",
    "Price to Sales",
    "Profit growth",
    "Sales growth",
    "Industry PE",
    "Pledged percentage",
    "Change in promoter holding",
    "Return on equity",
    "Price to book value",
    "Profit after tax latest quarter",
    "Sales latest quarter",
    "Last result date",
    "Last annual result date",
    "ROCE3yr avg",
    "OPM latest quarter",
    "Working Capital Days",
    "Cash Conversion Cycle",
    "Debtor days",
    "Average debtor days 3years",
    "Cash from operations last year",
    "Operating cash flow 5years",
    "Free cash flow last year",
    "Piotroski score",
    "Altman Z Score",
    "Operating profit growth",
    "Other income latest quarter",
    "Number of equity shares preceding year",
    "Number of equity shares",
]

# Metadata/identity fields Screener may include automatically.
IDENTITY_COLUMNS = ["Name", "NSE Code"]
OPTIONAL_METADATA_COLUMNS = [
    "BSE Code",
    "ISIN",
    "Industry Group",
    "Industry",
    "Sector",
    "Current Price",
]

# Only aliases that represent the same concept are allowed here.
# We intentionally do NOT map Sales latest quarter to Sales TTM, or similar
# semantically different fields.
COLUMN_ALIASES: Dict[str, str] = {
    "ISIN Code": "ISIN",
    "Price to Earnings": "Price to Earning",
    "Price to Earnings ratio": "Price to Earning",
    "Price/Earnings": "Price to Earning",
    "P/E": "Price to Earning",
    "Price to Book Value": "Price to book value",
    "Price to Book": "Price to book value",
    "P/B": "Price to book value",
    "Return on Capital Employed": "Return on capital employed",
    "ROCE": "Return on capital employed",
    "Return on Equity": "Return on equity",
    "ROE": "Return on equity",
    "Operating Profit Margin": "OPM",
    "Operating profit margin": "OPM",
    "Debt to Equity": "Debt to equity",
    "Debt/Equity": "Debt to equity",
    "D/E": "Debt to equity",
    "Working Capital (Days)": "Working Capital Days",
    "Working capital days": "Working Capital Days",
    "Debtor Days": "Debtor days",
    "Promoter Holding": "Promoter holding",
    "Change in Promoter Holding": "Change in promoter holding",
    "Pledged": "Pledged percentage",
    "Pledged (%)": "Pledged percentage",
    "Last Result Date": "Last result date",
    "Last quarterly result date": "Last result date",
    "Last Quarterly Result Date": "Last result date",
    "Annual Result Date": "Last annual result date",
    "Average ROCE 3Years": "ROCE3yr avg",
}

TEXT_COLUMNS = {
    "Name",
    "NSE Code",
    "BSE Code",
    "ISIN",
    "Industry Group",
    "Industry",
    "Sector",
}

DATE_COLUMNS = {
    "Last result date",
    "Last annual result date",
}

NUMERIC_COLUMNS = set(CORE_50_COLUMNS) - DATE_COLUMNS
NUMERIC_COLUMNS.add("Current Price")


def _header_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip()).casefold()


def _build_alias_lookup() -> Dict[str, str]:
    lookup: Dict[str, str] = {}

    all_canonical = (
        IDENTITY_COLUMNS
        + OPTIONAL_METADATA_COLUMNS
        + CORE_50_COLUMNS
    )
    for canonical in all_canonical:
        lookup[_header_key(canonical)] = canonical

    for alias, canonical in COLUMN_ALIASES.items():
        lookup[_header_key(alias)] = canonical

    return lookup


ALIAS_LOOKUP = _build_alias_lookup()


def _canonicalize_headers(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, str]]:
    rename_map: Dict[str, str] = {}
    existing_targets = set()

    for original in df.columns:
        stripped = str(original).strip()
        target = ALIAS_LOOKUP.get(_header_key(stripped), stripped)

        # Avoid silently creating duplicate semantic columns.
        if target in existing_targets and target != stripped:
            print(
                "Warning: Header alias collision: "
                f"{original!r} also maps to {target!r}; leaving it unchanged."
            )
            target = stripped

        rename_map[original] = target
        existing_targets.add(target)

    df = df.rename(columns=rename_map)
    changed = {
        old: new
        for old, new in rename_map.items()
        if str(old).strip() != new
    }
    return df, changed


def _clean_text_series(series: pd.Series) -> pd.Series:
    result = series.astype("string").str.strip()
    return result.replace(
        {
            "": pd.NA,
            "nan": pd.NA,
            "None": pd.NA,
            "none": pd.NA,
            "NA": pd.NA,
            "N/A": pd.NA,
        }
    )


def _coerce_numeric_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")

    cleaned = (
        series.astype("string")
        .str.strip()
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace("₹", "", regex=False)
        .str.replace("Rs.", "", regex=False)
        .str.replace("Rs", "", regex=False)
        .replace(
            {
                "": pd.NA,
                "-": pd.NA,
                "--": pd.NA,
                "NA": pd.NA,
                "N/A": pd.NA,
                "None": pd.NA,
                "nan": pd.NA,
            }
        )
    )
    return pd.to_numeric(cleaned, errors="coerce")


def _parse_screener_result_period(series: pd.Series) -> pd.Series:
    """
    Screener exports result periods such as 202606.00 (YYYYMM) for some
    date-like fields. Those are reporting periods, not filing timestamps.

    Values matching YYYYMM are parsed as the first day of that month.
    Other values are parsed as ordinary dates with day-first support.
    """
    raw = series.astype("string").str.strip()
    raw = raw.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})

    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")

    yyyymm = raw.str.extract(r"^(?P<period>\d{6})(?:\.0+)?$")["period"]
    period_mask = yyyymm.notna()
    if period_mask.any():
        result.loc[period_mask] = pd.to_datetime(
            yyyymm.loc[period_mask],
            format="%Y%m",
            errors="coerce",
        )

    other_mask = raw.notna() & ~period_mask
    if other_mask.any():
        result.loc[other_mask] = pd.to_datetime(
            raw.loc[other_mask],
            errors="coerce",
            dayfirst=True,
        )

    return result


def _deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()

    if "ISIN" in work.columns:
        isin = _clean_text_series(work["ISIN"]).str.upper()
    else:
        isin = pd.Series(pd.NA, index=work.index, dtype="string")

    nse = _clean_text_series(work["NSE Code"]).str.upper()

    dedupe_key = pd.Series(pd.NA, index=work.index, dtype="string")
    has_isin = isin.notna()
    dedupe_key.loc[has_isin] = "ISIN:" + isin.loc[has_isin]
    has_nse = ~has_isin & nse.notna()
    dedupe_key.loc[has_nse] = "NSE:" + nse.loc[has_nse]

    duplicate_mask = dedupe_key.notna() & dedupe_key.duplicated(keep="first")
    duplicate_count = int(duplicate_mask.sum())
    if duplicate_count:
        print(
            f"Note: Dropping {duplicate_count} duplicate company rows "
            "using ISIN first, then NSE Code."
        )
        work = work.loc[~duplicate_mask].copy()

    return work


def _report_core50(df: pd.DataFrame) -> List[str]:
    missing_core = [col for col in CORE_50_COLUMNS if col not in df.columns]
    present_count = len(CORE_50_COLUMNS) - len(missing_core)

    print(
        f"Core-50 coverage: {present_count}/{len(CORE_50_COLUMNS)} "
        "analytical fields present."
    )
    if missing_core:
        print(f"Warning: Missing Core-50 fields: {missing_core}")

    return missing_core


def validate_screener_csv(
    file_path: str,
    output_path: Optional[str] = None,
    strict_core50: bool = False,
) -> pd.DataFrame:
    """
    Load, validate and normalize a Screener export.

    Hard requirement:
      - NSE Code column must exist because the current app analyzes NSE stocks.

    Core-50 fields:
      - Reported as present/missing.
      - Missing fields are warnings by default.
      - strict_core50=True converts missing Core-50 fields into an error.

    Raw input is never overwritten.
    """
    source = Path(file_path)
    if not source.exists():
        raise FileNotFoundError(
            f"Screener CSV not found: {source.resolve()}"
        )

    df = pd.read_csv(source, low_memory=False)
    if df.empty:
        raise ValueError("Screener CSV is empty.")

    df.columns = [str(col).strip() for col in df.columns]
    df, changed_headers = _canonicalize_headers(df)

    if changed_headers:
        print(f"Info: Canonicalized headers: {changed_headers}")

    if "NSE Code" not in df.columns:
        raise ValueError(
            "Missing required identifier column 'NSE Code'. "
            "This app currently scans NSE equities."
        )

    if "Name" not in df.columns:
        print("Warning: Optional identity column 'Name' is missing.")

    for col in TEXT_COLUMNS:
        if col in df.columns:
            df[col] = _clean_text_series(df[col])

    df["NSE Code"] = df["NSE Code"].str.upper()

    if "ISIN" in df.columns:
        df["ISIN"] = df["ISIN"].str.upper()

    if "BSE Code" in df.columns:
        df["BSE Code"] = (
            df["BSE Code"]
            .astype("string")
            .str.strip()
            .str.replace(r"\.0$", "", regex=True)
            .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
        )

    missing_nse_rows = df["NSE Code"].isna()
    missing_nse_count = int(missing_nse_rows.sum())
    if missing_nse_count:
        print(
            f"Warning: Removing {missing_nse_count} rows with no NSE Code "
            "from this NSE-only analysis dataset."
        )
        df = df.loc[~missing_nse_rows].copy()

    missing_core = _report_core50(df)
    if strict_core50 and missing_core:
        raise ValueError(
            "Strict Core-50 validation failed. Missing columns: "
            f"{missing_core}"
        )

    for col in NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = _coerce_numeric_series(df[col])

    for date_col in DATE_COLUMNS:
        if date_col in df.columns:
            parsed_col = f"{date_col} parsed"
            df[parsed_col] = _parse_screener_result_period(df[date_col])

    if "Last result date parsed" in df.columns:
        threshold = (
            pd.Timestamp.today().normalize()
            - pd.DateOffset(months=RESULT_FRESHNESS_MONTHS)
        )
        parsed = df["Last result date parsed"]
        stale_mask = parsed.notna() & (parsed < threshold)
        stale_count = int(stale_mask.sum())
        unknown_count = int(parsed.isna().sum())

        if stale_count:
            print(
                f"Warning: {stale_count} companies have a last-result "
                f"period older than {RESULT_FRESHNESS_MONTHS} months."
            )
        if unknown_count:
            print(
                f"Warning: {unknown_count} companies have an unknown or "
                "unparseable last-result period."
            )

        df["Result data stale"] = stale_mask

    df = _deduplicate(df)

    if output_path:
        destination = Path(output_path)
        if destination.resolve() == source.resolve():
            raise ValueError(
                "output_path must be different from the raw Screener CSV. "
                "Raw snapshots must not be overwritten."
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(destination, index=False)
        print(f"Validated CSV written to: {destination}")

    print(
        f"Validated {len(df)} NSE companies from "
        f"{source.name}."
    )
    return df


if __name__ == "__main__":
    from config import SCREENER_CSV_PATH, VALIDATED_CSV_PATH, STRICT_CORE50_DEFAULT

    validate_screener_csv(
        str(SCREENER_CSV_PATH),
        output_path=str(VALIDATED_CSV_PATH),
        strict_core50=STRICT_CORE50_DEFAULT,
    )
