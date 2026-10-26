# -*- coding: utf-8 -*-
"""全市场实时快照（腾讯行情批量接口）。

为什么需要它
------------
日线表 ``daily_quotes`` 是 T-1 收盘，盘中异动 / 涨跌停 / 量比这些只能靠实时快照。
东财 ``clist`` 一次只回 100 行（全市场要 60 次请求），而腾讯 ``qt.gtimg.cn``
一次可拿 400~500 只，5000 只 A 股约 11~14 个请求、2 秒左右。

实现要点
--------
1. 代码清单来自 ``stock_basic_info``（缓存 300s），不受行情源影响；
2. 400 只/批、4 线程并发；单批失败重试一次，仍失败则丢弃该批（快照仍有几千只）；
3. 解析腾讯 88 字段中的价格 / 涨跌幅 / 量比 / 换手 / 成交额 / 涨跌停价 / 市值；
4. 整张快照缓存 20 秒，请求路径只读缓存，冷启动约 2 秒。
"""
from __future__ import annotations

import re
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from ..cache import ttl_cache
from ..db import query

_BATCH = 400
_WORKERS = 4
_URL = "https://qt.gtimg.cn/q="
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_LINE_RE = re.compile(r'v_([a-z]{2}\d{6})="([^"]*)"')


def _f(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


@ttl_cache(300)
def _code_rows() -> list[dict]:
    return query(
        """
        SELECT ts_code, stock_name, is_st, market
        FROM stock_basic_info
        WHERE is_active = true
        ORDER BY ts_code
        """
    )


@ttl_cache(300)
def _prefix_map() -> dict[str, str]:
    out = {}
    for r in _code_rows():
        code = r["ts_code"]
        pure, _, ex = code.partition(".")
        ex = ex.upper()
        prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(ex)
        if prefix:
            out[f"{prefix}{pure}"] = code
    return out


def _parse(text: str, mapping: dict[str, str], st_map: dict[str, bool]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in _LINE_RE.finditer(text):
        prefixed, payload = m.group(1), m.group(2)
        code = mapping.get(prefixed)
        if not code:
            continue
        f = payload.split("~")
        if len(f) < 50:
            continue
        price = _f(f[3])
        if not price or price <= 0:
            continue
        pre_close = _f(f[4])
        pct = _f(f[32])
        if pct is None and pre_close:
            pct = (price - pre_close) / pre_close * 100
        ts_raw = f[30] or ""
        as_of = None
        if len(ts_raw) == 14 and ts_raw.isdigit():
            try:
                as_of = datetime.strptime(ts_raw, "%Y%m%d%H%M%S").isoformat(timespec="seconds")
            except ValueError:
                as_of = None
        out[code] = {
            "ts_code": code,
            "name": f[1] or code,
            "price": round(price, 4),
            "pct_chg": round(pct, 4) if pct is not None else None,
            "open": _f(f[5]),
            "high": _f(f[33]),
            "low": _f(f[34]),
            "pre_close": pre_close,
            "volume": _f(f[6]),
            "amount": round((_f(f[37], 0) or 0) * 1e4, 2),
            "turnover_rate": _f(f[38]),
            "volume_ratio": _f(f[49]),
            "pe_ratio": _f(f[39]),
            "pb_ratio": _f(f[46]),
            "market_cap": round((_f(f[44], 0) or 0) * 1e8, 2),
            "circulating_market_cap": round((_f(f[45], 0) or 0) * 1e8, 2),
            "limit_up_price": _f(f[47]),
            "limit_down_price": _f(f[48]),
            "is_st": bool(st_map.get(code)),
            "as_of": as_of,
            "source": "tencent",
        }
    return out


def _fetch_batch(items: list[tuple[str, str]], mapping: dict[str, str], st_map: dict[str, bool]) -> dict[str, dict]:
    prefixed = [p for p, _ in items]
    url = _URL + ",".join(prefixed)
    last = None
    for _ in range(2):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": _UA, "Referer": "https://gu.qq.com/"}
            )
            with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310 域名固定
                return _parse(resp.read().decode("gbk", errors="replace"), mapping, st_map)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(str(last))


@ttl_cache(20)
def snapshot() -> dict:
    """全市场实时快照；失败时返回空 items，由调用方降级到日线事件。"""
    rows = _code_rows()
    mapping = _prefix_map()
    st_map = {r["ts_code"]: bool(r.get("is_st")) for r in rows}
    pairs = [(prefix, code) for prefix, code in mapping.items()]
    batches = [pairs[i : i + _BATCH] for i in range(0, len(pairs), _BATCH)]

    items: dict[str, dict] = {}
    failed = 0
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = [pool.submit(_fetch_batch, b, mapping, st_map) for b in batches]
        for fut in futures:
            try:
                items.update(fut.result())
            except Exception:  # noqa: BLE001 单批失败不影响其余
                failed += 1

    # 补抓：并发批失败时用 100 只的小批再试一轮（小 URL 更稳）
    missing = [(p, c) for p, c in pairs if c not in items]
    if missing:
        failed = 0
        for i in range(0, len(missing), 100):
            try:
                items.update(_fetch_batch(missing[i : i + 100], mapping, st_map))
            except Exception:  # noqa: BLE001
                failed += 1

    as_of = datetime.now().isoformat(timespec="seconds")
    return {
        "items": list(items.values()),
        "count": len(items),
        "as_of": as_of,
        "source": "tencent",
        "failed_batches": failed,
        "batches": len(batches),
    }


# 请求路径只读这份内存快照：刷新由后台线程做，接口永远不等外部行情源
_current: dict = {}
_current_lock = threading.Lock()


def get_snapshot() -> dict:
    with _current_lock:
        return _current.get("value") or {
            "items": [],
            "count": 0,
            "as_of": None,
            "source": "warming",
        }


def _set_current(value: dict) -> dict:
    with _current_lock:
        _current["value"] = value
    return value


def refresh_now() -> dict:
    """立即拉取并替换快照（「立即扫描」用；可能耗时 3~10 秒）。"""
    return _set_current(snapshot.refresh())


# ---------------------------------------------------------------------------
# 后台刷新：保证 /anomaly/events 永远读到热快照，而不是每次请求等 3 秒
# ---------------------------------------------------------------------------

_stop = threading.Event()
_thread: threading.Thread | None = None


def _loop() -> None:
    while not _stop.is_set():
        ok = False
        try:
            _set_current(snapshot.refresh())
            ok = True
        except Exception:  # noqa: BLE001 外部源/数据库未就绪时快速重试
            pass
        now = datetime.now()
        trading = now.weekday() < 5 and (9 * 60 <= now.hour * 60 + now.minute <= 15 * 60)
        if not ok:
            delay = 3
        else:
            delay = 20 if trading else 300
        if _stop.wait(delay):
            break


def start_refresher() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="live-snapshot", daemon=True)
    _thread.start()


def stop_refresher() -> None:
    _stop.set()
