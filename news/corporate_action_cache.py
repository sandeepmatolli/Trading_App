from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

from config import (
    NSE_CORPORATE_ACTIONS_API_URL,
    SQLITE_DB_PATH,
)
from news.cache_db import cache_connection


PathLike = Union[str, Path]


CORPORATE_ACTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS corporate_action_query_cache (
    query_key TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    boundary_dates_json TEXT NOT NULL,
    padding_days INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    available INTEGER,
    complete INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    action_count INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    total_elapsed_ms REAL,
    last_attempt_at TEXT,
    last_http_status INTEGER,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    next_retry_after TEXT,
    served_from_cache INTEGER NOT NULL DEFAULT 0,
    cache_age_seconds REAL,
    updated_at TEXT,
    result_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ca_query_symbol
    ON corporate_action_query_cache(symbol);

CREATE TABLE IF NOT EXISTS corporate_action_items (
    action_key TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    purpose TEXT,
    action_type TEXT,
    ratio TEXT,
    theoretical_price_factor REAL,
    ex_date TEXT,
    record_date TEXT,
    source_id TEXT,
    source_url TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ca_items_symbol_exdate
    ON corporate_action_items(symbol, ex_date);
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


def _ensure_query_cache_columns(
    connection,
) -> None:
    existing = {
        str(row["name"])
        for row in connection.execute(
            "PRAGMA table_info(corporate_action_query_cache)"
        ).fetchall()
    }

    additions = {
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "total_elapsed_ms": "REAL",
        "last_attempt_at": "TEXT",
        "last_http_status": "INTEGER",
        "consecutive_failures": "INTEGER NOT NULL DEFAULT 0",
        "last_error": "TEXT",
        "next_retry_after": "TEXT",
        "served_from_cache": "INTEGER NOT NULL DEFAULT 0",
        "cache_age_seconds": "REAL",
        "updated_at": "TEXT",
    }

    for name, sql_type in additions.items():
        if name in existing:
            continue
        connection.execute(
            f"ALTER TABLE corporate_action_query_cache "
            f"ADD COLUMN {name} {sql_type}"
        )


def _action_key(action: Dict) -> str:
    identity = "|".join(
        [
            str(
                action.get(
                    "symbol",
                    "",
                )
                or ""
            ).upper(),
            str(
                action.get(
                    "purpose",
                    "",
                )
                or ""
            ).strip().lower(),
            str(
                action.get(
                    "ex_date",
                    "",
                )
                or ""
            ),
            str(
                action.get(
                    "record_date",
                    "",
                )
                or ""
            ),
            str(
                action.get(
                    "source_id",
                    "",
                )
                or ""
            ),
        ]
    )

    return hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()


class CorporateActionCache:
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
                CORPORATE_ACTION_SCHEMA
            )
            _ensure_query_cache_columns(
                connection
            )

    @staticmethod
    def make_query_key(
        *,
        symbol: str,
        boundary_dates: Iterable[str],
        padding_days: int,
        source_url: str = NSE_CORPORATE_ACTIONS_API_URL,
    ) -> str:
        normalized_dates = sorted(
            {
                str(item).strip()
                for item in boundary_dates
                if str(item).strip()
            }
        )

        identity = _json_dumps(
            {
                "symbol": str(
                    symbol
                ).strip().upper(),
                "boundary_dates": normalized_dates,
                "padding_days": int(
                    padding_days
                ),
                "source_url": str(
                    source_url
                ).strip(),
                "schema_version": 1,
            }
        )

        return hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()

    def store_result(
        self,
        *,
        query_key: str,
        symbol: str,
        boundary_dates: List[str],
        padding_days: int,
        result: Dict,
        retrieved_at: datetime,
        telemetry: Optional[Dict] = None,
    ) -> None:
        retrieved_text = (
            retrieved_at.isoformat()
        )
        clean_result = deepcopy(
            result
        )
        clean_result.pop(
            "cache",
            None,
        )

        telemetry_values = (
            deepcopy(telemetry)
            if isinstance(
                telemetry,
                dict,
            )
            else {}
        )

        with cache_connection(
            self.db_path
        ) as connection:
            existing = connection.execute(
                """
                SELECT consecutive_failures
                FROM corporate_action_query_cache
                WHERE query_key = ?
                """,
                (query_key,),
            ).fetchone()

            previous_failures = (
                int(
                    existing[
                        "consecutive_failures"
                    ]
                )
                if existing is not None
                else 0
            )

            complete = bool(
                clean_result.get(
                    "complete",
                    False,
                )
            )

            consecutive_failures = (
                0
                if complete
                else previous_failures + 1
            )

            connection.execute(
                """
                INSERT INTO corporate_action_query_cache (
                    query_key,
                    symbol,
                    boundary_dates_json,
                    padding_days,
                    source_url,
                    retrieved_at,
                    available,
                    complete,
                    status,
                    action_count,
                    attempt_count,
                    total_elapsed_ms,
                    last_attempt_at,
                    last_http_status,
                    consecutive_failures,
                    last_error,
                    next_retry_after,
                    served_from_cache,
                    cache_age_seconds,
                    updated_at,
                    result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(query_key) DO UPDATE SET
                    retrieved_at = excluded.retrieved_at,
                    available = excluded.available,
                    complete = excluded.complete,
                    status = excluded.status,
                    action_count = excluded.action_count,
                    attempt_count = excluded.attempt_count,
                    total_elapsed_ms = excluded.total_elapsed_ms,
                    last_attempt_at = excluded.last_attempt_at,
                    last_http_status = excluded.last_http_status,
                    consecutive_failures = excluded.consecutive_failures,
                    last_error = excluded.last_error,
                    next_retry_after = excluded.next_retry_after,
                    served_from_cache = excluded.served_from_cache,
                    cache_age_seconds = excluded.cache_age_seconds,
                    updated_at = excluded.updated_at,
                    result_json = excluded.result_json
                """,
                (
                    query_key,
                    str(
                        symbol
                    ).strip().upper(),
                    _json_dumps(
                        sorted(
                            boundary_dates
                        )
                    ),
                    int(padding_days),
                    str(
                        clean_result.get(
                            "source_url",
                            NSE_CORPORATE_ACTIONS_API_URL,
                        )
                    ),
                    retrieved_text,
                    (
                        None
                        if clean_result.get(
                            "available"
                        )
                        is None
                        else int(
                            bool(
                                clean_result.get(
                                    "available"
                                )
                            )
                        )
                    ),
                    int(complete),
                    str(
                        clean_result.get(
                            "status",
                            "UNKNOWN",
                        )
                    ),
                    int(
                        clean_result.get(
                            "action_count",
                            len(
                                clean_result.get(
                                    "actions",
                                    [],
                                )
                                or []
                            ),
                        )
                        or 0
                    ),
                    int(
                        telemetry_values.get(
                            "attempt_count",
                            0,
                        )
                        or 0
                    ),
                    telemetry_values.get(
                        "total_elapsed_ms"
                    ),
                    telemetry_values.get(
                        "last_attempt_at"
                    ),
                    telemetry_values.get(
                        "last_http_status"
                    ),
                    consecutive_failures,
                    telemetry_values.get(
                        "last_error"
                    ),
                    telemetry_values.get(
                        "next_retry_after"
                    ),
                    int(
                        bool(
                            telemetry_values.get(
                                "served_from_cache",
                                False,
                            )
                        )
                    ),
                    telemetry_values.get(
                        "cache_age_seconds"
                    ),
                    retrieved_text,
                    _json_dumps(
                        clean_result
                    ),
                ),
            )

            for action in clean_result.get(
                "actions",
                [],
            ) or []:
                key = _action_key(
                    action
                )

                connection.execute(
                    """
                    INSERT INTO corporate_action_items (
                        action_key,
                        symbol,
                        purpose,
                        action_type,
                        ratio,
                        theoretical_price_factor,
                        ex_date,
                        record_date,
                        source_id,
                        source_url,
                        first_seen_at,
                        last_seen_at,
                        payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(action_key) DO UPDATE SET
                        purpose = excluded.purpose,
                        action_type = excluded.action_type,
                        ratio = excluded.ratio,
                        theoretical_price_factor = excluded.theoretical_price_factor,
                        ex_date = excluded.ex_date,
                        record_date = excluded.record_date,
                        source_url = excluded.source_url,
                        last_seen_at = excluded.last_seen_at,
                        payload_json = excluded.payload_json
                    """,
                    (
                        key,
                        str(
                            action.get(
                                "symbol",
                                symbol,
                            )
                            or symbol
                        ).strip().upper(),
                        action.get(
                            "purpose"
                        ),
                        action.get(
                            "action_type"
                        ),
                        action.get(
                            "ratio"
                        ),
                        action.get(
                            "theoretical_price_factor"
                        ),
                        action.get(
                            "ex_date"
                        ),
                        action.get(
                            "record_date"
                        ),
                        action.get(
                            "source_id"
                        ),
                        action.get(
                            "source_url"
                        ),
                        retrieved_text,
                        retrieved_text,
                        _json_dumps(
                            action
                        ),
                    ),
                )

    def load_fresh_complete_result(
        self,
        *,
        query_key: str,
        now: datetime,
        max_age_days: int,
        not_after: Optional[datetime] = None,
    ) -> Optional[Tuple[Dict, datetime, float]]:
        with cache_connection(
            self.db_path
        ) as connection:
            row = connection.execute(
                """
                SELECT retrieved_at,
                       complete,
                       result_json
                FROM corporate_action_query_cache
                WHERE query_key = ?
                """,
                (query_key,),
            ).fetchone()

        if row is None:
            return None

        if not bool(
            row["complete"]
        ):
            # Failed/partial results are intentionally retained for telemetry,
            # but never reused as authoritative absence-of-action evidence.
            return None

        retrieved_at = _as_aware_datetime(
            row["retrieved_at"]
        )
        now_aware = _as_aware_datetime(
            now
        )

        if (
            retrieved_at is None
            or now_aware is None
        ):
            return None

        if retrieved_at > now_aware:
            return None

        if not_after is not None:
            cutoff = _as_aware_datetime(
                not_after
            )
            if (
                cutoff is None
                or retrieved_at > cutoff
            ):
                return None

        age_seconds = (
            now_aware
            - retrieved_at
        ).total_seconds()

        if age_seconds > (
            max_age_days * 86400
        ):
            return None

        result = _json_loads(
            row["result_json"],
            {},
        )
        if not isinstance(
            result,
            dict,
        ):
            return None

        return (
            result,
            retrieved_at,
            age_seconds,
        )

    def mark_cache_serve(
        self,
        *,
        query_key: str,
        served_at: datetime,
        age_seconds: float,
    ) -> None:
        with cache_connection(
            self.db_path
        ) as connection:
            connection.execute(
                """
                UPDATE corporate_action_query_cache
                SET served_from_cache = 1,
                    cache_age_seconds = ?,
                    updated_at = ?
                WHERE query_key = ?
                """,
                (
                    float(age_seconds),
                    served_at.isoformat(),
                    query_key,
                ),
            )

    def get_query_state(
        self,
        query_key: str,
    ) -> Optional[Dict]:
        with cache_connection(
            self.db_path
        ) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM corporate_action_query_cache
                WHERE query_key = ?
                """,
                (query_key,),
            ).fetchone()

        if row is None:
            return None

        result = dict(row)
        result["complete"] = bool(
            result.get(
                "complete"
            )
        )
        if result.get("available") is not None:
            result["available"] = bool(
                result.get(
                    "available"
                )
            )
        result["served_from_cache"] = bool(
            result.get(
                "served_from_cache"
            )
        )
        return result

    def count_actions(self) -> int:
        with cache_connection(
            self.db_path
        ) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM corporate_action_items
                """
            ).fetchone()

        return int(
            row["count"]
            if row is not None
            else 0
        )