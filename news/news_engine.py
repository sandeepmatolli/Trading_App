from __future__ import annotations

import calendar
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import feedparser

from config import (
    NEWS_EVENT_LOOKBACK_DAYS,
    NEWS_MAX_MATCHED_EVENTS,
    NEWS_RSS_URLS,
    NEWS_SOURCE_MAX_STALE_DAYS,
    NSE_ANNOUNCEMENTS_RSS_URL,
    NSE_BOARD_MEETINGS_RSS_URL,
    NSE_CORPORATE_ACTIONS_RSS_URL,
    NSE_FINANCIAL_RESULTS_RSS_URL,
)


IST = ZoneInfo("Asia/Kolkata")

NEGATIVE_HIGH_TERMS = (
    "fraud/default/arrest",
    "fraud",
    "default",
    "insolvency",
    "corporate insolvency resolution process",
    "cirp",
    "liquidation",
    "winding up",
    "credit rating downgrade",
    "rating downgrade",
    "resignation of auditor",
    "auditor resignation",
    "qualified opinion",
    "adverse opinion",
    "penalty",
    "investigation",
)

POSITIVE_HIGH_TERMS = (
    "bagging/receiving of orders/contracts",
    "bagging of orders",
    "receiving of orders",
    "order win",
    "new order",
    "contract award",
    "credit rating upgrade",
    "rating upgrade",
    "capacity expansion",
    "commissioning",
    "buyback",
)

CORPORATE_ACTION_TERMS = (
    "bonus",
    "stock split",
    "face value split",
    "sub-division",
    "subdivision",
    "rights issue",
    "rights",
    "dividend",
    "record date",
    "book closure",
)

FINANCIAL_RESULT_TERMS = (
    "financial results",
    "quarterly results",
    "annual results",
    "integrated filing financial",
)

BOARD_MEETING_TERMS = (
    "board meeting",
    "meeting of board",
)

GOVERNANCE_TERMS = (
    "change in directors",
    "change in director",
    "change in kmp",
    "appointment",
    "resignation",
    "auditor",
)

ANALYST_TERMS = (
    "analyst",
    "institutional investor",
    "conference call",
    "con. call",
    "earnings call",
)

# feedparser reliably normalizes standard RSS/Atom dates, but the live NSE
# feeds can expose non-standard/custom entry fields. These are explicit,
# publication/broadcast-oriented fallbacks only. Event dates such as record
# date, ex-date, book-closure date, meeting date, etc. are intentionally NOT
# used as publication timestamps.
ENTRY_PARSED_DATE_KEYS = (
    "published_parsed",
    "updated_parsed",
    "created_parsed",
)

ENTRY_RAW_DATE_KEYS = (
    "published",
    "updated",
    "created",
    "pubdate",
    "pub_date",
    "publication_date",
    "publicationdate",
    "publication_datetime",
    "publicationdatetime",
    "announcement_date",
    "announcementdate",
    "announcement_datetime",
    "announcementdatetime",
    "broadcast_date",
    "broadcastdate",
    "broadcast_datetime",
    "broadcastdatetime",
    "sort_date",
    "sortdate",
    "an_dt",
    "date",
    "datetime",
    "timestamp",
)

FEED_PARSED_DATE_KEYS = (
    "updated_parsed",
    "published_parsed",
    "created_parsed",
    "modified_parsed",
)

FEED_RAW_DATE_KEYS = (
    "updated",
    "published",
    "created",
    "modified",
    "pubdate",
    "pub_date",
    "lastbuilddate",
    "last_build_date",
)

CUSTOM_DATETIME_FORMATS = (
    "%d-%b-%Y %H:%M:%S",
    "%d-%b-%Y %H:%M",
    "%d-%b-%Y",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
)


def _as_of_ist(
    as_of: Optional[datetime] = None,
) -> datetime:
    if as_of is None:
        return datetime.now(tz=IST)

    if isinstance(as_of, datetime):
        value = as_of
    else:
        value = datetime.fromisoformat(str(as_of))

    if value.tzinfo is None:
        return value.replace(tzinfo=IST)

    return value.astimezone(IST)


