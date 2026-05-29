"""Shared PostgreSQL connection helper.

Uses a ThreadedConnectionPool to respect Supabase's connection limit (pool_size=15).
- get_db(): session-level cached connection from the pool (.close() is a no-op)
- get_db_fresh(): connection from the pool (.close() returns it for reuse)
- close_db_session(): returns the session connection to the pool
- close_all_pools(): closes all pool connections (call at process shutdown)

Set POSTGRES_POOL_SIZE (default 5) to control max connections per process.
With 5 per process, up to 3 concurrent workflows stay within the 15-connection limit.
"""
import os
import threading
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)

_pool = None
_pool_lock = threading.Lock()
_session_conn = None
_session_lock = threading.Lock()


def _connect_kwargs():
    return dict(
        host=os.getenv('POSTGRES_HOST'),
        port=int(os.getenv('POSTGRES_PORT') or '5432'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        dbname=os.getenv('POSTGRES_DB'),
        sslmode=os.getenv('POSTGRES_SSLMODE', 'require'),
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
        connect_timeout=15,
        options='-c statement_timeout=60000',
    )


def _get_pool():
    global _pool
    if _pool is not None and not _pool.closed:
        return _pool
    with _pool_lock:
        if _pool is not None and not _pool.closed:
            return _pool
        max_conn = int(os.getenv('POSTGRES_POOL_SIZE', '5'))
        _pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=max_conn,
            **_connect_kwargs(),
        )
        logger.info("DB connection pool created (max=%d)", max_conn)
    return _pool


class _PooledConnection:
    """Wrapper that returns the connection to the pool on .close()."""

    def __init__(self, conn, pool):
        object.__setattr__(self, '_pconn', conn)
        object.__setattr__(self, '_pool', pool)
        object.__setattr__(self, '_closed', False)
        object.__setattr__(self, 'cursor_factory', None)

    def __getattr__(self, name):
        return getattr(self._pconn, name)

    def __setattr__(self, name, value):
        if name in ('_pconn', '_pool', '_closed', 'cursor_factory'):
            object.__setattr__(self, name, value)
        else:
            setattr(self._pconn, name, value)

    def close(self):
        if not self._closed:
            object.__setattr__(self, '_closed', True)
            pool = self._pool
            conn = self._pconn
            try:
                if conn.closed:
                    pool.putconn(conn, close=True)
                else:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    pool.putconn(conn, close=False)
            except Exception:
                try:
                    pool.putconn(conn, close=True)
                except Exception:
                    pass

    @property
    def closed(self):
        return self._closed or self._pconn.closed

    def cursor(self, **kwargs):
        factory = self.cursor_factory
        if factory and 'cursor_factory' not in kwargs:
            kwargs['cursor_factory'] = factory
        return self._pconn.cursor(**kwargs)

    def commit(self):
        self._pconn.commit()

    def rollback(self):
        self._pconn.rollback()


class _NoCloseConnection:
    """Wrapper that delegates everything to the real connection except .close().

    Used by get_db() for session-level caching. .close() is a no-op;
    the real return happens via close_db_session().
    """

    def __init__(self, real_conn):
        self._conn = real_conn
        self.cursor_factory = None

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        pass

    @property
    def closed(self):
        return self._conn.closed

    def cursor(self, **kwargs):
        factory = self.cursor_factory
        if factory and 'cursor_factory' not in kwargs:
            kwargs['cursor_factory'] = factory
        return self._conn.cursor(**kwargs)


def get_db(use_dict_cursor: bool = False):
    """Return a cached session connection (.close() is a no-op).

    The connection is drawn from the pool and held until close_db_session().
    Not thread-safe - use get_db_fresh() for multithreaded use.
    """
    global _session_conn
    with _session_lock:
        if _session_conn is not None and not _session_conn.closed:
            try:
                _session_conn.cursor()
            except Exception:
                logger.warning("DB session connection lost, reconnecting...")
                try:
                    pool = _get_pool()
                    pool.putconn(_session_conn, close=True)
                except Exception:
                    pass
                _session_conn = None
        if _session_conn is None or _session_conn.closed:
            pool = _get_pool()
            _session_conn = pool.getconn()
        wrapped = _NoCloseConnection(_session_conn)
        if use_dict_cursor:
            wrapped.cursor_factory = RealDictCursor
    return wrapped


def get_db_fresh(use_dict_cursor: bool = False):
    """Get a connection from the pool (.close() returns it for reuse).

    Thread-safe - each call gets its own connection from the pool.
    When done, call .close() to return it.
    """
    pool = _get_pool()
    conn = pool.getconn()
    wrapped = _PooledConnection(conn, pool)
    if use_dict_cursor:
        wrapped.cursor_factory = RealDictCursor
    return wrapped


def close_db_session():
    """Return the session connection to the pool."""
    global _session_conn
    with _session_lock:
        if _session_conn is not None:
            try:
                pool = _get_pool()
                if not _session_conn.closed:
                    try:
                        _session_conn.rollback()
                    except Exception:
                        pass
                    pool.putconn(_session_conn, close=False)
                else:
                    pool.putconn(_session_conn, close=True)
            except Exception:
                try:
                    pool = _get_pool()
                    pool.putconn(_session_conn, close=True)
                except Exception:
                    pass
            _session_conn = None


def close_all_pools():
    """Close all connections in the pool (call at process shutdown)."""
    global _pool, _session_conn
    with _session_lock:
        _session_conn = None
    with _pool_lock:
        if _pool is not None and not _pool.closed:
            _pool.closeall()
            logger.info("DB connection pool closed")
        _pool = None


def db_configured() -> bool:
    return bool(os.getenv('POSTGRES_HOST') and os.getenv('POSTGRES_DB'))
