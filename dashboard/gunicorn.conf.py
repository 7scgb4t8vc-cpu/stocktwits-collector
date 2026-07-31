"""Gunicorn configuration for the stocktwits-collector dashboard.

MongoClient must never be created before the worker processes fork —
sockets in the connection pool get inherited by every child process,
which corrupts them and causes cursor reads (find -> getMore) to hang
forever while single round-trip writes appear to work fine.

The post_fork hook below runs in each worker immediately after it forks,
resetting any MongoClient that may have been created during import of
app.py in the master process, forcing a fresh client (and fresh sockets)
to be created lazily in that worker.
"""

timeout = 120


def post_fork(server, worker):
    import db

    db._client = None
    db._client_pid = None
