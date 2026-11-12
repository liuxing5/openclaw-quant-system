# -*- coding: utf-8 -*-
"""实时资金看板：大盘资金链路、板块资金、个股资金、广度。

数据源：
- 东方财富 push2：指数分钟资金流、概念/行业资金流、涨跌家数
- 新浪 MoneyFlow：个股主力净流入/流出（money_flow 服务）
- 数据库：实时源失败时的最近交易日板块/广度兜底

所有金额单位统一为元；前端负责格式化为亿/万。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from ..cache import ttl_cache
from ..db import query
from . import money_flow

BEIJING = timezone(timedelta(hours=8))
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://data.eastmoney.com/"}
_BASE = "https://push2.eastmoney.com/api/qt"
# 这些是宽泛风格/指数标签，不是可交易题材；混在概念资金榜会淹没真实板块
_SKIP_BOARD = (
    "融资融券", "富时罗素", "标准普尔", "MSCI", "沪股通", "深股通", "转债标的",
    "机构重仓", "基金重仓", "预盈预增", "预亏预减", "破净股", "高送转", "次新股",
    "B股", "AH股", "科创板", "创业板", "深成500", "上证180", "沪深300", "中证500",
    "昨日涨停", "昨日连板", "昨日触板", "百元股", "低价股", "微盘股",
)


def _f(v, default=0.0):
    try:
        x = float(v)
        return x if x == x else default
    except (TypeError, ValueError):
        return default


def _get(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _index_flow(secid: str) -> dict:
    url = (
        f"{_BASE}/stock/fflow/kline/get?lmt=0&klt=1&secid={secid}"
        "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63"
    )
    data = _get(url).get("data") or {}
    klines = data.get("klines") or []
    series = []
    for line in klines:
        p = line.split(",")
        if len(p) < 6:
            continue
        series.append({
            "time": p[0],
            "main": _f(p[1]),
            "small": _f(p[2]),
            "medium": _f(p[3]),
            "large": _f(p[4]),
            "super": _f(p[5]),
        })
    return {"name": data.get("name") or secid, "series": series}


def _boards(fs: str, asc: int, limit: int = 10) -> list[dict]:
    url = (
        f"{_BASE}/clist/get?pn=1&pz={limit}&po={asc}&np=1&fltt=2&invt=2&fid=f62&fs={urllib.parse.quote(fs)}"
        "&fields=f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f128,f136"
    )
    data = _get(url).get("data") or {}
    out = []
    for r in data.get("diff") or []:
        name = r.get("f14") or ""
        if any(x in name for x in _SKIP_BOARD):
            continue
        out.append({
            "code": r.get("f12"),
            "name": r.get("f14"),
            "price": _f(r.get("f2"), None),
            "pct_chg": _f(r.get("f3"), None),
            "main_net": _f(r.get("f62")),
            "main_ratio": _f(r.get("f184"), None),
            "super_net": _f(r.get("f66")),
            "super_ratio": _f(r.get("f69"), None),
            "large_net": _f(r.get("f72")),
            "large_ratio": _f(r.get("f75"), None),
            "medium_net": _f(r.get("f78")),
            "medium_ratio": _f(r.get("f81"), None),
            "small_net": _f(r.get("f84")),
            "small_ratio": _f(r.get("f87"), None),
            "leader": r.get("f128"),
            "leader_pct": _f(r.get("f136"), None),
        })
    return out


def _breadth() -> dict:
    url = (
        f"{_BASE}/ulist.np/get?secids=1.000001,0.399001&fields=f2,f3,f4,f12,f14,f104,f105,f106"
    )
    data = _get(url).get("data") or {}
    up = down = flat = 0
    rows = []
    for r in data.get("diff") or []:
        u, d, f = int(_f(r.get("f104"))), int(_f(r.get("f105"))), int(_f(r.get("f106")))
        up += u; down += d; flat += f
        rows.append({"code": r.get("f12"), "name": r.get("f14"), "pct_chg": _f(r.get("f3"), None)})
    return {"up": up, "down": down, "flat": flat, "rows": rows}


def _db_boards() -> dict:
    out = {}
    meta = {
        "concept": ("concept_code", "concept_name"),
        "industry": ("industry_code", "industry_name"),
    }
    for key, table in (("concept", "concept_board_quotes"), ("industry", "industry_board_quotes")):
        code_col, name_col = meta[key]
        try:
            rows = query(
                f"""SELECT {code_col} AS code, {name_col} AS name, pct_chg,
                           net_inflow * 1e8 AS main_net, lead_stock_name
                    FROM {table} WHERE trade_date=(SELECT MAX(trade_date) FROM {table})
                    ORDER BY net_inflow DESC NULLS LAST LIMIT 30"""
            )
            out[key] = [
                {
                    "code": r.get("code"), "name": r.get("name"),
                    "pct_chg": _f(r.get("pct_chg"), None),
                    "main_net": _f(r.get("main_net")),
                    "leader": r.get("lead_stock_name"),
                }
                for r in rows
                if not any(x in str(r.get("name") or "") for x in _SKIP_BOARD)
            ]
        except Exception:  # noqa: BLE001
            out[key] = []
    return out


def _db_stocks() -> dict:
    """实时个股资金不可用时的最近交易日兜底（TOP 10 流入/流出）。"""
    def fetch(direction: str):
        try:
            sign = ">" if direction == "in" else "<"
            rows = query(
                f"""SELECT d.ts_code, d.pct_chg, d.amount, d.main_force_net,
                          d.large_order_net, s.stock_name
                   FROM daily_quotes d
                   LEFT JOIN stock_basic_info s ON s.ts_code=d.ts_code
                   WHERE d.trade_date=(SELECT MAX(trade_date) FROM daily_quotes)
                     AND d.main_force_net IS NOT NULL AND d.main_force_net {sign} 0
                   ORDER BY d.main_force_net {'DESC' if direction == 'in' else 'ASC'} LIMIT 10"""
            )
            return [{
                "ts_code": r["ts_code"], "name": r.get("stock_name") or r["ts_code"],
                "pct_chg": _f(r.get("pct_chg"), None),
                "net_amount": _f(r.get("main_force_net")),
                "super_net": None,
                "net_ratio": None,
            } for r in rows]
        except Exception:  # noqa: BLE001
            return []
    return {"inflow": fetch("in"), "outflow": fetch("out")}



def _db_breadth() -> dict:
    try:
        r = query(
            """SELECT
                 COUNT(*) FILTER (WHERE pct_chg > 0) AS up,
                 COUNT(*) FILTER (WHERE pct_chg < 0) AS down,
                 COUNT(*) FILTER (WHERE pct_chg = 0) AS flat,
                 MAX(trade_date) AS d
               FROM daily_quotes
               WHERE trade_date=(SELECT MAX(trade_date) FROM daily_quotes)"""
        )[0]
        return {"up": r["up"], "down": r["down"], "flat": r["flat"], "date": str(r["d"])}
    except Exception:  # noqa: BLE001
        return {"up": 0, "down": 0, "flat": 0, "date": None}


@ttl_cache(20)
def dashboard() -> dict:
    now = datetime.now(BEIJING)
    notes = []
    live = True

    with ThreadPoolExecutor(max_workers=6) as pool:
        f_sh = pool.submit(_index_flow, "1.000001")
        f_sz = pool.submit(_index_flow, "0.399001")
        f_ci = pool.submit(_boards, "m:90+t:3", 1)
        f_co = pool.submit(_boards, "m:90+t:3", 0)
        f_ii = pool.submit(_boards, "m:90+t:2", 1)
        f_io = pool.submit(_boards, "m:90+t:2", 0)
        f_br = pool.submit(_breadth)
        f_st = pool.submit(money_flow.main_flow, 100)
        try:
            sh, sz = f_sh.result(), f_sz.result()
            concept_in, concept_out = f_ci.result(), f_co.result()
            industry_in, industry_out = f_ii.result(), f_io.result()
            breadth = f_br.result()
            stocks = f_st.result()
        except Exception as exc:  # noqa: BLE001
            live = False
            notes.append(f"实时资金源部分失败: {exc}")
            sh, sz = {"series": []}, {"series": []}
            concept_in = industry_in = []
            concept_out = industry_out = []
            breadth = _db_breadth()
            stocks = {"inflow": [], "outflow": []}

    # 合并沪深指数分钟资金流
    by_time: dict[str, dict] = {}
    for src in (sh, sz):
        for p in src.get("series") or []:
            slot = by_time.setdefault(p["time"], {"time": p["time"], "main": 0.0, "super": 0.0, "large": 0.0, "medium": 0.0, "small": 0.0})
            for k in ("main", "super", "large", "medium", "small"):
                slot[k] += p.get(k) or 0.0
    series = sorted(by_time.values(), key=lambda x: x["time"])
    latest = series[-1] if series else {}
    summary = {
        "main_net": latest.get("main", 0.0),
        "super_net": latest.get("super", 0.0),
        "large_net": latest.get("large", 0.0),
        "medium_net": latest.get("medium", 0.0),
        "small_net": latest.get("small", 0.0),
    }
    if not series:
        # 东财分钟资金流 502 时，用最近交易日全市场主力/大单净额兜底
        try:
            r = query(
                """SELECT SUM(main_force_net) AS main_net,
                          SUM(large_order_net) AS large_net
                   FROM daily_quotes
                   WHERE trade_date=(SELECT MAX(trade_date) FROM daily_quotes)"""
            )[0]
            summary["main_net"] = _f(r.get("main_net"))
            # daily_quotes.large_order_net 是占比（%），不是金额，不能用它冒充超大单金额
            summary["large_net"] = 0.0
            summary["super_net"] = 0.0
            notes.append("分钟资金流不可用，主力使用最近交易日全市场汇总；大单/超大单金额暂缺")
        except Exception:  # noqa: BLE001
            pass


    if not concept_in and not industry_in:
        db_boards = _db_boards()
        concept_in = sorted(db_boards.get("concept") or [], key=lambda x: -(x.get("main_net") or 0))[:10]
        concept_out = sorted(db_boards.get("concept") or [], key=lambda x: (x.get("main_net") or 0))[:10]
        industry_in = sorted(db_boards.get("industry") or [], key=lambda x: -(x.get("main_net") or 0))[:10]
        industry_out = sorted(db_boards.get("industry") or [], key=lambda x: (x.get("main_net") or 0))[:10]
        live = False
        notes.append("板块资金实时源不可用，已回退最近交易日快照")

    if not breadth.get("up") and not breadth.get("down"):
        breadth = _db_breadth()
        notes.append("涨跌家数实时源不可用，已回退最近交易日")

    # 个股资金：只展示有实际净额的 TOP，标记为样本而非全市场合计
    inflow = [x for x in (stocks.get("inflow") or []) if (x.get("net_amount") or 0) > 0][:10]
    outflow = [x for x in (stocks.get("outflow") or []) if (x.get("net_amount") or 0) < 0][:10]
    if not inflow and not outflow:
        db_stocks = _db_stocks()
        inflow = [x for x in (db_stocks.get("inflow") or []) if (x.get("net_amount") or 0) > 0][:10]
        outflow = [x for x in (db_stocks.get("outflow") or []) if (x.get("net_amount") or 0) < 0][:10]
        if inflow or outflow:
            notes.append("个股实时资金不可用，已回退最近交易日主力净额榜")
        else:
            live = False
            notes.append("个股主力资金源不可用，个股资金榜为空")

    return {
        "as_of": now.isoformat(timespec="seconds"),
        "live": live,
        "market": {
            "summary": summary,
            "series": series[-240:],
            "sh_name": sh.get("name"),
            "sz_name": sz.get("name"),
        },
        "breadth": breadth,
        "sectors": {
            "concept": {"inflow": concept_in, "outflow": concept_out},
            "industry": {"inflow": industry_in, "outflow": industry_out},
        },
        "stocks": {"inflow": inflow, "outflow": outflow},
        "notes": notes,
        "sources": ["eastmoney-push2", "sina-moneyflow", "daily_quotes fallback"],
    }
