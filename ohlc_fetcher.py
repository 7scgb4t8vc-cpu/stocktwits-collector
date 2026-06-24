"""
Daily OHLC History Fetcher
============================
Pulls daily Open/High/Low/Close/Volume history per symbol from FinViz Elite's
quote_export endpoint and stores it in MongoDB. Run once per day (not hourly) —
daily candles don't change intraday.
"""

import os
import csv
import io
import time
import requests

from db import save_ohlc

WATCHLIST = [
    "HOOD", "QURE", "RXT", "ACON", "AIBZ", "ALBT", "ALOT", "ARDX",
    "BFLY", "CAT", "DIS", "HQ", "MRNA", "QS", "UUUU", "AAT", "ABCB",
    "ABG", "ABNB", "ABTC", "ABUS", "ACA", "ACH", "ACLO", "ACMR", "ACR"
]

QUOTE_EXPORT_URL = "https://elite.finviz.com/quote_export?t={symbol}&p=d&auth={token}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def fetch_ohlc(symbol: str, token: str) -> list:
    url = QUOTE_EXPORT_URL.format(symbol=symbol, token=token)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"  [{symbol}] HTTP {resp.status_code} — {resp.text[:200]}")
            return []
        reader = csv.DictReader(io.StringIO(resp.text), delimiter="\t")
        rows = []
        for r in reader:
            try:
                rows.append({
                    "date":   r["Date"],
                    "open":   float(r["Open"]),
                    "high":   float(r["High"]),
                    "low":    float(r["Low"]),
                    "close":  float(r["Close"]),
                    "volume": int(r["Volume"]),
                })
            except (KeyError, ValueError):
                continue
        if not rows:
            print(f"  [{symbol}] Parsed 0 rows. Raw response start: {resp.text[:200]!r}")
        return rows[-300:]
    except Exception as e:
        print(f"  [{symbol}] Error: {e}")
        return []


def main():
    token = os.environ.get("FINVIZ_API_TOKEN", "")
    if not token:
        print("✗ FINVIZ_API_TOKEN environment variable not set.")
        return

    print(f"\nFetching daily OHLC history for {len(WATCHLIST)} symbols...")
    for symbol in WATCHLIST:
        rows = fetch_ohlc(symbol, token)
        if rows:
            save_ohlc(symbol, rows)
            print(f"  [{symbol}] {len(rows)} days saved.")
        else:
            print(f"  [{symbol}] No data.")
        time.sleep(1.0)

    print("\n✓ OHLC history update complete!")


if __name__ == "__main__":
    main()
