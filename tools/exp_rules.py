"""Compare candidate rule sets under legal T+1 execution (buy T+1 open, sell T+2 close)."""
import numpy as np
import pandas as pd

from strategy_lab import load, universe, simulate, yearly, bench, COST

df = load()
df["rank_gap"] = df["gap"]
df["rank_bias_ma10"] = (df["close"] / df["ma10"] - 1).abs()
df["rank_neg_ret5"] = -df["ret5"]
df["rank_turn"] = df["turnover_rate"]
u = universe(df)
ret = "r_oc2"

print("panel", df.shape, df.trade_date.min().date(), df.trade_date.max().date())
print("universe rows", int(u.sum()), "days", df.loc[u, "trade_date"].nunique())

b_ref = df[u]
b_daily = bench(b_ref, ret)
print("\n== BENCHMARK equal-weight, buy T+1 open sell T+2 close, cost 0.15% ==")
from strategy_lab import metrics
print(metrics(b_daily))
print(yearly(b_daily).to_string())

rules = {}

# 1) current 8-step semantics
rules["1_as_is_8step"] = (
    ((((df.pct_chg >= 3) & (df.pct_chg <= 6)) | ((df.pct_chg >= 6) & (df.pct_chg <= 9.7)))
     & (df.amount >= 0.5e8) & (df.amount <= 50e8)
     & (df.turnover_rate >= 5) & (df.turnover_rate <= 10)
     & (df.close > df.ma5) & (df.ma5 > df.ma10)
     & (df.dist_high20 > -0.08))
    , "pct_chg"
)

# 2) as-is + only buy on flat/down gap
rules["2_as_is_gap_neg"] = (rules["1_as_is_8step"][0] & (df.gap <= 0.002), "pct_chg")

# 3) quality pullback in uptrend
rules["3_pullback_quality"] = (
    (df.amount >= 1e8) & (df.circ_mcap_yi.between(50, 1500))
    & (df.turnover_rate.between(1.5, 6))
    & (df.close > df.ma20) & (df.ma20 > df.ma60)
    & (df.ret20 > 0) & (df.ret5 < 0.01)
    & ((df.close / df.ma10 - 1).abs() < 0.04)
    & (df.close_pos > 0.35)
    & (df.gap <= 0.01) & (df.gap >= -0.03)
    , "rank_bias_ma10"
)

# 4) oversold bounce with breadth panic gate
rules["4_panic_bounce"] = (
    (df.b20 < 0.30) & (df.amount >= 1e8) & (df.circ_mcap_yi.between(30, 2000))
    & (df.pct_chg <= -2) & (df.pct_chg >= -9)
    & (df.gap <= 0) & (df.gap >= -0.05)
    & (df.close > df.ma60 * 0.9)
    , "rank_neg_ret5"
)

# 5) low attention, mild up
rules["5_low_attention"] = (
    (df.amount >= 0.5e8) & (df.circ_mcap_yi.between(30, 800))
    & (df.turnover_rate.between(0.5, 3)) & (df.vol_ratio < 1.2)
    & (df.pct_chg.between(-1, 3))
    & (df.close > df.ma20) & (df.ret20 > 0)
    & (df.gap.between(-0.02, 0.01))
    , "rank_turn"
)

# 6) gap-down reversal in strong uptrend
rules["6_gap_reversal_strong"] = (
    (df.amount >= 1e8) & (df.circ_mcap_yi.between(50, 2000))
    & (df.close > df.ma20) & (df.ma20 > df.ma60) & (df.ret20 > 0.03)
    & (df.pct_chg.between(-6, -1.5))
    & (df.gap.between(-0.04, -0.005))
    & (df.turnover_rate.between(1.5, 8))
    , "rank_gap"
)

# 7) morning dip within uptrend, no prior-day requirement
rules["7_dip_in_uptrend"] = (
    (df.amount >= 1e8) & (df.circ_mcap_yi.between(50, 2000))
    & (df.close > df.ma20) & (df.ret20 > 0.02)
    & (df.gap.between(-0.035, -0.008))
    & (df.turnover_rate.between(1, 8))
    & (df.close_pos > 0.3)
    , "rank_gap"
)

# 8) momentum, but only mild gains and low turnover, near MA5
rules["8_mild_momentum"] = (
    (df.amount >= 1e8) & (df.circ_mcap_yi.between(50, 2000))
    & (df.pct_chg.between(0.5, 4))
    & (df.turnover_rate.between(1.5, 6))
    & (df.close > df.ma5) & (df.ma5 > df.ma10) & (df.ma10 > df.ma20)
    & (df.bias_ma5 < 0.04)
    & (df.gap.between(-0.02, 0.01))
    , "rank_turn"
)

for name, (m, rank) in rules.items():
    m = m & u
    res, daily, picks = simulate(df, m, rank_col=rank, top_n=5, ret=ret)
    print(f"\n== {name} ==")
    print({k: res[k] for k in ("days", "trades", "trades_per_day", "avg_trade", "win_trade", "total", "ann", "maxdd", "sharpe", "t")})
    print(yearly(daily).to_string())

