# main.py

from fundamentals.csv_validator import validate_screener_csv
from fundamentals.master_builder import update_company_master
from fundamentals.screener_symbols import get_nse_symbols
from market_data.groww_auth import get_groww_api
from market_data.groww_fetch import fetch_candles
from technical.smc_engine import detect_higher_tf_trend, detect_bullish_BOS, detect_bullish_CHOCH, find_order_blocks, find_fair_value_gaps
from news.news_engine import fetch_nse_announcements, classify_event_text
from ai.ai_validation import evaluate_candidate
import pandas as pd

def main():
    # 1. Validate Screener CSV
    df_fund = validate_screener_csv("stocks_screen.csv")

    # 2. Update company master
    update_company_master(df_fund)
    
    # 3. Get list of NSE symbols
    symbols = get_nse_symbols("fundamentals_validated.csv")

    # 4. Initialize Groww API
    groww = get_groww_api()

    shortlist = []
    # 5. For each symbol, fetch data and analyze
    for symbol in symbols:
        try:
            # 5a. Fetch price data
            daily = fetch_candles(groww, symbol, interval_min=1440, days_back=365)
            fourh = fetch_candles(groww, symbol, interval_min=240, days_back=90)
            
            # 5b. Technical signals
            tech_profile = {
                "daily_trend": detect_higher_tf_trend(daily),
                "daily_bos": detect_bullish_BOS(daily),
                "fourh_bos": detect_bullish_BOS(fourh),
                "daily_choch": detect_bullish_CHOCH(daily),
                "order_blocks": find_order_blocks(daily),
                "fourh_liq_sweep": False,  # Placeholder: implement liquidity-sweep detection
                "fourh_order_block": None, # Example, could pick last OB zone
                "daily_FVG": find_fair_value_gaps(daily)
            }

            # 5c. Fundamental profile (placeholder values from df_fund)
            # We map Screener columns to fund_profile dict
            fund_row = df_fund[df_fund["NSE Code"] == symbol].iloc[0]
            fund_profile = {
                "roce": fund_row.get("Return on capital employed", 0),
                "de_ratio": fund_row.get("Debt to equity", 0),
                "altman_z": fund_row.get("Altman Z Score", 0),
                "piotroski_score": fund_row.get("Piotroski score", 0)
            }

            # 5d. News profile
            # Check if symbol has any recent announcements (simple example)
            announcements = fetch_nse_announcements()
            ev = {"sentiment": "Neutral", "title": ""}
            for ann in announcements:
                if ann["symbol"] == symbol:
                    typ, sent = classify_event_text(ann["title"])
                    ev = {"sentiment": sent, "title": ann["title"]}
                    break

            # 5e. Evaluate candidate
            result = evaluate_candidate(symbol, fund_profile, tech_profile, ev)
            if result["decision"] == "Buy":
                shortlist.append((symbol, result["reasons"]))

        except Exception as e:
            print(f"Failed processing {symbol}: {e}")

    # 6. Output shortlist
    print("Final Shortlist:")
    for sym, reasons in shortlist:
        print(f"{sym}: {'; '.join(reasons)}")

if __name__ == "__main__":
    main()
