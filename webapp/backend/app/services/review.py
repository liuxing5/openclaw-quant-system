# -*- coding: utf-8 -*-
"""C2 复盘生成 + 情绪历史服务。

为什么复用 ``services/market.py`` 而不是自己写 SQL
------------------------------------------------
本机到远端 PostgreSQL 的链路只有约 40~90 KB/s，一次全市场聚合在冷缓存下要数秒。
``market.py`` 已经把 breadth / sentiment / ladder / rankings / board_top / index
做成了「SQL 下推 + ttl 缓存」，并且 ``warmup`` 后台线程会持续刷新同一批缓存键。
复盘的本质是把这批**小结果**组装成通顺的中文投研语言，因此这里：

  1. 只调用 market.py 的聚合函数，不重写任何全市场 SQL（口径天然一致，
     涨跌停判定与总台同源）；
  2. 调用参数与 ``warmup._jobs()`` 完全一致（ttl_cache 的 key 依赖 repr(args)），
     预热后生成一篇复盘只剩一次龙虎榜查询，秒级完成；
  3. 唯一自写的 SQL 是龙虎榜（单日几十行、有 trade_date 索引）和
     ``app_reviews`` / ``app_sentiment_history`` 两张业务表。

为什么复盘文案要「带数字」
--------------------------
复盘的价值在于可核对：每条要点都落到具体家数、幅度、金额上，读的人可以一眼验证，
而不是「市场情绪回暖」这类放之四海皆准的空话。
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime

from ..cache import ttl_cache
from ..db import execute, query, scalar
from . import market

# 段落骨架：顺序即前端渲染顺序，缺一段都会显得复盘不完整
SECTION_HEADS = ["大盘综述", "涨跌结构", "板块与资金", "龙虎榜与情绪"]

# 情绪历史回填的时间预算：单请求最多花 2 秒现算缺失日期，
# 超出就停（结果标注在 meta.backfilled 上），避免接口被 180 天回填拖死。
BACKFILL_BUDGET_SEC = 2.0
# 超出预算的回填只允许覆盖最近 60 个交易日（契约要求）
BACKFILL_FAST_WINDOW = 60


# --------------------------------------------------------------------------
# 序列化小工具
# --------------------------------------------------------------------------


def _f(v, default=0.0):
    """Decimal / None -> float，保证 JSON 可序列化。"""
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _d(v):
    """date / datetime -> str（psycopg2 的 date 不能直接进 JSON）。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return str(v.date())
    if isinstance(v, date):
        return str(v)
    return str(v.date() if hasattr(v, "date") else v)


def _iso(v):
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _signed(v, nd: int = 2) -> str:
    """带符号百分数 ``+2.35%``，用于行文中的涨跌幅。"""
    if v is None:
        return "—"
    return f"{v:+.{nd}f}%"


def _pct1(v) -> str:
    """一位小数百分数 ``75.5%``。"""
    if v is None:
        return "—"
    return f"{float(v):.1f}%"


def _yi(v) -> str:
    """金额（元）转中文口径：``1.95 万亿`` / ``23.10 亿``。"""
    return market.amount_yi(_f(v))


# --------------------------------------------------------------------------
# 列表 / 详情 / 落库
# --------------------------------------------------------------------------


def list_reviews(page: int = 1, size: int = 20) -> dict:
    """复盘列表：读 ``app_reviews``，字段直接从 jsonb 里抽，空表返回空列表。"""
    page = max(1, int(page))
    size = min(200, max(1, int(size)))
    total = int(scalar("SELECT COUNT(*) FROM app_reviews", (), 0) or 0)
    rows = query(
        """
        SELECT trade_date, region, generated_at,
               content->>'title'   AS title,
               content->>'mood'    AS mood,
               content->>'summary' AS summary,
               (content->>'score')::float AS score
        FROM app_reviews
        ORDER BY trade_date DESC
        LIMIT %s OFFSET %s
        """,
        (size, (page - 1) * size),
    )
    items = [
        {
            "trade_date": _d(r["trade_date"]),
            "region": r["region"],
            "title": r["title"],
            "mood": r["mood"],
            "score": _f(r["score"], None),
            "summary": r["summary"],
            "generated_at": _iso(r["generated_at"]),
        }
        for r in rows
    ]
    return {"items": items, "total": total, "page": page, "size": size}


