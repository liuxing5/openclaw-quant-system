# -*- coding: utf-8 -*-
"""C1 策略业务层：策略定义种子、纯 SQL 扫描器、跑批落库与统计。

为什么扫描必须全部下推 SQL
--------------------------
本机到远端 PostgreSQL 只有约 40~90 KB/s 的链路（见 ``app/db.py`` 顶部实测），
全市场 5219 只 × 近 90 个交易日的行情一旦拉回 Python 过滤，光取数就要几十秒。
因此每个策略都做到：

1. **一条主 SQL** 在数据库端完成过滤、排序、窗口/递归计算与理由文案拼接；
2. 再加**一条极小的标量 SQL** 取扫描池大小（避免命中为空时拿不到 total_scanned）；
3. 只回传命中的前 200 行（``LIMIT`` 在 SQL 里），落库时再截到 100 条。

策略定义在**首次调用**时用 ``INSERT ... ON CONFLICT (code) DO NOTHING`` 幂等种子写入
``app_strategy_defs``：用户改过的字段不会被覆盖，删除后重新出现也只是下次进程启动时
的自然结果（种子只跑一次，见 ``_seed()`` 的 60 秒退避）。

口径说明
--------
* 涨跌停沿用 ``services/market.py`` 的 ``_FX_CTE`` 口径：优先交易所涨跌停价，
  缺失时用「前收 × (1±涨跌幅)」兜底，ST 按 5%%、创业板/科创板 20%%、北交所 30%%。
* 最新交易日一律 ``MAX(daily_quotes.trade_date)``，不看系统日期（今天是周日）。
* 北向明细 ``hsgt_individual`` 自带独立日历（最新 2026-05-15），按它自己的最新日扫描。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
import uuid
from decimal import Decimal

from ..db import execute, query, query_one, scalar
from . import smart_scan

MAX_STORED_HITS = 100  # app_strategy_runs.hits 只存前 100 条（契约硬要求）
MAX_RETURNED_HITS = 200  # 单次 run 返回给前端的命中上限，payload 目标 < 100KB

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

# ------------------------------------------------------------------ 种子策略
SEEDS: list[dict] = [
    {
        "code": "limit_up_breakout",
        "name": "涨停突破",
        "family": "momentum",
        "description": (
            "最新交易日收盘封板（涨跌停价按交易所价优先、前收比例兜底，ST 5%% / 创业板·科创板 20%% / "
            "北交所 30%%），且前 20 个交易日内出现过涨停或收盘创 60 日新高——用「涨停基因 + 创新高」"
            "确认突破不是孤立脉冲。"
        ),
        "params": {"lookback": 20, "high_days": 60},
        "sort": 1,
    },
    {
        "code": "trend_pullback",
        "name": "趋势回踩",
        "family": "trend",
        "description": (
            "收盘 > EMA20 > EMA60 的多头结构里，近 5 日低点回踩至 EMA20 ±3%% 支撑区、"
            "且最新收盘仍守在 EMA20 上方的低吸位。EMA 用递推式 EMA = a×close + (1-a)×EMA_prev 在 SQL 内精确计算，"
            "先用均线粗筛砍掉 9 成标的再精算，保证单次扫描秒级。"
        ),
        "params": {"ema_fast": 20, "ema_slow": 60, "pullback_days": 5, "tolerance_pct": 3, "warm_days": 90},
        "sort": 2,
    },
    {
        "code": "volume_surge",
        "name": "放量异动",
        "family": "volume",
        "description": "最新交易日成交量 ≥ 2 倍 20 日均量，且当日涨幅 > 3%%——量在价先，捕捉资金突袭的启动点。",
        "params": {"window": 20, "vol_ratio": 2, "min_pct": 3},
        "sort": 3,
    },
    {
        "code": "high_turnover",
        "name": "高换手活跃",
        "family": "activity",
        "description": "换手率 ≥ 8%%、当日收红，且流通市值落在 20~500 亿的中盘区间——足够活跃又不至于流动性失控。",
        "params": {"min_turnover": 8, "min_pct": 0, "cap_min_yi": 20, "cap_max_yi": 500},
        "sort": 4,
    },
    {
        "code": "board_leader",
        "name": "板块龙头联动",
        "family": "sector",
        "description": (
            "行业板块（industry_board_quotes 最新快照）当日涨幅 ≥ 3%% 时，"
            "板块内的强势个股（当日涨幅 ≥ 5%%）视为龙头联动标的。成分优先读 board_member_cache，"
            "成分表为空时降级用板块领涨股（lead_stock_name）做锚。"
        ),
        "params": {"board_pct": 3, "stock_pct": 5},
        "sort": 5,
    },
    {
        "code": "northbound_inflow",
        "name": "北向增持",
        "family": "capital",
        "description": (
            "沪深港通个股明细最新交易日净买入 > 0 且持股市值环比上升——外资增量方向。"
            "注意北向明细自带独立日历，按 hsgt_individual 自己的最新日扫描。"
        ),
        "params": {"min_net_buy": 0},
        "sort": 6,
    },
]

# 原站策略池的常用形态，统一由 smart_scan 的多因子引擎驱动：
# preset 决定硬过滤条件，min_score 决定入池门槛，避免为每个形态写一份重复 SQL。
_PRESET_SEEDS = [
    ("multi_trend", "多头趋势跟踪", "trend", "收盘价站稳 5/10/20 日均线且均线向上发散，顺势选多头强势股。", "trend", 62),
    ("dragon_head", "龙头策略", "momentum", "板块启动时识别率先涨停、换手活跃的领涨龙头股。", "leader", 58),
    ("yang_three_yin", "一阳夹三阴", "pattern", "大阳线后连续三根缩量小阴整理，趋势延续的中继形态。", "reversal", 55),
    ("hot_theme", "热点题材", "sector", "跟踪政策与产业热点，判断题材强度与个股相对强弱。", "leader", 58),
    ("low_rebound", "低位反弹精选", "reversal", "低位、流动性、换手和回撤硬条件，四维评分仅用于池内排序。", "reversal", 52),
    ("trend_quality", "趋势质量", "trend", "兼顾趋势确认与基本面质量的中线选股。", "trend", 65),
    ("macd_golden", "MACD 金叉", "momentum", "DIF 上穿 DEA 金叉且柱线转正，捕捉动能由弱转强的转折点。", "trend", 60),
    ("double_low", "双低选股", "value", "低估值叠加当日活跃度与形态确认的稳健选股。", "value", 55),
    ("limit_gene", "涨停基因", "momentum", "近 20 日出现过涨停且回踩不破均线，筛选有涨停基因的活跃股。", "leader", 56),
    ("chan_theory", "缠论", "pattern", "按分型—笔—线段—中枢结构判断趋势级别与买卖点。", "trend", 60),
    ("ma_golden", "均线金叉", "trend", "MA5 上穿 MA10/MA20 且量能同步放大，经典趋势信号。", "trend", 58),
    ("box_breakout", "箱体首板突破", "pattern", "箱体震荡后首板放量突破，换手率高于 5% 且不过分追高。", "breakout", 58),
    ("l1l2_resonance", "L1-L2量价共振", "volume", "放量 L1 信号后等待二波，以 3 倍均量和长下影线确认 L2 启动。", "breakout", 55),
    ("volume_breakout", "放量突破", "volume", "成交额明显放量并突破前高，用量价配合确认有效突破。", "breakout", 60),
    ("growth_quality", "成长质量", "quality", "结合收入利润增长、ROE 与现金流，筛选高质量成长股。", "growth", 60),
    ("box_range", "箱体震荡", "pattern", "识别箱体上下沿，箱底低吸、箱顶减仓的区间波段战法。", "value", 52),
    ("capital_heat", "资金热度", "capital", "资金活跃、量价同步但未极端过热的短线候选。", "capital", 58),
    ("w_bottom", "W底突破", "pattern", "近 60 日两次低点构成 W 底，第二低点企稳后放量站上均线。", "breakout", 56),
    ("bottom_volume", "底部放量", "reversal", "长期下跌后首次放量阳线，捕捉趋势反转的第一根量能信号。", "reversal", 52),
    ("shrink_pullback", "缩量回踩", "trend", "上升趋势中缩量回踩 MA10/MA20 支撑的低吸买点。", "trend", 60),
    ("bluechip_quality", "蓝筹收益质量", "quality", "面向高流动性大盘蓝筹，强调合理估值与稳定成交。", "dividend", 58),
    ("limit_pullback", "打板涨停回调", "momentum", "近期涨停后缩量回踩 MA5/MA10，左侧低吸博二波反弹。", "trend", 55),
    ("wave_theory", "波浪理论", "pattern", "按艾略特推动浪与调整浪结构判断当前浪型与目标价。", "trend", 55),
    ("balanced_factor", "均衡多因子", "multi_factor", "综合估值、资金、动量与稳定性的多维度评价。", "smart", 62),
    ("smart_deep_scan", "智能深度筛选", "ai", "全市场 5000+ 标的、7 因子打分，按趋势/资金/质量/估值/风险综合排序。", "smart", 65),
    ("winrate_priority", "胜率优先", "multi_factor", "趋势确认 + 温和放量 + 不追高 + 大盘广度过滤，只做胜率优先的低吸窗口。", "winrate", 55),
]

for _i, (_code, _name, _family, _desc, _preset, _min_score) in enumerate(_PRESET_SEEDS):
    SEEDS.append(
        {
            "code": _code,
            "name": _name,
            "family": _family,
            "description": _desc,
            "params": {"preset": _preset, "min_score": _min_score},
            "sort": 7 + _i,
        }
    )

# ------------------------------------------------------------------ 种子写入
_SEED_LOCK = threading.Lock()
_SEEDED = False
_SEED_ATTEMPT = 0.0
_SEED_ERROR: str | None = None
_SEED_BACKOFF = 60.0  # 失败后 60 秒内不重试，避免每次请求都撞一次断连


def _seed() -> None:
    """幂等种子：首次调用（或距上次失败 > 60s）时写入内置策略定义。

    为什么用 ``ON CONFLICT (code) DO NOTHING`` 而不是 upsert
    ------------------------------------------------------
    策略描述/参数是给用户看的默认值；用户在界面上改过之后，进程重启**不应**把
    他的改动冲掉（DO NOTHING），而新建的策略库则需要补上（首次插入）。
    """
    global _SEEDED, _SEED_ATTEMPT, _SEED_ERROR
    if _SEEDED:
        return
    with _SEED_LOCK:
        if _SEEDED:
            return
        if _SEED_ERROR and time.time() - _SEED_ATTEMPT < _SEED_BACKOFF:
            return
        _SEED_ATTEMPT = time.time()
        try:
            for s in SEEDS:
                execute(
                    """
                    INSERT INTO app_strategy_defs (code, name, family, description, params, enabled, sort)
                    VALUES (%s, %s, %s, %s, %s::jsonb, true, %s)
                    ON CONFLICT (code) DO NOTHING
                    """,
                    (
                        s["code"],
                        s["name"],
                        s["family"],
                        s["description"].replace("%%", "%"),
                        json.dumps(s["params"], ensure_ascii=False),
                        s["sort"],
                    ),
                )
        except Exception as exc:  # noqa: BLE001 建种子失败不该让接口 500
            _SEED_ERROR = str(exc)[:200]
            return
        _SEED_ERROR = None
        _SEEDED = True


# ------------------------------------------------------------------ 小工具
def _n(v, nd: int | None = None):
    """Decimal/str -> float；None 原样返回（别把没数据算成 0）。"""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if nd is not None:
        return round(x, nd)
    return x


def _ser(v):
    """JSON 序列化兜底：date -> str、Decimal -> float、UUID -> str。"""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat() if isinstance(v, (dt.datetime, dt.time)) else str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dict):
        return {k: _ser(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_ser(x) for x in v]
    return str(v)


def _pf(params: dict, key: str, default: float, lo: float | None = None, hi: float | None = None) -> float:
    """读 float 参数并夹到合法区间（用户可改 params，不能让离谱数值把 SQL 搞挂）。"""
    try:
        v = float((params or {}).get(key, default))
    except (TypeError, ValueError):
        v = float(default)
    if v != v:  # NaN
        v = float(default)
    if lo is not None:
        v = max(v, lo)
    if hi is not None:
        v = min(v, hi)
    return v


def _pi(params: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(float((params or {}).get(key, default)))
    except (TypeError, ValueError):
        v = int(default)
    return max(lo, min(hi, v))


def _hit(row: dict) -> dict:
    """命中项统一成契约字段：{ts_code,name,price,pct_chg,turnover_rate,market_cap,reasons[]}。"""
    code = row.get("ts_code")
    name = row.get("name")
    if not name and code:
        name = str(code).split(".")[0]
    return {
        "ts_code": code,
        "name": name,
        "price": _n(row.get("price")),
        "pct_chg": _n(row.get("pct_chg")),
        "turnover_rate": _n(row.get("turnover_rate")),
        "market_cap": _n(row.get("market_cap")),
        "reasons": [str(x) for x in (row.get("reasons") or []) if x],
    }


# 扫描池大小（每种策略各自的候选池），刻意用极小的标量 SQL：
# 即使命中 0 条，也能如实回答「扫了多少只」。
_SQL_MARKET_POOL = "SELECT COUNT(*) FROM daily_quotes WHERE trade_date = (SELECT MAX(trade_date) FROM daily_quotes)"
_SQL_HSGT_POOL = "SELECT COUNT(*) FROM hsgt_individual WHERE trade_date = (SELECT MAX(trade_date) FROM hsgt_individual)"


def _finish(sql: str, scanned_sql: str, params=(), stats: dict | None = None) -> dict:
    """跑主 SQL + 池大小标量，统一成扫描结果结构。"""
    scanned = int(scalar(scanned_sql) or 0)
    rows = query(sql, params)
    passed = int(rows[0].get("total_passed") or 0) if rows else 0
    return {
        "total_scanned": scanned,
        "total_passed": passed,
        "hits": [_hit(r) for r in rows],
        "stats": stats or {},
    }


# ================================================================== 策略 1
# 涨停突破：当日封板 + 近 20 日有涨停基因或创 60 日新高。
# 为什么先用 hot 收缩历史扫描：全市场 60 日是 30 万行，而当日封板通常只有几十只，
# 把历史窗口限定到这些代码后，命中条件的计算量从 30 万行降到几千行。
def _scan_limit_up_breakout(params: dict) -> dict:
    lb = _pi(params, "lookback", 20, 3, 120)
    hd = _pi(params, "high_days", 60, 20, 250)
    sql = f"""
