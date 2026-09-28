from datetime import datetime
from types import SimpleNamespace

import time

from news import news_engine


def _parsed_tuple(value: str):
    dt = datetime.fromisoformat(value)
    return time.gmtime(dt.timestamp())


def _feed(
    entries,
    *,
    feed_meta=None,
    modified_parsed=None,
):
    return SimpleNamespace(
        bozo=False,
        entries=entries,
        feed=feed_meta or {},
        modified_parsed=modified_parsed,
    )


def test_classification_is_conservative():
    assert news_engine.classify_event_text(
        "Bagging/Receiving of orders/contracts",
    ) == (
        "Corporate Development",
        "Positive",
        "High",
    )

    assert news_engine.classify_event_text(
        "Fraud/Default/Arrest",
    ) == (
        "Material Risk",
        "Negative",
        "High",
    )

    assert news_engine.classify_event_text(
        "Record Date for Dividend",
    ) == (
        "Corporate Action",
        "Neutral",
        "Medium",
    )


def test_bonus_factor_parser():
    result = news_engine.parse_corporate_action_factor(
        "Bonus 1:1"
    )

    assert result is not None
    assert result["action_type"] == "bonus"
    assert result["theoretical_price_factor"] == 0.5


def test_split_factor_parser():
    result = news_engine.parse_corporate_action_factor(
        "Face Value Split (Sub-Division) - From Rs 10/- "
        "Per Share To Rs 2/- Per Share"
    )

    assert result is not None
    assert result["action_type"] == "split_or_consolidation"
    assert result["theoretical_price_factor"] == 0.2


def test_custom_nse_style_date_field_is_parsed():
    value, source = news_engine._entry_datetime_with_source(
        {
            "title": "Example",
            "date": "28-Sep-2026 12:05:30",
        }
    )

    assert value is not None
    assert value.isoformat().startswith(
        "2026-09-28T12:05:30"
    )
    assert source == "date"


def test_ist_abbreviation_is_parsed():
    value, source = news_engine._entry_datetime_with_source(
        {
            "published": (
                "Mon, 28 Sep 2026 12:05:30 IST"
            ),
        }
    )

    assert value is not None
    assert value.utcoffset().total_seconds() == 19800
    assert source == "published"


def test_official_sources_can_be_candidate_grade(monkeypatch):
    now = datetime.fromisoformat(
        "2026-09-28T12:00:00+05:30"
    )

    recent = _parsed_tuple(
        "2026-09-28T06:00:00+00:00"
    )

    def fake_parse(url, request_headers=None):
        if "Online_announcements" in url:
            entries = [
                {
                    "title": "Reliance Industries Limited",
                    "summary": (
                        "Reliance Industries Limited has informed the "
                        "Exchange about Bagging/Receiving of orders/contracts"
                    ),
                    "link": "https://example.test/reliance-order",
                    "published_parsed": recent,
                }
            ]
        elif "Corporate_action" in url:
            entries = [
                {
                    "title": "Example Industries Limited",
                    "summary": "Bonus 1:1",
                    "link": "https://example.test/example-bonus",
                    "published_parsed": recent,
                }
            ]
        elif "Financial_Results" in url:
            entries = [
                {
                    "title": "Example Industries Limited",
                    "summary": "Financial Results",
                    "link": "https://example.test/example-results",
                    "published_parsed": recent,
                }
            ]
        else:
            entries = [
                {
                    "title": "Example Industries Limited",
                    "summary": "Board Meeting",
                    "link": "https://example.test/example-board",
                    "published_parsed": recent,
                }
            ]

        return _feed(entries)

    monkeypatch.setattr(
        news_engine.feedparser,
        "parse",
        fake_parse,
    )

    bundle = news_engine.fetch_nse_announcements(
        as_of=now,
    )

    assert bundle["available"] is True
    assert bundle["candidate_eligible"] is True
    assert bundle["candidate_blockers"] == []

    profile = news_engine.event_profile_for_symbol(
        "RELIANCE",
        bundle,
        company_name="Reliance Industries Limited",
        aliases=["Reliance Industries"],
        as_of=now,
    )

    assert profile["available"] is True
    assert profile["candidate_eligible"] is True
    assert profile["event_count"] == 1
    assert profile["undated_event_count"] == 0
    assert profile["sentiment"] == "Positive"
    assert profile["event_type"] == "Corporate Development"


