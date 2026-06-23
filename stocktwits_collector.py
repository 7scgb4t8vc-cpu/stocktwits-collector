"""
StockTwits + FinViz Elite Collector (MongoDB version)
=====================================
Each run:
1. Fetches pre-filtered stocks from FinViz Elite screener export (v=171 Technical view)
2. Cross-references against a fixed 26-stock watchlist
3. Collects StockTwits messages for those stocks
4. Saves messages + FinViz data to MongoDB

Runs in under 60 seconds.
"""

import time
import csv
import io
import os
from datetime import datetime
import pytz

from curl_cffi import requests as curl_requests
import requests as std_requests

from db import insert_messages, upsert_finviz, save_cursors, load_cursors, log_price

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL      = "https://api.stocktwits.com/api/2"
IMPERSONATE   = "chrome120"
REQUEST_DELAY = 1.0
TOP_N_KEEP    = 20
MAX_NEW_MSGS  = 10

WATCHLIST = [
    "HOOD", "QURE", "RXT", "ACON", "AIBZ", "ALBT", "ALOT", "ARDX",
    "BFLY", "CAT", "DIS", "HQ", "MRNA", "QS", "UUUU", "AAT", "ABCB",
    "ABG", "ABNB", "ABTC", "ABUS", "ACA", "ACH", "ACLO", "ACMR", "ACR"
]

FINVIZ_TECHNICAL_URL = "https://elite.finviz.com/export?v=171&f=sh_curvol_o100,sh_relvol_o2,ta_change_u&ft=4&auth={token}"

ST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://stocktwits.com/",
    "Origin":          "https://stocktwits.com",
}

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


# ── FinViz Elite screener ─────────────────────────────────────────────────────

def fetch_finviz_screener(token: str) -> list:
    url = FINVIZ_TECHNICAL_URL.format(token=token)
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
    """Map FinViz Elite CSV columns (v=171 Technical view) to our internal field names.
    No market cap / relative volume — not in this FinViz view, and yfinance was dropped."""
    return {
        "price":      row.get("Price",                        ""),
        "change_pct": row.get("Change",                       ""),
        "volume":     row.get("Volume",                       ""),
        "avg_volume": row.get("Average Volume",               ""),
        "rsi":        row.get("Relative Strength Index (14)", ""),
        "beta":       row.get("Beta",                         ""),
        "52w_high":   row.get("52-Week High",                 ""),
        "52w_low":    row.get("52-Week Low",                  ""),
    }


# ── StockTwits fetchers ───────────────────────────────────────────────────────

def fetch_trending() -> list:
    url  = f"{BASE_URL}/trending/symbols.json"
    resp = curl_requests.get(url, headers=ST_HEADERS, impersonate=IMPERSONATE, timeout=20)
    resp.raise_for_status()
    return resp.json().get("symbols", [])


def fetch_new_messages(symbol: str, since_id) -> list:
    url    = f"{BASE_URL}/streams/symbol/{symbol}.json"
    params = {"limit": MAX_NEW_MSGS}
    if since_id:
        params["since"] = since_id
    resp = curl_requests.get(url, params=params, headers=ST_HEADERS, impersonate=IMPERSONATE, timeout=20)
    resp.raise_for_status()
    return resp.json().get("messages", [])


def get_sentiment(msg: dict) -> str:
    entities = msg.get("entities", {})
    if entities.get("sentiment"):
        return entities["sentiment"].get("basic", "None") or "None"
    return "None"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    et        = pytz.timezone("America/New_York")
    timestamp = datetime.now(et).strftime("%Y-%m-%d %H:%M ET")
    print(f"\n{'='*55}")
    print(f"StockTwits + FinViz Elite Collector — {timestamp}")
    print(f"{'='*55}")

    finviz_token = os.environ.get("FINVIZ_API_TOKEN", "")
    if not finviz_token:
        print("✗ FINVIZ_API_TOKEN environment variable not set.")
        return

    cursors = load_cursors()

    print("\nFetching FinViz Elite screener results...")
    fv_screener_rows = fetch_finviz_screener(finviz_token)

    if not fv_screener_rows:
        print("  No results from FinViz screener — filters may be too strict or market is closed.")
        return

    print("\nFetching StockTwits trending stocks (for reference only)...")
    try:
        trending      = fetch_trending()
        trending_syms = {s["symbol"] for s in trending}
    except Exception as e:
        print(f"  Error fetching trending: {e}")
        trending_syms = set()

    fv_lookup = {}
    for row in fv_screener_rows:
        sym = row.get("Ticker", "").strip()
        if sym:
            fv_lookup[sym] = row

    candidates = [s for s in WATCHLIST if s in fv_lookup][:TOP_N_KEEP]
    skipped    = [s for s in WATCHLIST if s not in fv_lookup]

    print(f"  Watchlist: {WATCHLIST}")
    print(f"  Trending overlap: {[s for s in WATCHLIST if s in trending_syms]}")
    print(f"  Selected ({len(candidates)}): {', '.join(candidates)}")
    if skipped:
        print(f"  Skipped (not in today's FinViz filter results): {', '.join(skipped)}")

    st_rows = []
    fv_rows = []

    print("\nCollecting StockTwits messages...")
    for symbol in candidates:
        since_id = cursors.get(symbol)
        fv_raw   = fv_lookup[symbol]
        fv_data  = parse_finviz_row(fv_raw)

        print(f"  [{symbol}] Price={fv_data['price']} Chg={fv_data['change_pct']} "
              f"Vol={fv_data['volume']} RSI={fv_data['rsi']}")

        print(f"  [{symbol}] Fetching messages (since_id={since_id})...", end=" ")
        try:
            messages = fetch_new_messages(symbol, since_id)
        except Exception as e:
            print(f"✗ Error: {e}")
            messages = []

        if messages:
            cursors[symbol] = messages[0]["id"]
            for msg in messages:
                st_rows.append({
                    "_id":       msg["id"],
                    "timestamp": timestamp,
                    "symbol":    symbol,
                    "message":   msg.get("body", "").replace("\n", " ")[:280],
                    "sentiment": get_sentiment(msg),
                })
            print(f"{len(messages)} new messages.")
        else:
            print("No new messages.")

        fv_rows.append({"symbol": symbol, "timestamp": timestamp, **fv_data})
        log_price(symbol, timestamp, fv_data["price"], fv_data["change_pct"], fv_data["volume"])
        time.sleep(REQUEST_DELAY)

    if st_rows:
        insert_messages(st_rows)
        save_cursors(cursors)
        print(f"\n✓ {len(st_rows)} new StockTwits messages saved to MongoDB.")
    else:
        print("\n✓ No new StockTwits messages this run.")

    if fv_rows:
        upsert_finviz(fv_rows)
        print(f"✓ {len(fv_rows)} FinViz rows upserted to MongoDB.")

    print("\n✓ All done!")


if __name__ == "__main__":
    main()