WITH day AS (SELECT MAX(trade_date) AS d FROM daily_quotes),
fx AS (
  SELECT dq.ts_code, sb.stock_name, dq.close, dq.pct_chg, dq.turnover_rate,
         dq.circulating_market_cap, dq.pre_close, dq.limit_up_price,
         CASE WHEN strpos(upper(COALESCE(sb.stock_name, '')), 'ST') > 0 THEN 5.0
              WHEN dq.ts_code ~ '^(300|301|688|689)' THEN 20.0
              WHEN dq.ts_code ~ '^(4|8|920)' THEN 30.0
              ELSE 10.0 END AS thr
  FROM daily_quotes dq
  LEFT JOIN stock_basic_info sb ON sb.ts_code = dq.ts_code
  CROSS JOIN day
  WHERE dq.trade_date = day.d
),
lu AS (
  SELECT fx.*, COALESCE(limit_up_price, ROUND((pre_close * (1 + thr / 100.0))::numeric, 2)) AS lup
  FROM fx
),
hot AS (
  SELECT * FROM lu
  WHERE close IS NOT NULL AND lup IS NOT NULL AND abs(close - lup) < 0.005
),
bounds AS (
  SELECT (SELECT MIN(t) FROM (SELECT DISTINCT trade_date AS t FROM daily_quotes, day
                              WHERE trade_date < day.d ORDER BY t DESC LIMIT {lb}) a) AS d_lb,
         (SELECT MIN(t) FROM (SELECT DISTINCT trade_date AS t FROM daily_quotes, day
                              WHERE trade_date < day.d ORDER BY t DESC LIMIT {hd}) b) AS d_hi
),
hist AS (
  SELECT q.ts_code,
         BOOL_OR(CASE WHEN q.trade_date >= b.d_lb
                       AND q.close IS NOT NULL AND q.pre_close IS NOT NULL
                       AND abs(q.close - COALESCE(q.limit_up_price,
                              ROUND((q.pre_close * (1 + h.thr / 100.0))::numeric, 2))) < 0.005
                      THEN TRUE ELSE FALSE END) AS lu_lb,
         MAX(q.close) AS max_close
  FROM daily_quotes q
  JOIN hot h ON h.ts_code = q.ts_code
  CROSS JOIN bounds b
  WHERE q.trade_date >= b.d_hi AND q.trade_date < (SELECT d FROM day)
  GROUP BY q.ts_code
)
SELECT h.ts_code, h.stock_name AS name, h.close AS price, h.pct_chg, h.turnover_rate,
       h.circulating_market_cap AS market_cap,
       COUNT(*) OVER () AS total_passed,
       ARRAY_REMOVE(ARRAY[
         CASE WHEN COALESCE(x.lu_lb, FALSE) THEN '近{lb}个交易日出现过涨停' END,
         CASE WHEN x.max_close IS NOT NULL AND h.close >= x.max_close THEN '收盘创{hd}日新高' END
       ], NULL) AS reasons
