# -*- coding: utf-8 -*-
"""市场状态门控验证（直接运行 python tests/test_strategy_gates.py）

覆盖 2026-10-14 收益改造引入的三类门控：
  1. pullback: apply_breadth_gate 行为（阈值过滤 / 关闭开关 / 空信号）
  2. 各策略门控阈值配置留痕（reward_risk / min_market_breadth / max_breadth_ma20
     / 8step MIN_MARKET_BREADTH）——防静默回改
  3. main_uptrend data_loader.get_market_breadth 数据库口径（0..1 合法值）
以及 2026-10-19 出场改造:
  4. pullback 时间止损 V3b 行为（触发 / 盈利不触发 / 同日止损优先 / 关闭回退）
"""
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

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


def _mk_quotes():
    """构造 4 只股票 × 25 日行情：3 只线性上行(站上MA20)，1 只线性下行(跌破)；
    末日 2 只上行股单日暴跌至 MA20 下方 → 末日宽度 = 1/4 = 0.25。"""
    dates = pd.bdate_range("2025-06-02", periods=25)
    rows = []
    for i, d in enumerate(dates):
        closes = {
            "000001.SZ": 10.0 + 0.1 * i,           # 上行，末日暴跌
            "000002.SZ": 20.0 + 0.2 * i,           # 上行，末日暴跌
            "000003.SZ": 30.0 + 0.3 * i,           # 上行，末日不跌
            "000004.SZ": 40.0 - 0.4 * i,           # 一路下行
        }
        if i == len(dates) - 1:                    # 末日：001/002 暴跌 25%
            closes["000001.SZ"] *= 0.75
            closes["000002.SZ"] *= 0.75
        for code, c in closes.items():
            rows.append({"ts_code": code, "trade_date": d, "close": c})
    return pd.DataFrame(rows), dates


def test_pullback_breadth_gate():
    print("[pullback apply_breadth_gate]")
    from strategies.pullback_strategy.config import PullbackConfig
    from strategies.pullback_strategy.engine import apply_breadth_gate

    quotes, dates = _mk_quotes()
    hot_day, cold_day = dates[-2], dates[-1]     # 倒数第2日 / 末日

    def mk_sig(dates_):
        return pd.DataFrame({
            "ts_code": ["000001.SZ"] * len(dates_),
            "trade_date": list(dates_),
            "quant_score": [80.0] * len(dates_),
        })

    # 阈值 0.4：热日(0.75)保留，冷日(0.25)剔除
    cfg = PullbackConfig(min_market_breadth=0.4)
    sig = mk_sig([hot_day, cold_day])
    out = apply_breadth_gate(sig, quotes, cfg)
    check("0.4阈值保留热日信号", len(out) == 1 and out["trade_date"].iloc[0] == hot_day,
          f"got {len(out)} rows")
    check("剔除行不残留 _b20 列", "_b20" not in out.columns)

    # 关闭门控（0）：全部保留
    cfg_off = PullbackConfig(min_market_breadth=0.0)
    out_off = apply_breadth_gate(mk_sig([hot_day, cold_day]), quotes, cfg_off)
    check("门控=0 关闭时全保留", len(out_off) == 2, f"got {len(out_off)}")

    # 高阈值 0.8：热日 0.75 也不达标 → 全部剔除
    cfg_hi = PullbackConfig(min_market_breadth=0.8)
    out_hi = apply_breadth_gate(mk_sig([hot_day, cold_day]), quotes, cfg_hi)
    check("0.8阈值全部剔除", len(out_hi) == 0, f"got {len(out_hi)}")

    # 空信号：原样返回不报错
    empty = pd.DataFrame({"ts_code": [], "trade_date": []})
    out_empty = apply_breadth_gate(empty, quotes, cfg)
    check("空信号安全返回", len(out_empty) == 0)

    # 宽度口径抽验：末日宽度确为 0.25（ma20 已预热）
    ma20 = quotes.groupby("ts_code", sort=False)["close"].transform(
        lambda s: s.rolling(20, min_periods=20).mean())
    valid = ma20.notna()
    num = ((quotes["close"] > ma20) & valid).groupby(quotes["trade_date"]).sum()
    den = valid.groupby(quotes["trade_date"]).sum().replace(0, np.nan)
    b = num / den
    check("末日宽度=0.25", abs(float(b.iloc[-1]) - 0.25) < 1e-9, f"got {b.iloc[-1]}")
    check("倒数第2日宽度=0.75", abs(float(b.iloc[-2]) - 0.75) < 1e-9, f"got {b.iloc[-2]}")
    # 预热期(ma20 全 NaN)宽度应为 NaN 而非 0
    check("预热期宽度=NaN", pd.isna(b.iloc[0]), f"got {b.iloc[0]}")


