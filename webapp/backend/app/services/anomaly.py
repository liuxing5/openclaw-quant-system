# -*- coding: utf-8 -*-
"""C3. 实时异动事件与异动规则。

设计要点
--------
1. **异动是「算出来的」，不是一张人工表**：本模块用当日 ``daily_quotes`` 快照
   + 60 日高点 + 板块行情，实时推导 7 类事件（涨停/跌停/急拉/放量/高换手/新高/板块大涨）。
   所有聚合、排序、限流都在 SQL 端完成，接口只回传分页后的事件列表。
2. **时间口径**：本地库是日线快照，没有逐笔时间戳。事件时间按「当日盘中回放」生成：
   把当日事件按优先级铺在 09:30—14:55 之间，保证时间轴可读、且同一天多次请求结果稳定
   （不用 now()，否则每次刷新时间都会漂移）。``meta.time_basis`` 明确标注该口径。
3. **规则**：``app_anomaly_rules`` 存用户规则（内置 7 条种子规则），扫描时累加运行次数。
4. **缓存**：事件池 30s TTL；「立即扫描」强制重算并返回新增条数（与上次扫描的 id 集合对比）。
"""
from __future__ import annotations

import datetime as dt
import json
import uuid

from ..cache import ttl_cache
from ..db import execute, query, query_one, scalar
from . import live_snapshot, money_flow
from .market import _FX_CTE, latest_trade_date

KIND_LABELS = {
    "limit_up": "涨停",
    "limit_down": "跌停",
    "rapid_rise": "急拉",
    "rapid_fall": "急跌",
    "broken": "炸板",
    "main_inflow": "主力流入",
    "main_outflow": "主力流出",
    "volume_spike": "放量",
    "turnover_high": "高换手",
    "new_high": "阶段新高",
    "board_surge": "板块大涨",
}
KINDS = tuple(KIND_LABELS)
SCOPES = ("symbol", "board", "all_market")

# 每类事件的采样上限：保证单次响应 < 100KB，同时覆盖最极端的一批标的
_LIMITS = {
    "limit_up": 100,
    "broken": 80,
    "main_inflow": 60,
    "main_outflow": 60,
    "limit_down": 60,
    "rapid_rise": 150,
    "rapid_fall": 100,
    "volume_spike": 150,
    "turnover_high": 100,
    "new_high": 80,
    "board_surge": 40,
}

_PRIO = {
    "limit_up": 1,
    "broken": 2,
    "main_inflow": 3,
    "main_outflow": 4,
    "limit_down": 5,
    "rapid_rise": 6,
    "rapid_fall": 7,
    "board_surge": 8,
    "volume_spike": 9,
    "new_high": 10,
    "turnover_high": 11,
}

_DEFAULT_RULES = [
    {
        "name": "涨停封板",
        "scope": "symbol",
        "kind": "limit_up",
        "params": {"pct_chg": 9.9, "window_min": 1},
    },
    {
        "name": "跌停打开风险",
        "scope": "symbol",
        "kind": "limit_down",
        "params": {"pct_chg": -9.9, "window_min": 1},
    },
    {
        "name": "盘中急拉",
        "scope": "symbol",
        "kind": "rapid_rise",
        "params": {"pct_chg": 7, "volume_ratio": 1.8, "window_min": 5},
    },
    {
        "name": "异常放量",
        "scope": "symbol",
        "kind": "volume_spike",
        "params": {"volume_ratio": 2.5, "window_min": 5},
    },
    {
        "name": "高换手活跃",
        "scope": "symbol",
        "kind": "turnover_high",
        "params": {"turnover_rate": 15, "window_min": 30},
    },
    {
        "name": "创 60 日新高",
        "scope": "symbol",
        "kind": "new_high",
        "params": {"days": 60, "threshold": 0.1},
    },
    {
        "name": "板块整体大涨",
        "scope": "board",
        "kind": "board_surge",
        "params": {"pct_chg": 4, "top_n": 10},
    },
]