def get_review(day: date, region: str = "cn") -> dict | None:
    """单日详情。返回时把 ``content`` 平铺到顶层（并保留 content 键），
    前端既可用 ``data.content.sections`` 也可直接用 ``data.title``。"""
    row = query_one_review(day, region)
    if not row:
        return None
    content = row.get("content") or {}
    out = {
        "trade_date": _d(row["trade_date"]),
        "region": row["region"],
        "generated_at": _iso(row.get("generated_at")),
        "content": content,
    }
    for k in ("title", "mood", "score", "summary"):
        out[k] = content.get(k)
    return out


def query_one_review(day: date, region: str) -> dict | None:
    rows = query(
        "SELECT trade_date, region, content, generated_at FROM app_reviews "
        "WHERE trade_date = %s AND region = %s",
        (day, region),
    )
    return rows[0] if rows else None


def has_quote(day: date) -> bool:
    """该交易日是否有行情；没有就无从生成复盘（非交易日走 400）。"""
    return bool(scalar("SELECT COUNT(*) FROM daily_quotes WHERE trade_date = %s", (day,), 0))


def save_review(day: date, content: dict, region: str = "cn") -> None:
    """幂等落库：同一天重复生成直接覆盖（用户点了「重新生成」）。"""
    execute(
        """
        INSERT INTO app_reviews (trade_date, region, content, generated_at)
        VALUES (%s, %s, %s::jsonb, NOW())
        ON CONFLICT (trade_date, region)
        DO UPDATE SET content = EXCLUDED.content, generated_at = NOW()
        """,
        (day, region, json.dumps(content, ensure_ascii=False)),
    )


# --------------------------------------------------------------------------
# 龙虎榜（唯一自写的行情 SQL：单日几十行 + trade_date 索引）
# --------------------------------------------------------------------------


def _lhb(day: date, limit: int = 5) -> dict:
    """净买额前 N + 当日榜上合计，一条 SQL 拿完（窗口函数在 LIMIT 前结算）。"""
    rows = query(
        """
        SELECT ts_code, stock_name, reason, net_amt, buy_amt,
               (SELECT COUNT(DISTINCT ts_code) FROM lhb_detail WHERE trade_date = %s) AS codes,
               COUNT(*) OVER ()              AS rows_n,
               SUM(net_amt) OVER ()          AS day_net
        FROM lhb_detail
        WHERE trade_date = %s
        ORDER BY net_amt DESC NULLS LAST
        LIMIT %s
        """,
        (day, day, limit),
    )
    if not rows:
        return {"items": [], "total": 0, "rows": 0, "net_amount": 0.0}
    top = rows[0]
    return {
        "items": [
            {
                "ts_code": r["ts_code"],
                "name": r["stock_name"],
                "reason": _cap(r["reason"], 60),
                "net_amount": _f(r["net_amt"]),
                "buy_amount": _f(r["buy_amt"]),
            }
            for r in rows
        ],
        "total": int(top.get("codes") or 0),
        "rows": int(top.get("rows_n") or 0),
        "net_amount": _f(top.get("day_net")),
    }


def _cap(s: str, n: int = 200) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


# --------------------------------------------------------------------------
# 句子构造（复盘正文）
# --------------------------------------------------------------------------


def _amount_sentence(amounts: list[dict]) -> str | None:
    """量能句：成交额 + 环比 + 相对 20 日均量 + 连续缩量天数。"""
    if not amounts:
        return None
    hist = [_f(a.get("amount")) for a in amounts]
    today = hist[-1]
    prev = hist[-2] if len(hist) > 1 else 0.0
    avg20 = (sum(hist[:-1]) / len(hist[:-1])) if len(hist) > 1 else today

    # 连续 N 日回落：从最新一天往前数，直到出现环比放量为止
    streak = 0
    for i in range(len(hist) - 1, 0, -1):
        if hist[i] < hist[i - 1]:
            streak += 1
        else:
            break

    txt = f"两市成交 {_yi(today)}"
    if prev:
        txt += f"，较前一交易日{'放量' if today >= prev else '缩量'} {abs(today - prev) / prev * 100:.1f}%"
    if avg20:
        txt += f"，为近 20 日均量 {_yi(avg20)} 的 {today / avg20:.2f} 倍"
    if streak >= 2:
        txt += f"，量能已连续 {streak} 个交易日回落"
    return txt + "。"


