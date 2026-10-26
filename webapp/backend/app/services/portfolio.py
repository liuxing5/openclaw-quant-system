# -*- coding: utf-8 -*-
"""B1 业务层：自选分组 / 持仓 / 价格提醒。

为什么把这些逻辑下沉到 service 而不是写在路由里
------------------------------------------------
1. 路由层只负责参数解析与 HTTP 语义，业务函数可以在**不起 uvicorn** 的情况下
   用 ``python -c`` 直接调用做联调、性能和清理测试。
2. 行情合并（实时优先 -> 日线降级）只写一遍，自选行情列与持仓列表共用，
   保证两处的 price / pct_chg / 昨收口径完全一致。
3. 提醒的触发检测是**惰性**的：本系统没有后台线程，谁读谁算，
   算完立刻回写 ``app_alerts``（trigger_count / last_triggered_at / last_message），
   于是 ``GET /alerts`` 与 ``GET /alerts/triggered`` 天然自带最新触发状态，
   且整批提醒只做**一次**行情快照（绝非每条一次）。

单用户本地系统：写入的 ``user_id`` 一律为 ``None``，查询不过滤 ``user_id``。
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from ..db import execute, query, query_one, scalar
from ..routers.system import get_setting, set_setting
from ..services.realtime import norm_code, snapshot_rows

# 契约 B1 的 alert_type 枚举（校验用白名单）
ALERT_TYPES = (
    "price_above",
    "price_below",
    "pct_chg_above",
    "pct_chg_below",
    "limit_up",
    "limit_down",
    "break_cost",
    "amount_above",
)

# 持仓破位提醒设置的 app_settings key（GET/PUT /watchlists/holding-alert-settings）
HOLDING_ALERT_KEY = "holding_alert_settings"
DEFAULT_BREAK_PCT = 5.0  # 破位阈值默认 5%（原站文案：跌破成本价的 5%）

# 实时行情源（services/realtime.py 的优先级顺序），非这些值即视为降级到日线
LIVE_SOURCES = ("sina", "tencent", "akshare")

# 可写字段白名单：动态拼 SET 子句时只允许这些列，避免把 SQL 片段交给调用方
_ALERT_UPDATABLE = ("alert_type", "ts_code", "stock_name", "threshold", "direction", "enabled", "scope")
_HOLDING_UPDATABLE = ("ts_code", "stock_name", "qty", "cost", "path", "entry_date", "notes")


# ---------------------------------------------------------------- 基础工具
def _ser(v):
    """psycopg2 会给出 Decimal/date/datetime/UUID，JSON 序列化前统一转换。"""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dict):
        return {k: _ser(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_ser(x) for x in v]
    return str(v)


def _num(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _r2(v, nd: int = 2):
    """金额/百分比统一保留 2 位；None 要原样保留（不能算出来就别瞎给 0）。"""
    return None if v is None else round(float(v), nd)


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _page(page, size):
    """分页参数兜底：越界值不报错而是夹到合法区间（本地单用户，宁可多给不可 500）。"""
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 20
    page = max(page, 1)
    size = min(max(size, 1), 200)
    return page, size, (page - 1) * size


def _norm(code) -> str:
    return norm_code(code or "")


def _names(codes: list[str]) -> dict[str, str]:
    """批量取股票名（一次查询）；底表异常时静默返回空，不影响主流程。"""
    codes = [c for c in dict.fromkeys(codes) if c]
    if not codes:
        return {}
    try:
        rows = query("SELECT ts_code, stock_name FROM stock_basic_info WHERE ts_code = ANY(%s)", (codes,))
    except Exception:  # noqa: BLE001 名称是锦上添花，查不到不该让接口 500
        return {}
    return {r["ts_code"]: r["stock_name"] for r in rows if r.get("stock_name")}


def _quotes_for(codes: list[str]) -> dict[str, dict]:
    """**一次**行情快照，返回以归一化 ts_code 为键的字典（实时优先、日线兜底）。"""
    norm = [c for c in dict.fromkeys(_norm(c) for c in codes if c)]
    if not norm:
        return {}
    return {r.get("ts_code"): r for r in snapshot_rows(norm)}


def _meta(codes: list[str], src_count: dict, missing: int) -> dict:
    """行情列的 meta：source / degraded / warning（照 spec/pages/watchlist.md 结构）。"""
    refreshed = len(codes) - missing
    if not codes:
        return {"source": "db", "degraded": False, "warning": None, "refreshed": 0, "missing": 0}
    source = max(src_count.items(), key=lambda kv: kv[1])[0] if src_count else "db"
    live = any(s in LIVE_SOURCES for s in src_count)
    if not live:
        return {
            "source": source,
            "degraded": True,
            "warning": f"实时行情不可用，已降级为最近交易日收盘价 · 已刷新 {refreshed} 个标的 · 缺失 {missing} 个",
            "refreshed": refreshed,
            "missing": missing,
        }
    if missing:
        return {
            "source": source,
            "degraded": True,
            "warning": f"已刷新 {refreshed} 个标的 · 缺失 {missing} 个",
            "refreshed": refreshed,
            "missing": missing,
        }
    return {"source": source, "degraded": False, "warning": None, "refreshed": refreshed, "missing": 0}


# ---------------------------------------------------------------- 自选分组
_GROUP_SELECT = """
SELECT g.id, g.name, g.sort, g.is_shared, g.created_at, g.updated_at,
       COALESCE(c.cnt, 0) AS item_count
