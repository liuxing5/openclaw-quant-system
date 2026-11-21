# -*- coding: utf-8 -*-
"""隔夜八步法 v3.0（融合版）纯函数测试。

直接运行: python tests/test_overnight_v2.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from strategies.overnight_8step import v2_intraday as v3

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


def _feat(n=6):
    rows = []
    for i in range(n):
        rows.append({
            "ts_code": f"60000{i}.SH",
            "stock_name": f"测试{i}",
            "price": 9.8, "pre_close": 10.2, "pct": -3.9,
            "high": 10.3, "low": 9.7,
            "volume": 2e7, "amount": 2e8, "turnover": 3.0, "circ_mcap": 100e8,
            "ma20": 9.5, "ma60": 9.0, "ret20": 0.08, "ret5": 0.02,
            "lu20": 0, "amount_ma20": 2e8,
            "close_pos": 0.10, "vwap_pos": -0.01, "amount_ratio": 1.0,
            "is_st": False, "list_date": pd.Timestamp("2015-01-01"),
        })
    return pd.DataFrame(rows)


def test_regime():
    print("[market_regime]")
    dates = pd.bdate_range("2026-01-01", periods=15)
    up = pd.DataFrame({"trade_date": dates, "mean_pct": [0.5] * 15})
    down = pd.DataFrame({"trade_date": dates, "mean_pct": [-0.5] * 15})
    check("上涨+广度0.5 -> 放行", v3.market_regime(up, 0.5)["blocked"] is False)
    check("下跌 -> 拦截", v3.market_regime(down, 0.5)["blocked"] is True)
    check("广度<20% -> 拦截", v3.market_regime(up, 0.10)["blocked"] is True)
    check("广度>70% -> 拦截", v3.market_regime(up, 0.80)["blocked"] is True)


def test_features():
    print("[build_features]")
    daily = pd.DataFrame({"ts_code": ["600000.SH"], "ma20": [9.5], "ma60": [9.0], "ret20": [0.1],
                          "ret5": [0.02], "lu20": [0], "amount_ma20": [2e8],
                          "stock_name": ["测试"], "is_st": [False], "list_date": [pd.Timestamp("2015-01-01")]})
    snap = pd.DataFrame({"ts_code": ["600000.SH"], "price": [9.8], "pct": [-3.0],
                         "high": [10.3], "low": [9.3], "volume": [2e7], "amount": [2e8],
                         "turnover": [3.0], "circ_mcap": [100e8]})
    f = v3.build_features(daily, snap)
    check("close_pos=0.5", abs(f.close_pos.iloc[0] - 0.5) < 1e-9, f.close_pos.iloc[0])
    check("vwap 由 amount/volume 计算", abs(f.vwap.iloc[0] - 10.0) < 1e-9, f.vwap.iloc[0])
    check("vwap_pos=-2%", abs(f.vwap_pos.iloc[0] + 0.02) < 1e-9, f.vwap_pos.iloc[0])
    check("amount_ratio≈1.11", abs(f.amount_ratio.iloc[0] - 1.111) < 0.01, f.amount_ratio.iloc[0])


def test_select():
    print("[select]")
    regime = {"date": "2026-05-20", "risk_on": True, "blocked": False, "breadth": 0.4}
    base = _feat()
    out = v3.select(base, regime)
    check("全部满足 -> 6 只", len(out) == 6, len(out))
    check("评分 55~100", out.score.between(55, 100).all())
    check("按收盘位置升序", list(out.close_pos) == sorted(out.close_pos))

    def blocked(**kw):
        f = _feat()
        for k, v in kw.items():
            f[k] = v
        return len(v3.select(f, regime))

    check("跌破MA20 -> 0", blocked(price=9.0) == 0)
    check("趋势破坏 -> 0", blocked(ma20=8.9, ma60=9.0) == 0)
    check("动量不足 -> 0", blocked(ret20=0.01) == 0)
    check("5日趋势坏 -> 0", blocked(ret5=-0.05) == 0)
    check("近期涨停过多 -> 0", blocked(lu20=2) == 0)
    check("回踩过浅 -> 0", blocked(pct=-1.0) == 0)
    check("回踩过深 -> 0", blocked(pct=-8.0) == 0)
    check("收盘偏强 -> 0", blocked(close_pos=0.9) == 0)
    check("跌破VWAP超过2% -> 0", blocked(vwap_pos=-0.03) == 0)
    check("换手过低 -> 0", blocked(turnover=1.0) == 0)
    check("换手过高 -> 0", blocked(turnover=12.0) == 0)
    check("量能不足 -> 0", blocked(amount_ratio=0.5) == 0)
    check("成交额不足 -> 0", blocked(amount=5e7) == 0)
    check("市值超限 -> 0", blocked(circ_mcap=5000e8) == 0)
    check("ST -> 0", blocked(is_st=True) == 0)
    check("次新 -> 0", blocked(list_date=pd.Timestamp("2026-05-01")) == 0)

    many = _feat(40)
    many["close_pos"] = [i / 400 for i in range(40)]
    check("观察池上限 30", len(v3.select(many, regime)) == 30)


def test_exit():
    print("[exit_plan]")
    plan = v3.exit_plan(10.0)
    check("止损价 9.8", plan["stop_loss"] == 9.8, plan)
    check("浮盈阈值 +0.5%", plan["gap_up_exit"] == 0.005)
    check("浮亏阈值 -3%", plan["gap_down_exit"] == -0.03)
    check("离场窗口 09:30-10:00", "09:30-10:00" in plan["exit_window"])
    check("买入窗口 14:40-15:00", "14:40-15:00" in plan["entry_window"])


def test_steps():
    print("[step_stats]")
    stats = v3.step_stats(_feat(5), {"date": "2026-05-20"})
    check("全市场=5", stats["全市场"] == 5, stats)
    check("漏斗单调", stats["全市场"] >= stats["趋势结构"] >= stats["尾盘回踩"]
          >= stats["恐慌收盘"] >= stats["流动性"] >= stats["换手量能"], stats)


def main():
    test_regime()
    test_features()
    test_select()
    test_exit()
    test_steps()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
