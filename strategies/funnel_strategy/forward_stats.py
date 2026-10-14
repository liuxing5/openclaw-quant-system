"""前向收益重算 — 七步漏斗 / LLM多源 / 八步隔夜法（统一成本 + 显著性口径）

背景（2026-10-13 审查）：
  三个策略历史上每天存候选（daily_candidates / funnel_results），但报告页从未算过
  它们的前向收益，也没有交易成本、显著性检验口径。本脚本用 strategies/shared/
  backtest_stats.py 的统一口径重算，并输出三个 JSON 供 generate_html.py 渲染。

Usage:
  python -m strategies.funnel_strategy.forward_stats                    # 默认全跑
  python -m strategies.funnel_strategy.forward_stats --sources funnel,eight
  python -m strategies.funnel_strategy.forward_stats --horizons 10,20

口径（与其他策略统一）：
  - 评估日 T = 快照日（候选生成日，盘后生成）→ T+1 开盘价入场；
    注意：回调法有"高开>3% 追高放弃"规则，本脚本不做该过滤（候选池不同，口径统一为
    全部按 T+1 开盘成交），仅扣固定往返成本 0.25%（买 0.10% + 卖 0.15%）。
  - T+1 无行情（停牌）→ 剔除该样本，并按原因计入 dropped_by_reason；
  - 前向 h 日毛收益 = close[T+1+h] / open[T+1] - 1（h=10/20/60 交易日），
    价格为**前复权价**（见下）；T+1+h 无行情 → 剔除该 horizon 样本并计数；
  - 毛收益经 net_returns() 扣成本后做 fwd_stats（含按日聚类 t/p、非重叠子样本）；
  - 基准 = 全市场等权（T+1 开盘 amount>=1e8 过滤流动性，amount 用 raw 不复权值）
    同窗口同入场约定的净收益均值，excess = 策略均值 - 基准均值，
    excess 的显著性按信号日配对差分（策略日均 - 基准日均）做 cluster_t_stat → excess_sig；
  - 八步法额外拆分隔夜段 close[T]→open[T+1] 与日内段 open[T+1]→close[T+1]（毛收益
    口径，成本只在完整持仓上扣），验证"A股隔夜收益平均为负"对该候选池是否成立。

2026-10-14 审查修复：
  1. 行情覆盖区间不再硬编码 (2026,1)~(2026,10)，改为从 daily_quotes 取
     MIN/MAX(trade_date) 动态分月扫描，实际区间写入输出 JSON 的 quote_coverage；
  2. 收益率/隔夜日内拆分全部改用前复权价（strategies.shared.adjust.qfq_factors
     沿时间轴的累计收益链），修复除权缺口被当成亏损（实测混入约 -0.64%）的问题；
  3. 停牌/缺行情/超出矩阵范围的样本按原因计数 dropped_by_reason，不再静默剔除；
  4. llm 盘前回退（aggregate.py 把昨日候选改日期复制为今日信号，logic_tags 带
     morning_reuse/盘前复用）在读库 SQL 侧剔除，并在 JSON 披露剔除数与 run_mode 分布；
  5. generated_at 改用北京时间(UTC+8)，另给数据截止日 data_as_of 与 quote_coverage。

数据源：
  - funnel 优先 funnel_results（trade_date + candidates JSONB），缺失退回
    daily_candidates source='funnel_strategy'；
  - llm  = daily_candidates source='llm_multisource'（全部 run_mode，含 selected 与否）；
  - eight= daily_candidates source='overnight_8step'（全部 run_mode）。

输出（strategies/funnel_strategy/docs/）：
  funnel_forward.json / llm_forward.json / eight_forward.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from dotenv import load_dotenv
for _env_path in [Path('.env'), Path('strategies/llm_multisource/.env')]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

from core.db.connection import get_db_fresh
from strategies.shared.adjust import qfq_factors
from strategies.shared.backtest_stats import (
    COST_NOTE, SIGNIFICANCE_NOTE, cluster_t_stat, fwd_stats, net_returns, t_stat,
)

HORIZONS = (10, 20, 60)
LIQ_THRESHOLD = 1e8          # 基准流动性过滤：T+1 开盘成交额（元，raw 口径）
ENTRY_CONVENTION = (
    "T+1 open entry (no gap-up veto), qfq-adjusted prices, net of 0.25% "
    "round-trip cost; benchmark = equal-weight all-market (T+1 open amount>=1e8, raw amount)"
)
BEIJING_TZ = timezone(timedelta(hours=8))

# aggregate.py 盘前回退分支写入的标记（新旧两套标签都认）
MORNING_REUSE_TAGS = ("morning_reuse", "盘前复用")
MORNING_REUSE_SQL = (
    "('morning_reuse' = ANY(COALESCE(logic_tags, '{}')) "
    "OR '盘前复用' = ANY(COALESCE(logic_tags, '{}')))"
)
MORNING_REUSE_NOTE = (
    "aggregate.py morning 模式在无合格候选时会把昨日 afternoon 选中候选改日期"
    "复制为今日信号（logic_tags 含 morning_reuse/盘前复用），读库时已剔除，"
    "否则会虚增样本量 n；excluded_morning_reuse 为被剔除行数"
)


# ---------------------------------------------------------------- 候选加载

def load_funnel_candidates(conn) -> tuple[list[tuple[date, str]], dict]:
    """优先 funnel_results.candidates (JSONB)，退回 daily_candidates funnel_strategy。"""
    cur = conn.cursor()
    cur.execute("SELECT trade_date, candidates FROM funnel_results ORDER BY trade_date")
    samples: list[tuple[date, str]] = []
    for row in cur.fetchall():
        raw = row["candidates"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not raw:
            continue
        for item in raw:
            code = (item or {}).get("ts_code")
            if code:
                samples.append((row["trade_date"], code))
    detail = "funnel_results.candidates"
    if not samples:
        cur.execute("""
            SELECT snapshot_date, ts_code FROM daily_candidates
            WHERE source = 'funnel_strategy' ORDER BY snapshot_date""")
        samples = [(r["snapshot_date"], r["ts_code"]) for r in cur.fetchall()]
        detail = "daily_candidates[funnel_strategy]"
    meta = {"candidate_detail": detail,
            "excluded_morning_reuse": 0,
            "run_mode_breakdown": {}}
    return samples, meta


def load_dc_candidates(conn, source: str) -> tuple[list[tuple[date, str]], dict]:
    """读 daily_candidates；盘前回退样本在 SQL 侧剔除并披露计数。

    2026-10-14 审查：aggregate.py 的 morning 回退会把昨日候选复制为今日信号，
    若计入前向收益会虚增 n。logic_tags 是 TEXT[]，用 = ANY() 判定（COALESCE
    兜底 NULL），同时返回保留行的 run_mode 分布供页面披露。
    """
    cur = conn.cursor()
    cur.execute(f"""
        SELECT snapshot_date, ts_code FROM daily_candidates
        WHERE source = %s AND NOT {MORNING_REUSE_SQL}
        ORDER BY snapshot_date""", (source,))
    samples = [(r["snapshot_date"], r["ts_code"]) for r in cur.fetchall()]

    cur.execute(f"""
        SELECT COUNT(*) AS n FROM daily_candidates
        WHERE source = %s AND {MORNING_REUSE_SQL}""", (source,))
    row = cur.fetchone()
    excluded = int(row["n"] or 0) if row else 0

    cur.execute(f"""
        SELECT run_mode, COUNT(*) AS n FROM daily_candidates
        WHERE source = %s AND NOT {MORNING_REUSE_SQL}
        GROUP BY run_mode ORDER BY run_mode""", (source,))
    breakdown = {str(r["run_mode"]): int(r["n"]) for r in cur.fetchall()}

    meta = {"candidate_detail": f"daily_candidates[{source}](morning_reuse excluded)",
            "excluded_morning_reuse": excluded,
            "run_mode_breakdown": breakdown,
            "morning_reuse_note": MORNING_REUSE_NOTE}
    return samples, meta


# ---------------------------------------------------------------- 行情矩阵

class QuoteMatrix:
    """ts_code × trade_date 的价格/成交额密集矩阵 + 交易日历映射。

    字段：
      open / close       —— **前复权价**（所有收益率、隔夜/日内拆分都用它）
      open_raw / close_raw —— 不复权原始价（仅留作核查）
      amount             —— 不复权成交额（流动性过滤，与复权无关）
      pct / factor       —— 当日涨跌幅(%) 与前复权因子（末日锚定 = 1）

    覆盖区间从 daily_quotes 的 MIN/MAX(trade_date) 动态推导（此前硬编码到
    2026-10，跨年后会静默截断），并按月切片用服务端游标流式读取。
    """

    def __init__(self, conn):
        # 本连接放宽语句超时（连接默认 60s，全表月度扫描在库繁忙时不够）
        cur = conn.cursor()
        cur.execute("SET statement_timeout = 600000")
        cur.execute("SELECT MIN(trade_date) AS min_d, MAX(trade_date) AS max_d "
                    "FROM daily_quotes")
        row = cur.fetchone()
        if isinstance(row, dict):
            min_d, max_d = row.get("min_d"), row.get("max_d")
        else:  # tuple 游标兜底
            min_d, max_d = row[0], row[1]
        if min_d is None or max_d is None:
            cur.execute("SET statement_timeout = 60000")
            raise RuntimeError("daily_quotes 为空，无法构建行情矩阵")

        # 分月拉取 + 服务器端游标流式读取，避免一次性大结果集触发 SSL 断连/超时
        chunks = []
        y, m = min_d.year, min_d.month
        end_y, end_m = max_d.year, max_d.month
        try:
            while (y, m) <= (end_y, end_m):
                ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
                scur = conn.cursor(name=f"qm_{y}_{m:02d}")
                scur.itersize = 50000
                scur.execute("""
                    SELECT ts_code, trade_date, open, close, amount, pct_chg
                    FROM daily_quotes
                    WHERE trade_date >= %s AND trade_date < %s""",
                    (date(y, m, 1), date(ny, nm, 1)))
                rows = list(scur.fetchall())
                scur.close()
                chunks.append(pd.DataFrame(
                    rows, columns=["ts_code", "trade_date", "open", "close",
                                   "amount", "pct_chg"]))
                y, m = ny, nm
            df = pd.concat(chunks, ignore_index=True)
        finally:
            # 恢复默认，避免超时设置泄漏到连接池（异常路径也必须恢复）
            cur.execute("SET statement_timeout = 60000")
        df = df.dropna(subset=["open", "close"])
        if df.empty:
            raise RuntimeError("daily_quotes 无有效 open/close 行情")
        self.days: list[date] = sorted(df["trade_date"].unique())
        self.day_pos = {d: i for i, d in enumerate(self.days)}
        self.codes = sorted(df["ts_code"].unique())
        self.code_pos = {c: i for i, c in enumerate(self.codes)}
        idx = df["ts_code"].map(self.code_pos)
        cols = df["trade_date"].map(self.day_pos)
        shape = (len(self.codes), len(self.days))
        self.open_raw = np.full(shape, np.nan)
        self.close_raw = np.full(shape, np.nan)
        self.amount = np.full(shape, np.nan)
        self.pct = np.full(shape, np.nan)
        self.open_raw[idx, cols] = df["open"].astype(float)
        self.close_raw[idx, cols] = df["close"].astype(float)
        self.amount[idx, cols] = df["amount"].astype(float)
        if "pct_chg" in df.columns:
            self.pct[idx, cols] = pd.to_numeric(df["pct_chg"],
                                                errors="coerce").astype(float)

        # 前复权：沿时间轴的累计收益链（无除权时 factor ≡ 1，qfq == raw）
        self.factor = qfq_factors(self.pct)
        self.open = self.open_raw * self.factor
        self.close = self.close_raw * self.factor

        self.coverage = {
            "start": str(min(self.days)),
            "end": str(max(self.days)),
            "n_days": int(len(self.days)),
            "n_codes": int(len(self.codes)),
            "adjusted": "qfq (strategies.shared.adjust.qfq_factors)",
        }

    def next_pos(self, d: date) -> int | None:
        """T 的下一交易日（T+1 入场日）下标；T 为最后交易日返回 None。"""
        p = self.day_pos.get(d)
        if p is None or p + 1 >= len(self.days):
            return None
        return p + 1


# ---------------------------------------------------------------- 核心统计

def strategy_forward(qm: QuoteMatrix, samples: list[tuple[date, str]],
                     trading_days: list[date],
                     horizons: tuple[int, ...]) -> dict:
    """逐 horizon 计算策略前向净收益统计（含基准、excess 与配对显著性）。

    剔除样本不再静默丢弃：按 horizon 输出 dropped_by_reason
    （out_of_range 超出行情矩阵 / no_entry 入场价缺失 / no_exit 出场价缺失）。
    """
    # 去重（同一 (日期, 股票) 可能因多 run_mode 重复入库）
    uniq = sorted(set(samples))
    code_idx = np.array([qm.code_pos.get(c, -1) for _, c in uniq], dtype=np.int64)
    t_idx = np.array([qm.day_pos.get(d, -1) for d, _ in uniq], dtype=np.int64)
    valid = (code_idx >= 0) & (t_idx >= 0)
    code_idx, t_idx = code_idx[valid], t_idx[valid]
    eval_dates = [uniq[i][0] for i in np.flatnonzero(valid)]
    not_in_matrix = int((~valid).sum())

    out = {"sample_rows": len(samples), "unique_signals": len(uniq),
           "signals_out_of_matrix": not_in_matrix,
           "with_entry": int(0), "forward": {}, "benchmark": {}, "excess": {},
           "excess_sig": {}, "dropped_by_reason": {}}

    for h in horizons:
        entry_pos = t_idx + 1
        exit_pos = entry_pos + h
        in_range = (exit_pos < len(qm.days)) & (entry_pos < len(qm.days))
        dropped = {"out_of_range": int((~in_range).sum()),
                   "no_entry": 0, "no_exit": 0}
        ci, ei = code_idx[in_range], entry_pos[in_range]
        entry_open = qm.open[ci, ei]
        exit_close = qm.close[ci, exit_pos[in_range]]
        ok_entry = ~np.isnan(entry_open) & (entry_open > 0)
        ok_exit = ~np.isnan(exit_close)
        dropped["no_entry"] = int((~ok_entry).sum())
        dropped["no_exit"] = int((ok_entry & ~ok_exit).sum())
        ok = ok_entry & ok_exit
        gross = exit_close[ok] / entry_open[ok] - 1.0
        sdates = [eval_dates[i] for i in np.flatnonzero(in_range)[ok]]
        if h == horizons[0]:
            out["with_entry"] = int(ok.sum())
        out["dropped_by_reason"][str(h)] = dropped

        net = net_returns(gross)
        out["forward"][str(h)] = fwd_stats(net, dates=sdates, horizon=h,
                                           trading_days=trading_days)

        # 基准：同一批评估日、全市场等权（entry 日 amount>=1e8，amount 用 raw）
        bench_gross = []
        bench_dates = []
        for d in sorted(set(sdates)):
            p = qm.day_pos[d] + 1
            x = p + h
            if x >= len(qm.days):
                continue
            amt = qm.amount[:, p]
            op = qm.open[:, p]
            cl = qm.close[:, x]
            m = (amt >= LIQ_THRESHOLD) & ~np.isnan(op) & ~np.isnan(cl) & (op > 0)
            if not m.any():
                continue
            bench_gross.append(float(np.mean(cl[m] / op[m] - 1.0)))
            bench_dates.append(d)
        bnet = net_returns(bench_gross)
        out["benchmark"][str(h)] = fwd_stats(bnet, dates=bench_dates, horizon=h,
                                             trading_days=trading_days)
        sm = out["forward"][str(h)]["mean"]
        bm = out["benchmark"][str(h)]["mean"]
        out["excess"][str(h)] = float(sm - bm)

        # excess 显著性：按信号日配对差分（策略日均 − 基准日均）再做聚类 t
        strat_daily: dict = {}
        for d, v in zip(sdates, net):
            strat_daily.setdefault(d, []).append(float(v))
        pair_dates, pair_diff = [], []
        for d, b in zip(bench_dates, bnet):
            if d in strat_daily:
                pair_dates.append(d)
                pair_diff.append(float(np.mean(strat_daily[d])) - float(b))
        if pair_diff:
            out["excess_sig"][str(h)] = cluster_t_stat(pair_diff, pair_dates,
                                                       horizon=h)
        else:
            out["excess_sig"][str(h)] = {**t_stat([]), "n_days": 0,
                                         "method": "naive",
                                         "note": "策略与基准无可配对信号日"}

    return out


def overnight_decomp(qm: QuoteMatrix, samples: list[tuple[date, str]]) -> dict:
    """八步法隔夜/日内拆分（毛收益口径：成本只在完整持仓扣，不摊到分段）。

    close/open 均为前复权价，除权缺口不会被误算成隔夜/日内收益。
    """
    uniq = sorted(set(samples))
    on_gross, id_gross, seg_dates = [], [], []
    for d, c in uniq:
        ci = qm.code_pos.get(c)
        ti = qm.day_pos.get(d)
        if ci is None or ti is None or ti + 1 >= len(qm.days):
            continue
        c_t = qm.close[ci, ti]        # close[T]（前复权）
        o_t1 = qm.open[ci, ti + 1]    # open[T+1]（前复权）
        c_t1 = qm.close[ci, ti + 1]   # close[T+1]（前复权）
        if np.isnan(c_t) or np.isnan(o_t1) or np.isnan(c_t1) or c_t <= 0:
            continue
        on_gross.append(o_t1 / c_t - 1.0)
        id_gross.append(c_t1 / o_t1 - 1.0)
        seg_dates.append(d)

    def _seg(vals: list[float], dates: list[date]) -> dict:
        st = t_stat(vals)
        cl = cluster_t_stat(vals, dates, horizon=1) if dates else st
        arr = np.asarray(vals, dtype=float)
        return {"count": st["n"], "win_rate": float((arr > 0).mean()) if len(arr) else 0.0,
                "mean": st["mean"], "median": float(np.median(arr)) if len(arr) else 0.0,
                "std": st["std"], "t": st["t"], "p": st["p"],
                "ci95_low": st["ci95_low"], "ci95_high": st["ci95_high"],
                "n_eff": int(cl.get("n_eff", st["n"])),
                "n_days": int(cl.get("n_days", 0)),
                "sig_source": str(cl.get("method", "n_eff")),
                "low_power": bool(st.get("low_power", False))}

    return {
        "note": ("隔夜段=close[T]→open[T+1]，日内段=open[T+1]→close[T+1]；"
                 "均为前复权价的毛收益（往返成本只在完整前向持仓上扣，不摊到分段）"),
        "overnight": _seg(on_gross, seg_dates),
        "intraday": _seg(id_gross, seg_dates),
    }


# ---------------------------------------------------------------- 主流程

def run_source(conn, qm: QuoteMatrix, trading_days: list[date],
               key: str, horizons: tuple[int, ...]) -> dict:
    if key == "funnel":
        samples, meta = load_funnel_candidates(conn)
        source_name, out_name = "funnel_strategy", "funnel_forward.json"
    elif key == "llm":
        samples, meta = load_dc_candidates(conn, "llm_multisource")
        source_name, out_name = "llm_multisource", "llm_forward.json"
    elif key == "eight":
        samples, meta = load_dc_candidates(conn, "overnight_8step")
        source_name, out_name = "overnight_8step", "eight_forward.json"
    else:
        raise ValueError(f"unknown source: {key}")

    if not samples:
        print(f"❌ [{key}] 无候选数据，跳过")
        return {}

    stats = strategy_forward(qm, samples, trading_days, horizons)
    dates = [d for d, _ in samples]
    now_bj = datetime.now(BEIJING_TZ)
    result = {
        "source": source_name,
        "generated_at": now_bj.date().isoformat(),
        "generated_at_time": now_bj.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone_note": ("generated_at/generated_at_time 为北京时间(UTC+8)；"
                          "data_as_of = daily_quotes 最新交易日（数据实际截止日）"),
        "data_as_of": qm.coverage["end"],
        "quote_coverage": qm.coverage,
        "entry_convention": ENTRY_CONVENTION,
        "cost_note": COST_NOTE,
        "significance_note": SIGNIFICANCE_NOTE,
        "candidate_detail": meta.get("candidate_detail", ""),
        "excluded_morning_reuse": int(meta.get("excluded_morning_reuse", 0)),
        "run_mode_breakdown": meta.get("run_mode_breakdown", {}),
        "morning_reuse_note": meta.get("morning_reuse_note", ""),
        "sample_range": [min(dates).isoformat(), max(dates).isoformat()],
        "total_candidates": stats["sample_rows"],
        "unique_signals": stats["unique_signals"],
        "signals_out_of_matrix": stats["signals_out_of_matrix"],
        "dropped_by_reason": stats["dropped_by_reason"],
        "benchmark": stats["benchmark"],
        "forward": stats["forward"],
        "excess": stats["excess"],
        "excess_sig": stats["excess_sig"],
    }
    if key == "eight":
        result["overnight_decomp"] = overnight_decomp(qm, samples)

    out_path = Path(__file__).parent / "docs" / out_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                   default=str),
                        encoding="utf-8")
    print(f"✓ [{key}] {out_path}  (rows={stats['sample_rows']}, "
          f"unique={stats['unique_signals']}, "
          f"excluded_morning_reuse={result['excluded_morning_reuse']})")
    for h in horizons:
        f = stats["forward"][str(h)]
        b = stats["benchmark"][str(h)]
        es = stats["excess_sig"][str(h)]
        dr = stats["dropped_by_reason"][str(h)]
        print(f"  h={h:>2}: n={f['count']:<5} win={f['win_rate']:.1%} "
              f"mean={f['mean']:+.2%} t={f['t']:+.2f} p={f['p']:.3f} | "
              f"bench={b['mean']:+.2%} excess={stats['excess'][str(h)]:+.2%} "
              f"(excess t={es.get('t', 0):+.2f} p={es.get('p', 1):.3f}) | "
              f"dropped={dr}")
    if "overnight_decomp" in result:
        od = result["overnight_decomp"]
        print(f"  隔夜段: mean={od['overnight']['mean']:+.4%} "
              f"t={od['overnight']['t']:+.2f} p={od['overnight']['p']:.3f} | "
              f"日内段: mean={od['intraday']['mean']:+.4%} "
              f"t={od['intraday']['t']:+.2f} p={od['intraday']['p']:.3f}")
    return result


def main():
    ap = argparse.ArgumentParser(description="漏斗/LLM多源/八步法 前向收益重算")
    ap.add_argument("--sources", default="funnel,llm,eight",
                    help="逗号分隔：funnel,llm,eight")
    ap.add_argument("--horizons", default="10,20,60",
                    help="逗号分隔的前向窗口（交易日）")
    args = ap.parse_args()

    keys = [k.strip() for k in args.sources.split(",") if k.strip()]
    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())
    if not horizons or any(h <= 0 for h in horizons):
        print("❌ horizons 必须为正整数")
        return 1

    conn = get_db_fresh(use_dict_cursor=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT trade_date FROM daily_quotes ORDER BY trade_date")
        trading_days = [r["trade_date"] for r in cur.fetchall()]
        print(f"交易日历: {len(trading_days)} 天 "
              f"({trading_days[0]} ~ {trading_days[-1]})")
        qm = QuoteMatrix(conn)
        print(f"行情矩阵: {len(qm.codes)} 股 × {len(qm.days)} 日 "
              f"({qm.coverage['start']} ~ {qm.coverage['end']}，前复权)")

        for key in keys:
            run_source(conn, qm, trading_days, key, horizons)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
