"""
FinViz Backfill Script
=======================
One-time script to fetch FinViz data for any symbol that exists in
stocktwits.csv but is missing from finviz.csv.

Run once manually:
    python finviz_backfill.py
"""

import csv
import re
import time
from pathlib import Path

from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup
import yfinance as yf

# ── Config ────────────────────────────────────────────────────────────────────

DATA_CSV      = Path("data/stocktwits.csv")
FINVIZ_CSV    = Path("data/finviz.csv")
REQUEST_DELAY = 1.5

FINVIZ_FIELDS = [
    "timestamp", "symbol", "price", "change_pct", "volume", "avg_volume",
    "rel_volume", "market_cap", "rsi", "beta", "52w_high", "52w_low",
    "sector", "industry"
]

FV_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Helpers (copied from collector) ──────────────────────────────────────────

def parse_52w(value: str) -> str:
    if not value:
        return ""
    prices = re.findall(r"\d+\.\d+", value)
    if prices:
        return max(prices, key=lambda x: float(x))
    return value


def fetch_finviz(symbol: str) -> dict | None:
    url = f"https://finviz.com/quote.ashx?t={symbol}&p=d"
    try:
        resp = curl_requests.get(url, headers=FV_HEADERS, impersonate="chrome120", timeout=20)
        if resp.status_code != 200:
            return None
    except Exception:
        return None

    soup  = BeautifulSoup(resp.text, "html.parser")
    data  = {}
    table = soup.find("table", class_="snapshot-table2")
    if table:
        tds = table.find_all("td")
        for i in range(0, len(tds) - 1, 2):
            label = tds[i].get_text(strip=True)
            value = tds[i + 1].get_text(strip=True)
            data[label] = value

    if not data:
        return None

    return {
        "price":      data.get("Price",      ""),
        "change_pct": data.get("Change",     ""),
        "volume":     data.get("Volume",     ""),
        "avg_volume": data.get("Avg Volume", ""),
        "rel_volume": data.get("Rel Volume", ""),
        "market_cap": data.get("Market Cap", ""),
        "rsi":        data.get("RSI (14)",   ""),
        "beta":       data.get("Beta",       ""),
        "52w_high":   parse_52w(data.get("52W High", "")),
        "52w_low":    parse_52w(data.get("52W Low",  "")),
        "sector":     "",
        "industry":   "",
    }


def fetch_sector_industry(symbol: str) -> tuple:
    try:
        info     = yf.Ticker(symbol).info
        sector   = info.get("sector",   "")
        industry = info.get("industry", "")
        return sector, industry
    except Exception:
        return "", ""


def upsert_finviz_csv(new_rows: list, path: Path, fields: list):
    """Update existing rows by symbol, insert if new. One row per symbol."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing[row["symbol"]] = row
    for row in new_rows:
        existing[row["symbol"]] = row
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(existing.values())


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*55}")
    print("FinViz Backfill Script")
    print(f"{'='*55}")

    # Load all symbols from stocktwits.csv
    if not DATA_CSV.exists():
        print(f"✗ {DATA_CSV} not found.")
        return

    with open(DATA_CSV, "r", encoding="utf-8") as f:
        st_rows = list(csv.DictReader(f))

    all_symbols = sorted(set(r["symbol"] for r in st_rows))
    print(f"\n  Symbols in stocktwits.csv ({len(all_symbols)}): {', '.join(all_symbols)}")

    # Load symbols already in finviz.csv
    existing_symbols = set()
    if FINVIZ_CSV.exists():
        with open(FINVIZ_CSV, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing_symbols.add(row["symbol"])

    print(f"  Symbols already in finviz.csv ({len(existing_symbols)}): {', '.join(sorted(existing_symbols)) or 'none'}")

    # Find missing symbols
    missing = [s for s in all_symbols if s not in existing_symbols]
    if not missing:
        print("\n✓ No missing symbols — finviz.csv is already complete.")
        return

    print(f"\n  Missing symbols to backfill ({len(missing)}): {', '.join(missing)}")
    print(f"\nFetching FinViz data...")

    backfill_rows = []
    for symbol in missing:
        print(f"  [{symbol}]...", end=" ")
        fv_data = fetch_finviz(symbol)

        if not fv_data:
            print("✗ Not found on FinViz, skipping.")
            time.sleep(REQUEST_DELAY)
            continue

        # Use last seen timestamp from stocktwits.csv for this symbol
        last_ts = next(
            (r["timestamp"] for r in reversed(st_rows) if r["symbol"] == symbol),
            ""
        )

        sector, industry = fetch_sector_industry(symbol)
        fv_data["sector"]   = sector
        fv_data["industry"] = industry

        backfill_rows.append({
            "timestamp": last_ts,
            "symbol":    symbol,
            **fv_data,
        })

        print(f"✓ Price={fv_data['price']} MCap={fv_data['market_cap']} Sector={sector}")
        time.sleep(REQUEST_DELAY)

    if backfill_rows:
        upsert_finviz_csv(backfill_rows, FINVIZ_CSV, FINVIZ_FIELDS)
        print(f"\n✓ Backfilled {len(backfill_rows)} symbols into {FINVIZ_CSV}")
    else:
        print("\n✓ Nothing to backfill.")

    print("\n✓ Done!")


if __name__ == "__main__":
    main()