FROM app_watchlist_groups g
LEFT JOIN (
    SELECT group_id, COUNT(*) AS cnt FROM app_watchlist_items GROUP BY group_id
) c ON c.group_id = g.id
"""


def list_groups() -> list[dict]:
    """分组列表带 item_count：计数用子查询一次算完，前端不再逐组 COUNT（防 N+1）。"""
    rows = query(_GROUP_SELECT + " ORDER BY g.sort ASC, g.created_at ASC, g.id ASC")
    return [_ser(r) for r in rows]


def get_group(gid) -> dict | None:
    row = query_one(_GROUP_SELECT + " WHERE g.id = %s", (gid,))
    return _ser(row) if row else None


def default_group() -> dict:
    """默认分组：不存在就自动建「默认分组」（契约要求，前端首屏直接用）。"""
    row = query_one(
        _GROUP_SELECT + " WHERE g.name = %s ORDER BY g.sort ASC, g.created_at ASC LIMIT 1",
        ("默认分组",),
    )
    if row:
        return _ser(row)
    # psycopg2 没注册 uuid 适配器，统一以字符串传参（PG 会推断成 uuid）
    gid = str(uuid.uuid4())
    execute(
        """
        INSERT INTO app_watchlist_groups (id, user_id, name, sort, is_shared)
        VALUES (%s, NULL, %s, 0, false)
        """,
        (gid, "默认分组"),
    )
    return get_group(gid) or _ser(
        {"id": gid, "name": "默认分组", "sort": 0, "is_shared": False, "item_count": 0}
    )


def create_group(name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("分组名称不能为空")
    if len(name) > 64:
        raise ValueError("分组名称最长 64 字")
    gid = str(uuid.uuid4())
    execute(
        """
        INSERT INTO app_watchlist_groups (id, user_id, name, sort, is_shared)
        SELECT %s, NULL, %s, COALESCE(MAX(sort), 0) + 1, false FROM app_watchlist_groups
        """,
        (gid, name),
    )
    return get_group(gid) or {}


def rename_group(gid, name: str) -> dict | None:
    name = (name or "").strip()
    if not name:
        raise ValueError("分组名称不能为空")
    if not execute("UPDATE app_watchlist_groups SET name = %s, updated_at = NOW() WHERE id = %s", (name, gid)):
        return None
    return get_group(gid)


def delete_group(gid) -> bool:
    """删组先删标的（表上没有外键，必须自己保证不留孤儿行）。"""
    execute("DELETE FROM app_watchlist_items WHERE group_id = %s", (gid,))
    return execute("DELETE FROM app_watchlist_groups WHERE id = %s", (gid,)) > 0


# ---------------------------------------------------------------- 自选标的
_ITEM_COLS = "id, group_id, ts_code, stock_name, sort, note, created_at"


def list_items(gid) -> dict:
    rows = query(
        f"""
        SELECT {_ITEM_COLS}
        FROM app_watchlist_items WHERE group_id = %s
        ORDER BY sort ASC, created_at ASC, id ASC
        """,
        (gid,),
    )
    no_name = [r for r in rows if not r.get("stock_name")]
    if no_name:
        names = _names([r["ts_code"] for r in no_name])
        for r in no_name:
            r["stock_name"] = names.get(r["ts_code"])
    return {"items": [_ser(r) for r in rows], "total": len(rows)}


def add_item(gid, ts_code: str, stock_name: str | None = None) -> dict:
    """加入分组；(group_id, ts_code) 唯一约束保证**重复添加幂等**且仍返回 200。

    注意这里必须走 ``execute()`` 而不是 ``query_one()``：db.query() 归还连接时会
    rollback，INSERT ... RETURNING 用它写不进库（第一版就栽在这）。
    """
    code = _norm(ts_code)
    if not code:
        raise ValueError("ts_code 不能为空")
    name = (stock_name or "").strip() or _names([code]).get(code)
    execute(
        f"""
        INSERT INTO app_watchlist_items (id, group_id, ts_code, stock_name, sort)
        SELECT %s, %s, %s, %s,
               COALESCE((SELECT MAX(sort) FROM app_watchlist_items WHERE group_id = %s), 0) + 1
        ON CONFLICT (group_id, ts_code) DO NOTHING
        """,
        (str(uuid.uuid4()), gid, code, name, gid),
    )
    row = query_one(
        f"SELECT {_ITEM_COLS} FROM app_watchlist_items WHERE group_id = %s AND ts_code = %s",
        (gid, code),
    )
    return _ser(row) or {}


def remove_item(gid, item_id) -> bool:
    return execute(
        "DELETE FROM app_watchlist_items WHERE id = %s AND group_id = %s", (item_id, gid)
    ) > 0


# ---------------------------------------------------------------- 行情合并
def group_quotes(gid, page=1, size=20, refresh: str | None = "auto") -> dict:
    """自选行情列：分页下推 SQL + **一次** snapshot_rows（整页共享，零 N+1）。

    ``refresh`` 参数保留给前端表达意图；实时层自己有 10s/600s 缓存
    （见 services/realtime.py），这里不再重复造缓存。
    """
    page, size, offset = _page(page, size)
    total = int(scalar("SELECT COUNT(*) FROM app_watchlist_items WHERE group_id = %s", (gid,)) or 0)
    rows = query(
        f"""
        SELECT {_ITEM_COLS}
        FROM app_watchlist_items WHERE group_id = %s
        ORDER BY sort ASC, created_at ASC, id ASC
        LIMIT %s OFFSET %s
        """,
        (gid, size, offset),
    )
    items, meta = _merge_items(rows)
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "as_of": _now(),
        "meta": meta,
    }


def _merge_items(rows: list[dict]) -> tuple[list[dict], dict]:
    codes = [r["ts_code"] for r in rows if r.get("ts_code")]
    quotes = _quotes_for(codes)
    no_name = [c for c in codes if not (quotes.get(c) or {}).get("name")]
    names = _names(no_name)

    items: list[dict] = []
    src_count: dict[str, int] = {}
    missing = 0
    for r in rows:
        code = r["ts_code"]
        q = quotes.get(code) or {}
        price = _num(q.get("price"))
        src = q.get("source") or "db"
        src_count[src] = src_count.get(src, 0) + 1
        if price is None:
            missing += 1
        items.append(
            {
                "item_id": _ser(r.get("id")),
                "ts_code": code,
                "name": q.get("name") or r.get("stock_name") or names.get(code),
                "price": price,
                "pct_chg": _num(q.get("pct_chg")),
                "open": _num(q.get("open")),
                "high": _num(q.get("high")),
                "low": _num(q.get("low")),
                "pre_close": _num(q.get("pre_close")),
                "volume": _num(q.get("volume")),
                "amount": _num(q.get("amount")),
                "turnover_rate": _num(q.get("turnover_rate")),
                "market_cap": _num(q.get("market_cap")),
                "limit_up_price": _num(q.get("limit_up_price")),
                "limit_down_price": _num(q.get("limit_down_price")),
                "trade_date": q.get("trade_date"),
                "source": src,
                "sort": r.get("sort"),
                "note": r.get("note"),
            }
        )
    return items, _meta(codes, src_count, missing)


# ---------------------------------------------------------------- 持仓
_HOLDING_COLS = "id, ts_code, stock_name, qty, cost, path, entry_date, notes, created_at, updated_at"


def list_holdings(page=1, size=20) -> dict:
    """持仓列表：LIMIT/OFFSET 下推 SQL，实时价只对**当前页**的代码查一次。"""
    page, size, offset = _page(page, size)
    total = int(scalar("SELECT COUNT(*) FROM app_holdings") or 0)
    rows = query(
        f"""
        SELECT {_HOLDING_COLS}
        FROM app_holdings
        ORDER BY updated_at DESC, created_at DESC, id ASC
        LIMIT %s OFFSET %s
        """,
        (size, offset),
    )
    return {"items": _merge_holdings(rows), "total": total, "page": page, "size": size}


def _merge_holdings(rows: list[dict]) -> list[dict]:
    codes = [r["ts_code"] for r in rows if r.get("ts_code")]
    quotes = _quotes_for(codes)
    names = _names([c for c in codes if not (quotes.get(c) or {}).get("name")])

    out = []
    for r in rows:
        code = r["ts_code"]
        q = quotes.get(code) or {}
        price = _num(q.get("price"))
        pre = _num(q.get("pre_close"))
        qty = _num(r.get("qty"), 0) or 0
        cost = _num(r.get("cost"), 0) or 0
        cost_value = cost * qty
        market_value = pnl = pnl_pct = today = None
        if price is not None:
            market_value = price * qty
            pnl = (price - cost) * qty
            pnl_pct = (price / cost - 1) * 100 if cost > 0 else None
            if pre:
                today = (price - pre) * qty
        out.append(
            {
                "id": _ser(r.get("id")),
                "ts_code": code,
                "stock_name": r.get("stock_name") or q.get("name") or names.get(code),
                "qty": int(r.get("qty") or 0),
                "cost": _r2(cost),
                "path": r.get("path"),
                "entry_date": str(r["entry_date"]) if r.get("entry_date") else None,
                "notes": r.get("notes"),
                "price": price,
                "pct_chg": _r2(_num(q.get("pct_chg"))),
                "pre_close": pre,
                "market_value": _r2(market_value),
                "cost_value": _r2(cost_value),
                "pnl": _r2(pnl),
                "pnl_pct": _r2(pnl_pct),
                "today_pnl": _r2(today),
                "source": q.get("source") or "db",
            }
        )
    return out


def get_holding(hid) -> dict | None:
    """单条持仓也走同一套行情合并，保证返回结构与列表页完全一致。"""
    row = query_one(f"SELECT {_HOLDING_COLS} FROM app_holdings WHERE id = %s", (hid,))
    if not row:
        return None
    return _merge_holdings([row])[0]


def create_holding(payload: dict) -> dict:
    code = _norm(payload.get("ts_code"))
    if not code:
        raise ValueError("ts_code 不能为空")
    qty = payload.get("qty")
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        raise ValueError("qty 必须是整数股数") from None
    if qty <= 0:
        raise ValueError("qty 必须大于 0")
    cost = _num(payload.get("cost"), 0) or 0
    if cost < 0:
        raise ValueError("cost 不能为负数")
    entry = _parse_date(payload.get("entry_date"))
    name = (payload.get("stock_name") or "").strip() or _names([code]).get(code)
    hid = str(uuid.uuid4())
    execute(
        f"""
        INSERT INTO app_holdings (id, user_id, ts_code, stock_name, qty, cost, path, entry_date, notes)
        VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            hid,
            code,
            name,
            qty,
            cost,
            str(payload.get("path") or "")[:32] or None,
            entry,
            payload.get("notes"),
        ),
    )
    return get_holding(hid) or {}


