import os
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI")

_client = None

def get_db():
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI)
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

def get_messages(symbol=None, scored_only=False, unscored_only=False):
    coll = messages_collection()
    query = {}
    if symbol:
        query["symbol"] = symbol
    if scored_only:
        query["nlp_label"] = {"$exists": True}
    if unscored_only:
        query["nlp_label"] = {"$exists": False}
    return list(coll.find(query))

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
