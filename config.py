# config.py

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(ENV_FILE, override=False)


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


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


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value.strip())
    except ValueError as exc:
        raise ValueError(
            f"Environment variable {name} must be numeric. "
            f"Received: {value!r}"
        ) from exc


def _get_list(name: str) -> List[str]:
    value = os.getenv(name, "").strip()
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _resolve_project_path(env_name: str, default_relative_path: str) -> Path:
    value = os.getenv(env_name, "").strip()
    if not value:
        return PROJECT_ROOT / default_relative_path

    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


# ---------------------------------------------------------------------------
# Groww credentials
# ---------------------------------------------------------------------------

GROWW_API_KEY = os.getenv("GROWW_API_KEY", "").strip()
GROWW_API_SECRET = os.getenv("GROWW_API_SECRET", "").strip()
GROWW_ACCESS_TOKEN = os.getenv("GROWW_ACCESS_TOKEN", "").strip()

GROWW_EXCHANGE = os.getenv("GROWW_EXCHANGE", "NSE").strip().upper()
GROWW_SEGMENT = os.getenv("GROWW_SEGMENT", "CASH").strip().upper()


# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------

DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
MARKET_DATA_DIR = DATA_DIR / "market"
OUTPUT_DIR = DATA_DIR / "output"
DATABASE_DIR = DATA_DIR / "database"

SCREENER_CSV_PATH = _resolve_project_path(
    "SCREENER_CSV_PATH",
    "stocks_screen.csv",
)
VALIDATED_CSV_PATH = PROCESSED_DATA_DIR / "fundamentals_validated.csv"
SQLITE_DB_PATH = DATABASE_DIR / "trading_app.db"


# ---------------------------------------------------------------------------
# Fundamental validation
# ---------------------------------------------------------------------------

STRICT_CORE50_DEFAULT = _get_bool("STRICT_CORE50", False)
RESULT_FRESHNESS_MONTHS = _get_int("RESULT_FRESHNESS_MONTHS", 4)


# ---------------------------------------------------------------------------
# Market-data architecture
# ---------------------------------------------------------------------------
#
# IMPORTANT:
# We no longer use Groww's direct 1-day or direct 4-hour candles as the
# authoritative SMC source.
#
# Observed API behaviour for 20MICRONS returned:
#   - only one direct Daily candle for a 60-day request
#   - irregular direct 4H timestamps
#
# V1 therefore uses:
#   Groww 1H -> Daily -> Weekly -> Monthly
#   Groww 15m -> session-aligned 75m setup candles
#
# 75 minutes divides the regular NSE cash session (375 minutes) exactly into
# five bars, avoiding arbitrary 4H session boundaries.
# ---------------------------------------------------------------------------

DEFAULT_HOURLY_HISTORY_DAYS = _get_int(
    "DEFAULT_HOURLY_HISTORY_DAYS",
    730,
)

DEFAULT_SETUP_15M_DAYS = _get_int(
    "DEFAULT_SETUP_15M_DAYS",
    89,
)

DEFAULT_MAX_SYMBOLS = _get_int(
    "DEFAULT_MAX_SYMBOLS",
    20,
)

# Use smaller operational windows than Groww's documented maxima.
# This is deliberate: the API accepted larger 1H windows in testing but
# returned a silent 42-day hole for 20MICRONS. Smaller windows reduce the
# chance of incomplete responses while keeping the documented limits intact.
GROWW_HOURLY_REQUEST_CHUNK_DAYS = _get_int(
    "GROWW_HOURLY_REQUEST_CHUNK_DAYS",
    60,
)

GROWW_15M_REQUEST_CHUNK_DAYS = _get_int(
    "GROWW_15M_REQUEST_CHUNK_DAYS",
    30,
)

GROWW_GAP_REPAIR_CHUNK_DAYS = _get_int(
    "GROWW_GAP_REPAIR_CHUNK_DAYS",
    14,
)

GROWW_GAP_REPAIR_MAX_PASSES = _get_int(
    "GROWW_GAP_REPAIR_MAX_PASSES",
    2,
)

NSE_SESSION_START = os.getenv(
    "NSE_SESSION_START",
    "09:15",
).strip()

NSE_SESSION_END = os.getenv(
    "NSE_SESSION_END",
    "15:30",
).strip()

MARKET_DATA_MAX_AGE_DAYS = _get_int(
    "MARKET_DATA_MAX_AGE_DAYS",
    7,
)

MARKET_DATA_MAX_DAILY_GAP_DAYS = _get_int(
    "MARKET_DATA_MAX_DAILY_GAP_DAYS",
    14,
)

MARKET_DATA_MIN_DAILY_COVERAGE_RATIO = _get_float(
    "MARKET_DATA_MIN_DAILY_COVERAGE_RATIO",
    0.55,
)

MARKET_DATA_MIN_SETUP_BARS = _get_int(
    "MARKET_DATA_MIN_SETUP_BARS",
    20,
)


# ---------------------------------------------------------------------------
# News / corporate announcements
# ---------------------------------------------------------------------------

NEWS_RSS_URLS = _get_list("NEWS_RSS_URLS")


# ---------------------------------------------------------------------------
# Ensure local directories exist
# ---------------------------------------------------------------------------

for directory in (
    DATA_DIR,
    RAW_DATA_DIR,
    PROCESSED_DATA_DIR,
    MARKET_DATA_DIR,
    OUTPUT_DIR,
    DATABASE_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    print("Project root:", PROJECT_ROOT)
    print("Screener CSV:", SCREENER_CSV_PATH)
    print("Validated CSV:", VALIDATED_CSV_PATH)
    print("SQLite DB:", SQLITE_DB_PATH)
    print("Groww API key configured:", bool(GROWW_API_KEY))
    print("Groww API secret configured:", bool(GROWW_API_SECRET))
    print("Groww direct access token configured:", bool(GROWW_ACCESS_TOKEN))
    print("Exchange:", GROWW_EXCHANGE)
    print("Segment:", GROWW_SEGMENT)
    print("Hourly history days:", DEFAULT_HOURLY_HISTORY_DAYS)
    print("15m setup history days:", DEFAULT_SETUP_15M_DAYS)
    print("Max symbols:", DEFAULT_MAX_SYMBOLS)
    print("1H request chunk days:", GROWW_HOURLY_REQUEST_CHUNK_DAYS)
    print("15m request chunk days:", GROWW_15M_REQUEST_CHUNK_DAYS)
    print("Gap-repair chunk days:", GROWW_GAP_REPAIR_CHUNK_DAYS)
