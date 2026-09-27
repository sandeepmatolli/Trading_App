# Groww API Reference (v1.5.0)

## Base URL & Endpoints
- **Base URL:** `https://api.groww.in/v1`.
- **Authentication:** `POST /token/api/access` to obtain an access token (using your API key/secret, plus any 2FA). Limits: max 5 calls/sec (30/min).
- **Instruments:** 
  - `GET /instruments` (list all instruments)  
  - `GET /instruments/search?exchange=NSE&text=<SYMBOL>` (find by name/symbol).  
  Use these to resolve trading symbols and exchange tokens.
- **Market Data (Live):**
  - `GET /market-data/quote?exchange=NSE&trading_symbol=<SYM>&segment=EQ` – current quote.
  - `GET /market-data/ohlc?exchange=NSE&trading_symbol=<SYM>&segment=EQ&interval=<INT>` – intraday OHLC (interval in minutes).  
- **Historical Data:**
  - `GET /history/candles?exchange=NSE&trading_symbol=<SYM>&segment=EQ&interval=<INT>&start_time=<ISO>&end_time=<ISO>`.  
    (Typically accessed via the Python SDK `get_historical_data`. Example in Python SDK.)

## Rate Limits
Groww enforces limits on all endpoints. As of v1.5.0:  
- **Auth token:** 5 requests/sec (30/min).  
- **Orders API:** 10 requests/sec (250/min).  
- **Market data (live or historical):** 10 req/sec (300/min).  
- **Non-trading data (e.g. fundamentals, news):** 20 req/sec (500/min).  
If limits are exceeded, calls return HTTP 429; the Python SDK raises `GrowwAPIRateLimitException`. Plan to throttle or batch requests accordingly.

## Candle Intervals & Chunking
Supported intervals and maximum history per call (source: Groww docs):

- **1-minute:** up to 7 days per request (3 months total data retention).  
- **5-minute:** up to 15 days (last 6 months).  
- **10-minute:** up to 30 days.  
- **15-minute:** *(not explicitly documented)* – use multiple 5-min requests or 1-min as needed. We recommend ≤30-day chunks (as used in our code).  
- **1-hour:** up to 150 days.  
- **4-hour:** up to 365 days (1 year).  
- **Daily:** up to 1080 days (~3 years).  
- **Weekly:** effectively unlimited.  

Always partition your date range into chunks no larger than these limits. For example, for hourly data we use ~60-day chunks, and for 15-minute data ~30-day chunks, to be safe.

## Known Quirks
- **Market Hours:** NSE trading is from 09:15 to 15:30 IST (no lunch break). All daily candles end at 15:30.  
- **15:30 Volume NaN:** We have observed that the final daily candle (timestamp 15:30) may show `volume: NaN`. This appears to be a data quirk. In practice, treat that as zero or recompute daily volume separately if needed.  
- **Missing Data:** The API may not return data beyond the supported lookback (e.g. >3 years). Confirm the `latest_time` in each response.

## Instrument Lookup
Always resolve the exact trading symbol and segment. Example (Python SDK):
```python
from growwapi import GrowwApi
api = GrowwApi(access_token)
inst = api.search_instrument(exchange="NSE", text="TCS")  # search by symbol/name
print(inst)
```
Use the returned `trading_symbol` and `exchange`/`segment` in later calls. (Our helper `resolve_groww_instrument()` does this.)

## Error Handling Patterns
Wrap all API calls in try/except. The `growwapi` library raises `GrowwAPIException` for invalid requests or auth errors, and specialized exceptions for rate limits or timeouts. Example:
```python
try:
    candles = api.get_historical_data(exchange="NSE", trading_symbol="TCS", ...)
except GrowwAPIRateLimitException:
    # Back off or retry
except GrowwAPIException as e:
    # Handle other errors (e.g. invalid credentials, network issues)
```

## Examples

- **Python (growwapi):**  
  ```python
  from growwapi import GrowwApi
  from growwapi.auth import Credentials
  # Obtain access token (if not already done)
  creds = Credentials(api_key, api_secret, totp=two_factor_code)
  access_token = creds.get_access_token()
  api = GrowwApi(access_token)
  # Fetch 60-min candles for RELIANCE in September 2026
  from datetime import date
  candles = api.get_historical_data(exchange="NSE", trading_symbol="RELIANCE", segment="EQ",
                                    interval="60",
                                    from_date=date(2026, 9, 1), to_date=date(2026, 9, 30))
  print(candles)
  ```
  (This mirrors Groww’s own example.)
  
- **cURL:**  
  ```
  curl -X GET "https://api.groww.in/v1/history/candles?exchange=NSE&trading_symbol=RELIANCE&segment=EQ&interval=60&start_time=2026-09-01T09:15:00&end_time=2026-09-30T15:30:00" \
       -H "Authorization: Bearer <ACCESS_TOKEN>"
  ```

## Recommended Tests
- **Memo File Exists:** Confirm `docs/GROWW_API_MEMO.md` is present in the repo.  
- **Content Checks:** Ensure the file mentions key phrases (e.g. “Rate Limits”, “Candle Interval”, “Groww API”). This verifies that the document was loaded and parsed.  
- **Parsing (optional):** If you load the markdown in code, validate its structure (e.g. using a markdown parser to ensure headings exist).

