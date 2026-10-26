# -*- coding: utf-8 -*-
"""实时行情快照层。

分层取数，越靠前越「实时」：
    1. 新浪 ``hq.sinajs.cn`` 盘口（交易时段 ~10s 刷新）
    2. 腾讯 ``qt.gtimg.cn`` 盘口
    3. 东方财富 ``push2.eastmoney.com`` JSON（新浪/腾讯都失败时的兜底）
    4. 本地 ``daily_quotes`` 最新交易日收盘（永远可用的兜底）

网络源在本机可能不可用，因此**任何调用都不允许抛异常**：拿不到就逐级降级，
最终一定返回结构完整的结果。请求路径上只读缓存，不做网络 I/O。

对外：
    is_trading()          -> bool            是否在交易时段
    snapshot(codes)       -> dict            以 ts_code 为键的行情字典
    snapshot_rows(codes)  -> list[dict]      保序的行列表（缺失的标的补空值）
    market_status()       -> dict            状态条：来源/缓存/下次刷新
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
import urllib.request

from ..cache import cache_get, cache_set
from ..db import query

log = logging.getLogger("realtime")

# 代码归一化：600519 / 600519.SH / sh600519 -> 600519.SH
_EXCH = {"SH": "SH", "SZ": "SZ", "BJ": "BJ"}


def norm_code(code: str) -> str:
    c = (code or "").strip().upper()
    if not c:
        return ""
    if "." in c:
        base, _, suf = c.partition(".")
        return f"{base}.{suf}"
    if c[:2] in ("SH", "SZ", "BJ") and c[2:].isdigit():
        return f"{c[2:]}.{c[:2]}"
    if c.startswith(("6", "9")):
        return f"{c}.SH"
    if c.startswith(("4", "8")):
        return f"{c}.BJ"
    return f"{c}.SZ"


def _to_sina(code: str) -> str:
    c = norm_code(code)
    base, _, suf = c.partition(".")
    return ("sh" if suf == "SH" else "bj" if suf == "BJ" else "sz") + base


def is_trading(now: dt.datetime | None = None) -> bool:
    """A 股交易时段：工作日 09:15-11:30 / 13:00-15:10（含集合竞价缓冲）。"""
    now = now or dt.datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (dt.time(9, 15) <= t <= dt.time(11, 31)) or (dt.time(13, 0) <= t <= dt.time(15, 10))


def _http_get(url: str, timeout: float = 1.2) -> str:
    """超时统一压到 1.2s：请求路径上最坏要串新浪→腾讯→东财三次取数，
    任何一个源挂起都不能让接口跟着挂起（宁可少一个实时字段，走 DB 底表）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 固定白名单域名
        return resp.read().decode("gbk", errors="replace")