# ---------------------------------------------------------------------------
# 事件池
# ---------------------------------------------------------------------------

_EVENT_SQL = (
    _FX_CTE.format(where="dq.trade_date = %s")
    + """
, nh AS (
  SELECT d2.ts_code, MAX(d2.high) AS high60
  FROM daily_quotes d2
  WHERE d2.trade_date <= %s
    AND d2.trade_date > %s::date - INTERVAL '90 days'
  GROUP BY d2.ts_code
), pool AS (
  SELECT f.ts_code, 'limit_up'::text AS kind, f.close AS price, f.pct_chg,
         f.turnover_rate, f.volume_ratio, f.amount, NULL::numeric AS ref_high, 1 AS prio
  FROM fx f WHERE f.is_lu
  UNION ALL
  SELECT f.ts_code, 'limit_down', f.close, f.pct_chg,
         f.turnover_rate, f.volume_ratio, f.amount, NULL, 2
  FROM fx f WHERE f.is_ld
  UNION ALL
  SELECT f.ts_code, 'rapid_rise', f.close, f.pct_chg,
         f.turnover_rate, f.volume_ratio, f.amount, NULL, 3
  FROM fx f WHERE f.pct_chg >= 7 AND NOT f.is_lu
  UNION ALL
  SELECT f.ts_code, 'volume_spike', f.close, f.pct_chg,
         f.turnover_rate, f.volume_ratio, f.amount, NULL, 5
  FROM fx f WHERE f.volume_ratio >= 2.5
  UNION ALL
  SELECT f.ts_code, 'turnover_high', f.close, f.pct_chg,
         f.turnover_rate, f.volume_ratio, f.amount, NULL, 7
  FROM fx f WHERE f.turnover_rate >= 15
  UNION ALL
  SELECT f.ts_code, 'new_high', f.close, f.pct_chg,
         f.turnover_rate, f.volume_ratio, f.amount, n.high60, 6
  FROM fx f
  JOIN nh n ON n.ts_code = f.ts_code
  WHERE n.high60 > 0 AND f.close >= n.high60 * 0.999 AND f.pct_chg > 0
)
SELECT p.ts_code, COALESCE(sb.stock_name, p.ts_code) AS name, p.kind,
       p.price, p.pct_chg, p.turnover_rate, p.volume_ratio, p.amount, p.ref_high
FROM pool p
LEFT JOIN stock_basic_info sb ON sb.ts_code = p.ts_code
ORDER BY p.prio, p.pct_chg DESC NULLS LAST, p.ts_code
LIMIT 400
"""
)

_BOARD_SQL = """
SELECT b.board_type, b.code, b.name, b.pct_chg, b.turnover_rate,
       b.net_inflow, b.lead_stock_name
FROM (
  SELECT 'concept'::text AS board_type, concept_code AS code, concept_name AS name,
         pct_chg, turnover_rate, net_inflow, lead_stock_name
  FROM concept_board_quotes WHERE trade_date = %s AND pct_chg >= 4
  UNION ALL
  SELECT 'industry'::text, industry_code, industry_name,
         pct_chg, turnover_rate, net_inflow, lead_stock_name
  FROM industry_board_quotes WHERE trade_date = %s AND pct_chg >= 4
) b
ORDER BY b.pct_chg DESC NULLS LAST
LIMIT 30
"""


def _f(v, default=None):
    try:
        return default if v is None else float(v)
    except (TypeError, ValueError):
        return default


def _int(v, default=0):
    try:
        return default if v is None else int(v)
    except (TypeError, ValueError):
        return default


def _fmt_amount(v) -> str:
    v = _f(v, 0) or 0
    if v >= 1e8:
        return f"{v / 1e8:.2f} 亿"
    if v >= 1e4:
        return f"{v / 1e4:.0f} 万"
    return f"{v:.0f}"