def update_holding(hid, payload: dict) -> dict | None:
    cols, params = _dynamic_set(payload, _HOLDING_UPDATABLE)
    if not cols:
        return get_holding(hid)
    cols.append("updated_at = NOW()")
    params.append(hid)
    if not execute(f"UPDATE app_holdings SET {', '.join(cols)} WHERE id = %s", tuple(params)):
        return None
    return get_holding(hid)


def delete_holding(hid) -> bool:
    return execute("DELETE FROM app_holdings WHERE id = %s", (hid,)) > 0


def holdings_summary() -> dict:
    """持仓汇总：全量行 + **一次**行情快照后在 Python 侧汇总。

    为什么不下推 SQL：现价来自内存实时缓存（services/realtime），库里没有；
    本地持仓表只有几十行，取全量再算的代价远小于把日线表 join 进来。
    """
    rows = query("SELECT ts_code, qty, cost FROM app_holdings ORDER BY id ASC")
    quotes = _quotes_for([r["ts_code"] for r in rows])

    total_value = total_cost = total_pnl = today_pnl = 0.0
    win = lose = 0
    for r in rows:
        qty = _num(r.get("qty"), 0) or 0
        cost = _num(r.get("cost"), 0) or 0
        cost_value = cost * qty
        total_cost += cost_value
        q = quotes.get(_norm(r["ts_code"])) or {}
        price = _num(q.get("price"))
        if price is None:
            continue
        pre = _num(q.get("pre_close"))
        total_value += price * qty
        pnl = (price - cost) * qty
        total_pnl += pnl
        if pre:
            today_pnl += (price - pre) * qty
        if pnl > 0:
            win += 1
        elif pnl < 0:
            lose += 1

    return {
        "total_value": _r2(total_value),
        "total_cost": _r2(total_cost),
        "total_pnl": _r2(total_pnl),
        "pnl_pct": _r2(total_pnl / total_cost * 100) if total_cost else 0.0,
        "today_pnl": _r2(today_pnl),
        "items": len(rows),
        "win_count": win,
        "lose_count": lose,
    }


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError("entry_date 必须是 YYYY-MM-DD") from None


