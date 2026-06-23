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
        query["sentiment"] = {"$exists": True}
    if unscored_only:
        query["sentiment"] = {"$exists": False}
    return list(coll.find(query))

def update_sentiment(message_id, sentiment, score):
    messages_collection().update_one(
        {"_id": message_id},
        {"$set": {"sentiment": sentiment, "sentiment_score": score}}
    )
