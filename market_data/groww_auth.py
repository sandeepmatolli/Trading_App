# market_data/groww_auth.py

from __future__ import annotations

from growwapi import GrowwAPI

from config import (
    GROWW_ACCESS_TOKEN,
    GROWW_API_KEY,
    GROWW_API_SECRET,
)


def get_groww_api() -> GrowwAPI:
    """
    Create an authenticated GrowwAPI client.

    Supported configuration:
      1. GROWW_ACCESS_TOKEN
      2. GROWW_API_KEY + GROWW_API_SECRET

    Credentials are read from environment variables via config.py.
    """
    if GROWW_ACCESS_TOKEN:
        return GrowwAPI(GROWW_ACCESS_TOKEN)

    if GROWW_API_KEY and GROWW_API_SECRET:
        access_token = GrowwAPI.get_access_token(
            api_key=GROWW_API_KEY,
            secret=GROWW_API_SECRET,
        )
        return GrowwAPI(access_token)

    raise RuntimeError(
        "Groww credentials are not configured. Set GROWW_ACCESS_TOKEN, "
        "or set both GROWW_API_KEY and GROWW_API_SECRET. "
        "Use 'python main.py --validate-only' if you only want to validate "
        "the Screener CSV."
    )


if __name__ == "__main__":
    client = get_groww_api()
    print("Groww API client created successfully.")
