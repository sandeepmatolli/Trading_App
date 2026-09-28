from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")

# Reuse the same broad engineering tolerance used by the SMC discontinuity
# hint. This is deliberately conservative and is not claimed to be optimized.
DEFAULT_FACTOR_RELATIVE_TOLERANCE = 0.08

# A labelled official action date should land very close to the price-regime
# boundary. NSE cash actions can interact with holidays/weekends, so allow a
# small calendar-day window instead of requiring an exact same-date match.
DEFAULT_ACTION_DATE_TOLERANCE_DAYS = 4

# When the RSS text contains no labelled record/ex/effective date, publication
# time is weaker evidence. A matching factor plus publication near the boundary
# can be surfaced as PROBABLE, but never CONFIRMED.
DEFAULT_PUBLICATION_LOOKBACK_DAYS = 60
DEFAULT_PUBLICATION_FORWARD_DAYS = 7

OFFICIAL_NSE_SOURCE_IDS = {
    "nse_announcements",
    "nse_corporate_actions",
}

STRONG_CORPORATE_ACTION_SOURCE_ID = "nse_corporate_actions"

ACTION_DATE_LABELS = (
    "record date",
    "record-date",
    "ex date",
    "ex-date",
    "effective date",
    "effective-date",
    "effective from",
    "with effect from",
)

DATE_PATTERNS = (
    r"\d{1,2}[-/]\d{1,2}[-/]\d{4}",
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}",
    r"\d{1,2}-[A-Za-z]{3}-\d{4}",
    r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}",
    r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}",
)

DATE_FORMATS = (
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d %Y",
    "%b %d, %Y",
    "%B %d %Y",
    "%B %d, %Y",
)


def _as_ist_datetime(value) -> Optional[datetime]:
    if value in (None, ""):
        return None

    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value))
        )
    except Exception:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=IST)

    return parsed.astimezone(IST)


def _parse_date_text(value: str) -> Optional[datetime]:
    raw = re.sub(r"\s+", " ", str(value or "").strip())
    if not raw:
        return None

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=IST)
        except Exception:
            continue

    return None


def extract_labeled_action_dates(
    title: str,
    summary: str = "",
) -> List[Dict]:
    """
    Extract only dates that are explicitly labelled as record/ex/effective
    dates. Generic dates are intentionally ignored because a filing can contain
    board-meeting dates, result-period dates, historical references, etc.
    """
    text = re.sub(
        r"\s+",
        " ",
        f"{title or ''} {summary or ''}".strip(),
    )

    if not text:
        return []

    matches: List[Dict] = []
    seen = set()

    for label in ACTION_DATE_LABELS:
        label_pattern = re.escape(label)

        for date_pattern in DATE_PATTERNS:
            pattern = (
                rf"(?i)\b({label_pattern})\b"
                rf"[\s:=-]{{0,12}}"
                rf"({date_pattern})"
            )

            for match in re.finditer(pattern, text):
                parsed = _parse_date_text(match.group(2))
                if parsed is None:
                    continue

                key = (
                    match.group(1).lower(),
                    parsed.date().isoformat(),
                )
                if key in seen:
                    continue

                seen.add(key)
                matches.append(
                    {
                        "label": match.group(1),
                        "date": parsed.date().isoformat(),
                    }
                )

    return matches


def _relative_error(
    observed: float,
    expected: float,
) -> Optional[float]:
    if observed <= 0 or expected <= 0:
        return None

    return abs(observed - expected) / expected


def _best_observed_factor(
    discontinuity: Dict,
    official_factor: float,
) -> Tuple[Optional[float], Optional[str], Optional[float]]:
    candidates: List[Tuple[str, float]] = []

    for key in (
        "open_ratio",
        "close_ratio",
        "matched_common_ratio",
    ):
        value = discontinuity.get(key)
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue

        if numeric > 0:
            candidates.append((key, numeric))

    if not candidates:
        return None, None, None

    ranked = []
    for key, value in candidates:
        error = _relative_error(
            value,
            official_factor,
        )
        if error is None:
            continue
        ranked.append(
            (
                error,
                key,
                value,
            )
        )

    if not ranked:
        return None, None, None

    error, key, value = min(
        ranked,
        key=lambda item: item[0],
    )

    return value, key, error


