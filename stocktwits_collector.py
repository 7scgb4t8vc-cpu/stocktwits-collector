"""
StockTwits + FinViz Collector
==============================
Each run:
1. Fetches top 15 trending stocks from StockTwits
2. Tests each against FinViz — keeps only stocks FinViz has data for
3. Takes the first 10 valid stocks
4. Collects StockTwits messages + FinViz data for those same 10 stocks
5. Updates frequency.csv with total mention counts per symbol
6. Exports everything to a formatted Excel workbook

Runs in under 60 seconds.
"""

import json
import time
import csv
from datetime import datetime
import pytz
from pathlib import Path

from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL      = "https://api.stocktwits.com/api/2"
IMPERSONATE   = "chrome120"
REQUEST_DELAY = 1.0
TOP_N_FETCH   = 20   # fetch this many trending stocks
TOP_N_KEEP    = 10   # keep this many after FinViz validation
MAX_NEW_MSGS  = 10
DATA_CSV      = Path("data/stocktwits.csv")
FREQ_CSV      = Path("data/frequency.csv")
FINVIZ_CSV    = Path("data/finviz.csv")
EXCEL_FILE    = Path("data/stocktwits_data.xlsx")
CURSOR_FILE   = Path("data/cursors.json")

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
}

CSV_FIELDS    = ["timestamp", "symbol", "message", "sentiment"]
FINVIZ_FIELDS = ["timestamp", "symbol", "price", "change_pct", "volume", "rel_volume", "market_cap"]

# ── Cursor tracking ───────────────────────────────────────────────────────────

def load_cursors() -> dict:
    if CURSOR_FILE.exists():
        with open(CURSOR_FILE, "r") as f:
            return json.load(f)
    return {}


def save_cursors(cursors: dict):
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CURSOR_FILE, "w") as f:
        json.dump(cursors, f, indent=2)


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


# ── FinViz fetcher ────────────────────────────────────────────────────────────

def fetch_finviz(symbol: str) -> dict | None:
    """
    Scrape key metrics for a symbol from FinViz.
    Returns None if the symbol is not found on FinViz.
    """
    url  = f"https://finviz.com/quote.ashx?t={symbol}&p=d"
    try:
        resp = curl_requests.get(url, headers=FV_HEADERS, impersonate="chrome120", timeout=20)
        if resp.status_code != 200:
            return None
    except Exception:
        return None

    soup  = BeautifulSoup(resp.text, "html.parser")

    # Build label->value dict from snapshot table
    data  = {}
    table = soup.find("table", class_="snapshot-table2")
    if table:
        tds = table.find_all("td")
        for i in range(0, len(tds) - 1, 2):
            label = tds[i].get_text(strip=True)
            value = tds[i + 1].get_text(strip=True)
            data[label] = value

    # If no data found, FinViz doesn't have this symbol
    if not data:
        return None

    return {
        "price":      data.get("Price",      ""),
        "change_pct": data.get("Change",     ""),
        "volume":     data.get("Volume",     ""),
        "rel_volume": data.get("Rel Volume", ""),
        "market_cap": data.get("Market Cap", ""),
    }


# ── Output helpers ────────────────────────────────────────────────────────────