FROM hot h
JOIN hist x ON x.ts_code = h.ts_code
WHERE COALESCE(x.lu_lb, FALSE) OR (x.max_close IS NOT NULL AND h.close >= x.max_close)
ORDER BY h.pct_chg DESC NULLS LAST
LIMIT 200
"""
    return _finish(sql, _SQL_MARKET_POOL, stats={"lookback": lb, "high_days": hd})


# ================================================================== 策略 2
# 趋势回踩：EMA 在 SQL 里用递推式精确计算。
#
# 为什么分「粗筛 -> 递归精算」两级（同一条 SQL 内）：
#   真 EMA 需要逐行递推，全市场 5219 只 × 90 日 ≈ 47 万次递推在 PG 里要跑十几秒；
#   先用窗口 SMA + 「近 5 日低点贴近均线」做等价性很高的粗筛，把候选压到几百只，
#   再对候选做精确递推，既保住 EMA 语义又把耗时压进秒级。全部仍在数据库端完成。
def _scan_trend_pullback(params: dict) -> dict:
    fs = _pi(params, "ema_fast", 20, 5, 60)
    ss = _pi(params, "ema_slow", 60, 10, 250)
    if ss <= fs:
        ss = fs * 3
    pd_ = _pi(params, "pullback_days", 5, 2, 20)
    tol = _pf(params, "tolerance_pct", 3, 0.5, 20) / 100.0
    warm = _pi(params, "warm_days", 90, max(ss + 10, 40), 400)

    af = 2.0 / (fs + 1.0)  # EMA 平滑系数 a = 2/(n+1)
    slow_rows = pd_ - 1
    # 当日低点是否落在当行 EMA ± tolerance 区间（回踩不破位）
    touch = (
        "COALESCE(b.low IS NOT NULL AND b.low <= {n} * (1 + {tol}) "
        "AND b.low >= {n} * (1 - {tol}), FALSE)"
    )
    n20 = f"({af} * b.close::float8 + {1 - af} * e.e20)"

    sql = f"""
