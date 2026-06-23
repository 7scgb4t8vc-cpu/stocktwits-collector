"""
StockTwits Dashboard — Flask App
=================================
Reads from MongoDB and serves the live dashboard.
"""

import os
import requests
from flask import Flask, render_template, jsonify, request

from db import get_db, get_messages, get_finviz, load_cursors, get_price_history

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

GITHUB_USER = "7scgb4t8vc-cpu"
GITHUB_REPO = "stocktwits-collector"
GITHUB_BRANCH = "main"

WATCHLIST = {
    "HOOD", "QURE", "RXT", "ACON", "AIBZ", "ALBT", "ALOT", "ARDX",
    "BFLY", "CAT", "DIS", "HQ", "MRNA", "QS", "UUUU", "AAT", "ABCB",
    "ABG", "ABNB", "ABTC", "ABUS", "ACA", "ACH", "ACLO", "ACMR", "ACR"
}

# ── Data loaders ──────────────────────────────────────────────────────────────

def load_social():
    rows = get_messages()
    rows = [r for r in rows if r.get("symbol", "") in WATCHLIST]
    rows.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

    result = []
    for row in rows:
        result.append({
            "timestamp":  row.get("timestamp", ""),
            "symbol":     row.get("symbol", ""),
            "message":    row.get("message", ""),
            "sentiment":  row.get("sentiment", "neutral"),
            "nlp_label":  row.get("sentiment", ""),
            "nlp_score":  row.get("sentiment_score", ""),
        })
    return result


def load_screener():
    rows = get_finviz()
    return [r for r in rows if r.get("symbol", "") in WATCHLIST]


def load_frequency():
    rows = get_messages()
    counts = {}
    last_seen = {}
    for row in rows:
        sym = row.get("symbol", "")
        if sym not in WATCHLIST:
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
    rows = get_messages()
    rows = [r for r in rows if r.get("symbol", "") in WATCHLIST]

    sentiment_by_symbol = {}
    counts_over_time = {}
    for row in rows:
        symbol = row.get("symbol", "")
        label  = row.get("sentiment", "neutral") or "neutral"
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


def load_symbol_chart_data(symbol: str):
    """Build message volume and sentiment time series for a single symbol."""
    symbol = symbol.upper()
    rows = get_messages(symbol=symbol)

    volume_by_ts = {}
    sentiment_by_ts = {}
    for row in rows:
        ts = row.get("timestamp", "")
        volume_by_ts[ts] = volume_by_ts.get(ts, 0) + 1

        label = (row.get("sentiment") or "neutral").lower()
        if label not in ("bullish", "bearish", "neutral", "mixed"):
            label = "neutral"

        if ts not in sentiment_by_ts:
            sentiment_by_ts[ts] = {"bullish": 0, "bearish": 0, "neutral": 0, "mixed": 0}
        sentiment_by_ts[ts][label] += 1

    timestamps = sorted(set(volume_by_ts.keys()) | set(sentiment_by_ts.keys()))

    # Real price-over-time history
    price_rows = get_price_history(symbol)
    price_series = [{"timestamp": r.get("timestamp", ""), "price": r.get("price", "")} for r in price_rows]

    return {
        "symbol": symbol,
        "price_series": price_series,
        "volume_series": [{"timestamp": ts, "count": volume_by_ts.get(ts, 0)} for ts in timestamps],
        "sentiment_series": [
            {"timestamp": ts, **sentiment_by_ts.get(ts, {"bullish": 0, "bearish": 0, "neutral": 0, "mixed": 0})}
            for ts in timestamps
        ],
    }


def load_momentum():
    rows = get_finviz()
    rows = [r for r in rows if r.get("symbol", "") in WATCHLIST]

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
    coll = get_db()["sentiment_scores"]
    rows = list(coll.find())
    return {r["symbol"]: r for r in rows if r.get("symbol", "") in WATCHLIST}


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
    symbol = request.args.get("symbol", "").upper()
    label  = request.args.get("label", "")
    rows   = load_social()
    if symbol and symbol in WATCHLIST:
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
    return jsonify(sorted(WATCHLIST))


@app.route("/api/charts")
def api_charts():
    return jsonify(load_charts_data())


@app.route("/api/charts/<symbol>")
def api_charts_symbol(symbol):
    if symbol.upper() not in WATCHLIST:
        return jsonify({"error": "Symbol not tracked"}), 404
    return jsonify(load_symbol_chart_data(symbol))


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