def _detail(kind: str, row: dict) -> str:
    pct = _f(row.get("pct_chg"))
    tr = _f(row.get("turnover_rate"))
    vr = _f(row.get("volume_ratio"))
    amt = _f(row.get("amount"))
    if kind == "limit_up":
        parts = ["封涨停"]
        if tr is not None:
            parts.append(f"换手 {tr:.1f}%")
        if amt:
            parts.append(f"成交 {_fmt_amount(amt)}")
        return " · ".join(parts)
    if kind == "limit_down":
        return "封跌停" + (f" · 换手 {tr:.1f}%" if tr is not None else "")
    if kind == "rapid_rise":
        parts = [f"涨幅 {pct:.2f}%" if pct is not None else "快速拉升"]
        if vr is not None:
            parts.append(f"量比 {vr:.2f}")
        if amt:
            parts.append(f"成交 {_fmt_amount(amt)}")
        return " · ".join(parts)
    if kind == "rapid_fall":
        parts = [f"跌幅 {pct:.2f}%" if pct is not None else "快速跳水"]
        if vr is not None:
            parts.append(f"量比 {vr:.2f}")
        if amt:
            parts.append(f"成交 {_fmt_amount(amt)}")
        return " · ".join(parts)
    if kind == "broken":
        return "炸板（触及涨停后打开）" + (f" · 换手 {tr:.1f}%" if tr is not None else "")
    if kind in ("main_inflow", "main_outflow"):
        net = _f(row.get("net_amount"))
        sup = _f(row.get("super_net"))
        parts = []
        if net is not None:
            parts.append(f"主力净{'流入' if net >= 0 else '流出'} {_fmt_amount(abs(net))}")
        if sup is not None:
            parts.append(f"超大单 {_fmt_amount(abs(sup))}")
        ratio = _f(row.get("net_ratio"))
        if ratio is not None:
            parts.append(f"占比 {ratio:+.2f}%")
        return " · ".join(parts) or "主力资金异动"
    if kind == "volume_spike":
        parts = [f"量比 {vr:.2f}" if vr is not None else "成交量异常"]
        if pct is not None:
            parts.append(f"涨幅 {pct:.2f}%")
        if amt:
            parts.append(f"成交 {_fmt_amount(amt)}")
        return " · ".join(parts)
    if kind == "turnover_high":
        return f"换手率 {tr:.1f}%" + (f" · 涨幅 {pct:.2f}%" if pct is not None else "")
    if kind == "new_high":
        hi = _f(row.get("ref_high"))
        return "突破 60 日高点" + (f"（前高 {hi:.2f}）" if hi else "")
    return ""


def _session_time(day: dt.date, idx: int, total: int) -> str:
    """把事件铺到 09:30—14:55：同一交易日、同一排序下结果稳定，便于回放。"""
    span = 325  # 09:30 -> 14:55 的分钟数
    step = span / max(total, 1)
    minute = int(idx * step)
    base = dt.datetime.combine(day, dt.time(9, 30))
    return (base + dt.timedelta(minutes=minute)).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 实时事件池（腾讯全市场快照）
# ---------------------------------------------------------------------------

@ttl_cache(600)
def _high60(day: dt.date | None) -> dict[str, float]:
    """最近交易日之前的 60 个交易日最高价（用于「阶段新高」判定）。"""
    if not day:
        return {}
    rows = query(
        """
        SELECT ts_code, MAX(high) AS h
        FROM daily_quotes
        WHERE trade_date < %s
          AND trade_date > %s::date - INTERVAL '90 days'
        GROUP BY ts_code
        """,
        (day, day),
    )
    return {r["ts_code"]: _f(r["h"]) for r in rows if r.get("ts_code")}


