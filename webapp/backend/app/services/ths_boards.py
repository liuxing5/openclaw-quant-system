# -*- coding: utf-8 -*-
"""同花顺概念板块成分（免费 HTML 源，按需抓取 + 落库缓存）。

东财 ``push2`` 的板块成分接口会被断连，akshare 也走同一接口；同花顺的
``q.10jqka.com.cn/gn/detail/code/{code}/`` 页面直接带成分股表格，分页 AJAX
也不需要登录，因此用它补齐概念成分。

策略：
* 概念名 → 同花顺概念代码：抓 ``q.10jqka.com.cn/gn/`` 列表并缓存 1 小时；
* 某个概念第一次被打开时抓成分（每页 10 只，最多 12 页），写入
  ``board_member_cache`` + ``concept_membership``；
* 之后直接从本地库读，不再打同花顺。
"""
from __future__ import annotations

import re
import urllib.request
from datetime import date

from ..cache import ttl_cache
from ..db import db_session, query

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "http://q.10jqka.com.cn/",
}
_LIST_URL = "http://q.10jqka.com.cn/gn/"
_DETAIL = "http://q.10jqka.com.cn/gn/detail/code/{code}/"
_AJAX = "http://q.10jqka.com.cn/gn/detail/field/199112/order/desc/page/{page}/ajax/1/code/{code}"

_TAG = re.compile(r"<[^>]+>")
_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_CODE = re.compile(r"(\d{6})")


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("gbk", "replace")


def _clean(s: str) -> str:
    return _TAG.sub("", s or "").replace("&nbsp;", " ").strip()


def _ts_code(code: str) -> str:
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8", "920")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _amount(v: str) -> float | None:
    s = (v or "").strip()
    if not s:
        return None
    try:
        if s.endswith("亿"):
            return float(s[:-1]) * 1e8
        if s.endswith("万"):
            return float(s[:-1]) * 1e4
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _parse_rows(html: str) -> list[dict]:
    out = []
    for block in _TR.findall(html):
        cells = [_clean(x) for x in _TD.findall(block)]
        if len(cells) < 4:
            continue
        m = _CODE.search(cells[1] if len(cells) > 1 else "")
        if not m:
            continue
        code = m.group(1)
        try:
            price = float(cells[3]) if cells[3] else None
        except ValueError:
            price = None
        try:
            pct = float(cells[4]) if len(cells) > 4 and cells[4] else None
        except ValueError:
            pct = None
        try:
            turnover = float(cells[7]) if len(cells) > 7 and cells[7] else None
        except ValueError:
            turnover = None
        amount = _amount(cells[10]) if len(cells) > 10 else None
        out.append(
            {
                "ts_code": _ts_code(code),
                "name": cells[2] if len(cells) > 2 else code,
                "price": price,
                "pct_chg": pct,
                "turnover_rate": turnover,
                "amount": amount,
            }
        )
    return out


@ttl_cache(3600)
def concept_code_map() -> dict[str, str]:
    try:
        html = _get(_LIST_URL)
    except Exception:  # noqa: BLE001
        return {}
    pairs = re.findall(r"gn/detail/code/(\d+)/\"[^>]*>([^<]+)</a>", html)
    return {name.strip(): code for code, name in pairs if name.strip()}


def _normalize(name: str) -> str:
    s = re.sub(r"\s+", "", name or "")
    for suffix in ("概念", "板块", "指数"):
        if s.endswith(suffix) and len(s) > len(suffix):
            s = s[: -len(suffix)]
    return s.lower()


def _find_code(name: str) -> str | None:
    mp = concept_code_map()
    if name in mp:
        return mp[name]
    target = _normalize(name)
    for n, c in mp.items():
        if _normalize(n) == target:
            return c
    # 包含匹配：只在唯一命中时使用，避免「AI」匹配到一堆概念
    hits = [(n, c) for n, c in mp.items() if target and (target in _normalize(n) or _normalize(n) in target)]
    return hits[0][1] if len(hits) == 1 else None


def fetch_concept(name: str, max_pages: int = 12) -> list[dict]:
    code = _find_code(name)
    if not code:
        return []
    rows = _parse_rows(_get(_DETAIL.format(code=code)))
    for page in range(2, max_pages + 1):
        try:
            html = _get(_AJAX.format(code=code, page=page))
        except Exception:  # noqa: BLE001
            break
        page_rows = _parse_rows(html)
        if not page_rows:
            break
        rows.extend(page_rows)
    # 去重，保留首次出现
    seen = set()
    out = []
    for r in rows:
        if r["ts_code"] in seen:
            continue
        seen.add(r["ts_code"])
        out.append(r)
    return out


def get_or_fetch_concept(board_code: str, board_name: str, max_pages: int = 12) -> list[dict]:
    """先读本地缓存，没有再抓同花顺并落库。"""
    try:
        rows = query(
            """
            SELECT b.ts_code, b.stock_name, q.close, q.pct_chg, q.amount, q.turnover_rate
            FROM board_member_cache b
            LEFT JOIN LATERAL (
                SELECT d.close, d.pct_chg, d.amount, d.turnover_rate
                FROM daily_quotes d WHERE d.ts_code = b.ts_code
                ORDER BY d.trade_date DESC LIMIT 1
            ) q ON TRUE
            WHERE b.board_type = 'concept' AND b.board_code = %s
            ORDER BY COALESCE(q.amount, 0) DESC, b.ts_code
            """,
            (board_code,),
        )
        if rows:
            return [
                {
                    "ts_code": r["ts_code"],
                    "name": r.get("stock_name") or r["ts_code"],
                    "close": r.get("close"),
                    "pct_chg": r.get("pct_chg"),
                    "amount": r.get("amount"),
                    "turnover_rate": r.get("turnover_rate"),
                }
                for r in rows
            ]
    except Exception:  # noqa: BLE001
        pass

    members = fetch_concept(board_name, max_pages=max_pages)
    if not members:
        return []
    try:
        with db_session() as conn:
            cur = conn.cursor()
            try:
                cur.executemany(
                    """
                    INSERT INTO board_member_cache (board_type, board_code, board_name, ts_code, stock_name, updated_at)
                    VALUES ('concept', %s, %s, %s, %s, now())
                    ON CONFLICT (board_type, board_code, ts_code)
                    DO UPDATE SET stock_name = EXCLUDED.stock_name, updated_at = now()
                    """,
                    [(board_code, board_name, m["ts_code"], m["name"]) for m in members],
                )
                cur.executemany(
                    """
                    INSERT INTO concept_membership (ts_code, concept_code, concept_name, update_date)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (ts_code, concept_code)
                    DO UPDATE SET concept_name = EXCLUDED.concept_name, update_date = EXCLUDED.update_date
                    """,
                    [(m["ts_code"], board_code, board_name, date.today()) for m in members],
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()
    except Exception:  # noqa: BLE001 落库失败也把本次结果返回
        pass
    return members