def _source_specs() -> List[Dict]:
    specs = [
        {
            "source_id": "nse_announcements",
            "source_type": "official_nse_announcements",
            "url": NSE_ANNOUNCEMENTS_RSS_URL,
            "required_for_candidate": True,
            "reliability": "PrimaryExchange",
        },
        {
            "source_id": "nse_corporate_actions",
            "source_type": "official_nse_corporate_actions",
            "url": NSE_CORPORATE_ACTIONS_RSS_URL,
            "required_for_candidate": True,
            "reliability": "PrimaryExchange",
        },
        {
            "source_id": "nse_financial_results",
            "source_type": "official_nse_financial_results",
            "url": NSE_FINANCIAL_RESULTS_RSS_URL,
            "required_for_candidate": False,
            "reliability": "PrimaryExchange",
        },
        {
            "source_id": "nse_board_meetings",
            "source_type": "official_nse_board_meetings",
            "url": NSE_BOARD_MEETINGS_RSS_URL,
            "required_for_candidate": False,
            "reliability": "PrimaryExchange",
        },
    ]

    for index, url in enumerate(NEWS_RSS_URLS, start=1):
        specs.append(
            {
                "source_id": f"supplemental_rss_{index}",
                "source_type": "supplemental_rss",
                "url": url,
                "required_for_candidate": False,
                "reliability": "Supplemental",
            }
        )

    return specs


def _mapping_get(mapping, key: str):
    if mapping is None:
        return None

    try:
        if hasattr(mapping, "get"):
            return mapping.get(key)
    except Exception:
        pass

    try:
        return getattr(mapping, key)
    except Exception:
        return None


def _clean_text(value) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value or "").strip(),
    )


def _parse_datetime_value(
    value,
    *,
    parsed_tuple_is_utc: bool = False,
) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=IST)
        return value.astimezone(IST)

    # feedparser *_parsed fields are UTC time tuples / struct_time.
    if isinstance(value, (tuple, list)) and len(value) >= 6:
        try:
            if parsed_tuple_is_utc:
                epoch = calendar.timegm(value)
                return datetime.fromtimestamp(
                    epoch,
                    tz=timezone.utc,
                ).astimezone(IST)

            parsed = datetime(
                int(value[0]),
                int(value[1]),
                int(value[2]),
                int(value[3]),
                int(value[4]),
                int(value[5]),
                tzinfo=IST,
            )
            return parsed
        except Exception:
            return None

    if isinstance(value, (int, float)):
        try:
            numeric = float(value)

            # Tolerate epoch milliseconds.
            if numeric > 10_000_000_000:
                numeric /= 1000.0

            return datetime.fromtimestamp(
                numeric,
                tz=timezone.utc,
            ).astimezone(IST)
        except Exception:
            return None

    raw = _clean_text(value)
    if not raw:
        return None

    # Common timezone abbreviation cleanup. A naked IST string is ambiguous to
    # many stdlib parsers, but NSE context is explicitly India Standard Time.
    raw_ist = re.sub(
        r"\bIST\b",
        "+0530",
        raw,
        flags=re.IGNORECASE,
    )

    try:
        parsed = parsedate_to_datetime(raw_ist)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        return parsed.astimezone(IST)
    except Exception:
        pass

    iso_candidate = raw_ist.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(iso_candidate)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        return parsed.astimezone(IST)
    except Exception:
        pass

    for fmt in CUSTOM_DATETIME_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.replace(tzinfo=IST)
        except Exception:
            continue

    return None