def test_feed_metadata_can_verify_source_freshness_but_not_undated_match(
    monkeypatch,
):
    now = datetime.fromisoformat(
        "2026-09-28T12:00:00+05:30"
    )

    recent_feed_time = _parsed_tuple(
        "2026-09-28T06:00:00+00:00"
    )

    def fake_parse(url, request_headers=None):
        return _feed(
            [
                {
                    "title": "Reliance Industries Limited",
                    "summary": "General Updates",
                    "link": url + "#1",
                }
            ],
            feed_meta={
                "updated_parsed": recent_feed_time,
            },
        )

    monkeypatch.setattr(
        news_engine.feedparser,
        "parse",
        fake_parse,
    )

    bundle = news_engine.fetch_nse_announcements(
        as_of=now,
    )

    assert bundle["candidate_eligible"] is True

    announcements = next(
        source
        for source in bundle["sources"]
        if source["source_id"] == "nse_announcements"
    )

    assert announcements["source_time_basis"] == "feed_metadata"
    assert announcements["timestamped_entry_count"] == 0
    assert announcements["undated_entry_count"] == 1

    profile = news_engine.event_profile_for_symbol(
        "RELIANCE",
        bundle,
        company_name="Reliance Industries Limited",
        as_of=now,
    )

    assert profile["candidate_eligible"] is False
    assert profile["event_count"] == 0
    assert profile["undated_event_count"] >= 1
    assert profile["event_type"] == "TimestampUnverified"


def test_feed_metadata_with_no_symbol_match_can_return_nonefound(
    monkeypatch,
):
    now = datetime.fromisoformat(
        "2026-09-28T12:00:00+05:30"
    )

    recent_feed_time = _parsed_tuple(
        "2026-09-28T06:00:00+00:00"
    )

    def fake_parse(url, request_headers=None):
        return _feed(
            [
                {
                    "title": "Another Company Limited",
                    "summary": "General Updates",
                    "link": url + "#1",
                }
            ],
            feed_meta={
                "updated_parsed": recent_feed_time,
            },
        )

    monkeypatch.setattr(
        news_engine.feedparser,
        "parse",
        fake_parse,
    )

    bundle = news_engine.fetch_nse_announcements(
        as_of=now,
    )

    profile = news_engine.event_profile_for_symbol(
        "RELIANCE",
        bundle,
        company_name="Reliance Industries Limited",
        as_of=now,
    )

    assert profile["available"] is True
    assert profile["candidate_eligible"] is True
    assert profile["event_count"] == 0
    assert profile["undated_event_count"] == 0
    assert profile["sentiment"] == "Neutral"
    assert profile["event_type"] == "NoneFound"


def test_no_entry_or_feed_timestamp_fails_closed(monkeypatch):
    now = datetime.fromisoformat(
        "2026-09-28T12:00:00+05:30"
    )

    def fake_parse(url, request_headers=None):
        return _feed(
            [
                {
                    "title": "Example Industries Limited",
                    "summary": "General Updates",
                    "link": url + "#1",
                }
            ]
        )

    monkeypatch.setattr(
        news_engine.feedparser,
        "parse",
        fake_parse,
    )

    bundle = news_engine.fetch_nse_announcements(
        as_of=now,
    )

    assert bundle["available"] is True
    assert bundle["candidate_eligible"] is False
    assert bundle["candidate_blockers"]

    announcements = next(
        source
        for source in bundle["sources"]
        if source["source_id"] == "nse_announcements"
    )

    assert announcements["latest_item_at"] is None
    assert announcements["source_time_basis"] == "unverified"