def _boundary_datetime(
    discontinuity: Dict,
) -> Optional[datetime]:
    for key in (
        "current_ts",
        "previous_ts",
    ):
        parsed = _as_ist_datetime(
            discontinuity.get(key)
        )
        if parsed is not None:
            return parsed

    return None


def _date_distance_days(
    left: datetime,
    right: datetime,
) -> int:
    return abs(
        (left.date() - right.date()).days
    )


def _publication_compatible(
    publication: datetime,
    boundary: datetime,
    lookback_days: int,
    forward_days: int,
) -> bool:
    delta = (
        boundary.date()
        - publication.date()
    ).days

    return (
        -forward_days
        <= delta
        <= lookback_days
    )


def _official_action_candidates(
    event_profile: Dict,
) -> List[Dict]:
    """
    Event profile is already symbol-scoped by news_engine.py.

    Prefer the parsed `corporate_actions` list because it contains only events
    where a bonus/split-style factor was extracted. Do not infer a factor from
    dividends/rights/general filings.
    """
    actions = []

    for action in (
        event_profile.get(
            "corporate_actions",
            [],
        )
        or []
    ):
        source_id = str(
            action.get("source_id", "")
            or ""
        )

        if source_id not in OFFICIAL_NSE_SOURCE_IDS:
            continue

        try:
            factor = float(
                action.get(
                    "theoretical_price_factor"
                )
            )
        except (TypeError, ValueError):
            continue

        if factor <= 0:
            continue

        enriched = dict(action)
        enriched[
            "theoretical_price_factor"
        ] = factor
        enriched["action_dates"] = (
            extract_labeled_action_dates(
                action.get("title", ""),
                action.get("summary", ""),
            )
        )

        actions.append(enriched)

    return actions


