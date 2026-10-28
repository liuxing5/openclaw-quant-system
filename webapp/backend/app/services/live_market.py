# -*- coding: utf-8 -*-
"""盘中实时总台：用全市场快照覆盖 T-1 日线聚合。

为什么需要这一层
----------------
``daily_quotes`` 最新只到上一交易日（今天是 2026-10-26，表里是 10-23），
所以「每 60 秒刷新」如果只重算日线聚合，数字当然不会变。本模块把
``live_snapshot``（腾讯 5224 只、20 秒刷新）作为**当日行情底座**，实时计算：

* 涨跌广度 / 涨跌停 / 封板率 / 成交额 / 分布直方图
* 四类榜单（涨幅/跌幅/成交额/换手）
* 连板梯队（今日涨停 + 截至上一交易日的连板高度）
* 情绪六维（广度、涨停效应、连板高度来自实时；趋势/量能复用日线缓存）
* 概念/行业热度（东财板块实时榜单）

任何一步失败都回退到 ``market.py`` 的 T-1 口径，接口不会因此不可用。
"""
from __future__ import annotations

import concurrent.futures
import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from ..cache import ttl_cache
from ..db import query
from . import live_snapshot, market, realtime, ths_boards

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_BOARD_FS = {"concept": "m:90+t:3+f:!50", "industry": "m:90+t:2+f:!50"}


def _get_json(url: str, timeout: float = 8.0) -> dict:
    """东财接口偶发 RemoteDisconnected，重试一次再放弃。"""
    last = None
    for _ in range(2):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": _UA, "Referer": "https://quote.eastmoney.com/"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 域名固定
                return json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.3)
    raise last


def _f(v, default=None):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def today() -> date:
    """实时口径的「今天」：工作日用自然日，周末沿用最近交易日。"""
    now = datetime.now()
    if now.weekday() < 5:
        return now.date()
    return market.latest_trade_date() or now.date()


def available() -> bool:
    return (live_snapshot.get_snapshot().get("count") or 0) >= 4000


def _items() -> list[dict]:
    return live_snapshot.get_snapshot().get("items") or []


def _as_of() -> str | None:
    return live_snapshot.get_snapshot().get("as_of")


# ---------------------------------------------------------------------------
# 广度
# ---------------------------------------------------------------------------

@ttl_cache(10)
def breadth() -> dict:
    items = _items()
    if not items:
        return {}
    day = today()
    up = flat = down = lu = ld = touched = broken = 0
    amount = 0.0
    total = 0
    for it in items:
        price = _f(it.get("price"))
        pct = _f(it.get("pct_chg"))
        if not price or pct is None:
            continue
        total += 1
        amount += _f(it.get("amount"), 0) or 0
        if pct > 0:
            up += 1
        elif pct < 0:
            down += 1
        else:
            flat += 1
        lup = _f(it.get("limit_up_price"))
        ldp = _f(it.get("limit_down_price"))
        high = _f(it.get("high"))
        is_lu = bool(lup and price >= lup - 0.005)
        is_ld = bool(ldp and price <= ldp + 0.005)
        hit = bool(lup and high and high >= lup - 0.005)
        if is_lu:
            lu += 1
        if is_ld:
            ld += 1
        if hit:
            touched += 1
            if not is_lu:
                broken += 1

    # 复用日线的分桶标签与顺序，保证「实时/日线」两张直方图可比
    labels = market.BREADTH_LABELS
    buckets = [0] * len(labels)
    for it in items:
        pct = _f(it.get("pct_chg"))
        price = _f(it.get("price"))
        if pct is None or not price:
            continue
        lup = _f(it.get("limit_up_price"))
        ldp = _f(it.get("limit_down_price"))
        if lup and price >= lup - 0.005:
            idx = 0
        elif ldp and price <= ldp + 0.005:
            idx = 10
        elif pct >= 7:
            idx = 1
        elif pct >= 5:
            idx = 2
        elif pct >= 3:
            idx = 3
        elif pct > 0:
            idx = 4
        elif pct <= -7:
            idx = 9
        elif pct <= -5:
            idx = 8
        elif pct <= -3:
            idx = 7
        elif pct < 0:
            idx = 6
        else:
            idx = 5
        buckets[idx] += 1

    return {
        "trade_date": str(day),
        "symbols": len(items),
        "total": total,
        "up": up,
        "flat": flat,
        "down": down,
        "limit_up": lu,
        "limit_down": ld,
        "broken": broken,
        "touched": touched,
        "seal_rate": round(lu / touched, 4) if touched else None,
        "amount": round(amount, 2),
        "up_ratio": round(up / total, 4) if total else None,
        "histogram": [{"label": labels[i], "count": buckets[i]} for i in range(len(labels))],
        "live": True,
        "as_of": _as_of(),
    }


