
"""Offline, read-only estimate of the scanner's baseline Groww candle requests.

This is not a provider quota, a rate limiter, a request reservation, or permission
for a scan. Actual instrument lookups, gap repair and retries are *additional*.
The exact windowing helper and documented per-request caps are imported from
market_data.groww_fetch so the estimate follows the current fetch implementation.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from config import (
    DEFAULT_HOURLY_HISTORY_DAYS, DEFAULT_SETUP_15M_DAYS,
    GROWW_HOURLY_REQUEST_CHUNK_DAYS, GROWW_15M_REQUEST_CHUNK_DAYS,
)
from market_data.groww_fetch import IST, NEW_API_MAX_DAYS, _build_windows
from screening.batch_runner import SYMBOL_PATTERN, ScanSafetyError


def queue_size(path: Path) -> int:
    """Count an explicit file without interpreting it as a vetted pre-filter."""
    path = Path(path)
    if not path.is_file():
        raise ScanSafetyError(f"Request-budget queue does not exist: {path}")
    symbols = [s.strip().upper() for s in path.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
    if not symbols or len(symbols) != len(set(symbols)):
        raise ScanSafetyError("Request-budget queue must be nonempty with unique symbols")
    if any(SYMBOL_PATTERN.fullmatch(s) is None for s in symbols):
        raise ScanSafetyError("Request-budget queue contains an invalid symbol")
    return len(symbols)


def estimate_request_budget(
    symbol_count: int,
    *,
    batch_size: int = 1,
    max_batches: int = 1,
    hourly_days: int = DEFAULT_HOURLY_HISTORY_DAYS,
    setup_days: int = DEFAULT_SETUP_15M_DAYS,
    hourly_chunk_days: int = GROWW_HOURLY_REQUEST_CHUNK_DAYS,
    setup_chunk_days: int = GROWW_15M_REQUEST_CHUNK_DAYS,
    as_of: Optional[datetime] = None,
) -> Dict:
    """Estimate initial 1H and 15m fetches for a *bounded invocation*.

    It is intentionally impossible to give a maximum total SDK request count:
    conditional gap repairs, provider retries, instrument lookups and other
    integrations may add requests; this report labels its scope explicitly.
    """
    if isinstance(symbol_count, bool) or not isinstance(symbol_count, int) or symbol_count < 1:
        raise ValueError("symbol_count must be a positive integer")
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in
           (batch_size, max_batches, hourly_days, setup_days, hourly_chunk_days, setup_chunk_days)):
        raise ValueError("Batch limits and history/chunk days must be positive integers")
    if batch_size > 4:
        raise ValueError("batch_size must be between 1 and 4 (batch runner limit)")
    end = as_of or datetime.now(IST)
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("as_of must be timezone aware")
    end = end.astimezone(IST)
    hourly_chunk = min(hourly_chunk_days, NEW_API_MAX_DAYS[60])
    setup_chunk = min(setup_chunk_days, NEW_API_MAX_DAYS[15])
    hourly_windows = len(_build_windows(end-timedelta(days=hourly_days), end, hourly_chunk))
    setup_windows = len(_build_windows(end-timedelta(days=setup_days), end, setup_chunk))
    planned = min(symbol_count, batch_size * max_batches)
    launches = (planned + batch_size - 1) // batch_size
    candles_per_symbol = hourly_windows + setup_windows
    return {
        "schema_version": 1,
        "mode": "OFFLINE_ESTIMATE_NO_PROVIDER_REQUESTS",
        "as_of_ist": end.isoformat(),
        "queue_symbols": symbol_count,
        "planned_symbols_this_invocation": planned,
        "planned_child_launches": launches,
        "batch_size": batch_size,
        "max_batches": max_batches,
        "hourly": {"lookback_days": hourly_days, "configured_chunk_days": hourly_chunk_days,
                   "effective_chunk_days": hourly_chunk, "baseline_requests_per_symbol": hourly_windows},
        "setup_15m": {"lookback_days": setup_days, "configured_chunk_days": setup_chunk_days,
                       "effective_chunk_days": setup_chunk, "baseline_requests_per_symbol": setup_windows},
        "baseline_candle_requests_per_symbol": candles_per_symbol,
        "baseline_candle_requests_this_invocation": planned*candles_per_symbol,
        "baseline_candle_requests_full_queue": symbol_count*candles_per_symbol,
        "additional_requests": "Unknown: optional instrument lookup, conditional 1H gap repairs, SDK/provider retries and other source calls",
        "warning": "No API rate limiting, provider quota, time prediction, network calls, permissions to scan or orders. The baseline is NOT a ceiling.",
    }
