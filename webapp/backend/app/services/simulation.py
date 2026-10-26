# -*- coding: utf-8 -*-
"""B2 模拟盘撮合引擎：账户 / 下单撮合 / 持仓 / 委托 / 汇总 / 清仓重置。

为什么撮合逻辑放 service 而不放路由
-----------------------------------
一笔下单要同时改「账户现金 + 持仓 + 委托」三张表，任何一步失败都必须整体回滚，
否则会出现「钱扣了但持仓没记」的账实不符。所以所有写操作都在 ``db_session()``
里用**同一条连接**执行、最后统一 commit（失败由 session 自动 rollback）；
路由层只做参数解析和 HTTP 语义。

为什么行情必须批量取
-------------------
本地库到远端 PG 的带宽只有几十 KB/s，逐票查询会把接口拖垮（见 db.py 的说明），
因此取价一律走 ``realtime.snapshot(codes)``：一次批量、实时优先、失败降级日线 close。

账务口径（A 股）
----------------
    佣金   max(5, amount * 0.0003)     买卖双向
    印花税 amount * 0.0005             仅卖出
    过户费 amount * 0.00002            买卖双向
    买入   cash -= amount + fee，持仓成本按加权平均 (旧数量*旧成本 + amount + fee) / 新数量
    卖出   cash += amount - fee，realized_pnl += amount - fee - 成本*数量

单用户本地系统：user_id 一律为 NULL，查询不做用户过滤。
"""
from __future__ import annotations

import datetime as dt
import uuid as uuid_lib

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor

from ..db import db_session
from .realtime import norm_code, snapshot

DEFAULT_CASH = 1_000_000.0  # 默认账户初始资金
DEFAULT_NAME = "默认账户"
LOT = 100  # A 股买入最小单位（1 手 = 100 股）
COMMISSION_RATE = 0.0003
COMMISSION_MIN = 5.0
STAMP_RATE = 0.0005  # 印花税，仅卖出
TRANSFER_RATE = 0.00002  # 过户费，双向


