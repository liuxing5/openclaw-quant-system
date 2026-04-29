"""
诊断脚本: 对比前后复权对 L1 的影响
================================================
用法: python diagnose_l1.py

会拉一组样本股的前/后复权 K 线,分别算 L1 的 drawdown 和 rebound,
直观展示 baostock 后复权价对 L1 的误判程度。
"""
from __future__ import annotations

import logging
from data_loader import DataLoader, ensure_logged_in

logging.basicConfig(level=logging.WARNING, format="%(message)s")

# 一组样本: 大、中、小盘 + 不同板块
SAMPLES = [
    ("000001", "平安银行"),
    ("600036", "招商银行"),
    ("002594", "比亚迪"),
    ("600519", "贵州茅台"),
    ("300750", "宁德时代"),
    ("002415", "海康威视"),
    ("600276", "恒瑞医药"),
    ("000333", "美的集团"),
    ("002400", "省广集团"),  # 你扫描里得 3 分的
    ("603171", "税友股份"),  # 你扫描里得 4 分的
]


def calc_l1(df, label):
    if df is None or len(df) < 250:
        return None, None, None
    last_close = float(df["close"].iloc[-1])
    h250 = float(df["high"].tail(250).max())
    l60 = float(df["low"].tail(60).min())
    drawdown = (h250 - last_close) / h250 if h250 > 0 else 0
    rebound = (last_close - l60) / l60 if l60 > 0 else 0
    return last_close, drawdown, rebound


def main():
    loader = DataLoader()
    ensure_logged_in()

    print()
    print("=" * 98)
    print(f"{'代码':<8}{'名称':<10}{'今价(qfq)':>12}{'今价(hfq)':>12}"
          f"{'回撤(qfq)':>12}{'回撤(hfq)':>12}{'反弹(qfq)':>12}{'反弹(hfq)':>12}{'L1判定':>8}")
    print("=" * 98)

    qfq_pass = 0
    hfq_pass = 0
    for code, name in SAMPLES:
        df_qfq = loader.get_kline(code, days=300, adjust="qfq")
        df_hfq = loader.get_kline(code, days=300, adjust="hfq")

        c_q, dd_q, rb_q = calc_l1(df_qfq, "qfq")
        c_h, dd_h, rb_h = calc_l1(df_hfq, "hfq")

        if dd_q is None or dd_h is None:
            print(f"{code:<8}{name:<10}  数据不足")
            continue

        # 判定: 回撤 ≥35% 且 反弹 ≤20%
        pass_qfq = dd_q >= 0.35 and rb_q <= 0.20
        pass_hfq = dd_h >= 0.35 and rb_h <= 0.20
        if pass_qfq:
            qfq_pass += 1
        if pass_hfq:
            hfq_pass += 1

        verdict = "qfq✓" if pass_qfq else "qfq✗"
        verdict += " hfq✓" if pass_hfq else " hfq✗"

        print(f"{code:<8}{name:<10}{c_q:>12.2f}{c_h:>12.2f}"
              f"{dd_q:>11.1%}{dd_h:>11.1%}{rb_q:>11.1%}{rb_h:>11.1%}  {verdict}")

    print("=" * 98)
    print(f"\n汇总: {len(SAMPLES)} 只样本中,L1 通过数:")
    print(f"  前复权(qfq, 真实价格): {qfq_pass} 只")
    print(f"  后复权(hfq, baostock 默认): {hfq_pass} 只")
    if qfq_pass > hfq_pass:
        print(f"\n→ 后复权漏掉了 {qfq_pass - hfq_pass} 只本该通过 L1 的股票")
        print("  这就是 86.6% 全市场卡 L1 的根本原因")
    elif qfq_pass == hfq_pass and qfq_pass == 0:
        print("\n→ 即使前复权也没有触发,可能是策略阈值确实太严")


if __name__ == "__main__":
    main()