def test_pullback_time_stop():
    """2026-10-19 出场改造（预注册 V3b）: 时间止损行为 + 配置开关。"""
    print("[pullback 时间止损 V3b]")
    from strategies.pullback_strategy.config import PullbackConfig
    from strategies.pullback_strategy.run_backtest import simulate

    code = "000001.SZ"
    dates = pd.bdate_range("2026-03-02", periods=12)

    def mkq(path):                     # path: 12 × (open, high, low, close)
        df = pd.DataFrame({
            "ts_code": code, "trade_date": dates,
            "open": [p[0] for p in path], "high": [p[1] for p in path],
            "low": [p[2] for p in path], "close": [p[3] for p in path],
        })
        df["raw_open"], df["raw_close"] = df["open"], df["close"]
        df["raw_prevc"] = df["close"].shift(1)
        return df

    def mksig():                       # 信号日=dates[0], 入场=dates[1] 开盘 10.0
        return pd.DataFrame({
            "ts_code": [code], "trade_date": [dates[0]],
            "close": [10.0], "raw_close": [10.0],
            "stop_loss": [9.4], "target_1": [11.8],
            "final_score": [80.0], "stock_name": ["测试"],
        })

    # 横盘微跌: 第5持有日(dates[5])收盘 9.8 < 入场 10.0 → 应 time_stop
    flat = [(10.0, 10.1, 9.9, 10.0), (10.0, 10.1, 9.9, 10.0),
            (10.0, 10.1, 9.8, 9.95), (9.95, 10.0, 9.7, 9.9),
            (9.9, 10.0, 9.65, 9.9), (9.9, 10.0, 9.6, 9.8)] + \
           [(9.8, 10.0, 9.6, 9.8)] * 6
    # 上行: 第5持有日收盘 10.4 > 入场 → 不触发，走满 10 日超时
    up = [(10.0, 10.1, 9.9, 10.0), (10.0, 10.1, 9.9, 10.0),
          (10.0, 10.3, 9.95, 10.2), (10.2, 10.45, 10.1, 10.4),
          (10.4, 10.55, 10.3, 10.5), (10.5, 10.6, 10.3, 10.4),
          (10.4, 10.6, 10.3, 10.45), (10.45, 10.6, 10.3, 10.4),
          (10.4, 10.5, 10.25, 10.4), (10.4, 10.5, 10.2, 10.4),
          (10.4, 10.5, 10.3, 10.4), (10.4, 10.5, 10.3, 10.4)]
    stopd = list(flat)
    stopd[5] = (9.9, 10.0, 9.3, 9.7)   # 第5持有日最低 9.3 触及止损 9.4

    cfg = PullbackConfig()
    check("config 默认 bt_time_stop_days==5", cfg.bt_time_stop_days == 5,
          f"got {cfg.bt_time_stop_days}")

    def one(tr):
        return tr.iloc[0] if len(tr) == 1 else None

    # A: 第5持有日收盘未盈利 → time_stop，按当日收盘出场
    tr, _ = simulate(mksig(), mkq(flat), cfg)
    r = one(tr)
    check("第5持有日未盈利 → time_stop", r is not None and r["exit_type"] == "time_stop",
          f"got {None if r is None else r['exit_type']} (n={len(tr)})")
    check("time_stop hold_days==5", r is not None and int(r["hold_days"]) == 5,
          f"got {None if r is None else r['hold_days']}")
    check("time_stop 按第5日收盘出场", r is not None
          and abs(float(r["exit_price"]) - 9.8) < 1e-6,
          f"got {None if r is None else r['exit_price']}")
    check("time_stop 收益为负(未盈利才触发)", r is not None and float(r["exit_ret"]) < 0,
          f"got {None if r is None else r['exit_ret']}")

    # B: 第5持有日已盈利 → 不触发，走满 max_hold 超时出场
    tr2, _ = simulate(mksig(), mkq(up), cfg)
    r2 = one(tr2)
    check("第5持有日盈利不触发 → timeout",
          r2 is not None and r2["exit_type"] == "timeout",
          f"got {None if r2 is None else r2['exit_type']}")
    check("盈利单持有满 10 日", r2 is not None and int(r2["hold_days"]) == 10,
          f"got {None if r2 is None else r2['hold_days']}")
    check("盈利单收益为正", r2 is not None and float(r2["exit_ret"]) > 0,
          f"got {None if r2 is None else r2['exit_ret']}")

    # C: 时间止损判定日同日触及止损 → 止损优先（同日判定顺序回归）
    tr3, _ = simulate(mksig(), mkq(stopd), cfg)
    r3 = one(tr3)
    check("同日止损判定优先于时间止损",
          r3 is not None and r3["exit_type"] == "stop",
          f"got {None if r3 is None else r3['exit_type']}")
    check("止损单按 stop 价成交", r3 is not None
          and abs(float(r3["exit_price"]) - 9.4) < 1e-6,
          f"got {None if r3 is None else r3['exit_price']}")

    # D: bt_time_stop_days=0 → 关闭，完全回退 V0 超时出场
    cfg_off = PullbackConfig(bt_time_stop_days=0)
    tr4, _ = simulate(mksig(), mkq(flat), cfg_off)
    r4 = one(tr4)
    check("时间止损=0 关闭 → 走满超时",
          r4 is not None and r4["exit_type"] == "timeout",
          f"got {None if r4 is None else r4['exit_type']}")
    check("关闭后持有满 10 日", r4 is not None and int(r4["hold_days"]) == 10,
          f"got {None if r4 is None else r4['hold_days']}")


