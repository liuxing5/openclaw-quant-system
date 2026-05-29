"""Shared PostgreSQL connection helper.

Uses a ThreadedConnectionPool to respect Supabase's connection limit (pool_size=15).
- get_db(): session-level cached connection from the pool (.close() is a no-op)
- get_db_fresh(): connection from the pool (.close() returns it for reuse)
- close_db_session(): returns the session connection to the pool
- close_all_pools(): closes all pool connections (call at process shutdown)

Set POSTGRES_POOL_SIZE (default 5) to control max connections per process.
With 5 per process, up to 3 concurrent workflows stay within the 15-connection limit.

When Supabase pool is exhausted (EMAXCONNSESSION), all connection acquisition
automatically retries with linear backoff (10s, 20s, 30s ...) up to 5 attempts.
"""
import os
import time
import threading
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool, PoolError

logger = logging.getLogger(__name__)

_pool = None
_pool_lock = threading.Lock()
_session_conn = None
_session_lock = threading.Lock()

_MAX_RETRIES = 5
_RETRY_DELAY = 10
_POOL_EXHAUSTED_MARKERS = ("EMAXCONNSESSION", "max clients reached")


def _is_pool_exhausted(exc):
    err = str(exc).lower()
    return any(m.lower() in err for m in _POOL_EXHAUSTED_MARKERS)


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
        # 关键：设置连接超时，防止僵尸连接
        options='-c statement_timeout=60000 -c idle_in_transaction_session_timeout=300000',
    )


def _get_pool():
    global _pool
    if _pool is not None and not _pool.closed:
        return _pool
    with _pool_lock:
        if _pool is not None and not _pool.closed:
            return _pool
        max_conn = int(os.getenv('POSTGRES_POOL_SIZE', '5'))
        for attempt in range(_MAX_RETRIES):
            try:
                _pool = ThreadedConnectionPool(
                    minconn=0,
                    maxconn=max_conn,
                    **_connect_kwargs(),
                )
                logger.info("DB connection pool created (max=%d)", max_conn)
                return _pool
            except psycopg2.OperationalError as e:
                if _is_pool_exhausted(e) and attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAY * (attempt + 1)
                    logger.warning(
                        "DB pool creation failed (Supabase exhausted), "
                        "retrying in %ds (%d/%d)...",
                        delay, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(delay)
                    continue
                raise
    return _pool


def _pool_getconn(pool):
    """Get a connection from the pool with retry on exhaustion.

    Handles two exhaustion scenarios:
    1. Supabase EMAXCONNSESSION: other workflows hold all 15 connections
    2. psycopg2 PoolError: this process already uses all pool slots
    In both cases, wait and retry - the situation is temporary.
    """
    for attempt in range(_MAX_RETRIES):
        try:
            return pool.getconn()
        except psycopg2.OperationalError as e:
            if _is_pool_exhausted(e) and attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAY * (attempt + 1)
                logger.warning(
                    "DB pool exhausted (Supabase), waiting %ds for available slot (%d/%d)...",
                    delay, attempt + 1, _MAX_RETRIES,
                )
                time.sleep(delay)
                continue
            raise
        except PoolError as e:
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAY * (attempt + 1)
                logger.warning(
                    "DB pool exhausted (local), waiting %ds for available slot (%d/%d)...",
                    delay, attempt + 1, _MAX_RETRIES,
                )
                time.sleep(delay)
                continue
            raise


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
            _session_conn = _pool_getconn(pool)
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
    conn = _pool_getconn(pool)
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
