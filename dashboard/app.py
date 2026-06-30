"""
StockTwits Dashboard — Flask App
=================================
Reads from MongoDB and serves the live dashboard.
"""

import os
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, jsonify, request

from db import get_db, get_messages, get_finviz, get_price_history, get_ohlc

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

# ── Watchlist ─────────────────────────────────────────────────────────────────

def get_watchlist() -> set:
    docs = list(get_db()["watchlist"].find())
    return {d["symbol"] for d in docs}

# ── Data loaders ──────────────────────────────────────────────────────────────

def load_social():
    watchlist = get_watchlist()
    rows = get_messages()
    rows = [r for r in rows if r.get("symbol", "") in watchlist]
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
    watchlist = get_watchlist()
    rows = get_finviz()
    return [r for r in rows if r.get("symbol", "") in watchlist]


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


def load_symbol_chart_data(symbol: str, timeframe: str = "1d"):
    symbol = symbol.upper()
    hours  = TIMEFRAME_HOURS.get(timeframe, 24)
    cutoff = cutoff_from_hours(hours)

    rows = get_messages(symbol=symbol)

    filtered = []
    for row in rows:
        raw_dt = row.get("created_at") or row.get("timestamp", "")
        if raw_dt.endswith("Z"):
            dt = datetime.strptime(raw_dt, "%Y-%m-%dT%H:%M:%SZ")
        else:
            dt = parse_timestamp(raw_dt)
        if dt and dt >= cutoff:
            filtered.append({**row, "_bucket": raw_dt[:16]})

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

    timestamps = sorted(set(volume_by_ts.keys()) | set(sentiment_by_ts.keys()))

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
                "timestamp": r.get("timestamp", "")[:16],
                "price": price_val,
            })

    all_ts = sorted(set([p["timestamp"] for p in price_series]) | set(volume_by_ts.keys()))
    price_by_ts = {p["timestamp"]: p["price"] for p in price_series}

    correlation_series = [
        {
            "timestamp": ts,
            "price":     price_by_ts.get(ts),
            "msg_count": volume_by_ts.get(ts, 0),
        }
        for ts in all_ts
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
            "timestamp": ts,
            "price":     price_by_ts.get(ts),
            "msg_count": volume_by_ts.get(ts, 0),
        }
        for ts in all_ts
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
            "timestamp": ts,
            "price":     price_by_ts.get(ts),
            "msg_count": volume_by_ts.get(ts, 0),
        }
        for ts in all_ts
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


def load_ohlc_data(symbol: str):
    symbol = symbol.upper()
    rows = get_ohlc(symbol)
    if not rows:
        return {"symbol": symbol, "candles": []}

    closes = [r["close"] for r in rows]
    sma50  = compute_sma(closes, 50)
    sma200 = compute_sma(closes, 200)

    candles = [
        {
            "date":   r["date"],
            "open":   r["open"],
            "high":   r["high"],
            "low":    r["low"],
            "close":  r["close"],
            "volume": r["volume"],
            "sma50":  sma50[i],
            "sma200": sma200[i],
        }
        for i, r in enumerate(rows)
    ]

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
        row["_rel_vol"] = parse_float(row.get("rel_volume", "0"))
        row["_change"]  = parse_float(str(row.get("change_pct", "0")).replace("%", ""))

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


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route("/api/social")
def api_social():
    watchlist = get_watchlist()
    symbol = request.args.get("symbol", "").upper()
    label  = request.args.get("label", "")
    rows   = load_social()
    if symbol and symbol in watchlist:
        rows = [r for r in rows if r["symbol"] == symbol]
    if label:
        rows = [r for r in rows if r["sentiment"].lower() == label.lower()
                or r["nlp_label"].lower() == label.lower()]
    return jsonify(rows)


@app.route("/api/screener")
def api_screener():
    return jsonify(load_screener())


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
    return jsonify(load_symbol_chart_data(symbol, timeframe))


@app.route("/api/ohlc/<symbol>")
def api_ohlc(symbol):
    if symbol.upper() not in get_watchlist():
        return jsonify({"error": "Symbol not tracked"}), 404
    return jsonify(load_ohlc_data(symbol))


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