def _dynamic_set(payload: dict, whitelist: tuple[str, ...]) -> tuple[list[str], list]:
    """按白名单生成 SET 子句：列名来自常量（安全），值全部走 %s 占位。"""
    # 列宽约束抄自 db_schema.py，越界直接截断，免得 PG 抛 500
    max_len = {"ts_code": 20, "stock_name": 64, "direction": 8, "scope": 16, "path": 32}
    cols, params = [], []
    for key in whitelist:
        if key not in payload:
            continue
        value = payload[key]
        if key == "ts_code":
            value = _norm(value)
            if not value:
                raise ValueError("ts_code 不能为空")
        if key == "entry_date":
            value = _parse_date(value)
        if key in ("qty",) and value is not None:
            value = int(value)
        if key == "cost" and value is not None:
            value = _num(value, value)
        if key in max_len and isinstance(value, str):
            value = value[: max_len[key]]
        cols.append(f"{key} = %s")
        params.append(value)
    return cols, params


# ---------------------------------------------------------------- 持仓破位设置
def get_holding_alert_settings() -> dict:
    """读 app_settings 的 holding_alert_settings；没存过就是契约里的默认 ``{"enabled": false}``。"""
    saved = get_setting(HOLDING_ALERT_KEY, None)
    if not isinstance(saved, dict):
        saved = {}
    return {"enabled": bool(saved.get("enabled", False)), **saved} if saved else {"enabled": False}


