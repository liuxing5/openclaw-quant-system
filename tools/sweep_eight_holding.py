"""八步 v3.1 持有期扫描：验证「隔夜隔日卖」改为「多日持有吃日内动量」。

诊断文档实验1：隔夜(T收->T+1开) -0.01%(t=-4.0)，日内 +0.19%(t=+73.4)；
T+1开->T+2收 +0.39%。当前策略 T日14:40买、T+1早盘卖，只吃到隔夜负段。
A 股 T+1 限制下，可行改造是「T日尾盘买 -> T+N 日卖」（N>=1），
把持有期拉长到 2/3/5/10 日，吃后续日内正收益。

用 panel_cache.parquet 的前复权前视列（f_close1/2/3/5/10/20）计算。
输出 docs/overnight_v2/holding_sweep.json
"""
import json

import numpy as np
import pandas as pd

OUT = "docs/overnight_v2"
HOLD_COLS = {1: "f_close1", 2: "f_close2", 3: "f_close3", 5: "f_close5",
             10: "f_close10", 20: "f_close20"}
COST = 0.0025


def stats(daily, label):
    if daily.empty:
        return {"label": label, "days": 0}
    eq = (1 + daily).cumprod()
    dd = eq / eq.cummax() - 1
    years = max((daily.index.max() - daily.index.min()).days / 365.25, 0.1)
    t = daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily))) if len(daily) > 2 else np.nan
    rng = np.random.default_rng(42)
    boots = [daily.sample(len(daily), replace=True, random_state=int(rng.integers(1e9))).mean()
             for _ in range(1000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {
        "label": label, "days": int(len(daily)),
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
    panel = pd.read_parquet("data/research/panel_cache.parquet")
    cols = ["ts_code", "trade_date", "close", "pct_chg"] + list(HOLD_COLS.values())
    panel = panel[cols].copy()
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    m = picks.merge(panel, left_on=["ts_code", "trade_date"],
                    right_on=["ts_code", "trade_date"], how="inner")
    print("matched", len(m), "of", len(picks))
    # 入场：信号日 T 的 14:40-14:50 尾盘价（用收盘近似），复权口径用 close
    out = {}
    for tier in ["A", "B", "AB"]:
        sub = m[m["tier"].isin(["A", "B"] if tier == "AB" else [tier])].copy()
        for n, col in HOLD_COLS.items():
            sub[f"ret{n}"] = sub[col] / sub["close"] - 1 - COST
            valid = sub[col].notna() & (sub[col] > 0)
            daily = sub[valid].groupby("trade_date")[f"ret{n}"].mean().sort_index()
            key = f"{tier}_hold{n}"
            out[key] = stats(daily, f"{tier} 级 · 持有{n}日")
    # top1 各持有期
    t1 = m[m["rank"] == 1].copy()
    for n, col in HOLD_COLS.items():
        t1[f"ret{n}"] = t1[col] / t1["close"] - 1 - COST
        valid = t1[col].notna() & (t1[col] > 0)
        daily = t1[valid].groupby("trade_date")[f"ret{n}"].mean().sort_index()
        out[f"top1_hold{n}"] = stats(daily, f"top1 · 持有{n}日")
    for k, v in out.items():
        print(f"{k:18s} d={v.get('days',0):3d} win={v.get('win_day',0):.1%} "
              f"avg={v.get('avg_day',0):+.2%} ann={v.get('ann',0):+.1%} "
              f"dd={v.get('dd',0):+.1%} t={v.get('t')} P>0={v.get('boot_pos',0):.1%}")
    with open(f"{OUT}/holding_sweep.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