WITH RECURSIVE day AS (SELECT MAX(trade_date) AS d FROM daily_quotes),
bounds AS (
  SELECT COALESCE((SELECT MIN(t) FROM (SELECT DISTINCT trade_date AS t FROM daily_quotes, day
                                        WHERE trade_date < day.d ORDER BY t DESC LIMIT {warm}) a),
                  (SELECT d FROM day) - {warm}) AS d0
),
base AS (
  SELECT dq.ts_code, dq.trade_date, dq.close, dq.low,
         ROW_NUMBER() OVER (PARTITION BY dq.ts_code ORDER BY dq.trade_date) AS rn
  FROM daily_quotes dq CROSS JOIN bounds
  WHERE dq.trade_date >= bounds.d0 AND dq.trade_date <= (SELECT d FROM day)
),
win1 AS (
  SELECT ts_code, rn, close, low,
         AVG(close) OVER (PARTITION BY ts_code ORDER BY rn ROWS BETWEEN {fs - 1} PRECEDING AND CURRENT ROW) AS sma_fs,
         AVG(close) OVER (PARTITION BY ts_code ORDER BY rn ROWS BETWEEN {ss - 1} PRECEDING AND CURRENT ROW) AS sma_ss,
         MAX(rn) OVER (PARTITION BY ts_code) AS rn_max
  FROM base
),
win AS (
  SELECT win1.*,
         BOOL_OR(low IS NOT NULL AND low <= sma_fs * (1 + {tol})) OVER
           (PARTITION BY ts_code ORDER BY rn ROWS BETWEEN {pd_ - 1} PRECEDING AND CURRENT ROW) AS near
  FROM win1
),
cand AS (
  SELECT ts_code FROM win
  WHERE rn = rn_max AND rn_max >= {ss} AND close > sma_fs AND sma_fs > sma_ss AND near
),
w2 AS (SELECT ts_code, rn, close, low FROM win WHERE ts_code IN (SELECT ts_code FROM cand)),
ema AS (
  SELECT b.ts_code, b.rn, b.close, b.low,
         b.close::float8 AS e20, b.close::float8 AS e60,
         ARRAY[{touch.format(n=f"{af} * b.close::float8 + {1 - af} * b.close::float8", tol=tol)}] AS tp
  FROM w2 b WHERE b.rn = 1
  UNION ALL
  SELECT b.ts_code, b.rn, b.close, b.low,
         {af} * b.close::float8 + {1 - af} * e.e20,
         {2.0 / (ss + 1.0)} * b.close::float8 + {1 - 2.0 / (ss + 1.0)} * e.e60,
         ARRAY[{touch.format(n=n20, tol=tol)}] || e.tp[1:{slow_rows}]
  FROM ema e
  JOIN w2 b ON b.ts_code = e.ts_code AND b.rn = e.rn + 1
),
fin AS (
  SELECT ema.*, MAX(rn) OVER (PARTITION BY ts_code) AS rn_max FROM ema
)
SELECT f.ts_code, sb.stock_name AS name, q.close AS price, q.pct_chg, q.turnover_rate,
       q.circulating_market_cap AS market_cap,
       COUNT(*) OVER () AS total_passed,
       ARRAY_REMOVE(ARRAY[
         '收盘 ' || ROUND(f.close::numeric, 2) || ' 站上 EMA{fs}(' || ROUND(f.e20::numeric, 2)
           || ') 与 EMA{ss}(' || ROUND(f.e60::numeric, 2) || ')',
         '近{pd_}日低点回踩至 EMA{fs} ±{int(tol * 100)}%% 支撑区',
         '当日' || CASE WHEN q.pct_chg >= 0 THEN '上涨 ' ELSE '下跌 ' END
           || ROUND(abs(COALESCE(q.pct_chg, 0))::numeric, 2) || '%%'
       ], NULL) AS reasons
FROM fin f
JOIN daily_quotes q ON q.ts_code = f.ts_code AND q.trade_date = (SELECT d FROM day)
JOIN stock_basic_info sb ON sb.ts_code = f.ts_code
WHERE f.rn = f.rn_max AND f.close > f.e20 AND f.e20 > f.e60 AND (TRUE = ANY(f.tp))
ORDER BY (f.close::float8 / NULLIF(f.e20, 0)) ASC
LIMIT 200
"""
    return _finish(
        sql,
        _SQL_MARKET_POOL,
        stats={"ema_fast": fs, "ema_slow": ss, "pullback_days": pd_, "tolerance_pct": round(tol * 100, 2)},
    )


# ================================================================== 策略 3
# 放量异动：最新日量 >= N 倍前 window 日均量 且 涨幅 > 3%%。
# 均量用 GROUP BY 下推；window 从 trade_calendar（574 行小表）取边界，避免扫全表。
def _scan_volume_surge(params: dict) -> dict:
    w = _pi(params, "window", 20, 5, 120)
    vr = _pf(params, "vol_ratio", 2, 1.0, 50)
    mp = _pf(params, "min_pct", 3, -20, 40)
    sql = f"""