def put_holding_alert_settings(payload: dict) -> dict:
    """合并写入：既收契约里的 ``enabled``，也收破位阈值 ``threshold``（默认 5%）。"""
    payload = dict(payload or {})
    if "enabled" in payload:
        payload["enabled"] = bool(payload["enabled"])
    if "threshold" in payload and payload["threshold"] is not None:
        th = _num(payload["threshold"])
        if th is None or not (0 < th < 100):
            raise ValueError("threshold 必须是 0~100 之间的百分数")
        payload["threshold"] = th
    current = get_holding_alert_settings()
    current.update(payload)
    set_setting(HOLDING_ALERT_KEY, current)
    return current


# ---------------------------------------------------------------- 提醒
def get_alert(aid) -> dict | None:
    row = query_one("SELECT * FROM app_alerts WHERE id = %s", (aid,))
    return _ser(row) if row else None


def list_alerts(page=1, size=20, alert_type: str | None = None) -> dict:
    page, size, offset = _page(page, size)
    # 惰性触发检测：先算一遍并回写计数，这样列表里的 trigger_count 就是最新的
    evaluate_alerts()
    where, params = "", []
    if alert_type:
        where = " WHERE alert_type = %s"
        params = [alert_type]
    total = int(scalar("SELECT COUNT(*) FROM app_alerts" + where, tuple(params)) or 0)
    rows = query(
        "SELECT * FROM app_alerts"
        + where
        + " ORDER BY created_at DESC, id ASC LIMIT %s OFFSET %s",
        tuple(params) + (size, offset),
    )
    return {"items": [_ser(r) for r in rows], "total": total, "page": page, "size": size}


