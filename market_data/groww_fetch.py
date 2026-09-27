# market_data/groww_fetch.py

import time
import pandas as pd
from datetime import datetime
from growwapi import GrowwAPI
from config import GROWW_EXCHANGE, GROWW_SEGMENT

def fetch_candles(groww: GrowwAPI, symbol: str, interval_min: int, days_back: int) -> pd.DataFrame:
    """
    Fetch historical candles for a symbol.
    interval_min: candle interval in minutes (e.g. 1440 for daily, 240 for 4H, 60 for 1H).
    days_back: how many days back to fetch.
    """
    end_ts = int(time.time() * 1000)  # current timestamp in milliseconds
    start_ts = end_ts - days_back * 24 * 3600 * 1000
    data = groww.get_historical_candle_data(
        trading_symbol=symbol,
        exchange=GROWW_EXCHANGE,
        segment=GROWW_SEGMENT,
        start_time=start_ts,
        end_time=end_ts,
        interval_in_minutes=interval_min
    )
    candles = data.get("candles", [])
    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit='s')  # convert to datetime
    return df

if __name__ == "__main__":
    # Example: fetch candles for RELIANCE
    groww = GrowwAPI(GrowwAPI.get_access_token(api_key="YOUR_KEY", secret="YOUR_SECRET"))
    daily = fetch_candles(groww, symbol="RELIANCE", interval_min=1440, days_back=365)
    fourh = fetch_candles(groww, symbol="RELIANCE", interval_min=240, days_back=90)
    oneh = fetch_candles(groww, symbol="RELIANCE", interval_min=60, days_back=30)
    print(f"Fetched {len(daily)} daily, {len(fourh)} 4H, {len(oneh)} 1H candles for RELIANCE.")