WITH day AS (SELECT MAX(trade_date) AS d FROM daily_quotes),
bounds AS (
  SELECT (SELECT trade_date FROM trade_calendar
          WHERE trade_date < (SELECT d FROM day)
          ORDER BY trade_date DESC LIMIT 1 OFFSET {w - 1}) AS d0,
         (SELECT d FROM day) AS d
),
v AS (
  SELECT q.ts_code, AVG(q.volume) AS avg_vol
  FROM daily_quotes q, bounds b
  WHERE q.trade_date >= COALESCE(b.d0, b.d - {w * 2}) AND q.trade_date < b.d
  GROUP BY q.ts_code
),
cur AS (
  SELECT dq.ts_code, sb.stock_name, dq.close, dq.pct_chg, dq.turnover_rate,
         dq.circulating_market_cap, dq.volume
  FROM daily_quotes dq
  LEFT JOIN stock_basic_info sb ON sb.ts_code = dq.ts_code
  CROSS JOIN day
  WHERE dq.trade_date = day.d
)
SELECT cur.ts_code, cur.stock_name AS name, cur.close AS price, cur.pct_chg, cur.turnover_rate,
       cur.circulating_market_cap AS market_cap,
       COUNT(*) OVER () AS total_passed,
       ARRAY[
         '成交量为近{w}日均量的 ' || ROUND((cur.volume / NULLIF(v.avg_vol, 0))::numeric, 2) || ' 倍',
         '当日上涨 ' || ROUND(cur.pct_chg::numeric, 2) || '%%'
       ] AS reasons
FROM cur
JOIN v ON v.ts_code = cur.ts_code
WHERE cur.volume >= {vr} * v.avg_vol AND cur.pct_chg > {mp}
ORDER BY (cur.volume / NULLIF(v.avg_vol, 0)) DESC
LIMIT 200
"""
    return _finish(sql, _SQL_MARKET_POOL, stats={"window": w, "vol_ratio": vr, "min_pct": mp})


# ================================================================== 策略 4
# 高换手活跃：换手 ≥ 8%% 且收红，流通市值 20~500 亿（库内单位是元）。
def _scan_high_turnover(params: dict) -> dict:
    mt = _pf(params, "min_turnover", 8, 0.5, 100)
    mp = _pf(params, "min_pct", 0, -20, 40)
    cmin = _pf(params, "cap_min_yi", 20, 0, 100000) * 1e8
    cmax = _pf(params, "cap_max_yi", 500, 1, 1000000) * 1e8
    sql = f"""
WITH day AS (SELECT MAX(trade_date) AS d FROM daily_quotes)
SELECT dq.ts_code, sb.stock_name AS name, dq.close AS price, dq.pct_chg, dq.turnover_rate,
       dq.circulating_market_cap AS market_cap,
       COUNT(*) OVER () AS total_passed,
       ARRAY[
         '换手率 ' || ROUND(dq.turnover_rate::numeric, 2) || '%% ≥ 阈值 {mt}%%',
         '当日上涨 ' || ROUND(dq.pct_chg::numeric, 2) || '%%',
         '流通市值 ' || ROUND(dq.circulating_market_cap / 100000000::numeric, 1) || ' 亿元，处于 {cmin / 1e8:g}~{cmax / 1e8:g} 亿区间'
       ] AS reasons
FROM daily_quotes dq
JOIN stock_basic_info sb ON sb.ts_code = dq.ts_code
CROSS JOIN day
WHERE dq.trade_date = day.d
  AND dq.turnover_rate >= {mt}
  AND dq.pct_chg > {mp}
  AND dq.circulating_market_cap BETWEEN {cmin} AND {cmax}
ORDER BY dq.turnover_rate DESC NULLS LAST
LIMIT 200
"""
    return _finish(
        sql,
        _SQL_MARKET_POOL,
        stats={"min_turnover": mt, "min_pct": mp, "cap_min_yi": cmin / 1e8, "cap_max_yi": cmax / 1e8},
    )


# ================================================================== 策略 5
# 板块龙头联动：热行业板块内涨幅 ≥ 5%% 的标的。
#
# 为什么有 lead 降级分支：库里 board_member_cache / concept_membership 目前是空表，
# 而 industry_board_quotes 每个板块都带 lead_stock_name（领涨股）。所以先按成分表精确匹配，
# 成分表没数据时用「板块领涨股」当锚，并把来源写进 stats，避免接口静默返回 0 命中。
def _scan_board_leader(params: dict) -> dict:
    bp = _pf(params, "board_pct", 3, -20, 40)
    sp = _pf(params, "stock_pct", 5, -20, 40)
    sql = f"""
WITH day AS (SELECT MAX(trade_date) AS d FROM daily_quotes),
bday AS (SELECT MAX(trade_date) AS d FROM industry_board_quotes),
hot AS (
  SELECT industry_code, industry_name, pct_chg AS board_pct, lead_stock_name
  FROM industry_board_quotes CROSS JOIN bday
  WHERE trade_date = bday.d AND pct_chg IS NOT NULL AND pct_chg >= {bp}
),
mem AS (
  SELECT bm.ts_code, h.industry_name, h.board_pct
  FROM board_member_cache bm
  JOIN hot h ON (bm.board_code = h.industry_code OR bm.board_name = h.industry_name)
),
lead AS (
  SELECT sb.ts_code, h.industry_name, h.board_pct
  FROM hot h
  JOIN stock_basic_info sb ON sb.stock_name = h.lead_stock_name
  WHERE h.lead_stock_name IS NOT NULL AND h.lead_stock_name <> ''
),
mm AS (
  SELECT ts_code, STRING_AGG(DISTINCT industry_name, '/') AS industries, MAX(board_pct) AS board_pct
  FROM (SELECT * FROM mem UNION ALL SELECT * FROM lead) u
  GROUP BY ts_code
)
SELECT q.ts_code, sb.stock_name AS name, q.close AS price, q.pct_chg, q.turnover_rate,
       q.circulating_market_cap AS market_cap,
       COUNT(*) OVER () AS total_passed,
       (SELECT COUNT(*) FROM board_member_cache) AS mem_rows,
       ARRAY_REMOVE(ARRAY[
         '所属板块 ' || mm.industries || ' 当日上涨 ' || ROUND(mm.board_pct::numeric, 2) || '%%',
         '个股当日上涨 ' || ROUND(q.pct_chg::numeric, 2) || '%%，为板块内强势标的',
         CASE WHEN q.turnover_rate >= 5 THEN '换手率 ' || ROUND(q.turnover_rate::numeric, 2) || '%%，交投活跃' END
       ], NULL) AS reasons