def test_a2_market_cap_units():
    """2026-10-19 A2 口径修复: 流通市值 = close × volume × 100 / 换手率(%)。

    原式漏乘 100 → 系统性低估 100×，[30e8,500e8] 实际筛出 ≥3000 亿超大盘
    （2026-03-20/10-13 实证 5 票 e1=真实/100；funnel layer2 同口径一直 ×100）。
    """
    print("[main_uptrend A2 市值单位 ×100]")
    from types import SimpleNamespace

    from strategies.main_uptrend.config import MainUptrendConfig
    from strategies.main_uptrend.layer_a_prescreen import LayerAPrescreener

    cfg = MainUptrendConfig()
    check("A2 区间留痕 30e8~500e8",
          cfg.a_market_cap_min == 30e8 and cfg.a_market_cap_max == 500e8,
          f"got {cfg.a_market_cap_min}~{cfg.a_market_cap_max}")

    day = pd.Timestamp("2026-10-13")
    # 1e6 股 × 10 元: 换手 1% → 流通盘 1e8 股 → 10 亿（低于下限出局）；
    #                  换手 0.1% → 1e9 股 → 100 亿（入围）
    df = pd.DataFrame({
        "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"],
        "trade_date": [day] * 4,
        "raw_close": [10.0] * 4,
        "close": [10.0] * 4,
        "volume": [1e6] * 4,
        "turnover_rate": [1.0, 0.1, None, 0.0],
    })
    p = LayerAPrescreener(cfg, loader=SimpleNamespace(_preloaded_daily=df))
    got = p._filter_market_cap_preloaded("2026-10-13")
    check("×100 口径: 100亿(0.1%换手)入围", got == {"000002.SZ"}, f"got {got}")
    check("10亿(1%换手)低于下限出局", "000001.SZ" not in got, f"got {got}")
    check("NULL/0 换手率行剔除",
          "000003.SZ" not in got and "000004.SZ" not in got, f"got {got}")


