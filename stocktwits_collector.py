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

from db import insert_messages, upsert_finviz, save_cursors, load_cursors, log_price, get_db, save_ohlc, get_price_history, get_active_symbols

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL      = "https://api.stocktwits.com/api/2"
IMPERSONATE   = "chrome120"
REQUEST_DELAY = 1.0
WATCHLIST_SIZE = 30

FINVIZ_COLUMNS = "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,73,75,76,77,78,79,80,81,82,83,84,85,86,87,88"

FINVIZ_URL = (
    "https://elite.finviz.com/export?v=152"
    "&f=geo_usa"
    "&ft=4&c={columns}&auth={token}"
)
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
FINVIZ_TICKER_URL = (
    "https://elite.finviz.com/export?v=111"
    "&t={tickers}&c={columns}&auth={token}"
)

def fetch_finviz_by_tickers(symbols: list, token: str) -> list:
    """Ticker-filtered FinViz export for the minute-level poller.
    v=111 (Overview) is required to get Price; much lighter than the
    full screener export since it's capped to ~50 symbols."""
    if not symbols:
        return []
    tickers = ",".join(symbols)
    url = FINVIZ_TICKER_URL.format(tickers=tickers, columns=FINVIZ_COLUMNS, token=token)
    try:
        resp = std_requests.get(url, headers=FV_HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"  Error: HTTP {resp.status_code} — {resp.text[:200]}")
            return []
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        return rows
    except Exception as e:
        print(f"  Error fetching FinViz by tickers: {e}")
        return []

def parse_finviz_row(row: dict) -> dict:
    """Dynamically convert every FinViz column into a clean field name.
    e.g. 'P/E' -> 'p_e', 'Market Cap' -> 'market_cap', 'RSI (14)' -> 'rsi_14'
    """
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


# ── StockTwits fetchers ───────────────────────────────────────────────────────

def fetch_trending() -> list:
    url  = f"{BASE_URL}/trending/symbols.json"
    resp = curl_requests.get(url, headers=ST_HEADERS, impersonate=IMPERSONATE, timeout=20)
    resp.raise_for_status()
    return resp.json().get("symbols", [])


def fetch_messages(symbol: str, since_id=None) -> list:
    """Always fetch the newest messages, regardless of backlog. Older skipped messages are accepted as lost."""
    url    = f"{BASE_URL}/streams/symbol/{symbol}.json"
    params = {"limit": 30}
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

    # ── Currently filtered/active symbols (from the Screener/News page) ──────
    watchlist = get_active_symbols()
    print(f"\nCurrent active symbols ({len(watchlist)} symbols): {watchlist}")

    # ── Always fetch the full FinViz screener universe (not just watchlist) ──
    print("\nFetching FinViz screener universe...")
    fv_screener_rows = fetch_finviz_screener(finviz_token)
    fv_lookup = {r.get("Ticker", "").strip(): r for r in fv_screener_rows}

    fv_rows = []
    for symbol, fv_raw in fv_lookup.items():
        if not symbol:
            continue
        fv_data = parse_finviz_row(fv_raw)
        fv_rows.append({"symbol": symbol, "timestamp": timestamp, **fv_data})
        log_price(symbol, timestamp, fv_data.get("price"), fv_data.get("change"), fv_data.get("volume"))

    if fv_rows:
        upsert_finviz(fv_rows)
        print(f"✓ {len(fv_rows)} FinViz rows upserted (full screener universe).")
    else:
        print("  No results from FinViz screener this run.")

    # ── Collect StockTwits messages only for user-selected watchlist stocks ──
    st_rows = []

    if not watchlist:
        print("\nNo stocks in your watchlist yet — add some from the Screener page.")
    else:
        print("\nCollecting StockTwits messages for watchlist stocks...")
        for symbol in watchlist:
            since_id = cursors.get(symbol)

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
                        "_id":        msg["id"],
                        "timestamp":  timestamp,
                        "created_at": msg.get("created_at", timestamp),
                        "symbol":     symbol,
                        "message":    cleaned,
                        "sentiment":  get_sentiment(msg),
                        "likes":      msg.get("likes", {}).get("total", 0),
                        "reshares":   msg.get("reshares", {}).get("reshared_count", 0),
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

    # Update today's OHLC candle from FinViz price snapshots already stored
    # via log_price() — no external market-data API needed.
    print("\nUpdating OHLC from FinViz price history...")
    today = timestamp[:10]  # "YYYY-MM-DD"
    for symbol in watchlist:
        try:
            history = get_price_history(symbol)
            today_prices = []
            today_vol = 0
            for r in history:
                if not r.get("timestamp", "").startswith(today):
                    continue
                try:
                    today_prices.append(float(str(r.get("price", "")).replace(",", "").strip()))
                except (TypeError, ValueError):
                    continue
                try:
                    today_vol = int(str(r.get("volume", "0")).replace(",", "").strip())
                except (TypeError, ValueError):
                    pass

            if not today_prices:
                print(f"  [{symbol}] No price data today, skipped.")
                continue

            save_ohlc(symbol, [{
                "date":   today,
                "open":   today_prices[0],
                "high":   max(today_prices),
                "low":    min(today_prices),
                "close":  today_prices[-1],
                "volume": today_vol,
            }])
            print(f"  [{symbol}] OHLC updated ({len(today_prices)} ticks today).")
        except Exception as e:
            print(f"  [{symbol}] Error: {e}")

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
