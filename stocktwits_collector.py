"""
StockTwits + FinViz Elite Collector (MongoDB version)
=====================================
Each run:
1. On first run: fetches stocks from FinViz Elite screener, picks top 30 by volume, saves as permanent watchlist
2. On subsequent runs: uses saved watchlist from MongoDB
3. Collects StockTwits messages for watchlist stocks
4. Saves messages + FinViz data to MongoDB
"""

import re
import html as html_lib
import time
import csv
import io
import os
from datetime import datetime
import pytz

from curl_cffi import requests as curl_requests
import requests as std_requests

from db import insert_messages, upsert_finviz, save_cursors, load_cursors, log_price, get_db

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL      = "https://api.stocktwits.com/api/2"
IMPERSONATE   = "chrome120"
REQUEST_DELAY = 1.0
WATCHLIST_SIZE = 30

FINVIZ_URL = "https://elite.finviz.com/export?v=152&f=sh_avgvol_o10000,sh_curvol_o5000,sh_price_o20,ta_rsi_nos60,geo_usa,cap_midover&ft=4&auth={token}"
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


# ── Watchlist (MongoDB) ───────────────────────────────────────────────────────

def load_watchlist() -> list:
    """Load permanent watchlist from MongoDB. Returns list of symbols."""
    coll = get_db()["watchlist"]
    docs = list(coll.find())
    return [d["symbol"] for d in docs]

def save_watchlist(symbols: list):
    """Save permanent watchlist to MongoDB."""
    coll = get_db()["watchlist"]
    coll.drop()
    coll.insert_many([{"symbol": s} for s in symbols])
    print(f"  ✓ Saved {len(symbols)} symbols to permanent watchlist.")


# ── FinViz Elite screener ─────────────────────────────────────────────────────

def fetch_finviz_screener(token: str) -> list:
    url = FINVIZ_URL.format(token=token)
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
    print(f"  DEBUG {row.get('Ticker')} raw Market Cap: '{row.get('Market Cap')}'")
    def format_market_cap(raw):
        try:
            val = float(str(raw).replace(",", "").strip()) * 1_000_000
            if val >= 1_000_000_000_000:
                return f"{val / 1_000_000_000_000:.2f}T"
            if val >= 1_000_000_000:
                return f"{val / 1_000_000_000:.2f}B"
            if val >= 1_000_000:
                return f"{val / 1_000_000:.2f}M"
            return str(raw)
        except:
            return raw

    return {
        "symbol":     row.get("Ticker",          ""),
        "company":    row.get("Company",         ""),
        "price":      row.get("Price",           ""),
        "change_pct": row.get("Change",          ""),
        "volume":     row.get("Volume",          ""),
        "avg_volume": row.get("Average Volume",  ""),
        "market_cap": format_market_cap(row.get("Market Cap", "")),
        "rel_volume": row.get("Relative Volume", ""),
        "pe":         "",
    }


# ── StockTwits fetchers ───────────────────────────────────────────────────────

def fetch_trending() -> list:
    url  = f"{BASE_URL}/trending/symbols.json"
    resp = curl_requests.get(url, headers=ST_HEADERS, impersonate=IMPERSONATE, timeout=20)
    resp.raise_for_status()
    return resp.json().get("symbols", [])