# ---------------------------------------------------------------- 网络源
def _sym_to_code(sym: str) -> str:
    """``sh600519`` / ``v_sh600519`` -> ``600519.SH``。"""
    s = sym.lower().lstrip("v_").lstrip("v")
    prefix, _, base = s.partition("_")
    if not base:
        prefix, base = s[:2], s[2:]
    suffix = {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(prefix[:2])
    if not suffix or not base.isdigit():
        return ""
    return f"{base}.{suffix}"


def _fetch_sina(codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    syms = ",".join(_to_sina(c) for c in codes)
    raw = _http_get(f"https://hq.sinajs.cn/list={syms}")
    out: dict[str, dict] = {}
    for line in raw.splitlines():
        if "hq_str_" not in line:
            continue
        head, _, body = line.partition("=")
        sym = head.split("hq_str_")[-1].strip()
        code = _sym_to_code(sym)
        if not code:
            continue
        parts = body.strip().strip('"').split(",")
        if len(parts) < 10:
            continue
        try:
            price = float(parts[3])
            pre = float(parts[2])
        except ValueError:
            continue
        if price <= 0:
            continue
        pre = pre or price
        out[code] = {
            "name": parts[0],
            "open": float(parts[1]) or None,
            "pre_close": pre,
            "price": price,
            "high": float(parts[4]) or None,
            "low": float(parts[5]) or None,
            "volume": float(parts[8]) or None,
            "amount": float(parts[9]) or None,
            "pct_chg": round((price - pre) / pre * 100, 2),
            "source": "sina",
        }
    return out


def _fetch_tencent(codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    syms = ",".join(_to_sina(c) for c in codes)
    raw = _http_get(f"https://qt.gtimg.cn/q={syms}")
    out: dict[str, dict] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        head, _, body = line.partition("=")
        sym = head.split("v_")[-1].strip()
        code = _sym_to_code(sym)
        if not code:
            continue
        parts = body.strip().strip('"').split("~")
        if len(parts) < 35:
            continue
        try:
            price = float(parts[3])
            pre = float(parts[4])
        except ValueError:
            continue
        if price <= 0:
            continue
        pre = pre or price

        def _f(idx, scale=1.0):
            try:
                return float(parts[idx]) * scale if parts[idx] else None
            except (ValueError, IndexError):
                return None

        out[code] = {
            "name": parts[1],
            "open": _f(5),
            "pre_close": pre,
            "price": price,
            "high": _f(33),
            "low": _f(34),
            "volume": _f(6, 100.0),
            "amount": _f(37, 10000.0),
            "pct_chg": _f(32) if parts[32] else round((price - pre) / pre * 100, 2),
            "source": "tencent",
        }
    return out


# ---------------------------------------------------------------- 东财 push2（第三兜底）
# 为什么还要第三个源：新浪/腾讯在本机偶发被远端掐断（超时或 RST），
# akshare 又是重依赖。东财 push2 是纯 JSON、无需 Key、单次往返，
# 作为新浪与腾讯都失败时的兜底，能把「全量降级到日线」的概率再降一档。
_EM_FIELDS = "f2,f3,f5,f6,f12,f14,f15,f16,f17,f18"  # 价/涨幅/量额/代码/名称/高/低/开/昨收
_EM_ONE_FIELDS = "f43,f44,f45,f46,f57,f58,f60,f170"  # 单只版字段（东财口径：×100）
_EM_STOCK_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_EM_LIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"


def _em_secid(code: str) -> str:
    """``600519.SH`` -> ``1.600519``（沪）、``000001.SZ``/``430047.BJ`` -> ``0.xxx``。

    北交所在东财有时是 ``2.`` 前缀，这里统一按 ``0.`` 请求；取不到就当该代码缺失，
    由 DB 底表兜底（宁可少一个字段，也不让整个接口失败）。
    """
    c = norm_code(code)
    base, _, suf = c.partition(".")
    if not base.isdigit():
        return ""
    if suf == "SH":
        return f"1.{base}"
    if suf in ("SZ", "BJ"):
        return f"0.{base}"
    return ""


def _http_get_json(url: str, timeout: float = 1.2) -> dict:
    """东财专用取数：JSON 是 UTF-8（新浪是 GBK 文本），所以不能复用 `_http_get` 的解码。

    超时压到 1.2s——它只是第三兜底，请求路径上不允许久等；失败一律抛给调用方去降级。
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://quote.eastmoney.com/",
            "Accept": "application/json, text/plain, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 固定白名单域名
        text = resp.read().decode("utf-8", errors="replace")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("eastmoney payload is not an object")
    return data


def _em_num(v):
    if v is None or v in ("", "-", "--"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _em_div(pct_raw, price_raw) -> float:
    """识别服务端是否忽略了 ``fltt=2``（那样所有数值都是整数 ×100）。

    日涨跌幅不可能超过 ±35%（北交所 30% 是上限），所以 |涨跌幅| > 35 就说明
    服务端给的是 ×100 的整数，需要再除一次 100。
    """
    if pct_raw is not None and abs(pct_raw) > 35:
        return 100.0
    if pct_raw is None and price_raw and price_raw > 10000:
        return 100.0
    return 1.0


def _em_row(d: dict) -> dict | None:
    """把东财字段映射成与新浪/腾讯一致的行结构（价格口径已由调用方归一到元）。"""
    price = _em_num(d.get("price"))
    pre = _em_num(d.get("pre_close"))
    if price is None or price <= 0:
        return None
    pre = pre or price
    pct = _em_num(d.get("pct_chg"))
    if pct is None:
        pct = round((price - pre) / pre * 100, 2)
    return {
        "name": d.get("name"),
        "open": _em_num(d.get("open")),
        "pre_close": pre,
        "price": price,
        "high": _em_num(d.get("high")),
        "low": _em_num(d.get("low")),
        "volume": _em_num(d.get("volume")),
        "amount": _em_num(d.get("amount")),
        "pct_chg": round(pct, 2),
        "source": "eastmoney",
    }


def _fetch_eastmoney(codes: list[str]) -> dict[str, dict]:
    """东财批量取价（``ulist.np``，一次请求带 ``fltt=2``，返回即为小数）。

    失败、字段缺失、个别代码取不到都**不抛异常**：能给几个算几个，
    剩下的由调用方用 DB 底表补齐（结构永远完整）。
    """
    if not codes:
        return {}
    secids: list[str] = []
    by_pure: dict[str, str] = {}
    for c in codes:
        sid = _em_secid(c)
        if not sid:
            continue
        secids.append(sid)
        by_pure[sid.split(".", 1)[1]] = c
    if not secids:
        return {}

    out: dict[str, dict] = {}
    try:
        payload = _http_get_json(
            f"{_EM_LIST_URL}?fltt=2&secids={','.join(secids)}&fields={_EM_FIELDS}",
            timeout=1.2,
        )
        diff = ((payload.get("data") or {}).get("diff")) or []
        if isinstance(diff, dict):  # 老版本返回 {0:{...},1:{...}}
            diff = list(diff.values())
        for d in diff:
            if not isinstance(d, dict):
                continue
            pure = str(d.get("f12") or "").strip()
            code = by_pure.get(pure)
            if not code:
                continue
            pct_raw = _em_num(d.get("f3"))
            price_raw = _em_num(d.get("f2"))
            div = _em_div(pct_raw, price_raw)
            row = _em_row(
                {
                    "name": d.get("f14"),
                    "open": (_em_num(d.get("f17")) or 0) / div or None,
                    "pre_close": (_em_num(d.get("f18")) or 0) / div or None,
                    "price": price_raw / div if price_raw is not None else None,
                    "high": (_em_num(d.get("f15")) or 0) / div or None,
                    "low": (_em_num(d.get("f16")) or 0) / div or None,
                    "volume": (_em_num(d.get("f5")) or 0) * 100.0 or None,  # 手 -> 股
                    "amount": _em_num(d.get("f6")),  # 单位元，不再缩放
                    "pct_chg": pct_raw / div if pct_raw is not None else None,
                },
            )
            if row:
                out[code] = row
        if out:
            return out
    except Exception as exc:  # noqa: BLE001  第三兜底，任何失败都往下走
        log.debug("eastmoney batch failed: %s", exc)

    # 单只场景再试一下「单股接口」（东财口径：价格字段为整数 ×100）
    if len(by_pure) == 1:
        try:
            return _fetch_eastmoney_one(codes)
        except Exception as exc:  # noqa: BLE001
            log.debug("eastmoney single failed: %s", exc)
    return {}


def _fetch_eastmoney_one(codes: list[str]) -> dict[str, dict]:
    """单股接口兜底（字段 ×100，按东财口径显式除以 100）。"""
    out: dict[str, dict] = {}
    for c in codes:
        sid = _em_secid(c)
        if not sid:
            continue
        payload = _http_get_json(f"{_EM_STOCK_URL}?secid={sid}&fields={_EM_ONE_FIELDS}", timeout=1.2)
        d = payload.get("data") or {}
        if not isinstance(d, dict) or not d:
            continue
        pct_raw = _em_num(d.get("f170"))
        row = _em_row(
            {
                "name": d.get("f58"),
                "open": (_em_num(d.get("f46")) or 0) / 100.0 or None,
                "pre_close": (_em_num(d.get("f60")) or 0) / 100.0 or None,
                "price": (_em_num(d.get("f43")) or 0) / 100.0 or None,
                "high": (_em_num(d.get("f44")) or 0) / 100.0 or None,
                "low": (_em_num(d.get("f45")) or 0) / 100.0 or None,
                "pct_chg": pct_raw / 100.0 if pct_raw is not None else None,
            },
        )
        if row:
            out[c] = row
        if len(out) >= len(codes):
            break
    return out


# ---------------------------------------------------------------- 本地兜底
def _fetch_db(codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    rows = query(
        """
        SELECT DISTINCT ON (q.ts_code)
               q.ts_code, q.trade_date, q.open, q.high, q.low, q.close, q.pre_close,
               q.volume, q.amount, q.pct_chg, q.turnover_rate, q.total_market_cap,
               q.limit_up_price, q.limit_down_price,
               i.stock_name, i.market
        FROM daily_quotes q
        LEFT JOIN stock_basic_info i ON i.ts_code = q.ts_code
        WHERE q.ts_code = ANY(%s)
        ORDER BY q.ts_code, q.trade_date DESC
        """,
        (codes,),
    )
    out: dict[str, dict] = {}
    for r in rows:
        close = float(r["close"] or 0)
        pre = float(r["pre_close"] or 0) or close
        out[r["ts_code"]] = {
            "name": r.get("stock_name"),
            "open": float(r["open"]) if r["open"] is not None else None,
            "pre_close": pre or None,
            "price": close,
            "high": float(r["high"]) if r["high"] is not None else None,
            "low": float(r["low"]) if r["low"] is not None else None,
            "volume": float(r["volume"]) if r["volume"] is not None else None,
            "amount": float(r["amount"]) if r["amount"] is not None else None,
            "pct_chg": round(float(r["pct_chg"] or 0), 2),
            "turnover_rate": float(r["turnover_rate"]) if r["turnover_rate"] is not None else None,
            "market_cap": float(r["total_market_cap"]) if r["total_market_cap"] is not None else None,
            "limit_up_price": float(r["limit_up_price"]) if r["limit_up_price"] is not None else None,
            "limit_down_price": float(r["limit_down_price"]) if r["limit_down_price"] is not None else None,
            "trade_date": str(r["trade_date"]) if r["trade_date"] else None,
            "market": r.get("market"),
            "source": "db",
        }
    return out


_NAMES: dict[str, str] = {}


def _names_for(codes: list[str]) -> dict[str, str]:
    missing = [c for c in codes if c not in _NAMES]
    if missing:
        try:
            for r in query("SELECT ts_code, stock_name FROM stock_basic_info WHERE ts_code = ANY(%s)", (missing,)):
                _NAMES[r["ts_code"]] = r["stock_name"]
        except Exception:  # noqa: BLE001
            pass
    return {c: _NAMES.get(c) for c in codes}


def _live_snapshot(codes: tuple[str, ...]) -> dict:
    """交易时段 10s、非交易时段 600s 的网络快照；全程不抛异常。"""
    codes = list(codes)
    if not codes:
        return {"source": "none", "items": {}}
    trading = is_trading()
    key = ("rt", "t" if trading else "i", tuple(codes))
    cached = cache_get(key)
    if cached is not None:
        return cached

    result = {"source": "none", "items": {}}
    counts: dict[str, int] = {}
    remaining = list(codes)
    # 优先级：新浪 -> 腾讯 -> 东财 push2。
    # 拿到结果不立刻停：某个源可能只覆盖部分代码（停牌、北交所、代码写错），
    # 继续用后面的源补齐缺失的那几只；全部补齐或三个源都试过才收手。
    for fn, tag in ((_fetch_sina, "sina"), (_fetch_tencent, "tencent"), (_fetch_eastmoney, "eastmoney")):
        if not remaining:
            break
        try:
            items = fn(list(remaining))
        except Exception as exc:  # noqa: BLE001
            log.debug("live source %s failed: %s", tag, exc)
            items = {}
        fresh = {k: v for k, v in (items or {}).items() if k in set(remaining) and k not in result["items"]}
        if not fresh:
            continue
        result["items"].update(fresh)
        counts[tag] = counts.get(tag, 0) + len(fresh)
        remaining = [c for c in remaining if c not in result["items"]]
    if counts:
        # source 取覆盖代码最多的那个源（平局时按优先级先来的算）
        result["source"] = max(counts, key=lambda t: counts[t])
    cache_set(key, result, 10 if trading else 600)
    return result


def snapshot(codes: list[str]) -> dict[str, dict]:
    """返回 ``{ts_code: {...行情字段...}}``，网络不可用时降级到日线收盘价。"""
    norm = [norm_code(c) for c in codes if c]
    # 去重但保序
    seen: dict[str, None] = {}
    for c in norm:
        seen.setdefault(c, None)
    norm = list(seen)
    if not norm:
        return {}

    live: dict = {}
    try:
        live = _live_snapshot(tuple(norm)).get("items") or {}
    except Exception:  # noqa: BLE001
        live = {}

    # 底表一份（含换手/市值/涨跌停价），实时值覆盖其上，网络失败时即为兜底
    base = _fetch_db(norm)
    names = _names_for(norm)
    result: dict[str, dict] = {}
    for code in norm:
        row = dict(base.get(code) or {})
        for k, v in (live.get(code) or {}).items():
            if v is not None:
                row[k] = v
        row["ts_code"] = code
        row.setdefault("name", names.get(code))
        row.setdefault("source", "db")
        result[code] = row
    return result


def snapshot_rows(codes: list[str]) -> list[dict]:
    """保序行列表；库里查不到的代码也返回一行（字段为 None），方便前端直出。"""
    norm = []
    seen: set[str] = set()
    for c in codes:
        n = norm_code(c)
        if n and n not in seen:
            seen.add(n)
            norm.append(n)
    data = snapshot(norm)
    rows = []
    for code in norm:
        row = dict(data.get(code) or {})
        row.setdefault("ts_code", code)
        row.setdefault("name", None)
        rows.append(row)
    return rows


def market_status() -> dict:
    trading = is_trading()
    try:
        live = _live_snapshot(("600519.SH", "000001.SZ"))
        source, cached = live.get("source"), len(live.get("items") or {})
    except Exception:  # noqa: BLE001
        source, cached = "none", 0
    return {
        "trading": trading,
        "source": source if source != "none" else "db",
        "live": source not in ("none", "db"),
        "cached": cached,
        "as_of": dt.datetime.now().isoformat(timespec="seconds"),
        "next_refresh_sec": 10 if trading else 600,
    }
