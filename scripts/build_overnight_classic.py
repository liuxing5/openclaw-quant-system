# -*- coding: utf-8 -*-
"""原版八步隔夜法：腾讯/新浪 1 分钟（历史用 5 分钟）计算 VWAP、相对强弱、
14:30 后新高、回踩 VWAP 不破，输出 funnel 页面可渲染的 JSON。

用法：
    python scripts/build_overnight_classic.py --date 2026-10-27
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webapp" / "backend"))


def _ensure_database_url() -> None:
    """GitHub Actions 只提供 POSTGRES_*，webapp/db.py 需要 DATABASE_URL。"""
    if os.getenv("DATABASE_URL") or os.getenv("WEBAPP_DATABASE_URL"):
        return
    host = os.getenv("POSTGRES_HOST")
    if not host:
        return
    user = quote(os.getenv("POSTGRES_USER", ""), safe="")
    password = quote(os.getenv("POSTGRES_PASSWORD", ""), safe="")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "postgres")
    url = f"postgresql://{user}:{password}@{host}:{port}/{db}?sslmode=require"
    os.environ["DATABASE_URL"] = url
    os.environ["WEBAPP_DATABASE_URL"] = url


_ensure_database_url()

from app.db import query  # noqa: E402


def _bars(symbol: str, scale: int = 1, datalen: int = 1023) -> list[dict]:
    url = (
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/"
        f"var%20_{symbol}=/CN_MarketDataService.getKLineData?"
        f"symbol={symbol}&scale={scale}&ma=no&datalen={datalen}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        text = resp.read().decode("utf-8", "replace")
    i = text.find("=(")
    if i < 0:
        return []
    arr = json.loads(text[i + 2 : text.rfind(")")])
    out = []
    for b in arr or []:
        try:
            out.append(
                {
                    "dt": datetime.strptime(b["day"], "%Y-%m-%d %H:%M:%S"),
                    "open": float(b["open"]),
                    "high": float(b["high"]),
                    "low": float(b["low"]),
                    "close": float(b["close"]),
                    "volume": float(b["volume"]),
                    "amount": float(b["amount"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _sina_symbol(code: str) -> str:
    return ("sh" if code.endswith(".SH") else "sz") + code.split(".")[0]


def _index_symbol(code: str) -> str:
    return "sh000001" if code.endswith(".SH") else "sz399001"


def _daily_candidates(day: str) -> list[dict]:
    return query(
        """
        WITH base AS (
            SELECT q.ts_code, q.trade_date, q.close, q.volume, q.pct_chg,
                   q.turnover_rate, q.amount,
                   COALESCE(q.circulating_market_cap, q.total_market_cap) AS cap_base,
                   CASE
                       WHEN q.turnover_rate IS NOT NULL THEN q.turnover_rate
                       WHEN COALESCE(q.circulating_market_cap, q.total_market_cap) > 0
                       THEN q.amount / COALESCE(q.circulating_market_cap, q.total_market_cap) * 100.0
                   END AS turn_est,
                   AVG(q.volume) OVER (
                       PARTITION BY q.ts_code ORDER BY q.trade_date
                       ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
                   ) AS vma5,
                   EXISTS (
                       SELECT 1 FROM daily_quotes z
                       WHERE z.ts_code = q.ts_code
                         AND z.trade_date BETWEEN q.trade_date - INTERVAL '30 days'
                                              AND q.trade_date - INTERVAL '1 day'
                         AND z.pct_chg >= 9.5
                   ) AS had_limit_up
            FROM daily_quotes q
            WHERE q.trade_date BETWEEN %s::date - INTERVAL '60 days' AND %s::date
        )
        SELECT b.*, COALESCE(b.cap_base,
                   CASE WHEN b.turn_est > 0 THEN b.amount / (b.turn_est / 100.0) END) AS cap_est,
               s.stock_name
        FROM base b
        LEFT JOIN stock_basic_info s ON s.ts_code = b.ts_code
        WHERE b.trade_date = %s::date
          AND (b.ts_code LIKE '60%%' OR b.ts_code LIKE '00%%')
          AND COALESCE(s.is_st, false) = false
          AND b.pct_chg BETWEEN 3 AND 5
          AND b.turn_est BETWEEN 5 AND 10
          AND b.cap_base BETWEEN 5e9 AND 2e10
          AND b.volume > b.vma5
          AND b.had_limit_up
        ORDER BY b.amount DESC
        """,
        (day, day, day),
    )


def _intraday(code: str, day: str) -> dict | None:
    scale = 1
    bars = _bars(_sina_symbol(code), 1)
    # 1m 接口通常只保留约 4 个交易日；更早日期回退 5m
    if not any(b["dt"].date().isoformat() == day for b in bars):
        scale = 5
        bars = _bars(_sina_symbol(code), 5)
    d = datetime.strptime(day, "%Y-%m-%d").date()
    day_bars = [b for b in bars if b["dt"].date() == d]
    if not day_bars:
        return None
    t1430 = time(14, 30)
    prior = [b for b in day_bars if b["dt"].time() < t1430]
    at_or_after = [b for b in day_bars if b["dt"].time() >= t1430]
    if not prior or not at_or_after:
        return None
    pre = [b for b in day_bars if b["dt"].time() <= t1430]
    after = at_or_after
    volume = sum(b["volume"] for b in day_bars)
    amount = sum(b["amount"] for b in day_bars)
    vwap = amount / volume if volume else 0
    entry = at_or_after[0]["close"]
    last = day_bars[-1]["close"]

    idx_bars = _bars(_index_symbol(code), scale)
    idx_day = [b for b in idx_bars if b["dt"].date() == d]
    stock_start = next((b["open"] for b in day_bars if b["dt"].time() >= time(9, 31)), day_bars[0]["open"])
    stock_ret = entry / stock_start - 1 if stock_start else 0
    idx_ret = 0
    if idx_day:
        idx_start = next((b["open"] for b in idx_day if b["dt"].time() >= time(9, 31)), idx_day[0]["open"])
        idx_ret = idx_day[-1]["close"] / idx_start - 1 if idx_start else 0

    new_high = max(b["high"] for b in after) > max(b["high"] for b in prior) * 1.0005
    pullback = min(b["low"] for b in after) <= vwap * 1.01 and last >= vwap * 0.995
    above_vwap = entry >= vwap and last >= vwap
    stronger = stock_ret > idx_ret
    return {
        "vwap": round(vwap, 3),
        "entry": round(entry, 3),
        "last": round(last, 3),
        "new_high": new_high,
        "pullback": pullback,
        "above_vwap": above_vwap,
        "stronger": stronger,
        "confirmed": bool(pullback and above_vwap and stronger),
        "scale": f"{scale}m",
        "stock_ret": round(stock_ret * 100, 2),
        "index_ret": round(idx_ret * 100, 2),
    }


def _live_candidates(day: str) -> list[dict]:
    """14:30 盘中扫描：日线历史算均线/压力，腾讯实时快照算当日涨幅/换手/市值。"""
    from app.services import live_snapshot

    snap = live_snapshot.snapshot()
    hist = query(
        """
        SELECT ts_code, trade_date, close, pct_chg, volume
        FROM daily_quotes
        WHERE trade_date < %s::date
          AND trade_date >= %s::date - INTERVAL '45 days'
        ORDER BY ts_code, trade_date
        """,
        (day, day),
    )
    by_code: dict[str, list] = {}
    for r in hist:
        by_code.setdefault(r["ts_code"], []).append(r)

    out = []
    for item in snap.get("items") or []:
        code = item.get("ts_code")
        rows = by_code.get(code) or []
        if not code or len(rows) < 5 or item.get("is_st"):
            continue
        if not (code.startswith("60") or code.startswith("00")):
            continue
        pct = item.get("pct_chg")
        turn = item.get("turnover_rate")
        cap = item.get("circulating_market_cap") or item.get("market_cap")
        vr = item.get("volume_ratio")
        price = item.get("price")
        if pct is None or turn is None or cap is None or not price:
            continue
        if not (3 <= pct <= 5 and 5 <= turn <= 10 and 5e9 <= cap <= 2e10):
            continue
        if vr is not None and vr <= 1:
            continue
        if not any(float(r.get("pct_chg") or 0) >= 9.5 for r in rows[-20:]):
            continue
        out.append(
            {
                "ts_code": code,
                "stock_name": item.get("name") or code,
                "trade_date": day,
                "pct_chg": pct,
                "turnover_rate": turn,
                "cap_est": cap,
                "amount": item.get("amount") or 0,
                "volume_ratio": vr,
            }
        )
    out.sort(key=lambda x: -(x.get("amount") or 0))
    return out[:60]


def build(day: str, live: bool = False) -> dict:
    rows = _live_candidates(day) if live else _daily_candidates(day)
    candidates = []
    rejected = 0
    confirmed_count = 0
    for r in rows:
        x = _intraday(r["ts_code"], day)
        if not x:
            rejected += 1
            continue
        confirmed_count += 1 if x["confirmed"] else 0
        tags = []
        if x["above_vwap"]:
            tags.append("VWAP上方")
        if x["stronger"]:
            tags.append("强于大盘")
        if x["new_high"]:
            tags.append("14:30新高")
        if x["pullback"]:
            tags.append("回踩不破")
        if not x["confirmed"]:
            tags.append("待确认")
        candidates.append(
            {
                "code": r["ts_code"],
                "name": r.get("stock_name") or r["ts_code"],
                "score": round(float(r["pct_chg"]) * 10 + 50, 1),
                "entry": x["entry"],
                "stop": x["vwap"],
                "target": round(x["entry"] * 1.03, 3),
                "quant_score": round(float(r["pct_chg"]) * 10 + 50, 1),
                "tags": tags,
                "source": "腾讯1m" if x["scale"] == "1m" else "新浪5m",
                "vwap": x["vwap"],
                "pct_chg": round(float(r["pct_chg"]), 2),
                "confirmed": x["confirmed"],
                "path": "原版确认" if x["confirmed"] else "待确认",
            }
        )
    steps = [
        {"name": "14:30-14:50 尾盘窗口", "pass": len(rows), "eliminated": "", "rule": "只做尾盘，不碰 14:55 突击拉高"},
        {"name": "主板 + 涨幅 3%~5%", "pass": len(rows), "eliminated": "", "rule": "仅 60/00 主板，排除 300/688/ST"},
        {"name": "近 20 日涨停基因", "pass": len(rows), "eliminated": "", "rule": "近 20 个交易日至少一次涨停"},
        {"name": "量比 > 1", "pass": len(rows), "eliminated": "", "rule": "当日量能高于近 5 日均量"},
        {"name": "换手率 5%~10%", "pass": len(rows), "eliminated": "", "rule": "兼顾流动性与不过热"},
        {"name": "流通市值 50~200亿", "pass": len(rows), "eliminated": "", "rule": "中盘弹性区间"},
        {"name": "VWAP + 强于大盘", "pass": len(rows), "eliminated": rejected, "rule": "全天在 VWAP 上方，且强于上证/深证"},
        {"name": "回踩 VWAP 不破", "pass": confirmed_count, "eliminated": len(rows) - confirmed_count, "rule": "尾盘回踩均价线不有效跌破，次日 9:30-9:40 清仓"},
    ]
    return {
        "title": "最严格杨永兴版",
        "date": day,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "steps": steps,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "confirmed_count": confirmed_count,
        "note": "严格规则：主板、近20日涨停基因、VWAP/强于大盘、尾盘回踩确认；次日 9:30-9:40 必须清仓。",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True)
    p.add_argument("--live", action="store_true", help="14:30 盘中实时扫描，不依赖当日日线入库")
    p.add_argument("--output", default=str(ROOT / "strategies/funnel_strategy/docs/overnight_classic.json"))
    args = p.parse_args()
    data = build(args.date, live=args.live)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"classic {data['candidate_count']} candidates -> {out}")


if __name__ == "__main__":
    main()