# ---------------------------------------------------------------- 序列化工具
def _num(v):
    """psycopg2 原生类型（Decimal/date/UUID）-> JSON 可序列化的 float/None。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _f(v, default: float = 0.0) -> float:
    n = _num(v)
    return default if n is None else n


def _r(v, nd: int = 2) -> float:
    """四舍五入并消灭 ``-0.0``（盈亏刚好在舍入区间内时 JSON 会打出 -0.0，很难看）。"""
    return round(_f(v), nd) + 0.0


def _s(v):
    """date / datetime / UUID -> str，避免 FastAPI 序列化踩坑。"""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _as_uuid(value, field: str) -> str:
    """校验并归一化外部传入的 id；非法 UUID 直接 400，不让 PG 抛 500。"""
    try:
        return str(uuid_lib.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"{field} 不是合法的 UUID") from exc


# ------------------------------------------------- 同连接 SQL 工具（事务内用）
def _q(conn, sql: str, params=()) -> list[dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def _one(conn, sql: str, params=()) -> dict | None:
    rows = _q(conn, sql, params)
    return rows[0] if rows else None


def _exec(conn, sql: str, params=()) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


# ---------------------------------------------------------------- 行转换
def _acct(r: dict | None) -> dict | None:
    if not r:
        return None
    return {
        "id": str(r["id"]),
        "user_id": _s(r.get("user_id")),
        "name": r.get("name"),
        "initial_cash": _f(r.get("initial_cash")),
        "cash": _f(r.get("cash")),
        "created_at": _s(r.get("created_at")),
        "updated_at": _s(r.get("updated_at")),
    }


def _order(r: dict | None) -> dict | None:
    if not r:
        return None
    return {
        "id": str(r["id"]),
        "account_id": str(r["account_id"]),
        "ts_code": r.get("ts_code"),
        "stock_name": r.get("stock_name"),
        "side": r.get("side"),
        "order_type": r.get("order_type"),
        "price": _num(r.get("price")),
        "qty": int(r.get("qty") or 0),
        "status": r.get("status"),
        "filled_price": _num(r.get("filled_price")),
        "filled_qty": int(r.get("filled_qty") or 0),
        "fee": _f(r.get("fee")),
        "reason": r.get("reason"),
        "filled_at": _s(r.get("filled_at")),
        "created_at": _s(r.get("created_at")),
    }


def _pos(r: dict | None) -> dict | None:
    if not r:
        return None
    return {
        "id": str(r["id"]),
        "account_id": str(r["account_id"]),
        "ts_code": r.get("ts_code"),
        "stock_name": r.get("stock_name"),
        "qty": int(r.get("qty") or 0),
        "cost": _f(r.get("cost")),
        "realized_pnl": _f(r.get("realized_pnl")),
        "fee_total": _f(r.get("fee_total")),
        "updated_at": _s(r.get("updated_at")),
    }


def _fee(amount: float, side: str) -> float:
    """佣金（双向，最低 5 元）+ 印花税（仅卖出）+ 过户费（双向）。"""
    commission = max(COMMISSION_MIN, amount * COMMISSION_RATE)
    stamp = amount * STAMP_RATE if side == "sell" else 0.0
    transfer = amount * TRANSFER_RATE
    return round(commission + stamp + transfer, 2)


# ---------------------------------------------------------------- 账户
def _ensure_account(conn, account_id=None) -> dict:
    """取账户；没给 id 就用「默认账户」，一个账户都没有则自动建（100 万初始资金）。

    为什么建完立即 commit：GET 类接口退出 session 时会 rollback，不 commit 的话
    「默认账户」每次都会重新建一次，产生重复行。
    """
    if account_id:
        aid = _as_uuid(account_id, "account_id")
        row = _one(conn, "SELECT * FROM app_sim_accounts WHERE id = %s", (aid,))
        if not row:
            raise HTTPException(status_code=404, detail="模拟盘账户不存在")
        return row
    row = _one(
        conn,
        "SELECT * FROM app_sim_accounts WHERE name = %s ORDER BY created_at LIMIT 1",
        (DEFAULT_NAME,),
    )
    if row:
        return row
    rows = _q(
        conn,
        "INSERT INTO app_sim_accounts (name, initial_cash, cash) VALUES (%s, %s, %s) RETURNING *",
        (DEFAULT_NAME, DEFAULT_CASH, DEFAULT_CASH),
    )
    conn.commit()
    return rows[0]


def list_accounts() -> dict:
    """账户列表；持仓数 / 委托数用标量子查询下推 SQL，不逐账户发请求。

    市值也一次批量取价算完（所有账户的持仓合起来只调一次 snapshot），
    这样列表能直接展示「总资产」，而不会退化成 N 个账户 × M 只票的查询风暴。
    """
    with db_session() as conn:
        # 兜底建默认账户：前端首屏先调列表，空列表会让人以为功能坏了
        _ensure_account(conn)
        rows = _q(
            conn,
            """
            SELECT a.*,
                   (SELECT COUNT(*) FROM app_sim_positions p
                     WHERE p.account_id = a.id AND p.qty > 0) AS position_count,
                   (SELECT COUNT(*) FROM app_sim_orders o
                     WHERE o.account_id = a.id) AS order_count
              FROM app_sim_accounts a
             ORDER BY a.created_at
            """,
        )
        # 一次捞出全部持仓，供下面统一估值（本地模拟盘账户/持仓数量都很小）
        pos_rows = _q(
            conn,
            "SELECT account_id, ts_code, qty, cost FROM app_sim_positions WHERE qty > 0",
        )
        conn.commit()

    quotes = _quote_batch([r["ts_code"] for r in pos_rows])
    mv: dict[str, float] = {}
    for r in pos_rows:
        q = quotes.get(r["ts_code"]) or {}
        qty = int(r.get("qty") or 0)
        price = _f(q.get("price"), 0.0) or _f(r.get("cost"))
        mv[str(r["account_id"])] = mv.get(str(r["account_id"]), 0.0) + price * qty

    items = []
    for r in rows:
        acct = _acct(r)
        market_value = round(mv.get(acct["id"], 0.0), 2)
        items.append(
            {
                **acct,
                "position_count": int(r.get("position_count") or 0),
                "order_count": int(r.get("order_count") or 0),
                "market_value": market_value,
                "total_value": round(_f(acct["cash"]) + market_value, 2),
            }
        )
    return {"items": items, "total": len(items)}


def create_account(name=None, initial_cash=None) -> dict:
    with db_session() as conn:
        safe_name = str(name or "").strip() or "模拟账户"
        if len(safe_name) > 64:
            raise HTTPException(status_code=400, detail="账户名最长 64 字")
        try:
            cash = float(initial_cash) if initial_cash not in (None, "") else DEFAULT_CASH
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="initial_cash 必须是数字") from exc
        if cash <= 0:
            raise HTTPException(status_code=400, detail="initial_cash 必须大于 0")
        row = _one(
            conn,
            "INSERT INTO app_sim_accounts (name, initial_cash, cash) VALUES (%s, %s, %s) RETURNING *",
            (safe_name, round(cash, 2), round(cash, 2)),
        )
        conn.commit()
    return _acct(row)


# ---------------------------------------------------------------- 行情（批量）
def _quote_batch(codes: list[str]) -> dict:
    """一次批量取价：实时优先，网络降级到 daily_quotes close。"""
    if not codes:
        return {}
    try:
        return snapshot(codes)
    except Exception:  # noqa: BLE001 撮合不因行情层异常而炸，交给调用方判"取不到价"
        return {}


# ---------------------------------------------------------------- 下单撮合
def place_order(payload: dict) -> dict:
    """下单并撮合：校验 → 定价 → 算费 → 改账 → 落库，一条事务完成。

    拒绝成交的委托同样落库（status='rejected' + reason），这样前端的委托历史能
    解释「为什么没成交」，而不是凭空消失；拒绝单不动现金、不动持仓。
    """
    data = payload or {}
    ts_code = norm_code(str(data.get("ts_code") or ""))
    side = str(data.get("side") or "").strip().lower()
    order_type = str(data.get("order_type") or "market").strip().lower()
    account_id = data.get("account_id")

    if not ts_code:
        raise HTTPException(status_code=400, detail="缺少 ts_code")
    if side not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail='side 必须是 "buy" 或 "sell"')
    if order_type not in ("market", "limit"):
        raise HTTPException(status_code=400, detail='order_type 必须是 "market" 或 "limit"')
    try:
        qty = int(data.get("qty") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="qty 必须是整数") from exc

    limit_price = None
    if order_type == "limit":
        limit_price = _f(data.get("price"), 0.0)
        if limit_price <= 0:
            raise HTTPException(status_code=400, detail="限价单必须提供有效的 price")

    # 取价放在事务外：不持有数据库连接/行锁做网络等待，避免拖长锁持有时间
    quote = _quote_batch([ts_code]).get(ts_code) or {}
    latest = _f(quote.get("price"), 0.0)
    stock_name = quote.get("name")

    with db_session() as conn:
        acct = _ensure_account(conn, account_id)
        acct_id = str(acct["id"])
        # 行锁：同一账户并发下单时把「读-改-写」串行化，防止超卖 / 透支
        locked = _one(conn, "SELECT * FROM app_sim_accounts WHERE id = %s FOR UPDATE", (acct_id,))
        if locked:
            acct = locked
        pos = _one(
            conn,
            "SELECT * FROM app_sim_positions WHERE account_id = %s AND ts_code = %s FOR UPDATE",
            (acct_id, ts_code),
        )
        if not stock_name:
            meta = _one(conn, "SELECT stock_name FROM stock_basic_info WHERE ts_code = %s", (ts_code,))
            stock_name = meta.get("stock_name") if meta else None

        held = int(pos["qty"]) if pos else 0
        cash = _f(acct.get("cash"))
        reason = ""

        # 1) 数量合法性：买入必须整手
        if qty <= 0:
            reason = "委托数量必须大于 0"
        elif side == "buy" and qty % LOT != 0:
            reason = f"买入必须为 {LOT} 股的整数倍（当前 {qty} 股）"

        # 2) 卖出不得超持仓
        if not reason and side == "sell" and qty > held:
            reason = f"卖出数量超过当前持仓（可用 {held} 股）"

        # 3) 必须拿得到最新价
        if not reason and latest <= 0:
            reason = "取不到该标的的最新行情，暂不能成交"

        # 4) 限价单方向校验：买单价高于最新价 / 卖单价低于最新价 → 拒绝成交
        if not reason and order_type == "limit":
            if side == "buy" and limit_price > latest:
                reason = f"限价买入价 {limit_price:.4f} 高于最新价 {latest:.4f}，按规则不成交"
            elif side == "sell" and limit_price < latest:
                reason = f"限价卖出价 {limit_price:.4f} 低于最新价 {latest:.4f}，按规则不成交"

        fill_price = None
        amount = 0.0
        fee = 0.0
        if not reason:
            # 市价单按最新价，限价单按委托价（已通过方向校验）
            fill_price = latest if order_type == "market" else limit_price
            amount = round(fill_price * qty, 2)
            fee = _fee(amount, side)
            # 5) 资金校验
            if side == "buy" and amount + fee > cash + 1e-9:
                reason = f"可用资金不足（需 {amount + fee:.2f} 元，可用 {cash:.2f} 元）"

        # 被拒绝的委托不落成交价，否则会出现「status=rejected 却带成交价」的自相矛盾
        if reason:
            fill_price = None

        status = "rejected" if reason else "filled"
        now = dt.datetime.now().astimezone()
        order_row = _one(
            conn,
            """
            INSERT INTO app_sim_orders
                (account_id, ts_code, stock_name, side, order_type, price, qty, status,
                 filled_price, filled_qty, fee, reason, filled_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                acct_id,
                ts_code,
                stock_name,
                side,
                order_type,
                round(limit_price, 4) if limit_price is not None else None,
                qty,
                status,
                round(fill_price, 4) if fill_price else None,
                qty if status == "filled" else 0,
                fee if status == "filled" else 0.0,
                reason or None,
                now if status == "filled" else None,
                now,
            ),
        )

        pos_row = pos
        if status == "filled":
            if side == "buy":
                total_cost = amount + fee  # 买入成本含费用，卖出时按此结转
                _exec(
                    conn,
                    "UPDATE app_sim_accounts SET cash = cash - %s, updated_at = now() WHERE id = %s",
                    (round(total_cost, 2), acct_id),
                )
                if pos:
                    new_qty = held + qty
                    new_cost = round((held * _f(pos["cost"]) + total_cost) / new_qty, 4)
                    _exec(
                        conn,
                        """
                        UPDATE app_sim_positions
                           SET qty = %s, cost = %s, stock_name = COALESCE(%s, stock_name),
                               fee_total = fee_total + %s, updated_at = now()
                         WHERE account_id = %s AND ts_code = %s
                        """,
                        (new_qty, new_cost, stock_name, fee, acct_id, ts_code),
                    )
                else:
                    _exec(
                        conn,
                        """
                        INSERT INTO app_sim_positions
                            (account_id, ts_code, stock_name, qty, cost, realized_pnl, fee_total, updated_at)
                        VALUES (%s, %s, %s, %s, %s, 0, %s, now())
                        """,
                        (acct_id, ts_code, stock_name, qty, round(total_cost / qty, 4), fee),
                    )
            else:  # 卖出：现金增加，结转成本，累计已实现盈亏
                proceeds = amount - fee
                realized = proceeds - _f(pos["cost"]) * qty
                new_qty = held - qty
                _exec(
                    conn,
                    "UPDATE app_sim_accounts SET cash = cash + %s, updated_at = now() WHERE id = %s",
                    (round(proceeds, 2), acct_id),
                )
                _exec(
                    conn,
                    """
                    UPDATE app_sim_positions
                       SET qty = %s,
                           cost = CASE WHEN %s <= 0 THEN 0 ELSE cost END,
                           realized_pnl = realized_pnl + %s,
                           fee_total = fee_total + %s,
                           stock_name = COALESCE(%s, stock_name),
                           updated_at = now()
                     WHERE account_id = %s AND ts_code = %s
                    """,
                    (
                        new_qty,
                        new_qty,
                        round(realized, 2),
                        fee,
                        stock_name,
                        acct_id,
                        ts_code,
                    ),
                )
            pos_row = _one(
                conn,
                "SELECT * FROM app_sim_positions WHERE account_id = %s AND ts_code = %s",
                (acct_id, ts_code),
            )

        acct_row = _one(conn, "SELECT * FROM app_sim_accounts WHERE id = %s", (acct_id,))
        conn.commit()

    return {
        "order": _order(order_row),
        "position": _pos(pos_row),
        "account": _acct(acct_row),
        "matched": status == "filled",
    }


