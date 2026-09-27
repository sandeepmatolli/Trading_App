# news/news_engine.py

from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

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

    Corporate actions such as dividend/bonus are not automatically labelled
    positive because price adjusts and context matters.
    """
    text = str(title or "").strip().lower()

    if any(term in text for term in NEGATIVE_WORDS):
        return "Material Risk", "Negative"

    if any(term in text for term in POSITIVE_WORDS):
        return "Corporate Development", "Positive"

    if any(term in text for term in NEUTRAL_EVENT_WORDS):
        return "Corporate Filing", "Neutral"

    return "General", "Neutral"


def fetch_nse_announcements() -> List[Dict]:
    """
    Optional RSS adapter.

    NSE's corporate-filings webpage is the preferred primary source for the
    application design, but this module does not hard-code an undocumented NSE
    API endpoint. If NEWS_RSS_URLS is configured, those feeds are parsed here.

    With no configured feeds the function safely returns an empty list.
    """
    if not NEWS_RSS_URLS:
        print(
            "News: no RSS feed configured. Continuing with neutral event "
            "context. Configure NEWS_RSS_URLS later or add a dedicated "
            "NSE/BSE filing provider."
        )
        return []

    announcements: List[Dict] = []

    for url in NEWS_RSS_URLS:
        feed = feedparser.parse(url)

        for entry in getattr(feed, "entries", []):
            title = str(entry.get("title", "")).strip()
            link = str(entry.get("link", "")).strip()
            published = str(
                entry.get("published", entry.get("updated", ""))
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

    return announcements


def event_profile_for_symbol(
    symbol: str,
    announcements: Iterable[Dict],
) -> Dict:
    symbol = str(symbol).strip().upper()
    matches = [
        item
        for item in announcements
        if str(item.get("symbol", "")).strip().upper() == symbol
    ]

    if not matches:
        return {
            "sentiment": "Neutral",
            "event_type": "None",
            "title": "",
            "source": "",
            "event_count": 0,
        }

    ranked = []
    priority = {"Negative": 3, "Positive": 2, "Neutral": 1}

    for item in matches:
        event_type, sentiment = classify_event_text(item.get("title", ""))
        ranked.append(
            (
                priority[sentiment],
                {
                    "sentiment": sentiment,
                    "event_type": event_type,
                    "title": item.get("title", ""),
                    "source": item.get("source", ""),
                    "event_count": len(matches),
                },
            )
        )

    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked[0][1]


if __name__ == "__main__":
    items = fetch_nse_announcements()
    print(f"Loaded {len(items)} configured-feed announcements.")
