"""
StockTwits Data Collector — curl_cffi impersonation
=====================================================
Collects messages (twits) for one or more ticker symbols from StockTwits
using curl_cffi to impersonate a real Chrome browser, bypassing bot-detection.

Install:
    pip install curl-cffi pandas

Usage:
    python stocktwits_collector.py                    # defaults to AAPL
    python stocktwits_collector.py --symbols AAPL TSLA NVDA --limit 100
    python stocktwits_collector.py --symbols SPY --paginate --pages 5
"""

import argparse
import json
import time
import csv
from datetime import datetime
from pathlib import Path

from curl_cffi import requests  # pip install curl-cffi

# ── Configuration ─────────────────────────────────────────────────────────────

BASE_URL = "https://api.stocktwits.com/api/2"
IMPERSONATE = "chrome120"           # browser fingerprint to mimic
REQUEST_DELAY = 1.5                 # seconds between requests (be polite)
MAX_PER_PAGE = 30                   # StockTwits max is 30 per request

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://stocktwits.com/",
    "Origin": "https://stocktwits.com",
}

# ── Core fetcher ──────────────────────────────────────────────────────────────

def fetch_symbol_stream(
    symbol: str,
    limit: int = MAX_PER_PAGE,
    max_id: int | None = None,
    since_id: int | None = None,
) -> dict:
    """
    Fetch one page of StockTwits messages for a symbol.

    Pagination:
      - Pass `max_id` (ID of the oldest message from the previous page)
        to get *older* messages (scroll backwards in time).
      - Pass `since_id` (ID of the newest message you already have)
        to get *newer* messages (poll for updates).
    """
    url = f"{BASE_URL}/streams/symbol/{symbol.upper()}.json"
    params: dict = {"limit": min(limit, MAX_PER_PAGE)}
    if max_id:
        params["max"] = max_id
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
    return resp.json()


# ── Pagination helper ─────────────────────────────────────────────────────────

def collect_messages(
    symbol: str,
    total: int = 100,
    pages: int | None = None,
    verbose: bool = True,
) -> list[dict]:
    """
    Collect up to `total` messages for a symbol (paginating backwards in time).
    If `pages` is set, stops after that many pages regardless of `total`.
    """
    all_msgs: list[dict] = []
    max_id: int | None = None
    page = 0

    while True:
        page += 1
        if verbose:
            print(f"  [{symbol}] Fetching page {page} "
                  f"(max_id={max_id}, collected={len(all_msgs)})…")

        try:
            data = fetch_symbol_stream(symbol, limit=MAX_PER_PAGE, max_id=max_id)
        except Exception as e:
            print(f"  [{symbol}] Error on page {page}: {e}")
            break

        messages = data.get("messages", [])
        if not messages:
            if verbose:
                print(f"  [{symbol}] No more messages.")
            break

        all_msgs.extend(messages)

        # Pagination cursor — use the smallest ID as next max_id
        max_id = data.get("cursor", {}).get("max") or messages[-1]["id"] - 1

        # Stop conditions
        if pages and page >= pages:
            break
        if len(all_msgs) >= total:
            break

        time.sleep(REQUEST_DELAY)

    return all_msgs[:total]


# ── Parsing / flattening ──────────────────────────────────────────────────────

def parse_message(msg: dict, symbol: str) -> dict:
    """Flatten a raw StockTwits message dict into a clean row."""
    user = msg.get("user", {})
    entities = msg.get("entities", {})

    # Sentiment label (bullish / bearish / None)
    sentiment = None
    if msg.get("entities") and msg["entities"].get("sentiment"):
        sentiment = msg["entities"]["sentiment"].get("basic")

    # Symbols mentioned
    symbols_mentioned = [
        s.get("symbol") for s in entities.get("symbols", [])
    ]

    return {
        "id": msg.get("id"),
        "symbol": symbol,
        "created_at": msg.get("created_at"),
        "body": msg.get("body", "").replace("\n", " "),
        "sentiment": sentiment,
        "likes": msg.get("likes", {}).get("total", 0),
        "reshares": msg.get("reshares", {}).get("reshared_count", 0),
        "symbols_mentioned": "|".join(symbols_mentioned),
        "user_id": user.get("id"),
        "username": user.get("username"),
        "followers": user.get("followers"),
        "following": user.get("following"),
        "watchlist_count": user.get("watchlist_stocks_count"),
        "user_join_date": user.get("join_date"),
        "official": user.get("official"),
    }


# ── Output ────────────────────────────────────────────────────────────────────

def save_to_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        print("  No rows to save.")
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Saved {len(rows)} rows → {path}")


def save_to_jsonl(messages: list[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    print(f"  Saved {len(messages)} raw messages → {path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="StockTwits collector (curl_cffi)")
    parser.add_argument(
        "--symbols", nargs="+", default=["AAPL"],
        help="Ticker symbols to collect (default: AAPL)"
    )
    parser.add_argument(
        "--limit", type=int, default=90,
        help="Max messages per symbol (default: 90)"
    )
    parser.add_argument(
        "--paginate", action="store_true",
        help="Paginate backwards in time"
    )
    parser.add_argument(
        "--pages", type=int, default=None,
        help="Max pages when paginating (default: unlimited until --limit)"
    )
    parser.add_argument(
        "--outdir", default="data",
        help="Output directory (default: ./data)"
    )
    parser.add_argument(
        "--format", choices=["csv", "jsonl", "both"], default="both",
        help="Output format (default: both)"
    )
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    all_flat_rows: list[dict] = []

    for symbol in args.symbols:
        print(f"\n{'─'*50}")
        print(f"Collecting: {symbol.upper()}")

        if args.paginate or args.pages:
            raw_msgs = collect_messages(
                symbol, total=args.limit, pages=args.pages, verbose=True
            )
        else:
            # Single fetch (up to 30)
            data = fetch_symbol_stream(symbol, limit=min(args.limit, MAX_PER_PAGE))
            raw_msgs = data.get("messages", [])
            print(f"  [{symbol}] Fetched {len(raw_msgs)} messages.")

        # Save raw JSONL per symbol
        if args.format in ("jsonl", "both"):
            jsonl_path = outdir / f"{symbol.upper()}_{timestamp}_raw.jsonl"
            save_to_jsonl(raw_msgs, jsonl_path)

        # Flatten
        flat = [parse_message(m, symbol) for m in raw_msgs]
        all_flat_rows.extend(flat)

        time.sleep(REQUEST_DELAY)

    # Save combined CSV
    if args.format in ("csv", "both") and all_flat_rows:
        csv_path = outdir / f"stocktwits_{'_'.join(args.symbols)}_{timestamp}.csv"
        save_to_csv(all_flat_rows, csv_path)

    print(f"\n✓ Done. Total messages collected: {len(all_flat_rows)}")


if __name__ == "__main__":
    main()
