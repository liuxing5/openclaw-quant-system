# -*- coding: utf-8 -*-
"""主力资金流（新浪财经免费接口）。

用于实时异动的「主力大单流入/流出」信号：
``MoneyFlow.ssl_bkzj_ssggzj`` 一次返回全市场按净流入排序的榜单，
取流入/流出各前 100，过滤 ETF/债券后得到 A 股主力净流入榜。

口径：``netamount`` 为主力净额（元），``r0_net`` 为超大单净额（元），
``r0x_ratio`` 为超大单占比。该接口为盘中/最近交易日口径，页面按「资金流」
标注，不声称逐笔 Level-2。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from ..cache import ttl_cache

_BASE = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_ssggzj"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://finance.sina.com.cn/",
}


def _num(v, default=None):
    try:
        x = float(v)
        return x if x == x else default
    except (TypeError, ValueError):
        return default


def _code(symbol: str) -> str | None:
    s = (symbol or "").lower()
    if len(s) < 8:
        return None
    pure = s[2:]
    # 只保留 A 股：沪 60/68/90，深 00/30，北 4/8/920；排除 ETF（51/15/56/58）等
    if s.startswith("sh") and pure.startswith(("600", "601", "603", "605", "688", "689")):
        return f"{pure}.SH"
    if s.startswith("sz") and pure.startswith(("000", "001", "002", "003", "300", "301", "302")):
        return f"{pure}.SZ"
    if s.startswith("bj") and pure.startswith(("4", "8", "920")):
        return f"{pure}.BJ"
    return None


def _fetch(asc: int, num: int = 100) -> list[dict]:
    params = {
        "page": "1",
        "num": str(num),
        "sort": "netamount",
        "asc": str(asc),
        "bankuai": "",
        "shangzhang": "",
    }
    url = f"{_BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", "replace")
    # 新浪偶发返回 JSONP / 前后空白
    start, end = raw.find("["), raw.rfind("]")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    data = json.loads(raw or "[]")
    return data if isinstance(data, list) else []


def _norm(row: dict) -> dict | None:
    code = _code(str(row.get("symbol") or ""))
    if not code:
        return None
    net = _num(row.get("netamount"))
    r0 = _num(row.get("r0_net"))
    if net is None and r0 is None:
        return None
    return {
        "ts_code": code,
        "name": row.get("name") or code,
        "price": _num(row.get("trade")),
        "pct_chg": round((_num(row.get("changeratio"), 0) or 0) * 100, 2),
        "amount": _num(row.get("amount")),
        "net_amount": net,
        "super_net": r0,
        "net_ratio": round((_num(row.get("ratioamount"), 0) or 0) * 100, 2),
        "super_ratio": round((_num(row.get("r0_ratio"), 0) or 0) * 100, 2),
    }


@ttl_cache(30)
def main_flow(num: int = 100) -> dict:
    """返回主力净流入 / 净流出榜；失败时返回空榜，绝不影响异动主流程。"""
    try:
        inflow = [x for x in (_norm(r) for r in _fetch(0, num)) if x and (x["net_amount"] or 0) > 0]
    except Exception:  # noqa: BLE001
        inflow = []
    try:
        outflow = [x for x in (_norm(r) for r in _fetch(1, num)) if x and (x["net_amount"] or 0) < 0]
    except Exception:  # noqa: BLE001
        outflow = []
    inflow.sort(key=lambda x: -(x.get("net_amount") or 0))
    outflow.sort(key=lambda x: (x.get("net_amount") or 0))
    return {
        "inflow": inflow,
        "outflow": outflow,
        "source": "sina-moneyflow",
        "count": len(inflow) + len(outflow),
    }
