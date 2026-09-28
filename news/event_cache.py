from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from config import SQLITE_DB_PATH
from news.cache_db import cache_connection


PathLike = Union[str, Path]


EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_bundle_cache (
    cache_key TEXT PRIMARY KEY,
    retrieved_at TEXT NOT NULL,
    bundle_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_source_state (
    source_id TEXT PRIMARY KEY,
    source_type TEXT,
    source_url TEXT,
    required_for_candidate INTEGER NOT NULL DEFAULT 0,
    reliability TEXT,
    last_attempt_at TEXT,
    last_success_at TEXT,
    latest_item_at TEXT,
    available INTEGER NOT NULL DEFAULT 0,
    candidate_eligible INTEGER NOT NULL DEFAULT 0,
    entry_count INTEGER NOT NULL DEFAULT 0,
    timestamped_entry_count INTEGER NOT NULL DEFAULT 0,
    undated_entry_count INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_items (
    event_key TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    source_type TEXT,
    source_url TEXT,
    symbol_hint TEXT,
    title TEXT NOT NULL,
    summary TEXT,
    link TEXT,
    published_at TEXT,
    published_at_source TEXT,
    timestamp_verified INTEGER NOT NULL DEFAULT 0,
    corporate_action_type TEXT,
    corporate_action_ratio TEXT,
    theoretical_price_factor REAL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_event_items_source
    ON event_items(source_id);

CREATE INDEX IF NOT EXISTS idx_event_items_published
    ON event_items(published_at);
"""


def _json_dumps(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _json_loads(value: Optional[str], default):
    if not value:
        return deepcopy(default)

    try:
        return json.loads(value)
    except Exception:
        return deepcopy(default)


def _as_aware_datetime(value) -> Optional[datetime]:
    if value in (None, ""):
        return None

    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(
                str(value)
            )
        except Exception:
            return None

    if parsed.tzinfo is None:
        return parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed


def _event_key(item: Dict) -> str:
    source_id = str(
        item.get("source_id", "")
        or ""
    ).strip()
    link = str(
        item.get("link", "")
        or ""
    ).strip()

    if link:
        identity = (
            f"{source_id}|link|{link}"
        )
    else:
        title = " ".join(
            str(
                item.get("title", "")
                or ""
            )
            .upper()
            .split()
        )
        published_at = str(
            item.get("published_at", "")
            or ""
        )
        identity = (
            f"{source_id}|text|{title}|{published_at}"
        )

    return hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()


class EventCache:
    CACHE_KEY = "nse_corporate_information_bundle_v1"

    def __init__(
        self,
        db_path: PathLike = SQLITE_DB_PATH,
    ) -> None:
        self.db_path = db_path
        self._initialize()

    def _initialize(self) -> None:
        with cache_connection(
            self.db_path
        ) as connection:
            connection.executescript(
                EVENT_SCHEMA
            )

    def store_bundle(
        self,
        bundle: Dict,
        *,
        retrieved_at: datetime,
    ) -> None:
        retrieved_text = (
            retrieved_at.isoformat()
        )

        clean_bundle = deepcopy(
            bundle
        )
        clean_bundle.pop(
            "cache",
            None,
        )

        with cache_connection(
            self.db_path
        ) as connection:
            for source in clean_bundle.get(
                "sources",
                [],
            ) or []:
                source_id = str(
                    source.get(
                        "source_id",
                        "",
                    )
                    or ""
                ).strip()

                if not source_id:
                    continue

                existing = connection.execute(
                    """
                    SELECT consecutive_failures,
                           last_success_at
                    FROM event_source_state
                    WHERE source_id = ?
                    """,
                    (source_id,),
                ).fetchone()

                available = bool(
                    source.get(
                        "available",
                        False,
                    )
                )

                previous_failures = (
                    int(
                        existing[
                            "consecutive_failures"
                        ]
                    )
                    if existing is not None
                    else 0
                )

                consecutive_failures = (
                    0
                    if available
                    else previous_failures + 1
                )

                if available:
                    last_success_at = (
                        retrieved_text
                    )
                elif existing is not None:
                    last_success_at = (
                        existing[
                            "last_success_at"
                        ]
                    )
                else:
                    last_success_at = None

                connection.execute(
                    """
                    INSERT INTO event_source_state (
                        source_id,
                        source_type,
                        source_url,
                        required_for_candidate,
                        reliability,
                        last_attempt_at,
                        last_success_at,
                        latest_item_at,
                        available,
                        candidate_eligible,
                        entry_count,
                        timestamped_entry_count,
                        undated_entry_count,
                        consecutive_failures,
                        errors_json,
                        warnings_json,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id) DO UPDATE SET
                        source_type = excluded.source_type,
                        source_url = excluded.source_url,
                        required_for_candidate = excluded.required_for_candidate,
                        reliability = excluded.reliability,
                        last_attempt_at = excluded.last_attempt_at,
                        last_success_at = excluded.last_success_at,
                        latest_item_at = excluded.latest_item_at,
                        available = excluded.available,
                        candidate_eligible = excluded.candidate_eligible,
                        entry_count = excluded.entry_count,
                        timestamped_entry_count = excluded.timestamped_entry_count,
                        undated_entry_count = excluded.undated_entry_count,
                        consecutive_failures = excluded.consecutive_failures,
                        errors_json = excluded.errors_json,
                        warnings_json = excluded.warnings_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        source_id,
                        source.get(
                            "source_type"
                        ),
                        source.get(
                            "url"
                        ),
                        int(
                            bool(
                                source.get(
                                    "required_for_candidate",
                                    False,
                                )
                            )
                        ),
                        source.get(
                            "reliability"
                        ),
                        retrieved_text,
                        last_success_at,
                        source.get(
                            "latest_item_at"
                        ),
                        int(available),
                        int(
                            bool(
                                source.get(
                                    "candidate_eligible",
                                    False,
                                )
                            )
                        ),
                        int(
                            source.get(
                                "entry_count",
                                0,
                            )
                            or 0
                        ),
                        int(
                            source.get(
                                "timestamped_entry_count",
                                0,
                            )
                            or 0
                        ),
                        int(
                            source.get(
                                "undated_entry_count",
                                0,
                            )
                            or 0
                        ),
                        consecutive_failures,
                        _json_dumps(
                            source.get(
                                "errors",
                                [],
                            )
                            or []
                        ),
                        _json_dumps(
                            source.get(
                                "warnings",
                                [],
                            )
                            or []
                        ),
                        retrieved_text,
                    ),
                )

            for item in clean_bundle.get(
                "items",
                [],
            ) or []:
                key = _event_key(
                    item
                )
                corporate_action = (
                    item.get(
                        "corporate_action"
                    )
                    if isinstance(
                        item.get(
                            "corporate_action"
                        ),
                        dict,
                    )
                    else {}
                )

                connection.execute(
                    """
                    INSERT INTO event_items (
                        event_key,
                        source_id,
                        source_type,
                        source_url,
                        symbol_hint,
                        title,
                        summary,
                        link,
                        published_at,
                        published_at_source,
                        timestamp_verified,
                        corporate_action_type,
                        corporate_action_ratio,
                        theoretical_price_factor,
                        first_seen_at,
                        last_seen_at,
                        payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_key) DO UPDATE SET
                        source_type = excluded.source_type,
                        source_url = excluded.source_url,
                        symbol_hint = excluded.symbol_hint,
                        title = excluded.title,
                        summary = excluded.summary,
                        link = excluded.link,
                        published_at = excluded.published_at,
                        published_at_source = excluded.published_at_source,
                        timestamp_verified = excluded.timestamp_verified,
                        corporate_action_type = excluded.corporate_action_type,
                        corporate_action_ratio = excluded.corporate_action_ratio,
                        theoretical_price_factor = excluded.theoretical_price_factor,
                        last_seen_at = excluded.last_seen_at,
                        payload_json = excluded.payload_json
                    """,
                    (
                        key,
                        str(
                            item.get(
                                "source_id",
                                "",
                            )
                            or ""
                        ),
                        item.get(
                            "source_type"
                        ),
                        item.get(
                            "source_url"
                        ),
                        item.get(
                            "symbol_hint"
                        ),
                        str(
                            item.get(
                                "title",
                                "",
                            )
                            or ""
                        ),
                        item.get(
                            "summary"
                        ),
                        item.get(
                            "link"
                        ),
                        item.get(
                            "published_at"
                        ),
                        item.get(
                            "published_at_source"
                        ),
                        int(
                            bool(
                                item.get(
                                    "timestamp_verified",
                                    False,
                                )
                            )
                        ),
                        corporate_action.get(
                            "action_type"
                        ),
                        corporate_action.get(
                            "ratio"
                        ),
                        corporate_action.get(
                            "theoretical_price_factor"
                        ),
                        retrieved_text,
                        retrieved_text,
                        _json_dumps(
                            item
                        ),
                    ),
                )

            connection.execute(
                """
                INSERT INTO event_bundle_cache (
                    cache_key,
                    retrieved_at,
                    bundle_json
                ) VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    retrieved_at = excluded.retrieved_at,
                    bundle_json = excluded.bundle_json
                """,
                (
                    self.CACHE_KEY,
                    retrieved_text,
                    _json_dumps(
                        clean_bundle
                    ),
                ),
            )

    def load_latest_bundle(
        self,
    ) -> Optional[Tuple[Dict, datetime]]:
        with cache_connection(
            self.db_path
        ) as connection:
            row = connection.execute(
                """
                SELECT retrieved_at,
                       bundle_json
                FROM event_bundle_cache
                WHERE cache_key = ?
                """,
                (self.CACHE_KEY,),
            ).fetchone()

        if row is None:
            return None

        retrieved_at = _as_aware_datetime(
            row["retrieved_at"]
        )
        if retrieved_at is None:
            return None

        bundle = _json_loads(
            row["bundle_json"],
            {},
        )
        if not isinstance(
            bundle,
            dict,
        ):
            return None

        return bundle, retrieved_at

    def load_fresh_bundle(
        self,
        *,
        now: datetime,
        max_age_minutes: int,
    ) -> Optional[Tuple[Dict, datetime, float]]:
        latest = self.load_latest_bundle()
        if latest is None:
            return None

        bundle, retrieved_at = latest

        now_aware = _as_aware_datetime(
            now
        )
        retrieved_aware = (
            _as_aware_datetime(
                retrieved_at
            )
        )

        if (
            now_aware is None
            or retrieved_aware is None
        ):
            return None

        # Never allow cache data first retrieved in the future relative to the
        # requested as_of time. This matters for eventual point-in-time work.
        if retrieved_aware > now_aware:
            return None

        age_seconds = (
            now_aware
            - retrieved_aware
        ).total_seconds()

        if age_seconds > (
            max_age_minutes * 60
        ):
            return None

        return (
            bundle,
            retrieved_aware,
            age_seconds,
        )

    def count_items(self) -> int:
        with cache_connection(
            self.db_path
        ) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM event_items"
            ).fetchone()

        return int(
            row["count"]
            if row is not None
            else 0
        )

    def get_source_state(
        self,
        source_id: str,
    ) -> Optional[Dict]:
        with cache_connection(
            self.db_path
        ) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM event_source_state
                WHERE source_id = ?
                """,
                (source_id,),
            ).fetchone()

        if row is None:
            return None

        result = dict(row)
        result["available"] = bool(
            result.get("available")
        )
        result["candidate_eligible"] = bool(
            result.get(
                "candidate_eligible"
            )
        )
        result["required_for_candidate"] = bool(
            result.get(
                "required_for_candidate"
            )
        )
        result["errors"] = _json_loads(
            result.pop(
                "errors_json",
                "[]",
            ),
            [],
        )
        result["warnings"] = _json_loads(
            result.pop(
                "warnings_json",
                "[]",
            ),
            [],
        )
        return result