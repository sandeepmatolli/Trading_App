from datetime import datetime
from types import SimpleNamespace

from news.nse_historical_corporate_actions import (
    fetch_historical_actions_for_discontinuities,
)


class FakeResponse:
    def __init__(
        self,
        payload=None,
        *,
        url="https://example.test",
        status_code=200,
    ):
        self._payload = payload
        self.url = url
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(
                f"HTTP {self.status_code}"
            )

    def json(self):
        return self._payload


class FakeSession:
    def __init__(
        self,
        payload,
        *,
        fail_warm=False,
        fail_api=False,
    ):
        self.payload = payload
        self.fail_warm = fail_warm
        self.fail_api = fail_api
        self.calls = []

    def get(
        self,
        url,
        *,
        headers=None,
        params=None,
        timeout=None,
    ):
        self.calls.append(
            {
                "url": url,
                "params": params,
                "timeout": timeout,
            }
        )

        if params is None:
            if self.fail_warm:
                raise RuntimeError(
                    "warmup failed"
                )

            return FakeResponse(
                {},
                url=url,
            )

        if self.fail_api:
            raise RuntimeError(
                "api failed"
            )

        return FakeResponse(
            self.payload,
            url=url,
        )


def _guard(
    current_ts="2024-10-28 00:00:00+05:30",
):
    return {
        "detected": True,
        "events": [
            {
                "current_ts": current_ts,
                "previous_ts": (
                    "2024-10-25 00:00:00+05:30"
                ),
                "open_ratio": 0.503445,
                "close_ratio": 0.502448,
                "matched_common_ratio": 0.5,
                "likely_corporate_action": True,
            }
        ],
    }


def test_no_discontinuity_skips_network():
    created = []

    def factory():
        created.append(True)
        return FakeSession([])

    result = (
        fetch_historical_actions_for_discontinuities(
            "RELIANCE",
            {
                "detected": False,
                "events": [],
            },
            session_factory=factory,
        )
    )

    assert result["requested"] is False
    assert result["status"] == (
        "NOT_REQUIRED_NO_DISCONTINUITY"
    )
    assert created == []


def test_reliance_like_bonus_is_normalized_and_exact_symbol_filtered():
    payload = [
        {
            "symbol": "RELIANCE",
            "comp": "Reliance Industries Limited",
            "series": "EQ",
            "faceVal": "10",
            "subject": "Bonus 1:1",
            "exDate": "28-Oct-2024",
            "recDate": "28-Oct-2024",
            "bcStartDate": "-",
            "bcEndDate": "-",
        },
        {
            "symbol": "OTHER",
            "comp": "Other Limited",
            "series": "EQ",
            "faceVal": "10",
            "subject": "Bonus 1:1",
            "exDate": "28-Oct-2024",
            "recDate": "28-Oct-2024",
        },
    ]

    session = FakeSession(
        payload
    )

    result = (
        fetch_historical_actions_for_discontinuities(
            "RELIANCE",
            _guard(),
            session_factory=lambda: session,
            as_of=datetime.fromisoformat(
                "2026-09-28T14:00:00+05:30"
            ),
        )
    )

    assert result["requested"] is True
    assert result["available"] is True
    assert result["complete"] is True
    assert result["status"] == (
        "AVAILABLE_COMPLETE"
    )
    assert result["action_count"] == 1

    action = result["actions"][0]

    assert action["symbol"] == "RELIANCE"
    assert action["action_type"] == "bonus"
    assert (
        action["theoretical_price_factor"]
        == 0.5
    )
    assert action["ex_date"] == (
        "2024-10-28"
    )
    assert action["record_date"] == (
        "2024-10-28"
    )

    api_call = session.calls[1]

    assert api_call["params"][
        "from_date"
    ] == "21-10-2024"

    assert api_call["params"][
        "to_date"
    ] == "04-11-2024"


def test_warmup_failure_is_unavailable_and_fail_closed():
    session = FakeSession(
        [],
        fail_warm=True,
    )

    result = (
        fetch_historical_actions_for_discontinuities(
            "RELIANCE",
            _guard(),
            session_factory=lambda: session,
        )
    )

    assert result["requested"] is True
    assert result["available"] is False
    assert result["complete"] is False
    assert result["status"] == "UNAVAILABLE"
    assert result["errors"]


def test_api_failure_marks_incomplete():
    session = FakeSession(
        [],
        fail_api=True,
    )

    result = (
        fetch_historical_actions_for_discontinuities(
            "RELIANCE",
            _guard(),
            session_factory=lambda: session,
        )
    )

    assert result["requested"] is True
    assert result["available"] is False
    assert result["complete"] is False
    assert result["status"] == "UNAVAILABLE"
    assert result["errors"]
