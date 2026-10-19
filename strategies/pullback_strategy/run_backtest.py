"""回调买入法 — 回测

Usage:
  python -m strategies.pullback_strategy.run_backtest                 # 默认 2025-01-01 起
  python -m strategies.pullback_strategy.run_backtest --start 2025-06-01 --end 2026-10-12

回测规则（贴近推文"买点尽量靠近止损"的低试错成本打法）:
  - 信号日 T 收盘确认四步条件 → T+1 开盘价入场（跳空高开>3% 视为追高放弃，跳空低开照常成交）
  - 止损: min(结构止损, 1.5×ATR14) 波动率止损，风险上限 6%（2026-10-13 审查: 原 3% 比日噪声窄）
  - 止盈: 触及 目标价 出场；否则持有 max_hold 个交易日按收盘出场
  - 时间止损 (2026-10-19): 持有第 bt_time_stop_days 日收盘未盈利 → 收盘离场
    （预注册出场网格 V3b; 同日止损/止盈优先; --time-stop-days 0 可关闭回退 V0）
  - 全部收益扣 0.25% 往返交易成本；前向收益报 t 值/非重叠子样本/全市场基准超额

输出 JSON: strategies/funnel_strategy/docs/pullback_backtest.json
（结构与 main_uptrend_backtest.json 一致，供 funnel 报告页渲染）

2026-10-14 审查修复（本文件）:
  问题1  NaN 开盘价弃单（不再污染统计）；build_summary 剔除非有限收益
  问题2  入场 K 线必须是信号日的次一交易日（停牌直接弃单，不再数周后成交）
  问题5  同票持仓去重 + 最大并发持仓数记录
  问题6  exit_stats 的 t 改按 eval_date 聚类（cluster_t_stat）
  问题8  hold_days 统一为"含入场日的持有交易日数"，上限 bt_max_hold
  问题9  数据不足 max_hold 的强制平仓单列 exit_type="data_end"（不再混入超时）
  问题12 主结论段落输出 Holm 多重检验校正 p 值
  问题13 触发止损当日跌停 → 延后一日按次日开盘成交（不再按不可成交的 stop 价）
  问题14 回测结束日用 SELECT MAX(trade_date) 单查询
  问题18 基准套与策略相同的基础过滤；超额收益按信号日配对差分 + 聚类 t
  问题19 by_year 样本 <30 只报 count，不报比率
  问题4  收益/止损/前向全部复权口径；报告的 entry/exit 价为原始价（raw）

2026-10-19 出场改造（预注册, 详见 docs/收益改造报告_2026-10-14.md 第二轮）:
  - 时间止损 V3b: 持有第 bt_time_stop_days 日收盘未盈利 → 收盘出场
    exit_type="time_stop"（预注册 8 变体网格按验证窗>0 入选 + 全样本聚类 t
    排序选定; 效应量 +0.12 t、噪声级, 如实标注; 0=关闭回退 V0）
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


def holm_adjust(pvals: dict) -> dict:
    """Holm-Bonferroni 多重检验校正（问题12，本地实现，不动 shared 模块）。

    pvals: {名称: 原始 p}；返回 {名称: 校正后 p}（单调化，截断到 1）。
    """
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    adj, running = {}, 0.0
    for rank, (k, p) in enumerate(items):
        v = min(1.0, (m - rank) * float(p))
        running = max(running, v)          # Holm 单调化：校正 p 不会随排序下降
        adj[k] = running
    return adj


def simulate(sigs: pd.DataFrame, quotes: pd.DataFrame, cfg,
             calendar: list | None = None) -> tuple[pd.DataFrame, dict]:
    """逐笔模拟：T+1 开盘入场 → 止损/止盈/时间止损/超时出场。

    返回 (trades_df, diag)。diag 含弃单分类计数与最大并发持仓数。
    修复（问题1/2/5/8/9/13）:
      - 入场前校验开盘价有限（NaN 弃单，不再穿透过滤污染统计）；
      - 交易日历校验入场 K 线 == 信号日次一交易日（停牌弃单）；
      - 同票持仓期间跳过新信号（去重），并记录最大并发持仓数；
      - hold_days 统一为含入场日的持有交易日数（1..bt_max_hold）；
      - 数据不足以持有到 max_hold → exit_type="data_end" 单列；
      - 止损触发当日跌停 → 延后一日按次日开盘成交（问题13简化建模）。
    """
    q = quotes.sort_values(["ts_code", "trade_date"])
    q_by_code = {k: g.reset_index(drop=True) for k, g in q.groupby("ts_code")}

    # 交易日历（问题2）：全市场日线的 DISTINCT trade_date，升序
    if calendar is None:
        calendar = sorted({pd.Timestamp(c).normalize()
                           for c in quotes["trade_date"].unique()})
    cal_pos = {c: p for p, c in enumerate(calendar)}

    sigs = sigs.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)

    busy: dict = {}          # ts_code -> 持仓占用至的日历位置（含）
    skipped = {"gap_up": 0, "suspended_next_day": 0, "nan_entry": 0,
               "entry_below_stop": 0, "no_data": 0, "duplicate_hold": 0}
    rows = []
    for _, s in sigs.iterrows():
        g = q_by_code.get(s["ts_code"])
        if g is None:
            skipped["no_data"] += 1
            continue
        idx = g.index[g["trade_date"] == s["trade_date"]]
        if len(idx) == 0:
            skipped["no_data"] += 1
            continue
        i = int(idx[0])
        if i + 1 >= len(g):
            skipped["no_data"] += 1                       # 信号日之后无数据，无法入场
            continue

        # ---- 问题2: 入场 K 线必须是信号日的次一交易日 ----
        sig_pos = cal_pos.get(pd.Timestamp(s["trade_date"]).normalize())
        if sig_pos is None or sig_pos + 1 >= len(calendar):
            skipped["no_data"] += 1
            continue
        entry_pos = sig_pos + 1
        entry_bar = g.iloc[i + 1]
        if pd.Timestamp(entry_bar["trade_date"]).normalize() != calendar[entry_pos]:
            skipped["suspended_next_day"] += 1            # T+1 停牌：弃单，不等复牌成交
            continue

        # ---- 问题5: 同票持仓去重 ----
        if busy.get(s["ts_code"], -1) >= entry_pos:
            skipped["duplicate_hold"] += 1
            continue

        # ---- 问题1: 开盘价有限性校验 ----
        entry = float(entry_bar["open"])
        if not np.isfinite(entry):
            skipped["nan_entry"] += 1
            continue
        # 跳空高开>3%：推文强调"买点靠近止损"，追高放弃
        if entry > float(s["close"]) * 1.03:
            skipped["gap_up"] += 1
            continue
        stop = float(s["stop_loss"])
        target = float(s["target_1"])
        if not (np.isfinite(stop) and np.isfinite(target)):
            skipped["nan_entry"] += 1
            continue
        if entry <= stop:
            skipped["entry_below_stop"] += 1              # 开盘已低于止损，风险几何失效
            continue

        # 复权→原始价换算系数（报告展示用；收益一律复权口径，问题4）
        s_raw_close = s["raw_close"] if "raw_close" in s.index else np.nan
        sig_scale = (float(s_raw_close) / float(s["close"])
                     if np.isfinite(float(s_raw_close)) and np.isfinite(float(s["close"]))
                     and abs(float(s["close"])) > 1e-12 else 1.0)

        # 跌停判定（问题13，原始价口径）
        ld_mask = engine.limit_down_mask(g)

        # A 股 T+1：T+1 开盘买入当日不可卖出，最早 T+2 出场
        # 问题8: hold = 含入场日的持有交易日数（入场日=1，上限 bt_max_hold）
        exit_price, exit_type, hold, exit_idx, delayed = None, "timeout", 0, i + 1, False
        for j in range(i + 2, min(i + cfg.bt_max_hold + 1, len(g))):
            bar = g.iloc[j]
            hold = j - i
            lo, hi = float(bar["low"]), float(bar["high"])
            if lo <= stop:                                # 止损优先于止盈判定
                if bool(ld_mask[j]):
                    # 问题13: 当日跌停封死无法按 stop 价成交 → 延后一日按开盘成交
                    if j + 1 < len(g):
                        exit_price, exit_type = float(g.iloc[j + 1]["open"]), "stop"
                        hold, exit_idx, delayed = (j + 1) - i, j + 1, True
                    else:                                 # 延后也无数据 → 按收盘出场
                        exit_price, exit_type, exit_idx = float(bar["close"]), "data_end", j
                    break
                # 正常情形按止损价成交；跳空低开（open<stop）只能按更差的开盘价成交
                exit_price, exit_type = min(stop, float(bar["open"])), "stop"
                exit_idx = j
                break
            if hi >= target:
                exit_price = float(bar["open"]) if float(bar["open"]) >= target else target
                exit_type, exit_idx = "target", j
                break
            # 2026-10-19 出场改造（预注册网格 V3b）: 第 bt_time_stop_days 个持有日
            # 收盘仍未盈利 → 按当日收盘离场（上面同日止损/止盈判定优先; 0=关闭）
            if cfg.bt_time_stop_days and hold == cfg.bt_time_stop_days:
                if float(bar["close"]) / entry - 1.0 <= 0.0:
                    exit_price, exit_type, exit_idx = float(bar["close"]), "time_stop", j
                    break
        if exit_price is None:
            k = min(i + cfg.bt_max_hold, len(g) - 1)
            # 问题9: 数据不足以持有到 max_hold → data_end 单列（不再按"最后收盘"算超时）
            exit_type = "timeout" if k >= i + cfg.bt_max_hold else "data_end"
            exit_price, hold, exit_idx = float(g.iloc[k]["close"]), k - i, k

        exit_bar = g.iloc[exit_idx]
        exit_scale = (float(exit_bar["raw_close"]) / float(exit_bar["close"])
                      if np.isfinite(float(exit_bar["close"]))
                      and abs(float(exit_bar["close"])) > 1e-12 else 1.0)

        ret = exit_price / entry - 1.0
        # 2026-10-13: 统一扣 0.25% 往返交易成本（原口径不计成本，微利被成本吃掉）
        ret = bs.net_return(ret)
        # 前向收益同样扣往返成本（持有到期卖出的等价口径），复权价口径
        fwd = {}
        for d in (10, 20, 60):
            k = i + 1 + d
            if k < len(g):
                fwd[d] = bs.net_return(float(g.iloc[k]["close"]) / entry - 1.0)

        # 问题5: 记录持仓区间（日历位置）用于去重与并发统计
        exit_pos = cal_pos.get(pd.Timestamp(exit_bar["trade_date"]).normalize(), entry_pos)
        busy[s["ts_code"]] = max(busy.get(s["ts_code"], -1), exit_pos)

        entry_raw = (float(entry_bar["raw_open"]) if "raw_open" in g.columns
                     else entry * sig_scale)
        rows.append({
            "ts_code": s["ts_code"],
            "stock_name": s.get("stock_name", ""),
            "eval_date": str(s["trade_date"].date()),
            "composite_score": float(s["final_score"]),
            # 报告价为原始价（可下单核对），收益/止损触发为复权口径（问题4）
            "entry_price": round(entry_raw, 3),
            "entry_price_qfq": round(entry, 3),
            "stop_loss": round(stop * sig_scale, 3),
            "target_1": round(target * sig_scale, 3),
            "exit_ret": round(ret, 4),
            "exit_price": round(exit_price * exit_scale, 3),
            "hold_days": int(hold),
            "exit_type": exit_type,
            "exit_date": str(exit_bar["trade_date"].date()),
            "limit_down_delayed": bool(delayed),
            "fwd": fwd,
            "_entry_pos": int(entry_pos),
            "_exit_pos": int(exit_pos),
        })

    # 最大并发持仓数（问题5）：区间 [entry_pos, exit_pos] 扫描
    events = []
    for r in rows:
        events.append((r["_entry_pos"], 1))
        events.append((r["_exit_pos"], -1))
    events.sort(key=lambda e: (e[0], e[1]))               # 同位置先减后加
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)

    diag = {
        "signals_total": int(len(sigs)),
        "trades": int(len(rows)),
        "skipped": skipped,
        "max_concurrent_positions": int(peak),
        "limit_down_delayed": int(sum(1 for r in rows if r["limit_down_delayed"])),
    }
    return pd.DataFrame(rows), diag


def fwd_stats(trades: pd.DataFrame, days: int, trading_days=None) -> dict | None:
    """前向收益统计：扣成本全样本 + 重叠校正 t 检验 + 非重叠子样本 + 基准超额。

    2026-10-14: 传 dates 时 shared.fwd_stats 自动走按评估日聚类路径
    （cluster_t_stat，修同日截面相关+窗口重叠），low_power 随之透传。
    """
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


def market_benchmark(quotes: pd.DataFrame, days: int, eval_dates,
                     cfg, meta: pd.DataFrame) -> tuple[dict | None, pd.Series | None]:
    """全市场等权基准：T+1 开盘入场、T+1+days 收盘出场，全市场平均，扣成本。

    2026-10-14 审查修复（问题18）: 基准套与策略相同的基础过滤——
    流动性(≥0.5亿)/股价≥3元/上市≥60天/当日非涨跌停（板块规则，信号日 T）/
    按日 PIT 非 ST/高开>3% 放弃；并保证入场价非 NaN。
    返回 (统计dict, 按信号日索引的基准净收益 Series)——后者供超额配对差分。
    """
    if quotes.empty:
        return None, None
    q = quotes.merge(meta[["ts_code", "list_date"]], on="ts_code", how="left")
    q = q.sort_values(["ts_code", "trade_date"])
    gdf = q.groupby("ts_code", sort=False)
    q["entry_open"] = gdf["open"].shift(-1)
    q["exit_close"] = gdf["close"].shift(-(1 + days))

    base = (
        (q["amount"] >= cfg.min_amount_yi * 1e8)
        & (q["close"] >= cfg.min_close)
        & engine.not_limit_up(q)                            # 信号日未涨停
        & engine.not_limit_down(q)                          # 信号日未跌停
        & q["entry_open"].notna() & q["exit_close"].notna()  # 问题1: 无入场/出场数据不计入
        & (q["entry_open"].astype("float64")
           <= q["close"].astype("float64") * 1.03)          # 同策略：高开>3%放弃
    )
    list_date = pd.to_datetime(q["list_date"])
    age_ok = list_date.isna() | (
        (q["trade_date"] - list_date).dt.days >= cfg.exclude_new_days)

    # 只对有信号的日期做 PIT ST 判定（基准仅在信号日取值，控制查询量）
    sig_dates = {pd.Timestamp(ed) for ed in eval_dates}
    qsig = q[q["trade_date"].isin(sig_dates)]
    st_ok = pd.Series(True, index=q.index)
    if cfg.exclude_st and len(qsig):
        from strategies.shared.st_history import is_st_mask
        from core.db.connection import get_db_fresh
        conn = get_db_fresh()
        try:
            for dt, idx in qsig.groupby(qsig["trade_date"].dt.date).groups.items():
                codes = qsig.loc[idx, "ts_code"].tolist()
                st_ok.loc[idx] = ~is_st_mask(conn, codes, dt)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    ok = base & age_ok & st_ok
    q = q.assign(_fwd=q["exit_close"] / q["entry_open"] - 1.0)
    by_date = q[ok].groupby("trade_date")["_fwd"].mean()

    gross, keys = [], []
    for ed in eval_dates:
        ts = pd.Timestamp(ed)
        v = by_date.get(ts)
        if v is not None and np.isfinite(v):
            gross.append(float(v))
            keys.append(ts)
    if not gross:
        return None, None
    arr = bs.net_returns(gross)
    series = pd.Series({k: float(v) for k, v in zip(keys, arr)})
    stats = {"count": int(len(arr)), "mean": float(np.mean(arr)),
             "win_rate": float(np.mean(arr > 0)),
             "filter_note": "同策略基础过滤：额≥0.5亿/价≥3元/上市≥60天/非涨跌停/非ST(PIT)/高开≤3%"}
    return stats, series


def build_summary(trades: pd.DataFrame, start: str, end: str,
                  quotes: pd.DataFrame, cfg=None, meta=None,
                  diag: dict | None = None) -> dict:
    diag = diag or {}
    eval_dates_all = sorted(set(trades["eval_date"])) if len(trades) else []
    # 问题1: 剔除非有限值（NaN/inf 不再进入任何统计）
    if len(trades):
        rets_all = trades["exit_ret"].to_numpy(dtype=float)
        finite_mask = np.isfinite(rets_all)
        rets = rets_all[finite_mask]
        fin_dates = trades.loc[finite_mask, "eval_date"].tolist()
    else:
        rets, finite_mask, fin_dates = np.array([]), np.array([], dtype=bool), []
    exits = trades["exit_type"].value_counts().to_dict() if len(trades) else {}
    # 问题6: exit_stats 的 t 改按 eval_date 聚类（同日多票截面相关 → 日均值检验）
    exit_sig = (bs.cluster_t_stat(rets, fin_dates, horizon=1)
                if len(rets) else {"t": 0.0, "p": 1.0, "low_power": True})
    # 交易日历（非重叠采样用）
    trading_days = sorted({d.date() for d in quotes["trade_date"].unique()}) \
        if len(quotes) else None
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
            # 新增字段（向后兼容：旧字段全保留）
            "time_stop_count": int(exits.get("time_stop", 0)),
            "data_end_count": int(exits.get("data_end", 0)),
            "limit_down_delayed_count": int(diag.get("limit_down_delayed", 0)),
            "n_eff": exit_sig.get("n_eff", 0),
            "n_days": exit_sig.get("n_days", 0),
            "low_power": bool(exit_sig.get("low_power", True)),
            "sig_source": "cluster_by_date",
        },
        "simulation": {
            "signals_total": int(diag.get("signals_total", 0)),
            "trades": int(diag.get("trades", len(trades))),
            "skipped": diag.get("skipped", {}),
            "max_concurrent_positions": int(diag.get("max_concurrent_positions", 0)),
            "note": "同票持仓期间跳过新信号；无资金约束，最大并发持仓数仅供参考",
        },
        "forward_returns": {},
        "benchmark": {},
        "excess": {},
        "excess_stats": {},
        "by_year": {},
        "top10_signals": [],
        "all_signals": [],
    }
    main_ps = {"exit_ret": float(exit_sig["p"])}
    for d in (10, 20, 60):
        st = fwd_stats(trades, d, trading_days=trading_days)
        if st:
            summary["forward_returns"][str(d)] = st
            main_ps[f"fwd{d}d"] = float(st["p"])
            if meta is not None and cfg is not None:
                bm, bm_series = market_benchmark(
                    quotes, d, eval_dates_all, cfg, meta)
                if bm:
                    summary["benchmark"][str(d)] = bm
                    # 旧字段：均值差（页面直接渲染）
                    summary["excess"][str(d)] = st["mean"] - bm["mean"]
                    # 问题18: 超额按信号日配对差分 + 聚类 t
                    diffs, ddates = [], []
                    for _, row in trades.iterrows():
                        v = row["fwd"]
                        if isinstance(v, dict) and d in v and bm_series is not None:
                            bv = bm_series.get(pd.Timestamp(row["eval_date"]))
                            if bv is not None and np.isfinite(float(bv)):
                                diffs.append(float(v[d]) - float(bv))
                                ddates.append(row["eval_date"])
                    if diffs:
                        ex = bs.cluster_t_stat(diffs, ddates, horizon=d)
                        ex["sig_source"] = "paired_cluster_by_date"
                        summary["excess_stats"][str(d)] = ex
                        main_ps[f"excess{d}d"] = float(ex["p"])

    # 问题12: 主结论段落（出场 t + 前向 3 窗口 + 超额配对）Holm 多重检验校正
    holm = holm_adjust(main_ps)
    summary["holm_correction"] = {
        "method": "holm",
        "scope": "主结论：exit_ret t + 前向10/20/60 t + 基准超额配对 t",
        "n_tests": len(main_ps),
        "alpha": 0.05,
        "raw_p": {k: round(float(v), 6) for k, v in main_ps.items()},
        "adjusted_p": {k: round(float(v), 6) for k, v in holm.items()},
        "reject_005": {k: bool(v <= 0.05) for k, v in holm.items()},
        "note": "低功效（n_eff<10）项即便校正后显著也不作结论；参数与评估同区间调优，p 值仍偏乐观",
    }
    summary["significance_note_main"] = (
        "主结论已做 Holm 多重检验校正（见 holm_correction.adjusted_p）；"
        "t 按评估日聚类（同日截面相关+窗口重叠校正）；"
        "低功效项不作结论；参数与评估同区间调优，p 值偏乐观"
    )

    # 分子期稳定性（问题8: 单段全样本可能由某一段行情主导）
    # 问题19: 分年样本 <30 不报比率只报样本数（比率在小样本下是噪声）
    if len(trades):
        trades = trades.copy()
        trades["_year"] = pd.to_datetime(trades["eval_date"]).dt.year
        for year, sub in trades.groupby("_year"):
            yr = {"count": int(len(sub))}
            if len(sub) < 30:
                yr["low_sample"] = True
                summary["by_year"][str(year)] = yr
                continue
            r = sub["exit_ret"].to_numpy(dtype=float)
            r = r[np.isfinite(r)]
            yr["win_rate"] = float(np.mean(r > 0)) if len(r) else 0.0
            yr["mean"] = float(np.mean(r)) if len(r) else 0.0
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
                "exit_date": t.get("exit_date", ""),
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
                "exit_date": t.get("exit_date", ""),
            })
    return summary


def main():
    ap = argparse.ArgumentParser(description="回调买入法 — 回测")
    ap.add_argument("--start", default=CFG.bt_start)
    ap.add_argument("--end", default=CFG.bt_end or None)
    ap.add_argument("--output", default=None, help="输出 JSON 路径")
    ap.add_argument("--time-stop-days", type=int, default=None,
                    help="时间止损天数（覆盖 config; 0=关闭回退 V0）")
    args = ap.parse_args()

    if args.time_stop_days is not None:
        CFG.bt_time_stop_days = int(args.time_stop_days)

    end = args.end
    if not end:
        # 问题14: SELECT MAX(trade_date) 单查询（原硬编码探测日+全表扫描）
        end = engine.latest_trade_date()
        if not end:
            print("❌ 无法确定回测结束日期（daily_quotes 为空）")
            return 1

    # 预热: 动能120交易日 + EMA50 + 20日高点 + ATR14 → 提前 260 自然日
    warm = str(datetime.strptime(args.start, "%Y-%m-%d").date() - timedelta(days=260))
    print(f"[回调买入法回测] 区间 {args.start} ~ {end}  (数据 {warm} 起，含预热)")

    meta = engine.load_stock_meta()
    quotes = engine.load_quotes(warm, end)
    print(f"  行情 {len(quotes)} 行, {quotes['ts_code'].nunique()} 只股票（前复权口径）")
    # 交易日历（问题2/非重叠采样共用）：全市场日线的 DISTINCT trade_date
    calendar = sorted({pd.Timestamp(c).normalize()
                       for c in quotes["trade_date"].unique()})
    quotes = engine.add_indicators(quotes, CFG)

    sigs = engine.generate_signals(quotes, CFG, meta, start=args.start, end=end)
    print(f"  信号 {len(sigs)} 条")
    if sigs.empty:
        print("❌ 无信号，回测终止")
        return 1

    trades, diag = simulate(sigs, quotes, CFG, calendar=calendar)
    sk = diag["skipped"]
    print(f"  成交 {len(trades)} 笔（信号 {diag['signals_total']}，"
          f"弃单: 追高 {sk['gap_up']} / 次日停牌 {sk['suspended_next_day']} / "
          f"开盘NaN {sk['nan_entry']} / 开盘破止损 {sk['entry_below_stop']} / "
          f"同票持仓 {sk['duplicate_hold']} / 无数据 {sk['no_data']}）")
    print(f"  最大并发持仓: {diag['max_concurrent_positions']}（无资金约束，仅供参考）")

    summary = build_summary(trades, args.start, end, quotes, cfg=CFG, meta=meta,
                            diag=diag)
    es = summary["exit_stats"]
    print("\n" + "=" * 56)
    exit_lp = "（低功效，不作结论）" if es.get("low_power") else ""
    print(f"总信号: {summary['total_signals']}   胜率: {es['win_rate']:.1%}   "
          f"平均收益: {es['mean']:+.2%}   中位: {es['median']:+.2%}   "
          f"t={es['t']:.2f} (p={es['p']:.3f}, n_eff={es.get('n_eff', 0)}){exit_lp}")
    print(f"出场: 止损 {es['stop_count']} / 止盈 {es['target_count']} / "
          f"时间止损 {es.get('time_stop_count', 0)} / "
          f"超时 {es['timeout_count']} / 数据中断 {es['data_end_count']}   "
          f"平均持有 {es['avg_hold_days']:.1f} 日")
    print(f"口径: {summary['cost_model']}")
    holm = summary["holm_correction"]["adjusted_p"]
    for d, st in summary["forward_returns"].items():
        bm = summary["benchmark"].get(d)
        exc = summary["excess"].get(d)
        exs = summary["excess_stats"].get(d, {})
        bm_s = f"  基准={bm['mean']:+.2%}  超额={exc:+.2%}" if bm else ""
        lp = "（低功效）" if st.get("low_power") else ""
        print(f"前向{d:>2}日: n={st['count']:<5} 胜率={st['win_rate']:.1%} "
              f"均值={st['mean']:+.2%} t={st['t']:.2f} "
              f"(p={st['p']:.3f}, n_eff={st['n_eff']}, Holm p={holm.get('fwd' + d + 'd', st['p']):.3f}){lp}{bm_s}")
        if exs:
            print(f"          超额配对: n={exs.get('count', 0)} t={exs.get('t', 0):.2f} "
                  f"(p={exs.get('p', 1):.3f}, Holm p={holm.get('excess' + d + 'd', 0):.3f})")
        non = st.get("nonoverlap", {})
        if non.get("count"):
            print(f"          非重叠: n={non['count']} 均值={non['mean']:+.2%} "
                  f"t={non['t']:.2f} (p={non['p']:.3f})")
    for y, yr in summary["by_year"].items():
        if yr.get("low_sample"):
            print(f"{y} 分段: n={yr['count']}（样本<30，不报比率）")
        else:
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