def _index_sentence(indexes: list[dict]) -> str | None:
    """指数句：几家涨跌 + 领涨领跌 + 上证点位。"""
    valid = [i for i in indexes if i.get("pct_chg") is not None]
    if not valid:
        return None
    ups = [i for i in valid if i["pct_chg"] > 0]
    downs = [i for i in valid if i["pct_chg"] < 0]
    lead = max(valid, key=lambda x: x["pct_chg"])
    lagg = min(valid, key=lambda x: x["pct_chg"])
    sh = next((i for i in valid if i["ts_code"] == "000001.SH"), valid[0])
    txt = f"主要指数 {len(ups)} 涨 {len(downs)} 跌"
    if lead["ts_code"] != lagg["ts_code"]:
        txt += f"，{lead['name']} 领涨 {_signed(lead['pct_chg'])}，{lagg['name']} 领跌 {_signed(lagg['pct_chg'])}"
    close = _f(sh.get("close"))
    if close:
        txt += f"；{sh['name']} 收报 {close:.2f} 点，{_signed(sh['pct_chg'])}"
    return txt + "。"


def _breadth_sentence(br: dict) -> str | None:
    """广度句：涨跌平家数 + 上涨占比 + 涨跌比。"""
    total = int(br.get("total") or 0)
    if not total:
        return None
    up = int(br.get("up") or 0)
    down = int(br.get("down") or 0)
    flat = int(br.get("flat") or 0)
    up_ratio = br.get("up_ratio")
    txt = (
        f"全市场 {total} 只个股中 {up} 只上涨、{down} 只下跌、{flat} 只平盘，"
        f"上涨占比 {_pct1((up_ratio or 0) * 100)}"
    )
    if down:
        txt += f"，涨跌家数比 {up / down:.2f}:1"
    return txt + "。"


def _ladder_sentence(ladder: dict) -> str | None:
    """连板句：最高板 + 梯队成员。"""
    tiers = ladder.get("tiers") or []
    if not tiers:
        return None
    top_streak = int(ladder.get("max_streak") or 0)
    upper = [t for t in tiers if (t.get("streak") or 0) >= 2]
    n_upper = sum(int(t.get("count") or 0) for t in upper)
    if not upper:
        return f"当日 {int(ladder.get('limit_up') or 0)} 只个股涨停，但均为首次封板，最高连板 {top_streak} 板，梯队高度尚未打开。"
    members = []
    for t in sorted(upper, key=lambda x: -(x.get("streak") or 0))[:3]:
        m = (t.get("members") or [{}])[0]
        if m.get("name"):
            members.append(f"{m['name']}（{t['streak']} 板）")
    txt = f"最高连板 {top_streak} 板，2 板及以上个股共 {n_upper} 只"
    if members:
        txt += "，其中 " + "、".join(members) + " 领衔"
    return txt + "。"


def _histogram_sentence(br: dict) -> str | None:
    """分布句：涨幅超 5% vs 跌幅超 5% 的家数对比。"""
    hist = {h["label"]: int(h["count"] or 0) for h in (br.get("histogram") or [])}
    if not hist:
        return None
    strong_up = hist.get("涨停", 0) + hist.get("涨7~10%", 0) + hist.get("涨5~7%", 0)
    strong_down = hist.get("跌5~7%", 0) + hist.get("跌7~10%", 0) + hist.get("跌停", 0)
    mid_up = hist.get("涨3~5%", 0) + hist.get("涨0~3%", 0)
    mid_down = hist.get("跌0~3%", 0) + hist.get("跌3~5%", 0)
    txt = f"涨幅超 5% 的个股 {strong_up} 只，跌幅超 5% 的 {strong_down} 只，强弱对比 {strong_up}:{strong_down}"
    if mid_up + mid_down:
        txt += f"；涨跌 5% 以内的震荡区间共 {mid_up + mid_down} 只"
    return txt + "。"


