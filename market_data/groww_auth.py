"""Initialize the Groww SDK and optionally govern direct market-data SDK calls.

The guard covers the SDK methods this research project calls: instrument
resolution and historical candles. It never invokes an endpoint by itself,
does not pace authentication performed before wrapping, and is PER PROCESS.
It is not a claim about the provider's published rate limits or a cross-process
quota. Existing groww_fetch.py chunking remains unchanged.
"""
from __future__ import annotations

from collections import Counter
import math
import os
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from growwapi import GrowwAPI

from config import GROWW_ACCESS_TOKEN, GROWW_API_KEY, GROWW_API_SECRET


_GUARDED_METHODS = frozenset({
    "get_historical_candles",
    "get_instrument_by_exchange_and_trading_symbol",
})


class GrowwSDKCallLimitReached(RuntimeError):
    """An explicitly configured per-child call ceiling stopped an SDK call."""


class GuardedGrowwClient:
    """Transparent SDK adapter with serialized start pacing and safe counts.

    Locks are held through each guarded SDK call: two threads cannot bypass
    the configured start spacing or call ceiling in the same Python process.
    Failed SDK attempts count toward the ceiling, preventing rapid retries.
    Other SDK properties/methods are delegated unchanged; this V1 research
    pipeline uses only the guarded methods for market-data access.
    """

    def __init__(
        self,
        client: Any,
        *,
        min_start_gap_seconds: float = 0.0,
        max_calls: int = 0,
        log_calls: bool = False,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            isinstance(min_start_gap_seconds, bool)
            or not isinstance(min_start_gap_seconds, (int, float))
            or not math.isfinite(min_start_gap_seconds)
            or not 0 <= min_start_gap_seconds <= 60
        ):
            raise ValueError("Groww SDK minimum start gap must be finite and 0..60 seconds")
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or not 0 <= max_calls <= 100000:
            raise ValueError("Groww SDK per-child call ceiling must be an integer 0..100000")
        self._client = client
        self._gap = float(min_start_gap_seconds)
        self._max = max_calls  # zero disables the ceiling; it never implies a provider quota
        self._log = bool(log_calls)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_start: Optional[float] = None
        self._attempted = 0
        self._succeeded = 0
        self._failed = 0
        self._by_method: Counter = Counter()

    def __getattr__(self, name: str) -> Any:
        # Look up the underlying attribute FIRST. A missing optional SDK method
        # must remain missing so hasattr() keeps the original Groww behaviour.
        method = getattr(self._client, name)
        if name not in _GUARDED_METHODS or not callable(method):
            return method

        def guarded(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                if self._max and self._attempted >= self._max:
                    raise GrowwSDKCallLimitReached(
                        "Configured Groww SDK calls-per-child ceiling reached BEFORE request; "
                        "stop/review this child scan. Not a provider quota."
                    )
                delay = 0.0
                if self._last_start is not None and self._gap:
                    delay = max(0.0, self._gap - (self._clock() - self._last_start))
                    if delay:
                        self._sleep(delay)
                self._last_start = self._clock()
                self._attempted += 1
                self._by_method[name] += 1
                if self._log:
                    # Never print symbols, tokens, request arguments or responses.
                    print(
                        f"Groww SDK gate: method={name} attempt={self._attempted} "
                        f"wait_seconds={delay:.3f} per_child_cap={self._max or 'disabled'}"
                    )
                try:
                    response = method(*args, **kwargs)
                except Exception:
                    self._failed += 1
                    raise
                else:
                    self._succeeded += 1
                    return response

        return guarded

    def request_stats(self) -> Dict[str, Any]:
        """Metadata only; no provider payloads, credentials or symbols."""
        with self._lock:
            return {
                "scope": "PER_PYTHON_PROCESS_GUARDED_SDK_METHODS_ONLY",
                "min_start_gap_seconds": self._gap,
                "configured_max_calls": self._max,
                "attempted": self._attempted,
                "succeeded": self._succeeded,
                "failed": self._failed,
                "by_method": dict(self._by_method),
                "exclusions": "Access-token acquisition, SDK-internal retries, other SDK methods, other child processes",
            }


def _environment_settings() -> Tuple[float, int, bool]:
    """Config is read after config.py has loaded the project's local .env."""
    raw_gap = os.getenv("GROWW_SDK_MIN_START_GAP_SECONDS", "0").strip() or "0"
    raw_max = os.getenv("GROWW_SDK_MAX_CALLS_PER_CHILD", "0").strip() or "0"
    raw_log = os.getenv("GROWW_SDK_CALL_TELEMETRY", "false").strip().lower()
    try:
        gap = float(raw_gap)
        maximum = int(raw_max)
    except ValueError as exc:
        raise ValueError("Invalid Groww SDK pacing configuration in .env") from exc
    if raw_log not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
        raise ValueError("GROWW_SDK_CALL_TELEMETRY must be true or false")
    # Constructor also validates infinities, NaN and range constraints.
    GuardedGrowwClient(object(), min_start_gap_seconds=gap, max_calls=maximum)
    return gap, maximum, raw_log in {"true", "1", "yes", "on"}


def _wrap(client: Any, settings: Tuple[float, int, bool]) -> GuardedGrowwClient:
    gap, maximum, log_calls = settings
    return GuardedGrowwClient(
        client, min_start_gap_seconds=gap, max_calls=maximum, log_calls=log_calls,
    )


def get_groww_api() -> GuardedGrowwClient:
    """Return authenticated SDK behind an opt-in, per-process request guard.

    Authentication priority: GROWW_ACCESS_TOKEN, then API key + secret.
    All credential values stay in the local .env and are never logged.
    The default gap=0 and ceiling=0 do not change existing fetch pacing.
    """
    settings = _environment_settings()  # fail before any authentication request

    if GROWW_ACCESS_TOKEN:
        try:
            client = GrowwAPI(GROWW_ACCESS_TOKEN)
            print("Groww authentication configured using access token.")
            return _wrap(client, settings)
        except Exception as exc:
            raise RuntimeError(
                "Failed to initialize Groww client using GROWW_ACCESS_TOKEN."
            ) from exc

    if GROWW_API_KEY and GROWW_API_SECRET:
        try:
            access_token = GrowwAPI.get_access_token(
                api_key=GROWW_API_KEY, secret=GROWW_API_SECRET,
            )
            if not access_token:
                raise RuntimeError("Groww returned an empty access token.")
            client = GrowwAPI(access_token)
            print("Groww authentication successful using API key + secret.")
            return _wrap(client, settings)
        except Exception as exc:
            raise RuntimeError(
                "Groww API authentication failed. Check the API key/secret "
                "and today's Groww API-key approval."
            ) from exc

    raise RuntimeError(
        "\nGroww credentials are not configured.\n\n"
        "Create a local .env with GROWW_ACCESS_TOKEN, or GROWW_API_KEY and "
        "GROWW_API_SECRET. Never commit credentials to Git."
    )


def test_groww_authentication() -> bool:
    """Initialize authentication without displaying secret values."""
    get_groww_api()
    print("Groww authentication test: OK")
    return True


if __name__ == "__main__":
    test_groww_authentication()