def append_to_csv(rows: list, path: Path, fields: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


# ── Frequency tracking ────────────────────────────────────────────────────────

def update_frequency():
    if not DATA_CSV.exists():
        return

    counts    = {}
    last_seen = {}

    with open(DATA_CSV, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            s = row["symbol"]
            counts[s]    = counts.get(s, 0) + 1
            last_seen[s] = row["timestamp"]

    FREQ_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(FREQ_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "mention_count", "last_seen"])
        writer.writeheader()
        for s, c in sorted(counts.items(), key=lambda x: x[1], reverse=True):
            writer.writerow({"symbol": s, "mention_count": c, "last_seen": last_seen[s]})

    print(f"  Updated {FREQ_CSV} — {len(counts)} symbols tracked.")


# ── Excel export ──────────────────────────────────────────────────────────────

def export_to_excel():
    wb          = openpyxl.Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E79")

    def style_header(cell):
        cell.font      = header_font
        cell.fill      = header_fill
        cell.alignment = Alignment(horizontal="center")

    # ── Sheet 1: Raw Messages ──
    ws1 = wb.active
    ws1.title = "Raw Messages"
    sentiment_colors = {"Bullish": "C6EFCE", "Bearish": "FFC7CE", "None": "FFFFFF"}

    if DATA_CSV.exists():
        with open(DATA_CSV, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for col, h in enumerate(CSV_FIELDS, 1):
            style_header(ws1.cell(row=1, column=col, value=h.upper()))
        for r, row in enumerate(rows, 2):
            fill = PatternFill("solid", fgColor=sentiment_colors.get(row.get("sentiment", "None"), "FFFFFF"))
            for col, key in enumerate(CSV_FIELDS, 1):
                ws1.cell(row=r, column=col, value=row.get(key, "")).fill = fill
        ws1.column_dimensions["A"].width = 22
        ws1.column_dimensions["B"].width = 10
        ws1.column_dimensions["C"].width = 80
        ws1.column_dimensions["D"].width = 12

    # ── Sheet 2: Ticker Frequency ──
    ws2       = wb.create_sheet("Ticker Frequency")
    freq_cols = ["symbol", "mention_count", "last_seen"]

    if FREQ_CSV.exists():
        with open(FREQ_CSV, "r", encoding="utf-8") as f:
            freq_rows = list(csv.DictReader(f))
        for col, h in enumerate(freq_cols, 1):
            style_header(ws2.cell(row=1, column=col, value=h.upper()))
        for r, row in enumerate(freq_rows, 2):
            for col, key in enumerate(freq_cols, 1):
                ws2.cell(row=r, column=col, value=row.get(key, ""))
        ws2.column_dimensions["A"].width = 12
        ws2.column_dimensions["B"].width = 16
        ws2.column_dimensions["C"].width = 22

    # ── Sheet 3: FinViz Data ──
    ws3 = wb.create_sheet("FinViz Data")

    if FINVIZ_CSV.exists():
        with open(FINVIZ_CSV, "r", encoding="utf-8") as f:
            fv_rows = list(csv.DictReader(f))
        for col, h in enumerate(FINVIZ_FIELDS, 1):
            style_header(ws3.cell(row=1, column=col, value=h.upper()))
        for r, row in enumerate(fv_rows, 2):
            for col, key in enumerate(FINVIZ_FIELDS, 1):
                ws3.cell(row=r, column=col, value=row.get(key, ""))
        col_widths = [22, 10, 10, 12, 15, 12, 14, 20]
        for i, w in enumerate(col_widths, 1):
            ws3.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    EXCEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    wb.save(EXCEL_FILE)
    print(f"  Saved Excel workbook → {EXCEL_FILE}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    et = pytz.timezone("America/New_York")
timestamp = datetime.now(et).strftime("%Y-%m-%d %H:%M ET")
    print(f"\n{'='*55}")
    print(f"StockTwits + FinViz Collector — {timestamp}")
    print(f"{'='*55}")

    cursors = load_cursors()

    # Step 1 — get top 15 trending stocks
    print("\nFetching trending stocks...")
    try:
        trending = fetch_trending()
    except Exception as e:
        print(f"  Error fetching trending: {e}")
        return

    candidates = trending[:TOP_N_FETCH]
    print(f"  Candidates: {', '.join(s['symbol'] for s in candidates)}")

    # Step 2 — validate against FinViz, keep first 10 that have data
    print(f"\nValidating against FinViz (need {TOP_N_KEEP})...")
    valid_stocks = []
    fv_cache     = {}

    for stock in candidates:
        if len(valid_stocks) >= TOP_N_KEEP:
            break

        symbol = stock.get("symbol", "")
        print(f"  Checking {symbol}...", end=" ")
        fv_data = fetch_finviz(symbol)

        if fv_data and fv_data.get("market_cap"):
            print(f"✓ Price={fv_data['price']} MarketCap={fv_data['market_cap']}")
            valid_stocks.append(stock)
            fv_cache[symbol] = fv_data
        elif fv_data and not fv_data.get("market_cap"):
            print("✗ ETF or no market cap, skipping.")
        else:
            print("✗ Not on FinViz, skipping.")

        time.sleep(REQUEST_DELAY)

    print(f"\n  Valid stocks ({len(valid_stocks)}): {', '.join(s['symbol'] for s in valid_stocks)}")

    st_rows = []
    fv_rows = []

    # Step 3 — collect StockTwits messages for valid stocks
    print("\nCollecting StockTwits messages...")
    for stock in valid_stocks:
        symbol   = stock.get("symbol", "")
        since_id = cursors.get(symbol)

        print(f"  [{symbol}] Fetching messages (since_id={since_id})...")
        try:
            messages = fetch_new_messages(symbol, since_id)
        except Exception as e:
            print(f"  [{symbol}] Error: {e}")
            messages = []

        if messages:
            cursors[symbol] = messages[0]["id"]
            for msg in messages:
                st_rows.append({
                    "timestamp": timestamp,
                    "symbol":    symbol,
                    "message":   msg.get("body", "").replace("\n", " ")[:280],
                    "sentiment": get_sentiment(msg),
                })
            print(f"  [{symbol}] {len(messages)} new messages.")
        else:
            print(f"  [{symbol}] No new messages.")

        # Step 4 — add FinViz row (already fetched, use cache)
        fv = fv_cache[symbol]
        fv_rows.append({"timestamp": timestamp, "symbol": symbol, **fv})

        time.sleep(REQUEST_DELAY)

    # Save all data
    if st_rows:
        append_to_csv(st_rows, DATA_CSV, CSV_FIELDS)
        save_cursors(cursors)
        print(f"\n✓ {len(st_rows)} new StockTwits messages appended.")
    else:
        print("\n✓ No new StockTwits messages this run.")

    if fv_rows:
        append_to_csv(fv_rows, FINVIZ_CSV, FINVIZ_FIELDS)
        print(f"✓ {len(fv_rows)} FinViz rows appended.")

    # Update frequency + export Excel
    print("\nUpdating ticker mention frequency...")
    update_frequency()

    print("\nExporting to Excel...")
    export_to_excel()

    print("\n✓ All done!")


if __name__ == "__main__":
    main()
