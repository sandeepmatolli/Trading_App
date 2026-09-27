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


def _bullish_technical_profile(candidate_eligible: bool = True):
    return {
        "data_quality": {
            "valid": True,
            "candidate_eligible": candidate_eligible,
            "reasons": [],
            "candidate_blockers": (
                []
                if candidate_eligible
                else ["75m source completeness is below candidate-grade."]
            ),
        },
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
            }
        },
        {
            "available": True,
            "sentiment": "Neutral",
            "title": "",
        },
    )

    assert result["decision"] == "DATA_REJECT"


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