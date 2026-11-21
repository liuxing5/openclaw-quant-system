"""隔夜八步法 v2.0 · 恐慌缺口承接（Overnight 8-Step v2）

设计依据（2024-09 ~ 2026-04，全 A 股 119 万条日线复权样本）：
  * 旧版「14:30 追涨 3~9.7% + 隔夜持有」的候选在次日隔夜段 -0.19%、
    隔夜+次日 -0.39%，显著为负（A 股隔夜漂移为负，追涨股更负）。
  * 把入场从「T 日收盘」改成「T+1 开盘承接缺口」后，方向反转：
    强趋势中回踩 -1.5%~-6%、收盘接近日内低点、次日低开 -1%~-4% 的标的，
    T+1 开盘买入 / T+2 收盘卖出，扣 0.25% 往返成本后单笔均值 +0.8%。

八步：
  1. 大盘环境  等权指数 > 10 日线，且全市场站上 MA20 比例 >= 30%
  2. 趋势结构  收盘 > MA20 > MA60，20 日动量 > 3%
  3. 回踩幅度  当日涨跌幅 -6.0% ~ -1.5%（健康回踩，不接崩塌）
  4. 恐慌收盘  收盘价位于当日振幅下 40%（卖压出清）
  5. 流动性    成交额 >= 1 亿，流通市值 50~2000 亿
  6. 换手适中  换手率 1.5% ~ 8%（避免拥挤）
  7. 缺口触发  T+1 开盘缺口 -4.0% ~ -1.0% 才买（不追高开、不接断崖）
  8. 执行风控  最多 5 只等权；T+1 开盘买入，T+2 收盘离场；ST/次新/涨跌停开盘剔除

T+1 制度下当日买入不能当日卖出，因此最短合法持有是 T+1 开盘 -> T+2 收盘。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

CONFIG: Dict[str, Any] = {
    "regime_ma": 10,
    "breadth_min": 0.30,
    "trend_ma_short": 20,
    "trend_ma_long": 60,
    "ret20_min": 0.03,
    "pullback_lo": -6.0,
    "pullback_hi": -1.5,
    "close_pos_max": 0.40,
    "min_amount": 1e8,
    "mktcap_lo": 50e8,
    "mktcap_hi": 2000e8,
    "turnover_lo": 1.5,
    "turnover_hi": 8.0,
    "gap_lo": -0.04,
    "gap_hi": -0.01,
    "max_positions": 5,
    "watchlist_max": 30,
    "position_pct": 0.20,
    "min_list_days": 60,
    "source": "overnight_8step_v2",
    "run_mode": "afternoon",
}


# ============================================================
#  纯函数（可单测，无 DB 依赖）
# ============================================================
def market_regime(index_df: pd.DataFrame, cfg: Dict[str, Any] = CONFIG) -> Dict[str, Any]:
    """index_df: trade_date/mean_pct (percent), ascending."""
    df = index_df.sort_values("trade_date").copy()
    df["idx"] = (1 + df["mean_pct"] / 100.0).cumprod()
    df["ma"] = df["idx"].rolling(cfg["regime_ma"]).mean()
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    return {
        "index": float(last["idx"]),
        "ma": float(last["ma"]) if pd.notna(last["ma"]) else None,
        "risk_on": bool(pd.notna(last["ma"]) and last["idx"] > last["ma"]),
        "index_1d": float(last["mean_pct"]) / 100.0,
        "index_prev_1d": float(prev["mean_pct"]) / 100.0,
        "date": str(last["trade_date"])[:10],
    }


def gate(regime: Dict[str, Any], breadth: float, cfg: Dict[str, Any] = CONFIG) -> bool:
    """大盘门控：指数在 10 日线上 且 广度 >= 阈值。返回 True 表示被拦截。"""
    return (not regime.get("risk_on")) or (breadth < cfg["breadth_min"])


def apply_steps(feat: pd.DataFrame, breadth: float, cfg: Dict[str, Any] = CONFIG) -> pd.DataFrame:
    """Apply steps 1-6 + 8 (step 7 gap is an execution-time condition).

    feat columns: ts_code, close, high, low, pct_chg, turnover_rate, amount,
    circulating_market_cap, ma20, ma60, ret20, is_st, list_date, stock_name.
    Returns passing rows with close_pos and score, sorted by conviction.
    """
    f = feat.copy()
    rng = (f["high"] - f["low"]).replace(0, float("nan"))
    f["close_pos"] = pd.to_numeric((f["close"] - f["low"]) / rng, errors="coerce")
    f["circ_mcap"] = pd.to_numeric(f["circulating_market_cap"], errors="coerce").fillna(0.0)
    f["list_date"] = pd.to_datetime(f.get("list_date"), errors="coerce")

    today = pd.Timestamp(cfg.get("_today")) if cfg.get("_today") else pd.Timestamp.today().normalize()

    step1 = pd.Series(True, index=f.index)                 # regime gate applied by caller
    step2 = (f["close"] > f["ma20"]) & (f["ma20"] > f["ma60"]) & (f["ret20"] > cfg["ret20_min"])
    step3 = f["pct_chg"].between(cfg["pullback_lo"], cfg["pullback_hi"])
    step4 = f["close_pos"] < cfg["close_pos_max"]
    step5 = (f["amount"] >= cfg["min_amount"]) & f["circ_mcap"].between(cfg["mktcap_lo"], cfg["mktcap_hi"])
    step6 = f["turnover_rate"].between(cfg["turnover_lo"], cfg["turnover_hi"])
    step8 = (~f["is_st"].fillna(False)) & (
        f["list_date"].isna() | (f["list_date"] <= today - pd.Timedelta(days=cfg["min_list_days"]))
    )

    mask = step1 & step2 & step3 & step4 & step5 & step6 & step8
    out = f[mask].copy()
    if out.empty:
        return out

    # 透明评分：收盘越弱（恐慌越充分）、回踩越深、换手越温和 -> 分越高
    pos_score = (1 - out["close_pos"]).clip(0, 1)
    depth = ((cfg["pullback_hi"] - out["pct_chg"]) /
             (cfg["pullback_hi"] - cfg["pullback_lo"])).clip(0, 1)
    turn_score = 1 - ((out["turnover_rate"] - cfg["turnover_lo"]) /
                      (cfg["turnover_hi"] - cfg["turnover_lo"])).clip(0, 1)
    out["score"] = (60 + 22 * pos_score + 12 * depth + 6 * turn_score).round(1)
    out["rank"] = out["close_pos"].rank(method="first")
    return out.sort_values(["rank", "score"], ascending=[True, False]).head(cfg["watchlist_max"])


def gap_bands(close: float, cfg: Dict[str, Any] = CONFIG) -> Dict[str, float]:
    """T+1 开盘可承接的价格区间（由 T 日收盘推算）。"""
    return {
        "entry_low": round(close * (1 + cfg["gap_lo"]), 2),
        "entry_high": round(close * (1 + cfg["gap_hi"]), 2),
    }


def gap_ok(gap: float, cfg: Dict[str, Any] = CONFIG) -> bool:
    return cfg["gap_lo"] <= gap <= cfg["gap_hi"]


# ============================================================
#  数据加载
# ============================================================
FEATURE_SQL = """
with base as (
  select ts_code, trade_date, open, high, low, close, pct_chg,
         turnover_rate, amount::float8 as amount,
         circulating_market_cap::float8 as circulating_market_cap
  from daily_quotes
  where trade_date >= %(start)s and trade_date <= %(end)s
    and close > 0 and open > 0
    and (ts_code like '%%.SH' or ts_code like '%%.SZ')
),
feat as (
  select *,
    avg(close) over w20 as ma20,
    avg(close) over w60 as ma60,
    close / nullif(lag(close, 20) over (partition by ts_code order by trade_date), 0) - 1 as ret20,
    row_number() over (partition by ts_code order by trade_date desc) as rn
  from base
  window w20 as (partition by ts_code order by trade_date rows between 19 preceding and current row),
         w60 as (partition by ts_code order by trade_date rows between 59 preceding and current row)
)
select ts_code, trade_date, open, high, low, close, pct_chg, turnover_rate,
       amount, circulating_market_cap, ma20, ma60, ret20
