from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import requests

from config import (
    NSE_CORPORATE_ACTIONS_API_URL,
    NSE_HISTORICAL_CA_PADDING_DAYS,
    NSE_HISTORICAL_CA_TIMEOUT_SECONDS,
    NSE_WEB_BASE_URL,
)
from news.news_engine import parse_corporate_action_factor


IST = ZoneInfo("Asia/Kolkata")

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": (
        "https://www.nseindia.com/"
        "companies-listing/corporate-filings-actions"
    ),
}

DATE_FORMATS = (
    "%d-%b-%Y",
    "%d-%B-%Y",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%Y/%m/%d",
)


def _as_ist_datetime(value) -> Optional[datetime]:
    if value in (None, ""):
        return None

    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value))
        )
    except Exception:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=IST)

    return parsed.astimezone(IST)


def _parse_nse_date(value) -> Optional[date]:
    raw = str(value or "").strip()

    if not raw or raw in {"-", "--", "NA", "N/A"}:
        return None

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except Exception:
            continue

    return None


def _format_query_date(value: date) -> str:
    return value.strftime("%d-%m-%Y")


def _row_value(row: Dict, *names: str):
    for name in names:
        if name in row and row.get(name) not in (None, ""):
            return row.get(name)
    return None


def _normalize_action_row(
    row: Dict,
    *,
    retrieved_at: datetime,
) -> Dict:
    symbol = str(
        _row_value(
            row,
            "symbol",
            "Symbol",
        )
        or ""
    ).strip().upper()

    company = str(
        _row_value(
            row,
            "comp",
            "companyName",
            "company",
            "Company Name",
        )
        or ""
    ).strip()

    series = str(
        _row_value(
            row,
            "series",
            "Series",
        )
        or ""
    ).strip().upper()

    purpose = str(
        _row_value(
            row,
            "subject",
            "purpose",
            "Purpose",
        )
        or ""
    ).strip()

    ex_raw = _row_value(
        row,
        "exDate",
        "ex_date",
        "Ex Date",
        "EX-DATE",
    )

    record_raw = _row_value(
        row,
        "recDate",
        "recordDate",
        "record_date",
        "Record Date",
        "RECORD DATE",
    )

    bc_start_raw = _row_value(
        row,
        "bcStartDate",
        "bookClosureStartDate",
        "book_closure_start_date",
    )

    bc_end_raw = _row_value(
        row,
        "bcEndDate",
        "bookClosureEndDate",
        "book_closure_end_date",
    )

    pay_raw = _row_value(
        row,
        "payDate",
        "paymentDate",
        "payment_date",
    )

    face_value_raw = _row_value(
        row,
        "faceVal",
        "faceValue",
        "face_value",
    )

    try:
        face_value = (
            float(face_value_raw)
            if face_value_raw not in (None, "", "-")
            else None
        )
    except (TypeError, ValueError):
        face_value = None

    parsed_factor = parse_corporate_action_factor(
        purpose
    )

    action_dates: List[Dict] = []

    for label, raw in (
        ("ex date", ex_raw),
        ("record date", record_raw),
    ):
        parsed = _parse_nse_date(raw)

        if parsed is None:
            continue

        action_dates.append(
            {
                "label": label,
                "date": parsed.isoformat(),
            }
        )

    result = {
        "symbol": symbol,
        "company": company,
        "series": series,
        "purpose": purpose,
        "title": purpose,
        "summary": "",
        "face_value": face_value,
        "ex_date": (
            _parse_nse_date(ex_raw).isoformat()
            if _parse_nse_date(ex_raw)
            else None
        ),
        "record_date": (
            _parse_nse_date(record_raw).isoformat()
            if _parse_nse_date(record_raw)
            else None
        ),
        "book_closure_start_date": (
            _parse_nse_date(
                bc_start_raw
            ).isoformat()
            if _parse_nse_date(bc_start_raw)
            else None
        ),
        "book_closure_end_date": (
            _parse_nse_date(
                bc_end_raw
            ).isoformat()
            if _parse_nse_date(bc_end_raw)
            else None
        ),
        "payment_date": (
            _parse_nse_date(pay_raw).isoformat()
            if _parse_nse_date(pay_raw)
            else None
        ),
        "remarks": str(
            _row_value(
                row,
                "remarks",
                "Remarks",
            )
            or ""
        ).strip(),
        "source_id": "nse_historical_corporate_actions",
        "source_type": (
            "official_nse_historical_corporate_actions_web_api"
        ),
        "source_url": NSE_CORPORATE_ACTIONS_API_URL,
        "reliability": "PrimaryExchange",
        "published_at": None,
        "published_at_source": None,
        "timestamp_verified": False,
        "action_dates": action_dates,
        "retrieved_at": retrieved_at.isoformat(),
        "raw": dict(row),
    }

    if parsed_factor:
        result.update(parsed_factor)

    return result


