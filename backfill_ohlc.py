"""
backfill_ohlc.py
Run once to backfill 60 days of 5-minute OHLC for all watchlist symbols.
Usage: python backfill_ohlc.py
"""

import os
import pytz
import yfinance as yf
from datetime import datetime, timedelta
from db import get_db, save_ohlc, get_watchlist

ET = pytz.timezone("America/New_York")

def backfill():
    watchlist = get_watchlist()
    print(f"Backfilling {len(watchlist)} symbols...")

    for symbol in watchlist:
        print(f"  [{symbol}] Fetching 5m OHLC...", end=" ")
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="60d", interval="5m")
            if df.empty:
                print("No data.")
                continue

            rows = []
            for ts, row in df.iterrows():
                # yfinance returns tz-aware timestamps — convert to ET so this
                # lines up with price_history, which is stored in ET.
                ts_et = ts.tz_convert(ET) if ts.tzinfo else ET.localize(ts)
                rows.append({
                    "date": ts_et.strftime("%Y-%m-%d %H:%M"),
                    "open":   round(float(row["Open"]), 4),
                    "high":   round(float(row["High"]), 4),
                    "low":    round(float(row["Low"]), 4),
                    "close":  round(float(row["Close"]), 4),
                    "volume": int(row["Volume"]),
                })

            save_ohlc(symbol, rows)
            print(f"{len(rows)} bars saved.")
        except Exception as e:
            print(f"Error: {e}")

    print("\nDone!")

if __name__ == "__main__":
    backfill()