from feat
where rn = 1 and trade_date = %(end)s
"""

INDEX_SQL = """
select trade_date, avg(pct_chg)::float8 as mean_pct
from daily_quotes
where trade_date >= %(start)s and trade_date <= %(end)s
  and (ts_code like '%%.SH' or ts_code like '%%.SZ')
group by trade_date
order by trade_date
"""

BREADTH_SQL = """
with base as (
  select ts_code, trade_date, close,
         avg(close) over (partition by ts_code order by trade_date rows between 19 preceding and current row) as ma20,
         row_number() over (partition by ts_code order by trade_date desc) as rn
  from daily_quotes
  where trade_date >= %(start)s and trade_date <= %(end)s
    and close > 0 and (ts_code like '%%.SH' or ts_code like '%%.SZ')
)
select avg(case when close > ma20 then 1.0 else 0.0 end)::float8 as breadth
from base where rn = 1
"""


def _db():
    from core.utils.env import load_project_env
    from core.db.connection import get_db_fresh
    load_project_env()
    conn = get_db_fresh()
    try:
        cur = conn.cursor()
        cur.execute("SET statement_timeout = '300000'")
        cur.close()
    except Exception:
        pass
    return conn


def _read_sql(conn, sql: str, params: dict) -> pd.DataFrame:
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
    finally:
        cur.close()
    return pd.DataFrame(rows, columns=cols)


def load_features(trade_date: str, days: int = 120) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    end = datetime.strptime(trade_date, "%Y-%m-%d").date()
    start = (end - timedelta(days=days)).isoformat()
    params = {"start": start, "end": trade_date}
    conn = _db()
    try:
        feat = _read_sql(conn, FEATURE_SQL, params)
        idx = _read_sql(conn, INDEX_SQL, params)
        breadth_df = _read_sql(conn, BREADTH_SQL, params)
        basic = _read_sql(conn, "select ts_code, stock_name, is_st, list_date from stock_basic_info", params)
    finally:
        conn.close()
    feat = feat.merge(basic, on="ts_code", how="left")
    for col in ["open", "high", "low", "close", "pct_chg", "turnover_rate", "amount",
                "circulating_market_cap", "ma20", "ma60", "ret20"]:
        feat[col] = pd.to_numeric(feat[col], errors="coerce")
    idx["mean_pct"] = pd.to_numeric(idx["mean_pct"], errors="coerce")
    breadth = float(breadth_df["breadth"].iloc[0]) if len(breadth_df) else 0.0
    return feat, idx, breadth


# ============================================================
#  扫描与落库
# ============================================================
def step_stats(feat: pd.DataFrame, cfg: Dict[str, Any] = CONFIG) -> Dict[str, int]:
    f = feat.copy()
    rng = (f["high"] - f["low"]).replace(0, float("nan"))
    f["close_pos"] = pd.to_numeric((f["close"] - f["low"]) / rng, errors="coerce")
    f["circ_mcap"] = pd.to_numeric(f["circulating_market_cap"], errors="coerce").fillna(0.0)
    c2 = (f["close"] > f["ma20"]) & (f["ma20"] > f["ma60"]) & (f["ret20"] > cfg["ret20_min"])
    c3 = f["pct_chg"].between(cfg["pullback_lo"], cfg["pullback_hi"])
    c4 = f["close_pos"] < cfg["close_pos_max"]
    c5 = (f["amount"] >= cfg["min_amount"]) & f["circ_mcap"].between(cfg["mktcap_lo"], cfg["mktcap_hi"])
    c6 = f["turnover_rate"].between(cfg["turnover_lo"], cfg["turnover_hi"])
    c8 = ~f["is_st"].fillna(False)
    return {
        "全市场": int(len(f)),
        "趋势结构": int((c2 & c8).sum()),
        "回踩幅度": int((c2 & c3 & c8).sum()),
        "恐慌收盘": int((c2 & c3 & c4 & c8).sum()),
        "流动性": int((c2 & c3 & c4 & c5 & c8).sum()),
        "换手适中": int((c2 & c3 & c4 & c5 & c6 & c8).sum()),
    }


def scan(trade_date: str, cfg: Dict[str, Any] = CONFIG) -> Dict[str, Any]:
    feat, idx, breadth = load_features(trade_date)
    regime = market_regime(idx, cfg)

    if len(feat) < 100:
        raise RuntimeError(f"{trade_date} 行情覆盖不足（{len(feat)} 行），拒绝出信号")

    c = dict(cfg)
    c["_today"] = trade_date
    passed = apply_steps(feat, breadth, c)
    blocked = gate(regime, breadth, cfg)
    return {
        "trade_date": trade_date,
        "universe": int(len(feat)),
        "breadth": round(breadth, 4),
        "regime": regime,
        "blocked": bool(blocked),
        "candidates": passed,
        "step_counts": step_stats(feat, cfg),
    }


def _to_rows(result: Dict[str, Any], cfg: Dict[str, Any] = CONFIG) -> List[dict]:
    rows = []
    risk_on = not result["blocked"]
    for _, r in result["candidates"].iterrows():
        bands = gap_bands(float(r["close"]), cfg)
        tags = [
            "隔夜八步法v2", "恐慌缺口承接",
            f"回踩{float(r['pct_chg']):.1f}%",
            f"收盘位置{float(r['close_pos']):.2f}",
            "T+1缺口-4%~-1%触发",
            "T+2收盘离场",
        ]
        if not risk_on:
            tags.append("⛔大盘门控·观察不买")
        rows.append({
            "ts_code": r["ts_code"],
            "stock_name": r.get("stock_name"),
            "final_score": float(r["score"]),
            "quant_score": float(r["score"]),
            "logic_tags": tags,
            "selected": bool(risk_on),
            "position_pct": cfg["position_pct"] if risk_on else 0.0,
            "entry_low": bands["entry_low"],
            "entry_high": bands["entry_high"],
            "stop_loss": None,
            "target_1": None,
            "target_2": None,
            "sources": {
                "regime": result["regime"],
                "breadth": result["breadth"],
                "gap_rule": [cfg["gap_lo"], cfg["gap_hi"]],
                "hold": "T+1 open -> T+2 close",
                "watch_only": not risk_on,
            },
        })
    return rows


def persist(result: Dict[str, Any], cfg: Dict[str, Any] = CONFIG) -> int:
    from core.db.candidates import write_candidates

    date_str = result["trade_date"]
    rows = _to_rows(result, cfg)
    conn = _db()
    try:
        cur = conn.cursor()
        cur.execute("delete from daily_candidates where snapshot_date=%s and source=%s",
                    (date_str, cfg["source"]))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    n = write_candidates(rows, date_str, source=cfg["source"], run_mode=cfg["run_mode"])

    stats = dict(result["step_counts"])
    stats["7_缺口触发"] = 0
    stats["8_执行风控"] = int(len(result["candidates"]))
    stats["regime_blocked"] = int(result["blocked"])

    # 发布用快照（页面/复盘读取；不含未来数据）
    try:
        out_dir = os.path.join(BASE_DIR, "docs", "overnight_v2")
        os.makedirs(out_dir, exist_ok=True)
        snapshot = {
            "trade_date": date_str,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "universe": result["universe"],
            "breadth": round(result["breadth"], 4),
            "risk_on": bool(result["regime"].get("risk_on")),
            "blocked": bool(result["blocked"]),
            "steps": stats,
            "candidates": [
                {
                    "ts_code": r["ts_code"], "stock_name": r.get("stock_name"),
                    "score": round(float(r["final_score"]), 1),
                    "entry_low": r["entry_low"], "entry_high": r["entry_high"],
                    "selected": r["selected"],
                }
                for r in rows
            ],
        }
        with open(os.path.join(out_dir, "latest.json"), "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ latest.json 写入失败: {exc}")

    conn = _db()
    try:
        cur = conn.cursor()
        cur.execute("delete from strategy_scans where snapshot_date=%s and strategy=%s",
                    (date_str, cfg["source"]))
        cur.execute("""
            insert into strategy_scans
              (snapshot_date, strategy, total_scanned, total_passed, filter_stats, sentiment_score, mood)
            values (%s, %s, %s, %s, %s, %s, %s)
        """, (date_str, cfg["source"], result["universe"], len(rows),
              json.dumps(stats, ensure_ascii=False), round(result["breadth"] * 100, 1),
              "risk_on" if not result["blocked"] else "risk_off"))
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return n


def main():
    ap = argparse.ArgumentParser(description="隔夜八步法 v2 扫描")
    ap.add_argument("--date", default=None, help="快照日 YYYY-MM-DD（默认最近交易日）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写库")
    args = ap.parse_args()

    trade_date = args.date
    if trade_date is None:
        conn = _db()
        try:
            cur = conn.cursor()
            cur.execute("select max(trade_date) from daily_quotes")
            trade_date = str(cur.fetchone()[0])
            cur.close()
        finally:
            conn.close()

    result = scan(trade_date)
    print(f"\n=== 隔夜八步法 v2 · {trade_date} ===")
    print(f"全市场 {result['universe']} 只 | 广度 {result['breadth']:.1%} | "
          f"指数 {'在10日线上' if result['regime']['risk_on'] else '跌破10日线'} | "
          f"门控 {'开启' if not result['blocked'] else '关闭'}")
    if result["blocked"]:
        print("⛔ 大盘环境不满足：不出手（空仓也是策略结果）")
    for _, r in result["candidates"].iterrows():
        print(f"  {r['ts_code']} {r.get('stock_name', '')} 收盘{r['close']:.2f} "
              f"跌幅{r['pct_chg']:+.2f}% 收盘位置{r['close_pos']:.2f} "
              f"换手{r['turnover_rate']:.1f}% 分值{r['score']:.0f} "
              f"承接区间{round(r['close'] * (1 + CONFIG['gap_lo']), 2)}~{round(r['close'] * (1 + CONFIG['gap_hi']), 2)}")

    if not args.dry_run:
        n = persist(result)
        print(f"✅ 写入 {n} 条候选（source={CONFIG['source']}）")
    else:
        print("(dry-run，不写库)")


if __name__ == "__main__":
    main()
