"""Shared PostgreSQL connection helper.

Session-level connection caching: repeated get_db() calls within the same
session reuse ONE TCP+TLS connection.  Layers' .close() calls are intercepted
as no-ops; the real close only happens via close_db_session().
"""
import os
import threading
import psycopg2
from psycopg2.extras import RealDictCursor

_session_conn = None
_session_lock = threading.Lock()


def _connect():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=int(os.getenv('POSTGRES_PORT') or '5432'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        dbname=os.getenv('POSTGRES_DB'),
        sslmode=os.getenv('POSTGRES_SSLMODE', 'require'),
    )


class _NoCloseConnection:
    """Wrapper that delegates everything to the real connection except .close()."""

    def __init__(self, real_conn):
        self._conn = real_conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        pass  # intercepted — the session manages the real close

    @property
    def closed(self):
        return self._conn.closed


def get_db(use_dict_cursor: bool = False):
    """Return a cached session connection (.close() is a no-op).

    For multithreaded use (ThreadPoolExecutor), the connection must be
    released before spawning threads.  Use get_db_fresh() if a thread
    needs its own connection.
    """
    global _session_conn
    with _session_lock:
        if _session_conn is None or _session_conn.closed:
            _session_conn = _connect()
        wrapped = _NoCloseConnection(_session_conn)
        if use_dict_cursor:
            wrapped.cursor_factory = RealDictCursor
    return wrapped


def get_db_fresh(use_dict_cursor: bool = False):
    """Always create a brand-new connection (for multithreaded use)."""
    conn = _connect()
    if use_dict_cursor:
        conn.cursor_factory = RealDictCursor
    return conn


def _get_cursor(conn, **kwargs):
    """Create a cursor, respecting cursor_factory if set on the connection."""
    factory = getattr(conn, 'cursor_factory', None)
    if factory and 'cursor_factory' not in kwargs:
        kwargs['cursor_factory'] = factory
    return conn.cursor(**kwargs)


def close_db_session():
    """Close the underlying session connection (call at shutdown)."""
    global _session_conn
    with _session_lock:
        if _session_conn and not _session_conn.closed:
            _session_conn.close()
        _session_conn = None


def db_configured() -> bool:
    return bool(os.getenv('POSTGRES_HOST') and os.getenv('POSTGRES_DB'))
