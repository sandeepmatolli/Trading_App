# Groww SDK guard — operating notes (V1 research only)

This guide describes the existing `market_data/groww_auth.py` wrapper as implemented on
`dev2` at `c5d4b4faf1e4a8dc0393f7a2702057f5163deecb`.
It does **not** authorize a live scan or define Groww's account-level API limits.
No broker orders are placed by the guard or the offline tests.

## Scope and defaults

| Local `.env` key | Default | Meaning |
|---|---:|---|
| `GROWW_SDK_MIN_START_GAP_SECONDS` | `0` | Optional minimum gap between **starts** of guarded SDK calls within a Python process. Valid finite range: 0–60 seconds. |
| `GROWW_SDK_MAX_CALLS_PER_CHILD` | `0` | Optional hard ceiling on attempted guarded method calls in one child process. Zero means disabled; valid integer range 0–100000. |
| `GROWW_SDK_CALL_TELEMETRY` | `false` | Optional method name, count, wait and configured-cap log lines; no symbols, request arguments, payloads or secrets are logged by the wrapper. |

Guarded methods are `get_historical_candles` and, where provided by the installed SDK,
`get_instrument_by_exchange_and_trading_symbol`. The guard also preserves SDK constants
and optional-lookup detection with `hasattr`. A failed attempt counts. Calls over an
explicit cap raise `GrowwSDKCallLimitReached` **before** contacting the underlying SDK.

Excluded: access-token acquisition, SDK-internal retries, other SDK methods, other
child processes, NSE RSS and Screener. The fetcher's existing 0.15-second spacing
between historical request chunks remains separate. `batch_scan.py --cooldown-seconds`
spaces child *launches*, not the requests inside a child.

## Example opt-in — not a Groww recommendation

Put these entries in your **local** `.env` only, if you choose to enable them after
checking provider terms and usage:

```dotenv
GROWW_SDK_MIN_START_GAP_SECONDS=1.0
GROWW_SDK_MAX_CALLS_PER_CHILD=0
GROWW_SDK_CALL_TELEMETRY=true