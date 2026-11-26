"""八步 v3.1 出场时点扫描：验证「次日早盘卖」改为「次日尾盘卖」能否提高胜率/收益。

背景：docs/策略诊断与隔夜八步法v2_2026-11-21.md 实验1 指出
  隔夜(T收->T+1开) 均值 -0.01% (t=-4.0)；次日日内(T+1开->T+1收) +0.19% (t=+73.4)。
A 股 T+1 不能当日回转，故买入仍在 T 日 14:40-14:50，只把卖点从 T+1 09:30/10:00
推迟到 T+1 盘中/尾盘，理论上能多吃次日日内正收益。

口径：5分钟 bar（data/research/morning_exit_bars.pkl），成本 0.25% 往返。
输出：docs/overnight_v2/exit_timing_sweep.json
"""
import json
import pickle

import numpy as np
import pandas as pd

CACHE = "data/research/morning_exit_bars.pkl"
OUT = "docs/overnight_v2"
COST = 0.0025
# 候选卖点（T+1 北京时间 24h，HHMM）
EXITS = {
    "0930": "09:30 开盘",
    "1000": "10:00（现状主卖点）",
    "1130": "11:30 午盘",
    "1400": "14:00 下午",
    "1430": "14:30（原扫描时点）",
    "1500": "15:00 收盘（次日尾盘）",
}


def _bar_map(bars, day):
    return {b["time"]: b for b in bars if b["date"] == day}


def load_signals():
    """复用 v3.1 的严格 A 级样本构造逻辑，拿到信号日/代码/次日。"""
    import sys
    sys.path.insert(0, "tools")
    from backtest_v31_final import load_a_sample
    a = load_a_sample()
    return a[["date", "code"]].drop_duplicates()


def stats(d, label):
    daily = d.groupby("date").ret.mean()
    eq = (1 + daily).cumprod()
    dd = eq / eq.cummax() - 1
    years = max((daily.index.max() - daily.index.min()).days / 365.25, 0.1)
    t = d.ret.mean() / (d.ret.std(ddof=1) / np.sqrt(len(d))) if len(d) > 2 else np.nan
    rng = np.random.default_rng(42)
    boots = [d.ret.sample(len(d), replace=True, random_state=int(rng.integers(1e9))).mean()
             for _ in range(1000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {
        "label": label, "n": int(len(d)), "days": int(len(daily)),
        "win": round(float((d.ret > 0).mean()), 4), "avg": round(float(d.ret.mean()), 4),
        "ann": round(float(eq.iloc[-1] ** (1 / years) - 1), 4),
        "dd": round(float(dd.min()), 4),
        "t": round(float(t), 2) if t == t else None,
        "boot_lo": round(float(lo), 4), "boot_hi": round(float(hi), 4),
        "boot_pos": round(float(np.mean(np.array(boots) > 0)), 3),
    }


def main():
    sig = load_signals()
    cache = pickle.loads(open(CACHE, "rb").read())
    panel = pd.read_parquet("data/research/panel_cache.parquet").set_index(["ts_code", "trade_date"])
    rows = []
    for r in sig.itertuples():
        d0 = str(r.date.date())
        d1 = str(pd.Timestamp(r.date) + pd.Timedelta(days=1))[:10]
        bars = cache.get((r.code, d0, d1), [])
        if not bars:
            continue
        b0 = _bar_map(bars, d0)
        b1 = _bar_map(bars, d1)
        if not b0 or not b1:
            continue
        # 买入：T 日 14:40~14:50 窗口的最后一笔收盘（近似 14:50 成交）
        entry_bars = [b0[t] for t in ("1440", "1445", "1450") if t in b0]
        if not entry_bars:
            continue
        entry = entry_bars[-1]["close"]
        if not entry or entry != entry or entry <= 0:
            continue
        # 复权因子：bar 与 panel 日线的比例（同一 T 日收盘对齐）
        try:
            pr = panel.loc[(r.code, pd.Timestamp(d0))]
            factor = float(pr["close"]) / b0["1500"]["close"] if b0.get("1500") else 1.0
        except KeyError:
            factor = 1.0
        if not factor or factor != factor:
            factor = 1.0
        adj_entry = entry * factor
        rec = {"date": r.date, "code": r.code}
        for hhmm in EXITS:
            rec[hhmm] = np.nan
        for hhmm, _ in EXITS.items():
            # bar 时间可能落在 0935 等网格上，取 <= 目标时刻的最后一笔
            candidates = [b for t, b in b1.items() if t <= hhmm and b.get("close")]
            if candidates:
                b = max(candidates, key=lambda x: x["time"])
                rec[hhmm] = b["close"] * factor / adj_entry - 1 - COST
        rows.append(rec)
    df = pd.DataFrame(rows)
    if df.empty:
        print("no usable signals")
        return
    print(f"usable signals {len(df)} / {len(sig)}")
    out = {}
    for hhmm, name in EXITS.items():
        sub = df[["date", "code", hhmm]].dropna().rename(columns={hhmm: "ret"})
        if sub.empty:
            continue
        s = stats(sub, name)
        out[hhmm] = s
        print(f"{name:24s} n={s['n']:4d} win={s['win']:.1%} avg={s['avg']:+.2%} "
              f"ann={s['ann']:+.1%} dd={s['dd']:+.1%} t={s['t']} P>0={s['boot_pos']:.1%}")
    with open(f"{OUT}/exit_timing_sweep.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
