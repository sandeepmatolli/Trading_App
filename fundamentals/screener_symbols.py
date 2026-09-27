# fundamentals/screener_symbols.py

from __future__ import annotations

from pathlib import Path
from typing import List, Union

import pandas as pd


DataSource = Union[str, Path, pd.DataFrame]


def get_nse_symbols(source: DataSource) -> List[str]:
    """
    Return unique, normalized NSE symbols from either a DataFrame or CSV path.
    """
    if isinstance(source, pd.DataFrame):
        df = source
    else:
        df = pd.read_csv(source, low_memory=False)

    if "NSE Code" not in df.columns:
        raise ValueError("Column 'NSE Code' is required to extract symbols.")

    symbols = (
        df["NSE Code"]
        .astype("string")
        .str.strip()
        .str.upper()
        .dropna()
    )
    symbols = symbols[
        ~symbols.isin(["", "NAN", "NONE", "NA", "N/A"])
    ]

    result = symbols.drop_duplicates().tolist()
    print(f"Extracted {len(result)} unique NSE symbols.")
    return result


if __name__ == "__main__":
    from config import VALIDATED_CSV_PATH

    items = get_nse_symbols(VALIDATED_CSV_PATH)
    print(items[:10])
