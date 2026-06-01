"""
StockTwits Trending Collector
==============================
Every run:
1. Hits the StockTwits trending endpoint
2. Grabs top 15 trending stocks
3. Fetches one page of messages per stock
4. Calculates sentiment score + message density
5. Appends one row per stock to summary CSV (with sample message text)

Runs in under 60 seconds.
"""

import json
import time
import csv
from datetime import datetime
from pathlib import Path

from curl_cffi import requests

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL       = "https://api.stocktwits.com/api/2"
IMPERSONATE    = "chrome120"
REQUEST_DELAY  = 1.5
TOP_N_STOCKS   = 15
SUMMARY_CSV    = Path("data/summary.csv")
RAW_JSONL      = Path("data/raw.jsonl")

HEADERS = {
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

SUMMARY_FIELDS = [
    "timestamp",
    "symbol",
    "company_name",
    "message_density",
    "bullish_count",
    "bearish_count",
    "neutral_count",
    "sentiment_score",
    "top_message_1",
    "top_message_2",
    "top_message_3",
    "top_user_1",
    "top_user_2",
    "top_user_3",
    "top_likes_1",
    "top_likes_2",
    "top_likes_3",
]

# ── Fetchers ──────────────────────────────────────────────────────────────────

def fetch_trending():
    """Get the current trending stocks on StockTwits."""
    url = f"{BASE_URL}/trending/symbols.json"
    resp = requests.get(url, headers=HEADERS, impersonate=IMPERSONATE, timeout=20)
    resp.raise_for_status()
    return resp.json().get("symbols", [])


def fetch_messages(symbol):
    """Fetch one page (up to 30) of messages for a symbol."""
    url = f"{BASE_URL}/streams/symbol/{symbol}.json"
    resp = requests.get(
        url,
        params={"limit": 30},
        headers=HEADERS,
        impersonate=IMPERSONATE,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("messages", [])


# ── Processing ────────────────────────────────────────────────────────────────

def calculate_sentiment(messages):
    """Count bullish, bearish, neutral from message sentiment tags."""
    bullish = 0
    bearish = 0
    neutral = 0

    for msg in messages:
        sentiment = None
        entities = msg.get("entities", {})
        if entities.get("sentiment"):
            sentiment = entities["sentiment"].get("basic")

        if sentiment == "Bullish":
            bullish += 1
        elif sentiment == "Bearish":
            bearish += 1
        else:
            neutral += 1

    total_tagged = bullish + bearish
    score = round(bullish / total_tagged, 2) if total_tagged > 0 else None

    return bullish, bearish, neutral, score


def get_top_messages(messages, n=3):
    """Get the top N messages sorted by likes."""
    sorted_msgs = sorted(
        messages,
        key=lambda m: m.get("likes", {}).get("total", 0),
        reverse=True
    )
    top = sorted_msgs[:n]

    bodies = []
    users  = []
    likes  = []

    for msg in top:
        bodies.append(msg.get("body", "").replace("\n", " ")[:200])
        users.append(msg.get("user", {}).get("username", ""))
        likes.append(msg.get("likes", {}).get("total", 0))

    # Pad to always return n items
    while len(bodies) < n:
        bodies.append("")
        users.append("")
        likes.append("")

    return bodies, users, likes


# ── Output ────────────────────────────────────────────────────────────────────

def append_to_csv(rows):
    """Append rows to the summary CSV, creating it with headers if needed."""
    SUMMARY_CSV.parent.mkdir(parents=True, exist_ok=True)
    write_header = not SUMMARY_CSV.exists()

    with open(SUMMARY_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)

    print(f"  Appended {len(rows)} rows to {SUMMARY_CSV}")


def append_to_jsonl(messages, symbol):
    """Append raw messages to the running JSONL file."""
    RAW_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(RAW_JSONL, "a", encoding="utf-8") as f:
        for msg in messages:
            msg["_symbol"] = symbol
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'═'*55}")
    print(f"StockTwits Trending Collector — {timestamp}")
    print(f"{'═'*55}")

    # Step 1 — get trending stocks
    print("\nFetching trending stocks...")
    try:
        trending = fetch_trending()
    except Exception as e:
        print(f"  Error fetching trending: {e}")
        return

    top_stocks = trending[:TOP_N_STOCKS]
    print(f"  Got {len(top_stocks)} trending stocks: "
          f"{', '.join(s['symbol'] for s in top_stocks)}")

    summary_rows = []

    # Step 2 — fetch messages for each stock
    for stock in top_stocks:
        symbol       = stock.get("symbol", "")
        company_name = stock.get("title", "")

        print(f"\n  [{symbol}] Fetching messages...")

        try:
            messages = fetch_messages(symbol)
        except Exception as e:
            print(f"  [{symbol}] Error: {e}")
            continue

        # Calculations
        bullish, bearish, neutral, score = calculate_sentiment(messages)
        bodies, users, likes             = get_top_messages(messages, n=3)
        density                          = len(messages)

        print(f"  [{symbol}] {density} messages | "
              f"Bullish: {bullish} Bearish: {bearish} "
              f"Score: {score}")

        # Build summary row
        row = {
            "timestamp":      timestamp,
            "symbol":         symbol,
            "company_name":   company_name,
            "message_density": density,
            "bullish_count":  bullish,
            "bearish_count":  bearish,
            "neutral_count":  neutral,
            "sentiment_score": score,
            "top_message_1":  bodies[0],
            "top_message_2":  bodies[1],
            "top_message_3":  bodies[2],
            "top_user_1":     users[0],
            "top_user_2":     users[1],
            "top_user_3":     users[2],
            "top_likes_1":    likes[0],
            "top_likes_2":    likes[1],
            "top_likes_3":    likes[2],
        }
        summary_rows.append(row)

        # Save raw messages
        append_to_jsonl(messages, symbol)

        time.sleep(REQUEST_DELAY)

    # Step 3 — save summary
    print(f"\n{'─'*55}")
    append_to_csv(summary_rows)
    print(f"\n✓ Done. {len(summary_rows)} stocks processed.")


if __name__ == "__main__":
    main()
