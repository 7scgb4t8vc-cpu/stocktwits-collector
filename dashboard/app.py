"""
StockTwits Dashboard — Flask App
=================================
Reads from MongoDB and serves the live dashboard.
"""

import os
import requests
import pytz
import csv
import io
import re

FINVIZ_COLUMNS = "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,73,75,76,77,78,79,80,81,82,83,84,85,86,87,88"

FINVIZ_TICKER_URL = (
    "https://elite.finviz.com/export?v=111"
    "&t={tickers}&c={columns}&auth={token}"
)

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

def fetch_finviz_by_tickers(symbols, token):
    if not symbols:
        return []
    tickers = ",".join(symbols)
    url = FINVIZ_TICKER_URL.format(tickers=tickers, columns=FINVIZ_COLUMNS, token=token)
    try:
        resp = requests.get(url, headers=FV_HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"  Error: HTTP {resp.status_code} — {resp.text[:200]}")
            return []
        return list(csv.DictReader(io.StringIO(resp.text)))
    except Exception as e:
        print(f"  Error fetching FinViz by tickers: {e}")
        return []

def parse_finviz_row(row):
    parsed = {}
    for key, val in row.items():
        if not key:
            continue
        field = re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
        parsed[field] = val
    return parsed
ET = pytz.timezone("America/New_York")
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, jsonify, request

from db import get_db, get_messages, get_finviz, get_price_history, get_ohlc, add_to_watchlist, remove_from_watchlist, get_blocked_symbols

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

GITHUB_USER   = "7scgb4t8vc-cpu"
GITHUB_REPO   = "stocktwits-collector"
GITHUB_BRANCH = "main"

# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_timestamp(ts_str: str):
    """Parse timestamp strings like '2026-06-29 14:32 ET' into a UTC-naive datetime."""
    if not ts_str:
        return None
    ts_str = ts_str.strip()
    for fmt in ("%Y-%m-%d %H:%M ET", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(ts_str[:16], fmt[:len(fmt)])
        except ValueError:
            continue
    try:
        return datetime.strptime(ts_str[:16], "%Y-%m-%d %H:%M")
    except Exception:
        return None


def cutoff_from_hours(hours: float):
    """Return a naive datetime representing now - hours."""
    return datetime.utcnow() - timedelta(hours=hours)


TIMEFRAME_HOURS = {
    "5m":   5 / 60,
    "15m":  15 / 60,
    "30m":  30 / 60,
    "1h":   1,
    "2h":   2,
    "4h":   4,
    "6h":   6,
    "12h":  12,
    "1d":   24,
    "7d":   24 * 7,
    "30d":  24 * 30,
}
BUCKET_MINUTES = {
    "5m": 1, "15m": 1, "30m": 1,
    "1h": 5, "2h": 5,
    "4h": 15, "6h": 15,
    "12h": 30, "1d": 30,
    "7d": 60, "30d": 240,
}

def round_to_bucket(dt: datetime, bucket_minutes: int) -> str:
    """Round a datetime down to the nearest bucket and return a label string."""
    discard = dt.minute % bucket_minutes
    dt = dt - timedelta(minutes=discard, seconds=dt.second, microseconds=dt.microsecond)
    return dt.strftime("%Y-%m-%d %H:%M")

# ── Watchlist ─────────────────────────────────────────────────────────────────

def get_watchlist() -> set:
    docs = list(get_db()["watchlist"].find())
    return {d["symbol"] for d in docs}

# ── Data loaders ──────────────────────────────────────────────────────────────

def load_social():
    active = set(get_active_symbols())
    rows = get_messages()
    rows = [r for r in rows if r.get("symbol", "") in active]
    rows.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    result = []
    for row in rows:
        result.append({
            "timestamp":  row.get("timestamp", ""),
            "symbol":     row.get("symbol", ""),
            "message":    row.get("message", ""),
            "sentiment":  row.get("sentiment", "None"),
            "nlp_label":  row.get("nlp_label", ""),
            "nlp_score":  row.get("nlp_score", ""),
            "likes":      row.get("likes", 0),
            "reshares":   row.get("reshares", 0),
        })
    return result


def load_screener():
    # Full FinViz universe — no longer restricted to the watchlist
    return get_finviz()


def load_frequency():
    watchlist = get_watchlist()
    rows = get_messages()
    counts = {}
    last_seen = {}
    for row in rows:
        sym = row.get("symbol", "")
        if sym not in watchlist:
            continue
        counts[sym] = counts.get(sym, 0) + 1
        ts = row.get("timestamp", "")
        if sym not in last_seen or ts > last_seen[sym]:
            last_seen[sym] = ts

    result = [
        {"symbol": sym, "mention_count": c, "last_seen": last_seen.get(sym, "")}
        for sym, c in counts.items()
    ]
    return sorted(result, key=lambda r: r["mention_count"], reverse=True)


def load_charts_data():
    watchlist = get_watchlist()
    rows = get_messages()
    rows = [r for r in rows if r.get("symbol", "") in watchlist]

    sentiment_by_symbol = {}
    counts_over_time = {}
    for row in rows:
        symbol = row.get("symbol", "")
        label  = row.get("nlp_label", "neutral") or "neutral"
        if symbol not in sentiment_by_symbol:
            sentiment_by_symbol[symbol] = {"bullish": 0, "bearish": 0, "neutral": 0, "mixed": 0}
        if label in sentiment_by_symbol[symbol]:
            sentiment_by_symbol[symbol][label] += 1

        ts = row.get("timestamp", "")[:16]
        counts_over_time[ts] = counts_over_time.get(ts, 0) + 1

    return {
        "sentiment_by_symbol": sentiment_by_symbol,
        "counts_over_time":    sorted(counts_over_time.items()),
    }

def load_raw_price_ticks(symbol: str, cutoff: datetime):
    """Merge backfilled 5m OHLC (deep history) with live FinViz price snapshots
    (recent, overrides on overlap) into one continuous chronological tick list."""
    ticks = {}

    for r in get_ohlc(symbol, limit_days=100000):
        try:
            dt = datetime.strptime(r["date"], "%Y-%m-%d %H:%M")
        except Exception:
            continue
        if dt >= cutoff:
            ticks[dt] = r["close"]

    for r in get_price_history(symbol):
        dt = parse_timestamp(r.get("timestamp", ""))
        if not dt or dt < cutoff:
            continue
        try:
            price = float(str(r.get("price", "")).replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        ticks[dt] = price

    ordered = sorted(ticks.items())
    return [{"timestamp": dt.strftime("%Y-%m-%d %H:%M"), "price": p} for dt, p in ordered]

def load_symbol_chart_data(symbol: str, timeframe: str = "1d", end: datetime = None):
    symbol = symbol.upper()
    hours  = TIMEFRAME_HOURS.get(timeframe, 24)
    window_end = end if end else datetime.utcnow()
    cutoff = window_end - timedelta(hours=hours)
    bucket_minutes = BUCKET_MINUTES.get(timeframe, 30)

    rows = get_messages(symbol=symbol)

    filtered = []
    for row in rows:
        raw_dt = row.get("created_at") or row.get("timestamp", "")
        if raw_dt.endswith("Z"):
            dt = datetime.strptime(raw_dt, "%Y-%m-%dT%H:%M:%SZ")
        else:
            dt = parse_timestamp(raw_dt)
        if dt and cutoff <= dt <= window_end:
            filtered.append({**row, "_bucket": round_to_bucket(dt, bucket_minutes)})

    volume_by_ts = {}
    sentiment_by_ts = {}
    for row in filtered:
        ts = row["_bucket"]
        volume_by_ts[ts] = volume_by_ts.get(ts, 0) + 1

        label = (row.get("nlp_label") or "neutral").lower()
        if label not in ("bullish", "bearish", "neutral", "mixed"):
            label = "neutral"

        if ts not in sentiment_by_ts:
            sentiment_by_ts[ts] = {"bullish": 0, "bearish": 0, "neutral": 0, "mixed": 0}
        sentiment_by_ts[ts][label] += 1

    # Generate all buckets across the full window so x-axis spans the full timeframe
    discard = int(cutoff.minute % bucket_minutes)
    bucket_start = cutoff - timedelta(minutes=discard, seconds=cutoff.second, microseconds=cutoff.microsecond)
    all_buckets = []
    bucket_dt = bucket_start
    while bucket_dt <= window_end:
        all_buckets.append(bucket_dt.strftime("%Y-%m-%d %H:%M"))
        bucket_dt += timedelta(minutes=bucket_minutes)
    timestamps = sorted(all_buckets)

    price_rows = get_price_history(symbol)
    price_series = []
    for r in price_rows:
        dt = parse_timestamp(r.get("timestamp", ""))
        if dt and dt >= cutoff:
            try:
                price_val = float(str(r.get("price", "0")).replace(",", "").strip())
            except Exception:
                price_val = None
            price_series.append({
                "timestamp": round_to_bucket(dt, bucket_minutes),
                "price": price_val,
            })

    price_by_ts = {p["timestamp"]: p["price"] for p in price_series}

    correlation_series = [
        {
            "timestamp": ts,
            "price":     price_by_ts.get(ts),
            "msg_count": volume_by_ts.get(ts, 0),
        }
        for ts in timestamps
    ]

    return {
        "symbol":             symbol,
        "timeframe":          timeframe,
        "price_series":       price_series,
        "volume_series":      [{"timestamp": ts, "count": volume_by_ts.get(ts, 0)} for ts in timestamps],
        "sentiment_series":   [
            {"timestamp": ts, **sentiment_by_ts.get(ts, {"bullish": 0, "bearish": 0, "neutral": 0, "mixed": 0})}
            for ts in timestamps
        ],
        "correlation_series": correlation_series,
    }


def compute_sma(closes: list, period: int) -> list:
    result = []
    for i in range(len(closes)):
        if i + 1 < period:
            result.append(None)
        else:
            window = closes[i + 1 - period:i + 1]
            result.append(round(sum(window) / period, 2))
    return result


def bucket_key(ts_str: str, interval: str):
    dt = parse_timestamp(ts_str)
    if not dt:
        return None
    if interval == "5m":
        discard = dt.minute % 5
        dt = dt - timedelta(minutes=discard, seconds=dt.second, microseconds=dt.microsecond)
        return dt.strftime("%Y-%m-%d %H:%M")
    if interval == "1h":
        return dt.strftime("%Y-%m-%d %H:00")
    if interval == "1w":
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return dt.strftime("%Y-%m-%d")  # "1d"


def load_ohlc_data(symbol: str, interval: str = "1d"):
    symbol = symbol.upper()
    rows = get_price_history(symbol)

    buckets = {}
    for r in rows:
        key = bucket_key(r.get("timestamp", ""), interval)
        if not key:
            continue
        try:
            price = float(str(r.get("price", "")).replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        try:
            vol = int(str(r.get("volume", "0")).replace(",", "").strip())
        except (TypeError, ValueError):
            vol = 0
        if key not in buckets:
            buckets[key] = {"date": key, "open": price, "high": price, "low": price, "close": price, "volume": vol}
        else:
            b = buckets[key]
            b["high"]   = max(b["high"], price)
            b["low"]    = min(b["low"], price)
            b["close"]  = price
            b["volume"] = vol

    ordered = [buckets[k] for k in sorted(buckets.keys())]
    if not ordered:
        return {"symbol": symbol, "candles": []}

    closes = [r["close"] for r in ordered]
    sma50  = compute_sma(closes, 50)
    sma200 = compute_sma(closes, 200)

    candles = [{**r, "sma50": sma50[i], "sma200": sma200[i]} for i, r in enumerate(ordered)]
    return {"symbol": symbol, "candles": candles}


def load_momentum():
    watchlist = get_watchlist()
    rows = get_finviz()
    rows = [r for r in rows if r.get("symbol", "") in watchlist]

    def parse_float(v):
        try:
            return float(str(v).replace("%", "").replace(",", "").strip())
        except Exception:
            return 0.0

    for row in rows:
        row["_rel_vol"] = parse_float(row.get("relative_volume", "0"))
        row["_change"]  = parse_float(str(row.get("change", "0")).replace("%", ""))

    return sorted(rows, key=lambda r: r["_rel_vol"], reverse=True)


def load_sentiment_scores():
    watchlist = get_watchlist()
    coll = get_db()["sentiment_scores"]
    rows = list(coll.find())
    for r in rows:
        r.pop("_id", None)
    return {r["symbol"]: r for r in rows if r.get("symbol", "") in watchlist}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", page="home")


@app.route("/social")
def social():
    return render_template("social.html", page="social")


@app.route("/screener")
def screener():
    return render_template("screener.html", page="screener")


@app.route("/charts")
def charts():
    return render_template("charts.html", page="charts")


@app.route("/momentum")
def momentum():
    return render_template("momentum.html", page="momentum")

@app.route("/news")
def news():
    return render_template("news.html", page="news")


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route("/api/social")
def api_social():
    symbol = request.args.get("symbol", "").upper()
    label  = request.args.get("label", "")
    rows   = load_social()
    if symbol:
        rows = [r for r in rows if r["symbol"] == symbol]
    if label:
        rows = [r for r in rows if r["sentiment"].lower() == label.lower()
                or r["nlp_label"].lower() == label.lower()]
    return jsonify(rows)


@app.route("/api/screener")
def api_screener():
    return jsonify(load_screener())


@app.route("/api/watchlist/add", methods=["POST"])
def api_watchlist_add():
    symbol = (request.get_json(silent=True) or {}).get("symbol", "").upper().strip()
    if not symbol:
        return jsonify({"error": "No symbol provided"}), 400
    add_to_watchlist(symbol)
    return jsonify({"status": "added", "symbol": symbol})


@app.route("/api/watchlist/remove", methods=["POST"])
def api_watchlist_remove():
    symbol = (request.get_json(silent=True) or {}).get("symbol", "").upper().strip()
    if not symbol:
        return jsonify({"error": "No symbol provided"}), 400
    remove_from_watchlist(symbol)
    return jsonify({"status": "removed", "symbol": symbol})


@app.route("/api/frequency")
def api_frequency():
    return jsonify(load_frequency())


@app.route("/api/symbols")
def api_symbols():
    return jsonify(sorted(get_watchlist()))


@app.route("/api/charts")
def api_charts():
    return jsonify(load_charts_data())


@app.route("/api/charts/<symbol>")
def api_charts_symbol(symbol):
    if symbol.upper() not in get_watchlist():
        return jsonify({"error": "Symbol not tracked"}), 404
    timeframe = request.args.get("tf", "1d")
    end_param = request.args.get("end")
    end_dt = None
    if end_param:
        try:
            end_dt = datetime.strptime(end_param, "%Y-%m-%dT%H:%M")
        except Exception:
            end_dt = None
    debug = request.args.get("debug")
    if debug:
        rows = get_messages(symbol=symbol.upper())
        rows.sort(key=lambda r: r.get("created_at", ""))
        sample = [{"created_at": r.get("created_at"), "timestamp": r.get("timestamp")} for r in rows[-5:]]
        return jsonify({"sample": sample, "now_utc": datetime.utcnow().isoformat(), "total": len(rows)})
    return jsonify(load_symbol_chart_data(symbol, timeframe, end_dt))

@app.route("/api/charts/<symbol>/full")
def api_charts_symbol_full(symbol):
    symbol = symbol.upper()
    if not get_finviz(symbol):
        return jsonify({"error": "Unknown symbol"}), 404

    cutoff = datetime.utcnow() - timedelta(days=30)
    rows = get_messages(symbol=symbol)

    messages = []
    for row in rows:
        raw_dt = row.get("created_at") or row.get("timestamp", "")
        if raw_dt.endswith("Z"):
            dt = datetime.strptime(raw_dt, "%Y-%m-%dT%H:%M:%SZ")
        else:
            dt = parse_timestamp(raw_dt)
        if dt and dt >= cutoff:
            label = (row.get("nlp_label") or "neutral").lower()
            if label not in ("bullish", "bearish", "neutral", "mixed"):
                label = "neutral"
            messages.append({"created_at": dt.strftime("%Y-%m-%d %H:%M"), "nlp_label": label})

    return jsonify({
        "symbol": symbol,
        "messages": messages,
        "price_ticks": load_raw_price_ticks(symbol, cutoff),
    })
@app.route("/api/ohlc/<symbol>")
def api_ohlc(symbol):
    if symbol.upper() not in get_watchlist():
        return jsonify({"error": "Symbol not tracked"}), 404
    interval = request.args.get("interval", "1d")
    return jsonify(load_ohlc_data(symbol, interval))


@app.route("/api/momentum")
def api_momentum():
    return jsonify(load_momentum())


@app.route("/api/sentiment-scores")
def api_sentiment_scores():
    return jsonify(load_sentiment_scores())


@app.route("/api/trigger-refresh", methods=["POST"])
def trigger_refresh():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return jsonify({"error": "No token configured"}), 500

    resp = requests.post(
        f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/actions/workflows/collect.yml/dispatches",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        json={"ref": GITHUB_BRANCH},
        timeout=10,
    )
    if resp.status_code == 204:
        return jsonify({"status": "triggered"})
    return jsonify({"error": resp.text}), resp.status_code
@app.route("/api/watchlist/sync", methods=["POST"])
def sync_watchlist():
    symbols = request.json.get("symbols", [])
    coll = get_db()["watchlist"]
    coll.delete_many({})
    if symbols:
        coll.insert_many([{"symbol": s} for s in symbols])
    return {"status": "ok", "count": len(symbols)}

from db import set_active_symbols

@app.route("/api/active-symbols", methods=["POST"])
def api_set_active_symbols():
    data = request.get_json(silent=True) or {}
    symbols = data.get("symbols", [])
    symbols = [s.strip().upper() for s in symbols if isinstance(s, str) and s.strip()]
    set_active_symbols(symbols)
    return jsonify({"status": "ok", "count": len(symbols)})
import threading
import time
import uuid
from db import get_active_symbols, log_price_tick, try_acquire_poller_lock

_POLLER_WORKER_ID = str(uuid.uuid4())

def _price_poller_loop():
    finviz_token = os.environ.get("FINVIZ_API_TOKEN", "")
    if not finviz_token:
        print("Poller: FINVIZ_API_TOKEN not set, skipping.")
        return
    while True:
        try:
            if try_acquire_poller_lock(_POLLER_WORKER_ID):
                symbols = get_active_symbols()
                if symbols:
                    rows = fetch_finviz_by_tickers(symbols, finviz_token)
                    now_et = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
                    for raw in rows:
                        parsed = parse_finviz_row(raw)
                        sym = parsed.get("ticker", "").strip().upper()
                        price = parsed.get("price")
                        if sym and price:
                            log_price_tick(sym, now_et, price)
                    print(f"Poller: logged {len(rows)} ticks for {symbols}")
        except Exception as e:
            print(f"Poller error: {e}")
        time.sleep(60)

threading.Thread(target=_price_poller_loop, daemon=True).start()

@app.route("/api/debug-social")
def debug_social():
    watchlist = get_watchlist()
    all_rows = get_messages()
    matching = [r for r in all_rows if r.get("symbol", "") in watchlist]
    return jsonify({
        "watchlist_size": len(watchlist),
        "aibz_in_watchlist": "AIBZ" in watchlist,
        "total_messages": len(all_rows),
        "matching_messages": len(matching),
        "sample_watchlist": list(watchlist)[:5],
        "sample_message_symbols": [r.get("symbol") for r in all_rows[:5]],
@app.route("/api/blocked-symbols")
def api_blocked_symbols():
    return jsonify(get_blocked_symbols())
    })
