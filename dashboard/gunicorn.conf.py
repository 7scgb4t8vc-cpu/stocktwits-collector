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
import sys
import traceback

import certifi
from pymongo import MongoClient

timeout = 120


def post_fork(server, worker):
    pid = os.getpid()
    print(f"[post_fork] worker {pid}: starting post_fork hook", file=sys.stderr, flush=True)

    try:
        print(f"[post_fork] worker {pid}: importing db module", file=sys.stderr, flush=True)
        import db

        print(f"[post_fork] worker {pid}: resolving certifi CA bundle", file=sys.stderr, flush=True)
        ca_file = certifi.where()
        print(f"[post_fork] worker {pid}: certifi CA bundle at {ca_file}", file=sys.stderr, flush=True)

        print(f"[post_fork] worker {pid}: creating MongoClient", file=sys.stderr, flush=True)
        db._client = MongoClient(
            db.MONGO_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=120000,
            tls=True,
            tlsCAFile=ca_file,
        )
        db._client_pid = pid
        print(f"[post_fork] worker {pid}: MongoClient created successfully", file=sys.stderr, flush=True)

        print(f"[post_fork] worker {pid}: creating indexes", file=sys.stderr, flush=True)
        database = db._client["stocktwits"]
        database["messages"].create_index("created_at")
        database["messages"].create_index([("symbol", 1), ("created_at", -1)])
        print(f"[post_fork] worker {pid}: indexes created successfully", file=sys.stderr, flush=True)

        print(f"[post_fork] worker {pid}: post_fork hook completed successfully", file=sys.stderr, flush=True)
    except Exception:
        print(f"[post_fork] worker {pid}: EXCEPTION raised in post_fork hook", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        raise
