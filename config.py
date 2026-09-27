# config.py

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv


# ============================================================
# PROJECT PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent

# Load secrets/settings from:
# C:\Users\sande\Desktop\trading_app\.env
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(ENV_FILE, override=False)


# ============================================================
# ENVIRONMENT HELPERS
# ============================================================

def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)

    if value is None or not value.strip():
        return default

    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(
            f"Environment variable {name} must be an integer. "
            f"Received: {value!r}"
        ) from exc


def _get_list(name: str) -> List[str]:
    value = os.getenv(name, "").strip()

    if not value:
        return []

    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]


def _resolve_project_path(
    env_name: str,
    default_relative_path: str,
) -> Path:
    value = os.getenv(env_name, "").strip()

    if not value:
        return PROJECT_ROOT / default_relative_path

    candidate = Path(value).expanduser()

    if candidate.is_absolute():
        return candidate

    return PROJECT_ROOT / candidate


# ============================================================
# GROWW CREDENTIALS
# ============================================================

# IMPORTANT:
# Never put real credentials directly in this Python file.
#
# Put them inside the local .env file instead.
#
# API Key + Secret flow:
# GROWW_API_KEY=...
# GROWW_API_SECRET=...
#
# OR direct access-token flow:
# GROWW_ACCESS_TOKEN=...

GROWW_API_KEY = os.getenv(
    "GROWW_API_KEY",
    "",
).strip()

GROWW_API_SECRET = os.getenv(
    "GROWW_API_SECRET",
    "",
).strip()

GROWW_ACCESS_TOKEN = os.getenv(
    "GROWW_ACCESS_TOKEN",
    "",
).strip()


# ============================================================
# GROWW MARKET SETTINGS
# ============================================================

GROWW_EXCHANGE = os.getenv(
    "GROWW_EXCHANGE",
    "NSE",
).strip().upper()

GROWW_SEGMENT = os.getenv(
    "GROWW_SEGMENT",
    "CASH",
).strip().upper()


# ============================================================
# DATA DIRECTORIES
# ============================================================

DATA_DIR = PROJECT_ROOT / "data"

RAW_DATA_DIR = DATA_DIR / "raw"

PROCESSED_DATA_DIR = DATA_DIR / "processed"

MARKET_DATA_DIR = DATA_DIR / "market"

OUTPUT_DIR = DATA_DIR / "output"

DATABASE_DIR = DATA_DIR / "database"


# ============================================================
# SCREENER FILES
# ============================================================

SCREENER_CSV_PATH = _resolve_project_path(
    "SCREENER_CSV_PATH",
    "stocks_screen.csv",
)

VALIDATED_CSV_PATH = PROCESSED_DATA_DIR / (
    "fundamentals_validated.csv"
)


# ============================================================
# DATABASE
# ============================================================

SQLITE_DB_PATH = DATABASE_DIR / "trading_app.db"


# ============================================================
# SCREENER VALIDATION
# ============================================================

STRICT_CORE50_DEFAULT = _get_bool(
    "STRICT_CORE50",
    False,
)

RESULT_FRESHNESS_MONTHS = _get_int(
    "RESULT_FRESHNESS_MONTHS",
    4,
)


# ============================================================
# MARKET-DATA HISTORY
# ============================================================

# Enough Daily history for Weekly/Monthly analysis.
DEFAULT_DAILY_DAYS = _get_int(
    "DEFAULT_DAILY_DAYS",
    730,
)

DEFAULT_FOUR_HOUR_DAYS = _get_int(
    "DEFAULT_FOUR_HOUR_DAYS",
    180,
)

DEFAULT_ONE_HOUR_DAYS = _get_int(
    "DEFAULT_ONE_HOUR_DAYS",
    60,
)


# ============================================================
# PIPELINE LIMITS
# ============================================================

# Keep this low while developing/testing.
# Increase after the complete pipeline is stable.
DEFAULT_MAX_SYMBOLS = _get_int(
    "DEFAULT_MAX_SYMBOLS",
    20,
)


# ============================================================
# NEWS / CORPORATE ANNOUNCEMENTS
# ============================================================

# Optional comma-separated RSS feeds.
#
# We intentionally do not hard-code an undocumented NSE API.
NEWS_RSS_URLS = _get_list(
    "NEWS_RSS_URLS"
)


# ============================================================
# CREATE REQUIRED DIRECTORIES
# ============================================================

for directory in [
    DATA_DIR,
    RAW_DATA_DIR,
    PROCESSED_DATA_DIR,
    MARKET_DATA_DIR,
    OUTPUT_DIR,
    DATABASE_DIR,
]:
    directory.mkdir(
        parents=True,
        exist_ok=True,
    )


# ============================================================
# DEBUG / HEALTH CHECK
# ============================================================

if __name__ == "__main__":
    print("Project root:", PROJECT_ROOT)

    print(
        "Screener CSV:",
        SCREENER_CSV_PATH,
    )

    print(
        "Validated CSV:",
        VALIDATED_CSV_PATH,
    )

    print(
        "SQLite DB:",
        SQLITE_DB_PATH,
    )

    print(
        "Groww API key configured:",
        bool(GROWW_API_KEY),
    )

    print(
        "Groww API secret configured:",
        bool(GROWW_API_SECRET),
    )

    print(
        "Groww direct access token configured:",
        bool(GROWW_ACCESS_TOKEN),
    )

    print(
        "Exchange:",
        GROWW_EXCHANGE,
    )

    print(
        "Segment:",
        GROWW_SEGMENT,
    )

    print(
        "Max symbols:",
        DEFAULT_MAX_SYMBOLS,
    )