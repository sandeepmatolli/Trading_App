from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from config import (
    NEWS_CACHE_ENABLED,
    NEWS_CACHE_REFRESH_MINUTES,
    NSE_HISTORICAL_CA_CACHE_ENABLED,
    NSE_HISTORICAL_CA_CACHE_REFRESH_DAYS,
    NSE_HISTORICAL_CA_PADDING_DAYS,
    NSE_HISTORICAL_CA_TIMEOUT_SECONDS,
)
from news.corporate_action_cache import (
    CorporateActionCache,
)
from news.event_cache import EventCache
from news.news_engine import (
    fetch_nse_announcements as _fetch_nse_announcements_uncached,
)
from news.nse_historical_corporate_actions import (
    fetch_historical_actions_for_discontinuities
    as _fetch_historical_actions_uncached,
)


IST = ZoneInfo("Asia/Kolkata")


def _as_ist(
    value: Optional[datetime],
) -> datetime:
    if value is None:
        return datetime.now(tz=IST)

    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(
            str(value)
        )

    if parsed.tzinfo is None:
        return parsed.replace(
            tzinfo=IST
        )

    return parsed.astimezone(IST)


def _cache_meta(
    *,
    enabled: bool,
    hit: bool,
    refreshed: bool,
    stale_fallback: bool,
    retrieved_at: Optional[datetime],
    age_seconds: Optional[float],
    backend: str = "sqlite",
    refresh_error: Optional[str] = None,
) -> Dict:
    return {
        "enabled": bool(enabled),
        "backend": backend,
        "hit": bool(hit),
        "refreshed": bool(refreshed),
        "stale_fallback": bool(
            stale_fallback
        ),
        "retrieved_at": (
            retrieved_at.isoformat()
            if retrieved_at is not None
            else None
        ),
        "age_seconds": (
            round(
                float(age_seconds),
                3,
            )
            if age_seconds is not None
            else None
        ),
        "refresh_error": refresh_error,
    }


def _append_unique(
    target: List[str],
    value: str,
) -> None:
    if value not in target:
        target.append(value)


def fetch_nse_announcements(
    as_of: Optional[datetime] = None,
    *,
    force_refresh: bool = False,
    cache: Optional[EventCache] = None,
    refresh_minutes: Optional[int] = None,
) -> Dict:
    """
    Persistent wrapper around the already-verified NSE RSS engine.

    The underlying parsing, timestamp, freshness, dedupe, and classification
    logic stays in news.news_engine. This wrapper only decides whether a recent
    successful/failed bundle can be reused and persists its normalized output.
    """
    now = _as_ist(
        as_of
    )
    refresh_window = int(
        NEWS_CACHE_REFRESH_MINUTES
        if refresh_minutes is None
        else refresh_minutes
    )

    if refresh_window < 0:
        raise ValueError(
            "refresh_minutes must be >= 0."
        )

    cache_enabled = bool(
        NEWS_CACHE_ENABLED
        or cache is not None
    )

    if not cache_enabled:
        result = (
            _fetch_nse_announcements_uncached(
                as_of=as_of,
            )
        )
        result = deepcopy(result)
        result["cache"] = _cache_meta(
            enabled=False,
            hit=False,
            refreshed=True,
            stale_fallback=False,
            retrieved_at=now,
            age_seconds=0.0,
            backend="disabled",
        )
        return result

    cache_obj = (
        cache
        if cache is not None
        else EventCache()
    )

    if not force_refresh:
        cached = cache_obj.load_fresh_bundle(
            now=now,
            max_age_minutes=refresh_window,
        )

        if cached is not None:
            bundle, retrieved_at, age_seconds = (
                cached
            )
            result = deepcopy(
                bundle
            )
            result["cache"] = _cache_meta(
                enabled=True,
                hit=True,
                refreshed=False,
                stale_fallback=False,
                retrieved_at=retrieved_at,
                age_seconds=age_seconds,
            )
            return result

    try:
        fresh = (
            _fetch_nse_announcements_uncached(
                as_of=as_of,
            )
        )
    except Exception as exc:
        # Unexpected wrapper-level/network exception. The underlying engine is
        # already mostly fail-closed, but keep a final conservative fallback.
        latest = cache_obj.load_latest_bundle()

        if latest is not None:
            stale_bundle, retrieved_at = (
                latest
            )
            result = deepcopy(
                stale_bundle
            )
            result["candidate_eligible"] = False
            result["degraded"] = True

            errors = list(
                result.get(
                    "errors",
                    [],
                )
                or []
            )
            blockers = list(
                result.get(
                    "candidate_blockers",
                    [],
                )
                or []
            )

            message = (
                "Persistent event refresh failed: "
                + str(exc)
            )
            blocker = (
                "Event refresh failed; stale cached evidence cannot permit "
                "CANDIDATE."
            )

            _append_unique(
                errors,
                message,
            )
            _append_unique(
                blockers,
                blocker,
            )

            result["errors"] = errors
            result[
                "candidate_blockers"
            ] = blockers

            age_seconds = max(
                0.0,
                (
                    now
                    - retrieved_at.astimezone(
                        IST
                    )
                ).total_seconds(),
            )

            result["cache"] = _cache_meta(
                enabled=True,
                hit=True,
                refreshed=False,
                stale_fallback=True,
                retrieved_at=retrieved_at,
                age_seconds=age_seconds,
                refresh_error=str(exc),
            )
            return result

        return {
            "available": False,
            "candidate_eligible": False,
            "degraded": True,
            "items": [],
            "sources": [],
            "errors": [
                "Persistent event refresh failed: "
                + str(exc)
            ],
            "warnings": [],
            "candidate_blockers": [
                "Official event evidence is unavailable because refresh "
                "failed and no cache exists."
            ],
            "as_of": now.isoformat(),
            "cache": _cache_meta(
                enabled=True,
                hit=False,
                refreshed=False,
                stale_fallback=False,
                retrieved_at=None,
                age_seconds=None,
                refresh_error=str(exc),
            ),
        }

    cache_obj.store_bundle(
        fresh,
        retrieved_at=now,
    )

    result = deepcopy(
        fresh
    )
    result["cache"] = _cache_meta(
        enabled=True,
        hit=False,
        refreshed=True,
        stale_fallback=False,
        retrieved_at=now,
        age_seconds=0.0,
    )
    return result