def test_stale_required_source_blocks_candidate(monkeypatch):
    now = datetime.fromisoformat(
        "2026-09-28T12:00:00+05:30"
    )

    stale = _parsed_tuple(
        "2026-09-10T06:00:00+00:00"
    )

    def fake_parse(url, request_headers=None):
        return _feed(
            [
                {
                    "title": "Example Industries Limited",
                    "summary": "General Updates",
                    "link": url + "#1",
                    "published_parsed": stale,
                }
            ]
        )

    monkeypatch.setattr(
        news_engine.feedparser,
        "parse",
        fake_parse,
    )

    bundle = news_engine.fetch_nse_announcements(
        as_of=now,
    )

    assert bundle["available"] is True
    assert bundle["candidate_eligible"] is False
    assert bundle["candidate_blockers"]


def test_negative_event_outranks_positive_within_lookback(monkeypatch):
    now = datetime.fromisoformat(
        "2026-09-28T12:00:00+05:30"
    )

    recent = _parsed_tuple(
        "2026-09-28T06:00:00+00:00"
    )

    def fake_parse(url, request_headers=None):
        if "Online_announcements" in url:
            entries = [
                {
                    "title": "Reliance Industries Limited",
                    "summary": "New order received",
                    "link": "https://example.test/positive",
                    "published_parsed": recent,
                },
                {
                    "title": "Reliance Industries Limited",
                    "summary": "Credit Rating Downgrade",
                    "link": "https://example.test/negative",
                    "published_parsed": recent,
                },
            ]
        else:
            entries = [
                {
                    "title": "Example Industries Limited",
                    "summary": "General update",
                    "link": url + "#context",
                    "published_parsed": recent,
                }
            ]

        return _feed(entries)

    monkeypatch.setattr(
        news_engine.feedparser,
        "parse",
        fake_parse,
    )

    bundle = news_engine.fetch_nse_announcements(
        as_of=now,
    )

    profile = news_engine.event_profile_for_symbol(
        "RELIANCE",
        bundle,
        company_name="Reliance Industries Limited",
        as_of=now,
    )

    assert profile["sentiment"] == "Negative"
    assert profile["event_type"] == "Material Risk"


def test_no_matching_event_is_neutral_when_source_is_healthy(monkeypatch):
    now = datetime.fromisoformat(
        "2026-09-28T12:00:00+05:30"
    )

    recent = _parsed_tuple(
        "2026-09-28T06:00:00+00:00"
    )

    def fake_parse(url, request_headers=None):
        return _feed(
            [
                {
                    "title": "Another Company Limited",
                    "summary": "General Updates",
                    "link": url + "#1",
                    "published_parsed": recent,
                }
            ]
        )

    monkeypatch.setattr(
        news_engine.feedparser,
        "parse",
        fake_parse,
    )

    bundle = news_engine.fetch_nse_announcements(
        as_of=now,
    )

    profile = news_engine.event_profile_for_symbol(
        "RELIANCE",
        bundle,
        company_name="Reliance Industries Limited",
        as_of=now,
    )

    assert profile["available"] is True
    assert profile["candidate_eligible"] is True
    assert profile["event_count"] == 0
    assert profile["sentiment"] == "Neutral"
    assert profile["event_type"] == "NoneFound"


def test_dedupe_preserves_same_link_across_different_official_sources():
    items = [
        {
            "source_id": "nse_announcements",
            "title": "Example",
            "link": "https://example.test/filing",
            "published_at": "2026-09-28T10:00:00+05:30",
        },
        {
            "source_id": "nse_financial_results",
            "title": "Example",
            "link": "https://example.test/filing",
            "published_at": "2026-09-28T10:00:00+05:30",
        },
    ]

    result = news_engine._dedupe_items(items)

    assert len(result) == 2