def cancel_order(order_id) -> dict:
    """撤单：本引擎即时撮合，只有 pending 状态还能撤，其余一律 400 说明原因。"""
    with db_session() as conn:
        oid = _as_uuid(order_id, "order_id")
        row = _one(conn, "SELECT * FROM app_sim_orders WHERE id = %s FOR UPDATE", (oid,))
        if not row:
            raise HTTPException(status_code=404, detail="委托不存在")
        if row["status"] != "pending":
            raise HTTPException(
                status_code=400,
                detail=f"仅 pending 状态的委托可撤销，当前状态 {row['status']}（本引擎即时撮合，成交/拒绝后不可撤）",
            )
        _exec(
            conn,
            "UPDATE app_sim_orders SET status = 'cancelled', reason = COALESCE(reason, '用户撤销') WHERE id = %s",
            (oid,),
        )
        row = _one(conn, "SELECT * FROM app_sim_orders WHERE id = %s", (oid,))
        conn.commit()
    return _order(row)


# ---------------------------------------------------------------- 查询
def positions(account_id=None, page: int = 1, size: int = 20) -> dict:
    """持仓分页：LIMIT/OFFSET 下推 SQL，只对本页代码批量取一次行情。"""
    page = max(1, int(page))
    size = min(max(1, int(size)), 200)
    with db_session() as conn:
        acct = _ensure_account(conn, account_id)
        acct_id = str(acct["id"])
        total = _one(conn, "SELECT COUNT(*) AS c FROM app_sim_positions WHERE account_id = %s AND qty > 0", (acct_id,))
        rows = _q(
            conn,
            """
            SELECT id, account_id, ts_code, stock_name, qty, cost, realized_pnl, fee_total, updated_at
              FROM app_sim_positions
             WHERE account_id = %s AND qty > 0
             ORDER BY updated_at DESC, ts_code
             LIMIT %s OFFSET %s
            """,
            (acct_id, size, (page - 1) * size),
        )
        cash = _f(acct.get("cash"))
        initial_cash = _f(acct.get("initial_cash"), DEFAULT_CASH)
        conn.commit()

    quotes = _quote_batch([r["ts_code"] for r in rows])
    items = []
    for r in rows:
        q = quotes.get(r["ts_code"]) or {}
        cost = _f(r.get("cost"))
        qty = int(r.get("qty") or 0)
        price = _f(q.get("price"), 0.0) or cost  # 行情缺失时按成本估值，避免市值归零
        pre = _f(q.get("pre_close"), 0.0)
        market_value = price * qty
        cost_value = cost * qty
        pnl = market_value - cost_value
        items.append(
            {
                **_pos(r),
                "price": round(price, 4),
                "pre_close": round(pre, 4) if pre else None,
                "pct_chg": _num(q.get("pct_chg")),
                "source": q.get("source"),
                "market_value": round(market_value, 2),
                "cost_value": round(cost_value, 2),
                "pnl": _r(pnl),
                "pnl_pct": _r((price - cost) / cost * 100) if cost else 0.0,
                "today_pnl": _r(qty * (price - pre)) if pre else 0.0,
            }
        )
    return {
        "items": items,
        "total": int((total or {}).get("c") or 0),
        "page": page,
        "size": size,
        "account_id": acct_id,
        "cash": round(cash, 2),
        "initial_cash": round(initial_cash, 2),
    }


