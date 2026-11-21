"""隔夜八步法 v2.1 · 尾盘恐慌承接（实盘口径）

节奏（用户指定）：
  14:30 扫描（腾讯实时快照） → 14:50 左右买入 → 次日 09:30-10:00 卖出（最晚 10:00）

八步：
  1 大盘环境  等权指数 > 10 日线，且全市场站上 MA20 比例在 20%~70%
  2 趋势结构  收盘（T-1）> MA20 > MA60，20 日动量 > 3%
  3 尾盘回踩  14:30 涨跌幅 -6.5% ~ -2.0%（健康回踩，不接崩塌）
  4 恐慌收盘  14:30 价格位于当日振幅下 20%（卖压出清）
  5 流动性    成交额 ≥ 1 亿，流通市值 50~2000 亿
  6 换手适中  换手率 1.5% ~ 8%（截至 14:30 的累计换手）
  7 执行     14:50 买入，单票 ≤20%，最多 5 只
  8 离场     次日 09:35-10:00 卖出；盘中止损 -2%

依据：2025-01~2026-04 的 5 分钟线回测（见 tools/backtest_v2_intraday.py）：
  该条件下 14:50 买入、次日 09:40 卖出，扣 0.25% 成本后单笔为正；
  而旧八步法（涨 3~9.7% / 换手 5~10%）在同口径下为负。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

CONFIG: Dict[str, Any] = {
    "source": "overnight_8step_v2",
    "run_mode": "intraday",
    "scan_time": "14:30",
    "entry_time": "14:50",
    "exit_window": "09:30-10:00（优先09:50）",
    "regime_ma": 10,
    "breadth_min": 0.20,
    "breadth_max": 0.70,
    "ret20_min": 0.03,
    "pullback_lo": -7.0,
    "pullback_hi": -2.0,
    "close_pos_max": 0.20,
    "min_amount": 1e8,
    "mktcap_lo": 50e8,
    "mktcap_hi": 2000e8,
    "turnover_lo": 1.5,
    "turnover_hi": 8.0,
    "watchlist_max": 30,
    "max_positions": 5,
    "position_pct": 0.20,
    "stop_loss": -0.02,
    "gap_up_exit": 0.005,
    "gap_down_exit": -0.03,
    "exit_prefer": "09:50",
    "min_list_days": 60,
}


# ============================================================
#  纯函数（可单测）
# ============================================================
def market_regime(index_df: pd.DataFrame, breadth: float, cfg: Dict[str, Any] = CONFIG) -> Dict[str, Any]:
    """index_df: trade_date/mean_pct（百分数），含今日实时均值；breadth 为今日站上 MA20 比例。"""
    df = index_df.sort_values("trade_date").copy()
    df["idx"] = (1 + df["mean_pct"] / 100.0).cumprod()
    df["ma"] = df["idx"].rolling(cfg["regime_ma"]).mean()
    last = df.iloc[-1]
    risk_on = bool(pd.notna(last["ma"]) and last["idx"] > last["ma"])
    blocked = (not risk_on) or (breadth < cfg["breadth_min"]) or (breadth > cfg["breadth_max"])
    return {
        "date": str(last["trade_date"])[:10],
        "index": float(last["idx"]),
        "ma": float(last["ma"]) if pd.notna(last["ma"]) else None,
        "risk_on": risk_on,
        "breadth": float(breadth),
        "blocked": bool(blocked),
    }


def build_features(daily: pd.DataFrame, snap: pd.DataFrame) -> pd.DataFrame:
    """daily: 截至 T-1 的每股均线/动量；snap: 14:30 腾讯快照。"""
    f = snap.merge(daily, on="ts_code", how="inner", suffixes=("", "_d"))
    f["pct"] = pd.to_numeric(f["pct"], errors="coerce")
    f["price"] = pd.to_numeric(f["price"], errors="coerce")
    f["high"] = pd.to_numeric(f["high"], errors="coerce")
    f["low"] = pd.to_numeric(f["low"], errors="coerce")
    f["turnover"] = pd.to_numeric(f["turnover"], errors="coerce")
    f["amount"] = pd.to_numeric(f["amount"], errors="coerce")
    f["circ_mcap"] = pd.to_numeric(f["circ_mcap"], errors="coerce")
    rng = (f["high"] - f["low"]).replace(0, np.nan)
    f["close_pos"] = ((f["price"] - f["low"]) / rng).astype(float)
    f["above_ma20"] = (f["price"] > f["ma20"]).astype(float)
    return f


def select(feat: pd.DataFrame, regime: Dict[str, Any], cfg: Dict[str, Any] = CONFIG) -> pd.DataFrame:
    f = feat.copy()
    today = pd.Timestamp(regime["date"])
    f["list_date"] = pd.to_datetime(f.get("list_date"), errors="coerce")
    f["above_ma20"] = (f["price"] > f["ma20"]).astype(float)

    step2 = ((f["above_ma20"] > 0) & (f["ma20"] > f["ma60"])
             & (f["ret20"] > cfg["ret20_min"]))
    step3 = f["pct"].between(cfg["pullback_lo"], cfg["pullback_hi"])
    step4 = f["close_pos"] < cfg["close_pos_max"]
    step5 = (f["amount"] >= cfg["min_amount"]) & f["circ_mcap"].between(cfg["mktcap_lo"], cfg["mktcap_hi"])
    step6 = f["turnover"].between(cfg["turnover_lo"], cfg["turnover_hi"])
    step8 = (~f["is_st"].fillna(False)) & (
        f["list_date"].isna() | (f["list_date"] <= today - pd.Timedelta(days=cfg["min_list_days"]))
    )
    mask = step2 & step3 & step4 & step5 & step6 & step8
    out = f[mask].copy()
    if out.empty:
        return out

    # 透明评分：收盘位置越低、回踩越深、换手越温和，评分越高
    pos_score = (1 - out["close_pos"]).clip(0, 1)
    depth = ((cfg["pullback_hi"] - out["pct"]) /
             (cfg["pullback_hi"] - cfg["pullback_lo"])).clip(0, 1)
    turn_score = 1 - ((out["turnover"] - cfg["turnover_lo"]) /
                      (cfg["turnover_hi"] - cfg["turnover_lo"])).clip(0, 1)
    out["score"] = (60 + 22 * pos_score + 12 * depth + 6 * turn_score).round(1)
    out["rank"] = out["close_pos"].rank(method="first")
    return out.sort_values(["rank", "score"], ascending=[True, False]).head(cfg["watchlist_max"])


def step_stats(feat: pd.DataFrame, regime: Dict[str, Any], cfg: Dict[str, Any] = CONFIG) -> Dict[str, int]:
    f = feat.copy()
    c2 = (f["ma20"] > f["ma60"]) & (f["ret20"] > cfg["ret20_min"])
    c3 = f["pct"].between(cfg["pullback_lo"], cfg["pullback_hi"])
    c4 = f["close_pos"] < cfg["close_pos_max"]
    c5 = (f["amount"] >= cfg["min_amount"]) & f["circ_mcap"].between(cfg["mktcap_lo"], cfg["mktcap_hi"])
    c6 = f["turnover"].between(cfg["turnover_lo"], cfg["turnover_hi"])
    c8 = ~f["is_st"].fillna(False)
    return {
        "全市场": int(len(f)),
        "趋势结构": int((c2 & c8).sum()),
        "尾盘回踩": int((c2 & c3 & c8).sum()),
        "恐慌收盘": int((c2 & c3 & c4 & c8).sum()),
        "流动性": int((c2 & c3 & c4 & c5 & c8).sum()),
        "换手适中": int((c2 & c3 & c4 & c5 & c6 & c8).sum()),
    }


def exit_plan(entry: float, cfg: Dict[str, Any] = CONFIG) -> Dict[str, Any]:
    return {
        "entry_time": cfg["entry_time"],
        "exit_window": cfg["exit_window"],
        "exit_prefer": cfg["exit_prefer"],
        "stop_loss": round(entry * (1 + cfg["stop_loss"]), 2),
        "stop_pct": cfg["stop_loss"],
        "gap_up_exit": cfg["gap_up_exit"],
        "gap_down_exit": cfg["gap_down_exit"],
        "hold": ("T+1 09:25 看开盘：浮盈≥+0.5% 开盘卖；浮亏≥-3% 开盘卖；"
                 "否则 -2% 止损，09:50-10:00 卖出"),
    }


# ============================================================
#  数据层
# ============================================================
DAILY_SQL = """
with base as (
  select ts_code, trade_date, close::float8 as close,
         avg(close) over (partition by ts_code order by trade_date
                          rows between 19 preceding and current row) as ma20,
         avg(close) over (partition by ts_code order by trade_date
                          rows between 59 preceding and current row) as ma60,
         close / nullif(lag(close, 20) over (partition by ts_code order by trade_date), 0) - 1 as ret20,
         row_number() over (partition by ts_code order by trade_date desc) as rn
  from daily_quotes
  where trade_date >= %(start)s and trade_date <= %(end)s
    and close > 0 and (ts_code like '%%.SH' or ts_code like '%%.SZ')
)
select ts_code, close, ma20, ma60, ret20
from base where rn = 1 and ret20 is not null
"""

INDEX_SQL = """
select trade_date, avg(pct_chg)::float8 as mean_pct
from daily_quotes
where trade_date >= %(start)s and trade_date <= %(end)s
  and (ts_code like '%%.SH' or ts_code like '%%.SZ')