@ttl_cache(60)
def _live_boards() -> list[dict]:
    """板块大涨：读板块快照的最新交易日（概念/行业表分别取 MAX）。"""
    rows = query(
        """
        SELECT 'concept'::text AS board_type, concept_code AS code, concept_name AS name,
               pct_chg, lead_stock_name
        FROM concept_board_quotes
        WHERE trade_date = (SELECT MAX(trade_date) FROM concept_board_quotes)
          AND pct_chg >= 4
        UNION ALL
        SELECT 'industry'::text, industry_code, industry_name,
               pct_chg, lead_stock_name
        FROM industry_board_quotes
        WHERE trade_date = (SELECT MAX(trade_date) FROM industry_board_quotes)
          AND pct_chg >= 4
        ORDER BY pct_chg DESC NULLS LAST
        LIMIT 30
        """
    )
    out = []
    for r in rows:
        pct = _f(r.get("pct_chg")) or 0
        lead = r.get("lead_stock_name")
        detail = f"{'概念' if r.get('board_type') == 'concept' else '行业'}板块涨幅 {pct:.2f}%"
        if lead:
            detail += f" · 领涨 {lead}"
        out.append(
            {
                "id": f"live:board_surge:{r.get('code')}",
                "ts_code": r.get("code"),
                "name": r.get("name"),
                "kind": "board_surge",
                "kind_label": KIND_LABELS["board_surge"],
                "price": None,
                "pct_chg": round(pct, 2),
                "detail": detail,
                "scope": "board",
                "board": "板块",
                "session": "盘中",
                "is_st": False,
                "occurred_at": None,
            }
        )
    return out