def triggered_alerts(limit=20) -> dict:
    """最近触发记录：先惰性算一轮，再按 last_triggered_at 倒序取前 N 条。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    limit = min(max(limit, 1), 200)
    evaluate_alerts()
    total = int(scalar("SELECT COUNT(*) FROM app_alerts WHERE last_triggered_at IS NOT NULL") or 0)
    rows = query(
        """
        SELECT * FROM app_alerts WHERE last_triggered_at IS NOT NULL
        ORDER BY last_triggered_at DESC, id ASC LIMIT %s
        """,
        (limit,),
    )
    return {"items": [_ser(r) for r in rows], "total": total}


def create_alert(payload: dict) -> dict:
    alert_type = (payload.get("alert_type") or "").strip()
    if alert_type not in ALERT_TYPES:
        raise ValueError("alert_type 必须是 " + "/".join(ALERT_TYPES) + " 之一")
    code = _norm(payload.get("ts_code"))
    if not code:
        raise ValueError("ts_code 不能为空")
    threshold = payload.get("threshold")
    if threshold is not None:
        threshold = _num(threshold)
        if threshold is None:
            raise ValueError("threshold 必须是数字")
    name = (payload.get("stock_name") or "").strip() or _names([code]).get(code)
    scope = str(payload.get("scope") or "stock")[:16]
    direction = payload.get("direction")
    direction = str(direction)[:8] if direction not in (None, "") else None
    aid = str(uuid.uuid4())
    execute(
        """
        INSERT INTO app_alerts
            (id, user_id, ts_code, stock_name, alert_type, scope, threshold, direction, enabled)
        VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            aid,
            code,
            name,
            alert_type,
            scope,
            threshold,
            direction,
            bool(payload.get("enabled", True)),
        ),
    )
    return get_alert(aid) or {}


def update_alert(aid, payload: dict) -> dict | None:
    if "alert_type" in payload and (payload.get("alert_type") or "") not in ALERT_TYPES:
        raise ValueError("alert_type 必须是 " + "/".join(ALERT_TYPES) + " 之一")
    cols, params = _dynamic_set(payload, _ALERT_UPDATABLE)
    if not cols:
        return get_alert(aid)
    cols.append("updated_at = NOW()")
    params.append(aid)
    if not execute(f"UPDATE app_alerts SET {', '.join(cols)} WHERE id = %s", tuple(params)):
        return None
    return get_alert(aid)


def delete_alert(aid) -> bool:
    return execute("DELETE FROM app_alerts WHERE id = %s", (aid,)) > 0


# ---------------------------------------------------------------- 触发检测（惰性）
def _mark_many(hits: list[tuple[str, str]]) -> int:
    """命中回写：计数 +1、记时间、留最后一次的触发文案。

    N 条命中合并成**一次** UPDATE —— 远端库单程 RTT 约 0.3s，逐条写会把
    GET /alerts 拖到秒级；命中多少条都只有一次往返。
    """
    if not hits:
        return 0
    return execute(
        """
        UPDATE app_alerts a
        SET trigger_count = COALESCE(a.trigger_count, 0) + 1,
            last_triggered_at = NOW(),
            last_message = v.msg,
            updated_at = NOW()
        FROM (SELECT unnest(%s::uuid[]) AS id, unnest(%s::text[]) AS msg) v
        WHERE a.id = v.id
        """,
        ([str(h[0]) for h in hits], [h[1] for h in hits]),
    )


def evaluate_alerts() -> int:
    """惰性触发检测（没有后台线程，读接口顺带算一次）。返回本次命中的条数。

    性能：全部启用的提醒 + 全部持仓只做**一次** snapshot_rows，
    判定在内存里完成；没有提醒也没有持仓时直接短路返回。
    """
    alerts = query("SELECT * FROM app_alerts WHERE enabled = true")
    holdings = query("SELECT id, ts_code, stock_name, qty, cost FROM app_holdings ORDER BY id ASC")
    if not alerts and not holdings:
        return 0

    settings = get_holding_alert_settings()
    hmap = {_norm(h["ts_code"]): h for h in holdings}
    codes = [a.get("ts_code") for a in alerts if a.get("ts_code")]
    codes += [h["ts_code"] for h in holdings]
    quotes = _quotes_for(codes)

    hits: list[tuple[str, str]] = []
    for a in alerts:
        msg = _check_alert(a, quotes.get(_norm(a.get("ts_code"))), hmap, settings)
        if msg:
            hits.append((str(a["id"]), msg))
    hits += _eval_break_cost(holdings, quotes, alerts, settings)
    return _mark_many(hits)


