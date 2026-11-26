"""八步 v3.1 选股方式扫描：top1 vs 当日全部通过者等权。

诊断文档 9.5 节方案 A 显示：43 笔随机 A 级样本等权 +1.12%/笔，
而 121 天实盘 top1 口径 -0.52%/笔。本脚本用 daily_picks.parquet 的每日
全候选 + daily_pick_trades.csv 的实际成交收益，验证在不同筛选口径下
「买top1」与「全通过者等权」的胜率/收益差异，并扫描 lu20/ret5/b20 过滤。

输出 docs/overnight_v2/selection_sweep.json
"""
import json

import numpy as np
import pandas as pd

OUT = "docs/overnight_v2"


def stats(daily, label):
    if daily.empty:
        return {"label": label, "n": 0}
    eq = (1 + daily).cumprod()
    dd = eq / eq.cummax() - 1
    years = max((daily.index.max() - daily.index.min()).days / 365.25, 0.1)
    t = daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily))) if len(daily) > 2 else np.nan
    rng = np.random.default_rng(42)
    boots = [daily.sample(len(daily), replace=True, random_state=int(rng.integers(1e9))).mean()
             for _ in range(1000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {
        "label": label, "days": int(len(daily)), "n": int(len(daily)),
        "win_day": round(float((daily > 0).mean()), 4),
        "avg_day": round(float(daily.mean()), 4),
        "ann": round(float(eq.iloc[-1] ** (1 / years) - 1), 4),
        "dd": round(float(dd.min()), 4),
        "t": round(float(t), 2) if t == t else None,
        "boot_lo": round(float(lo), 4), "boot_hi": round(float(hi), 4),
        "boot_pos": round(float(np.mean(np.array(boots) > 0)), 3),
    }


def main():
    picks = pd.read_parquet("data/research/daily_picks.parquet")
    picks = picks[picks.trade_date >= "2025-01-01"].copy()
    trades = pd.read_csv(f"{OUT}/daily_pick_trades.csv")
    trades["date"] = pd.to_datetime(trades.date)
    picks["date"] = pd.to_datetime(picks.trade_date)
    # 用 (date, code) 关联实际成交净收益
    m = picks.merge(trades[["date", "code", "net", "why"]],
                    left_on=["date", "ts_code"], right_on=["date", "code"], how="inner")
    print("matched", len(m), "of", len(picks))
    out = {}
    # 基线：每天 rank=1
    top1 = m[m["rank"] == 1].groupby("date").net.mean().sort_index()
    out["top1"] = stats(top1, "每日 rank1（现状）")
    # 方案A：当日全部 A 级等权
    a = m[m["tier"] == "A"].groupby("date").net.mean().sort_index()
    out["all_A"] = stats(a, "当日全部 A 级等权（方案A）")
    # 变体：A级 + lu20<=1（不追涨停）
    for name, sub in [
        ("A_lu20le1", (m["tier"] == "A") & (m["lu20"] <= 1)),
        ("A_lu20le1_b20le70", (m["tier"] == "A") & (m["lu20"] <= 1) & (m["b20"] <= 0.70)),
        ("A_lu20le1_ret5pos", (m["tier"] == "A") & (m["lu20"] <= 1) & (m["ret5"] > 0)),
        ("A_strict_all", (m["tier"] == "A") & (m["lu20"] <= 1) & (m["ret5"] > -0.02)
         & (m["b20"] <= 0.70) & (m["amt_ratio"] >= 0.8)),
    ]:
        d = m[sub].groupby("date").net.mean().sort_index()
        out[name] = stats(d, name)
    # 只买分数最高但换过滤：top1 且 lu20<=1
    t1f = m[(m["rank"] == 1) & (m["lu20"] <= 1)].groupby("date").net.mean().sort_index()
    out["top1_lu20le1"] = stats(t1f, "top1 且不追涨停")
    for k, v in out.items():
        print(f"{k:20s} days={v.get('days',0):3d} win={v.get('win_day',0):.1%} "
              f"avg={v.get('avg_day',0):+.2%} ann={v.get('ann',0):+.1%} "
              f"dd={v.get('dd',0):+.1%} t={v.get('t')} P>0={v.get('boot_pos',0):.1%}")
    with open(f"{OUT}/selection_sweep.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
