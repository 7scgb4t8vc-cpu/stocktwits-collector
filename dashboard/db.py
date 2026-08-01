from datetime import datetime, timedelta
import os
import certifi
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI")

_client = None
_client_pid = None

def get_db():
    """Pure getter — the MongoClient is created exactly once per worker by
    the post_fork hook in gunicorn.conf.py. Creating it here (i.e. before
    fork, or lazily on first request) causes the connection pool's sockets
    to be shared/inherited across worker processes, which corrupts them and
    causes cursor reads (find -> getMore) to hang forever.

    This function only checks that we're still running in the process the
    client was created for (a paranoia check in case a worker somehow forks
    again without going through post_fork) and returns the existing client.
    """
    global _client, _client_pid
    current_pid = os.getpid()
    if _client is None:
        raise RuntimeError(
            "MongoClient has not been initialized. It must be created in "
            "gunicorn.conf.py's post_fork hook before get_db() is called."
        )
    if _client_pid != current_pid:
        raise RuntimeError(
            "MongoClient was created in a different process (pid "
            f"{_client_pid}) than the current one (pid {current_pid}). "
            "This should never happen — post_fork should initialize the "
            "client for every worker process."
        )
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
    query["created_at"] = {"$gte": cutoff}
    return list(coll.find(query, batch_size=500).limit(20000))

def update_sentiment(message_id, sentiment, score):
    messages_collection().update_one(
        {"_id": message_id},
        {"$set": {"nlp_label": sentiment, "nlp_score": score}}
    )

def finviz_collection():
    return get_db()["finviz"]

from pymongo import ReplaceOne

# After updating current symbols, deletes any old symbol NOT in this fetch —
# this is what removes stale/excluded tickers (e.g. ETFs after adding the
# ind_stocksonly filter) instead of leaving them stuck in the database forever.
def upsert_finviz(rows):
    if not rows:
        return
    coll = finviz_collection()
    current_symbols = [row["symbol"] for row in rows]
    operations = [
        ReplaceOne({"symbol": row["symbol"]}, row, upsert=True)
        for row in rows
    ]
    coll.bulk_write(operations, ordered=False)
    coll.delete_many({"symbol": {"$nin": current_symbols}})

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
        
# Prevents two app instances (or two background threads) from double-collecting
# the same data at the same time. Each poller "claims" the lock by writing its
# own worker ID; a claim only succeeds if nobody currently holds it, WE already
# hold it, or the existing claim is stale (holder crashed/hung). Currently
# Railway only runs 1 worker, so this is a safety net more than a strict need.
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
    
# Adds/refreshes symbols instead of overwriting the tracked list. This lets a
# user switch screener filters (e.g. mega-cap -> nano-cap) without losing data
# collection for symbols they were already tracking.
def set_active_symbols(symbols):
    """Mark each symbol as seen now. Doesn't remove others — they expire
    naturally via get_active_symbols() after EXPIRY_HOURS of inactivity."""
    now = datetime.utcnow()
    coll = active_symbols_collection()
    for s in symbols:
        coll.update_one(
            {"symbol": s},
            {"$set": {"symbol": s, "last_seen": now}},
            upsert=True
        )

EXPIRY_HOURS = 72

# Symbols that haven't been re-selected in EXPIRY_HOURS are dropped automatically,
# so the tracked list doesn't grow forever as filters change over time.
def get_active_symbols():
    """Return symbols seen in any filtered view within the last EXPIRY_HOURS."""
    cutoff = datetime.utcnow() - timedelta(hours=EXPIRY_HOURS)
    coll = active_symbols_collection()
    coll.delete_many({"last_seen": {"$lt": cutoff}})
    docs = list(coll.find())
    return [d["symbol"] for d in docs if "symbol" in d]

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
def config_collection():
    return get_db()["config"]

def set_finviz_token(token: str):
    config_collection().update_one(
        {"_id": "finviz_token"},
        {"$set": {"value": token}},
        upsert=True
    )

def get_finviz_token() -> str:
    doc = config_collection().find_one({"_id": "finviz_token"})
    return doc["value"] if doc else ""
def delete_old_messages(days=7):
    """Remove messages older than `days` to keep the collection size bounded
    and queries fast. Called periodically by the message poller."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = messages_collection().delete_many({"created_at": {"$lt": cutoff}})
    return result.deleted_count
def get_finviz(symbol=None, symbols=None, fields=None):
       coll = finviz_collection()
       projection = {"_id": 0}
       if fields:
           projection = {f: 1 for f in fields}
           projection["_id"] = 0
       if symbol:
           return coll.find_one({"symbol": symbol}, projection)
       query = {}
       if symbols:
           query = {"symbol": {"$in": list(symbols)}}
       docs = list(coll.find(query, projection).batch_size(500))
       return docs