def _entry_datetime_with_source(
    entry,
) -> Tuple[Optional[datetime], Optional[str]]:
    for key in ENTRY_PARSED_DATE_KEYS:
        parsed = _mapping_get(entry, key)
        if parsed:
            value = _parse_datetime_value(
                parsed,
                parsed_tuple_is_utc=True,
            )
            if value is not None:
                return value, key

    for key in ENTRY_RAW_DATE_KEYS:
        raw = _mapping_get(entry, key)
        if raw in (None, ""):
            continue

        value = _parse_datetime_value(raw)
        if value is not None:
            return value, key

    return None, None


def _entry_datetime(
    entry,
) -> Optional[datetime]:
    value, _ = _entry_datetime_with_source(entry)
    return value


def _feed_datetime_with_source(
    parsed_feed,
) -> Tuple[Optional[datetime], Optional[str]]:
    feed_meta = _mapping_get(parsed_feed, "feed")

    for key in FEED_PARSED_DATE_KEYS:
        for container_name, container in (
            ("feed", feed_meta),
            ("response", parsed_feed),
        ):
            raw = _mapping_get(container, key)
            if not raw:
                continue

            value = _parse_datetime_value(
                raw,
                parsed_tuple_is_utc=True,
            )
            if value is not None:
                return value, f"{container_name}.{key}"

    for key in FEED_RAW_DATE_KEYS:
        for container_name, container in (
            ("feed", feed_meta),
            ("response", parsed_feed),
        ):
            raw = _mapping_get(container, key)
            if raw in (None, ""):
                continue

            value = _parse_datetime_value(raw)
            if value is not None:
                return value, f"{container_name}.{key}"

    return None, None