def _trend_sentence(trend: dict) -> str | None:
    """趋势句：均线占比 + 60 日新高新低。"""
    if not trend:
        return None
    m5 = trend.get("ma5_ratio")
    m20 = trend.get("ma20_ratio")
    if m5 is None and m20 is None:
        return None
    txt = f"站上 5 日线的个股占比 {_pct1((m5 or 0) * 100)}，站上 20 日线的占比 {_pct1((m20 or 0) * 100)}"
    nh, nl = int(trend.get("new_high") or 0), int(trend.get("new_low") or 0)
    txt += f"；60 日新高 {nh} 家、新低 {nl} 家"
    if nl:
        txt += f"，新高/新低比 {nh / nl:.2f}"
    return txt + "。"


def _leaders_sentence(gain: list[dict], loss: list[dict]) -> str | None:
    """涨跌幅榜首尾句。"""
    if not gain or not loss:
        return None
    g, l = gain[0], loss[0]
    return (
        f"涨幅榜由 {g['name']}（{g['ts_code']}）{_signed(g['pct_chg'])} 领跑，"
        f"跌幅榜 {l['name']} {_signed(l['pct_chg'])} 垫底，"
        f"榜首与榜尾相差 {abs(_f(g['pct_chg']) - _f(l['pct_chg'])):.2f} 个百分点。"
    )


def _amount_top_sentence(rows: list[dict], total_amount) -> str | None:
    """成交集中度句（用 rankings(amount, 8)，与 warmup 同 key）。"""
    if not rows:
        return None
    top5 = rows[:5]
    s = sum(_f(r.get("amount")) for r in top5)
    txt = "成交额前五个股为 " + "、".join(r["name"] for r in top5) + f"，合计 {_yi(s)}"
    if total_amount:
        txt += f"，占两市成交的 {s / _f(total_amount) * 100:.1f}%"
    return txt + "。"


def _board_sentence(board: dict, side: str, topn: int = 3) -> str | None:
    """板块领涨/领跌句（board_top 的 gainers / losers）。"""
    rows = (board.get(side) or [])[:topn]
    if not rows:
        return None
    parts = []
    for r in rows:
        seg = f"{r['name']} {_signed(r['pct_chg'])}"
        lead = (r.get("lead_stock") or "").strip()
        if lead:
            seg += f"（领涨股 {lead}）"
        parts.append(seg)
    if side == "gainers":
        txt = "领涨板块：" + "、".join(parts)
        inflow = rows[0].get("net_inflow")
        if inflow is not None:
            txt += f"；其中 {rows[0]['name']} 主力净流入 {float(inflow):.2f} 亿元"
    else:
        txt = "领跌板块：" + "、".join(parts)
        inflow = rows[0].get("net_inflow")
        if inflow is not None:
            txt += f"；{rows[0]['name']} 净流出 {abs(float(inflow)):.2f} 亿元"
    return txt + "。"


def _dims_sentence(sent: dict) -> str | None:
    """情绪六维句：点出最强与最弱维度。"""
    dims = [d for d in (sent.get("dims") or []) if d.get("value") is not None]
    if not dims:
        return None
    hi = max(dims, key=lambda x: x["value"])
    lo = min(dims, key=lambda x: x["value"])
    txt = (
        f"情绪六维中「{hi['label']}」{hi['value']:.1f} 分最强，"
        f"「{lo['label']}」{lo['value']:.1f} 分最弱"
    )
    if hi["label"] != lo["label"]:
        txt += f"，维度差 {hi['value'] - lo['value']:.1f} 分，结构分化明显"
    return txt + "。"


def _lhb_sentence(lhb: dict) -> str | None:
    """龙虎榜总述句。"""
    items = lhb.get("items") or []
    if not items:
        return None
    top = items[0]
    txt = (
        f"当日龙虎榜上榜 {lhb.get('total') or 0} 只个股，合计净买入 {_yi(lhb.get('net_amount'))}，"
        f"{top['name']} 净买入 {_yi(top['net_amount'])} 位居榜首"
    )
    reason = top.get("reason")
    if reason:
        txt += f"（{reason}）"
    return txt + "。"


def _lhb_detail_sentence(lhb: dict) -> str | None:
    """净买入前五句。"""
    items = lhb.get("items") or []
    if len(items) < 2:
        return None
    parts = [f"{i['name']} {_yi(i['net_amount'])}" for i in items[:5]]
    return "龙虎榜净买入前五：" + "、".join(parts) + "。"


# --------------------------------------------------------------------------
# 复盘生成
# --------------------------------------------------------------------------