def _boundary_dates(
    corporate_action_guard: Optional[Dict],
) -> List[str]:
    guard = (
        corporate_action_guard
        if isinstance(
            corporate_action_guard,
            dict,
        )
        else {}
    )

    values = set()

    for event in guard.get(
        "events",
        [],
    ) or []:
        raw = event.get(
            "current_ts"
        )
        if raw in (None, ""):
            continue

        try:
            parsed = datetime.fromisoformat(
                str(raw)
            )
        except Exception:
            continue

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=IST
            )
        else:
            parsed = parsed.astimezone(
                IST
            )

        values.add(
            parsed.date().isoformat()
        )

    return sorted(values)


def fetch_historical_actions_for_discontinuities(
    symbol: str,
    corporate_action_guard: Optional[Dict],
    *,
    padding_days: int = NSE_HISTORICAL_CA_PADDING_DAYS,
    timeout_seconds: int = NSE_HISTORICAL_CA_TIMEOUT_SECONDS,
    session_factory: Optional[Callable] = None,
    as_of: Optional[datetime] = None,
    force_refresh: bool = False,
    cache: Optional[
        CorporateActionCache
    ] = None,
    refresh_days: Optional[int] = None,
) -> Dict:
    """
    Persistent wrapper around the existing fail-closed historical NSE adapter.

    Only COMPLETE cached queries are reusable. Partial/unavailable cached
    searches are retained for telemetry but are never interpreted as proof
    that no corporate action exists.
    """
    now = _as_ist(
        as_of
    )
    refresh_window = int(
        NSE_HISTORICAL_CA_CACHE_REFRESH_DAYS
        if refresh_days is None
        else refresh_days
    )

    if refresh_window < 0:
        raise ValueError(
            "refresh_days must be >= 0."
        )

    boundary_dates = _boundary_dates(
        corporate_action_guard
    )

    kwargs = {
        "padding_days": padding_days,
        "timeout_seconds": timeout_seconds,
        "as_of": as_of,
    }
    if session_factory is not None:
        kwargs[
            "session_factory"
        ] = session_factory

    # Preserve the existing zero-network fast path when no discontinuity exists.
    if not boundary_dates:
        result = (
            _fetch_historical_actions_uncached(
                symbol,
                corporate_action_guard,
                **kwargs,
            )
        )
        result = deepcopy(
            result
        )
        result["cache"] = _cache_meta(
            enabled=bool(
                NSE_HISTORICAL_CA_CACHE_ENABLED
                or cache is not None
            ),
            hit=False,
            refreshed=False,
            stale_fallback=False,
            retrieved_at=None,
            age_seconds=None,
        )
        return result

    cache_enabled = bool(
        NSE_HISTORICAL_CA_CACHE_ENABLED
        or cache is not None
    )

    if not cache_enabled:
        result = (
            _fetch_historical_actions_uncached(
                symbol,
                corporate_action_guard,
                **kwargs,
            )
        )
        result = deepcopy(
            result
        )
        result["cache"] = _cache_meta(
            enabled=False,
            hit=False,
            refreshed=True,
            stale_fallback=False,
            retrieved_at=now,
            age_seconds=0.0,
            backend="disabled",
        )
        return result

    cache_obj = (
        cache
        if cache is not None
        else CorporateActionCache()
    )

    query_key = (
        CorporateActionCache.make_query_key(
            symbol=symbol,
            boundary_dates=boundary_dates,
            padding_days=padding_days,
        )
    )

    if not force_refresh:
        cached = (
            cache_obj.load_fresh_complete_result(
                query_key=query_key,
                now=now,
                max_age_days=refresh_window,
            )
        )

        if cached is not None:
            result, retrieved_at, age_seconds = (
                cached
            )
            output = deepcopy(
                result
            )
            output["cache"] = {
                **_cache_meta(
                    enabled=True,
                    hit=True,
                    refreshed=False,
                    stale_fallback=False,
                    retrieved_at=retrieved_at,
                    age_seconds=age_seconds,
                ),
                "query_key": query_key,
                "complete_result_reused": True,
            }
            return output

    result = (
        _fetch_historical_actions_uncached(
            symbol,
            corporate_action_guard,
            **kwargs,
        )
    )

    cache_obj.store_result(
        query_key=query_key,
        symbol=symbol,
        boundary_dates=boundary_dates,
        padding_days=padding_days,
        result=result,
        retrieved_at=now,
    )

    output = deepcopy(
        result
    )
    output["cache"] = {
        **_cache_meta(
            enabled=True,
            hit=False,
            refreshed=True,
            stale_fallback=False,
            retrieved_at=now,
            age_seconds=0.0,
        ),
        "query_key": query_key,
        "complete_result_reused": False,
    }
    return output