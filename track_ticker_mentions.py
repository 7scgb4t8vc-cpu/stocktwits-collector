"""
Ticker Mention Frequency Tracker
==================================
Reads the growing stocktwits.csv and produces a summary of:
- How many times each ticker was mentioned per run (by timestamp)
- Cumulative mention counts across all runs
- A rolling mention-frequency CSV for trend analysis

Output:
  data/ticker_mentions.csv   — one row per (timestamp, symbol) with counts
  data/ticker_summary.csv    — cumulative totals per symbol (refreshed each run)
"""

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

DATA_CSV       = Path("data/stocktwits.csv")
MENTIONS_CSV   = Path("data/ticker_mentions.csv")
SUMMARY_CSV    = Path("data/ticker_summary.csv")

MENTIONS_FIELDS = ["timestamp", "symbol", "mention_count", "bullish", "bearish", "neutral"]
SUMMARY_FIELDS  = ["symbol", "total_mentions", "total_bullish", "total_bearish",
                   "total_neutral", "bullish_pct", "bearish_pct", "first_seen", "last_seen"]

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_stocktwits() -> list[dict]:
    """Load all rows from the main stocktwits CSV."""
    if not DATA_CSV.exists():
        raise FileNotFoundError(f"{DATA_CSV} not found. Run the collector first.")
    with open(DATA_CSV, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_processed_timestamps() -> set:
    """Return already-processed (timestamp, symbol) pairs to avoid double-counting."""
    seen = set()
    if MENTIONS_CSV.exists():
        with open(MENTIONS_CSV, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                seen.add((row["timestamp"], row["symbol"]))
    return seen


# ── Core logic ────────────────────────────────────────────────────────────────

def compute_mention_rows(rows: list[dict], already_processed: set) -> list[dict]:
    """
    Aggregate raw messages into per-(timestamp, symbol) mention counts.
    Skips any (timestamp, symbol) pairs already written to mentions CSV.
    """
    # bucket: {(timestamp, symbol): {count, bullish, bearish, neutral}}
    buckets = defaultdict(lambda: {"mention_count": 0, "bullish": 0, "bearish": 0, "neutral": 0})

    for row in rows:
        key = (row["timestamp"], row["symbol"])
        b   = buckets[key]
        b["mention_count"] += 1

        sentiment = (row.get("sentiment") or "None").strip().lower()
        if sentiment == "bullish":
            b["bullish"] += 1
        elif sentiment == "bearish":
            b["bearish"] += 1
        else:
            b["neutral"] += 1

    # Filter out already-processed pairs
    new_rows = []
    for (ts, sym), counts in sorted(buckets.items()):
        if (ts, sym) not in already_processed:
            new_rows.append({
                "timestamp":     ts,
                "symbol":        sym,
                "mention_count": counts["mention_count"],
                "bullish":       counts["bullish"],
                "bearish":       counts["bearish"],
                "neutral":       counts["neutral"],
            })

    return new_rows


def append_mention_rows(new_rows: list[dict]):
    """Append new mention rows to ticker_mentions.csv."""
    if not new_rows:
        return
    MENTIONS_CSV.parent.mkdir(parents=True, exist_ok=True)
    write_header = not MENTIONS_CSV.exists()
    with open(MENTIONS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MENTIONS_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)


def rebuild_summary():
    """
    Rebuild ticker_summary.csv from scratch using all rows in ticker_mentions.csv.
    Called every run so summary always reflects full history.
    """
    if not MENTIONS_CSV.exists():
        return

    totals = defaultdict(lambda: {
        "total_mentions": 0, "total_bullish": 0, "total_bearish": 0,
        "total_neutral": 0, "first_seen": None, "last_seen": None,
    })

    with open(MENTIONS_CSV, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sym = row["symbol"]
            t   = totals[sym]
            t["total_mentions"] += int(row["mention_count"])
            t["total_bullish"]  += int(row["bullish"])
            t["total_bearish"]  += int(row["bearish"])
            t["total_neutral"]  += int(row["neutral"])
            ts = row["timestamp"]
            if t["first_seen"] is None or ts < t["first_seen"]:
                t["first_seen"] = ts
            if t["last_seen"] is None or ts > t["last_seen"]:
                t["last_seen"] = ts

    summary_rows = []
    for sym, t in sorted(totals.items(), key=lambda x: -x[1]["total_mentions"]):
        total = t["total_mentions"] or 1  # avoid div/0
        summary_rows.append({
            "symbol":         sym,
            "total_mentions": t["total_mentions"],
            "total_bullish":  t["total_bullish"],
            "total_bearish":  t["total_bearish"],
            "total_neutral":  t["total_neutral"],
            "bullish_pct":    round(t["total_bullish"] / total * 100, 1),
            "bearish_pct":    round(t["total_bearish"] / total * 100, 1),
            "first_seen":     t["first_seen"],
            "last_seen":      t["last_seen"],
        })

    with open(SUMMARY_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary_rows)

    return summary_rows


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'═'*50}")
    print(f"Ticker Mention Tracker — {timestamp}")
    print(f"{'═'*50}")

    # Load raw messages
    print("\nLoading stocktwits data...")
    rows = load_stocktwits()
    print(f"  {len(rows)} total messages loaded.")

    # Compute new mention rows (incremental)
    already_processed = load_processed_timestamps()
    new_rows = compute_mention_rows(rows, already_processed)

    if new_rows:
        append_mention_rows(new_rows)
        print(f"  {len(new_rows)} new (timestamp, symbol) pairs written to {MENTIONS_CSV}")
    else:
        print("  No new data to process.")

    # Rebuild summary
    summary = rebuild_summary()
    if summary:
        print(f"\n  Top 10 by mention count:")
        print(f"  {'Symbol':<8} {'Mentions':>8} {'Bullish%':>9} {'Bearish%':>9}")
        print(f"  {'-'*38}")
        for row in summary[:10]:
            print(f"  {row['symbol']:<8} {row['total_mentions']:>8} "
                  f"{row['bullish_pct']:>8.1f}% {row['bearish_pct']:>8.1f}%")

    print(f"\n✓ Done. Summary written to {SUMMARY_CSV}")


if __name__ == "__main__":
    main()
