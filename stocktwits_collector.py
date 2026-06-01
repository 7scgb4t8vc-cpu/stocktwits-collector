"""
StockTwits Trending Collector
==============================
Each run:
1. Fetches top 15 trending stocks
2. For each stock, fetches only NEW messages since last run (since_id)
3. Caps at 10 new messages per stock
4. Appends one row per message to a single growing CSV

Runs in under 60 seconds.
"""

import json
import time
import csv
from datetime import datetime
from pathlib import Path

from curl_cffi import requests

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL      = "https://api.stocktwits.com/api/2"
IMPERSONATE   = "chrome120"
REQUEST_DELAY = 1.5
TOP_N_STOCKS  = 15
MAX_NEW_MSGS  = 10
DATA_CSV      = Path("data/stocktwits.csv")
CURSOR_FILE   = Path("data/cursors.json")

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

CSV_FIELDS = ["timestamp", "symbol", "message", "sentiment"]

# ── Cursor (since_id) tracking ────────────────────────────────────────────────

def load_cursors() -> dict:
    """Load the last seen message ID per symbol."""
    if CURSOR_FILE.exists():
        with open(CURSOR_FILE, "r") as f:
            return json.load(f)
    return {}


def save_cursors(cursors: dict):
    """Save the last seen message ID per symbol."""
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CURSOR_FILE, "w") as f:
        json.dump(cursors, f, indent=2)


# ── Fetchers ──────────────────────────────────────────────────────────────────

def fetch_trending() -> list:
    """Get the current trending stocks on StockTwits."""
    url = f"{BASE_URL}/trending/symbols.json"
    resp = requests.get(url, headers=HEADERS, impersonate=IMPERSONATE, timeout=20)
    resp.raise_for_status()
    return resp.json().get("symbols", [])


def fetch_new_messages(symbol: str, since_id: int | None) -> list:
    """Fetch only new messages since the last run."""
    url = f"{BASE_URL}/streams/symbol/{symbol}.json"
    params = {"limit": MAX_NEW_MSGS}
    if since_id:
        params["since"] = since_id

    resp = requests.get(
        url,
        params=params,
        headers=HEADERS,
        impersonate=IMPERSONATE,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("messages", [])


# ── Parsing ───────────────────────────────────────────────────────────────────

def get_sentiment(msg: dict) -> str:
    """Extract sentiment label from a message."""
    entities = msg.get("entities", {})
    if entities.get("sentiment"):
        return entities["sentiment"].get("basic", "None") or "None"
    return "None"


# ── Output ────────────────────────────────────────────────────────────────────

def append_to_csv(rows: list[dict]):
    """Append rows to the growing CSV, creating headers if needed."""
    DATA_CSV.parent.mkdir(parents=True, exist_ok=True)
    write_header = not DATA_CSV.exists()

    with open(DATA_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'═'*50}")
    print(f"StockTwits Collector — {timestamp}")
    print(f"{'═'*50}")

    # Load cursors (last seen message IDs)
    cursors = load_cursors()

    # Step 1 — get trending stocks
    print("\nFetching trending stocks...")
    try:
        trending = fetch_trending()
    except Exception as e:
        print(f"  Error: {e}")
        return

    top_stocks = trending[:TOP_N_STOCKS]
    print(f"  Trending: {', '.join(s['symbol'] for s in top_stocks)}")

    all_rows = []

    # Step 2 — fetch new messages per stock
    for stock in top_stocks:
        symbol   = stock.get("symbol", "")
        since_id = cursors.get(symbol)

        print(f"\n  [{symbol}] Fetching new messages (since_id={since_id})...")

        try:
            messages = fetch_new_messages(symbol, since_id)
        except Exception as e:
            print(f"  [{symbol}] Error: {e}")
            continue

        if not messages:
            print(f"  [{symbol}] No new messages.")
            time.sleep(REQUEST_DELAY)
            continue

        # Update cursor to newest message ID
        cursors[symbol] = messages[0]["id"]

        # Build one row per message
        for msg in messages:
            all_rows.append({
                "timestamp": timestamp,
                "symbol":    symbol,
                "message":   msg.get("body", "").replace("\n", " ")[:280],
                "sentiment": get_sentiment(msg),
            })

        print(f"  [{symbol}] {len(messages)} new messages.")
        time.sleep(REQUEST_DELAY)

    # Step 3 — save
    if all_rows:
        append_to_csv(all_rows)
        save_cursors(cursors)
        print(f"\n✓ Done. {len(all_rows)} new messages appended to {DATA_CSV}")
    else:
        print("\n✓ Done. No new messages this run.")


if __name__ == "__main__":
    main()
