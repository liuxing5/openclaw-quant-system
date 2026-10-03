#!/usr/bin/env python3
"""汇总 tools/run_rolling_backtest.py 产生的各窗口文本报告。"""
import pathlib
import re
import sys

FILES = sorted(pathlib.Path("/tmp").glob("rolling_backtest_*.txt"))
if not FILES:
    sys.exit("no files matched /tmp/rolling_backtest_*.txt")

rows = []
for f in FILES:
    txt = f.read_text(encoding="utf-8", errors="ignore")
    def grab(pat):
        m = re.search(pat, txt, re.S)
        return m.group(1) if m else ""
    m = re.search(r"rolling_backtest_(.+)_(.+)\.txt", f.name)
    window = f"{m.group(1)}~{m.group(2)}" if m else f.name
    rows.append(
        {
            "window": window,
            "signals": grab(r"总信号数: (\d+)"),
            "win10": grab(r"10日后收益分布.*?胜率\(>0\): ([\d.]+)%"),
            "mean10": grab(r"10日后收益分布.*?均值收益: ([\d.-]+)%"),
            "win20": grab(r"20日后收益分布.*?胜率\(>0\): ([\d.]+)%"),
            "mean20": grab(r"20日后收益分布.*?均值收益: ([\d.-]+)%"),
            "rankic20v": (lambda m: m[-1].group(1) if m else "")(list(re.finditer(r"composite_score: IC=[\d.-]+, RankIC=([\d.-]+)", txt))),
        }
    )

# 简元:第三列不容易,用单独列输出 RankIC
print("| 窗口 | 信号数 | 10d胜率 | 10d均值 | 20d胜率 | 20d均值 | 20d RankIC |")
print("|---|---|---|---|---|---|---|")
for r in rows:
    print(
        f"| {r['window']} | {r['signals']} | {r['win10']}% | {r['mean10']}% | {r['win20']}% | {r['mean20']}% | {r['rankic20v']} |"
    )