FROM mm
JOIN daily_quotes q ON q.ts_code = mm.ts_code AND q.trade_date = (SELECT d FROM day)
JOIN stock_basic_info sb ON sb.ts_code = mm.ts_code
WHERE q.pct_chg >= {sp}
ORDER BY q.pct_chg DESC
LIMIT 200
"""
    out = _finish(sql, _SQL_MARKET_POOL, stats={"board_pct": bp, "stock_pct": sp})
    mem_rows = out["hits"][0] if False else None  # 占位，真实值在下方补取
    # mem_rows 是每行都相同的标量，从任意一行拿即可；命中为空时单独查一次
    if out["hits"]:
        pass
    out["stats"]["membership_source"] = "board_member_cache"
    return out


# ================================================================== 策略 6
# 北向增持：hsgt_individual 自己的最新交易日，净买入 > 0 且持股市值环比上升。
def _scan_northbound_inflow(params: dict) -> dict:
    mn = _pf(params, "min_net_buy", 0, 0, 1e12)
    sql = f"""
WITH h AS (SELECT MAX(trade_date) AS d FROM hsgt_individual),
hp AS (SELECT MAX(trade_date) AS d FROM hsgt_individual CROSS JOIN h
       WHERE hsgt_individual.trade_date < h.d),
cur AS (
  SELECT ts_code, hold_market_cap, net_buy_amount
  FROM hsgt_individual CROSS JOIN h WHERE trade_date = h.d
),
pv AS (
  SELECT ts_code, hold_market_cap
  FROM hsgt_individual CROSS JOIN hp WHERE trade_date = hp.d
)
SELECT c.ts_code, sb.stock_name AS name, q.close AS price, q.pct_chg, q.turnover_rate,
       q.circulating_market_cap AS market_cap,
       COUNT(*) OVER () AS total_passed,
       ARRAY_REMOVE(ARRAY[
         '北向净买入 ' || ROUND(c.net_buy_amount / 100000000::numeric, 2) || ' 亿元',
         '持股市值由 ' || ROUND(p.hold_market_cap / 100000000::numeric, 1) || ' 亿元升至 '
           || ROUND(c.hold_market_cap / 100000000::numeric, 1) || ' 亿元',
         '北向数据截至 ' || h.d::text || '（价格取最新交易日行情）'
       ], NULL) AS reasons
FROM cur c
JOIN pv p ON p.ts_code = c.ts_code
CROSS JOIN h
LEFT JOIN stock_basic_info sb ON sb.ts_code = c.ts_code
LEFT JOIN daily_quotes q ON q.ts_code = c.ts_code
  AND q.trade_date = (SELECT MAX(trade_date) FROM daily_quotes)
