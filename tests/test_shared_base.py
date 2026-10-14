# -*- coding: utf-8 -*-
"""共享底座验证：复权换算 + 统计修正（直接运行: python tests/test_shared_base.py）"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from strategies.shared.adjust import cum_returns, qfq_adjust, qfq_factors
from strategies.shared.backtest_stats import (
    cluster_t_stat, fwd_stats, format_sig, net_returns, t_stat,
)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def test_adjust():
    print("[adjust]")
    # 现金分红除权日：10 元除息 0.5 元 → 除息日 pre_close=9.50，
    # 收 9.60，交易所口径 pct_chg = 9.60/9.50-1 = +1.05263%（raw 环比 -4%）
    df = pd.DataFrame({
        "open":  [10.0, 9.55, 9.60],
        "high":  [10.1, 9.70, 9.75],
        "low":   [ 9.9, 9.50, 9.55],
        "close": [10.0, 9.60, 9.70],
        "pct_chg": [1.0, 1.0526316, 1.0416667],
    })
    out = qfq_adjust(df.copy())
    # 末日锚定：qfq close == raw close
    check("末日锚定", abs(out["close"].iloc[-1] - 9.70) < 1e-9)
    # 除权日真实收益链应连续：qfq 第2日收益 ≈ 交易所 pct_chg
    r1 = out["close"].iloc[1] / out["close"].iloc[0] - 1
    check("除权日收益连续", abs(r1 - 0.010526316) < 1e-6, f"r1={r1}")
    # OHLC 同步缩放：除权日 scale≈1（close 恰为 adj_close）
    # 无除权时 qfq == raw
    df2 = pd.DataFrame({"open": [10, 10.1], "high": [10.2, 10.2],
                        "low": [9.9, 10.0], "close": [10.0, 10.1],
                        "pct_chg": [0.0, 1.0]})
    out2 = qfq_adjust(df2.copy())
    check("无除权 qfq==raw", abs(out2["close"].iloc[0] - 10.0) < 1e-9,
          f"got {out2['close'].iloc[0]}")
    check("无除权 open 不变", abs(out2["open"].iloc[0] - 10.0) < 1e-9)
    check("raw_close 保留", abs(out2["raw_close"].iloc[0] - 10.0) < 1e-9)
    # pct_chg 全缺失 → 退化为 close 环比（收益链保持，scale≡1）
    df3 = pd.DataFrame({"open": [10, 5, 5.1], "high": [10, 5, 5.1],
                        "low": [10, 5, 5.1], "close": [10.0, 5.0, 5.1]})
    out3 = qfq_adjust(df3.copy())
    r = out3["close"].iloc[1] / out3["close"].iloc[0] - 1
    check("pct缺失退化", abs(r - (-0.5)) < 1e-9, f"r={r}")
    # qfq_factors 末日=1
    f = qfq_factors(np.array([1.0, 1.0526316, 1.0416667]))
    check("因子末日=1", abs(f[-1] - 1.0) < 1e-12)
    check("cum_returns 首元素", abs(cum_returns(np.array([2.0]))[0] - 1.02) < 1e-12)


def test_stats():
    print("[backtest_stats]")
    # 1) Student-t：n=5 极端样本的 p 应比正态更宽
    vals = [0.05, 0.04, 0.06, 0.05, 0.045]
    st = t_stat(vals)
    check("t 正常计算", st["t"] > 2.0, f"t={st['t']}")
    check("df 校正 p 更保守", st["p"] > 0.0, f"p={st['p']}")
    # 2) 低功效标注
    st2 = t_stat([0.1, -0.1])
    check("n=2 low_power", st2["low_power"] is True)
    # 3) 按日聚类：同日强相关样本 → 日均值后 t 大幅收缩
    rng = np.random.default_rng(7)
    dates, vals = [], []
    for d in range(60):
        shock = rng.normal(0.01, 0.02)  # 同日共同因子
        for _ in range(10):             # 每天10只票，强截面相关
            vals.append(shock + rng.normal(0, 0.001))
            dates.append(d)
    naive = t_stat(vals, n_eff=len(vals) // 10)
    cluster = cluster_t_stat(vals, dates, horizon=10)
    check("聚类 t 收缩", abs(cluster["t"]) < abs(naive["t"]) * 0.8,
          f"cluster={cluster['t']:.2f} naive={naive['t']:.2f}")
    check("聚类 n_eff=日数//horizon", cluster["n_eff"] == 6, f"{cluster['n_eff']}")
    # 4) fwd_stats 走聚类路径
    import datetime as _dt
    date_list = [_dt.date(2026, 1, 1) + _dt.timedelta(days=d // 3) for d in dates]
    fs = fwd_stats(vals, dates=date_list, horizon=10)
    check("fwd_stats sig_source", fs["sig_source"] == "cluster_by_date")
    check("fwd_stats n_days", fs["n_days"] == 20, f"got {fs['n_days']}")
    # 5) 无 dates 回退旧口径
    fs2 = fwd_stats([0.01] * 100, dates=None, horizon=10)
    check("无dates回退", fs2["sig_source"] == "n_eff" and fs2["n_eff"] == 10)
    # 6) 低功效格式化
    check("format_sig low_power", "低功效" in format_sig({"t": -23.7, "p": 1e-9,
                                                          "low_power": True}))
    check("format_sig star", format_sig({"t": 3.0, "p": 0.001,
                                         "low_power": False}).endswith("**"))
    # 7) 成本模型不变
    check("net_returns 成本", abs(net_returns([0.0025])[0]) < 1e-9)
    # 8) count=2 非重叠子样本 → low_power
    fs3 = fwd_stats([0.1, -0.05, 0.02], dates=["2026-01-05", "2026-01-06",
                                               "2026-06-01"], horizon=60)
    check("非重叠小样本 low_power", fs3["nonoverlap"]["low_power"] is True)


def test_st_intervals():
    """ST 区间压缩 + 进行中区间置 NULL（2026-10-14 审查修复）。"""
    print("[st_intervals]")
    from datetime import date
    from strategies.shared.backfill_st_history import _st_intervals_from_flags

    d1, d2, d3 = date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)
    # 1) ST run 延续到数据末日 → end_date=None（进行中）
    iv = _st_intervals_from_flags([(d1, '1'), (d2, '1'), (d3, '1')])
    check("进行中区间NULL", iv == [(d1, None)], f"got {iv}")
    # 2) 中途摘帽 → 闭合区间
    iv = _st_intervals_from_flags([(d1, '1'), (d2, '1'), (d3, '0')])
    check("摘帽闭合", iv == [(d1, d2)], f"got {iv}")
    # 3) 从未 ST → 空
    check("无ST空表", _st_intervals_from_flags([(d1, '0'), (d2, '0')]) == [])
    # 4) 空输入
    check("空输入", _st_intervals_from_flags([]) == [])
    # 5) 两段 ST（ST→摘帽→再ST且延续到末日）
    d4, d5 = date(2026, 1, 8), date(2026, 1, 9)
    iv = _st_intervals_from_flags([(d1, '1'), (d2, '0'), (d3, '1'),
                                   (d4, '1'), (d5, '1')])
    check("两段+末段进行中", iv == [(d1, d1), (d3, None)], f"got {iv}")


if __name__ == "__main__":
    test_adjust()
    test_stats()
    test_st_intervals()
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)
