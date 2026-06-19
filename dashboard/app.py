"""
StockTwits Dashboard — Flask App
=================================
Fetches CSV files directly from GitHub raw content URLs and serves a live dashboard.
"""

import csv
import io
import os
import requests
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

GITHUB_USER = "7scgb4t8vc-cpu"
GITHUB_REPO = "stocktwits-collector"
GITHUB_BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/data"

# Locked-in watchlist — only these symbols should appear anywhere in the dashboard
WATCHLIST = {
    "HOOD", "QURE", "RXT", "ACON", "AIBZ", "ALBT", "ALOT", "ARDX",
    "BFLY", "CAT", "DIS", "HQ", "MRNA", "QS", "UUUU", "AAT", "ABCB",
    "ABG", "ABNB", "ABTC", "ABUS", "ACA", "ACH", "ACLO", "ACMR", "ACR"
}

# ── Data loaders ──────────────────────────────────────────────────────────────

def load_csv_from_github(filename: str) -> list:
    url = f"{RAW_BASE}/{filename}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        reader = csv.DictReader(io.StringIO(resp.text))
        return list(reader)
    except Exception:
        return []


def load_social():
    st_rows  = load_csv_from_github("stocktwits.csv")
    nlp_rows = load_csv_from_github("nlp_output.csv")

    nlp_map = {}
    for row in nlp_rows:
        key = (row.get("timestamp", ""), row.get("symbol", ""))
        nlp_map[key] = row

    result = []
    for row in reversed(st_rows):
        sym = row.get("symbol", "")
        if sym not in WATCHLIST:
            continue
        key = (row.get("timestamp", ""), sym)
        nlp = nlp_map.get(key, {})
        result.append({
            "timestamp":  row.get("timestamp", ""),
            "symbol":     sym,
            "message":    row.get("message", ""),
            "sentiment":  row.get("sentiment", "None"),
            "nlp_label":  nlp.get("nlp_label", ""),
            "nlp_score":  nlp.get("nlp_score", ""),
            "clean_text": nlp.get("clean_text", ""),
        })
    return result


def load_screener():
    rows = load_csv_from_github("finviz.csv")
    return [r for r in rows if r.get("symbol", "") in WATCHLIST]


def load_frequency():
    rows = load_csv_from_github("frequency.csv")
    return [r for r in rows if r.get("symbol", "") in WATCHLIST]


def load_charts_data():
    st_rows  = load_csv_from_github("stocktwits.csv")
    nlp_rows = load_csv_from_github("nlp_output.csv")

    st_rows  = [r for r in st_rows  if r.get("symbol", "") in WATCHLIST]
    nlp_rows = [r for r in nlp_rows if r.get("symbol", "") in WATCHLIST]

    sentiment_by_symbol = {}
    for row in nlp_rows:
        symbol = row.get("symbol", "")
        label  = row.get("nlp_label", "neutral")
        if symbol not in sentiment_by_symbol:
            sentiment_by_symbol[symbol] = {"bullish": 0, "bearish": 0, "neutral": 0, "mixed": 0}
        sentiment_by_symbol[symbol][label] = sentiment_by_symbol[symbol].get(label, 0) + 1

    counts_over_time = {}
    for row in st_rows:
        ts = row.get("timestamp", "")[:16]
        counts_over_time[ts] = counts_over_time.get(ts, 0) + 1

    return {
        "sentiment_by_symbol": sentiment_by_symbol,
        "counts_over_time":    sorted(counts_over_time.items()),
    }


def load_symbol_chart_data(symbol: str):
    """Build price, message volume, and sentiment time series for a single symbol."""
    symbol = symbol.upper()

    price_rows = load_csv_from_github("price_history.csv")
    price_rows = [r for r in price_rows if r.get("symbol", "").upper() == symbol]
    price_series = [
        {"timestamp": r.get("timestamp", ""), "price": r.get("price", "")}
        for r in price_rows
    ]

    st_rows  = load_csv_from_github("stocktwits.csv")
    nlp_rows = load_csv_from_github("nlp_output.csv")

    nlp_map = {}
    for row in nlp_rows:
        key = (row.get("timestamp", ""), row.get("symbol", ""))
        nlp_map[key] = row

    st_rows = [r for r in st_rows if r.get("symbol", "").upper() == symbol]

    volume_by_ts = {}
    sentiment_by_ts = {}
    for row in st_rows:
        ts = row.get("timestamp", "")
        volume_by_ts[ts] = volume_by_ts.get(ts, 0) + 1

        key = (ts, row.get("symbol", ""))
        nlp = nlp_map.get(key, {})
        label = (nlp.get("nlp_label") or row.get("sentiment") or "neutral").lower()
        if label not in ("bullish", "bearish", "neutral", "mixed"):
            label = "neutral"

        if ts not in sentiment_by_ts:
            sentiment_by_ts[ts] = {"bullish": 0, "bearish": 0, "neutral": 0, "mixed": 0}
        sentiment_by_ts[ts][label] += 1

    timestamps = sorted(set(volume_by_ts.keys()) | set(sentiment_by_ts.keys()))

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
    rows = load_csv_from_github("finviz.csv")
    rows = [r for r in rows if r.get("symbol", "") in WATCHLIST]

    def parse_float(v):
        try:
            return float(str(v).replace("%", "").replace(",", "").strip())
        except Exception:
            return 0.0

    for row in rows:
        row["_rel_vol"] = parse_float(row.get("rel_volume", "0"))
        row["_change"]  = parse_float(row.get("change_pct", "0").replace("%", ""))

    return sorted(rows, key=lambda r: r["_rel_vol"], reverse=True)


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
