# -*- coding: utf-8 -*-
"""后台预热线程。

背景：本机到远端 PostgreSQL 的链路只有约 40~90 KB/s，一次全市场聚合在冷缓存下
需要数十秒。为了做到「打开页面秒出、盘中自动刷新」，服务启动后由一个守护线程
按交易时段节奏持续重算各聚合键：

  * 重算期间旧值继续对外服务（stale-while-revalidate），用户不会撞上冷缓存；
  * 交易时段每 60 秒刷新一轮，盘后每 10 分钟刷新一轮，兼顾实时性与开销。
"""
from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime

_log_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None


def _log(msg: str) -> None:
    with _log_lock:
        print(f"[warmup] {msg}", flush=True)


def _is_trading_moment(now: datetime) -> bool:
    """是否需要高频刷新：交易日的盘前/盘中/收盘后 30 分钟内。"""
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return 9 * 60 <= t <= 15 * 60 + 30


def _jobs() -> list[tuple]:
    """返回 (函数, args, kwargs) 列表，必须与业务调用处完全一致（缓存 key 依赖 repr）。"""
    from .services import market as svc

    day = svc.latest_trade_date()
    if not day:
        return []
    return [
        (svc.latest_trade_date, (), {}),
        (svc.breadth, (day,), {}),
        (svc.trend_and_newhigh, (day,), {}),
        (svc.index_overview, (day,), {}),
        (svc.rankings, (day, "pct_chg_desc", 8), {}),
        (svc.rankings, (day, "pct_chg_asc", 8), {}),
        (svc.rankings, (day, "amount", 8), {}),
        (svc.rankings, (day, "turnover_rate", 8), {}),
        (svc.board_top, (day, "concept", 5), {}),
        (svc.board_top, (day, "industry", 5), {}),
        (svc.amount_series, (day, 21), {}),
        (svc.amount_series, (day, 30), {}),
        (svc.limit_ladder, (day,), {"detail": False}),
        (svc.limit_ladder, (day,), {"detail": True}),
        (svc.limit_up_codes, (day,), {}),
    ]


def warm_once() -> float:
    """跑一轮预热，返回耗时（秒）。"""
    start = time.time()
    try:
        from .services.calendar import ensure_calendar

        ensure_calendar()
        for fn, args, kwargs in _jobs():
            if _stop.is_set():
                break
            refresh = getattr(fn, "refresh", None)
            if refresh is None:
                fn(*args, **kwargs)
            else:
                refresh(*args, **kwargs)
    except Exception:  # noqa: BLE001
        _log("warmup failed:\n" + traceback.format_exc(limit=4))
    return time.time() - start


def _loop() -> None:
    # 启动后先歇一下，避免抢在服务完全就绪前打满数据库
    if _stop.wait(3):
        return
    while not _stop.is_set():
        cost = warm_once()
        now = datetime.now()
        interval = 60 if _is_trading_moment(now) else 600
        _log(f"round done in {cost:.1f}s, next in {interval}s")
        if _stop.wait(interval):
            break


def start_warmup() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="warmup", daemon=True)
    _thread.start()
    _log("started")


def stop_warmup() -> None:
    _stop.set()