def _score_candidate(
    discontinuity: Dict,
    action: Dict,
    *,
    factor_tolerance: float,
    action_date_tolerance_days: int,
    publication_lookback_days: int,
    publication_forward_days: int,
) -> Dict:
    boundary = _boundary_datetime(
        discontinuity
    )

    official_factor = float(
        action["theoretical_price_factor"]
    )

    observed_factor, observed_factor_field, factor_error = (
        _best_observed_factor(
            discontinuity,
            official_factor,
        )
    )

    factor_compatible = bool(
        factor_error is not None
        and factor_error <= factor_tolerance
    )

    labelled_dates = action.get(
        "action_dates",
        [],
    ) or []

    date_matches: List[Dict] = []

    if boundary is not None:
        for date_item in labelled_dates:
            parsed = _parse_date_text(
                date_item.get("date", "")
            )

            if parsed is None:
                continue

            distance = _date_distance_days(
                parsed,
                boundary,
            )

            date_matches.append(
                {
                    **date_item,
                    "distance_days": distance,
                    "compatible": (
                        distance
                        <= action_date_tolerance_days
                    ),
                }
            )

    labelled_date_compatible = any(
        item.get("compatible")
        for item in date_matches
    )

    publication = _as_ist_datetime(
        action.get("published_at")
    )

    publication_compatible = bool(
        boundary is not None
        and publication is not None
        and _publication_compatible(
            publication,
            boundary,
            lookback_days=publication_lookback_days,
            forward_days=publication_forward_days,
        )
    )

    source_id = str(
        action.get("source_id", "")
        or ""
    )

    strong_source = (
        source_id
        == STRONG_CORPORATE_ACTION_SOURCE_ID
    )

    if (
        factor_compatible
        and labelled_date_compatible
        and strong_source
    ):
        status = "CONFIRMED"
        confidence = "HIGH"
        reason = (
            "Official NSE Corporate Actions evidence has a compatible "
            "bonus/split factor and a labelled action date near the SMC "
            "price-discontinuity boundary."
        )
    elif (
        factor_compatible
        and labelled_date_compatible
    ):
        status = "PROBABLE"
        confidence = "MEDIUM"
        reason = (
            "Official NSE evidence has a compatible factor and labelled "
            "action date, but it was not sourced from the dedicated "
            "Corporate Actions feed."
        )
    elif (
        factor_compatible
        and publication_compatible
    ):
        status = "PROBABLE"
        confidence = "MEDIUM"
        reason = (
            "Official NSE evidence has a compatible factor and was published "
            "near the discontinuity, but no labelled record/ex/effective "
            "date was available for strong confirmation."
        )
    else:
        status = "NO_MATCH"
        confidence = "LOW"
        reason = (
            "The official action evidence did not satisfy both factor/date "
            "compatibility requirements."
        )

    score = 0

    if factor_compatible:
        score += 50

    if labelled_date_compatible:
        score += 35
    elif publication_compatible:
        score += 15

    if strong_source:
        score += 10

    if (
        factor_error is not None
        and factor_error <= 0.02
    ):
        score += 5

    return {
        "status": status,
        "confidence": confidence,
        "reason": reason,
        "score": score,
        "boundary_ts": (
            boundary.isoformat()
            if boundary is not None
            else None
        ),
        "observed_factor": observed_factor,
        "observed_factor_field": observed_factor_field,
        "official_factor": official_factor,
        "factor_relative_error": (
            round(factor_error, 6)
            if factor_error is not None
            else None
        ),
        "factor_difference_pct": (
            round(
                factor_error * 100.0,
                4,
            )
            if factor_error is not None
            else None
        ),
        "factor_compatible": factor_compatible,
        "labelled_action_dates": date_matches,
        "labelled_action_date_compatible": labelled_date_compatible,
        "publication_at": (
            publication.isoformat()
            if publication is not None
            else None
        ),
        "publication_compatible": publication_compatible,
        "source_id": source_id,
        "action_type": action.get(
            "action_type"
        ),
        "ratio": action.get("ratio"),
        "old_face_value": action.get(
            "old_face_value"
        ),
        "new_face_value": action.get(
            "new_face_value"
        ),
        "title": action.get(
            "title",
            "",
        ),
        "summary": action.get(
            "summary",
            "",
        ),
        "link": action.get(
            "link",
            "",
        ),
    }


