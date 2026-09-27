# news/news_engine.py

import feedparser
from datetime import datetime, timedelta

NSE_RSS_URL = "https://www.nseindia.com/rss/corporate-announcements.xml"

def fetch_nse_announcements():
    """
    Parse the NSE corporate announcements RSS feed.
    Returns a list of dicts: symbol, title, link, date.
    """
    feed = feedparser.parse(NSE_RSS_URL)
    announcements = []
    for entry in feed.entries:
        published = entry.get("published", "")
        # Example title: "RELIANCE: Board Meeting Results"
        title = entry.get("title", "")
        link = entry.get("link", "")
        date = entry.get("published", "")
        # Try to extract symbol as first word before colon
        symbol = title.split(":")[0] if ":" in title else ""
        announcements.append({
            "symbol": symbol,
            "title": title,
            "link": link,
            "date": published
        })
    return announcements

def classify_event_text(title: str) -> (str, str):
    """
    Very simple rule-based classification of title.
    Returns (type, sentiment) e.g. ("Results", "Neutral") or ("News", "Positive").
    """
    text = title.lower()
    if any(x in text for x in ["buyback", "bonus", "dividend"]):
        return "Corporate Action", "Positive"
    if any(x in text for x in ["board meeting", "results", "quarterly"]):
        return "Results", "Neutral"
    if any(x in text for x in ["debt", "fall", "restructure", "loss"]):
        return "Fundamental", "Negative"
    return "General", "Neutral"

# Example usage
if __name__ == "__main__":
    anns = fetch_nse_announcements()
    for ann in anns[:3]:
        typ, sent = classify_event_text(ann["title"])
        print(ann["symbol"], "-", ann["title"][:50], "-", sent)
