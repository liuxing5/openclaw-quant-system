# -*- coding: utf-8 -*-
"""全市场智能深度筛选。

不是把 5000 只股票拉回 Python 打分，而是**一条 SQL** 在库端完成：
近 80 个交易日的均线/量能/20 日高低点、最新资金流、最新财报成长与估值，
再按多因子权重合成 0~100 分，只回传前 N 名和可解释理由。

预设（preset）只改变硬过滤条件，总分权重保持一致，避免用户看到
「换个名字分数完全不一样」；每个因子分都单独返回，前端可展开。
"""
from __future__ import annotations

import threading
import time
from datetime import date, timedelta

from ..cache import ttl_cache
from ..db import db_session, query, scalar

_TABLE = "app_smart_snapshot"
_COLS = (
    "trade_date, ts_code, stock_name, industry, close, pct_chg, turnover_rate, amount, volume, "
    "total_market_cap, ma5, ma10, ma20, ma60, hh20, ll20, vma20, main_force_net, "
    "revenue_yoy, profit_yoy, roe, debt_ratio, trend_score, momentum_score, volume_score, "
    "capital_score, quality_score, valuation_score, risk_score, score"
)
_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    trade_date date NOT NULL,
    ts_code varchar(16) NOT NULL,
    stock_name varchar(64),
    industry varchar(64),
    close numeric,
    pct_chg numeric,
    turnover_rate numeric,
    amount numeric,
    volume numeric,
    total_market_cap numeric,
    ma5 numeric, ma10 numeric, ma20 numeric, ma60 numeric,
    hh20 numeric, ll20 numeric, vma20 numeric,
    main_force_net numeric,
    revenue_yoy numeric, profit_yoy numeric, roe numeric, debt_ratio numeric,
    trend_score numeric, momentum_score numeric, volume_score numeric,
    capital_score numeric, quality_score numeric, valuation_score numeric, risk_score numeric,
    score numeric,
    updated_at timestamptz DEFAULT now(),
    PRIMARY KEY (trade_date, ts_code)
)
"""

PRESETS = {
    "smart": {"label": "综合智能", "where": "1=1"},
    "trend": {"label": "趋势强势", "where": "l.close > l.ma20 AND l.ma5 > l.ma10"},
    "growth": {"label": "成长质量", "where": "(f.revenue_yoy >= 15 OR f.profit_yoy >= 20)"},
    "value": {"label": "低估值", "where": "l.pe_ratio > 0 AND l.pe_ratio < 35 AND l.pb_ratio > 0 AND l.pb_ratio < 4"},
    "capital": {"label": "资金驱动", "where": "COALESCE(l.main_force_net, 0) > 0"},
    "breakout": {"label": "突破新高", "where": "l.close >= l.hh20 * 0.98"},
    "reversal": {"label": "超跌反弹", "where": "l.pct_chg < -2 AND l.close > l.ma5"},
    "leader": {"label": "龙头强势", "where": "l.pct_chg >= 5 AND l.turnover_rate >= 5"},
    "quality": {"label": "高ROE", "where": "COALESCE(f.net_profit / NULLIF(f.equity, 0) * 100, 0) >= 10"},
    "dividend": {"label": "蓝筹低波", "where": "l.total_market_cap >= 20000000000 AND l.pe_ratio > 0 AND l.pe_ratio < 25"},
    "winrate": {
        "label": "胜率优先",
        "where": (
            "l.close > l.ma20 AND l.ma20 > l.ma60 AND l.ma5 > l.ma10 "
            "AND l.close > l.ma5 * 0.97 AND l.close >= l.ma20 * 1.03 AND l.close < l.ma20 * 1.08 "
            "AND l.pct_chg BETWEEN 0 AND 2 "
            "AND l.amount BETWEEN 200000000 AND 2000000000 "
            "AND l.turnover_rate BETWEEN 3 AND 8 "
            "AND (l.volume / NULLIF(l.vma20, 0)) BETWEEN 1.1 AND 2.2 "
            "AND (SELECT breadth_pct FROM breadth) >= 0.55"
        ),
    },
}

_WHERE = {
    "": "",
    "A主板": "AND (l.ts_code LIKE '60%' OR l.ts_code LIKE '00%')",
    "创业板": "AND l.ts_code LIKE '30%'",
    "科创板": "AND l.ts_code LIKE '68%'",
    "北交所": "AND (l.ts_code LIKE '4%' OR l.ts_code LIKE '8%' OR l.ts_code LIKE '920%')",
}

_SQL = """
WITH days AS (SELECT MAX(trade_date) AS day FROM daily_quotes),
h AS (
    SELECT q.ts_code, q.trade_date, q.close, q.high, q.low, q.volume, q.amount,
           q.pct_chg, q.turnover_rate, q.main_force_net, q.total_market_cap,
           q.pe_ratio, q.pb_ratio,
           ROW_NUMBER() OVER (PARTITION BY q.ts_code ORDER BY q.trade_date DESC) AS rn
    FROM daily_quotes q, days
    WHERE q.trade_date >= days.day - INTERVAL '100 days'
),
agg AS (
    SELECT ts_code,
           MAX(close) FILTER (WHERE rn = 1) AS close,
           MAX(volume) FILTER (WHERE rn = 1) AS volume,
           MAX(amount) FILTER (WHERE rn = 1) AS amount,
           MAX(pct_chg) FILTER (WHERE rn = 1) AS pct_chg,
           MAX(turnover_rate) FILTER (WHERE rn = 1) AS turnover_rate,
           MAX(main_force_net) FILTER (WHERE rn = 1) AS main_force_net,
           MAX(total_market_cap) FILTER (WHERE rn = 1) AS total_market_cap,
           MAX(pe_ratio) FILTER (WHERE rn = 1) AS pe_ratio,
           MAX(pb_ratio) FILTER (WHERE rn = 1) AS pb_ratio,
           AVG(close) FILTER (WHERE rn <= 5) AS ma5,
           AVG(close) FILTER (WHERE rn <= 10) AS ma10,
           AVG(close) FILTER (WHERE rn <= 20) AS ma20,
           AVG(close) FILTER (WHERE rn <= 60) AS ma60,
           AVG(volume) FILTER (WHERE rn <= 20) AS vma20,
           MAX(high) FILTER (WHERE rn <= 20) AS hh20,
           MIN(low) FILTER (WHERE rn <= 20) AS ll20
    FROM h
    GROUP BY ts_code
),
breadth AS (
    SELECT AVG(CASE WHEN close > ma20 THEN 1.0 ELSE 0.0 END) AS breadth_pct FROM agg
),
fund AS (
    SELECT DISTINCT ON (ts_code) ts_code, revenue_yoy, profit_yoy, equity, net_profit, debt_ratio, roe, industry
    FROM (
        SELECT ts_code, report_date, revenue_yoy, profit_yoy, equity, net_profit, debt_ratio, industry,
               CASE WHEN equity > 0 THEN net_profit / equity * 100 ELSE NULL END AS roe
        FROM stock_fundamentals
        WHERE report_date IS NOT NULL
    ) x
    ORDER BY ts_code, report_date DESC
),
scored AS (
    SELECT (SELECT day FROM days) AS trade_date, l.ts_code, s.stock_name, f.industry,
           l.close, l.pct_chg, l.turnover_rate, l.amount, l.volume, l.total_market_cap,
           l.ma5, l.ma10, l.ma20, l.ma60, l.hh20, l.ll20, l.vma20,
           l.main_force_net, f.revenue_yoy, f.profit_yoy, f.roe, f.debt_ratio,
           -- 趋势 0~100
           (CASE WHEN l.close > l.ma5 THEN 15 ELSE 0 END
          + CASE WHEN l.close > l.ma10 THEN 15 ELSE 0 END
          + CASE WHEN l.close > l.ma20 THEN 20 ELSE 0 END
          + CASE WHEN l.ma5 > l.ma10 AND l.ma10 > l.ma20 THEN 20 ELSE 0 END
          + CASE WHEN l.close > l.ma60 THEN 15 ELSE 0 END
          + CASE WHEN l.hh20 > 0 THEN GREATEST(0, 15 - (l.hh20 - l.close) / NULLIF(l.close, 0) * 100) ELSE 0 END
           ) AS trend_score,
           -- 动量 0~100
           LEAST(100, GREATEST(0, 50 + COALESCE(l.pct_chg, 0) * 4
                + (l.close / NULLIF(l.ma20, 0) - 1) * 100 * 1.5)) AS momentum_score,
           -- 量能 0~100
           LEAST(100, GREATEST(0, (l.volume / NULLIF(l.vma20, 0) - 0.6) * 60)) AS volume_score,
           -- 资金 0~100
           LEAST(100, GREATEST(0, 50 + COALESCE(l.main_force_net, 0) / NULLIF(l.amount, 0) * 400)) AS capital_score,
           -- 质量 0~100
           LEAST(100, GREATEST(0, COALESCE(f.roe, 0) * 4 + COALESCE(f.revenue_yoy, 0) * 1.2
                + COALESCE(f.profit_yoy, 0) * 0.6)) AS quality_score,
           -- 估值 0~100
           LEAST(100, GREATEST(0,
             (CASE WHEN l.pe_ratio > 0 AND l.pe_ratio < 80 THEN 100 - l.pe_ratio * 1.1 ELSE 40 END
            + CASE WHEN l.pb_ratio > 0 AND l.pb_ratio < 10 THEN 20 - l.pb_ratio * 2 ELSE 0 END)
           )) AS valuation_score,
           -- 风险 0~100（越高越稳）
           LEAST(100, GREATEST(0, 100 - COALESCE(l.pct_chg, 0) * 1.5 - COALESCE(f.debt_ratio, 0) * 0.35
                - (l.hh20 - l.ll20) / NULLIF(l.close, 0) * 100)) AS risk_score
    FROM agg l
    JOIN stock_basic_info s ON s.ts_code = l.ts_code
    LEFT JOIN fund f ON f.ts_code = l.ts_code
    WHERE COALESCE(s.is_active, TRUE) AND l.close > 0
      AND ({preset_where}) {board_where}
),
ranked AS (
    SELECT *,
           ROUND((
               trend_score * 0.30 + momentum_score * 0.10 + volume_score * 0.15
             + capital_score * 0.15 + quality_score * 0.15 + valuation_score * 0.10
             + risk_score * 0.05
           )::numeric, 1) AS score
    FROM scored
)
SELECT * FROM ranked
WHERE score >= %s
ORDER BY score DESC, amount DESC
LIMIT %s
"""


def refresh_snapshot() -> int:
    """把全市场评分物化到 app_smart_snapshot（约 40s，离线/每日刷新用）。"""
    base = _SQL.format(preset_where="1=1", board_where="").split("SELECT * FROM ranked")[0]
    base += "SELECT * FROM ranked WHERE score >= 0"
    with db_session() as conn:
        cur = conn.cursor()
        try:
            cur.execute(_DDL)
            cur.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS volume numeric")
            cur.execute(f"TRUNCATE {_TABLE}")
            cur.execute(f"INSERT INTO {_TABLE} ({_COLS}) {base}")
            n = cur.rowcount
            conn.commit()
            return int(n or 0)
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


_refresh_lock = threading.Lock()
_refreshing = False
_last_check = 0.0


def ensure_fresh() -> None:
    """数据日推进后，后台异步重建物化表；本次请求仍返回旧快照，不阻塞用户。"""
    global _refreshing, _last_check
    if time.time() - _last_check < 300:
        return
    _last_check = time.time()
    try:
        latest = scalar("SELECT MAX(trade_date) FROM daily_quotes")
        snap = scalar(f"SELECT MAX(trade_date) FROM {_TABLE}")
    except Exception:  # noqa: BLE001
        return
    if not latest or latest == snap or _refreshing:
        return
    with _refresh_lock:
        if _refreshing:
            return
        _refreshing = True

    def _job():
        global _refreshing
        try:
            refresh_snapshot()
        except Exception:  # noqa: BLE001
            pass
        finally:
            _refreshing = False

    threading.Thread(target=_job, name="smart-scan-refresh", daemon=True).start()


_SPEC_WEIGHTS = {
    "multi_trend": (0.55, 0.10, 0.10, 0.10, 0.05, 0.05, 0.05),
    "dragon_head": (0.20, 0.35, 0.10, 0.25, 0.05, 0.00, 0.05),
    "yang_three_yin": (0.25, 0.20, 0.25, 0.10, 0.05, 0.05, 0.10),
    "hot_theme": (0.20, 0.25, 0.25, 0.20, 0.05, 0.00, 0.05),
    "low_rebound": (0.15, 0.30, 0.15, 0.10, 0.10, 0.10, 0.10),
    "trend_quality": (0.30, 0.10, 0.10, 0.10, 0.30, 0.05, 0.05),
    "macd_golden": (0.35, 0.30, 0.10, 0.10, 0.05, 0.05, 0.05),
    "double_low": (0.05, 0.05, 0.05, 0.05, 0.10, 0.60, 0.10),
    "limit_gene": (0.25, 0.35, 0.20, 0.10, 0.05, 0.00, 0.05),
    "chan_theory": (0.45, 0.10, 0.10, 0.10, 0.05, 0.05, 0.15),
    "ma_golden": (0.40, 0.20, 0.20, 0.10, 0.05, 0.00, 0.05),
    "box_breakout": (0.30, 0.20, 0.25, 0.15, 0.05, 0.00, 0.05),
    "l1l2_resonance": (0.20, 0.20, 0.35, 0.15, 0.05, 0.00, 0.05),
    "volume_breakout": (0.25, 0.15, 0.40, 0.10, 0.05, 0.00, 0.05),
    "growth_quality": (0.10, 0.05, 0.05, 0.05, 0.55, 0.10, 0.10),
    "box_range": (0.15, 0.05, 0.10, 0.10, 0.10, 0.30, 0.20),
    "capital_heat": (0.10, 0.20, 0.20, 0.40, 0.05, 0.00, 0.05),
    "w_bottom": (0.35, 0.25, 0.15, 0.10, 0.05, 0.05, 0.05),
    "bottom_volume": (0.10, 0.25, 0.40, 0.10, 0.05, 0.05, 0.05),
    "shrink_pullback": (0.40, 0.05, 0.20, 0.10, 0.10, 0.05, 0.10),
    "bluechip_quality": (0.15, 0.05, 0.05, 0.05, 0.35, 0.20, 0.15),
    "limit_pullback": (0.35, 0.20, 0.10, 0.10, 0.05, 0.05, 0.15),
    "wave_theory": (0.40, 0.15, 0.10, 0.10, 0.05, 0.05, 0.15),
    "balanced_factor": (0.20, 0.15, 0.15, 0.15, 0.15, 0.10, 0.10),
    "smart_deep_scan": (0.30, 0.10, 0.15, 0.15, 0.15, 0.10, 0.05),
}

SPEC_CODES = frozenset(_SPEC_WEIGHTS)

_SPEC_FILTERS = {
    "multi_trend": lambda f: f("close") > f("ma20") and f("ma5") > f("ma10") > f("ma20"),
    "dragon_head": lambda f: f("pct_chg") >= 5 and f("turnover_rate") >= 5 and f("amount") >= 5e8,
    "yang_three_yin": lambda f: -3 <= f("pct_chg") <= 3 and f("volume") < f("vma20") * 0.9 and f("close") > f("ma20"),
    "hot_theme": lambda f: f("pct_chg") >= 3 and f("amount") >= 5e8 and f("turnover_rate") >= 3,
    "low_rebound": lambda f: f("pct_chg") > 0 and f("close") < f("ma20") * 1.03 and f("close") > f("ma5"),
    "trend_quality": lambda f: f("close") > f("ma20") and f("roe") >= 8,
    "macd_golden": lambda f: f("macd_cross") == "golden" or (f("hist") > 0 and f("macd_hist_turn") == "positive"),
    "double_low": lambda f: f("valuation_score") >= 75 and f("pct_chg") > -2,
    "limit_gene": lambda f: (f("pct_chg") >= 7 or f("close") >= f("hh20") * 0.99) and f("turnover_rate") >= 4,
    "chan_theory": lambda f: f("buy_point") in ("1", "2", "3") and f("trend_direction") != "down",
    "ma_golden": lambda f: f("close") > f("ma5") > f("ma10") > f("ma20") and f("volume") > f("vma20"),
    "box_breakout": lambda f: f("close") >= f("hh20") * 0.99 and f("pct_chg") >= 5,
    "l1l2_resonance": lambda f: f("volume") >= f("vma20") * 1.5 and f("pct_chg") >= 3,
    "volume_breakout": lambda f: f("close") >= f("hh20") * 0.99 and f("volume") >= f("vma20") * 1.5,
    "growth_quality": lambda f: f("revenue_yoy") >= 20 and f("profit_yoy") >= 20 and f("roe") >= 8,
    "box_range": lambda f: f("close") < f("hh20") * 0.97 and f("close") > f("ma20") and abs(f("pct_chg")) <= 3,
    "capital_heat": lambda f: f("main_force_net") > 0 and f("volume") > f("vma20") and f("pct_chg") > 0,
    "w_bottom": lambda f: f("close") > f("ma20") and f("close") < f("hh20") * 0.96 and f("pct_chg") >= 3,
    "bottom_volume": lambda f: f("pct_chg") >= 3 and f("close") < f("ma20") and f("volume") >= f("vma20") * 2,
    "shrink_pullback": lambda f: f("close") > f("ma20") and f("volume") < f("vma20") * 0.8 and f("pct_chg") < 2,
    "bluechip_quality": lambda f: f("total_market_cap") >= 5e10 and f("roe") >= 10 and f("valuation_score") >= 60,
    "limit_pullback": lambda f: -3 <= f("pct_chg") <= 3 and f("close") > f("ma5") and f("turnover_rate") >= 3,
    "wave_theory": lambda f: f("trend_direction") == "up" and f("segment_trend") == "up",
    "balanced_factor": lambda f: f("trend_score") >= 60 and f("quality_score") >= 55 and f("valuation_score") >= 55,
    "smart_deep_scan": lambda f: True,
}


def _spec_score(r: dict, code: str) -> float:
    w = _SPEC_WEIGHTS.get(code)
    if not w:
        return _f(r.get("score")) or 0
    vals = [
        _f(r.get("trend_score")) or 0,
        _f(r.get("momentum_score")) or 0,
        _f(r.get("volume_score")) or 0,
        _f(r.get("capital_score")) or 0,
        _f(r.get("quality_score")) or 0,
        _f(r.get("valuation_score")) or 0,
        _f(r.get("risk_score")) or 0,
    ]
    return round(sum(v * w[i] for i, v in enumerate(vals)), 1)


def scan_code(code: str, limit: int = 100) -> dict:
    """专属策略扫描：独立权重 + 独立形态过滤，全部在物化表上完成。"""
    ensure_fresh()
    limit = max(1, min(int(limit or 100), 100))
    day = scalar(f"SELECT MAX(trade_date) FROM {_TABLE}")
    if not day:
        # 物化表没建时给空结果，避免单卡点击触发 40 秒实时 SQL
        return {
            "trade_date": None,
            "preset": code,
            "preset_label": code,
            "filters": {},
            "total_scanned": 0,
            "total_passed": 0,
            "elapsed_ms": 0,
            "hits": [],
            "stats": {"strategy": code},
            "error": "智能评分表尚未构建，请先运行 refresh_smart_snapshot.py",
        }
    rows = query(
        f"SELECT * FROM {_TABLE} WHERE trade_date = %s ORDER BY score DESC, amount DESC LIMIT 1800",
        (day,),
    )
    # 真实 MACD / 缠论结构（若技术表已构建）
    try:
        tech = {
            r["ts_code"]: r
            for r in query(
                "SELECT ts_code, dif, dea, hist, macd_cross, macd_hist_turn, chan_signal, "
                "chan_trend, pivot_low, pivot_high, last_bottom, last_top, stroke_count, fractal_count, "
                "segment_count, segment_trend, pivot_count, seg_pivot_low, seg_pivot_high, "
                "trend_type, trend_direction, buy_point, sell_point, "
                "level2_segment_count, level2_pivot_count, level2_pivot_low, level2_pivot_high, "
                "level2_trend_type, level2_direction, level2_segment_trend "
                "FROM app_technical_snapshot "
                "WHERE trade_date = (SELECT MAX(trade_date) FROM app_technical_snapshot) "
                "AND ts_code = ANY(%s)",
                ([r["ts_code"] for r in rows],),
            )
        }
    except Exception:  # noqa: BLE001
        tech = {}
    if tech:
        for r in rows:
            r.update(tech.get(r["ts_code"]) or {})
    fn = _SPEC_FILTERS.get(code)
    if fn:
        def _field_for(row):
            def _get(k):
                if k in (
                    "macd_cross", "macd_hist_turn", "chan_signal", "chan_trend",
                    "segment_trend", "trend_type", "trend_direction", "buy_point", "sell_point",
                    "level2_trend_type", "level2_direction", "level2_segment_trend",
                ):
                    return str(row.get(k) or "")
                return _f(row.get(k)) or 0

            return _get

        rows = [r for r in rows if fn(_field_for(r))]
    rows.sort(key=lambda r: -_spec_score(r, code))
    rows = rows[:limit]
    scanned = int(scalar("SELECT COUNT(*) FROM daily_quotes WHERE trade_date = %s", (day,)) or 0)
    hits = []
    for i, r in enumerate(rows):
        hits.append(
            {
                "ts_code": r["ts_code"],
                "name": r.get("stock_name") or r["ts_code"],
                "industry": r.get("industry"),
                "price": _f(r.get("close")),
                "pct_chg": _f(r.get("pct_chg")),
                "turnover_rate": _f(r.get("turnover_rate")),
                "amount": _f(r.get("amount")),
                "market_cap": _f(r.get("total_market_cap")),
                "score": _spec_score(r, code),
                "factors": {
                    "trend": _f(r.get("trend_score")),
                    "momentum": _f(r.get("momentum_score")),
                    "volume": _f(r.get("volume_score")),
                    "capital": _f(r.get("capital_score")),
                    "quality": _f(r.get("quality_score")),
                    "valuation": _f(r.get("valuation_score")),
                    "risk": _f(r.get("risk_score")),
                },
                "reasons": _reasons(r),
                "macd": {
                    "dif": _f(r.get("dif")),
                    "dea": _f(r.get("dea")),
                    "hist": _f(r.get("hist")),
                    "cross": r.get("macd_cross"),
                    "hist_turn": r.get("macd_hist_turn"),
                },
                "chan": {
                    "signal": r.get("chan_signal"),
                    "trend": r.get("chan_trend"),
                    "pivot_low": _f(r.get("pivot_low")),
                    "pivot_high": _f(r.get("pivot_high")),
                    "stroke_count": r.get("stroke_count"),
                    "segment_count": r.get("segment_count"),
                    "segment_trend": r.get("segment_trend"),
                    "pivot_count": r.get("pivot_count"),
                    "trend_type": r.get("trend_type"),
                    "buy_point": r.get("buy_point"),
                    "sell_point": r.get("sell_point"),
                    "level2_segment_count": r.get("level2_segment_count"),
                    "level2_pivot_count": r.get("level2_pivot_count"),
                    "level2_trend_type": r.get("level2_trend_type"),
                    "level2_direction": r.get("level2_direction"),
                },
                "rank": i + 1,
            }
        )
    return {
        "trade_date": str(day),
        "preset": code,
        "preset_label": code,
        "filters": {"strategy": code},
        "total_scanned": scanned,
        "total_passed": len(hits),
        "elapsed_ms": 0,
        "hits": hits,
        "stats": {"strategy": code, "trade_date": str(day)},
    }


def _preset_score(r: dict, preset: str) -> float:
    f = lambda k: _f(r.get(k)) or 0  # noqa: E731
    trend, mom, vol = f("trend_score"), f("momentum_score"), f("volume_score")
    cap, qua, val, risk = f("capital_score"), f("quality_score"), f("valuation_score"), f("risk_score")
    weights = {
        "trend": (0.55, 0.15, 0.10, 0.10, 0.05, 0.00, 0.05),
        "growth": (0.15, 0.05, 0.05, 0.10, 0.50, 0.10, 0.05),
        "value": (0.10, 0.00, 0.00, 0.05, 0.20, 0.50, 0.15),
        "capital": (0.15, 0.15, 0.15, 0.50, 0.00, 0.00, 0.05),
        "breakout": (0.35, 0.20, 0.25, 0.15, 0.00, 0.00, 0.05),
        "reversal": (0.20, 0.35, 0.20, 0.15, 0.00, 0.00, 0.10),
        "leader": (0.20, 0.35, 0.15, 0.25, 0.00, 0.00, 0.05),
        "quality": (0.10, 0.00, 0.00, 0.00, 0.60, 0.20, 0.10),
        "dividend": (0.10, 0.00, 0.00, 0.00, 0.20, 0.40, 0.30),
        "winrate": (0.35, 0.15, 0.20, 0.05, 0.10, 0.05, 0.10),
    }.get(preset)
    if not weights:
        return f("score")
    score = (
        trend * weights[0] + mom * weights[1] + vol * weights[2] + cap * weights[3]
        + qua * weights[4] + val * weights[5] + risk * weights[6]
    )
    if preset == "winrate" and r.get("lhb_buy"):
        score += 3
    return round(score, 1)


def _match_preset(r: dict, preset: str) -> bool:
    f = lambda k: _f(r.get(k)) or 0  # noqa: E731
    close, ma5, ma20 = f("close"), f("ma5"), f("ma20")
    if preset == "trend":
        return close > ma20 and ma5 > f("ma10")
    if preset == "growth":
        return f("revenue_yoy") >= 15 or f("profit_yoy") >= 20
    if preset == "value":
        return f("valuation_score") >= 65
    if preset == "capital":
        return f("main_force_net") > 0
    if preset == "breakout":
        return f("hh20") > 0 and close >= f("hh20") * 0.98
    if preset == "reversal":
        return f("pct_chg") < -2 and close > ma5
    if preset == "leader":
        return f("pct_chg") >= 5 and f("turnover_rate") >= 5
    if preset == "quality":
        return f("roe") >= 10
    if preset == "dividend":
        return f("total_market_cap") >= 2e10 and f("valuation_score") >= 55
    if preset == "winrate":
        vma20 = f("vma20")
        vr = (f("volume") / vma20) if vma20 else 0
        state = r.get("market_state") or "trend"
        if state == "range":
            pct_lo, pct_hi = -1.0, 1.5
            bias_lo, bias_hi = 1.02, 1.05
            min_quality = 50
        else:
            pct_lo, pct_hi = 0.0, 2.0
            bias_lo, bias_hi = 1.03, 1.08
            min_quality = 45
        return (
            close > ma20 > f("ma60")
            and ma5 > f("ma10")
            and close > ma5 * 0.97
            and close >= ma20 * bias_lo
            and close < ma20 * bias_hi
            and pct_lo <= f("pct_chg") <= pct_hi
            and 2e8 <= f("amount") <= 2e9
            and 3 <= f("turnover_rate") <= 8
            and 1.1 <= vr <= 2.2
            and f("quality_score") >= min_quality
            and r.get("industry_ok", True)
        )
    return True


@ttl_cache(120)
def smart_scan(preset: str = "smart", board: str = "", min_score: float = 55, limit: int = 50) -> dict:
    t0 = time.perf_counter()
    p = PRESETS.get(preset) or PRESETS["smart"]
    bw = _WHERE.get(board or "", "")
    limit = max(1, min(int(limit or 50), 100))
    try:
        min_score = max(0.0, min(float(min_score), 100.0))
    except (TypeError, ValueError):
        min_score = 55.0

    ensure_fresh()
    day = scalar(f"SELECT MAX(trade_date) FROM {_TABLE}")
    rows = []
    market_breadth = None
    sentiment_breadth = None
    sentiment_breadth_ma20 = None
    regime_improving = None
    sentiment_score = None
    market_state = None
    if day:
        if preset == "winrate":
            market_breadth = scalar(
                "SELECT AVG(CASE WHEN close > ma20 THEN 1.0 ELSE 0.0 END) "
                f"FROM {_TABLE} WHERE trade_date = %s",
                (day,),
            )
            try:
                sentiment_score = scalar(
                    "SELECT score FROM app_sentiment_history WHERE trade_date <= %s "
                    "ORDER BY trade_date DESC LIMIT 1",
                    (day,),
                )
            except Exception:  # noqa: BLE001
                sentiment_score = None
            try:
                hist = query(
                    "SELECT (metrics->'dims'->>'breadth')::float AS b "
                    "FROM app_sentiment_history WHERE trade_date <= %s "
                    "ORDER BY trade_date DESC LIMIT 21",
                    (day,),
                )
                vals = [float(r["b"]) / 100.0 for r in hist if r.get("b") is not None]
                if vals:
                    sentiment_breadth = vals[0]
                    sentiment_breadth_ma20 = sum(vals) / len(vals)
                    regime_improving = sentiment_breadth >= sentiment_breadth_ma20 + 0.05
            except Exception:  # noqa: BLE001 情绪历史可能尚未回填
                pass
            b = float(market_breadth or 0)
            if b >= 0.55 and regime_improving:
                market_state = "trend"
            elif b >= 0.40 and float(sentiment_score or 0) >= 45:
                market_state = "range"
            else:
                market_state = "weak"
        # 物化表读取：先按综合分取前 1200，再在 Python 里做 preset 硬过滤
        rows = query(
            f"SELECT * FROM {_TABLE} WHERE trade_date = %s AND score >= %s {bw.replace('l.', '')} "
            "ORDER BY score DESC, amount DESC LIMIT 1200",
            (day, min_score),
        )
        strong_industries = set()
        lhb_codes = set()
        if preset == "winrate":
            inds = query(
                "SELECT industry, AVG(close / NULLIF(ma20, 0) - 1) AS mom "
                f"FROM {_TABLE} WHERE trade_date = %s AND industry IS NOT NULL "
                "GROUP BY industry ORDER BY mom DESC",
                (day,),
            )
            top_ratio = 0.35 if market_state == "trend" else 0.25
            top_n = max(1, int(len(inds) * top_ratio))
            strong_industries = {r["industry"] for r in inds[:top_n]}
            try:
                since = day - timedelta(days=7) if isinstance(day, date) else day
                lhb_codes = {
                    r["ts_code"]
                    for r in query(
                        "SELECT DISTINCT ts_code FROM lhb_detail "
                        "WHERE trade_date >= %s AND trade_date <= %s AND net_amt > 0",
                        (since, day),
                    )
                }
            except Exception:  # noqa: BLE001 龙虎榜表可能尚未建好
                lhb_codes = set()
        for r in rows:
            r["industry_ok"] = (not strong_industries) or (r.get("industry") in strong_industries)
            r["lhb_buy"] = r.get("ts_code") in lhb_codes
            r["market_state"] = market_state
        rows = [r for r in rows if _match_preset(r, preset)]
        if preset == "winrate" and market_state == "weak":
            rows = []
        rows.sort(key=lambda r: -_preset_score(r, preset))
        if preset == "winrate":
            rows = rows[: min(limit, 5 if market_state == "trend" else 3)]
        else:
            rows = rows[:limit]
    else:
        sql = _SQL.format(preset_where=p["where"], board_where=bw)
        rows = query(sql, (min_score, limit))
        day = scalar("SELECT MAX(trade_date) FROM daily_quotes")

    scanned = int(scalar("SELECT COUNT(*) FROM daily_quotes WHERE trade_date = %s", (day,)) or 0)
    hits = []
    for i, r in enumerate(rows):
        hits.append(
            {
                "ts_code": r["ts_code"],
                "name": r.get("stock_name") or r["ts_code"],
                "industry": r.get("industry"),
                "price": _f(r.get("close")),
                "pct_chg": _f(r.get("pct_chg")),
                "turnover_rate": _f(r.get("turnover_rate")),
                "amount": _f(r.get("amount")),
                "market_cap": _f(r.get("total_market_cap")),
                "score": _preset_score(r, preset),
                "factors": {
                    "trend": _f(r.get("trend_score")),
                    "momentum": _f(r.get("momentum_score")),
                    "volume": _f(r.get("volume_score")),
                    "capital": _f(r.get("capital_score")),
                    "quality": _f(r.get("quality_score")),
                    "valuation": _f(r.get("valuation_score")),
                    "risk": _f(r.get("risk_score")),
                },
                "reasons": _reasons(r),
                "rank": i + 1,
            }
        )
    return {
        "trade_date": str(day) if day else None,
        "preset": preset,
        "preset_label": p["label"],
        "filters": {"board": board or "全部", "min_score": min_score, "limit": limit},
        "total_scanned": scanned,
        "total_passed": len(hits),
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "hits": hits,
        "market_breadth": round(float(market_breadth), 4) if market_breadth is not None else None,
        "sentiment_breadth": round(float(sentiment_breadth), 4) if sentiment_breadth is not None else None,
        "sentiment_breadth_ma20": round(float(sentiment_breadth_ma20), 4) if sentiment_breadth_ma20 is not None else None,
        "sentiment_score": round(float(sentiment_score), 1) if sentiment_score is not None else None,
        "regime_improving": regime_improving,
        "market_state": market_state,
        "market_blocked": bool(preset == "winrate" and market_state == "weak"),
        "stats": {"preset": preset, "trade_date": str(day) if day else None},
    }


def _f(v):
    try:
        return round(float(v), 2) if v is not None else None
    except (TypeError, ValueError):
        return None


def _reasons(r: dict) -> list[str]:
    out = []
    close = _f(r.get("close")) or 0
    ma5 = _f(r.get("ma5")) or 0
    ma10 = _f(r.get("ma10")) or 0
    ma20 = _f(r.get("ma20")) or 0
    vol = _f(r.get("volume")) or 0
    vma20 = _f(r.get("vma20")) or 0
    hh20 = _f(r.get("hh20")) or 0
    if ma20 and close > ma20:
        out.append("站上MA20")
    if ma5 > ma10 > ma20:
        out.append("均线多头")
    if vma20 and vol > vma20 * 1.5:
        out.append("量能放大")
    if (_f(r.get("main_force_net")) or 0) > 0:
        out.append("主力净流入")
    if (_f(r.get("roe")) or 0) >= 10:
        out.append("ROE≥10%")
    if (_f(r.get("revenue_yoy")) or 0) >= 20:
        out.append("营收高增")
    if (_f(r.get("profit_yoy")) or 0) >= 20:
        out.append("利润高增")
    if hh20 and close >= hh20 * 0.99:
        out.append("接近20日新高")
    if r.get("industry_ok"):
        out.append("强势行业")
    if r.get("lhb_buy"):
        out.append("龙虎榜净买")
    tech = []
    if r.get("macd_cross") == "golden":
        tech.append("MACD金叉")
    if r.get("macd_hist_turn") == "positive":
        tech.append("MACD柱转正")
    if r.get("chan_signal") in ("buy1", "buy2", "buy3"):
        tech.append(f"缠论{r.get('chan_signal').replace('buy', '买点')}")
    return (tech + out)[:5]