def test_threshold_config_invariants():
    print("[门控阈值配置留痕]")
    from strategies.pullback_strategy.config import PullbackConfig
    from strategies.main_uptrend.config import MainUptrendConfig

    pb = PullbackConfig()
    check("pullback reward_risk==3.0", pb.reward_risk == 3.0, f"got {pb.reward_risk}")
    check("pullback min_market_breadth==0.4", pb.min_market_breadth == 0.4,
          f"got {pb.min_market_breadth}")
    check("pullback bt_time_stop_days==5（2026-10-19 预注册 V3b）",
          pb.bt_time_stop_days == 5, f"got {pb.bt_time_stop_days}")

    mu = MainUptrendConfig()
    check("main_uptrend max_breadth_ma20==0.6", mu.max_breadth_ma20 == 0.6,
          f"got {mu.max_breadth_ma20}")

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    bt8 = os.path.join(root, "strategies", "overnight_8step", "backtest_8step.py")
    src = open(bt8, encoding="utf-8").read()
    m = re.search(r"^MIN_MARKET_BREADTH = ([0-9.]+)", src, re.M)
    check("8step 回测门控阈值==0.5", m is not None and float(m.group(1)) == 0.5,
          f"match={m.group(1) if m else None}")
    check("8step 回测加载 .env", "_load_dotenv(" in src)

    zy1 = os.path.join(root, "strategies", "overnight_8step", "zuiyou1.py")
    src1 = open(zy1, encoding="utf-8").read()
    m1 = re.search(r"^MIN_MARKET_BREADTH = ([0-9.]+)", src1, re.M)
    check("8step 扫描器门控阈值==0.5", m1 is not None and float(m1.group(1)) == 0.5,
          f"match={m1.group(1) if m1 else None}")
    check("8step 扫描器接入宽度门控", "_market_breadth(" in src1 and
          "MIN_MARKET_BREADTH > 0" in src1)


def test_main_uptrend_breadth_source():
    print("[main_uptrend get_market_breadth DB 口径]")
    try:
        from strategies.main_uptrend.data_loader import DataLoader
        dl = DataLoader()
        # 用一个较早交易日（数据确定存在）验证 SQL 口径
        b = dl.get_market_breadth("2026-06-15")
    except Exception as e:  # noqa: BLE001
        check("get_market_breadth 可执行", False, repr(e))
        return
    b20 = b.get("breadth_ma20")
    check("breadth_ma20 为 [0,1] 数值",
          isinstance(b20, (int, float)) and 0.0 <= float(b20) <= 1.0,
          f"got {b20!r}")
    b5 = b.get("breadth_ma5")
    check("breadth_ma5 为 [0,1] 数值",
          isinstance(b5, (int, float)) and 0.0 <= float(b5) <= 1.0,
          f"got {b5!r}")


def test_uptrend_preload_offbyone():
    print("[main_uptrend preload 上界 off-by-one 修复]")
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    dl = os.path.join(root, "strategies", "main_uptrend", "data_loader.py")
    src = open(dl, encoding="utf-8").read()
    # 修前恒用 `trade_date < %s`（开区间），使 preload(start,end) 永不含 end 当日；
    # run_daily 调 preload(preload_start, eval_date) 时 eval_date 被排除 →
    # get_indicators_snapshot(eval_date) 恒空 → B/E 层自 2026-06-01 起恒 0
    # （分批重构 1d102ec4 引入）。修后：末批闭区间 `<=`、非末批开区间 `<`。
    # 断言防止静默回改。（函数级验证见 verify_uptrend_e2e / 手工 diag，
    #  preload 全市场需数分钟，不纳入快速单测。）
    check("preload 使用可变上界算子 op（末批<=，非末批<）",
          'op = "<=" if batch_end_dt >= ed else "<"' in src)
    check("preload SQL 上界用 {op} 而非硬编码 <",
          "trade_date {op} %s" in src and "trade_date < %s" not in src)


def main():
    test_pullback_breadth_gate()
    test_pullback_time_stop()
    test_a2_market_cap_units()
    test_threshold_config_invariants()
    test_main_uptrend_breadth_source()
    test_uptrend_preload_offbyone()
    print("=" * 40)
    print(f"PASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
