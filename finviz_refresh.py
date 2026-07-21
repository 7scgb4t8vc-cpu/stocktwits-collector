"""
FinViz Elite Screener Refresh
==============================
Refreshes the full FinViz universe (~10k stocks) on a slower cadence
than the main StockTwits collector, since this step is the heaviest
Mongo write and doesn't need to run every 5 minutes.
"""

import os
import re
import csv
import io
from datetime import datetime
import pytz
import requests as std_requests

from db import upsert_finviz, log_prices_bulk

FINVIZ_COLUMNS = "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,73,75,76,77,78,79,80,81,82,83,84,85,86,87,88"

FINVIZ_URL = (
    "https://elite.finviz.com/export?v=152"
    "&f=geo_usa,ind_stocksonly"
    "&ft=4&c={columns}&auth={token}"
)

FV_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://elite.finviz.com/",
}


def fetch_finviz_screener(token: str) -> list:
    url = FINVIZ_URL.format(columns=FINVIZ_COLUMNS, token=token)
    try:
        resp = std_requests.get(url, headers=FV_HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"  Error: HTTP {resp.status_code} — {resp.text[:200]}")
            return []
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        print(f"  {len(rows)} stocks passed FinViz filters.")
        return rows
    except Exception as e:
        print(f"  Error fetching FinViz screener: {e}")
        return []


def parse_finviz_row(row: dict) -> dict:
    parsed = {}
    for key, val in row.items():
        if not key:
            continue
        field = re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
        parsed[field] = val

    if "market_cap" in parsed:
        try:
            val = float(str(parsed["market_cap"]).replace(",", "").strip()) * 1_000_000
            if val >= 1_000_000_000_000:
                parsed["market_cap"] = f"{val / 1_000_000_000_000:.2f}T"
            elif val >= 1_000_000_000:
                parsed["market_cap"] = f"{val / 1_000_000_000:.2f}B"
            elif val >= 1_000_000:
                parsed["market_cap"] = f"{val / 1_000_000:.2f}M"
        except:
            pass

    return parsed


def main():
    et = pytz.timezone("America/New_York")
    timestamp = datetime.now(et).strftime("%Y-%m-%d %H:%M ET")
    print(f"\n{'='*55}")
    print(f"FinViz Screener Refresh — {timestamp}")
    print(f"{'='*55}")

    finviz_token = os.environ.get("FINVIZ_API_TOKEN", "")
    if not finviz_token:
        print("✗ FINVIZ_API_TOKEN environment variable not set.")
        return

    print("\nFetching FinViz screener universe...")
    fv_screener_rows = fetch_finviz_screener(finviz_token)
    fv_lookup = {r.get("Ticker", "").strip(): r for r in fv_screener_rows}

    fv_rows = []
    price_rows = []
    for symbol, fv_raw in fv_lookup.items():
        if not symbol:
            continue
        fv_data = parse_finviz_row(fv_raw)
        fv_rows.append({"symbol": symbol, "timestamp": timestamp, **fv_data})
        price_rows.append({
            "symbol": symbol,
            "timestamp": timestamp,
            "price": fv_data.get("price"),
            "change_pct": fv_data.get("change"),
            "volume": fv_data.get("volume"),
        })

    log_prices_bulk(price_rows)

    if fv_rows:
        upsert_finviz(fv_rows)
        print(f"✓ {len(fv_rows)} FinViz rows upserted (full screener universe).")
    else:
        print("  No results from FinViz screener this run.")

    print("\n✓ Done!")


if __name__ == "__main__":
    main()
