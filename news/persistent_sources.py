from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import time
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from config import (
    NEWS_CACHE_ENABLED,
    NEWS_CACHE_REFRESH_MINUTES,
    NEWS_FETCH_MAX_ATTEMPTS,
    NEWS_FETCH_RETRY_BASE_SECONDS,
    NEWS_FETCH_RETRY_MAX_SECONDS,
    NEWS_RETRY_COOLDOWN_MINUTES,
    NSE_HISTORICAL_CA_CACHE_ENABLED,
    NSE_HISTORICAL_CA_CACHE_REFRESH_DAYS,
    NSE_HISTORICAL_CA_MAX_ATTEMPTS,
    NSE_HISTORICAL_CA_PADDING_DAYS,
    NSE_HISTORICAL_CA_RETRY_BASE_SECONDS,
    NSE_HISTORICAL_CA_RETRY_COOLDOWN_MINUTES,
    NSE_HISTORICAL_CA_RETRY_MAX_SECONDS,
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
from news.retry_utils import (
    bounded_backoff_seconds,
    retry_after_iso,
)


IST = ZoneInfo("Asia/Kolkata")


class _RetryExhaustedError(RuntimeError):
    def __init__(
        self,
        message: str,
        attempts: List[Dict],
    ) -> None:
        super().__init__(message)
        self.attempts = deepcopy(
            attempts
        )


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


def _wall_clock_ist() -> datetime:
    return datetime.now(tz=IST)


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


def _last_message(
    values,
) -> Optional[str]:
    items = [
        str(value)
        for value in (values or [])
        if str(value).strip()
    ]
    return items[-1] if items else None


def _first_int_status(
    value: Dict,
) -> Optional[int]:
    candidates = []

    for key in (
        "http_status",
        "status_code",
    ):
        candidates.append(
            value.get(key)
        )

    for source in value.get(
        "sources",
        [],
    ) or []:
        if not isinstance(source, dict):
            continue
        for key in (
            "http_status",
            "status_code",
        ):
            candidates.append(
                source.get(key)
            )

    for candidate in candidates:
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue

    return None


def _event_bundle_needs_retry(
    result: Dict,
) -> bool:
    if not isinstance(result, dict):
        return True

    if not bool(
        result.get(
            "available",
            False,
        )
    ):
        return True

    for source in result.get(
        "sources",
        [],
    ) or []:
        if not isinstance(source, dict):
            continue

        if (
            source.get(
                "required_for_candidate",
                False,
            )
            and not source.get(
                "available",
                False,
            )
        ):
            return True

    # Candidate ineligibility caused only by stale-but-available evidence is
    # not retried. Re-querying immediately would not make old official feed
    # timestamps newer and would add avoidable traffic.
    return False


def _event_result_error(
    result: Dict,
) -> Optional[str]:
    error = _last_message(
        result.get(
            "errors",
            [],
        )
    )
    if error:
        return error

    for source in result.get(
        "sources",
        [],
    ) or []:
        if not isinstance(source, dict):
            continue
        error = _last_message(
            source.get(
                "errors",
                [],
            )
        )
        if error:
            return error

    return None


def _event_result_warning(
    result: Dict,
) -> Optional[str]:
    warning = _last_message(
        result.get(
            "warnings",
            [],
        )
    )
    if warning:
        return warning

    for source in result.get(
        "sources",
        [],
    ) or []:
        if not isinstance(source, dict):
            continue
        warning = _last_message(
            source.get(
                "warnings",
                [],
            )
        )
        if warning:
            return warning

    return None


def _health_from_attempts(
    *,
    attempts: List[Dict],
    max_attempts: int,
    served_from_cache: bool,
    cache_age_seconds: Optional[float],
    last_success_at: Optional[datetime],
    next_retry_after: Optional[str],
    evaluation_as_of: datetime,
    retrieved_at: Optional[datetime],
    last_warning: Optional[str] = None,
    origin_health: Optional[Dict] = None,
) -> Dict:
    origin = (
        deepcopy(origin_health)
        if isinstance(
            origin_health,
            dict,
        )
        else {}
    )

    last_attempt = (
        attempts[-1]
        if attempts
        else {}
    )

    total_elapsed_ms = round(
        sum(
            float(
                item.get(
                    "elapsed_ms",
                    0.0,
                )
                or 0.0
            )
            for item in attempts
        ),
        3,
    )

    return {
        "served_from_cache": bool(
            served_from_cache
        ),
        "cache_age_seconds": (
            round(
                float(cache_age_seconds),
                3,
            )
            if cache_age_seconds is not None
            else None
        ),
        "attempt_count": len(attempts),
        "max_attempts": int(max_attempts),
        "retried": len(attempts) > 1,
        "total_elapsed_ms": total_elapsed_ms,
        "last_attempt_at": last_attempt.get(
            "attempted_at"
        ),
        "last_http_status": last_attempt.get(
            "http_status"
        ),
        "last_error": last_attempt.get(
            "error"
        ),
        "last_warning": last_warning,
        "last_success_at": (
            last_success_at.isoformat()
            if last_success_at is not None
            else origin.get(
                "last_success_at"
            )
        ),
        "next_retry_after": next_retry_after,
        "evaluation_as_of": (
            evaluation_as_of.isoformat()
        ),
        "retrieved_at": (
            retrieved_at.isoformat()
            if retrieved_at is not None
            else None
        ),
        "origin_attempt_count": origin.get(
            "attempt_count"
        ),
        "attempts": deepcopy(
            attempts
        ),
    }


def _event_live_fetch(
    *,
    as_of: Optional[datetime],
    evaluation_as_of: datetime,
) -> Dict:
    max_attempts = max(
        1,
        int(
            NEWS_FETCH_MAX_ATTEMPTS
        ),
    )

    attempts: List[Dict] = []
    final_result: Optional[Dict] = None
    last_exception: Optional[Exception] = None

    for attempt_number in range(
        1,
        max_attempts + 1,
    ):
        attempted_at = _wall_clock_ist()
        started = time.perf_counter()
        result: Optional[Dict] = None
        error: Optional[str] = None

        try:
            result = (
                _fetch_nse_announcements_uncached(
                    as_of=as_of,
                )
            )
            retryable = (
                _event_bundle_needs_retry(
                    result
                )
            )
            error = _event_result_error(
                result
            )
        except Exception as exc:
            last_exception = exc
            retryable = True
            error = str(exc)

        elapsed_ms = round(
            (
                time.perf_counter()
                - started
            )
            * 1000.0,
            3,
        )

        attempts.append(
            {
                "attempt": attempt_number,
                "attempted_at": (
                    attempted_at.isoformat()
                ),
                "elapsed_ms": elapsed_ms,
                "http_status": (
                    _first_int_status(result)
                    if isinstance(
                        result,
                        dict,
                    )
                    else None
                ),
                "retryable": bool(
                    retryable
                ),
                "error": error,
            }
        )

        if isinstance(
            result,
            dict,
        ):
            final_result = result

        if not retryable:
            break

        if attempt_number >= max_attempts:
            break

        delay = bounded_backoff_seconds(
            attempt_number,
            base_seconds=(
                NEWS_FETCH_RETRY_BASE_SECONDS
            ),
            max_seconds=(
                NEWS_FETCH_RETRY_MAX_SECONDS
            ),
        )

        if delay > 0:
            time.sleep(delay)

    retrieved_at = _wall_clock_ist()

    if final_result is None:
        message = (
            str(last_exception)
            if last_exception is not None
            else "Unknown NSE event fetch failure."
        )
        raise _RetryExhaustedError(
            message,
            attempts,
        )

    retry_needed = _event_bundle_needs_retry(
        final_result
    )

    next_retry_after = (
        retry_after_iso(
            retrieved_at,
            cooldown_minutes=(
                NEWS_RETRY_COOLDOWN_MINUTES
            ),
        )
        if retry_needed
        else None
    )

    successful = not retry_needed

    output = deepcopy(
        final_result
    )
    output["source_health"] = (
        _health_from_attempts(
            attempts=attempts,
            max_attempts=max_attempts,
            served_from_cache=False,
            cache_age_seconds=0.0,
            last_success_at=(
                retrieved_at
                if successful
                else None
            ),
            next_retry_after=(
                next_retry_after
            ),
            evaluation_as_of=(
                evaluation_as_of
            ),
            retrieved_at=retrieved_at,
            last_warning=(
                _event_result_warning(
                    final_result
                )
            ),
        )
    )

    return output


def fetch_nse_announcements(
    as_of: Optional[datetime] = None,
    *,
    force_refresh: bool = False,
    cache: Optional[EventCache] = None,
    refresh_minutes: Optional[int] = None,
) -> Dict:
    """
    Persistent, fail-closed wrapper around the verified NSE RSS engine.

    New in this phase:
    - bounded retry for transient/unavailable required-source bundles;
    - request/source-health telemetry;
    - wall-clock retrieval timestamps kept separate from evaluation `as_of`;
    - cache-hit telemetry without pretending a network request occurred.
    """
    evaluation_as_of = _as_ist(
        as_of
    )
    retrieval_now = _wall_clock_ist()

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

    cache_obj = (
        cache
        if cache is not None
        else (
            EventCache()
            if cache_enabled
            else None
        )
    )

    if (
        cache_enabled
        and cache_obj is not None
        and not force_refresh
    ):
        cached = cache_obj.load_fresh_bundle(
            now=retrieval_now,
            max_age_minutes=refresh_window,
            not_after=(
                evaluation_as_of
                if as_of is not None
                else None
            ),
        )

        if cached is not None:
            bundle, retrieved_at, age_seconds = (
                cached
            )
            result = deepcopy(
                bundle
            )
            origin_health = result.get(
                "source_health",
                {},
            )
            result["source_health"] = (
                _health_from_attempts(
                    attempts=[],
                    max_attempts=max(
                        1,
                        int(
                            NEWS_FETCH_MAX_ATTEMPTS
                        ),
                    ),
                    served_from_cache=True,
                    cache_age_seconds=(
                        age_seconds
                    ),
                    last_success_at=None,
                    next_retry_after=None,
                    evaluation_as_of=(
                        evaluation_as_of
                    ),
                    retrieved_at=(
                        retrieved_at
                    ),
                    last_warning=(
                        origin_health.get(
                            "last_warning"
                        )
                        if isinstance(
                            origin_health,
                            dict,
                        )
                        else None
                    ),
                    origin_health=(
                        origin_health
                    ),
                )
            )
            result["cache"] = _cache_meta(
                enabled=True,
                hit=True,
                refreshed=False,
                stale_fallback=False,
                retrieved_at=retrieved_at,
                age_seconds=age_seconds,
            )
            cache_obj.mark_cache_serve(
                served_at=retrieval_now,
                age_seconds=age_seconds,
            )
            return result

    try:
        fresh = _event_live_fetch(
            as_of=as_of,
            evaluation_as_of=(
                evaluation_as_of
            ),
        )
    except Exception as exc:
        fresh = None
        refresh_exception = exc
        failed_attempts = (
            deepcopy(
                exc.attempts
            )
            if isinstance(
                exc,
                _RetryExhaustedError,
            )
            else []
        )
    else:
        refresh_exception = None
        failed_attempts = []

    if fresh is None:
        latest = (
            cache_obj.load_latest_bundle()
            if cache_enabled
            and cache_obj is not None
            else None
        )

        if latest is not None:
            stale_bundle, retrieved_at = (
                latest
            )

            if (
                as_of is None
                or retrieved_at.astimezone(
                    IST
                )
                <= evaluation_as_of
            ):
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
                    + str(refresh_exception)
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
                        retrieval_now
                        - retrieved_at.astimezone(
                            IST
                        )
                    ).total_seconds(),
                )

                origin_health = result.get(
                    "source_health",
                    {},
                )
                result["source_health"] = (
                    _health_from_attempts(
                        attempts=failed_attempts,
                        max_attempts=max(
                            1,
                            int(
                                NEWS_FETCH_MAX_ATTEMPTS
                            ),
                        ),
                        served_from_cache=True,
                        cache_age_seconds=(
                            age_seconds
                        ),
                        last_success_at=None,
                        next_retry_after=(
                            retry_after_iso(
                                retrieval_now,
                                cooldown_minutes=(
                                    NEWS_RETRY_COOLDOWN_MINUTES
                                ),
                            )
                        ),
                        evaluation_as_of=(
                            evaluation_as_of
                        ),
                        retrieved_at=(
                            retrieved_at
                        ),
                        last_warning=None,
                        origin_health=(
                            origin_health
                        ),
                    )
                )
                result["cache"] = _cache_meta(
                    enabled=True,
                    hit=True,
                    refreshed=False,
                    stale_fallback=True,
                    retrieved_at=retrieved_at,
                    age_seconds=age_seconds,
                    refresh_error=str(
                        refresh_exception
                    ),
                )
                cache_obj.mark_cache_serve(
                    served_at=retrieval_now,
                    age_seconds=age_seconds,
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
                + str(refresh_exception)
            ],
            "warnings": [],
            "candidate_blockers": [
                "Official event evidence is unavailable because refresh "
                "failed and no eligible cache exists."
            ],
            "as_of": (
                evaluation_as_of.isoformat()
            ),
            "source_health": (
                _health_from_attempts(
                    attempts=failed_attempts,
                    max_attempts=max(
                        1,
                        int(
                            NEWS_FETCH_MAX_ATTEMPTS
                        ),
                    ),
                    served_from_cache=False,
                    cache_age_seconds=None,
                    last_success_at=None,
                    next_retry_after=(
                        retry_after_iso(
                            retrieval_now,
                            cooldown_minutes=(
                                NEWS_RETRY_COOLDOWN_MINUTES
                            ),
                        )
                    ),
                    evaluation_as_of=(
                        evaluation_as_of
                    ),
                    retrieved_at=None,
                    last_warning=None,
                )
            ),
            "cache": _cache_meta(
                enabled=cache_enabled,
                hit=False,
                refreshed=False,
                stale_fallback=False,
                retrieved_at=None,
                age_seconds=None,
                backend=(
                    "sqlite"
                    if cache_enabled
                    else "disabled"
                ),
                refresh_error=str(
                    refresh_exception
                ),
            ),
        }

    stored_at = _as_ist(
        fresh.get(
            "source_health",
            {},
        ).get(
            "retrieved_at"
        )
    )

    if (
        cache_enabled
        and cache_obj is not None
    ):
        cache_obj.store_bundle(
            fresh,
            retrieved_at=stored_at,
        )

    result = deepcopy(
        fresh
    )
    result["cache"] = _cache_meta(
        enabled=cache_enabled,
        hit=False,
        refreshed=True,
        stale_fallback=False,
        retrieved_at=stored_at,
        age_seconds=0.0,
        backend=(
            "sqlite"
            if cache_enabled
            else "disabled"
        ),
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


def _historical_result_needs_retry(
    result: Dict,
) -> bool:
    if not isinstance(result, dict):
        return True

    return not bool(
        result.get(
            "complete",
            False,
        )
    )


def _historical_result_error(
    result: Dict,
) -> Optional[str]:
    return _last_message(
        result.get(
            "errors",
            [],
        )
    )


def _historical_result_warning(
    result: Dict,
) -> Optional[str]:
    return _last_message(
        result.get(
            "warnings",
            [],
        )
    )


def _historical_live_fetch(
    *,
    symbol: str,
    corporate_action_guard: Optional[Dict],
    kwargs: Dict,
    evaluation_as_of: datetime,
) -> Dict:
    max_attempts = max(
        1,
        int(
            NSE_HISTORICAL_CA_MAX_ATTEMPTS
        ),
    )

    attempts: List[Dict] = []
    final_result: Optional[Dict] = None
    last_exception: Optional[Exception] = None

    for attempt_number in range(
        1,
        max_attempts + 1,
    ):
        attempted_at = _wall_clock_ist()
        started = time.perf_counter()
        result: Optional[Dict] = None
        error: Optional[str] = None

        try:
            result = (
                _fetch_historical_actions_uncached(
                    symbol,
                    corporate_action_guard,
                    **kwargs,
                )
            )
            retryable = (
                _historical_result_needs_retry(
                    result
                )
            )
            error = _historical_result_error(
                result
            )
        except Exception as exc:
            last_exception = exc
            retryable = True
            error = str(exc)

        elapsed_ms = round(
            (
                time.perf_counter()
                - started
            )
            * 1000.0,
            3,
        )

        attempts.append(
            {
                "attempt": attempt_number,
                "attempted_at": (
                    attempted_at.isoformat()
                ),
                "elapsed_ms": elapsed_ms,
                "http_status": (
                    _first_int_status(result)
                    if isinstance(
                        result,
                        dict,
                    )
                    else None
                ),
                "retryable": bool(
                    retryable
                ),
                "error": error,
            }
        )

        if isinstance(
            result,
            dict,
        ):
            final_result = result

        if not retryable:
            break

        if attempt_number >= max_attempts:
            break

        delay = bounded_backoff_seconds(
            attempt_number,
            base_seconds=(
                NSE_HISTORICAL_CA_RETRY_BASE_SECONDS
            ),
            max_seconds=(
                NSE_HISTORICAL_CA_RETRY_MAX_SECONDS
            ),
        )

        if delay > 0:
            time.sleep(delay)

    retrieved_at = _wall_clock_ist()

    if final_result is None:
        message = (
            str(last_exception)
            if last_exception is not None
            else "Unknown historical corporate-action fetch failure."
        )
        final_result = {
            "requested": True,
            "available": False,
            "complete": False,
            "status": "UNAVAILABLE",
            "symbol": str(
                symbol
            ).strip().upper(),
            "source_id": (
                "nse_historical_corporate_actions"
            ),
            "source_url": None,
            "boundary_dates": _boundary_dates(
                corporate_action_guard
            ),
            "windows": [],
            "actions": [],
            "action_count": 0,
            "errors": [
                "Historical corporate-action refresh failed: "
                + message
            ],
            "warnings": [
                "Historical corporate-action confirmation remains "
                "fail-closed."
            ],
        }

    retry_needed = (
        _historical_result_needs_retry(
            final_result
        )
    )

    next_retry_after = (
        retry_after_iso(
            retrieved_at,
            cooldown_minutes=(
                NSE_HISTORICAL_CA_RETRY_COOLDOWN_MINUTES
            ),
        )
        if retry_needed
        else None
    )

    output = deepcopy(
        final_result
    )
    output["source_health"] = (
        _health_from_attempts(
            attempts=attempts,
            max_attempts=max_attempts,
            served_from_cache=False,
            cache_age_seconds=0.0,
            last_success_at=(
                retrieved_at
                if not retry_needed
                else None
            ),
            next_retry_after=(
                next_retry_after
            ),
            evaluation_as_of=(
                evaluation_as_of
            ),
            retrieved_at=retrieved_at,
            last_warning=(
                _historical_result_warning(
                    final_result
                )
            ),
        )
    )

    return output


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
    Persistent, fail-closed historical NSE corporate-action wrapper.

    Only COMPLETE cached queries are reusable. Partial/unavailable searches are
    stored for telemetry but are retried and never treated as authoritative
    proof that no corporate action occurred.
    """
    evaluation_as_of = _as_ist(
        as_of
    )
    retrieval_now = _wall_clock_ist()

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

    cache_enabled = bool(
        NSE_HISTORICAL_CA_CACHE_ENABLED
        or cache is not None
    )

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
        result["source_health"] = {
            "served_from_cache": False,
            "cache_age_seconds": None,
            "attempt_count": 0,
            "max_attempts": max(
                1,
                int(
                    NSE_HISTORICAL_CA_MAX_ATTEMPTS
                ),
            ),
            "retried": False,
            "total_elapsed_ms": 0.0,
            "last_attempt_at": None,
            "last_http_status": None,
            "last_error": None,
            "last_warning": None,
            "last_success_at": None,
            "next_retry_after": None,
            "evaluation_as_of": (
                evaluation_as_of.isoformat()
            ),
            "retrieved_at": None,
            "origin_attempt_count": None,
            "attempts": [],
        }
        result["cache"] = _cache_meta(
            enabled=cache_enabled,
            hit=False,
            refreshed=False,
            stale_fallback=False,
            retrieved_at=None,
            age_seconds=None,
            backend=(
                "sqlite"
                if cache_enabled
                else "disabled"
            ),
        )
        return result

    cache_obj = (
        cache
        if cache is not None
        else (
            CorporateActionCache()
            if cache_enabled
            else None
        )
    )

    query_key = (
        CorporateActionCache.make_query_key(
            symbol=symbol,
            boundary_dates=boundary_dates,
            padding_days=padding_days,
        )
    )

    if (
        cache_enabled
        and cache_obj is not None
        and not force_refresh
    ):
        cached = (
            cache_obj.load_fresh_complete_result(
                query_key=query_key,
                now=retrieval_now,
                max_age_days=refresh_window,
                not_after=(
                    evaluation_as_of
                    if as_of is not None
                    else None
                ),
            )
        )

        if cached is not None:
            result, retrieved_at, age_seconds = (
                cached
            )
            output = deepcopy(
                result
            )
            origin_health = output.get(
                "source_health",
                {},
            )
            output["source_health"] = (
                _health_from_attempts(
                    attempts=[],
                    max_attempts=max(
                        1,
                        int(
                            NSE_HISTORICAL_CA_MAX_ATTEMPTS
                        ),
                    ),
                    served_from_cache=True,
                    cache_age_seconds=(
                        age_seconds
                    ),
                    last_success_at=None,
                    next_retry_after=None,
                    evaluation_as_of=(
                        evaluation_as_of
                    ),
                    retrieved_at=(
                        retrieved_at
                    ),
                    last_warning=(
                        origin_health.get(
                            "last_warning"
                        )
                        if isinstance(
                            origin_health,
                            dict,
                        )
                        else None
                    ),
                    origin_health=(
                        origin_health
                    ),
                )
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
            cache_obj.mark_cache_serve(
                query_key=query_key,
                served_at=retrieval_now,
                age_seconds=age_seconds,
            )
            return output

    result = _historical_live_fetch(
        symbol=symbol,
        corporate_action_guard=(
            corporate_action_guard
        ),
        kwargs=kwargs,
        evaluation_as_of=(
            evaluation_as_of
        ),
    )

    stored_at = _as_ist(
        result.get(
            "source_health",
            {},
        ).get(
            "retrieved_at"
        )
    )

    if (
        cache_enabled
        and cache_obj is not None
    ):
        cache_obj.store_result(
            query_key=query_key,
            symbol=symbol,
            boundary_dates=boundary_dates,
            padding_days=padding_days,
            result=result,
            retrieved_at=stored_at,
            telemetry=result.get(
                "source_health",
                {},
            ),
        )

    output = deepcopy(
        result
    )
    output["cache"] = {
        **_cache_meta(
            enabled=cache_enabled,
            hit=False,
            refreshed=True,
            stale_fallback=False,
            retrieved_at=stored_at,
            age_seconds=0.0,
            backend=(
                "sqlite"
                if cache_enabled
                else "disabled"
            ),
        ),
        "query_key": query_key,
        "complete_result_reused": False,
    }
    return output