def _build_live_pool(scope: str = "all_market") -> dict | None:
    """由全市场实时快照推导异动。快照不足 1000 只时返回 None，交给日线兜底。"""
    try:
        snap = live_snapshot.get_snapshot()
    except Exception:  # noqa: BLE001 行情源异常不阻断页面
        return None
    items = snap.get("items") or []
    if len(items) < 1000:
        return None

    day = latest_trade_date()
    highs = _high60(day)
    buckets: dict[str, list[dict]] = {k: [] for k in KINDS}

    def board_of(code: str) -> str:
        pure = code.split(".")[0]
        if pure.startswith(("688", "689")):
            return "科创板"
        if pure.startswith(("300", "301", "302")):
            return "创业板"
        if pure.startswith(("4", "8", "920")):
            return "北交所"
        return "主板"

    for it in items:
        price = _f(it.get("price"))
        pct = _f(it.get("pct_chg"))
        if not price or pct is None:
            continue
        lup = _f(it.get("limit_up_price"))
        ldp = _f(it.get("limit_down_price"))
        tr = _f(it.get("turnover_rate"))
        vr = _f(it.get("volume_ratio"))
        high = _f(it.get("high"))
        low = _f(it.get("low"))
        pre = _f(it.get("pre_close"))
        code = it["ts_code"]
        occurred = it.get("as_of") or snap.get("as_of")
        amplitude = round((high - low) / pre * 100, 2) if high and low and pre else None
        hm = str(occurred or "")[11:16]
        session = "竞价" if hm and hm < "09:30" else ("盘中" if hm and hm <= "15:00" else "偏移")

        def add(kind: str):
            row = dict(it)
            row["ref_high"] = highs.get(code)
            buckets[kind].append(
                {
                    "id": f"live:{kind}:{code}",
                    "ts_code": code,
                    "name": it.get("name") or code,
                    "kind": kind,
                    "kind_label": KIND_LABELS[kind],
                    "price": round(price, 2),
                    "pct_chg": round(pct, 2),
                    "volume_ratio": round(vr, 2) if vr is not None else None,
                    "turnover_rate": round(tr, 2) if tr is not None else None,
                    "amplitude": amplitude,
                    "board": board_of(code),
                    "is_st": bool(it.get("is_st")),
                    "session": session,
                    "detail": _detail(kind, row),
                    "scope": "symbol",
                    "occurred_at": occurred,
                }
            )

        is_lu = bool(lup and price >= lup - 0.005)
        is_ld = bool(ldp and price <= ldp + 0.005)
        touched = bool(lup and high and high >= lup - 0.005)
        if is_lu:
            add("limit_up")
        if is_ld:
            add("limit_down")
        if touched and not is_lu:
            add("broken")
        if pct >= 7 and not is_lu:
            add("rapid_rise")
        if pct <= -7 and not is_ld:
            add("rapid_fall")
        if vr is not None and vr >= 2.5:
            add("volume_spike")
        if tr is not None and tr >= 15:
            add("turnover_high")
        h60 = highs.get(code)
        if h60 and price >= h60 * 0.999 and pct > 0:
            add("new_high")

    all_items: list[dict] = []
    for kind in KINDS:
        # 每类按涨跌幅取前 N，避免某类极端行情把响应撑爆
        all_items.extend(
            sorted(buckets[kind], key=lambda x: -(x.get("pct_chg") or 0))[:_LIMITS[kind]]
        )

    if scope != "symbol":
        boards = _live_boards()
        for b in boards:
            b["occurred_at"] = snap.get("as_of")
        all_items.extend(boards)
    if scope == "board":
        all_items = [x for x in all_items if x["scope"] == "board"]

    # 主力资金流（新浪免费榜）：与腾讯快照互相独立，失败不影响其他信号
    if scope != "board":
        try:
            flow = money_flow.main_flow()
        except Exception:  # noqa: BLE001
            flow = {}
        occurred = snap.get("as_of")
        hm = str(occurred or "")[11:16]
        flow_session = "竞价" if hm and hm < "09:30" else ("盘中" if hm and hm <= "15:00" else "偏移")
        for kind, key in (("main_inflow", "inflow"), ("main_outflow", "outflow")):
            for row in (flow.get(key) or [])[: _LIMITS[kind]]:
                code = row["ts_code"]
                all_items.append(
                    {
                        "id": f"live:{kind}:{code}",
                        "ts_code": code,
                        "name": row.get("name") or code,
                        "kind": kind,
                        "kind_label": KIND_LABELS[kind],
                        "price": row.get("price"),
                        "pct_chg": row.get("pct_chg"),
                        "volume_ratio": None,
                        "turnover_rate": None,
                        "amplitude": None,
                        "net_amount": row.get("net_amount"),
                        "super_net": row.get("super_net"),
                        "net_ratio": row.get("net_ratio"),
                        "board": board_of(code),
                        "is_st": "ST" in str(row.get("name") or ""),
                        "session": flow_session,
                        "detail": _detail(kind, row),
                        "scope": "symbol",
                        "occurred_at": occurred,
                    }
                )

    all_items.sort(key=lambda x: (_PRIO.get(x["kind"], 99), -(x.get("pct_chg") or 0)))
    by_kind = {k: 0 for k in KINDS}
    for it in all_items:
        by_kind[it["kind"]] = by_kind.get(it["kind"], 0) + 1
    return {
        "trade_date": str(day) if day else None,
        "items": all_items,
        "by_kind": by_kind,
        "as_of": snap.get("as_of"),
        "source": "tencent-live",
        "count": len(items),
    }