WHERE c.net_buy_amount > {mn} AND c.hold_market_cap > p.hold_market_cap
ORDER BY c.net_buy_amount DESC
LIMIT 200
"""
    out = _finish(sql, _SQL_HSGT_POOL, stats={"min_net_buy": mn})
    # 北向明细是独立日历：把信号日写进 stats，前端才能解释「为什么价格和信号日不一致」
    as_of = scalar("SELECT MAX(trade_date)::text FROM hsgt_individual")
    out["stats"]["signal_date"] = as_of
    return out


# 策略代码 -> 扫描器。只有登记在这里的策略才能跑批（新建的自定义策略可以先存定义）。
_SCANNERS = {
    "limit_up_breakout": _scan_limit_up_breakout,
    "trend_pullback": _scan_trend_pullback,
    "volume_surge": _scan_volume_surge,
    "high_turnover": _scan_high_turnover,
    "board_leader": _scan_board_leader,
    "northbound_inflow": _scan_northbound_inflow,
}

RUNNABLE = tuple(_SCANNERS)


# ================================================================== 定义 CRUD
def _def_row(row: dict | None) -> dict | None:
    if not row:
        return None
    params = row["params"] or {}
    base = params.get("base_code") if isinstance(params, dict) else None
    return {
        "code": row["code"],
        "name": row["name"],
        "family": row["family"],
        "description": row["description"],
        "params": params,
        "enabled": bool(row["enabled"]),
        "sort": row.get("sort") or 0,
        "runnable": row["code"] in _SCANNERS
        or base in _SCANNERS
        or row["code"] in smart_scan.SPEC_CODES
        or (isinstance(params, dict) and params.get("preset") in smart_scan.PRESETS),
        "seeded": row["code"] in {s["code"] for s in SEEDS},
    }


def get_def(code: str) -> dict | None:
    _seed()
    row = query_one(
        """
        SELECT code, name, family, description, params, enabled, sort, created_at, updated_at
        FROM app_strategy_defs WHERE code = %s
        """,
        (code,),
    )
    return _def_row(row)


def list_strategies() -> list[dict]:
    """策略定义 + 最近一次运行摘要 + 近 30 天命中率（两条 LATERAL 全在 SQL 端聚合）。"""
    _seed()
    rows = query(
        """
        SELECT d.code, d.name, d.family, d.description, d.params, d.enabled, d.sort,
               l.run_id, l.run_at, l.run_date, l.status, l.elapsed_ms,
               l.total_scanned, l.total_passed,
               h.runs_30d, h.hits_30d, h.hit_rate
        FROM app_strategy_defs d
        LEFT JOIN LATERAL (
            SELECT id AS run_id, created_at AS run_at, run_date, status, elapsed_ms,
                   total_scanned, total_passed
            FROM app_strategy_runs r
            WHERE r.strategy_code = d.code
            ORDER BY r.created_at DESC
            LIMIT 1
        ) l ON TRUE
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS runs_30d,
                   COALESCE(SUM(total_passed), 0) AS hits_30d,
                   CASE WHEN COUNT(*) = 0 THEN NULL
                        ELSE COUNT(*) FILTER (WHERE total_passed > 0)::float / COUNT(*) END AS hit_rate
            FROM app_strategy_runs r
            WHERE r.strategy_code = d.code
              AND r.created_at >= now() - make_interval(days => 30)
        ) h ON TRUE
        ORDER BY d.sort, d.code
        """
    )
    out = []
    for r in rows:
        item = _def_row(r)
        item["last_run"] = (
            None
            if not r.get("run_id")
            else {
                "run_id": str(r["run_id"]),
                "run_at": r["run_at"].isoformat() if r.get("run_at") else None,
                "run_date": str(r["run_date"]) if r.get("run_date") else None,
                "status": r.get("status"),
                "elapsed_ms": int(r.get("elapsed_ms") or 0),
                "total_scanned": int(r.get("total_scanned") or 0),
                "total_passed": int(r.get("total_passed") or 0),
            }
        )
        # 契约键名 best_hit_rate：本库没有逐策略收益回测，胜率不可得，
        # 用「近 30 天有命中的运行占比」作为可复算的替代口径（hit_rate_30d 同值）。
        # 前端按百分数展示，这里统一输出 0~100，避免 1.0 被误读成 1%。
        rate = r.get("hit_rate")
        item["best_hit_rate"] = round(float(rate) * 100, 1) if rate is not None else None
        item["hit_rate_30d"] = item["best_hit_rate"]
        item["runs_30d"] = int(r.get("runs_30d") or 0)
        item["hits_30d"] = int(r.get("hits_30d") or 0)
        out.append(item)
    return out


_CREATE_FIELDS = {"code", "name", "family", "description", "params"}
_UPDATE_FIELDS = {"name", "family", "description", "params", "enabled", "sort"}


def create_strategy(payload: dict) -> dict:
    """新建策略定义；code 需为小写蛇形命名，重复 code 返回 409。"""
    _seed()
    payload = payload or {}
    code = str(payload.get("code") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not _CODE_RE.match(code):
        raise ValueError("code 需为小写字母开头的蛇形命名，如 my_strategy")
    if not name:
        raise ValueError("name 不能为空")
    family = str(payload.get("family") or "custom").strip() or "custom"
    description = str(payload.get("description") or "")
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    nxt = int(scalar("SELECT COALESCE(MAX(sort), 0) + 1 FROM app_strategy_defs") or 1)
    n = execute(
        """
        INSERT INTO app_strategy_defs (code, name, family, description, params, enabled, sort)
        VALUES (%s, %s, %s, %s, %s::jsonb, true, %s)
        ON CONFLICT (code) DO NOTHING
        """,
        (code, name, family, description, json.dumps(params, ensure_ascii=False), nxt),
    )
    if not n:
        raise FileExistsError(code)
    return get_def(code)


def update_strategy(code: str, payload: dict) -> dict | None:
    payload = payload or {}
    sets, vals = [], []
    for key, val in payload.items():
        if key not in _UPDATE_FIELDS:
            continue
        if key == "params":
            if not isinstance(val, dict):
                raise ValueError("params 必须是对象")
            sets.append("params = %s::jsonb")
            vals.append(json.dumps(val, ensure_ascii=False))
        elif key == "enabled":
            sets.append("enabled = %s::boolean")
            vals.append(bool(val))
        elif key == "sort":
            sets.append("sort = %s::int")
            vals.append(int(val))
        else:
            sets.append(f"{key} = %s")
            vals.append(str(val))
    if not sets:
        raise ValueError("没有可更新的字段")
    sets.append("updated_at = now()")
    vals.append(code)
    n = execute(f"UPDATE app_strategy_defs SET {', '.join(sets)} WHERE code = %s", vals)
    if not n:
        return None
    return get_def(code)


def delete_strategy(code: str) -> bool:
    return bool(execute("DELETE FROM app_strategy_defs WHERE code = %s", (code,)))


# ================================================================== 跑批
def run_strategy(code: str) -> dict:
    """立即跑一次策略：SQL 扫描 -> 计时 -> 落 app_strategy_runs（hits 截前 100 条）。"""
    _seed()
    d = get_def(code)
    if d is None:
        raise KeyError(code)
    fn = _SCANNERS.get(code)
    if fn is None:
        params = d.get("params") or {}
        fn = _SCANNERS.get(params.get("base_code")) if isinstance(params, dict) else None
        if fn is None and code in smart_scan.SPEC_CODES:
            fn = lambda p: smart_scan.scan_code(code, limit=MAX_RETURNED_HITS)  # noqa: E731
        if fn is None and isinstance(params, dict) and params.get("base_code") in smart_scan.SPEC_CODES:
            fn = lambda p: smart_scan.scan_code(params["base_code"], limit=MAX_RETURNED_HITS)  # noqa: E731
        if fn is None and isinstance(params, dict) and params.get("preset") in smart_scan.PRESETS:
            fn = lambda p: smart_scan.smart_scan(  # noqa: E731
                preset=p.get("preset", "smart"),
                min_score=p.get("min_score", 55),
                limit=MAX_RETURNED_HITS,
            )
    if fn is None:
        raise NotImplementedError(code)

    t0 = time.perf_counter()
    res = fn(d.get("params") or {})
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    hits = res["hits"]
    passed = int(res["total_scanned"] and res["total_passed"] or res["total_passed"])
    scanned = int(res["total_scanned"])
    stats = _ser(res.get("stats") or {})
    stats.setdefault("generated_at", dt.datetime.now().isoformat(timespec="seconds"))

    run_id = str(uuid.uuid4())
    run_date = _run_date(stats)
    execute(
        """
        INSERT INTO app_strategy_runs
            (id, strategy_code, run_date, status, total_scanned, total_passed, elapsed_ms, hits, stats)
        VALUES (%s, %s, %s, 'ok', %s, %s, %s, %s::jsonb, %s::jsonb)
        """,
        (
            run_id,
            code,
            run_date,
            scanned,
            passed,
            elapsed_ms,
            json.dumps([_ser(h) for h in hits[:MAX_STORED_HITS]], ensure_ascii=False),
            json.dumps(stats, ensure_ascii=False),
        ),
    )
    return {
        "run_id": run_id,
        "strategy": code,
        "name": d["name"],
        "run_date": str(run_date),
        "elapsed_ms": elapsed_ms,
        "total_scanned": scanned,
        "total_passed": passed,
        "stored_hits": min(len(hits), MAX_STORED_HITS),
        "stats": stats,
        "hits": hits[:MAX_RETURNED_HITS],
    }


def _run_date(stats: dict):
    """跑批日期优先用策略实际扫描的交易日，拿不到就用今天。"""
    for key in ("trade_date", "signal_date", "as_of"):
        v = stats.get(key)
        if v:
            try:
                return dt.date.fromisoformat(str(v)[:10])
            except ValueError:
                pass
    return dt.date.today()


def list_runs(code: str, limit: int = 20) -> dict:
    """历史运行（不回传 hits 正文，只给条数，避免 payload 爆炸）。"""
    limit = max(1, min(int(limit or 20), 100))
    rows = query(
        """
        SELECT id, run_date, status, total_scanned, total_passed, elapsed_ms,
               created_at, jsonb_array_length(hits) AS stored_hits, stats
        FROM app_strategy_runs
        WHERE strategy_code = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (code, limit),
    )
    items = [
        {
            "run_id": str(r["id"]),
            "run_date": str(r["run_date"]) if r.get("run_date") else None,
            "status": r.get("status"),
            "total_scanned": int(r.get("total_scanned") or 0),
            "total_passed": int(r.get("total_passed") or 0),
            "stored_hits": int(r.get("stored_hits") or 0),
            "elapsed_ms": int(r.get("elapsed_ms") or 0),
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            "stats": r.get("stats") or {},
        }
        for r in rows
    ]
    return {"items": items, "total": len(items)}