def _eval_break_cost(holdings, quotes, alerts, settings) -> list[tuple[str, str]]:
    """持仓破位：对 price <= cost*(1-threshold) 的持仓补一条 break_cost 提醒。

    只给**还没有** break_cost 记录的持仓建行（避免每次读都插重复行）；
    已有记录的持仓走常规判定，同样只计一次。返回待回写的 (id, message)。
    """
    if not holdings:
        return []
    have = {_norm(a.get("ts_code")) for a in alerts if a.get("alert_type") == "break_cost"}
    pct = _num((settings or {}).get("threshold"), DEFAULT_BREAK_PCT)
    created: list[tuple[str, str]] = []
    for h in holdings:
        code = _norm(h["ts_code"])
        if not code or code in have:
            continue
        q = quotes.get(code) or {}
        price = _num(q.get("price"))
        cost = _num(h.get("cost"), 0) or 0
        if price is None or cost <= 0:
            continue
        if price > cost * (1 - pct / 100):
            continue
        name = h.get("stock_name") or q.get("name") or code
        msg = f"{name}({code}) 持仓破位：现价 {price:.2f} ≤ 成本 {cost:.2f}×(1-{pct:g}%)"
        aid = str(uuid.uuid4())
        # 必须走 execute()：db.query() 归还连接时会 rollback，写不进库
        execute(
            """
            INSERT INTO app_alerts
                (id, user_id, ts_code, stock_name, alert_type, scope, threshold, enabled, trigger_count)
            VALUES (%s, NULL, %s, %s, 'break_cost', 'holding', %s, true, 0)
            """,
            (aid, code, name, pct),
        )
        created.append((aid, msg))
    return created


def _check_alert(a: dict, q: dict | None, hmap: dict, settings: dict) -> str | None:
    """单条判定：命中返回中文触发文案，未命中返回 None。"""
    q = q or {}
    kind = a.get("alert_type")
    th = _num(a.get("threshold"))
    code = _norm(a.get("ts_code"))
    name = q.get("name") or a.get("stock_name") or code or "市场"
    price = _num(q.get("price"))
    pct = _num(q.get("pct_chg"))
    amt = _num(q.get("amount"))

    if kind == "price_above":
        if price is None or th is None:
            return None
        return f"{name} 现价 {price:.2f} ≥ 提醒价 {th:.2f}" if price >= th else None
    if kind == "price_below":
        if price is None or th is None:
            return None
        return f"{name} 现价 {price:.2f} ≤ 提醒价 {th:.2f}" if price <= th else None
    if kind == "pct_chg_above":
        if pct is None or th is None:
            return None
        return f"{name} 涨幅 {pct:.2f}% ≥ 阈值 {th:.2f}%" if pct >= th else None
    if kind == "pct_chg_below":
        if pct is None or th is None:
            return None
        return f"{name} 跌幅 {pct:.2f}% ≤ 阈值 {th:.2f}%" if pct <= th else None
    if kind == "amount_above":
        if amt is None or th is None:
            return None
        return f"{name} 成交额 {amt:,.0f} ≥ 阈值 {th:,.0f}" if amt >= th else None
    if kind in ("limit_up", "limit_down"):
        if price is None:
            return None
        limit = _num(q.get("limit_up_price") if kind == "limit_up" else q.get("limit_down_price"))
        base = 9.9 if kind == "limit_up" else -9.9
        if limit is not None and limit > 0:
            hit = price >= limit - 1e-8 if kind == "limit_up" else price <= limit + 1e-8
        else:
            hit = pct is not None and (pct >= base if kind == "limit_up" else pct <= base)
        if not hit:
            return None
        word = "涨停" if kind == "limit_up" else "跌停"
        tail = f"（{pct:+.2f}%）" if pct is not None else ""
        return f"{name} 触发{word}：{price:.2f}{tail}"
    if kind == "break_cost":
        h = hmap.get(code)
        if not h or price is None:
            return None
        # 惰性生成的破位记录（scope=holding）阈值跟设置走；手工建的用它自己的 threshold
        pct_break = (
            _num((settings or {}).get("threshold"))
            if a.get("scope") == "holding"
            else None
        ) or th or DEFAULT_BREAK_PCT
        cost = _num(h.get("cost"), 0) or 0
        if cost <= 0:
            return None
        floor = cost * (1 - pct_break / 100)
        if price > floor:
            return None
        nm = h.get("stock_name") or name
        return f"{nm}({code}) 持仓破位：现价 {price:.2f} ≤ 成本 {cost:.2f}×(1-{pct_break:g}%)"
    return None
