"""
StockTwits Trending Collector
==============================
Each run:
1. Fetches top 10 trending stocks
2. For each stock, fetches only NEW messages since last run (since_id)
3. Caps at 10 new messages per stock
4. Appends one row per message to a single growing CSV
5. Updates frequency.csv with total mention counts per symbol

Runs in under 60 seconds.
"""

import json
import time
import csv
from datetime import datetime
from pathlib import Path

from curl_cffi import requests
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL      = "https://api.stocktwits.com/api/2"
IMPERSONATE   = "chrome120"
REQUEST_DELAY = 1.0
TOP_N_STOCKS  = 10
MAX_NEW_MSGS  = 10
DATA_CSV      = Path("data/stocktwits.csv")
FREQ_CSV      = Path("data/frequency.csv")
EXCEL_FILE    = Path("data/stocktwits_data.xlsx")
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


# ── Frequency tracking ───────────────────────────────────────────────────────

def update_frequency():
    """Read stocktwits.csv and recount total mentions per symbol."""
    if not DATA_CSV.exists():
        return

    # Count mentions per symbol from full CSV
    counts: dict = {}
    last_seen: dict = {}

    with open(DATA_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = row["symbol"]
            counts[symbol] = counts.get(symbol, 0) + 1
            last_seen[symbol] = row["timestamp"]

    # Write frequency CSV sorted by mention count
    FREQ_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(FREQ_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "mention_count", "last_seen"])
        writer.writeheader()
        for symbol, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
            writer.writerow({
                "symbol":        symbol,
                "mention_count": count,
                "last_seen":     last_seen[symbol],
            })

    print(f"  Updated {FREQ_CSV} — {len(counts)} symbols tracked.")


# ── Excel export ─────────────────────────────────────────────────────────────

def export_to_excel():
    """Write stocktwits.csv and frequency.csv into a formatted Excel workbook."""
    wb = openpyxl.Workbook()

    # ── Sheet 1: Raw Messages ──
    ws1 = wb.active
    ws1.title = "Raw Messages"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E79")

    if DATA_CSV.exists():
        with open(DATA_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        # Headers
        headers = ["timestamp", "symbol", "message", "sentiment"]
        for col, h in enumerate(headers, 1):
            cell = ws1.cell(row=1, column=col, value=h.upper())
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        # Data rows with sentiment color coding
        sentiment_colors = {
            "Bullish": "C6EFCE",   # green
            "Bearish": "FFC7CE",   # red
            "None":    "FFFFFF",   # white
        }

        for r, row in enumerate(rows, 2):
            sentiment = row.get("sentiment", "None")
            fill_color = sentiment_colors.get(sentiment, "FFFFFF")
            row_fill = PatternFill("solid", fgColor=fill_color)

            for col, key in enumerate(headers, 1):
                cell = ws1.cell(row=r, column=col, value=row.get(key, ""))
                cell.fill = row_fill

        # Column widths
        ws1.column_dimensions["A"].width = 22
        ws1.column_dimensions["B"].width = 10
        ws1.column_dimensions["C"].width = 80
        ws1.column_dimensions["D"].width = 12

    # ── Sheet 2: Frequency ──
    ws2 = wb.create_sheet("Ticker Frequency")

    if FREQ_CSV.exists():
        with open(FREQ_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            freq_rows = list(reader)

        freq_headers = ["symbol", "mention_count", "last_seen"]
        for col, h in enumerate(freq_headers, 1):
            cell = ws2.cell(row=1, column=col, value=h.upper())
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        for r, row in enumerate(freq_rows, 2):
            for col, key in enumerate(freq_headers, 1):
                ws2.cell(row=r, column=col, value=row.get(key, ""))

        ws2.column_dimensions["A"].width = 12
        ws2.column_dimensions["B"].width = 16
        ws2.column_dimensions["C"].width = 22

    EXCEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    wb.save(EXCEL_FILE)
    print(f"  Saved Excel workbook → {EXCEL_FILE}")


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

    # Step 4 — update frequency counts
    print("\nUpdating ticker mention frequency...")
    update_frequency()

    # Step 5 — export to Excel
    print("\nExporting to Excel...")
    export_to_excel()


if __name__ == "__main__":
    main()
