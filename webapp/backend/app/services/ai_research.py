# -*- coding: utf-8 -*-
"""多 Agent 个股会诊（可解释规则版）。

吸收外部 daily_stock_analysis 的 Technical / Intel / Risk / Skill / Decision
分层思路，但保持本地系统可复现、无外部 LLM 依赖：

- Technical：趋势、均线、量比、RSI、支撑压力
- Intel：新闻/研报/公告/LLM 多源信号
- Risk：ST、亏损、负债、估值、回撤、负面公告；高风险否决
- Skill：外部策略 YAML 思路的本地规则投影
- Decision：技术 40% + 信息 30% + 风险 30% + 技能 20% 的加权综合
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from ..db import query
from . import realtime, stock_analysis

BUY_WORDS = ("买入", "增持", "推荐", "看多", "强烈推荐", "目标价", "上调评级", "中标", "订单", "预增")
SELL_WORDS = ("卖出", "减持", "看空", "下调评级", "预亏", "亏损", "立案", "处罚", "解禁", "诉讼", "退市", "风险提示")


def _f(v, default=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _signal_score(signal: str) -> float:
    return {"buy": 1.0, "strong_buy": 1.0, "hold": 0.0, "sell": -1.0, "strong_sell": -1.0}.get(signal, 0.0)


def _rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(-n, 0):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag, al = sum(gains) / n, sum(losses) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return round(100 - 100 / (1 + rs), 1)


def _load_context(ts_code: str):
    daily = query(
        """SELECT trade_date, open, high, low, close, pre_close, volume, amount,
                  pct_chg, turnover_rate
           FROM daily_quotes WHERE ts_code = %s ORDER BY trade_date DESC LIMIT 120""",
        (ts_code,),
    )
    daily = list(reversed(daily))
    basic = query("SELECT * FROM stock_basic_info WHERE ts_code = %s LIMIT 1", (ts_code,)) or [{}]
    fund = query(
        "SELECT * FROM stock_fundamentals WHERE ts_code = %s ORDER BY report_date DESC LIMIT 1",
        (ts_code,),
    ) or [{}]
    announcements = query(
        """SELECT title, category, publish_date, url FROM stock_announcements
           WHERE ts_code = %s ORDER BY publish_date DESC LIMIT 10""",
        (ts_code,),
    )
    recs = query(
        """SELECT source_name, recommendation_type, strength, logic_summary, pub_time, confidence
           FROM extracted_recommendations WHERE ts_code = %s
           ORDER BY pub_time DESC NULLS LAST LIMIT 10""",
        (ts_code,),
    )
    live = {}
    try:
        live = (realtime.snapshot_rows([ts_code]) or [{}])[0]
    except Exception:  # noqa: BLE001
        live = {}
    return daily, basic[0], fund[0], announcements, recs, live


def _technical(daily, live, proj):
    closes = [_f(r.get("close")) for r in daily if _f(r.get("close"))]
    highs = [_f(r.get("high")) for r in daily if _f(r.get("high"))]
    lows = [_f(r.get("low")) for r in daily if _f(r.get("low"))]
    price = _f(live.get("price")) or (closes[-1] if closes else None)
    levels = proj.get("levels") or {}
    ma5, ma10, ma20, ma60 = (_f(levels.get(k)) for k in ("ma5", "ma10", "ma20", "ma60"))
    trend = _f(proj.get("trend_score"), 50) or 50
    vr = _f(proj.get("volume_ratio"), 1) or 1
    rsi = _rsi(closes)
    alignment = bool(ma5 and ma10 and ma20 and ma5 > ma10 > ma20 and price and price > ma20)
    if alignment and trend >= 65 and vr >= 1:
        signal, conf = "buy", min(0.9, 0.55 + (trend - 60) / 100)
    elif price and ma20 and price < ma20:
        signal, conf = "sell", min(0.85, 0.55 + max(0, 55 - trend) / 100)
    else:
        signal, conf = "hold", 0.45
    reasons = [
        f"趋势分 {trend:.0f}，量比 {vr:.2f}",
        "均线多头排列" if alignment else "均线尚未形成多头排列",
        f"RSI(14) {rsi}" if rsi is not None else "RSI 数据不足",
    ]
    if levels.get("r1"):
        reasons.append(f"上方 R1 {levels['r1']:.2f}")
    return {
        "agent": "technical",
        "label": "技术面",
        "signal": signal,
        "confidence": round(conf, 2),
        "score": round(trend, 1),
        "reasons": reasons,
    }


def _intel(recs, announcements):
    texts = []
    pos = neg = 0
    for r in recs:
        text = " ".join(str(r.get(k) or "") for k in ("logic_summary", "recommendation_type", "source_name"))
        texts.append(text)
        pos += sum(1 for w in BUY_WORDS if w in text)
        neg += sum(1 for w in SELL_WORDS if w in text)
    for a in announcements:
        text = f"{a.get('category') or ''} {a.get('title') or ''}"
        texts.append(text)
        pos += sum(1 for w in BUY_WORDS if w in text)
        neg += sum(1 for w in SELL_WORDS if w in text)
    strength = sum(_f(r.get("strength"), 0) or 0 for r in recs) / max(1, len(recs))
    if not texts:
        return {
            "agent": "intel",
            "label": "信息面",
            "signal": "hold",
            "confidence": 0.2,
            "score": 50,
            "reasons": ["近期没有可用的新闻/研报/公告信号"],
            "evidence": [],
        }
    if neg > pos:
        signal, conf = "sell", min(0.85, 0.5 + neg * 0.08)
    elif pos > neg or strength >= 3:
        signal, conf = "buy", min(0.85, 0.5 + pos * 0.06 + strength * 0.03)
    else:
        signal, conf = "hold", 0.4
    return {
        "agent": "intel",
        "label": "信息面",
        "signal": signal,
        "confidence": round(conf, 2),
        "score": round(max(0, min(100, 50 + (pos - neg) * 8 + strength * 5)), 1),
        "reasons": [f"近期信号 {len(texts)} 条", f"正面词 {pos} / 负面词 {neg}", f"平均推荐强度 {strength:.1f}"],
        "evidence": texts[:4],
    }


def _risk(daily, basic, fund, live, announcements, proj):
    flags = []
    if basic.get("is_st"):
        flags.append({"severity": "high", "category": "ST", "description": "ST/*ST 风险标的"})
    profit = _f(fund.get("net_profit"))
    if profit is not None and profit < 0:
        flags.append({"severity": "high", "category": "业绩", "description": "最近报告期净利润为负"})
    debt = _f(fund.get("debt_ratio"))
    if debt is not None and debt > 70:
        flags.append({"severity": "medium", "category": "负债", "description": f"资产负债率 {debt:.1f}%"})
    pe, pb = _f(fund.get("pe_ratio")), _f(fund.get("pb_ratio"))
    if pe is not None and (pe < 0 or pe > 100):
        flags.append({"severity": "medium", "category": "估值", "description": f"PE {pe:.1f} 异常"})
    if pb is not None and pb > 10:
        flags.append({"severity": "medium", "category": "估值", "description": f"PB {pb:.1f} 偏高"})
    price = _f(live.get("price")) or ( _f(daily[-1].get("close")) if daily else None)
    high20 = _f((proj.get("levels") or {}).get("high20"))
    if price and high20 and price / high20 - 1 < -0.15:
        flags.append({"severity": "medium", "category": "技术", "description": "距 20 日高点回撤超过 15%"})
    for a in announcements:
        text = f"{a.get('category') or ''} {a.get('title') or ''}"
        bad = [w for w in ("减持", "预亏", "立案", "处罚", "解禁", "诉讼", "退市") if w in text]
        if bad:
            flags.append({"severity": "high" if any(w in text for w in ("立案", "退市", "预亏")) else "medium",
                          "category": "公告", "description": text[:80]})
    hard = any(f["severity"] == "high" for f in flags)
    return {
        "agent": "risk",
        "label": "风险面",
        "signal": "sell" if hard else "hold",
        "confidence": 0.85 if hard else (0.6 if flags else 0.55),
        "score": max(0, 100 - sum(35 if f["severity"] == "high" else 12 for f in flags)),
        "flags": flags,
        "veto": hard,
        "reasons": [f["description"] for f in flags[:4]] or ["未发现高优先级风险标记"],
    }


def _skills(daily, basic, fund, proj, intel, risk):
    levels = proj.get("levels") or {}
    price = _f(proj.get("price"))
    ma5, ma10, ma20, ma60 = (_f(levels.get(k)) for k in ("ma5", "ma10", "ma20", "ma60"))
    vr = _f(proj.get("volume_ratio"), 1) or 1
    high20 = _f(levels.get("high20"))
    out = []

    def add(skill_id, name, ok, signal, reason):
        out.append({"skill": skill_id, "name": name, "matched": bool(ok), "signal": signal if ok else "hold",
                    "confidence": 0.7 if ok else 0.25, "reason": reason})

    add("bull_trend", "多头趋势", bool(ma5 and ma10 and ma20 and ma5 > ma10 > ma20 and price > ma20),
        "buy", "MA5>MA10>MA20 且价格站上 MA20")
    add("volume_breakout", "放量突破", bool(price and high20 and price >= high20 * 0.99 and vr >= 1.5),
        "buy", "接近/突破 20 日高且量比≥1.5")
    add("shrink_pullback", "缩量回踩", bool(price and ma20 and abs(price / ma20 - 1) <= 0.03 and vr < 1),
        "buy", "价格贴近 MA20 且缩量")
    add("bottom_volume", "底部放量", bool(ma60 and price and price < ma60 and vr >= 2 and _f(daily[-1].get("pct_chg"), 0) > 3),
        "buy", "低位放量反弹")
    add("growth_quality", "成长质量", bool((_f(fund.get("profit_yoy"), 0) or 0) > 20 and (_f(fund.get("roe"), 0) or 0) > 10),
        "buy", "利润增速>20% 且 ROE>10%")
    add("event_driven", "事件驱动", intel.get("signal") == "buy" and intel.get("score", 0) >= 55,
        "buy", "信息面出现正向事件/研报信号")
    add("risk_guard", "风险规避", not risk.get("veto") and not risk.get("flags"),
        "buy", "无高优先级风险标记")
    return out


def research(code: str) -> dict | None:
    ts_code = stock_analysis._norm(code)
    proj = stock_analysis.projection(ts_code)
    if not proj:
        return None
    daily, basic, fund, announcements, recs, live = _load_context(ts_code)
    tech = _technical(daily, live, proj)
    intel = _intel(recs, announcements)
    risk = _risk(daily, basic, fund, live, announcements, proj)
    skills = _skills(daily, basic, fund, proj, intel, risk)
    skill_avg = sum(_signal_score(s["signal"]) for s in skills) / max(1, len(skills))
    weighted = (
        _signal_score(tech["signal"]) * tech["confidence"] * 0.40
        + _signal_score(intel["signal"]) * intel["confidence"] * 0.30
        + _signal_score(risk["signal"]) * risk["confidence"] * 0.30
        + skill_avg * 0.20
    )
    if risk.get("veto"):
        decision = "hold"
        confidence = "低"
        advice = "存在高优先级风险，暂不参与，等待风险解除"
    elif weighted >= 0.30:
        decision = "buy"
        confidence = "高" if weighted >= 0.65 else "中"
        advice = "可纳入观察/回踩分批，严格设置止损"
    elif weighted <= -0.25:
        decision = "sell"
        confidence = "中"
        advice = "趋势或风险偏弱，反弹减仓"
    else:
        decision = "hold"
        confidence = "中"
        advice = "信号不一致，等待突破或回踩确认"
    tech_score = max(0, min(100, 50 + weighted * 50))
    limitations = []
    if daily:
        last = str(daily[-1].get("trade_date"))
        if last < date.today().isoformat():
            limitations.append(f"日线最新到 {last}")
    else:
        limitations.append("日线数据不足")
    if not recs and not announcements:
        limitations.append("近期无新闻/研报/公告证据")
    if not live.get("price"):
        limitations.append("实时价不可用，使用最新收盘价")
    return {
        "ts_code": ts_code,
        "name": live.get("name") or proj.get("name") or ts_code,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "method": "deterministic-multi-agent",
        "decision": {
            "type": decision,
            "score": round(tech_score, 1),
            "confidence": confidence,
            "advice": advice,
            "weighted": round(weighted, 3),
            "target": proj.get("target"),
            "stop": proj.get("stop"),
            "probability": proj.get("probability"),
        },
        "agents": [tech, intel, risk],
        "skills": skills,
        "risk_flags": risk.get("flags") or [],
        "evidence": {
            "news": [r.get("logic_summary") for r in recs[:3] if r.get("logic_summary")],
            "announcements": [a.get("title") for a in announcements[:3] if a.get("title")],
        },
        "data_limitations": limitations,
        "disclaimer": "多 Agent 规则会诊，仅用于研究，不构成投资建议。",
    }