def reconcile_corporate_action_guard(
    symbol: str,
    corporate_action_guard: Optional[Dict],
    event_profile: Optional[Dict],
    *,
    factor_tolerance: float = DEFAULT_FACTOR_RELATIVE_TOLERANCE,
    action_date_tolerance_days: int = DEFAULT_ACTION_DATE_TOLERANCE_DAYS,
    publication_lookback_days: int = DEFAULT_PUBLICATION_LOOKBACK_DAYS,
    publication_forward_days: int = DEFAULT_PUBLICATION_FORWARD_DAYS,
) -> Dict:
    """
    Cross-check SMC price discontinuities with symbol-scoped official NSE
    corporate-action evidence.

    This function NEVER changes historical candles and NEVER removes the SMC
    safety truncation. It only adds a confirmation layer.

    Important limitation:
    current NSE RSS feeds are recent/current feeds. An old discontinuity (for
    example RELIANCE in 2024) can remain UNVERIFIED when that historical action
    is no longer present in the current feed window. That must not be converted
    into "not a corporate action".
    """
    symbol = str(symbol or "").strip().upper()

    guard = (
        corporate_action_guard
        if isinstance(
            corporate_action_guard,
            dict,
        )
        else {}
    )

    profile = (
        event_profile
        if isinstance(
            event_profile,
            dict,
        )
        else {}
    )

    discontinuities = list(
        guard.get(
            "events",
            [],
        )
        or []
    )

    actions = _official_action_candidates(
        profile
    )

    base = {
        "symbol": symbol,
        "method": (
            "official_NSE_corporate_action_factor_date_crosscheck"
        ),
        "changes_price_history": False,
        "smc_guard_preserved": True,
        "factor_relative_tolerance": factor_tolerance,
        "action_date_tolerance_days": action_date_tolerance_days,
        "publication_lookback_days": publication_lookback_days,
        "publication_forward_days": publication_forward_days,
        "discontinuity_count": len(
            discontinuities
        ),
        "official_action_candidate_count": len(
            actions
        ),
        "source_available": bool(
            profile.get(
                "available",
                False,
            )
        ),
        "source_candidate_eligible": bool(
            profile.get(
                "candidate_eligible",
                False,
            )
        ),
        "feed_window_limitation": (
            "Current NSE RSS/event-profile evidence may not contain older "
            "historical corporate actions. NO_MATCH therefore means "
            "unverified from the currently available official feed evidence, "
            "not proof that no corporate action occurred."
        ),
    }

    if not discontinuities:
        return {
            **base,
            "status": "NO_DISCONTINUITY",
            "confirmed_count": 0,
            "probable_count": 0,
            "unverified_count": 0,
            "matches": [],
            "unverified_discontinuities": [],
        }

    if not actions:
        return {
            **base,
            "status": "UNVERIFIED_NO_OFFICIAL_MATCH_IN_CURRENT_FEED",
            "confirmed_count": 0,
            "probable_count": 0,
            "unverified_count": len(
                discontinuities
            ),
            "matches": [],
            "unverified_discontinuities": [
                {
                    "boundary_ts": item.get(
                        "current_ts"
                    ),
                    "open_ratio": item.get(
                        "open_ratio"
                    ),
                    "close_ratio": item.get(
                        "close_ratio"
                    ),
                    "matched_common_ratio": item.get(
                        "matched_common_ratio"
                    ),
                    "likely_corporate_action": item.get(
                        "likely_corporate_action"
                    ),
                    "reason": (
                        "No parsed bonus/split factor from symbol-matched "
                        "official NSE corporate-action evidence is available "
                        "in the current event profile."
                    ),
                }
                for item in discontinuities
            ],
        }

    matches: List[Dict] = []
    unverified: List[Dict] = []

    for discontinuity in discontinuities:
        candidates = [
            _score_candidate(
                discontinuity,
                action,
                factor_tolerance=factor_tolerance,
                action_date_tolerance_days=action_date_tolerance_days,
                publication_lookback_days=publication_lookback_days,
                publication_forward_days=publication_forward_days,
            )
            for action in actions
        ]

        candidates.sort(
            key=lambda item: (
                item.get("score", 0),
                -(
                    item.get(
                        "factor_relative_error"
                    )
                    if item.get(
                        "factor_relative_error"
                    )
                    is not None
                    else 999.0
                ),
            ),
            reverse=True,
        )

        best = candidates[0]

        if best["status"] in {
            "CONFIRMED",
            "PROBABLE",
        }:
            matches.append(best)
        else:
            unverified.append(
                {
                    "boundary_ts": (
                        _boundary_datetime(
                            discontinuity
                        ).isoformat()
                        if _boundary_datetime(
                            discontinuity
                        )
                        is not None
                        else None
                    ),
                    "open_ratio": discontinuity.get(
                        "open_ratio"
                    ),
                    "close_ratio": discontinuity.get(
                        "close_ratio"
                    ),
                    "matched_common_ratio": discontinuity.get(
                        "matched_common_ratio"
                    ),
                    "likely_corporate_action": discontinuity.get(
                        "likely_corporate_action"
                    ),
                    "best_official_candidate": best,
                    "reason": (
                        "Official NSE action evidence was inspected but did "
                        "not satisfy the confirmation thresholds."
                    ),
                }
            )

    confirmed_count = sum(
        item["status"] == "CONFIRMED"
        for item in matches
    )

    probable_count = sum(
        item["status"] == "PROBABLE"
        for item in matches
    )

    if confirmed_count:
        overall_status = "CONFIRMED"
    elif probable_count:
        overall_status = "PROBABLE"
    else:
        overall_status = "UNVERIFIED_NO_COMPATIBLE_MATCH"

    return {
        **base,
        "status": overall_status,
        "confirmed_count": confirmed_count,
        "probable_count": probable_count,
        "unverified_count": len(
            unverified
        ),
        "matches": matches,
        "unverified_discontinuities": unverified,
    }