@ttl_cache(30)
def event_pool(day: dt.date, scope: str = "all_market") -> dict:
    """当日异动事件池（未分页）：个股事件 + 板块事件。"""
    rows = query(_EVENT_SQL, (day, day, day))
    items: list[dict] = []
    for r in rows:
        kind = r["kind"]
        code = r["ts_code"]
        items.append(
            {
                "id": f"{day}:{kind}:{code}",
                "ts_code": code,
                "name": r.get("name") or code,
                "kind": kind,
                "kind_label": KIND_LABELS.get(kind, kind),
                "price": round(_f(r.get("price"), 0) or 0, 2) or None,
                "pct_chg": round(_f(r.get("pct_chg"), 0) or 0, 2),
                "detail": _detail(kind, r),
                "scope": "symbol",
                "trade_date": str(day),
            }
        )

    if scope != "symbol":
        boards = query(_BOARD_SQL, (day, day))
        for r in boards:
            pct = _f(r.get("pct_chg")) or 0
            lead = r.get("lead_stock_name")
            detail = f"{'概念' if r.get('board_type') == 'concept' else '行业'}板块涨幅 {pct:.2f}%"
            if lead:
                detail += f" · 领涨 {lead}"
            items.append(
                {
                    "id": f"{day}:board_surge:{r.get('code')}",
                    "ts_code": r.get("code"),
                    "name": r.get("name"),
                    "kind": "board_surge",
                    "kind_label": KIND_LABELS["board_surge"],
                    "price": None,
                    "pct_chg": round(pct, 2),
                    "detail": detail,
                    "scope": "board",
                    "trade_date": str(day),
                }
            )

    items.sort(key=lambda x: (_PRIO.get(x["kind"], 99), -(x["pct_chg"] or 0)))
    items = items[:400]
    total = len(items)
    for i, it in enumerate(items):
        it["occurred_at"] = _session_time(day, i, total)

    by_kind = {k: 0 for k in KINDS}
    for it in items:
        by_kind[it["kind"]] = by_kind.get(it["kind"], 0) + 1
    return {"trade_date": str(day), "items": items, "by_kind": by_kind}


@ttl_cache(60)
def _holding_codes() -> set[str]:
    try:
        rows = query(
            "SELECT ts_code FROM app_watchlist_items UNION SELECT ts_code FROM app_holdings"
        )
        return {r["ts_code"] for r in rows if r.get("ts_code")}
    except Exception:  # noqa: BLE001
        return set()


def events(
    day: dt.date | None = None,
    scope: str = "all_market",
    page: int = 1,
    size: int = 20,
    signal: str | None = None,
    board: str | None = None,
    direction: str | None = None,
    only_holdings: bool = False,
    session: str | None = None,
) -> dict:
    """分页事件流：盘中优先用实时快照推导，失败/指定日期时退回日线快照。"""
    explicit_day = day
    day = day or latest_trade_date()
    scope = (scope or "all_market").strip()
    page = max(1, int(page or 1))
    size = max(1, min(int(size or 20), 100))
    if not day:
        return {"items": [], "total": 0, "page": page, "size": size, "meta": {"trade_date": None}}

    live = _build_live_pool(scope) if explicit_day is None else None
    pool = live or event_pool(day, scope)
    rows = list(pool["items"])

    sig = (signal or "").strip()
    if sig and sig not in ("all", "全部"):
        wanted = {x.strip() for x in sig.split(",") if x.strip()}
        rows = [x for x in rows if x["kind"] in wanted]
    bd = (board or "").strip()
    if bd and bd not in ("全部", "all"):
        if bd == "ST":
            rows = [x for x in rows if x.get("is_st")]
        elif bd in ("板块", "board"):
            rows = [x for x in rows if x.get("scope") == "board"]
        else:
            rows = [x for x in rows if x.get("board") == bd]
    dr = (direction or "").strip()
    if dr in ("up", "down"):
        rows = [x for x in rows if (x.get("pct_chg") or 0) > 0] if dr == "up" else [
            x for x in rows if (x.get("pct_chg") or 0) < 0
        ]
    ss = (session or "").strip()
    if ss and ss not in ("全部", "all"):
        rows = [x for x in rows if x.get("session") == ss]
    if only_holdings:
        codes = _holding_codes()
        rows = [x for x in rows if x.get("ts_code") in codes]

    rows.sort(key=lambda x: x.get("occurred_at") or "", reverse=True)
    start = (page - 1) * size
    last_scan = _last_scan()
    return {
        "items": rows[start : start + size],
        "total": len(rows),
        "page": page,
        "size": size,
        "trade_date": str(day),
        "as_of": pool.get("as_of") or last_scan.get("scanned_at"),
        "by_kind": pool["by_kind"],
        "last_scan_at": last_scan.get("scanned_at"),
        "meta": {
            "source": pool.get("source") or "daily_quotes + concept/industry_board_quotes",
            "live": bool(live),
            "snapshot_count": pool.get("count"),
            "as_of": pool.get("as_of") or last_scan.get("scanned_at"),
            "time_basis": "tick_snapshot" if live else "session_replay",
            "time_note": (
                "事件时间取实时行情快照中该标的最近一笔成交时间"
                if live
                else "日线快照无逐笔时间，事件时间按当日 09:30—14:55 稳定回放"
            ),
            "scope": scope,
            "filters": {
                "signal": sig or None,
                "board": bd or None,
                "direction": dr or None,
                "only_holdings": bool(only_holdings),
                "session": ss or None,
            },
        },
    }


