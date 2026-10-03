# Groww API reference memo for Trading_App

**Source basis:** Groww Python SDK documentation supplied by the project owner, whose supplied changelog showed `growwapi==1.5.0`. The repository pins `growwapi==1.5.0`. This is a project memo, **not** a verified statement of current Groww API limits or an authorization to make live calls; confirm live behavior and usage rules against the current official provider documentation.

## Authentication supported by the existing code

`market_data/groww_auth.py` loads credentials from `config.py`, which reads the local `.env`. If a nonempty `GROWW_ACCESS_TOKEN` is configured, it initializes `GrowwAPI` with that token. Otherwise, if both `GROWW_API_KEY` and `GROWW_API_SECRET` are present, it obtains a token using `GrowwAPI.get_access_token` before constructing the client.

Illustrative **API key + secret** flow from the supplied SDK material (use local environment values in the actual project, not embedded credentials):

```python
from growwapi import GrowwAPI

api_key = "YOUR_API_KEY"
secret = "YOUR_API_SECRET"
access_token = GrowwAPI.get_access_token(api_key=api_key, secret=secret)
groww = GrowwAPI(access_token)
```

Illustrative **pre-obtained token** initialization:

```python
from growwapi import GrowwAPI

groww = GrowwAPI("YOUR_ACCESS_TOKEN")
```

The actual application entrypoint uses `get_groww_api()` from `market_data/groww_auth.py`, which returns an optional per-process guarded adapter. Never commit or print credentials. The opt-in guard does not cover authentication requests, SDK-internal retries or account-wide usage. See `docs/GROWW_SDK_GUARD_OPERATIONS.md` for the precise scope and configuration.

## Historical data in this repository

`market_data/groww_fetch.py` resolves the instrument where the installed SDK provides the lookup method and requests historical candles in bounded chunks. One-hour source candles form Daily, then Weekly and Monthly; 15-minute source candles form fixed session-aligned 75-minute setup windows. Data-quality and closed-bar checks must still pass independently. See `market_data/groww_fetch.py` for the implemented chunk sizes and interval mapping, rather than treating this memo as a live API specification.
