"""Final backtest for overnight 8-step v2: panic gap-down reversal in an uptrend.

Signal at T close, entry T+1 open, exit T+2 close (minimum legal hold under T+1).
"""
import json
import os

import numpy as np
import pandas as pd

from strategy_lab import load, universe

COST = 0.0025
TOP_N = 5

df = load()
u = universe(df)

mkt = df.groupby("trade_date").agg(mkt=("pct_chg", "mean"), mb20=("b20", "first"))
mkt["m_idx"] = (1 + mkt["mkt"] / 100).cumprod()
mkt["m_ma10"] = mkt["m_idx"].rolling(10).mean()
mcols = ["m_idx", "m_ma10", "mb20"]
df = df.drop(columns=[c for c in mcols if c in df.columns]).merge(
    mkt[mcols], left_on="trade_date", right_index=True, how="left")
df["rank_gap"] = df["gap"]

steps = {
    "1_regime": (df.m_idx > df.m_ma10) & (df.mb20 >= 0.30),
    "2_trend": (df.close > df.ma20) & (df.ma20 > df.ma60) & (df.ret20 > 0.03),
    "3_pullback": df.pct_chg.between(-6, -1.5),
    "4_weak_close": df.close_pos < 0.40,
    "5_liquidity": (df.amount >= 1e8) & (df.circ_mcap_yi.between(50, 2000)),
    "6_turnover": df.turnover_rate.between(1.5, 8),
    "7_gap": df.gap.between(-0.04, -0.01),
    "8_tradable": u,
}
mask = pd.Series(True, index=df.index)
for k, v in steps.items():
    if k == "7_gap":
        continue
    mask &= v

# 可执行口径：T 日收盘只发布「收盘最弱」的 30 只观察池；
# T+1 开盘在观察池内按缺口 -4%~-1% 过滤，缺口最浅的 5 只买入。
d = df[mask].dropna(subset=["r_oc2"]).sort_values(["trade_date", "close_pos"])
watch = d.groupby("trade_date").head(30)
picks = (watch[watch.gap.between(-0.04, -0.01)]
         .sort_values(["trade_date", "gap"])
         .groupby("trade_date").head(TOP_N).copy())
daily = picks.groupby("trade_date")["r_oc2"].mean() - COST
bench = df[u].dropna(subset=["r_oc2"]).groupby("trade_date")["r_oc2"].mean() - COST

eq = (1 + daily).cumprod()
dd = eq / eq.cummax() - 1
years = (daily.index.max() - daily.index.min()).days / 365.25
excess = (daily - bench.reindex(daily.index)).dropna()
beq = (1 + bench.reindex(daily.index)).cumprod()

stats = {
    "period": [str(daily.index.min().date()), str(daily.index.max().date())],
    "trading_days": int(len(daily)),
    "trades": int(len(picks)),
    "trades_per_day": round(len(picks) / len(daily), 2),
    "avg_trade_ret": round(picks.r_oc2.mean() - COST, 4),
    "win_rate": round((picks.r_oc2 > COST).mean(), 4),
    "total_return": round(eq.iloc[-1] - 1, 4),
    "annualized": round(eq.iloc[-1] ** (1 / years) - 1, 4),
    "max_drawdown": round(dd.min(), 4),
    "sharpe": round(daily.mean() / daily.std() * np.sqrt(252), 2),
    "daily_mean": round(daily.mean(), 4),
    "excess_mean": round(excess.mean(), 4),
    "excess_t": round(excess.mean() / (excess.std() / np.sqrt(len(excess))), 2),
    "benchmark_total": round(beq.iloc[-1] - 1, 4),
    "benchmark_maxdd": round((beq / beq.cummax() - 1).min(), 4),
    "cost": COST,
}
print(json.dumps(stats, ensure_ascii=False, indent=2))

print("\n== step funnel ==")
m = pd.Series(True, index=df.index)
for k, v in steps.items():
    m &= v
    print(k, "rows", int(m.sum()), "days", df.loc[m, "trade_date"].nunique())

print("\n== yearly ==")
yr = pd.DataFrame({
    "days": daily.groupby(daily.index.year).size(),
    "return": (1 + daily).groupby(daily.index.year).prod() - 1,
    "win_day": daily.groupby(daily.index.year).apply(lambda s: (s > 0).mean()),
    "bench": (1 + bench.reindex(daily.index)).groupby(daily.index.year).prod() - 1,
}).round(3)
print(yr.to_string())

print("\n== monthly ==")
mon = (1 + daily).groupby([daily.index.year, daily.index.month]).prod() - 1
print(mon.round(3).to_string())

print("\n== train / validation ==")
TH = pd.Timestamp("2025-10-01")
for label, part in [("train 2024-12~2025-09", daily[daily.index < TH]),
                    ("valid 2025-10~2026-05", daily[daily.index >= TH])]:
    e = (1 + part).cumprod()
    sub = picks[picks.trade_date < TH] if "train" in label else picks[picks.trade_date >= TH]
    print(label, {"days": len(part), "trades": len(sub),
                  "avg": round(sub.r_oc2.mean() - COST, 4),
                  "total": round(e.iloc[-1] - 1, 4),
                  "dd": round((e / e.cummax() - 1).min(), 4),
                  "sharpe": round(part.mean() / part.std() * np.sqrt(252), 2)})

print("\n== cost sensitivity ==")
for c in (0.0015, 0.0025, 0.004):
    d2 = picks.groupby("trade_date")["r_oc2"].mean() - c
    e = (1 + d2).cumprod()
    print(f"cost={c:.2%} total={e.iloc[-1]-1:+.1%} ann={e.iloc[-1]**(1/years)-1:+.1%} dd={(e/e.cummax()-1).min():+.1%}")

os.makedirs("docs/overnight_v2", exist_ok=True)
out = {
    "stats": stats,
    "steps": {k: {"rows": int(v.sum()), "days": int(df.loc[v, "trade_date"].nunique())} for k, v in steps.items()},
    "yearly": [{"year": int(i), **{c: float(yr.loc[i, c]) for c in yr.columns}} for i in yr.index],
    "monthly": [{"ym": f"{y}-{m:02d}", "ret": round(float(v), 4)} for (y, m), v in mon.items()],
    "equity": [{"date": str(k.date()), "equity": round(float(v), 4), "drawdown": round(float(dd.loc[k]), 4)}
               for k, v in eq.items()],
    "note": "signal T close; buy T+1 open; sell T+2 close; 0.25% round-trip cost; top5 equal weight",
}
with open("docs/overnight_v2/backtest.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
picks_out = picks[["trade_date", "ts_code", "close", "pct_chg", "close_pos", "turnover_rate",
                   "circ_mcap_yi", "ret20", "gap", "r_oc2", "f_open1", "f_close2"]].copy()
picks_out["trade_date"] = picks_out["trade_date"].dt.strftime("%Y-%m-%d")
picks_out.to_csv("docs/overnight_v2/trades.csv", index=False, encoding="utf-8-sig")
print("\nsaved docs/overnight_v2/backtest.json, trades.csv")