group by trade_date order by trade_date
"""


def _db():
    from core.utils.env import load_project_env
    from core.db.connection import get_db_fresh
    load_project_env()
    conn = get_db_fresh()
    try:
        cur = conn.cursor()
        cur.execute("SET statement_timeout = '120000'")
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


def load_daily(trade_date: str, days: int = 120) -> tuple[pd.DataFrame, pd.DataFrame]:
    end = trade_date
    start = (datetime.strptime(trade_date, "%Y-%m-%d").date() - timedelta(days=days)).isoformat()
    params = {"start": start, "end": end}
    conn = _db()
    try:
        daily = _read_sql(conn, DAILY_SQL, params)
        index = _read_sql(conn, INDEX_SQL, params)
        basic = _read_sql(conn, "select ts_code, stock_name, is_st, list_date from stock_basic_info", params)
    finally:
        conn.close()
    daily = daily.merge(basic, on="ts_code", how="left")
    for c in ["close", "ma20", "ma60", "ret20"]:
        daily[c] = pd.to_numeric(daily[c], errors="coerce")
    index["mean_pct"] = pd.to_numeric(index["mean_pct"], errors="coerce")
    return daily, index


def fetch_snapshots(codes: list[str], batch: int = 50) -> pd.DataFrame:
    """腾讯行情快照；codes 为 sh600519 形式。"""
    import requests

    rows = []
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        try:
            resp = requests.get("http://qt.gtimg.cn/q=" + ",".join(chunk),
                                headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            if resp.status_code != 200:
                continue
            for line in resp.text.split(";"):
                p = line.split("~")
                if len(p) < 46:
                    continue
                try:
                    api = p[0].split("=")[0].strip()[-8:]      # sh600519
                    now, pre = float(p[3]), float(p[4])
                    if now <= 0:
                        continue
                    ts_code = api[2:].upper() + "." + api[:2].upper()
                    rows.append({
                        "ts_code": ts_code,
                        "price": now, "pre_close": pre, "pct": float(p[32]),
                        "high": float(p[33]), "low": float(p[34]),
                        "amount": float(p[37]) * 1e4,
                        "turnover": float(p[38]),
                        "circ_mcap": float(p[45]) * 1e8 if float(p[45]) > 0 else np.nan,
                        "stock_name": p[1],
                    })
                except (ValueError, IndexError):
                    continue
        except Exception:
            continue
        time.sleep(0.05)
    return pd.DataFrame(rows)


def _index_with_today(index_hist: pd.DataFrame, snap: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    today_mean = float(pd.to_numeric(snap["pct"], errors="coerce").mean())
    row = pd.DataFrame([{"trade_date": pd.Timestamp(trade_date), "mean_pct": today_mean}])
    hist = index_hist.copy()
    hist["trade_date"] = pd.to_datetime(hist["trade_date"])
    hist = hist[hist["trade_date"] < pd.Timestamp(trade_date)]
    return pd.concat([hist, row], ignore_index=True)


# ============================================================
#  扫描 / 落库
# ============================================================
def scan(trade_date: str, cfg: Dict[str, Any] = CONFIG, snap: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    daily, index_hist = load_daily(trade_date, days=120)
    if snap is None:
        codes = []
        for ts in daily["ts_code"]:
            code, mkt = ts.split(".")
            codes.append(("sh" if mkt == "SH" else "sz") + code)
        snap = fetch_snapshots(codes)
    if len(snap) < 500:
        raise RuntimeError(f"{trade_date} 快照覆盖不足（{len(snap)} 只），拒绝出信号")

    feat = build_features(daily, snap)
    breadth = float((feat["price"] > feat["ma20"]).mean())
    index_today = _index_with_today(index_hist, snap, trade_date)
    regime = market_regime(index_today, breadth, cfg)
    passed = select(feat, regime, cfg)
    return {
        "trade_date": trade_date,
        "universe": int(len(feat)),
        "breadth": round(breadth, 4),
        "regime": regime,
        "blocked": bool(regime["blocked"]),
        "candidates": passed,
        "step_counts": step_stats(feat, regime, cfg),
    }


def _to_rows(result: Dict[str, Any], cfg: Dict[str, Any] = CONFIG) -> List[dict]:
    rows = []
    risk_on = not result["blocked"]
    for _, r in result["candidates"].iterrows():
        entry = float(r["price"])
        plan = exit_plan(entry, cfg)
        tags = [
            "隔夜八步法v2", "尾盘恐慌承接", "14:30扫描", "14:50买入",
            f"回踩{float(r['pct']):.1f}%", f"收盘位置{float(r['close_pos']):.2f}",
            "次日09:25看缺口",
            f"浮盈≥{cfg['gap_up_exit']*100:.1f}%开盘卖",
            f"浮亏≤{cfg['gap_down_exit']*100:.0f}%开盘卖",
            f"否则止损{cfg['stop_loss']*100:.0f}%·{cfg['exit_prefer']}前卖",
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
            "entry_low": round(entry * 0.995, 2),
            "entry_high": round(entry * 1.005, 2),
            "stop_loss": plan["stop_loss"],
            "target_1": None,
            "target_2": None,
            "sources": {"regime": result["regime"], "steps": result["step_counts"],
                        "exit": plan, "watch_only": not risk_on},
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
    stats["8_执行风控"] = int(len(result["candidates"]))
    stats["regime_blocked"] = int(result["blocked"])

    try:
        out_dir = os.path.join(BASE_DIR, "docs", "overnight_v2")
        os.makedirs(out_dir, exist_ok=True)
        snapshot = {
            "trade_date": date_str,
            "scan_time": cfg["scan_time"],
            "entry_time": cfg["entry_time"],
            "exit_window": cfg["exit_window"],
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "universe": result["universe"],
            "breadth": result["breadth"],
            "risk_on": result["regime"]["risk_on"],
            "blocked": result["blocked"],
            "steps": stats,
            "candidates": [
                {"ts_code": r["ts_code"], "stock_name": r.get("stock_name"),
                 "score": round(float(r["final_score"]), 1), "entry": r["entry_low"],
                 "entry_low": r["entry_low"], "entry_high": r["entry_high"],
                 "stop_loss": r["stop_loss"], "selected": r["selected"]}
                for r in rows
            ],
        }
        with open(os.path.join(out_dir, "latest.json"), "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    except Exception as exc:
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
    ap = argparse.ArgumentParser(description="隔夜八步法 v2.1（14:30 扫描 / 14:50 买入 / 次日早盘卖出）")
    ap.add_argument("--date", default=None, help="交易日期 YYYY-MM-DD（默认今日）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    trade_date = args.date or datetime.now().strftime("%Y-%m-%d")
    result = scan(trade_date)
    print(f"\n=== 隔夜八步法 v2.1 · {trade_date} 14:30 ===")
    print(f"全市场 {result['universe']} 只 | 广度 {result['breadth']:.1%} | "
          f"指数 {'在10日线上' if result['regime']['risk_on'] else '跌破10日线'} | "
          f"门控 {'开启' if not result['blocked'] else '关闭'}")
    if result["blocked"]:
        print("⛔ 大盘环境不满足：不出手（空仓也是策略结果）")
    for _, r in result["candidates"].iterrows():
        print(f"  {r['ts_code']} {r.get('stock_name', '')} 现价{r['price']:.2f} "
              f"跌幅{r['pct']:+.2f}% 收盘位置{r['close_pos']:.2f} 换手{r['turnover']:.1f}% "
              f"评分{r['score']:.0f} 止损{round(r['price'] * 0.98, 2)}")

    if not args.dry_run:
        n = persist(result)
        print(f"✅ 写入 {n} 条候选（source={CONFIG['source']}）")
    else:
        print("(dry-run，不写库)")


if __name__ == "__main__":
    main()