def scan(scope: str = "all_market", day: dt.date | None = None) -> dict:
    """立即重算：刷新实时快照 / 日线事件池，落扫描时间并对比新增事件。"""
    explicit_day = day
    day = day or latest_trade_date()
    if not day:
        return {"scanned": 0, "new": 0, "scanned_at": None, "trade_date": None}
    scope = (scope or "all_market").strip()

    live = None
    if explicit_day is None:
        try:
            live_snapshot.refresh_now()  # 强制拉一张最新快照
        except Exception:  # noqa: BLE001
            pass
        live = _build_live_pool(scope)

    pool = live or event_pool.refresh(day, scope)
    ids = [it["id"] for it in pool["items"]]
    prev = _last_scan()
    prev_ids = set(prev.get("ids") or [])
    new_ids = [i for i in ids if i not in prev_ids]

    now = dt.datetime.now().isoformat(timespec="seconds")
    _save_last_scan({"scanned_at": now, "trade_date": str(day), "ids": ids})
    try:
        execute(
            "UPDATE app_anomaly_rules SET run_count = run_count + 1, last_run_at = NOW() "
            "WHERE enabled = true"
        )
    except Exception:  # noqa: BLE001 规则计数失败不影响扫描结果
        pass
    return {
        "scanned": len(ids),
        "new": len(new_ids),
        "new_ids": new_ids[:50],
        "scanned_at": now,
        "trade_date": str(day),
        "by_kind": pool["by_kind"],
        "scope": scope,
        "source": pool.get("source") or "daily_quotes",
        "live": bool(live),
        "as_of": pool.get("as_of") or now,
    }


# ---------------------------------------------------------------------------
# 扫描状态（app_settings 单行 JSON）
# ---------------------------------------------------------------------------

_SCAN_KEY = "anomaly:last_scan"


def _last_scan() -> dict:
    try:
        row = query_one("SELECT value FROM app_settings WHERE key = %s", (_SCAN_KEY,))
        return (row or {}).get("value") or {}
    except Exception:  # noqa: BLE001
        return {}


def _save_last_scan(value: dict) -> None:
    try:
        execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (%s, %s::jsonb, NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
            (_SCAN_KEY, json.dumps(value, ensure_ascii=False)),
        )
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 规则 CRUD
# ---------------------------------------------------------------------------

def _seed() -> None:
    try:
        n = _int(scalar("SELECT COUNT(*) FROM app_anomaly_rules") or 0)
        if n:
            return
    except Exception:  # noqa: BLE001
        return
    for r in _DEFAULT_RULES:
        try:
            execute(
                """
                INSERT INTO app_anomaly_rules (name, scope, kind, params, enabled)
                VALUES (%s, %s, %s, %s::jsonb, true)
                """,
                (
                    r["name"],
                    r["scope"],
                    r["kind"],
                    json.dumps(r["params"], ensure_ascii=False),
                ),
            )
        except Exception:  # noqa: BLE001
            pass


