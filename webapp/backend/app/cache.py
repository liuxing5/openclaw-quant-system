# -*- coding: utf-8 -*-
"""进程内 TTL 缓存：全市场聚合（涨跌分布、情绪、梯队）计算代价高，短缓存即可扛住并发。

采用 stale-while-revalidate 语义：
  * 命中未过期 → 直接返回；
  * 已过期但有旧值、且后台正在重算 → 返回旧值（用户永远不会撞上冷缓存）；
  * 后台线程用 ``fn.refresh(...)`` 强制重算，重算期间旧值继续对外服务。
"""
import threading
import time
from functools import wraps

_store = {}
_computing = set()
_lock = threading.Lock()


def _key(fn, args, kwargs):
    return (fn.__module__, fn.__qualname__, repr(args), repr(sorted(kwargs.items())))


def ttl_cache(seconds: float, key_fn=None):
    """装饰器：按参数缓存返回值 seconds 秒。"""

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            k = key_fn(*args, kwargs) if key_fn else _key(fn, args, kwargs)
            now = time.time()
            with _lock:
                hit = _store.get(k)
                if hit and hit[0] > now:
                    return hit[1]
                stale = hit[1] if hit else None
                if k in _computing:
                    if stale is not None:
                        return stale
                _computing.add(k)
            try:
                value = fn(*args, **kwargs)
            finally:
                with _lock:
                    _computing.discard(k)
            with _lock:
                _store[k] = (time.time() + seconds, value)
            return value

        def refresh(*args, **kwargs):
            """强制重算并原子替换；重算期间并发读取仍返回旧值。"""
            k = key_fn(*args, kwargs) if key_fn else _key(fn, args, kwargs)
            with _lock:
                hit = _store.get(k)
                if k in _computing:
                    return hit[1] if hit else None
                _computing.add(k)
            try:
                value = fn(*args, **kwargs)
            finally:
                with _lock:
                    _computing.discard(k)
            with _lock:
                _store[k] = (time.time() + seconds, value)
            return value

        wrapper.refresh = refresh
        wrapper.invalidate = lambda *a, **k: _store.pop(_key(fn, a, k), None)
        wrapper.cache_clear = lambda: _store.clear()
        return wrapper

    return deco


def cache_get(key):
    with _lock:
        hit = _store.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
    return None


def cache_set(key, value, seconds: float):
    with _lock:
        _store[key] = (time.time() + seconds, value)
    return value


def cache_clear():
    with _lock:
        _store.clear()


def cache_stats() -> dict:
    now = time.time()
    with _lock:
        alive = sum(1 for exp, _ in _store.values() if exp > now)
        return {"entries": len(_store), "alive": alive, "computing": len(_computing)}
