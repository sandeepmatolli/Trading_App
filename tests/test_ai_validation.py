from ai.ai_validation import evaluate_candidate


def _fundamentals():
    return {
        "roce": 18,
        "de_ratio": 0.4,
        "altman_z": 3.0,
        "piotroski_score": 8,
        "pledged_percentage": 0,
        "industry": "Engineering",
        "industry_group": "Industrials",
        "sector": "Industrials",
    }


def _bullish_technical_profile(
    candidate_eligible: bool = True,
    older_gap: bool = False,
):
    data_quality = {
        "valid": True,
        "candidate_eligible": candidate_eligible,
        "reasons": [],
        "warnings": [],
        "candidate_blockers": (
            []
            if candidate_eligible
            else ["75m source completeness is below candidate-grade."]
        ),
        "older_large_daily_gaps": [],
        "degraded_features": [],
    }

    if older_gap:
        data_quality["warnings"] = [
            "Older Daily history contains a 42-day gap outside "
            "the recent continuity window."
        ]
        data_quality["older_large_daily_gaps"] = [
            {
                "previous_daily": "2024-10-21 00:00:00+05:30",
                "next_daily": "2024-12-02 00:00:00+05:30",
                "gap_days": 42,
            }
        ]
        data_quality["degraded_features"] = [
            "older_historical_continuity"
        ]

    return {
        "data_quality": data_quality,
        "daily_trend": "Bullish",
        "daily_bos_bullish": True,
        "daily_choch_bullish": False,
        "daily_bos_bearish": False,
        "daily_choch_bearish": False,
        "setup_75m_bos_bullish": True,
        "setup_75m_choch_bullish": False,
        "setup_75m_liquidity_sweep": None,
    }


def test_bad_market_data_is_data_reject():
    result = evaluate_candidate(
        "EXAMPLE",
        _fundamentals(),
        {
            "data_quality": {
                "valid": False,
                "candidate_eligible": False,
                "reasons": ["Daily history too sparse."],
                "older_large_daily_gaps": [],
            }
        },
        {
            "available": True,
            "sentiment": "Neutral",
            "title": "",
        },
    )

    assert result["decision"] == "DATA_REJECT"
    assert result["candidate_eligibility_reason"]
    assert any(
        "Daily history too sparse" in item
        for item in result["candidate_eligibility_reason"]
    )


def test_missing_news_keeps_setup_on_watch():
    result = evaluate_candidate(
        "EXAMPLE",
        _fundamentals(),
        _bullish_technical_profile(candidate_eligible=True),
        {
            "available": False,
            "sentiment": "Unknown",
            "title": "",
        },
    )

    assert result["decision"] == "WATCH"
    assert any(
        "News/corporate-announcement" in item
        for item in result["missing_data"]
    )
    assert any(
        "unavailable" in item.lower()
        for item in result["candidate_eligibility_reason"]
    )


def test_degraded_market_data_cannot_become_candidate():
    result = evaluate_candidate(
        "EXAMPLE",
        _fundamentals(),
        _bullish_technical_profile(candidate_eligible=False),
        {
            "available": True,
            "sentiment": "Neutral",
            "title": "",
        },
    )

    assert result["decision"] == "WATCH"
    assert any(
        "Market-data candidate gate" in item
        for item in result["missing_data"]
    )
    assert any(
        "not candidate-grade" in item.lower()
        for item in result["candidate_eligibility_reason"]
    )


def test_complete_candidate_grade_evidence_can_be_candidate():
    result = evaluate_candidate(
        "EXAMPLE",
        _fundamentals(),
        _bullish_technical_profile(candidate_eligible=True),
        {
            "available": True,
            "sentiment": "Neutral",
            "title": "",
        },
    )

    assert result["decision"] == "CANDIDATE"
    assert result["candidate_eligibility_reason"]


def test_old_historical_gap_is_visible_but_does_not_by_itself_block_candidate():
    result = evaluate_candidate(
        "EXAMPLE",
        _fundamentals(),
        _bullish_technical_profile(
            candidate_eligible=True,
            older_gap=True,
        ),
        {
            "available": True,
            "sentiment": "Neutral",
            "title": "",
        },
    )

    assert result["decision"] == "CANDIDATE"
    assert result["historical_context_degraded"] is True
    assert result["historical_warnings"]
    assert any(
        "42 calendar days" in warning
        for warning in result["historical_warnings"]
    )


def test_old_historical_gap_remains_visible_when_news_is_missing():
    result = evaluate_candidate(
        "EXAMPLE",
        _fundamentals(),
        _bullish_technical_profile(
            candidate_eligible=True,
            older_gap=True,
        ),
        {
            "available": False,
            "sentiment": "Unknown",
            "title": "",
        },
    )

    assert result["decision"] == "WATCH"
    assert result["historical_context_degraded"] is True
    assert result["historical_warnings"]
    assert any(
        "News/corporate-announcement" in item
        for item in result["missing_data"]
    )
