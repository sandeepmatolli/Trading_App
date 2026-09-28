from __future__ import annotations

import calendar
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple
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


def _entry_datetime(
    entry,
) -> Optional[datetime]:
    for key in (
        "published_parsed",
        "updated_parsed",
        "created_parsed",
    ):
        parsed = entry.get(key)
        if parsed:
            try:
                epoch = calendar.timegm(parsed)
                return datetime.fromtimestamp(
                    epoch,
                    tz=timezone.utc,
                ).astimezone(IST)
            except Exception:
                pass

    for key in (
        "published",
        "updated",
        "created",
    ):
        raw = str(entry.get(key, "") or "").strip()
        if not raw:
            continue

        try:
            parsed = parsedate_to_datetime(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(IST)
        except Exception:
            continue

    return None


def _clean_text(value) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value or "").strip(),
    )


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
    Parse only straightforward bonus / face-value split ratios.

    The returned factor is theoretical. It is not automatically applied to
    Groww history in this phase. It is surfaced so the later corporate-action
    adjustment layer can cross-check the existing discontinuity guard.
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

    Titles are intentionally NOT treated as natural-language investment advice.
    Only high-confidence keyword classes get Positive/Negative sentiment.
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
    title = _clean_text(entry.get("title", ""))
    summary = _clean_text(
        entry.get(
            "summary",
            entry.get("description", ""),
        )
    )
    link = _clean_text(entry.get("link", ""))
    published_at = _entry_datetime(entry)
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
        "source_id": spec["source_id"],
        "source_type": spec["source_type"],
        "source_url": spec["url"],
        "reliability": spec["reliability"],
        "corporate_action": corporate_action,
    }


def _dedupe_items(
    items: List[Dict],
) -> List[Dict]:
    seen = set()
    deduped: List[Dict] = []

    for item in items:
        link = str(item.get("link", "") or "").strip()
        if link:
            key = ("link", link)
        else:
            key = (
                "text",
                item.get("source_type"),
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
                "latest_item_at": None,
                "latest_item_age_days": None,
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
                "latest_item_at": None,
                "latest_item_age_days": None,
                "errors": [f"Feed request failed: {exc}"],
                "warnings": [],
            },
            [],
        )

    raw_entries = list(getattr(feed, "entries", []) or [])

    if getattr(feed, "bozo", False):
        message = str(
            getattr(
                feed,
                "bozo_exception",
                "unknown parse warning",
            )
        )
        if raw_entries:
            warnings.append(
                f"Feed parser warning but entries were recovered: {message}"
            )
        else:
            errors.append(
                f"Feed parse failed: {message}"
            )

    items = [
        _normalize_entry(entry, spec)
        for entry in raw_entries
    ]

    parsed_dates = []
    for item in items:
        raw = item.get("published_at")
        if not raw:
            continue
        try:
            parsed_dates.append(
                datetime.fromisoformat(raw).astimezone(IST)
            )
        except Exception:
            continue

    latest = max(parsed_dates) if parsed_dates else None
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

    if available and latest is None:
        warnings.append(
            "Feed entries were received but no parseable publication "
            "timestamp was found."
        )

    if (
        available
        and age_days is not None
        and age_days > NEWS_SOURCE_MAX_STALE_DAYS
    ):
        warnings.append(
            f"Latest feed item is {age_days:.1f} day(s) old; "
            f"candidate freshness maximum is "
            f"{NEWS_SOURCE_MAX_STALE_DAYS} day(s)."
        )

    if not raw_entries and not errors:
        errors.append("Source returned zero RSS entries.")
        available = False
        source_candidate_eligible = False

    status = {
        **spec,
        "available": available,
        "candidate_eligible": source_candidate_eligible,
        "entry_count": len(items),
        "latest_item_at": (
            latest.isoformat()
            if latest is not None
            else None
        ),
        "latest_item_age_days": age_days,
        "errors": errors,
        "warnings": warnings,
    }

    return status, items


def fetch_nse_announcements(
    as_of: Optional[datetime] = None,
) -> Dict:
    """
    Fetch official NSE corporate-information RSS feeds.

    Naming is retained for backward compatibility with main.py, but this
    function now returns a multi-source event bundle:
      - announcements (required)
      - corporate actions (required)
      - financial results (context)
      - board meetings (context)
      - optional supplemental RSS feeds

    "available" means the official announcements source is reachable.
    "candidate_eligible" is stricter: every required official source must be
    available AND fresh enough.
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
                    f"Required event source unavailable: "
                    f"{status['source_id']}."
                )
            elif not status.get("candidate_eligible"):
                candidate_blockers.append(
                    f"Required event source is not fresh/candidate-grade: "
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
        announcement_status.get("available", False)
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
        normalized_alias = _normalize_for_match(alias)

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
        item.get("symbol_hint", "") or ""
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
        parsed = datetime.fromisoformat(str(raw))
    except Exception:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)

    return parsed.astimezone(IST)


def _within_event_window(
    item: Dict,
    now: datetime,
) -> bool:
    published = _published_datetime_from_item(item)

    if published is None:
        # Keep the item visible, but source freshness has already been assessed
        # independently at the bundle level.
        return True

    age_days = (
        now - published
    ).total_seconds() / 86400.0

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

    published = _published_datetime_from_item(event)
    timestamp = (
        published.timestamp()
        if published is not None
        else 0.0
    )

    return (
        sentiment_score + materiality_score,
        timestamp,
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
        feed_result.get("available", False)
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

    # Preserve order while removing duplicate aliases.
    alias_values = list(
        dict.fromkeys(alias_values)
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
            "matched_events": [],
            "corporate_actions": [],
            "freshest_event_at": None,
            "candidate_blockers": list(
                feed_result.get(
                    "candidate_blockers",
                    [],
                )
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

    for item in feed_result.get("items", []) or []:
        is_match, match_method = _item_matches_symbol(
            item,
            symbol,
            alias_values,
        )

        if not is_match:
            continue

        if not _within_event_window(
            item,
            now,
        ):
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

        matched.append(enriched)

    matched.sort(
        key=_event_rank,
        reverse=True,
    )

    limited = matched[:NEWS_MAX_MATCHED_EVENTS]

    corporate_actions = [
        {
            "title": event.get("title", ""),
            "summary": event.get("summary", ""),
            "published_at": event.get("published_at"),
            "source_id": event.get("source_id"),
            "link": event.get("link", ""),
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

    freshest = max(dated) if dated else None

    common = {
        "available": True,
        "candidate_eligible": source_candidate_eligible,
        "event_count": len(matched),
        "matched_events": limited,
        "corporate_actions": corporate_actions,
        "freshest_event_at": (
            freshest.isoformat()
            if freshest is not None
            else None
        ),
        "candidate_blockers": list(
            feed_result.get(
                "candidate_blockers",
                [],
            )
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
        "title": primary.get("title", ""),
        "source": primary.get("source_id", ""),
        "source_type": primary.get("source_type", ""),
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
            f"latest={status['latest_item_at']}"
        )
