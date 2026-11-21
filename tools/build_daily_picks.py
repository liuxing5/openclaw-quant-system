"""构建「每日一笔」候选：用日线近似 14:30 特征，按分数排序取每日第一。

注意：14:30 的真实分时不可得时，用 T 日收盘/日内位置做近似（有轻微前视），
实际成交与收益稍后用 5 分钟线复核。
"""
import numpy as np
import pandas as pd

from strategy_lab import load

df = load()
df = df[(df.pct_chg.abs() < 21) & (df.amount >= 1e8) & df.circ_mcap_yi.between(50, 2000)].copy()
df = df.sort_values(["ts_code", "trade_date"])
g = df.groupby("ts_code", sort=False)
for c in ["close", "ma20", "ma60", "ret20", "ret5", "amount_ma20"]:
    df[f"prev_{c}"] = g[c].shift(1)
df["prev_lu20"] = g["is_limit_up"].transform(lambda s: s.shift(1).rolling(20).sum())
df["pct"] = df["pct_chg"]
df["pos"] = df["close_pos"]
df["amount_ratio"] = df["amount"] / df["prev_amount_ma20"]
df["vwap_proxy"] = (df["high"] + df["low"] + df["close"]) / 3
df["vwap_pos_proxy"] = df["close"] / df["vwap_proxy"] - 1

base = (
    (df.prev_close > df.prev_ma20) & (df.prev_ma20 > df.prev_ma60) & (df.prev_ret20 > 0.03)
    & (df.pos < 0.20) & df.pct.between(-7, -2) & df.turnover_rate.between(1.5, 8)
    & (df.prev_lu20 <= 1) & (df.prev_ret5 > -0.02) & (df.amount_ratio >= 0.8)
    & (df.b20 <= 0.70)
)
df["tier"] = np.where(base, "A", "B")
df["depth"] = (( -2 - df.pct) / 5).clip(0, 1)
df["pos_score"] = (1 - df.pos).clip(0, 1)
df["turn_score"] = 1 - ((df.turnover_rate - 1.5) / 6.5).clip(0, 1)
df["score"] = (18 * df.pos_score + 12 * df.depth + 7 * df.turn_score).round(2)

# A 池不足时用 B 池（放宽回踩/位置）补足；仍无则当日空仓
relaxed = (
    (df.prev_close > df.prev_ma20) & (df.prev_ma20 > df.prev_ma60) & (df.prev_ret20 > 0.03)
    & (df.pos < 0.30) & df.pct.between(-9, -1.5) & df.turnover_rate.between(1.0, 10)
    & (df.prev_lu20 <= 1) & (df.prev_ret5 > -0.03) & (df.amount_ratio >= 0.6)
    & (df.b20 <= 0.75)
)
df["relaxed"] = relaxed

out = []
for d, gd in df[(df.tier == "A") | df.relaxed].groupby("trade_date"):
    a = gd[gd.tier == "A"].sort_values("score", ascending=False)
    pool = a if len(a) else gd.sort_values("score", ascending=False)
    top = pool.head(3).copy()
    for i, r in enumerate(top.itertuples()):
        out.append({"trade_date": d, "rank": i + 1, "ts_code": r.ts_code, "tier": r.tier,
                    "score": r.score, "pct": r.pct, "pos": r.pos, "turn": r.turnover_rate,
                    "vwap_proxy": r.vwap_pos_proxy, "lu20": r.prev_lu20, "ret5": r.prev_ret5,
                    "amt_ratio": r.amount_ratio, "b20": r.b20})
d = pd.DataFrame(out)
d.to_parquet("data/research/daily_picks.parquet", index=False)
d1 = d[d["rank"] == 1]
print("days", len(d1), "A", int((d1.tier == "A").sum()), "B", int((d1.tier == "B").sum()))
print("date range", d1.trade_date.min(), d1.trade_date.max())
print(d1.head(8)[["trade_date", "ts_code", "tier", "score", "pct", "pos", "lu20", "ret5"]].to_string(index=False))