def _title(day: date, indexes: list[dict], br: dict, sent: dict, amounts: list[dict]) -> str:
    """标题：量能方向 + 指数方向 + 情绪档位，读起来是一句人话。

    例：``2026-10-23 复盘：放量上行，情绪温和``
    """
    vol_word = "平量"
    if len(amounts) >= 2:
        prev = _f(amounts[-2].get("amount"))
        cur = _f(amounts[-1].get("amount"))
        if prev:
            chg = (cur - prev) / prev * 100
            vol_word = "放量" if chg > 5 else ("缩量" if chg < -5 else "平量")

    sh = next((i for i in indexes if i.get("ts_code") == "000001.SH"), None)
    if sh and sh.get("pct_chg") is not None:
        p = sh["pct_chg"]
    else:
        p = (br.get("up_ratio") or 0.5) * 100 - 50  # 无指数时退化用广度判断方向
    dir_word = "上行" if p > 0.3 else ("回调" if p < -0.3 else "震荡")
    return f"{day} 复盘：{vol_word}{dir_word}，情绪{sent.get('level') or '—'}"


def _breadth_word(br: dict) -> str:
    """摘要里的市场强弱词（按上涨家数占比给一个定性判断）。"""
    up_ratio = br.get("up_ratio")
    if up_ratio is None:
        return "窄幅震荡"
    if up_ratio > 0.55:
        return "普涨"
    if up_ratio < 0.45:
        return "普跌"
    return "分化震荡"


def _summary(day: date, br: dict, sent: dict, amounts: list[dict]) -> str:
    """摘要：两三句把量、涨跌、涨停效应、情绪档位说清楚。"""
    parts = [f"{day} A 股{_breadth_word(br)}"]
    if amounts and len(amounts) >= 2:
        prev = _f(amounts[-2].get("amount"))
        cur = _f(amounts[-1].get("amount"))
        if prev:
            parts.append(
                f"，两市成交 {_yi(cur)}（较前一交易日 "
                f"{'放量' if cur >= prev else '缩量'} {abs(cur - prev) / prev * 100:.1f}%）"
            )
    parts.append(
        f"，{int(br.get('up') or 0)} 家上涨、{int(br.get('down') or 0)} 家下跌，"
        f"涨停 {int(br.get('limit_up') or 0)} 家、封板率 {_pct1((br.get('seal_rate') or 0) * 100)}。"
    )
    parts.append(
        f"情绪综合分 {sent.get('score')}，处于「{sent.get('level')}」区间：{sent.get('hint')}。"
    )
    return "".join(parts)


def _outlook_sentence(br: dict, sent: dict, amounts: list[dict], ind: dict) -> str | None:
    """收尾的后市观察：情绪提示 + 量能条件 + 需要跟踪的板块。"""
    hint = sent.get("hint")
    if not hint:
        return None
    shrinking = False
    if amounts and len(amounts) >= 2:
        prev = _f(amounts[-2].get("amount"))
        cur = _f(amounts[-1].get("amount"))
        shrinking = bool(prev and cur < prev)
    vol_part = (
        "若量能不能重新放大，情绪修复恐难持续"
        if shrinking
        else "量能维持当前水平即可支撑结构性机会"
    )
    leader = ((ind.get("gainers") or [{}])[0].get("name") or "领涨板块")
    return f"后市观察：{hint}；两市成交 {_yi(br.get('amount'))}，{vol_part}，板块端重点跟踪 {leader} 的持续性。"