def _ser(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "scope": row.get("scope"),
        "kind": row.get("kind"),
        "kind_label": KIND_LABELS.get(row.get("kind"), row.get("kind")),
        "params": row.get("params") or {},
        "enabled": bool(row.get("enabled")),
        "run_count": _int(row.get("run_count")),
        "last_run_at": row["last_run_at"].isoformat() if row.get("last_run_at") else None,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def list_rules(page: int = 1, size: int = 50) -> dict:
    _seed()
    page = max(1, int(page or 1))
    size = max(1, min(int(size or 50), 200))
    total = _int(scalar("SELECT COUNT(*) FROM app_anomaly_rules") or 0)
    rows = query(
        """
        SELECT id, name, scope, kind, params, enabled, run_count,
               last_run_at, created_at, updated_at
        FROM app_anomaly_rules
        ORDER BY enabled DESC, created_at ASC
        LIMIT %s OFFSET %s
        """,
        (size, (page - 1) * size),
    )
    return {
        "items": [_ser(r) for r in rows],
        "total": total,
        "page": page,
        "size": size,
        "kinds": [{"value": k, "label": KIND_LABELS[k]} for k in KINDS],
        "scopes": [{"value": s, "label": {"symbol": "个股", "board": "板块", "all_market": "全市场"}[s]} for s in SCOPES],
    }


def create_rule(payload: dict) -> dict:
    _seed()
    payload = payload or {}
    name = str(payload.get("name") or "").strip()
    kind = str(payload.get("kind") or "").strip()
    scope = str(payload.get("scope") or "symbol").strip()
    if not name:
        raise ValueError("规则名称不能为空")
    if kind not in KINDS:
        raise ValueError("kind 必须是 " + "/".join(KINDS) + " 之一")
    if scope not in SCOPES:
        raise ValueError("scope 必须是 " + "/".join(SCOPES) + " 之一")
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    enabled = bool(payload.get("enabled", True))
    rid = str(uuid.uuid4())
    # db.query() 只读（结束 rollback），写操作走 execute()
    execute(
        """
        INSERT INTO app_anomaly_rules (id, name, scope, kind, params, enabled)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s)
        """,
        (rid, name, scope, kind, json.dumps(params, ensure_ascii=False), enabled),
    )
    return get_rule(rid)


def get_rule(rule_id) -> dict | None:
    try:
        uid = str(uuid.UUID(str(rule_id)))
    except (ValueError, TypeError, AttributeError):
        return None
    row = query_one(
        """
        SELECT id, name, scope, kind, params, enabled, run_count,
               last_run_at, created_at, updated_at
        FROM app_anomaly_rules WHERE id = %s
        """,
        (uid,),
    )
    return _ser(row) if row else None


def update_rule(rule_id: str, payload: dict) -> dict | None:
    payload = payload or {}
    sets, vals = [], []
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("规则名称不能为空")
        sets.append("name = %s")
        vals.append(name)
    if "kind" in payload:
        kind = str(payload.get("kind") or "").strip()
        if kind not in KINDS:
            raise ValueError("kind 必须是 " + "/".join(KINDS) + " 之一")
        sets.append("kind = %s")
        vals.append(kind)
    if "scope" in payload:
        scope = str(payload.get("scope") or "").strip()
        if scope not in SCOPES:
            raise ValueError("scope 必须是 " + "/".join(SCOPES) + " 之一")
        sets.append("scope = %s")
        vals.append(scope)
    if "params" in payload:
        params = payload.get("params")
        if not isinstance(params, dict):
            raise ValueError("params 必须是对象")
        sets.append("params = %s::jsonb")
        vals.append(json.dumps(params, ensure_ascii=False))
    if "enabled" in payload:
        sets.append("enabled = %s::boolean")
        vals.append(bool(payload.get("enabled")))
    if not sets:
        raise ValueError("没有可更新的字段")
    sets.append("updated_at = NOW()")
    vals.append(str(rule_id))
    execute(f"UPDATE app_anomaly_rules SET {', '.join(sets)} WHERE id = %s", vals)
    return get_rule(rule_id)


def delete_rule(rule_id: str) -> bool:
    try:
        uid = str(uuid.UUID(str(rule_id)))
    except (ValueError, TypeError, AttributeError):
        return False
    return execute("DELETE FROM app_anomaly_rules WHERE id = %s", (uid,)) > 0