# ---------------------------------------------------------------------------
# 榜单
# ---------------------------------------------------------------------------

_SORT_KEYS = {
    "pct_chg_desc": lambda x: -(x.get("pct_chg") or 0),
    "pct_chg_asc": lambda x: x.get("pct_chg") or 0,
    "amount": lambda x: -(x.get("amount") or 0),
    "turnover_rate": lambda x: -(x.get("turnover_rate") or 0),
    "volume": lambda x: -(x.get("volume") or 0),
}


@ttl_cache(10)
def rankings(sort: str = "pct_chg_desc", limit: int = 8) -> list[dict]:
    items = [x for x in _items() if _f(x.get("price")) and _f(x.get("pct_chg")) is not None]
    if not items:
        return []
    key = _SORT_KEYS.get(sort, _SORT_KEYS["pct_chg_desc"])
    rows = sorted(items, key=key)[: max(1, min(int(limit), 50))]
    codes = [r["ts_code"] for r in rows]
    boards = market._boards(codes)
    return [
        {
            "ts_code": r["ts_code"],
            "name": r.get("name") or r["ts_code"],
            "board": boards.get(r["ts_code"], ""),
            "close": round(_f(r.get("price"), 0) or 0, 2),
            "pct_chg": round(_f(r.get("pct_chg"), 0) or 0, 2),
            "amount": _f(r.get("amount"), 0) or 0,
            "turnover_rate": round(_f(r.get("turnover_rate"), 0) or 0, 2),
            "volume": _f(r.get("volume"), 0) or 0,
            "circulating_market_cap": _f(r.get("circulating_market_cap")),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 成交额序列（历史 + 今日实时）
# ---------------------------------------------------------------------------

@ttl_cache(20)
def amount_series(limit: int = 30) -> list[dict]:
    db_day = market.latest_trade_date()
    rows = list(market.amount_series(db_day, limit)) if db_day else []
    br = breadth()
    day = today()
    if br and (not rows or str(rows[-1].get("date")) != str(day)):
        rows.append({"date": str(day), "amount": br.get("amount")})
    return rows[-limit:]


# ---------------------------------------------------------------------------
# 指数（实时价覆盖 + 保留 30 日曲线）
# ---------------------------------------------------------------------------

@ttl_cache(10)
def indexes() -> list[dict]:
    base = market.index_overview()
    by_code = {b["ts_code"]: b for b in base}
    codes = [c for c, _ in market.INDEX_LIST]
    try:
        live = {r["ts_code"]: r for r in realtime.snapshot_rows(codes)}
    except Exception:  # noqa: BLE001
        live = {}
    day = today()
    out = []
    for code, name in market.INDEX_LIST:
        b = dict(by_code.get(code) or {"ts_code": code, "name": name, "curve": []})
        l = live.get(code) or {}
        price = _f(l.get("price"))
        if price:
            b["close"] = price
            b["pct_chg"] = _f(l.get("pct_chg"))
            b["open"] = _f(l.get("open"))
            b["high"] = _f(l.get("high"))
            b["low"] = _f(l.get("low"))
            b["prev_close"] = _f(l.get("pre_close"))
            b["live"] = True
            curve = list(b.get("curve") or [])
            if not curve or str(curve[-1].get("date")) != str(day):
                curve.append({"date": str(day), "close": price})
            b["curve"] = curve[-31:]
        out.append(b)
    return out


# ---------------------------------------------------------------------------
# 连板梯队
# ---------------------------------------------------------------------------

@ttl_cache(10)
def mode_ladder(mode: str = "limit_up", detail: bool = True) -> dict:
    """跌停 / 炸板 / 断板 列表（统一成一组 tier，复用连板页渲染）。"""
    items = _items()
    br = breadth()
    day = today()

    def member(it: dict, streak: int = 1) -> dict:
        code = it.get("ts_code") or it.get("code")
        return {
            "ts_code": code,
            "name": it.get("name") or it.get("stock_name") or code,
            "board": it.get("board") or _board_name(code or ""),
            "streak": streak,
            "status": {"limit_down": "跌停", "broken": "炸板", "failed": "断板"}.get(mode, mode),
            "close": _f(it.get("price") or it.get("close")),
            "pct_chg": round(_f(it.get("pct_chg"), 0) or 0, 2),
            "amount": _f(it.get("amount"), 0) or 0,
            "turnover_rate": round(_f(it.get("turnover_rate"), 0) or 0, 2),
            "volume": _f(it.get("volume"), 0) or 0,
        }

    members: list[dict] = []
    if mode == "limit_down":
        for it in items:
            price = _f(it.get("price"))
            ldp = _f(it.get("limit_down_price"))
            if price and ldp and price <= ldp + 0.005:
                members.append(member(it))
    elif mode == "broken":
        for it in items:
            price = _f(it.get("price"))
            lup = _f(it.get("limit_up_price"))
            high = _f(it.get("high"))
            if price and lup and high and high >= lup - 0.005 and price < lup - 0.005:
                members.append(member(it))
    elif mode == "failed":
        prev_day = (market.trade_dates(2) or [None, None])[1]
        prev_codes = set(market.limit_up_codes(prev_day)) if prev_day else set()
        today_lu = {
            it["ts_code"]
            for it in items
            if _f(it.get("limit_up_price")) and (_f(it.get("price")) or 0) >= (_f(it.get("limit_up_price")) or 0) - 0.005
        }
        by_code = {it["ts_code"]: it for it in items}
        for code in prev_codes - today_lu:
            it = by_code.get(code)
            if it:
                members.append(member(it))
            else:
                # 今日快照缺失（停牌/源缺失）时退回日线最新价
                row = query(
                    "SELECT ts_code, stock_name, close, pct_chg, amount, turnover_rate, volume "
                    "FROM daily_quotes d LEFT JOIN stock_basic_info s USING (ts_code) "
                    "WHERE ts_code = %s ORDER BY trade_date DESC LIMIT 1",
                    (code,),
                )
                if row:
                    r = row[0]
                    members.append(
                        {
                            "ts_code": code,
                            "name": r.get("stock_name") or code,
                            "board": _board_name(code),
                            "streak": 1,
                            "status": "断板",
                            "close": _f(r.get("close")),
                            "pct_chg": round(_f(r.get("pct_chg"), 0) or 0, 2),
                            "amount": _f(r.get("amount"), 0) or 0,
                            "turnover_rate": round(_f(r.get("turnover_rate"), 0) or 0, 2),
                            "volume": _f(r.get("volume"), 0) or 0,
                        }
                    )

    members.sort(key=lambda x: (x.get("pct_chg") or 0))
    tiers = [{"streak": 1, "count": len(members), "members": members if detail else members[:8]}] if members else []
    return {
        "trade_date": str(day),
        "tiers": tiers,
        "max_streak": 1 if members else 0,
        "limit_up": br.get("limit_up", 0),
        "limit_down": br.get("limit_down", 0),
        "broken": br.get("broken", 0),
        "touched": br.get("touched", 0),
        "seal_rate": br.get("seal_rate"),
        "mode": mode,
        "live": True,
        "as_of": _as_of(),
    }


def _board_name(code: str) -> str:
    pure = (code or "").split(".")[0]
    if pure.startswith(("688", "689")):
        return "科创板"
    if pure.startswith(("300", "301", "302")):
        return "创业板"
    if pure.startswith(("4", "8", "920")):
        return "北交所"
    return "A主板"


@ttl_cache(10)
def limit_ladder(detail: bool = True) -> dict:
    items = _items()
    if not items:
        return {"trade_date": None, "tiers": [], "max_streak": 0, "limit_up": 0}
    lu = []
    for it in items:
        name = str(it.get("name") or "")
        # 原站默认榜单不计 ST 与 C 字头新股；它们单独有筛选入口
        if "ST" in name.upper() or name.startswith("C"):
            continue
        price = _f(it.get("price"))
        lup = _f(it.get("limit_up_price"))
        if price and lup and price >= lup - 0.005:
            lu.append(it)
    if not lu:
        return {
            "trade_date": str(today()),
            "tiers": [],
            "max_streak": 0,
            "limit_up": 0,
            "live": True,
            "as_of": _as_of(),
        }

    codes = [x["ts_code"] for x in lu]
    db_day = market.latest_trade_date()
    prior: dict[str, int] = {}
    if db_day:
        dates = market.trade_dates(120)
        window = [db_day] + [d for d in dates if d < db_day][:29]
        try:
            rows = query(market._STREAK_SQL, (codes, window))
            prior = {r["ts_code"]: int(r["streak"]) for r in rows if r.get("streak")}
        except Exception:  # noqa: BLE001
            prior = {}

    streaks = {
        x["ts_code"]: (prior.get(x["ts_code"], 0) if db_day == today() else prior.get(x["ts_code"], 0) + 1)
        or 1
        for x in lu
    }
    by_code = {x["ts_code"]: x for x in lu}
    boards = market._boards(codes)
    tier_map: dict[int, list[dict]] = {}
    for code, n in streaks.items():
        it = by_code[code]
        tier_map.setdefault(n, []).append(
            {
                "ts_code": code,
                "name": it.get("name") or code,
                "board": boards.get(code, ""),
                "streak": n,
                "status": "今日涨停" if n <= 1 else f"{n}连板",
                "close": round(_f(it.get("price"), 0) or 0, 2),
                "pct_chg": round(_f(it.get("pct_chg"), 0) or 0, 2),
                "amount": _f(it.get("amount"), 0) or 0,
                "turnover_rate": round(_f(it.get("turnover_rate"), 0) or 0, 2),
                "volume": _f(it.get("volume"), 0) or 0,
            }
        )

    tiers = []
    for n in sorted(tier_map, reverse=True):
        members = sorted(tier_map[n], key=lambda x: -(x.get("pct_chg") or 0))
        tiers.append(
            {
                "streak": n,
                "count": len(members),
                "members": members if detail else members[:8],
            }
        )
    return {
        "trade_date": str(today()),
        "tiers": tiers,
        "max_streak": max(tier_map),
        "limit_up": len(streaks),
        "live": True,
        "as_of": _as_of(),
    }


# ---------------------------------------------------------------------------
# 情绪六维
# ---------------------------------------------------------------------------

@ttl_cache(20)
def sentiment() -> dict:
    br = breadth()
    if not br:
        return {}
    day = today()
    db_day = market.latest_trade_date()
    trend = market.trend_and_newhigh(db_day) if db_day else {}
    amounts = market.amount_series(db_day, 21) if db_day else []
    ladder = limit_ladder(detail=False)

    avg20 = 0.0
    if amounts:
        prev = [_f(a.get("amount"), 0) or 0 for a in amounts[:-1]]
        avg20 = sum(prev) / len(prev) if prev else 0
    vol_ratio = (br["amount"] / avg20) if avg20 else 0
    max_streak = ladder.get("max_streak") or 0
    up_ratio = br.get("up_ratio") or 0
    seal_rate = br.get("seal_rate") or 0
    hl = trend.get("hl_ratio") or 0
    ma5 = trend.get("ma5_ratio") or 0
    ma20 = trend.get("ma20_ratio") or 0

    dims = [
        {"key": "trend", "label": "趋势强度", "value": round(min(100, 60 * ma5 + 40 * ma20), 1),
         "hint": "站上 MA5 / MA20 的个股比例加权"},
        {"key": "breadth", "label": "上涨广度", "value": round(100 * up_ratio, 1),
         "hint": "全市场上涨家数占比"},
        {"key": "seal", "label": "涨停效应", "value": round(100 * (seal_rate or 0), 1),
         "hint": "触板封住比例（封板率）"},
        {"key": "streak", "label": "连板高度", "value": round(min(100, max_streak / 8 * 100), 1),
         "hint": f"最高 {max_streak} 板 / 8 板封顶"},
        {"key": "momentum", "label": "新高动能", "value": round(min(100, 100 * hl), 1),
         "hint": "60 日新高 / 新低家数比"},
        {"key": "volume", "label": "量能活跃", "value": round(min(100, 100 * vol_ratio / 1.5), 1),
         "hint": "当日成交额 / 20 日均额"},
    ]
    score = round(sum(d["value"] for d in dims) / len(dims), 1)
    band = next(b for b in market.SENTIMENT_BANDS if score >= b[0])
    return {
        "trade_date": str(day),
        "score": score,
        "level": band[1],
        "hint": band[2],
        "dims": dims,
        "trend": trend,
        "amount_today": round(br["amount"], 2),
        "amount_avg20": round(avg20, 2),
        "amount_ratio": round(vol_ratio, 4) if avg20 else None,
        "amount_series": amounts,
        "live": True,
        "as_of": br.get("as_of"),
    }


# ---------------------------------------------------------------------------
# 板块热度（东财实时板块榜）
# ---------------------------------------------------------------------------

def _board_page(board_type: str, ascending: bool, limit: int) -> tuple[list[dict], int]:
    params = {
        "pn": "1",
        "pz": str(max(1, limit)),
        "po": "0" if ascending else "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": _BOARD_FS.get(board_type, _BOARD_FS["concept"]),
        "fields": "f3,f12,f14,f62,f104,f105,f128,f136",
    }
    url = "https://push2.eastmoney.com/api/qt/clist/get?" + urllib.parse.urlencode(params)
    data = _get_json(url).get("data") or {}
    rows = data.get("diff") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    out = [
        {
            "code": r.get("f12"),
            "name": r.get("f14"),
            "pct_chg": round(_f(r.get("f3"), 0) or 0, 2),
            "stock_count": None,
            "lead_stock": (r.get("f128") or "").strip(),
            "net_inflow": _f(r.get("f62")),
            "up_count": r.get("f104"),
            "down_count": r.get("f105"),
            "amount": None,
        }
        for r in rows[:limit]
    ]
    return out, int(data.get("total") or 0)


_INDUSTRY_ALIASES = {
    "文字媒体": "数字媒体",
    "视频媒体": "影视院线",
    "模拟芯片设计": "半导体",
    "数字芯片设计": "半导体",
    "集成电路制造": "半导体",
    "集成电路封测": "半导体",
    "半导体设备": "半导体",
    "分立器件": "半导体",
    "光学元件": "光学光电子",
    "LED": "光学光电子",
    "面板": "光学光电子",
    "消费电子零部件": "消费电子",
    "品牌消费电子": "消费电子",
}

# 东财 90 个行业与本地申万 128 个二级行业不是一一对应，合并行业需要一对多。
_INDUSTRY_GROUPS = {
    "IT服务": ["IT服务Ⅱ"],
    "中药": ["中药Ⅱ"],
    "保险": ["保险Ⅱ"],
    "其他电子": ["其他电子Ⅱ"],
    "军工装备": ["航天装备Ⅱ", "航空装备Ⅱ", "地面兵装Ⅱ", "航海装备Ⅱ", "军工电子Ⅱ"],
    "其他社会服务": ["专业服务", "体育Ⅱ", "教育", "旅游及景区"],
    "文化传媒": ["影视院线", "数字媒体", "出版", "广告营销", "游戏Ⅱ"],
    "旅游及酒店": ["旅游及景区", "酒店餐饮", "旅游零售Ⅱ"],
    "机场航运": ["航空机场", "航运港口"],
    "汽车服务及其他": ["汽车服务", "摩托车及其他"],
    "油气开采及服务": ["油气开采Ⅱ", "油服工程"],
    "港口航运": ["航运港口"],
    "煤炭开采加工": ["煤炭开采", "焦炭Ⅱ"],
    "石油加工贸易": ["炼化及贸易"],
    "种植业与林业": ["种植业", "林业Ⅱ"],
    "综合": ["综合Ⅱ"],
    "美容护理": ["化妆品", "个护用品", "医疗美容"],
    "证券": ["证券Ⅱ"],
    "钢铁": ["普钢", "特钢Ⅱ", "冶钢原料"],
    "银行": ["银行Ⅱ"],
    "零售": ["一般零售", "专业连锁Ⅱ", "互联网电商", "旅游零售Ⅱ"],
    "饮料制造": ["饮料乳品", "非白酒", "白酒Ⅱ"],
    "食品加工制造": ["食品加工"],
    "汽车整车": ["乘用车", "商用车"],
    "建筑材料": ["水泥", "玻璃玻纤", "装修建材"],
    "建筑装饰": ["装修装饰Ⅱ", "专业工程", "基础建设", "房屋建设Ⅱ"],
    "房地产": ["房地产开发", "房地产服务"],
    "公路铁路运输": ["铁路公路"],
}


def _industry_rows(name: str, limit: int) -> list[dict]:
    return query(
        """
        SELECT f.ts_code, s.stock_name, f.industry,
               q.close, q.pct_chg, q.amount, q.turnover_rate
        FROM (SELECT DISTINCT ON (ts_code) ts_code, industry
              FROM stock_fundamentals
              WHERE industry IS NOT NULL
              ORDER BY ts_code, report_date DESC) f
        LEFT JOIN stock_basic_info s ON s.ts_code = f.ts_code
        LEFT JOIN LATERAL (
            SELECT d.close, d.pct_chg, d.amount, d.turnover_rate
            FROM daily_quotes d
            WHERE d.ts_code = f.ts_code
            ORDER BY d.trade_date DESC LIMIT 1
        ) q ON TRUE
        WHERE f.industry ILIKE %s OR %s ILIKE f.industry || '%%'
        ORDER BY COALESCE(q.amount, 0) DESC
        LIMIT %s
        """,
        (f"%{name}%", f"%{name}%", int(limit)),
    )


def industry_members(name: str, limit: int = 200) -> list[dict]:
    """按本地财报行业字段取行业成分，并用实时快照覆盖价格。

    东财行业名（如“文字媒体”“模拟芯片设计”）与本地申万行业名不完全一致，
    先做别名映射，再做字符重合度匹配，避免详情页成分股为空。
    """
    if not name:
        return []
    clean = str(name).replace("Ⅱ", "").replace("Ⅲ", "").replace("Ⅰ", "").strip()
    aliases = _INDUSTRY_GROUPS.get(clean)
    if not aliases:
        single = _INDUSTRY_ALIASES.get(clean, clean)
        aliases = [single]
    try:
        rows = []
        seen = set()
        for target in aliases:
            for row in _industry_rows(target, limit):
                if row["ts_code"] not in seen:
                    seen.add(row["ts_code"])
                    rows.append(row)
        if not rows:
            candidates = [
                r["industry"]
                for r in query("SELECT DISTINCT industry FROM stock_fundamentals WHERE industry IS NOT NULL")
            ]
            target_chars = set(clean)
            ranked = sorted(
                (
                    (len(target_chars & set(str(c).replace("Ⅱ", "").replace("Ⅲ", "").replace("Ⅰ", ""))), c)
                    for c in candidates
                ),
                reverse=True,
            )
            if ranked and ranked[0][0] >= 2:
                rows = _industry_rows(ranked[0][1], limit)
    except Exception:  # noqa: BLE001
        return []
    rows = rows[:limit]
    snap = {it["ts_code"]: it for it in _items()}
    out = []
    for r in rows:
        live_it = snap.get(r["ts_code"]) or {}
        out.append(
            {
                "ts_code": r["ts_code"],
                "name": r.get("stock_name") or r["ts_code"],
                "close": _f(live_it.get("price")) or _f(r.get("close")),
                "pct_chg": _f(live_it.get("pct_chg")) if _f(live_it.get("pct_chg")) is not None else _f(r.get("pct_chg")),
                "amount": _f(live_it.get("amount")) or _f(r.get("amount")),
                "turnover_rate": _f(live_it.get("turnover_rate")) if _f(live_it.get("turnover_rate")) is not None else _f(r.get("turnover_rate")),
            }
        )
    out.sort(key=lambda x: -(x.get("pct_chg") or 0))
    return out


@ttl_cache(30)
def board_detail(kind: str, code: str) -> dict | None:
    """东财板块实时详情 + 近 20 日 K 线；成分股接口不稳定，members 返回空。"""
    secid = f"90.{code}"
    try:
        qurl = (
            "https://push2.eastmoney.com/api/qt/stock/get?"
            + urllib.parse.urlencode(
                {
                    "secid": secid,
                    "fltt": "2",
                    "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f169,f170,f171,f168",
                }
            )
        )
        q = _get_json(qurl).get("data") or {}
        if not q.get("f58"):
            return None

        kurl = (
            "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
            + urllib.parse.urlencode(
                {
                    "secid": secid,
                    "klt": "101",
                    "fqt": "1",
                    "lmt": "20",
                    "end": "20500101",
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                }
            )
        )
        kd = _get_json(kurl).get("data") or {}
        history = []
        for line in kd.get("klines") or []:
            p = line.split(",")
            if len(p) < 7:
                continue
            history.append(
                {
                    "trade_date": p[0],
                    "open": _f(p[1]),
                    "close": _f(p[2]),
                    "high": _f(p[3]),
                    "low": _f(p[4]),
                    "volume": _f(p[5]),
                    "amount": _f(p[6]),
                }
            )
        snapshot = {
            "code": code,
            "concept_code" if kind == "concept" else "industry_code": code,
            "concept_name" if kind == "concept" else "industry_name": q.get("f58"),
            "name": q.get("f58"),
            "trade_date": str(today()),
            "close": _f(q.get("f43")),
            "high": _f(q.get("f44")),
            "low": _f(q.get("f45")),
            "open": _f(q.get("f46")),
            "amount": _f(q.get("f48")),
            "pre_close": _f(q.get("f60")),
            "pct_chg": _f(q.get("f170")),
            "change": _f(q.get("f169")),
            "turnover_rate": _f(q.get("f168")),
            "stock_count": None,
            "lead_stock_name": None,
            "net_inflow": None,
        }

        # 行业成分：东财 clist 被断连，退回本地 stock_fundamentals.industry 匹配 + 实时价覆盖
        members = industry_members(q.get("f58"), limit=100) if kind == "industry" else []

        return {
            "kind": kind,
            "code": code,
            "snapshot": snapshot,
            "history": history,
            "member_count": len(members),
            "members": members,
            "members_available": bool(members),
            "live": True,
            "as_of": _as_of(),
            "note": "东财实时板块行情；行业成分来自本地财报行业字段，概念成分暂无免费源",
        }
    except Exception:  # noqa: BLE001
        return None


@ttl_cache(60)
def _limit_concept_sets() -> dict[str, set[str]]:
    """涨跌停类概念没有成分表，用当日/上一交易日涨停梯队反推成员。"""
    items = _items()
    d = market.latest_trade_date()
    if d == today():
        d = (market.trade_dates(2) or [None, None])[1]
    prev = d
    if not prev:
        return {}
    try:
        ladder = market.limit_ladder(prev, detail=True)
    except Exception:  # noqa: BLE001
        return {}
    prev_all = {m["ts_code"] for t in ladder.get("tiers", []) for m in t.get("members", [])}
    prev_2 = {m["ts_code"] for t in ladder.get("tiers", []) if t.get("streak", 0) >= 2 for m in t.get("members", [])}
    prev_1 = {m["ts_code"] for t in ladder.get("tiers", []) if t.get("streak", 0) == 1 for m in t.get("members", [])}
    return {
        "昨日涨停": prev_all,
        "昨日涨停_含一字": prev_all,
        "昨日连板": prev_2,
        "昨日连板_含一字": prev_2,
        "昨日首板": prev_1,
        "昨日打二板以上表现": prev_2,
    }


def enrich_concept_items(items: list[dict]) -> list[dict]:
    """补齐概念成分数/涨跌家数：优先实时字段，其次板块成分缓存，最后涨停梯队反推。"""
    if not items:
        return items
    codes = [x.get("code") for x in items if x.get("code")]
    counts: dict[str, dict] = {}
    if codes:
        try:
            rows = query(
                """
                SELECT b.board_code,
                       COUNT(*) AS n,
                       COUNT(*) FILTER (WHERE q.pct_chg > 0) AS up,
                       COUNT(*) FILTER (WHERE q.pct_chg < 0) AS down
                FROM board_member_cache b
                LEFT JOIN LATERAL (
                    SELECT d.pct_chg FROM daily_quotes d
                    WHERE d.ts_code = b.ts_code
                    ORDER BY d.trade_date DESC LIMIT 1
                ) q ON TRUE
                WHERE b.board_type = 'concept' AND b.board_code = ANY(%s)
                GROUP BY b.board_code
                """,
                (codes,),
            )
            counts = {r["board_code"]: r for r in rows}
        except Exception:  # noqa: BLE001
            counts = {}

    limit_sets = _limit_concept_sets()
    live = {x["ts_code"]: x for x in _items()}
    need = [it for it in items if ths_boards._find_code(it.get("name") or "")]
    infos: dict[str, dict] = {}
    if need:
        def _one(it):
            return it.get("name"), ths_boards.concept_info(it.get("name") or "")

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                for name, info in ex.map(_one, need):
                    if info:
                        infos[name] = info
        except Exception:  # noqa: BLE001
            infos = {}
    for it in items:
        name = it.get("name") or ""
        up0, down0 = it.get("up_count"), it.get("down_count")
        if up0 is not None and down0 is not None and it.get("stock_count") is not None:
            continue
        if name in infos:
            info = infos[name]
            it["stock_count"] = info.get("stock_count")
            it["up_count"] = info.get("up_count")
            it["down_count"] = info.get("down_count")
            if not it.get("amount"):
                it["amount"] = info.get("amount")
            if not it.get("net_inflow"):
                it["net_inflow"] = info.get("net_inflow")
            continue
        count = counts.get(it.get("code"))
        if count:
            it["stock_count"] = int(count["n"])
            it["up_count"] = int(count["up"])
            it["down_count"] = int(count["down"])
            continue
        if name in limit_sets:
            codes_set = limit_sets[name]
            ups = sum(1 for c in codes_set if (_f((live.get(c) or {}).get("pct_chg")) or 0) > 0)
            it["stock_count"] = len(codes_set)
            it["up_count"] = ups
            it["down_count"] = len(codes_set) - ups
            continue
        # 东财实时字段可能已经给出涨跌家数
        up, down = it.get("up_count"), it.get("down_count")
        if up is not None and down is not None:
            it["stock_count"] = int(up) + int(down)
        elif not it.get("stock_count"):
            it["stock_count"] = None
    return items


@ttl_cache(30)
def boards_page(board_type: str, sort: str = "pct_chg_desc", limit: int = 20, offset: int = 0) -> dict:
    """板块列表分页（东财实时榜单），字段与 market._boards 对齐。"""
    sort_map = {
        "pct_chg_desc": ("f3", "1"),
        "pct_chg_asc": ("f3", "0"),
        "net_inflow": ("f62", "1"),
        "stock_count": ("f104", "1"),
    }
    fid, po = sort_map.get(sort, sort_map["pct_chg_desc"])
    page = (offset // max(1, limit)) + 1
    params = {
        "pn": str(page),
        "pz": str(max(1, limit)),
        "po": po,
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": fid,
        "fs": _BOARD_FS.get(board_type, _BOARD_FS["concept"]),
        "fields": "f3,f12,f14,f62,f104,f105,f128",
    }
    url = "https://push2.eastmoney.com/api/qt/clist/get?" + urllib.parse.urlencode(params)
    data = _get_json(url).get("data") or {}
    rows = data.get("diff") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    items = [
        {
            "code": r.get("f12"),
            "name": r.get("f14"),
            "pct_chg": round(_f(r.get("f3"), 0) or 0, 2),
            "stock_count": None,
            "lead_stock_name": (r.get("f128") or "").strip(),
            "net_inflow": _f(r.get("f62")),
            "up_count": int(r["f104"]) if str(r.get("f104", "")).isdigit() else None,
            "down_count": int(r["f105"]) if str(r.get("f105", "")).isdigit() else None,
            "amount": None,
            "rank": offset + i + 1,
        }
        for i, r in enumerate(rows[:limit])
    ]
    for it in items:
        if it["up_count"] is not None and it["down_count"] is not None:
            it["stock_count"] = it["up_count"] + it["down_count"]
    enrich_concept_items(items)
    return {
        "trade_date": str(today()),
        "items": items,
        "total": int(data.get("total") or 0),
        "kind": "concept" if board_type == "concept" else "industry",
        "live": True,
        "as_of": _as_of(),
    }


@ttl_cache(30)
def board_top(board_type: str = "concept", limit: int = 5) -> dict:
    try:
        gainers, total = _board_page(board_type, False, limit)
        losers, _ = _board_page(board_type, True, limit)
        for i, g in enumerate(gainers):
            g["rank"] = i + 1
        for i, l in enumerate(losers):
            l["rank"] = total - i
        return {
            "gainers": gainers,
            "losers": losers,
            "trade_date": str(today()),
            "total": total,
            "live": True,
            "as_of": _as_of(),
        }
    except Exception:  # noqa: BLE001
        return market.board_top(None, board_type, limit)


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------

def overview(refresh: bool = False) -> dict:
    if not available():
        return market.dashboard_overview(refresh=refresh)
    if refresh:
        try:
            live_snapshot.refresh_now()
        except Exception:  # noqa: BLE001
            pass

    day = today()
    br = breadth()
    amounts = amount_series(30)
    return {
        "trade_date": str(day),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "live": True,
        "as_of": _as_of(),
        "data_source": "tencent-live + daily_quotes",
        "indexes": indexes(),
        "breadth": br,
        "sentiment": sentiment(),
        "ladder": limit_ladder(detail=True),
        "rankings": {
            "pct_chg_desc": rankings("pct_chg_desc", 8),
            "pct_chg_asc": rankings("pct_chg_asc", 8),
            "amount": rankings("amount", 8),
            "turnover_rate": rankings("turnover_rate", 8),
        },
        "concepts": board_top("concept", 5),
        "industries": board_top("industry", 5),
        "amount_series": amounts,
    }


def summary() -> dict:
    if not available():
        return {}
    br = breadth()
    senti = sentiment()
    return {
        "trade_date": str(today()),
        "live": True,
        "as_of": _as_of(),
        "score": senti.get("score"),
        "level": senti.get("level"),
        "amount": br.get("amount"),
        "limit_up": br.get("limit_up"),
        "limit_down": br.get("limit_down"),
        "seal_rate": br.get("seal_rate"),
        "up": br.get("up"),
        "down": br.get("down"),
        "flat": br.get("flat"),
    }
