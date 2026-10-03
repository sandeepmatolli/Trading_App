# Groww SDK guard — operating notes (V1 research only)

This guide describes the opt-in adapter in `market_data/groww_auth.py`. It does **not** authorize a live scan, define the provider's rate limit, verify account-wide quota or place broker orders. No live requests are made by the guard's offline regression tests.

## Configuration and scope

| Local `.env` setting | Default | What it controls |
|---|---:|---|
| `GROWW_SDK_MIN_START_GAP_SECONDS` | `0` | Optional gap between starts of guarded SDK calls within one Python process; finite 0–60 seconds |
| `GROWW_SDK_MAX_CALLS_PER_CHILD` | `0` | Optional ceiling of attempted guarded method calls per child; 0 disables it, accepted range 0–100000 |
| `GROWW_SDK_CALL_TELEMETRY` | `false` | Metadata-only console messages for method name, attempt count, wait and configured ceiling |

The guarded methods are `get_historical_candles` and (if the installed SDK exposes it) `get_instrument_by_exchange_and_trading_symbol`. SDK attributes and the fetcher's optional-method `hasattr` fallback are delegated correctly. The adapter serializes its guarded method calls with a process-local lock. A failed SDK attempt counts toward the ceiling. Once the explicitly configured ceiling is exhausted, `GrowwSDKCallLimitReached` stops the next call **before** invoking the underlying SDK.

**Outside this guard:** access-token acquisition, SDK-internal retries, other SDK methods, other child processes, NSE RSS, Screener, and any other caller not using `get_groww_api()`. The fetcher's existing 0.15-second spacing between historical chunks is separate. `batch_scan.py --cooldown-seconds` spaces child starts, not calls inside a child. The adapter is **not a shared account-wide rate limiter**.

## Optional example — not a provider recommendation

After checking the current provider terms and your actual usage, choose values in your **local** `.env`. The example below enables one-second spacing and method telemetry, but does not impose a hard attempted-call ceiling:

```dotenv
GROWW_SDK_MIN_START_GAP_SECONDS=1.0
GROWW_SDK_MAX_CALLS_PER_CHILD=0
GROWW_SDK_CALL_TELEMETRY=true
```

A nonzero ceiling should be selected only with enough room for the selected batch's instrument lookups, initial 1H/15m chunks, and possible gap repairs. The offline estimate below counts initial candle windows only. It is **not** an upper bound and does not include authentication, optional instrument lookups, gap repair or provider/SDK retries:

```powershell
python request_budget.py --symbol-count 1 --batch-size 1 --max-batches 1
python -m pytest -q tests/test_groww_request_guard.py tests/test_groww_fetch_guard_integration.py
```

Under the repository's current default lookbacks/chunking, the estimate is 13 initial 1H plus 3 initial 15m candle requests per symbol, before additional calls; do not extrapolate this into a validated provider quota.

## Failures and operational boundaries

A configuration error is rejected before token authentication. An exceeded ceiling raises `GrowwSDKCallLimitReached` before an additional guarded request. The `main.py` research pipeline can serialize a caught exception as a `DATA_REJECT` with a `Pipeline error:` risk flag; `screening/batch_runner.py` treats such a row as an unresolved pipeline failure for review/retry, **not** a finished research decision. Inspect the batch's `main.log` and the preserved checkpoint; do not call that result a genuine data-quality rejection or manually mark it completed.

The optional telemetry printed by the guard does not include symbols, tokens, request arguments or returned payloads. Other application logs may contain symbols and ordinary diagnostic data. `request_stats()` returns process-local metadata when called on the adapter; its output is not an account-wide usage ledger. Keep `.env`, raw exports and `data/output/` private and out of Git.

For campaign use, record completed research with `python campaign_scan.py record` before creating a new campaign session, preserve archived child evidence, and use the latest current-day pre-filter. Historical `CANDIDATE` output always needs fresh manual price, event, data and closed-candle review; no script in this guide authorizes a trade.
