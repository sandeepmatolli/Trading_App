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
    Create and return an authenticated GrowwAPI client.

    Authentication priority:

    1. Direct access token:
       GROWW_ACCESS_TOKEN

    2. API key + secret:
       GROWW_API_KEY
       GROWW_API_SECRET

    Secrets are loaded by config.py from the local .env file.

    No credentials are printed or logged.
    """

    # --------------------------------------------------------
    # OPTION 1:
    # Direct access token
    # --------------------------------------------------------

    if GROWW_ACCESS_TOKEN:
        try:
            groww = GrowwAPI(
                GROWW_ACCESS_TOKEN
            )

            print(
                "Groww authentication configured "
                "using access token."
            )

            return groww

        except Exception as exc:
            raise RuntimeError(
                "Failed to initialize Groww client "
                "using GROWW_ACCESS_TOKEN."
            ) from exc

    # --------------------------------------------------------
    # OPTION 2:
    # API key + secret
    # --------------------------------------------------------

    if GROWW_API_KEY and GROWW_API_SECRET:
        try:
            access_token = (
                GrowwAPI.get_access_token(
                    api_key=GROWW_API_KEY,
                    secret=GROWW_API_SECRET,
                )
            )

            if not access_token:
                raise RuntimeError(
                    "Groww returned an empty access token."
                )

            groww = GrowwAPI(
                access_token
            )

            print(
                "Groww authentication successful "
                "using API key + secret."
            )

            return groww

        except Exception as exc:
            raise RuntimeError(
                "Groww API authentication failed. "
                "Check that the API key and secret are "
                "correct and that today's API-key approval "
                "has been completed in Groww."
            ) from exc

    # --------------------------------------------------------
    # NO CREDENTIALS
    # --------------------------------------------------------

    raise RuntimeError(
        "\nGroww credentials are not configured.\n\n"
        "Create this file:\n"
        "  .env\n\n"
        "Then configure either:\n\n"
        "  GROWW_API_KEY='your_api_key'\n"
        "  GROWW_API_SECRET='your_api_secret'\n\n"
        "or:\n\n"
        "  GROWW_ACCESS_TOKEN='your_access_token'\n\n"
        "Do not put credentials directly inside Python files."
    )


def test_groww_authentication() -> bool:
    """
    Generate/initialize authentication without printing
    sensitive information.

    Returns True when an authenticated GrowwAPI client
    is successfully created.
    """

    get_groww_api()

    print(
        "Groww authentication test: OK"
    )

    return True


if __name__ == "__main__":
    test_groww_authentication()