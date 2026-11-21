"""尾盘买入 (T close) / 次日早盘卖出 的选股条件研究。

目标：在 T 日收盘买入、T+1 开盘或早盘卖出的约束下，寻找正期望的候选条件。
输出：各条件下 overnight / 次日日内 / 次日最高触及 的统计。
"""
import numpy as np
import pandas as pd

from strategy_lab import load, universe

df = load()
u = universe(df)
df["overnight"] = df["r_overnight"]
df["next_intraday"] = df["r_next_intraday"]
df["next_high_gain"] = df["f_high1"] / df["f_open1"] - 1   # T+1 high / T+1 open - 1
df["next_low_loss"] = df["f_low1"] / df["f_open1"] - 1

base = (df.amount >= 1e8) & df.circ_mcap_yi.between(50, 2000) & u


def stat(mask, label):
    d = df[mask].dropna(subset=["overnight"])
    if len(d) < 30:
        print(f"{label}: n={len(d)} 样本不足")
        return
    def s(col):
        x = d[col].dropna()
        return f"{x.mean()*100:+.3f}% (t={x.mean()/(x.std()/np.sqrt(len(x))):+.1f}, 胜率{(x>0).mean()*100:.1f}%)"
    print(f"{label:38s} n={len(d):6d} days={d.trade_date.nunique():3d} | 隔夜 {s('overnight')} | 次日日内 {s('next_intraday')} | 次日最高 {s('next_high_gain')}")


print("== 按 T 日涨跌幅分桶（全市场，T 收盘买）==")
df["pctb"] = pd.cut(df.pct_chg, [-21, -9, -6, -4, -2, 0, 2, 4, 7, 9.7, 21])
for b, g in df[base].groupby("pctb", observed=True):
    x = g.overnight.dropna()
    print(f"{str(b):16s} n={len(x):6d} 隔夜{x.mean()*100:+.3f}% 胜率{(x>0).mean()*100:.1f}%")

print("\n== 按 T 日收盘位置分桶 ==")
df["posb"] = pd.cut(df.close_pos, [0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0])
for b, g in df[base].groupby("posb", observed=True):
    x = g.overnight.dropna()
    print(f"{str(b):16s} n={len(x):6d} 隔夜{x.mean()*100:+.3f}% 胜率{(x>0).mean()*100:.1f}%")

print("\n== 候选条件 ==")
cond = {
    "旧八步法(涨3-9.7/换手5-10)": ((df.pct_chg.between(3, 9.7)) & (df.turnover_rate.between(5, 10))),
    "旧八步法+均线": ((df.pct_chg.between(3, 9.7)) & (df.turnover_rate.between(5, 10)) & (df.close > df.ma5) & (df.ma5 > df.ma10)),
    "恐慌回踩(-6~-1.5,收盘<0.4,MA20上)": (df.pct_chg.between(-6, -1.5) & (df.close_pos < 0.4) & (df.close > df.ma20) & (df.ma20 > df.ma60) & (df.ret20 > 0.03)),
    "恐慌回踩+换手1.5-8": (df.pct_chg.between(-6, -1.5) & (df.close_pos < 0.4) & (df.close > df.ma20) & (df.ma20 > df.ma60) & (df.ret20 > 0.03) & df.turnover_rate.between(1.5, 8)),
    "深度恐慌(-9~-4,收盘<0.3)": (df.pct_chg.between(-9, -4) & (df.close_pos < 0.3)),
    "强势收盘(涨3-7,收盘>0.8)": (df.pct_chg.between(3, 7) & (df.close_pos > 0.8)),
    "涨停/接近涨停(>=9.5)": (df.pct_chg >= 9.5),
    "低位反弹(20日跌>10%,收盘<0.3)": ((df.ret20 < -0.10) & (df.close_pos < 0.3)),
    "低换手温和上涨(0-2%,换手<3%)": (df.pct_chg.between(0, 2) & (df.turnover_rate < 3)),
}
for label, c in cond.items():
    stat(base & c, label)

print("\n== 恐慌回踩：按次日开盘缺口分（缺口已知于 09:25，可用于是否卖出）==")
panic = cond["恐慌回踩(-6~-1.5,收盘<0.4,MA20上)"]
d = df[base & panic].dropna(subset=["overnight", "next_intraday"])
d["gapb"] = pd.cut(d.overnight, [-0.2, -0.02, -0.01, 0, 0.01, 0.02, 0.2])
print(d.groupby("gapb", observed=True).agg(n=("next_intraday", "size"),
                                           隔夜=("overnight", "mean"),
                                           次日日内=("next_intraday", "mean"),
                                           次日最高=("next_high_gain", "mean")).round(4).to_string())