def orders(account_id=None, page: int = 1, size: int = 20) -> dict:
    """委托历史分页：COUNT + 分页明细都下推 SQL。"""
    page = max(1, int(page))
    size = min(max(1, int(size)), 200)
    with db_session() as conn:
        acct = _ensure_account(conn, account_id)
        acct_id = str(acct["id"])
        total = _one(conn, "SELECT COUNT(*) AS c FROM app_sim_orders WHERE account_id = %s", (acct_id,))
        rows = _q(
            conn,
            """
            SELECT * FROM app_sim_orders
             WHERE account_id = %s
             ORDER BY created_at DESC, id DESC
             LIMIT %s OFFSET %s
            """,
            (acct_id, size, (page - 1) * size),
        )
        conn.commit()
    return {
        "items": [_order(r) for r in rows],
        "total": int((total or {}).get("c") or 0),
        "page": page,
        "size": size,
        "account_id": acct_id,
    }


def summary(account_id=None) -> dict:
    """账户总览：现金 + 持仓市值 + 盈亏 + 胜率 + 委托数，一次事务内取完再批量取价。

    win_rate 口径：已了结品种（realized_pnl <> 0）中盈利品种的占比，
    未平仓的持仓不计入分母，否则「刚建仓」会把胜率稀释成 0。
    """
    with db_session() as conn:
        acct = _ensure_account(conn, account_id)
        acct_id = str(acct["id"])
        stats = _one(
            conn,
            """
            SELECT COUNT(*) FILTER (WHERE qty > 0)                     AS held,
                   COUNT(*) FILTER (WHERE realized_pnl > 0)             AS win,
                   COUNT(*) FILTER (WHERE realized_pnl <> 0)            AS closed,
                   COALESCE(SUM(realized_pnl), 0)                      AS realized
              FROM app_sim_positions
             WHERE account_id = %s
            """,
            (acct_id,),
        ) or {}
        order_stats = _one(
            conn,
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE status = 'filled')   AS filled,
                   COUNT(*) FILTER (WHERE status = 'rejected') AS rejected
              FROM app_sim_orders WHERE account_id = %s
            """,
            (acct_id,),
        ) or {}
        rows = _q(
            conn,
            "SELECT ts_code, qty, cost FROM app_sim_positions WHERE account_id = %s AND qty > 0",
            (acct_id,),
        )
        cash = _f(acct.get("cash"))
        initial_cash = _f(acct.get("initial_cash"), DEFAULT_CASH)
        conn.commit()

    quotes = _quote_batch([r["ts_code"] for r in rows])
    equity = 0.0
    today_pnl = 0.0
    for r in rows:
        q = quotes.get(r["ts_code"]) or {}
        qty = int(r.get("qty") or 0)
        cost = _f(r.get("cost"))
        price = _f(q.get("price"), 0.0) or cost
        pre = _f(q.get("pre_close"), 0.0)
        equity += price * qty
        if pre:
            today_pnl += qty * (price - pre)

    total_value = cash + equity
    total_pnl = total_value - initial_cash
    closed = int(stats.get("closed") or 0)
    win = int(stats.get("win") or 0)
    return {
        "account_id": acct_id,
        "account_name": acct.get("name"),
        "cash": round(cash, 2),
        "initial_cash": round(initial_cash, 2),
        "equity": round(equity, 2),  # 持仓市值
        "total_value": round(total_value, 2),
        "total_pnl": _r(total_pnl),
        "pnl_pct": _r(total_pnl / initial_cash * 100) if initial_cash else 0.0,
        "roi": _r(total_pnl / initial_cash * 100) if initial_cash else 0.0,
        "today_pnl": _r(today_pnl),
        "realized_pnl": _r(stats.get("realized")),
        "positions": int(stats.get("held") or 0),
        "win_rate": _r(win / closed * 100) if closed else 0.0,
        "win_count": win,
        "closed_count": closed,
        "orders": int(order_stats.get("total") or 0),
        "filled_orders": int(order_stats.get("filled") or 0),
        "rejected_orders": int(order_stats.get("rejected") or 0),
        "as_of": dt.datetime.now().isoformat(timespec="seconds"),
    }


def reset(account_id=None) -> dict:
    """清仓重置：删光该账户的委托与持仓，现金恢复到 initial_cash。"""
    with db_session() as conn:
        acct = _ensure_account(conn, account_id)
        acct_id = str(acct["id"])
        deleted_orders = _exec(conn, "DELETE FROM app_sim_orders WHERE account_id = %s", (acct_id,))
        deleted_positions = _exec(conn, "DELETE FROM app_sim_positions WHERE account_id = %s", (acct_id,))
        _exec(
            conn,
            "UPDATE app_sim_accounts SET cash = initial_cash, updated_at = now() WHERE id = %s",
            (acct_id,),
        )
        row = _one(conn, "SELECT * FROM app_sim_accounts WHERE id = %s", (acct_id,))
        conn.commit()
    return {
        "account": _acct(row),
        "deleted_orders": deleted_orders,
        "deleted_positions": deleted_positions,
        "reset_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