def _normalize_for_match(value: str) -> str:
    text = _clean_text(value).upper()
    text = re.sub(r"[^A-Z0-9&]+", " ", text)
    text = re.sub(
        r"\b(LIMITED|LTD|PLC)\b",
        " ",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()


def _extract_symbol_hint(
    title: str,
    summary: str,
) -> str:
    combined = f"{title} {summary}".strip()

    patterns = (
        r"^\s*([A-Z0-9&.-]{1,24})\s*:\s*",
        r"\bSYMBOL\s*[:=-]\s*([A-Z0-9&.-]{1,24})\b",
        r"\bNSE\s*SYMBOL\s*[:=-]\s*([A-Z0-9&.-]{1,24})\b",
    )

    for pattern in patterns:
        match = re.search(
            pattern,
            combined,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1).strip().upper()

    return ""


def _parse_bonus_factor(
    text: str,
) -> Optional[Dict]:
    match = re.search(
        r"\bbonus\b[^0-9]{0,20}"
        r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    bonus = float(match.group(1))
    existing = float(match.group(2))

    if bonus <= 0 or existing <= 0:
        return None

    factor = existing / (existing + bonus)

    return {
        "action_type": "bonus",
        "ratio": f"{match.group(1)}:{match.group(2)}",
        "theoretical_price_factor": round(factor, 8),
    }


def _parse_split_factor(
    text: str,
) -> Optional[Dict]:
    match = re.search(
        r"(?:split|sub-division|subdivision)"
        r".{0,80}?\bfrom\b\s*(?:rs\.?|re\.?)?\s*"
        r"(\d+(?:\.\d+)?)"
        r".{0,60}?\bto\b\s*(?:rs\.?|re\.?)?\s*"
        r"(\d+(?:\.\d+)?)",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    old_face = float(match.group(1))
    new_face = float(match.group(2))

    if old_face <= 0 or new_face <= 0:
        return None

    return {
        "action_type": "split_or_consolidation",
        "old_face_value": old_face,
        "new_face_value": new_face,
        "theoretical_price_factor": round(
            new_face / old_face,
            8,
        ),
    }


def parse_corporate_action_factor(
    text: str,
) -> Optional[Dict]:
    """
    Parse straightforward bonus / face-value split ratios only.

    The returned factor is theoretical evidence. It is NOT automatically
    applied to historical Groww prices in this phase.
    """
    cleaned = _clean_text(text)

    bonus = _parse_bonus_factor(cleaned)
    if bonus is not None:
        return bonus

    return _parse_split_factor(cleaned)


def classify_event_text(
    title: str,
    summary: str = "",
    source_type: str = "",
) -> Tuple[str, str, str]:
    """
    Conservative deterministic event classification.

    Returns:
      event_type, sentiment, materiality
    """
    text = f"{title} {summary}".strip().lower()

    if any(term in text for term in NEGATIVE_HIGH_TERMS):
        return "Material Risk", "Negative", "High"

    if any(term in text for term in POSITIVE_HIGH_TERMS):
        return "Corporate Development", "Positive", "High"

    if (
        source_type == "official_nse_corporate_actions"
        or any(term in text for term in CORPORATE_ACTION_TERMS)
    ):
        return "Corporate Action", "Neutral", "Medium"

    if (
        source_type == "official_nse_financial_results"
        or any(term in text for term in FINANCIAL_RESULT_TERMS)
    ):
        return "Financial Results", "Neutral", "Medium"

    if (
        source_type == "official_nse_board_meetings"
        or any(term in text for term in BOARD_MEETING_TERMS)
    ):
        return "Board Meeting", "Neutral", "Medium"

    if any(term in text for term in GOVERNANCE_TERMS):
        return "Governance", "Neutral", "Medium"

    if any(term in text for term in ANALYST_TERMS):
        return "Investor Communication", "Neutral", "Low"

    return "General Filing", "Neutral", "Low"


def _normalize_entry(
    entry,
    spec: Dict,
) -> Dict:
    title = _clean_text(_mapping_get(entry, "title"))
    summary = _clean_text(
        _mapping_get(entry, "summary")
        or _mapping_get(entry, "description")
    )
    link = _clean_text(_mapping_get(entry, "link"))

    published_at, published_at_source = _entry_datetime_with_source(
        entry
    )

    symbol_hint = _extract_symbol_hint(
        title,
        summary,
    )

    combined_action_text = f"{title} {summary}"
    corporate_action = parse_corporate_action_factor(
        combined_action_text
    )

    return {
        "symbol_hint": symbol_hint,
        "title": title,
        "summary": summary,
        "link": link,
        "published_at": (
            published_at.isoformat()
            if published_at is not None
            else None
        ),
        "published_at_source": published_at_source,
        "timestamp_verified": published_at is not None,
        "source_id": spec["source_id"],
        "source_type": spec["source_type"],
        "source_url": spec["url"],
        "reliability": spec["reliability"],
        "corporate_action": corporate_action,
    }


def _dedupe_items(
    items: List[Dict],
) -> List[Dict]:
    """
    Deduplicate inside a source, not across official source families.

    The same filing can legitimately appear in announcements and a dedicated
    results/actions feed. Preserving the source_id in the key prevents source
    provenance from disappearing.
    """
    seen = set()
    deduped: List[Dict] = []

    for item in items:
        source_id = str(item.get("source_id", "") or "")
        link = str(item.get("link", "") or "").strip()

        if link:
            key = (
                source_id,
                "link",
                link,
            )
        else:
            key = (
                source_id,
                "text",
                _normalize_for_match(item.get("title", "")),
                item.get("published_at"),
            )

        if key in seen:
            continue

        seen.add(key)
        deduped.append(item)

    return deduped


def _source_age_days(
    latest: Optional[datetime],
    now: datetime,
) -> Optional[float]:
    if latest is None:
        return None

    seconds = (now - latest).total_seconds()

    if seconds < 0:
        # Small clock skew / future server timestamp should not become a
        # negative age. Keep it visible through latest_item_at.
        return 0.0

    return round(seconds / 86400.0, 3)


def _fetch_source(
    spec: Dict,
    now: datetime,
) -> Tuple[Dict, List[Dict]]:
    errors: List[str] = []
    warnings: List[str] = []

    url = str(spec.get("url", "") or "").strip()

    if not url:
        return (
            {
                **spec,
                "available": False,
                "candidate_eligible": False,
                "entry_count": 0,
                "timestamped_entry_count": 0,
                "undated_entry_count": 0,
                "latest_item_at": None,
                "latest_item_age_days": None,
                "source_time_basis": "unverified",
                "source_time_field": None,
                "errors": ["Source URL is empty."],
                "warnings": [],
            },
            [],
        )

    try:
        feed = feedparser.parse(
            url,
            request_headers={
                "User-Agent": (
                    "TradingAppResearch/1.0 "
                    "(NSE swing-research; RSS reader)"
                )
            },
        )
    except Exception as exc:
        return (
            {
                **spec,
                "available": False,
                "candidate_eligible": False,
                "entry_count": 0,
                "timestamped_entry_count": 0,
                "undated_entry_count": 0,
                "latest_item_at": None,
                "latest_item_age_days": None,
                "source_time_basis": "unverified",
                "source_time_field": None,
                "errors": [f"Feed request failed: {exc}"],
                "warnings": [],
            },
            [],
        )

    raw_entries = list(
        _mapping_get(feed, "entries")
        or []
    )

    if bool(_mapping_get(feed, "bozo")):
        message = str(
            _mapping_get(feed, "bozo_exception")
            or "unknown parse warning"
        )

        if raw_entries:
            warnings.append(
                "Feed parser warning but entries were recovered: "
                + message
            )
        else:
            errors.append(
                "Feed parse failed: " + message
            )

    items = [
        _normalize_entry(entry, spec)
        for entry in raw_entries
    ]

    parsed_dates: List[datetime] = []

    for item in items:
        raw = item.get("published_at")
        if not raw:
            continue

        try:
            parsed_dates.append(
                datetime.fromisoformat(
                    str(raw)
                ).astimezone(IST)
            )
        except Exception:
            continue

    latest_entry = (
        max(parsed_dates)
        if parsed_dates
        else None
    )

    feed_datetime, feed_datetime_source = (
        _feed_datetime_with_source(feed)
    )

    if latest_entry is not None:
        latest = latest_entry
        source_time_basis = "entry_timestamp"
        source_time_field = "latest_entry"
    elif feed_datetime is not None:
        latest = feed_datetime
        source_time_basis = "feed_metadata"
        source_time_field = feed_datetime_source
        warnings.append(
            "No entry publication timestamps were parseable; "
            "source freshness is based on feed-level metadata."
        )
    else:
        latest = None
        source_time_basis = "unverified"
        source_time_field = None

    age_days = _source_age_days(
        latest,
        now,
    )

    available = bool(items) and not (
        errors and not raw_entries
    )

    source_candidate_eligible = bool(
        available
        and latest is not None
        and age_days is not None
        and age_days <= NEWS_SOURCE_MAX_STALE_DAYS
    )

    timestamped_entry_count = sum(
        bool(item.get("timestamp_verified"))
        for item in items
    )
    undated_entry_count = (
        len(items) - timestamped_entry_count
    )

    if available and latest is None:
        warnings.append(
            "Feed entries were received, but neither entry-level nor "
            "feed-level publication/update timestamps were parseable."
        )

    if (
        available
        and age_days is not None
        and age_days > NEWS_SOURCE_MAX_STALE_DAYS
    ):
        warnings.append(
            f"Latest source timestamp is {age_days:.1f} day(s) old; "
            f"candidate freshness maximum is "
            f"{NEWS_SOURCE_MAX_STALE_DAYS} day(s)."
        )

    if not raw_entries and not errors:
        errors.append(
            "Source returned zero RSS entries."
        )
        available = False
        source_candidate_eligible = False

    status = {
        **spec,
        "available": available,
        "candidate_eligible": source_candidate_eligible,
        "entry_count": len(items),
        "timestamped_entry_count": timestamped_entry_count,
        "undated_entry_count": undated_entry_count,
        "latest_item_at": (
            latest.isoformat()
            if latest is not None
            else None
        ),
        "latest_item_age_days": age_days,
        "source_time_basis": source_time_basis,
        "source_time_field": source_time_field,
        "feed_timestamp_at": (
            feed_datetime.isoformat()
            if feed_datetime is not None
            else None
        ),
        "errors": errors,
        "warnings": warnings,
    }

    return status, items


def fetch_nse_announcements(
    as_of: Optional[datetime] = None,
) -> Dict:
    """
    Fetch official NSE corporate-information RSS feeds.

    `available` means the primary announcements feed returned usable entries.

    `candidate_eligible` is stricter. Every required official source must be
    available and have verifiable recent source freshness.

    Missing timestamps are never silently converted into "fresh".
    """
    now = _as_of_ist(as_of)

    statuses: List[Dict] = []
    items: List[Dict] = []
    errors: List[str] = []
    warnings: List[str] = []
    candidate_blockers: List[str] = []

    for spec in _source_specs():
        status, source_items = _fetch_source(
            spec,
            now,
        )

        statuses.append(status)
        items.extend(source_items)

        for error in status.get("errors", []):
            errors.append(
                f"{status['source_id']}: {error}"
            )

        for warning in status.get("warnings", []):
            warnings.append(
                f"{status['source_id']}: {warning}"
            )

        if spec.get("required_for_candidate"):
            if not status.get("available"):
                candidate_blockers.append(
                    "Required event source unavailable: "
                    f"{status['source_id']}."
                )
            elif not status.get("candidate_eligible"):
                candidate_blockers.append(
                    "Required event source is not fresh/candidate-grade: "
                    f"{status['source_id']}."
                )

    items = _dedupe_items(items)

    status_by_id = {
        item["source_id"]: item
        for item in statuses
    }

    announcement_status = status_by_id.get(
        "nse_announcements",
        {},
    )

    available = bool(
        announcement_status.get(
            "available",
            False,
        )
    )

    candidate_eligible = bool(
        available
        and not candidate_blockers
    )

    return {
        "available": available,
        "candidate_eligible": candidate_eligible,
        "degraded": bool(
            candidate_blockers
            or errors
            or warnings
        ),
        "items": items,
        "sources": statuses,
        "errors": errors,
        "warnings": warnings,
        "candidate_blockers": candidate_blockers,
        "as_of": now.isoformat(),
        "event_lookback_days": NEWS_EVENT_LOOKBACK_DAYS,
        "source_max_stale_days": NEWS_SOURCE_MAX_STALE_DAYS,
    }


def _symbol_in_text(
    symbol: str,
    text: str,
) -> bool:
    if not symbol or not text:
        return False

    pattern = (
        r"(?<![A-Z0-9])"
        + re.escape(symbol.upper())
        + r"(?![A-Z0-9])"
    )

    return bool(
        re.search(
            pattern,
            text.upper(),
        )
    )


def _company_name_matches(
    aliases: List[str],
    text: str,
) -> bool:
    normalized_text = _normalize_for_match(text)

    if not normalized_text:
        return False

    for alias in aliases:
        normalized_alias = _normalize_for_match(
            alias
        )

        # Avoid unsafe fuzzy matching and very short aliases such as "TCS"
        # through company-name substring logic. Short exchange symbols are
        # handled separately by exact token matching.
        if len(normalized_alias) < 6:
            continue

        if normalized_alias in normalized_text:
            return True

    return False


def _item_matches_symbol(
    item: Dict,
    symbol: str,
    aliases: List[str],
) -> Tuple[bool, str]:
    symbol = symbol.strip().upper()

    hint = str(
        item.get("symbol_hint", "")
        or ""
    ).strip().upper()

    if hint and hint == symbol:
        return True, "explicit_symbol"

    combined = " ".join(
        [
            str(item.get("title", "") or ""),
            str(item.get("summary", "") or ""),
            str(item.get("link", "") or ""),
        ]
    )

    if _symbol_in_text(
        symbol,
        combined,
    ):
        return True, "symbol_token"

    if _company_name_matches(
        aliases,
        combined,
    ):
        return True, "company_name"

    return False, ""


def _published_datetime_from_item(
    item: Dict,
) -> Optional[datetime]:
    raw = item.get("published_at")

    if not raw:
        return None

    try:
        parsed = datetime.fromisoformat(
            str(raw)
        )
    except Exception:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=IST
        )

    return parsed.astimezone(IST)


def _within_event_window(
    item: Dict,
    now: datetime,
) -> bool:
    published = _published_datetime_from_item(
        item
    )

    if published is None:
        return False

    age_days = (
        now - published
    ).total_seconds() / 86400.0

    if age_days < 0:
        # A small future clock skew can still represent a current item.
        return True

    return age_days <= NEWS_EVENT_LOOKBACK_DAYS


def _event_rank(
    event: Dict,
) -> Tuple[int, float]:
    sentiment_score = {
        "Negative": 400,
        "Positive": 300,
        "Neutral": 100,
    }.get(
        event.get("sentiment"),
        0,
    )

    materiality_score = {
        "High": 30,
        "Medium": 20,
        "Low": 10,
    }.get(
        event.get("materiality"),
        0,
    )

    published = _published_datetime_from_item(
        event
    )

    timestamp = (
        published.timestamp()
        if published is not None
        else 0.0
    )

    return (
        sentiment_score + materiality_score,
        timestamp,
    )


def _dedupe_blockers(
    values: Iterable[str],
) -> List[str]:
    return list(
        dict.fromkeys(
            str(value)
            for value in values
            if str(value).strip()
        )
    )


def event_profile_for_symbol(
    symbol: str,
    feed_result: Dict,
    company_name: str = "",
    aliases: Optional[List[str]] = None,
    as_of: Optional[datetime] = None,
) -> Dict:
    symbol = str(symbol).strip().upper()

    now = _as_of_ist(
        as_of
        or feed_result.get("as_of")
    )

    source_available = bool(
        feed_result.get(
            "available",
            False,
        )
    )

    source_candidate_eligible = bool(
        feed_result.get(
            "candidate_eligible",
            source_available,
        )
    )

    alias_values: List[str] = []

    if company_name:
        alias_values.append(
            str(company_name)
        )

    for alias in aliases or []:
        text = str(alias or "").strip()
        if text:
            alias_values.append(text)

    alias_values = list(
        dict.fromkeys(alias_values)
    )

    base_blockers = list(
        feed_result.get(
            "candidate_blockers",
            [],
        )
    )

    if not source_available:
        return {
            "available": False,
            "candidate_eligible": False,
            "sentiment": "Unknown",
            "event_type": "Unavailable",
            "materiality": "Unknown",
            "title": "",
            "source": "",
            "source_type": "",
            "event_count": 0,
            "undated_event_count": 0,
            "matched_events": [],
            "undated_matches": [],
            "corporate_actions": [],
            "freshest_event_at": None,
            "candidate_blockers": _dedupe_blockers(
                base_blockers
            ),
            "source_coverage": list(
                feed_result.get(
                    "sources",
                    [],
                )
            ),
            "errors": list(
                feed_result.get(
                    "errors",
                    [],
                )
            ),
            "warnings": list(
                feed_result.get(
                    "warnings",
                    [],
                )
            ),
        }

    matched: List[Dict] = []
    undated_matches: List[Dict] = []

    for item in feed_result.get("items", []) or []:
        is_match, match_method = _item_matches_symbol(
            item,
            symbol,
            alias_values,
        )

        if not is_match:
            continue

        event_type, sentiment, materiality = classify_event_text(
            item.get("title", ""),
            item.get("summary", ""),
            item.get("source_type", ""),
        )

        enriched = dict(item)
        enriched.update(
            {
                "match_method": match_method,
                "event_type": event_type,
                "sentiment": sentiment,
                "materiality": materiality,
            }
        )

        if not item.get("timestamp_verified"):
            undated_matches.append(
                enriched
            )
            continue

        if not _within_event_window(
            item,
            now,
        ):
            continue

        matched.append(enriched)

    matched.sort(
        key=_event_rank,
        reverse=True,
    )

    limited = matched[
        :NEWS_MAX_MATCHED_EVENTS
    ]

    limited_undated = undated_matches[
        :NEWS_MAX_MATCHED_EVENTS
    ]

    corporate_actions = [
        {
            "title": event.get(
                "title",
                "",
            ),
            "summary": event.get(
                "summary",
                "",
            ),
            "published_at": event.get(
                "published_at"
            ),
            "published_at_source": event.get(
                "published_at_source"
            ),
            "source_id": event.get(
                "source_id"
            ),
            "link": event.get(
                "link",
                "",
            ),
            **event["corporate_action"],
        }
        for event in limited
        if event.get("corporate_action")
    ]

    dated = [
        _published_datetime_from_item(item)
        for item in matched
    ]

    dated = [
        item
        for item in dated
        if item is not None
    ]

    freshest = (
        max(dated)
        if dated
        else None
    )

    blockers = list(
        base_blockers
    )

    if undated_matches:
        blockers.append(
            "Matched event(s) exist but their publication timestamp "
            "could not be verified."
        )

    per_symbol_candidate_eligible = bool(
        source_candidate_eligible
        and not undated_matches
    )

    common = {
        "available": True,
        "candidate_eligible": per_symbol_candidate_eligible,
        "event_count": len(matched),
        "undated_event_count": len(
            undated_matches
        ),
        "matched_events": limited,
        "undated_matches": limited_undated,
        "corporate_actions": corporate_actions,
        "freshest_event_at": (
            freshest.isoformat()
            if freshest is not None
            else None
        ),
        "candidate_blockers": _dedupe_blockers(
            blockers
        ),
        "source_coverage": list(
            feed_result.get(
                "sources",
                [],
            )
        ),
        "errors": list(
            feed_result.get(
                "errors",
                [],
            )
        ),
        "warnings": list(
            feed_result.get(
                "warnings",
                [],
            )
        ),
    }

    if not matched:
        if undated_matches:
            primary = undated_matches[0]

            return {
                **common,
                "sentiment": "Unknown",
                "event_type": "TimestampUnverified",
                "materiality": primary.get(
                    "materiality",
                    "Unknown",
                ),
                "title": primary.get(
                    "title",
                    "",
                ),
                "source": primary.get(
                    "source_id",
                    "",
                ),
                "source_type": primary.get(
                    "source_type",
                    "",
                ),
            }

        return {
            **common,
            "sentiment": "Neutral",
            "event_type": "NoneFound",
            "materiality": "None",
            "title": "",
            "source": "",
            "source_type": "",
        }

    primary = matched[0]

    return {
        **common,
        "sentiment": primary["sentiment"],
        "event_type": primary["event_type"],
        "materiality": primary["materiality"],
        "title": primary.get(
            "title",
            "",
        ),
        "source": primary.get(
            "source_id",
            "",
        ),
        "source_type": primary.get(
            "source_type",
            "",
        ),
    }


if __name__ == "__main__":
    bundle = fetch_nse_announcements()

    print(
        "Event source: "
        f"available={bundle['available']} "
        f"candidate_eligible={bundle['candidate_eligible']} "
        f"items={len(bundle['items'])} "
        f"blockers={len(bundle['candidate_blockers'])}"
    )

    for status in bundle["sources"]:
        print(
            f"- {status['source_id']}: "
            f"available={status['available']} "
            f"candidate_eligible={status['candidate_eligible']} "
            f"entries={status['entry_count']} "
            f"timestamped={status.get('timestamped_entry_count')} "
            f"undated={status.get('undated_entry_count')} "
            f"latest={status['latest_item_at']} "
            f"basis={status.get('source_time_basis')} "
            f"field={status.get('source_time_field')}"
        )
