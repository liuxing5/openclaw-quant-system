# -*- coding: utf-8 -*-
"""多市场行情适配：A 股、港股、美股、日股、韩股。

行情主源为腾讯 qt.gtimg.cn，历史 K 线为腾讯 fqkline。免费源对美股/日股/
韩股的历史深度有限，接口会明确返回 available_days，不伪造长历史。
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import date, timedelta

from ..cache import ttl_cache
from .realtime import norm_code

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
_LINE = re.compile(r'v_([a-zA-Z0-9]+)="([^"]*)"')
_CURRENCY = {"us": "USD", "hk": "HKD", "jp": "JPY", "kr": "KRW", "sh": "CNY", "sz": "CNY"}


def normalize(symbol: str) -> str | None:
    s = (symbol or "").strip().upper()
    if not s:
        return None
    if s.endswith(".SH"):
        return "sh" + s[:-3].zfill(6)
    if s.endswith(".SZ"):
        return "sz" + s[:-3].zfill(6)
    if s.endswith(".HK"):
        return "hk" + s[:-3].zfill(5)
    if s.endswith(".US"):
        return "us" + s[:-3]
    if s.endswith(".T"):
        return "jp" + s[:-2]
    if s.endswith(".KS"):
        return "kr" + s[:-3]
    if s.isdigit() and len(s) == 6:
        return norm_code(s)
    if s.isalpha() and len(s) <= 6:
        return "us" + s
    return None


def _market(sym: str) -> str:
    return {"us": "美股", "hk": "港股", "jp": "日股", "kr": "韩股", "sh": "A股", "sz": "A股"}.get(sym[:2], "其他")


def _num(v):
    try:
        x = float(v)
        return x
    except (TypeError, ValueError):
        return None


@ttl_cache(20)
def quotes(symbols: list[str]) -> list[dict]:
    cleaned = []
    for s in symbols:
        n = normalize(s)
        if n and n not in cleaned:
            cleaned.append(n)
    if not cleaned:
        return []
    url = "https://qt.gtimg.cn/q=" + ",".join(cleaned)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Referer": "https://gu.qq.com/"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        text = resp.read().decode("gbk", "replace")
    out = []
    for m in _LINE.finditer(text):
        sym, payload = m.group(1), m.group(2)
        f = payload.split("~")
        if len(f) < 35:
            continue
        price = _num(f[3])
        if price is None:
            continue
        currency = f[35] if len(f) > 35 and f[35] and f[35].isalpha() else _CURRENCY.get(sym[:2], "")
        out.append(
            {
                "symbol": sym,
                "code": f[2],
                "name": f[1],
                "market": _market(sym),
                "currency": currency,
                "price": price,
                "pre_close": _num(f[4]),
                "open": _num(f[5]),
                "high": _num(f[33]),
                "low": _num(f[34]),
                "change": _num(f[31]),
                "pct_chg": _num(f[32]),
                "volume": _num(f[6]),
                "amount": _num(f[37]) if len(f) > 37 else None,
                "as_of": f[30] if len(f) > 30 else None,
                "source": "tencent",
            }
        )
    return out


@ttl_cache(60)
def history(symbol: str, days: int = 250) -> dict:
    sym = normalize(symbol)
    if not sym:
        return {"symbol": symbol, "items": [], "error": "无法识别市场代码"}
    days = max(20, min(int(days), 500))
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{days},qfq"
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Referer": "https://gu.qq.com/"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    node = (data.get("data") or {}).get(sym) or {}
    rows = node.get("qfqday") or node.get("day") or []
    items = []
    cutoff = (date.today() - timedelta(days=400)).isoformat()
    for r in rows:
        if len(r) < 6 or str(r[0]) < cutoff:
            continue
        items.append(
            {
                "date": r[0],
                "open": _num(r[1]),
                "close": _num(r[2]),
                "high": _num(r[3]),
                "low": _num(r[4]),
                "volume": _num(r[5]),
            }
        )
    return {"symbol": sym, "market": _market(sym), "items": items, "available_days": len(items)}