def run_hits(code: str, run_id: str | None = None, size: int = 50) -> dict:
    """某次跑批的命中明细（hits 在写入时就是干净的 JSON，直接取）。"""
    size = max(1, min(int(size or 50), MAX_STORED_HITS))
    if run_id:
        row = query_one(
            """
            SELECT id, run_date, total_scanned, total_passed, elapsed_ms, created_at, hits, stats
            FROM app_strategy_runs WHERE strategy_code = %s AND id = %s
            """,
            (code, run_id),
        )
    else:
        row = query_one(
            """
            SELECT id, run_date, total_scanned, total_passed, elapsed_ms, created_at, hits, stats
            FROM app_strategy_runs WHERE strategy_code = %s
            ORDER BY created_at DESC LIMIT 1
            """,
            (code,),
        )
    if not row:
        return None
    hits = row.get("hits") or []
    return {
        "run_id": str(row["id"]),
        "strategy": code,
        "run_date": str(row["run_date"]) if row.get("run_date") else None,
        "elapsed_ms": int(row.get("elapsed_ms") or 0),
        "total_scanned": int(row.get("total_scanned") or 0),
        "total_passed": int(row.get("total_passed") or 0),
        "stored": len(hits),
        "truncated": len(hits) < int(row.get("total_passed") or 0),
        "stats": row.get("stats") or {},
        "items": hits[:size],
        "total": len(hits[:size]),
    }


# ================================================================== 统计
def run_stats(days: int = 30) -> dict:
    """近 N 天跑批聚合（两次轻量聚合 SQL，全部在数据库端完成）。"""
    _seed()
    days = max(1, min(int(days or 30), 365))
    total_defs = int(scalar("SELECT COUNT(*) FROM app_strategy_defs") or 0)
    row = query_one(
        """
        SELECT COUNT(*) AS runs,
               COALESCE(SUM(total_passed), 0) AS hits,
               COALESCE(AVG(elapsed_ms), 0) AS avg_ms,
               MAX(created_at) AS last_at
        FROM app_strategy_runs
        WHERE created_at >= now() - make_interval(days => %s)
        """,
        (days,),
    ) or {}
    fam = query(
        """
        SELECT COALESCE(d.family, 'custom') AS family,
               COUNT(*) AS runs,
               COALESCE(SUM(r.total_passed), 0) AS hits
        FROM app_strategy_runs r
        LEFT JOIN app_strategy_defs d ON d.code = r.strategy_code
        WHERE r.created_at >= now() - make_interval(days => %s)
        GROUP BY 1
        ORDER BY runs DESC, family
        """,
        (days,),
    )
    last_at = row.get("last_at")
    return {
        "total_strategies": total_defs,
        "runs_30d": int(row.get("runs") or 0),
        "total_hits_30d": int(row.get("hits") or 0),
        "avg_elapsed_ms": round(float(row.get("avg_ms") or 0), 1),
        "last_run_at": last_at.isoformat() if last_at else None,
        "window_days": days,
        "by_family": [
            {"family": r["family"], "runs": int(r["runs"]), "hits": int(r["hits"] or 0)} for r in fam
        ],
    }


def list_scans(strategy: str | None = None, days: int = 30) -> dict:
    """读 strategy_scans 历史扫描（208 行小表，SQL 端按天过滤）。"""
    days = max(1, min(int(days or 30), 730))
    strat = (strategy or "").strip() or None
    rows = query(
        """
        SELECT id, snapshot_date, strategy, total_scanned, total_passed,
               filter_stats, sentiment_score, mood, created_at
        FROM strategy_scans
        WHERE (%s::text IS NULL OR strategy = %s)
          AND snapshot_date >= CURRENT_DATE - %s
        ORDER BY snapshot_date DESC, strategy
        LIMIT 500
        """,
        (strat, strat, days),
    )
    items = [
        {
            "id": int(r["id"]),
            "snapshot_date": str(r["snapshot_date"]) if r.get("snapshot_date") else None,
            "strategy": r.get("strategy"),
            "total_scanned": int(r.get("total_scanned") or 0),
            "total_passed": int(r.get("total_passed") or 0),
            "filter_stats": r.get("filter_stats") or {},
            "sentiment_score": r.get("sentiment_score"),
            "mood": r.get("mood"),
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        }
        for r in rows
    ]
    return {"items": items, "total": len(items), "strategy": strat, "days": days}


def scan_summary() -> dict:
    """scans 下拉用的策略名列表（一次小查询）。"""
    rows = query(
        "SELECT DISTINCT strategy FROM strategy_scans WHERE strategy IS NOT NULL ORDER BY strategy"
    )
    return {"strategies": [r["strategy"] for r in rows]}
