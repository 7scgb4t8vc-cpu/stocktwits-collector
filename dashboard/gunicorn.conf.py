"""Gunicorn configuration for the stocktwits-collector dashboard.

MongoClient must never be created before the worker processes fork —
sockets in the connection pool get inherited by every child process,
which corrupts them and causes cursor reads (find -> getMore) to hang
forever while single round-trip writes appear to work fine.

The post_fork hook below runs in each worker immediately after it forks
and is the ONLY place a MongoClient is ever constructed. It creates one
client for the lifetime of the worker process and stashes it on the db
module; db.get_db() is a pure getter that just returns it (and raises if
it's ever called before this hook has run, or from a different process).
This guarantees exactly one client — and one pool of fresh, non-inherited
sockets — per worker.
"""

import os

import certifi
from pymongo import MongoClient

timeout = 120


def post_fork(server, worker):
    import db

    db._client = MongoClient(
        db.MONGO_URI,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=120000,
        tls=True,
        tlsCAFile=certifi.where(),
    )
    db._client_pid = os.getpid()

    database = db._client["stocktwits"]
    database["messages"].create_index("created_at")
    database["messages"].create_index([("symbol", 1), ("created_at", -1)])
