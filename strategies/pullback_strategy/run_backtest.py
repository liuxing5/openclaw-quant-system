"""回调买入法 — 回测

Usage:
  python -m strategies.pullback_strategy.run_backtest                 # 默认 2025-01-01 起
  python -m strategies.pullback_strategy.run_backtest --start 2025-06-01 --end 2026-10-12

回测规则（贴近推文"买点尽量靠近止损"的低试错成本打法）:
  - 信号日 T 收盘确认四步条件 → T+1 开盘价入场（跳空高开>3% 视为追高放弃，跳空低开照常成交）
  - 止损: min(结构止损, 1.5×ATR14) 波动率止损，风险上限 6%（2026-10-13 审查: 原 3% 比日噪声窄）
  - 止盈: 触及 目标价 出场；否则持有 max_hold 个交易日按收盘出场
  - 全部收益扣 0.25% 往返交易成本；前向收益报 t 值/非重叠子样本/全市场基准超额

输出 JSON: strategies/funnel_strategy/docs/pullback_backtest.json
（结构与 main_uptrend_backtest.json 一致，供 funnel 报告页渲染）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from dotenv import load_dotenv
for _env_path in [Path('.env'), Path('strategies/llm_multisource/.env')]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

from strategies.pullback_strategy.config import DEFAULT_CONFIG as CFG
from strategies.pullback_strategy import engine
from strategies.shared import backtest_stats as bs


def simulate(sigs: pd.DataFrame, quotes: pd.DataFrame, cfg) -> pd.DataFrame:
    """逐笔模拟：T+1 开盘入场 → 止损/止盈/超时出场。"""
    # 股票 -> 按日期排序的行情数组（用于快速取 T+1..T+n）
    q = quotes.sort_values(["ts_code", "trade_date"])
    q_by_code = {k: g.reset_index(drop=True) for k, g in q.groupby("ts_code")}

    rows = []
    for _, s in sigs.iterrows():
        g = q_by_code.get(s["ts_code"])
        if g is None:
            continue
        idx = g.index[g["trade_date"] == s["trade_date"]]
        if len(idx) == 0:
            continue
        i = int(idx[0])
        if i + 1 >= len(g):
            continue                       # 信号日之后无数据，无法入场
        entry_bar = g.iloc[i + 1]
        entry = float(entry_bar["open"])
        # 跳空高开>3%：推文强调"买点靠近止损"，追高放弃
        if entry > float(s["close"]) * 1.03:
            continue
        stop = float(s["stop_loss"])
        target = float(s["target_1"])
        if entry <= stop:
            continue                       # 开盘已低于止损，风险几何失效

        # A 股 T+1：T+1 开盘买入当日不可卖出，最早 T+2 出场
        exit_price, exit_type, hold = None, "timeout", 0
        for j in range(i + 2, min(i + 2 + cfg.bt_max_hold, len(g))):
            bar = g.iloc[j]
            hold = j - i                    # 持有交易日（含入场日）
            lo, hi = float(bar["low"]), float(bar["high"])
            if lo <= stop:                  # 止损优先于止盈判定
                # 正常情形按止损价成交；跳空低开（open<stop）只能按更差的开盘价成交
                exit_price, exit_type = min(stop, float(bar["open"])), "stop"
                break
            if hi >= target:
                exit_price, exit_type = float(bar["open"]) if float(bar["open"]) >= target else target, "target"
                break
        if exit_price is None:
            k = min(i + 1 + cfg.bt_max_hold, len(g) - 1)
            last = g.iloc[k]
            exit_price, exit_type, hold = float(last["close"]), "timeout", k - i

        ret = exit_price / entry - 1.0
        # 2026-10-13: 统一扣 0.25% 往返交易成本（原口径不计成本，微利被成本吃掉）
        ret = bs.net_return(ret)
        # 前向收益同样扣往返成本（持有到期卖出的等价口径）
        fwd = {}
        for d in (10, 20, 60):
            k = i + 1 + d
            if k < len(g):
                fwd[d] = bs.net_return(float(g.iloc[k]["close"]) / entry - 1.0)

        rows.append({
            "ts_code": s["ts_code"],
            "stock_name": s.get("stock_name", ""),
            "eval_date": str(s["trade_date"].date()),
            "composite_score": float(s["final_score"]),
            "entry_price": round(entry, 3),
            "stop_loss": round(stop, 3),
            "target_1": round(target, 3),
            "exit_ret": round(ret, 4),
            "hold_days": int(hold),
            "exit_type": exit_type,
            "fwd": fwd,
        })
    return pd.DataFrame(rows)


def fwd_stats(trades: pd.DataFrame, days: int, trading_days=None) -> dict | None:
    """前向收益统计：扣成本全样本 + 重叠校正 t 检验 + 非重叠子样本 + 基准超额。"""
    vals, dates = [], []
    for _, row in trades.iterrows():
        v = row["fwd"]
        if isinstance(v, dict) and days in v:
            vals.append(v[days])
            dates.append(row["eval_date"])
    if not vals:
        return None
    arr = np.array(vals, dtype=float)
    st = bs.fwd_stats(arr, dates=dates, horizon=days, trading_days=trading_days)
    # 保留页面兼容的旧字段
    st["hit_rate_5pct"] = float(np.mean(arr > 0.05))
    st["max"] = float(np.max(arr))
    st["min"] = float(np.min(arr))
    st["above_3pct"] = float(np.mean(arr > 0.03))
    st["above_5pct"] = float(np.mean(arr > 0.05))
    st["above_10pct"] = float(np.mean(arr > 0.10))
    return st


def market_benchmark(quotes: pd.DataFrame, days: int, entry_dates) -> dict | None:
    """全市场等权基准：信号日 T+1 开盘入场、T+1+days 收盘出场，全市场平均，
    同样扣成本。向量化：先按票算好每个日期的前向收益，再按日期求均值。"""
    q = quotes[quotes["amount"] >= CFG.min_amount_yi * 1e8]
    if q.empty:
        return None
    q = q.sort_values(["ts_code", "trade_date"])
    g = q.groupby("ts_code", sort=False)
    entry_open = g["open"].shift(-1)
    exit_close = g["close"].shift(-(1 + days))
    q = q.assign(_fwd=exit_close / entry_open - 1.0)
    by_date = q.groupby("trade_date")["_fwd"].mean()
    vals = []
    for ed in entry_dates:
        v = by_date.get(pd.Timestamp(ed))
        if v is not None and not np.isnan(v):
            vals.append(float(v))
    if not vals:
        return None
    arr = bs.net_returns(vals)
    return {"count": int(len(arr)), "mean": float(np.mean(arr)),
            "win_rate": float(np.mean(arr > 0))}


def build_summary(trades: pd.DataFrame, start: str, end: str,
                  quotes: pd.DataFrame) -> dict:
    rets = trades["exit_ret"].to_numpy(dtype=float) if len(trades) else np.array([])
    exits = trades["exit_type"].value_counts().to_dict() if len(trades) else {}
    exit_sig = bs.t_stat(rets) if len(rets) else {"t": 0.0, "p": 1.0}
    # 交易日历（非重叠采样用）
    trading_days = sorted({d.date() for d in quotes["trade_date"].unique()}) \
        if len(quotes) else None
    eval_dates = sorted(set(trades["eval_date"])) if len(trades) else []
    summary = {
        "backtest_start": start,
        "backtest_end": end,
        "total_signals": int(len(trades)),
        "strategy": "pullback_buy",
        "strategy_name": "回调买入法 (Martin Luk)",
        "cost_model": bs.COST_NOTE,
        "significance_note": bs.SIGNIFICANCE_NOTE,
        "exit_stats": {
            "win_rate": float(np.mean(rets > 0)) if len(rets) else 0.0,
            "mean": float(np.mean(rets)) if len(rets) else 0.0,
            "median": float(np.median(rets)) if len(rets) else 0.0,
            "max": float(np.max(rets)) if len(rets) else 0.0,
            "min": float(np.min(rets)) if len(rets) else 0.0,
            "t": exit_sig["t"], "p": exit_sig["p"],
            "avg_hold_days": float(trades["hold_days"].mean()) if len(trades) else 0.0,
            "stop_count": int(exits.get("stop", 0)),
            "target_count": int(exits.get("target", 0)),
            "timeout_count": int(exits.get("timeout", 0)),
        },
        "forward_returns": {},
        "benchmark": {},
        "excess": {},
        "by_year": {},
        "top10_signals": [],
        "all_signals": [],
    }
    for d in (10, 20, 60):
        st = fwd_stats(trades, d, trading_days=trading_days)
        if st:
            bm = market_benchmark(quotes, d, eval_dates)
            if bm:
                summary["benchmark"][str(d)] = bm
                summary["excess"][str(d)] = st["mean"] - bm["mean"]
            summary["forward_returns"][str(d)] = st

    # 分子期稳定性（问题8: 单段全样本可能由某一段行情主导）
    if len(trades):
        trades = trades.copy()
        trades["_year"] = pd.to_datetime(trades["eval_date"]).dt.year
        for year, sub in trades.groupby("_year"):
            r = sub["exit_ret"].to_numpy(dtype=float)
            yr = {"count": int(len(sub)), "win_rate": float(np.mean(r > 0)),
                  "mean": float(np.mean(r))}
            for d in (10, 20, 60):
                vals = [v[d] for v in sub["fwd"] if isinstance(v, dict) and d in v]
                if vals:
                    yr[f"fwd{d}_mean"] = float(np.mean(vals))
                    yr[f"fwd{d}_win"] = float(np.mean(np.array(vals) > 0))
            summary["by_year"][str(year)] = yr

    if len(trades):
        top = trades.sort_values("composite_score", ascending=False).head(10)
        for _, t in top.iterrows():
            summary["top10_signals"].append({
                "ts_code": t["ts_code"],
                "stock_name": t["stock_name"],
                "eval_date": t["eval_date"],
                "composite_score": round(t["composite_score"], 1),
                "entry_price": t["entry_price"],
                "exit_ret": t["exit_ret"],
                "hold_days": t["hold_days"],
                "exit_type": t["exit_type"],
                "forward_rets": {str(k): round(v, 4) for k, v in t["fwd"].items()},
            })
        for _, t in trades.iterrows():
            summary["all_signals"].append({
                "ts_code": t["ts_code"],
                "stock_name": t["stock_name"],
                "eval_date": t["eval_date"],
                "composite_score": round(t["composite_score"], 1),
                "entry_price": t["entry_price"],
                "stop_loss": t["stop_loss"],
                "target_1": t["target_1"],
                "exit_ret": t["exit_ret"],
                "hold_days": t["hold_days"],
                "exit_type": t["exit_type"],
            })
    return summary


def main():
    ap = argparse.ArgumentParser(description="回调买入法 — 回测")
    ap.add_argument("--start", default=CFG.bt_start)
    ap.add_argument("--end", default=CFG.bt_end or None)
    ap.add_argument("--output", default=None, help="输出 JSON 路径")
    args = ap.parse_args()

    end = args.end
    if not end:
        # 未指定结束日期 → 取 daily_quotes 最新交易日
        df_tmp = engine.load_quotes("2026-01-01")
        end = str(df_tmp["trade_date"].max().date()) if not df_tmp.empty else None
        if not end:
            print("❌ 无法确定回测结束日期（daily_quotes 为空）")
            return 1

    # 预热: 动能120交易日 + EMA50 + 20日高点 + ATR14 → 提前 260 自然日
    warm = str(datetime.strptime(args.start, "%Y-%m-%d").date() - timedelta(days=260))
    print(f"[回调买入法回测] 区间 {args.start} ~ {end}  (数据 {warm} 起，含预热)")

    meta = engine.load_stock_meta()
    quotes = engine.load_quotes(warm, end)
    print(f"  行情 {len(quotes)} 行, {quotes['ts_code'].nunique()} 只股票")
    quotes = engine.add_indicators(quotes, CFG)

    sigs = engine.generate_signals(quotes, CFG, meta, start=args.start, end=end)
    print(f"  信号 {len(sigs)} 条")
    if sigs.empty:
        print("❌ 无信号，回测终止")
        return 1

    trades = simulate(sigs, quotes, CFG)
    print(f"  成交 {len(trades)} 笔（排除跳空追高/数据不足）")

    summary = build_summary(trades, args.start, end, quotes)
    es = summary["exit_stats"]
    print("\n" + "=" * 56)
    print(f"总信号: {summary['total_signals']}   胜率: {es['win_rate']:.1%}   "
          f"平均收益: {es['mean']:+.2%}   中位: {es['median']:+.2%}   "
          f"t={es['t']:.2f} (p={es['p']:.3f})")
    print(f"出场: 止损 {es['stop_count']} / 止盈 {es['target_count']} / 超时 {es['timeout_count']}   "
          f"平均持有 {es['avg_hold_days']:.1f} 日")
    print(f"口径: {summary['cost_model']}")
    for d, st in summary["forward_returns"].items():
        bm = summary["benchmark"].get(d)
        exc = summary["excess"].get(d)
        bm_s = f"  基准={bm['mean']:+.2%}  超额={exc:+.2%}" if bm else ""
        non = st.get("nonoverlap", {})
        print(f"前向{d:>2}日: n={st['count']:<5} 胜率={st['win_rate']:.1%} "
              f"均值={st['mean']:+.2%} t={st['t']:.2f} "
              f"(p={st['p']:.3f}, n_eff={st['n_eff']}){bm_s}")
        if non.get("count"):
            print(f"          非重叠: n={non['count']} 均值={non['mean']:+.2%} "
                  f"t={non['t']:.2f} (p={non['p']:.3f})")
    for y, yr in summary["by_year"].items():
        print(f"{y} 分段: n={yr['count']} 胜率={yr['win_rate']:.1%} 均值={yr['mean']:+.2%} | "
              f"fwd10={yr.get('fwd10_mean', 0):+.2%}")
    print("=" * 56)

    out = Path(args.output) if args.output else (
        Path(__file__).parent.parent / "funnel_strategy" / "docs" / "pullback_backtest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"✓ JSON 已保存: {out}")

    # 明细 CSV 便于复查
    csv_path = out.with_suffix(".csv")
    if len(trades):
        t = trades.drop(columns=["fwd"]).copy()
        for d in (10, 20, 60):
            t[f"fwd_{d}d"] = trades["fwd"].apply(lambda v, d=d: v.get(d))
        t.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"✓ 明细已保存: {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
