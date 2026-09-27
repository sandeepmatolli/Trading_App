# news/news_engine.py

from __future__ import annotations

from typing import Dict, List, Tuple

import feedparser

from config import NEWS_RSS_URLS


POSITIVE_WORDS = {
    "order win",
    "new order",
    "contract award",
    "buyback",
    "credit rating upgrade",
    "capacity expansion",
}

NEGATIVE_WORDS = {
    "fraud",
    "default",
    "insolvency",
    "penalty",
    "investigation",
    "credit rating downgrade",
    "resignation of auditor",
    "loss",
}

NEUTRAL_EVENT_WORDS = {
    "board meeting",
    "financial results",
    "quarterly results",
    "annual general meeting",
    "record date",
    "dividend",
    "bonus",
}


def classify_event_text(title: str) -> Tuple[str, str]:
    """
    Conservative rule-based classification.
    """
    text = str(title or "").strip().lower()

    if any(term in text for term in NEGATIVE_WORDS):
        return "Material Risk", "Negative"

    if any(term in text for term in POSITIVE_WORDS):
        return "Corporate Development", "Positive"

    if any(term in text for term in NEUTRAL_EVENT_WORDS):
        return "Corporate Filing", "Neutral"

    return "General", "Neutral"


def fetch_nse_announcements() -> Dict:
    """
    Optional feed adapter.

    This function deliberately does not pretend that "no configured feed"
    means "no news". It returns an explicit availability flag.
    """
    if not NEWS_RSS_URLS:
        print(
            "News: no source configured. Event evidence will be marked "
            "UNAVAILABLE, not Neutral."
        )
        return {
            "available": False,
            "items": [],
            "errors": ["No NEWS_RSS_URLS configured."],
        }

    announcements: List[Dict] = []
    errors: List[str] = []
    successful_sources = 0

    for url in NEWS_RSS_URLS:
        feed = feedparser.parse(url)

        if getattr(feed, "bozo", False):
            errors.append(
                f"Feed parse problem for {url}: "
                f"{getattr(feed, 'bozo_exception', 'unknown error')}"
            )
            continue

        successful_sources += 1

        for entry in getattr(feed, "entries", []):
            title = str(entry.get("title", "")).strip()
            link = str(entry.get("link", "")).strip()
            published = str(
                entry.get(
                    "published",
                    entry.get("updated", ""),
                )
            ).strip()

            symbol = ""
            if ":" in title:
                candidate = title.split(":", 1)[0].strip().upper()
                if candidate and " " not in candidate:
                    symbol = candidate

            announcements.append(
                {
                    "symbol": symbol,
                    "title": title,
                    "link": link,
                    "date": published,
                    "source": url,
                }
            )

    return {
        "available": successful_sources > 0,
        "items": announcements,
        "errors": errors,
    }


def event_profile_for_symbol(
    symbol: str,
    feed_result: Dict,
) -> Dict:
    symbol = str(symbol).strip().upper()

    available = bool(feed_result.get("available", False))
    items = list(feed_result.get("items", []))

    if not available:
        return {
            "available": False,
            "sentiment": "Unknown",
            "event_type": "Unavailable",
            "title": "",
            "source": "",
            "event_count": 0,
            "errors": list(feed_result.get("errors", [])),
        }

    matches = [
        item
        for item in items
        if str(item.get("symbol", "")).strip().upper() == symbol
    ]

    if not matches:
        return {
            "available": True,
            "sentiment": "Neutral",
            "event_type": "NoneFound",
            "title": "",
            "source": "",
            "event_count": 0,
            "errors": list(feed_result.get("errors", [])),
        }

    priority = {
        "Negative": 3,
        "Positive": 2,
        "Neutral": 1,
    }

    ranked = []

    for item in matches:
        event_type, sentiment = classify_event_text(
            item.get("title", "")
        )

        ranked.append(
            (
                priority[sentiment],
                {
                    "available": True,
                    "sentiment": sentiment,
                    "event_type": event_type,
                    "title": item.get("title", ""),
                    "source": item.get("source", ""),
                    "event_count": len(matches),
                    "errors": list(feed_result.get("errors", [])),
                },
            )
        )

    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked[0][1]


if __name__ == "__main__":
    result = fetch_nse_announcements()
    print(
        f"News source available={result['available']} "
        f"items={len(result['items'])}"
    )
