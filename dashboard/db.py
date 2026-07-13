from datetime import datetime, timedelta
import os
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI")

_client = None


def get_db():
    global _client
    if _client is None:
        _client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=20000,
        )
        db = _client["stocktwits"]
        db["messages"].create_index("created_at")
        db["messages"].create_index([("symbol", 1), ("created_at", -1)])
    return _client["stocktwits"]

def messages_collection():
    return get_db()["messages"]

def insert_messages(messages):
    """messages: list of dicts"""
    if not messages:
        return
    coll = messages_collection()
    for m in messages:
        coll.update_one(
            {"_id": m["_id"]},
            {"$set": m},
            upsert=True
        )

def get_messages(symbol=None, scored_only=False, unscored_only=False, days=7):
    coll = messages_collection()
    query = {}
    if symbol:
        query["symbol"] = symbol
    if scored_only:
        query["nlp_label"] = {"$exists": True}
    if unscored_only:
        query["nlp_label"] = {"$exists": False}
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query["$or"] = [
        {"created_at": {"$gte": cutoff}},
        {"created_at": {"$exists": False}},
    ]
    return list(coll.find(query).limit(20000))

def update_sentiment(message_id, sentiment, score):
    messages_collection().update_one(
        {"_id": message_id},
        {"$set": {"nlp_label": sentiment, "nlp_score": score}}
    )

def finviz_collection():
    return get_db()["finviz"]

def upsert_finviz(rows):
    coll = finviz_collection()
    for row in rows:
        coll.update_one(
            {"symbol": row["symbol"]},
            {"$set": row},
            upsert=True
        )

def get_finviz(symbol=None):
    coll = finviz_collection()
    if symbol:
        doc = coll.find_one({"symbol": symbol})
        if doc:
            doc.pop("_id", None)
        return doc
    docs = list(coll.find())
    for d in docs:
        d.pop("_id", None)
    return docs

def ohlc_collection():
    return get_db()["ohlc_history"]

def save_ohlc(symbol, rows):
    """rows: list of dicts with date, open, high, low, close, volume"""
    coll = ohlc_collection()
    for row in rows:
        coll.update_one(
            {"symbol": symbol, "date": row["date"]},
            {"$set": {**row, "symbol": symbol}},
            upsert=True
        )

def get_ohlc(symbol, limit_days=300):
    rows = list(ohlc_collection().find({"symbol": symbol}).sort("date", -1).limit(limit_days))
    for r in rows:
        r.pop("_id", None)
    return sorted(rows, key=lambda r: r["date"])

def watchlist_collection():
    return get_db()["watchlist"]

def add_to_watchlist(symbol: str):
    watchlist_collection().update_one(
        {"symbol": symbol}, {"$set": {"symbol": symbol}}, upsert=True
    )

def remove_from_watchlist(symbol: str):
    watchlist_collection().delete_one({"symbol": symbol})

def price_history_collection():
    return get_db()["price_history"]

def log_price(symbol, timestamp, price, change_pct, volume):
    price_history_collection().insert_one({
        "symbol": symbol,
        "timestamp": timestamp,
        "price": price,
        "change_pct": change_pct,
        "volume": volume,
    })

def get_price_history(symbol):
    rows = list(price_history_collection().find({"symbol": symbol}))
    for r in rows:
        r.pop("_id", None)
    return sorted(rows, key=lambda r: r.get("timestamp", ""))

def cursors_collection():
    return get_db()["cursors"]

def load_cursors():
    docs = cursors_collection().find()
    return {d["symbol"]: d["since_id"] for d in docs}

def save_cursors(cursors):
    coll = cursors_collection()
    for symbol, since_id in cursors.items():
        coll.update_one(
            {"symbol": symbol},
            {"$set": {"since_id": since_id}},
            upsert=True
        )
def try_acquire_poller_lock(worker_id, stale_after_seconds=90):
    """Atomically claim the poller lock if unclaimed, held by us, or stale.
    Returns True if this worker holds the lock this cycle."""
    coll = get_db()["poller_lock"]
    now = datetime.utcnow()
    stale_cutoff = now - timedelta(seconds=stale_after_seconds)
    result = coll.find_one_and_update(
        {
            "_id": "singleton",
            "$or": [
                {"holder": worker_id},
                {"updated_at": {"$lt": stale_cutoff}},
                {"holder": {"$exists": False}},
            ],
        },
        {"$set": {"holder": worker_id, "updated_at": now}},
        upsert=True,
        return_document=True,
    )
    return result.get("holder") == worker_id
def active_symbols_collection():
    return get_db()["active_symbols"]

def set_active_symbols(symbols):
    """Overwrite the current filtered symbol list (max 50) that the
    minute-level poller should track."""
    symbols = symbols[:50]
    active_symbols_collection().update_one(
        {"_id": "current"},
        {"$set": {"symbols": symbols, "updated_at": datetime.utcnow().isoformat()}},
        upsert=True
    )

def get_active_symbols():
    doc = active_symbols_collection().find_one({"_id": "current"})
    return doc["symbols"] if doc else []

def log_price_tick(symbol, timestamp, price):
    """Minute-level price tick from the background poller. Writes to the
    same price_history collection so existing chart code doesn't need to change."""
    price_history_collection().insert_one({
        "symbol": symbol,
        "timestamp": timestamp,
        "price": price,
        "change_pct": None,
        "volume": None,
        "source": "minute_poll",
    })
def try_acquire_poller_lock(worker_id, stale_after_seconds=90):
    from pymongo.errors import DuplicateKeyError
    coll = get_db()["poller_lock"]
    now = datetime.utcnow()
    stale_cutoff = now - timedelta(seconds=stale_after_seconds)
    try:
        result = coll.find_one_and_update(
            {
                "_id": "singleton",
                "$or": [
                    {"holder": worker_id},
                    {"updated_at": {"$lt": stale_cutoff}},
                    {"holder": {"$exists": False}},
                ],
            },
            {"$set": {"holder": worker_id, "updated_at": now}},
            upsert=True,
            return_document=True,
        )
        return result.get("holder") == worker_id
    except DuplicateKeyError:
        return False
def blocked_symbols_collection():
    return get_db()["blocked_symbols"]

def add_blocked_symbol(symbol: str, reason: str = "not_found"):
    blocked_symbols_collection().update_one(
        {"symbol": symbol},
        {"$set": {"symbol": symbol, "reason": reason}},
        upsert=True
    )

def get_blocked_symbols() -> list:
    docs = list(blocked_symbols_collection().find())
    return [d["symbol"] for d in docs]
