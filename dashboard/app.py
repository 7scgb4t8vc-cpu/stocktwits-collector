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

# ── Data loaders ──────────────────────────────────────────────────────────────

def load_csv_from_github(filename: str) -> list:
    """Fetch a CSV file from GitHub raw content and parse it."""
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
    """Load StockTwits messages, merge NLP labels if available."""
    st_rows  = load_csv_from_github("stocktwits.csv")
    nlp_rows = load_csv_from_github("nlp_output.csv")

    nlp_map = {}
    for row in nlp_rows:
        key = (row.get("timestamp", ""), row.get("symbol", ""))
        nlp_map[key] = row

    result = []
    for row in reversed(st_rows):
        key = (row.get("timestamp", ""), row.get("symbol", ""))
        nlp = nlp_map.get(key, {})
        result.append({
            "timestamp":  row.get("timestamp", ""),
            "symbol":     row.get("symbol", ""),
            "message":    row.get("message", ""),
            "sentiment":  row.get("sentiment", "None"),
            "nlp_label":  nlp.get("nlp_label", ""),
            "nlp_score":  nlp.get("nlp_score", ""),
            "clean_text": nlp.get("clean_text", ""),
        })
    return result


def load_screener():
    """Load FinViz data for the screener table."""
    return load_csv_from_github("finviz.csv")


def load_frequency():
    """Load ticker mention frequency."""
    return load_csv_from_github("frequency.csv")


def load_charts_data():
    """Compute sentiment breakdown per symbol for charts."""
    st_rows  = load_csv_from_github("stocktwits.csv")
    nlp_rows = load_csv_from_github("nlp_output.csv")

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


def load_momentum():
    """Load FinViz data sorted by relative volume for momentum view."""
    rows = load_csv_from_github("finviz.csv")

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
    if symbol:
        rows = [r for r in rows if r["symbol"] == symbol]
    if label:
        rows = [r for r in rows if r["sentiment"].lower() == label.lower()
                or r["nlp_label"].lower() == label.lower()]
    return jsonify(rows[:200])


@app.route("/api/screener")
def api_screener():
    return jsonify(load_screener())


@app.route("/api/frequency")
def api_frequency():
    return jsonify(load_frequency())


@app.route("/api/charts")
def api_charts():
    return jsonify(load_charts_data())


@app.route("/api/momentum")
def api_momentum():
    return jsonify(load_momentum())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