def fetch_messages(symbol: str, since_id) -> list:
    url    = f"{BASE_URL}/streams/symbol/{symbol}.json"
    params = {"limit": 30}
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

    # ── Load or build watchlist ───────────────────────────────────────────────
    watchlist = load_watchlist()

    if not watchlist:
        print("\nNo permanent watchlist found. Running FinViz filter to build one...")
        fv_screener_rows = fetch_finviz_screener(finviz_token)
        if not fv_screener_rows:
            print("  No results from FinViz screener — cannot build watchlist.")
            return

        # Sort by volume descending, take top 30
        def parse_volume(row):
            try:
                return int(str(row.get("Volume", "0")).replace(",", ""))
            except:
                return 0

        fv_screener_rows.sort(key=parse_volume, reverse=True)
        top_rows = fv_screener_rows[:WATCHLIST_SIZE]
        watchlist = [r.get("Ticker", "").strip() for r in top_rows if r.get("Ticker", "").strip()]
        save_watchlist(watchlist)
        print(f"  Permanent watchlist: {watchlist}")

        # Build fv_lookup from these rows so we don't re-fetch
        fv_lookup = {r.get("Ticker", "").strip(): r for r in top_rows}
    else:
        print(f"\nLoaded permanent watchlist ({len(watchlist)} symbols): {watchlist}")

        # Fetch fresh FinViz data for watchlist symbols
        print("\nFetching fresh FinViz data...")
        fv_screener_rows = fetch_finviz_screener(finviz_token)
        fv_lookup = {r.get("Ticker", "").strip(): r for r in fv_screener_rows}

    # ── Collect data ──────────────────────────────────────────────────────────
    st_rows = []
    fv_rows = []

    print("\nCollecting StockTwits messages...")
    for symbol in watchlist:
        since_id = cursors.get(symbol)
        fv_raw   = fv_lookup.get(symbol)

        if fv_raw:
            fv_data = parse_finviz_row(fv_raw)
            print(f"  [{symbol}] Price={fv_data['price']} Chg={fv_data['change_pct']} "
                  f"Vol={fv_data['volume']} P/E={fv_data['pe']} Cap={fv_data['market_cap']}")
            fv_rows.append({"symbol": symbol, "timestamp": timestamp, **fv_data})
            log_price(symbol, timestamp, fv_data["price"], fv_data["change_pct"], fv_data["volume"])
        else:
            print(f"  [{symbol}] No FinViz data today (not in filter results).")

        print(f"  [{symbol}] Fetching messages (since_id={since_id})...", end=" ")
        try:
            messages = fetch_messages(symbol, since_id)
        except Exception as e:
            print(f"✗ Error: {e}")
            messages = []

        if messages:
            cursors[symbol] = messages[0]["id"]
            accepted = 0
            for msg in messages:
                body = msg.get("body", "").replace("\n", " ").strip()
                cleaned = clean_message(body)
                if not is_quality_message(cleaned):
                    continue
                st_rows.append({
                    "_id":       msg["id"],
                    "timestamp": timestamp,
                    "symbol":    symbol,
                    "message":   cleaned,
                    "sentiment": get_sentiment(msg),
                    "likes":     msg.get("likes", {}).get("total", 0),
                    "reshares":  msg.get("reshares", {}).get("reshared_count", 0),
                })
                accepted += 1
            print(f"{len(messages)} fetched, {accepted} passed filters.")
        else:
            print("No new messages.")

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


import re
import html as html_lib

PROFANITY = {
    "fuck", "shit", "ass", "bitch", "cunt", "dick", "cock",
    "pussy", "bastard", "piss", "crap", "damn", "fag", "slut"
}

def clean_message(text: str) -> str:
    """Clean raw StockTwits message text."""
    text = html_lib.unescape(text)
    text = re.sub(r"http\S+|www\.\S+", "", text)
    text = re.sub(r"@\w+", "", text)
    text = re.sub(r"\$[A-Z]{1,5}", "", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9\s\.\,\!\?\'\-]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:280]


def is_quality_message(text: str) -> bool:
    """Return True if message meets quality standards."""
    if not text:
        return False
    words = text.split()
    if len(words) < 4:
        return False
    non_ticker = re.sub(r"\$[A-Z]{1,5}", "", text).strip()
    if len(non_ticker) < 10:
        return False
    real_words = [w for w in words if re.match(r"^[a-zA-Z]{2,}$", w)]
    if len(real_words) < 2:
        return False
    lower_words = set(w.lower().strip(".,!?") for w in words)
    if lower_words & PROFANITY:
        return False
    return True


if __name__ == "__main__":
    main()
