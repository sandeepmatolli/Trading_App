"""Read-only preflight comparing a *per-child* Groww SDK cap with baseline calls.

No authentication, API access, orders, checkpoint changes, or scan authorization.
This is not a provider quota or a proof that a configured cap will suffice:
optional lookup, conditional gap repair and internal SDK retries are outside
our initial candle-window baseline.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from market_data.groww_auth import _environment_settings
from screening.request_budget import estimate_request_budget


def build_guard_preflight(
    symbol_count: int,
    *,
    batch_size: int = 1,
    max_batches: int = 1,
    max_calls_override: Optional[int] = None,
    as_of: Optional[datetime] = None,
) -> Dict:
    """Preview largest child of a bounded invocation, not the whole queue.

    max_calls_override is hypothetical only and does not edit the local .env.
    When absent, inspect and validate the existing guard configuration without
    creating a Groww client. Zero means the per-child cap is disabled.
    """
    budget = estimate_request_budget(
        symbol_count, batch_size=batch_size, max_batches=max_batches, as_of=as_of,
    )
    if max_calls_override is None:
        gap, maximum, telemetry = _environment_settings()
        source = "LOCAL_ENV_GUARD_SETTINGS"
    else:
        if (isinstance(max_calls_override, bool)
                or not isinstance(max_calls_override, int)
                or not 0 <= max_calls_override <= 100000):
            raise ValueError("max_calls_override must be an integer from 0 to 100000")
        maximum = max_calls_override
        gap, telemetry = None, None
        source = "HYPOTHETICAL_OVERRIDE_NO_ENV_MODIFICATION"

    largest_child = min(batch_size, budget["planned_symbols_this_invocation"])
    candle_floor = largest_child * budget["baseline_candle_requests_per_symbol"]
    initial_with_lookup = candle_floor + largest_child
    if maximum == 0:
        status = "PER_CHILD_HARD_CAP_DISABLED"
    elif maximum < candle_floor:
        status = "BELOW_BASELINE_CANDLE_CALLS_IF_ALL_REACH_BOTH_FETCHES"
    elif maximum < initial_with_lookup:
        status = "BELOW_BASELINE_PLUS_OPTIONAL_LOOKUP_PER_SYMBOL"
    else:
        status = "AT_OR_ABOVE_INITIAL_ESTIMATE_NOT_PROVEN_SUFFICIENT"

    return {
        "schema_version": 1,
        "mode": "OFFLINE_GUARD_PREFLIGHT_NO_PROVIDER_REQUESTS",
        "as_of_ist": budget["as_of_ist"],
        "settings_source": source,
        "queue_symbols": budget["queue_symbols"],
        "planned_symbols_this_invocation": budget["planned_symbols_this_invocation"],
        "planned_child_launches": budget["planned_child_launches"],
        "largest_planned_child_symbols": largest_child,
        "baseline_candle_calls_per_symbol": budget["baseline_candle_requests_per_symbol"],
        "baseline_candle_calls_largest_child": candle_floor,
        "optional_initial_lookups_largest_child_if_supported": largest_child,
        "baseline_plus_optional_lookup_largest_child": initial_with_lookup,
        "configured_or_hypothetical_max_calls_per_child": maximum,
        "configured_min_start_gap_seconds": gap,
        "configured_telemetry": telemetry,
        "cap_comparison": status,
        "limitations": [
            "Comparison is per child process, never the account-wide Groww quota.",
            "Only the initial 1H/15m candle windows are baseline-counted; instrument lookup is optional.",
            "Gap repairs, provider or SDK-internal retries, authentication and other sources are not bounded by this preview.",
            "A low cap might interrupt a full fetch. A high cap does not guarantee successful data collection.",
            "This command never starts a scan, authenticates, accesses the network or places orders.",
        ],
    }