def generate_review(day: date, region: str = "cn") -> dict | None:
    """由当日数据自动撰写复盘并落库。

    数据全部来自 ``services/market.py`` 的缓存聚合 + 一条龙虎榜 SQL；
    ``day`` 不是交易日（当日无行情）时返回 None，由路由转 400。
    """
    if not has_quote(day):
        return None

    # --- 聚合：参数与 warmup._jobs() 一致，命中同一批缓存键 ---
    br = market.breadth(day)
    sent = market.sentiment(day)  # 内部会复用 breadth/trend/ladder/amount 缓存
    ladder = market.limit_ladder(day, detail=True)
    trend = sent.get("trend") or market.trend_and_newhigh(day)
    amounts = market.amount_series(day, 21)
    gain = market.rankings(day, "pct_chg_desc", 8)
    loss = market.rankings(day, "pct_chg_asc", 8)
    amount_top = market.rankings(day, "amount", 8)
    ind = market.board_top(day, "industry", 5)
    con = market.board_top(day, "concept", 5)
    indexes = market.index_overview(day)
    lhb = _lhb(day, 5)

    score = sent.get("score")
    level = sent.get("level")
    seal = br.get("seal_rate")

    # --- 段落一：大盘综述 ---
    s1 = [s for s in (
        _index_sentence(indexes),
        _amount_sentence(amounts),
        _breadth_sentence(br),
        (f"情绪综合分 {score}，处于「{level}」区间：{sent.get('hint')}。" if score is not None else None),
    ) if s]

    # --- 段落二：涨跌结构 ---
    s2 = [s for s in (
        (f"涨停 {int(br.get('limit_up') or 0)} 家、跌停 {int(br.get('limit_down') or 0)} 家，"
         f"触板 {int(br.get('touched') or 0)} 家、炸板 {int(br.get('broken') or 0)} 家，"
         f"封板率 {_pct1((seal or 0) * 100)}。" if br.get("touched") else None),
        _histogram_sentence(br),
        _ladder_sentence(ladder),
        _trend_sentence(trend),
        _leaders_sentence(gain, loss),
    ) if s]

    # --- 段落三：板块与资金 ---
    s3 = [s for s in (
        _amount_top_sentence(amount_top, br.get("amount")),
        _board_sentence(ind, "gainers"),
        _board_sentence(ind, "losers"),
        _board_sentence(con, "gainers"),
        _board_sentence(con, "losers"),
    ) if s]

    # --- 段落四：龙虎榜与情绪 ---
    s4 = [s for s in (
        _lhb_sentence(lhb),
        _lhb_detail_sentence(lhb),
        _dims_sentence(sent),
        _ladder_sentence(ladder),
        (f"后市观察：{sent.get('hint')}；量能上，{_yi(br.get('amount'))} 的两市成交需要"
         f"{'持续放大才能支撑情绪继续修复' if (amounts and len(amounts) >= 2 and _f(amounts[-1].get('amount')) <= _f(amounts[-2].get('amount'))) else '维持在当前水平即可支撑结构性机会'}，"
         f"板块方面继续跟踪 {((ind.get('gainers') or [{}])[0].get('name') or '领涨板块')} 的持续性。"
         if sent.get("hint") else None),
    ) if s]

    sections = [
        {"heading": h, "bullets": b}
        for h, b in zip(SECTION_HEADS, (s1, s2, s3, s4))
        if b
    ]

    content = {
        "title": _title(day, indexes, br, sent, amounts),
        "trade_date": str(day),
        "mood": level,
        "score": score,
        "summary": _summary(day, br, sent, amounts),
        "sections": sections,
        "metrics": {
            "up": int(br.get("up") or 0),
            "down": int(br.get("down") or 0),
            "flat": int(br.get("flat") or 0),
            "limit_up": int(br.get("limit_up") or 0),
            "limit_down": int(br.get("limit_down") or 0),
            "touched": int(br.get("touched") or 0),
            "broken": int(br.get("broken") or 0),
            "seal_rate": seal,
            "amount": _f(br.get("amount")),
            "amount_ratio": sent.get("amount_ratio"),
            "up_ratio": br.get("up_ratio"),
            "score": score,
            "max_streak": int(ladder.get("max_streak") or 0),
            "new_high": int(trend.get("new_high") or 0),
            "new_low": int(trend.get("new_low") or 0),
        },
        "leaders": {
            "top_gain": [
                {"ts_code": r["ts_code"], "name": r["name"], "close": _f(r.get("close"), None),
                 "pct_chg": _f(r.get("pct_chg"), None), "amount": _f(r.get("amount"), None)}
                for r in gain[:5]
            ],
            "top_loss": [
                {"ts_code": r["ts_code"], "name": r["name"], "close": _f(r.get("close"), None),
                 "pct_chg": _f(r.get("pct_chg"), None), "amount": _f(r.get("amount"), None)}
                for r in loss[:5]
            ],
            "hot_boards": [
                {"code": r.get("code"), "name": r.get("name"), "kind": kind,
                 "pct_chg": _f(r.get("pct_chg"), None),
                 "net_inflow": _f(r.get("net_inflow"), None),
                 "lead_stock": (r.get("lead_stock") or "").strip()}
                for kind, board in (("industry", ind), ("concept", con))
                for r in (board.get("gainers") or [])[:3]
            ],
            "lhb": lhb["items"],
        },
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_review(day, content, region)
    return content


# --------------------------------------------------------------------------
# 情绪历史
# --------------------------------------------------------------------------


@ttl_cache(300)
def _jsonable(v):
    """NaN/Infinity -> None：PostgreSQL 的 jsonb 不接受 NaN 字面量。"""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def sentiment_history(days: int = 180) -> dict:
    """情绪历史序列：优先读 ``app_sentiment_history``，只现算「最新交易日」一个点。

    性能约束（实测）：
      * 单日完整情绪要对 200 万行日线做 60 日窗口/连板/成交额聚合，约 4.5~5 秒；
        180 天全量回填 ≈ 15 分钟，**绝不能放在 HTTP 请求里**。
      * 因此历史缺口由 ``webapp/scripts/backfill_sentiment.py`` 离线回填，
        请求路径只补最新一个交易日（命中 dashboard 预热缓存时 <1 秒）。
      * 结果缓存 300 秒，避免情绪页刷新时重复扫库。
    """
    import time

    start = time.time()
    days = min(500, max(1, int(days or 180)))
    dates = market.trade_dates(days)  # DESC，走 trade_calendar 小表
    if not dates:
        return {"items": [], "total": 0, "meta": {"days": days, "backfilled": {"count": 0}}}

    have_rows = query(
        "SELECT trade_date, score, mood FROM app_sentiment_history "
        "WHERE trade_date = ANY(%s)",
        (dates,),
    )
    have = {}
    for r in have_rows:
        d = _d(r["trade_date"])
        have[d] = {"trade_date": d, "score": _f(r["score"], None), "mood": r["mood"]}

    missing = [d for d in dates if str(d) not in have]
    # 只补最新交易日：老日期交给离线回填脚本，避免把请求拖成分钟级
    latest = dates[0]
    limit = 1 if missing and str(latest) in {str(d) for d in missing} else 0

    backfilled = 0
    truncated = len(missing) > limit
    for day in [d for d in missing if str(d) == str(latest)][:limit]:
        try:
            sent = market.sentiment(day)
        except Exception:  # noqa: BLE001 单日失败不拖垮整条序列
            continue
        score = sent.get("score")
        mood = sent.get("level")
        metrics = {
            "dims": {d["key"]: d["value"] for d in (sent.get("dims") or [])},
            "amount_today": sent.get("amount_today"),
            "amount_ratio": sent.get("amount_ratio"),
            "source": "full_sentiment",
        }
        metrics = _jsonable(metrics)
        if isinstance(score, float) and not math.isfinite(score):
            score = None
        execute(
            """
            INSERT INTO app_sentiment_history (trade_date, score, mood, metrics, updated_at)
            VALUES (%s, %s, %s, %s::jsonb, NOW())
            ON CONFLICT (trade_date)
            DO UPDATE SET score = EXCLUDED.score, mood = EXCLUDED.mood,
                          metrics = EXCLUDED.metrics, updated_at = NOW()
            """,
            (day, score, mood, json.dumps(metrics, ensure_ascii=False)),
        )
        item = {"trade_date": str(day), "score": score, "mood": mood}
        have[str(day)] = item
        backfilled += 1

    elapsed_ms = int((time.time() - start) * 1000)
    items = sorted(
        [have[str(d)] for d in dates if str(d) in have],
        key=lambda x: x["trade_date"],
    )  # 升序，方便直接喂折线图
    return {
        "items": items,
        "total": len(items),
        "meta": {
            "days": days,
            "requested": len(dates),
            "missing_before": len(missing),
            "order": "asc",
            "elapsed_ms": elapsed_ms,
            "backfilled": {
                "count": backfilled,
                "limit": limit,
                "truncated": truncated,
                "budget_sec": BACKFILL_BUDGET_SEC,
                "note": (
                    "历史缺口由 scripts/backfill_sentiment.py 离线回填；请求路径只补最新交易日"
                    if truncated
                    else "本次无缺失日期"
                ),
            },
        },
    }
