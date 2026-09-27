# ai/ai_validation.py

def evaluate_candidate(symbol: str, fund_profile: dict, tech_profile: dict, event_profile: dict) -> dict:
    """
    Evaluate a stock by cross-validating fundamental, technical, and news profiles.
    Returns a dict with decision ('Buy', 'Hold', 'Sell') and rationale.
    """
    reasons = []
    decision = "Hold"
    
    # Fundamental checks
    if fund_profile.get("roce", 0) < 10 or fund_profile.get("de_ratio", 0) > 2:
        reasons.append("Weak fundamentals (ROCE<10% or D/E>2)")
    if fund_profile.get("altman_z", 0) < 1.8:
        reasons.append("Altman Z-Score low (distress sign)")
    if fund_profile.get("piotroski_score", 0) >= 7:
        reasons.append("High Piotroski Score (quality)")

    # Technical checks
    if tech_profile.get("daily_trend") == "Bullish":
        reasons.append("Daily uptrend")
    if tech_profile.get("daily_bos"):
        reasons.append("Daily BOS confirmed")
    if tech_profile.get("fourh_liq_sweep"):
        reasons.append("4H liquidity sweep detected")
    if tech_profile.get("fourh_order_block"):
        reasons.append(f"Order block at {tech_profile['fourh_order_block']}")
    if tech_profile.get("daily_FVG"):
        reasons.append(f"Fair Value Gap at {tech_profile['daily_FVG']}")

    # News checks
    if event_profile.get("sentiment") == "Negative":
        return {"decision": "Hold", "reason": f"Negative news: {event_profile.get('title', '')}"}
    if event_profile.get("sentiment") == "Positive":
        reasons.append("Positive news/announcement")

    # Decision logic
    if reasons:
        decision = "Buy" if tech_profile.get("daily_bos") or event_profile.get("sentiment") == "Positive" else "Hold"
    else:
        decision = "Hold"

    return {"decision": decision, "reasons": reasons}

if __name__ == "__main__":
    # Dummy profiles
    fund_pf = {"roce": 15, "de_ratio": 0.5, "altman_z": 3.0, "piotroski_score": 8}
    tech_pf = {"daily_trend": "Bullish", "daily_bos": True, 
               "fourh_liq_sweep": True, "fourh_order_block": (1700, 1730),
               "daily_FVG": (1680, 1690)}
    event_pf = {"sentiment": "Neutral", "title": ""}
    result = evaluate_candidate("SUNPHARMA", fund_pf, tech_pf, event_pf)
    print(result)