def _payload_rows(payload) -> List[Dict]:
    if isinstance(payload, list):
        return [
            row
            for row in payload
            if isinstance(row, dict)
        ]

    if isinstance(payload, dict):
        for key in (
            "data",
            "records",
            "rows",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                return [
                    row
                    for row in value
                    if isinstance(row, dict)
                ]

    return []


def _warm_session(
    session,
    timeout_seconds: int,
) -> None:
    response = session.get(
        NSE_WEB_BASE_URL,
        headers=NSE_HEADERS,
        timeout=timeout_seconds,
    )
    response.raise_for_status()


def _fetch_window(
    session,
    *,
    start_date: date,
    end_date: date,
    timeout_seconds: int,
    retrieved_at: datetime,
) -> Tuple[List[Dict], Dict]:
    params = {
        "index": "equities",
        "from_date": _format_query_date(
            start_date
        ),
        "to_date": _format_query_date(
            end_date
        ),
    }

    response = session.get(
        NSE_CORPORATE_ACTIONS_API_URL,
        headers=NSE_HEADERS,
        params=params,
        timeout=timeout_seconds,
    )
    response.raise_for_status()

    payload = response.json()
    rows = _payload_rows(payload)

    normalized = [
        _normalize_action_row(
            row,
            retrieved_at=retrieved_at,
        )
        for row in rows
    ]

    return (
        normalized,
        {
            "from_date": start_date.isoformat(),
            "to_date": end_date.isoformat(),
            "row_count": len(normalized),
            "url": getattr(
                response,
                "url",
                NSE_CORPORATE_ACTIONS_API_URL,
            ),
        },
    )


def _discontinuity_dates(
    corporate_action_guard: Optional[Dict],
) -> List[date]:
    guard = (
        corporate_action_guard
        if isinstance(
            corporate_action_guard,
            dict,
        )
        else {}
    )

    values: List[date] = []

    for event in (
        guard.get(
            "events",
            [],
        )
        or []
    ):
        parsed = _as_ist_datetime(
            event.get("current_ts")
        )

        if parsed is None:
            continue

        values.append(
            parsed.date()
        )

    return sorted(set(values))


def _merge_windows(
    dates: Sequence[date],
    *,
    padding_days: int,
) -> List[Tuple[date, date]]:
    if not dates:
        return []

    windows = sorted(
        (
            value
            - timedelta(days=padding_days),
            value
            + timedelta(days=padding_days),
        )
        for value in dates
    )

    merged: List[Tuple[date, date]] = []

    for start, end in windows:
        if not merged:
            merged.append((start, end))
            continue

        previous_start, previous_end = (
            merged[-1]
        )

        if start <= previous_end + timedelta(days=1):
            merged[-1] = (
                previous_start,
                max(previous_end, end),
            )
        else:
            merged.append((start, end))

    return merged


def _dedupe_actions(
    actions: Iterable[Dict],
) -> List[Dict]:
    seen = set()
    result = []

    for action in actions:
        key = (
            str(
                action.get(
                    "symbol",
                    "",
                )
            ).upper(),
            str(
                action.get(
                    "purpose",
                    "",
                )
            ).strip().lower(),
            action.get("ex_date"),
            action.get("record_date"),
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(action)

    return result


def fetch_historical_actions_for_discontinuities(
    symbol: str,
    corporate_action_guard: Optional[Dict],
    *,
    padding_days: int = NSE_HISTORICAL_CA_PADDING_DAYS,
    timeout_seconds: int = NSE_HISTORICAL_CA_TIMEOUT_SECONDS,
    session_factory: Callable = requests.Session,
    as_of: Optional[datetime] = None,
) -> Dict:
    """
    Query the official NSE corporate-actions web endpoint only when SMC has
    already detected a suspicious price-regime discontinuity.

    Important:
    - This is an adapter to NSE's public corporate-filings web interface.
    - It is not treated as a documented/stable developer API.
    - Any request/parse failure returns an UNAVAILABLE/INCOMPLETE result.
    - Existing SMC history truncation remains untouched.
    - Exact NSE symbol matching is performed locally.
    """
    symbol = str(
        symbol
        or ""
    ).strip().upper()

    if padding_days < 0:
        raise ValueError(
            "padding_days must be >= 0."
        )

    if timeout_seconds <= 0:
        raise ValueError(
            "timeout_seconds must be > 0."
        )

    boundary_dates = _discontinuity_dates(
        corporate_action_guard
    )

    if not boundary_dates:
        return {
            "requested": False,
            "available": None,
            "complete": True,
            "status": "NOT_REQUIRED_NO_DISCONTINUITY",
            "symbol": symbol,
            "source_id": (
                "nse_historical_corporate_actions"
            ),
            "source_url": (
                NSE_CORPORATE_ACTIONS_API_URL
            ),
            "boundary_dates": [],
            "windows": [],
            "actions": [],
            "errors": [],
            "warnings": [],
        }

    windows = _merge_windows(
        boundary_dates,
        padding_days=padding_days,
    )

    now = (
        as_of.astimezone(IST)
        if isinstance(
            as_of,
            datetime,
        )
        and as_of.tzinfo
        else (
            as_of.replace(tzinfo=IST)
            if isinstance(
                as_of,
                datetime,
            )
            else datetime.now(tz=IST)
        )
    )

    errors: List[str] = []
    warnings: List[str] = []
    window_results: List[Dict] = []
    all_actions: List[Dict] = []

    session = session_factory()

    try:
        _warm_session(
            session,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        return {
            "requested": True,
            "available": False,
            "complete": False,
            "status": "UNAVAILABLE",
            "symbol": symbol,
            "source_id": (
                "nse_historical_corporate_actions"
            ),
            "source_url": (
                NSE_CORPORATE_ACTIONS_API_URL
            ),
            "boundary_dates": [
                item.isoformat()
                for item in boundary_dates
            ],
            "windows": [
                {
                    "from_date": start.isoformat(),
                    "to_date": end.isoformat(),
                }
                for start, end in windows
            ],
            "actions": [],
            "errors": [
                "NSE web session warm-up failed: "
                + str(exc)
            ],
            "warnings": [
                "Historical corporate-action confirmation was not available; "
                "the existing SMC safety guard remains authoritative."
            ],
        }

    successful_windows = 0

    for start_date, end_date in windows:
        try:
            actions, metadata = _fetch_window(
                session,
                start_date=start_date,
                end_date=end_date,
                timeout_seconds=timeout_seconds,
                retrieved_at=now,
            )

            successful_windows += 1
            window_results.append(
                {
                    **metadata,
                    "success": True,
                }
            )
            all_actions.extend(actions)

        except Exception as exc:
            errors.append(
                "NSE historical corporate-action request failed for "
                f"{start_date.isoformat()} -> "
                f"{end_date.isoformat()}: {exc}"
            )

            window_results.append(
                {
                    "from_date": start_date.isoformat(),
                    "to_date": end_date.isoformat(),
                    "success": False,
                    "row_count": 0,
                    "error": str(exc),
                }
            )

    filtered = [
        action
        for action in all_actions
        if str(
            action.get(
                "symbol",
                "",
            )
        ).upper()
        == symbol
    ]

    filtered = _dedupe_actions(
        filtered
    )

    complete = (
        successful_windows
        == len(windows)
    )

    available = (
        successful_windows
        > 0
    )

    if not complete:
        warnings.append(
            "One or more historical corporate-action windows failed. "
            "Absence of a match must not be interpreted as proof that no "
            "corporate action occurred."
        )

    return {
        "requested": True,
        "available": available,
        "complete": complete,
        "status": (
            "AVAILABLE_COMPLETE"
            if complete
            else (
                "AVAILABLE_PARTIAL"
                if available
                else "UNAVAILABLE"
            )
        ),
        "symbol": symbol,
        "source_id": (
            "nse_historical_corporate_actions"
        ),
        "source_url": (
            NSE_CORPORATE_ACTIONS_API_URL
        ),
        "boundary_dates": [
            item.isoformat()
            for item in boundary_dates
        ],
        "windows": window_results,
        "actions": filtered,
        "action_count": len(filtered),
        "errors": errors,
        "warnings": warnings,
        "retrieved_at": now.isoformat(),
    }
