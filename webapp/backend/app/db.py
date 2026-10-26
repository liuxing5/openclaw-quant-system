# -*- coding: utf-8 -*-
"""数据库访问层：webapp 专用的真·连接复用池。

为什么不用 core.db.connection 的池
---------------------------------
psycopg2 的 ``ThreadedConnectionPool._putconn`` 只有在 ``len(_pool) < minconn``
时才把连接放回池里，否则直接 ``conn.close()``。core 里的池 ``minconn=0``，
于是**每次查询结束都会关掉连接**，下一次查询必须重新 TCP + TLS 握手。

实测（本机 -> 远端 PostgreSQL）：
    建连握手      0.5 ~ 3.7 s
    同一条 SQL    数据库端执行 2.3 ms，取数 0.16 s
    结论          95% 的耗时花在反复建连上

因此这里实现一个 LIFO 池：借出 -> 用完 rollback 归还 -> 下次直接复用，
握手开销只发生在建连那一刻；连接失效时自动丢弃重建。

另外注意：本机到远端库的**带宽只有约 40~90 KB/s**，所以业务层的铁律是
「聚合下推 SQL、只回传小结果集」，见 app/services/market.py。
"""
from __future__ import annotations

import os
import queue
import sys
import threading
from contextlib import contextmanager

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.utils.env import load_project_env  # noqa: E402

load_project_env()

import psycopg2  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

MAX_CONNS = int(os.getenv("WEBAPP_DB_POOL", "8"))
CHECKOUT_TIMEOUT = float(os.getenv("WEBAPP_DB_WAIT", "30"))

_idle: queue.LifoQueue = queue.LifoQueue(maxsize=MAX_CONNS)
_lock = threading.Lock()
_live = 0
_bad: set[int] = set()


def _new_conn():
    url = os.getenv("WEBAPP_DATABASE_URL") or os.environ["DATABASE_URL"]
    # Supabase 的 5432 是 session 模式：整个项目共享 15 个客户端，webapp 的常驻连接池
    # 很容易把它占满（EMAXCONNSESSION）。6543 是事务池模式，支持更多客户端，
    # 且本项目的 SQL 都是短事务、无会话级状态，可安全切换。
    if not os.getenv("WEBAPP_DATABASE_URL") and "pooler.supabase.com:5432" in url:
        url = url.replace("pooler.supabase.com:5432", "pooler.supabase.com:6543")
    return psycopg2.connect(url)


def _checkout():
    """取一条可用连接：优先复用空闲连接，其次新建（受 MAX_CONNS 约束）。"""
    global _live
    while True:
        try:
            conn = _idle.get_nowait()
        except queue.Empty:
            break
        if conn is None:
            continue
        if getattr(conn, "closed", True):
            with _lock:
                _live -= 1
            continue
        try:
            conn.rollback()  # 归还前已 rollback，这里只是兜底，确保处于 idle
        except Exception:  # noqa: BLE001
            try:
                conn.close()
            finally:
                with _lock:
                    _live -= 1
            continue
        return conn

    with _lock:
        can_create = _live < MAX_CONNS
        if can_create:
            _live += 1
    if can_create:
        try:
            return _new_conn()
        except Exception:
            with _lock:
                _live -= 1
            raise

    # 池已用满：等别人归还
    try:
        conn = _idle.get(timeout=CHECKOUT_TIMEOUT)
        if conn is not None and not conn.closed:
            return conn
    except queue.Empty:
        pass
    # 超时兜底：临时多开一条，避免请求直接失败
    with _lock:
        _live += 1
    return _new_conn()


def _checkin(conn, discard: bool = False):
    """归还连接；坏连接直接关闭。"""
    global _live
    if conn is None:
        return
    cid = id(conn)
    with _lock:
        was_bad = cid in _bad
        _bad.discard(cid)
    if discard or was_bad:
        try:
            conn.close()
        finally:
            with _lock:
                _live -= 1
        return
    try:
        if getattr(conn, "closed", True):
            with _lock:
                _live -= 1
            return
        conn.rollback()
    except Exception:  # noqa: BLE001
        try:
            conn.close()
        finally:
            with _lock:
                _live -= 1
        return
    try:
        _idle.put_nowait(conn)
    except queue.Full:
        try:
            conn.close()
        finally:
            with _lock:
                _live -= 1


def _mark_bad(conn):
    with _lock:
        _bad.add(id(conn))


def _retryable(exc) -> bool:
    return isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError))


@contextmanager
def db_session():
    """借出一条连接，退出时自动归还。"""
    conn = _checkout()
    try:
        yield conn
    finally:
        _checkin(conn)


def query(sql: str, params=()):
    """执行查询，返回 list[dict]；连接断开时自动换一条重试一次。"""
    last = None
    for _ in range(2):
        conn = _checkout()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute(sql, params)
                return list(cur.fetchall())
            finally:
                cur.close()
        except Exception as exc:  # noqa: BLE001
            if _retryable(exc):
                _mark_bad(conn)
                last = exc
                continue
            raise
        finally:
            _checkin(conn)
    raise last


def query_one(sql: str, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def scalar(sql: str, params=(), default=None):
    row = query_one(sql, params)
    if not row:
        return default
    return next(iter(row.values()))


def execute(sql: str, params=()):
    last = None
    for _ in range(2):
        conn = _checkout()
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql, params)
                conn.commit()
                return cur.rowcount
            finally:
                cur.close()
        except Exception as exc:  # noqa: BLE001
            if _retryable(exc):
                _mark_bad(conn)
                last = exc
                continue
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            _checkin(conn)
    raise last


def pool_stats() -> dict:
    with _lock:
        return {"live": _live, "idle": _idle.qsize(), "max": MAX_CONNS, "bad": len(_bad)}


def shutdown():
    """关掉所有空闲连接（进程退出时调用）。"""
    global _live
    while True:
        try:
            conn = _idle.get_nowait()
        except queue.Empty:
            break
        if conn is None:
            continue
        try:
            conn.close()
        finally:
            with _lock:
                _live -= 1
    try:
        from core.db.connection import close_all_pools

        close_all_pools()
    except Exception:  # noqa: BLE001
        pass
