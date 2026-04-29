"""
深度诊断: 验证 L1 数据真实性
================================================
用法: python deep_diagnose.py

绕过所有缓存和抽象,直接调 baostock,看最原始的数据。
重点验证: 赣锋锂业、天华超净等"高弹性"股票的真实回撤。
"""
from __future__ import annotations
import sys
from datetime import datetime, timedelta

import baostock as bs
import pandas as pd


def fetch_raw(symbol: str, days: int = 300, adjust: str = "2"):
    """直接调 baostock,不走任何缓存"""
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y-%m-%d")

    bs_code = f"sh.{symbol}" if symbol.startswith("6") else f"sz.{symbol}"
    rs = bs.query_history_k_data_plus(
        bs_code,
        "date,open,high,low,close,volume",
        start_date=start,
        end_date=end,
        frequency="d",
        adjustflag=adjust,
    )
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=rs.fields)
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def diagnose(symbol: str, name: str):
    print(f"\n{'='*70}")
    print(f"诊断: {name} ({symbol})")
    print('='*70)

    # 拉前复权,300 天窗口
    df = fetch_raw(symbol, days=300, adjust="2")
    if df is None or df.empty:
        print(f"❌ 无数据")
        return

    print(f"实际拿到 {len(df)} 行数据")
    print(f"日期范围: {df['date'].min().date()} ~ {df['date'].max().date()}")
    print(f"实际跨度: {(df['date'].max() - df['date'].min()).days} 天")

    if len(df) < 250:
        print(f"⚠ 警告: 数据不足 250 行,只有 {len(df)} 行!")
        print(f"  这意味着 250 日高点窗口实际只覆盖 {len(df)} 天")

    # 显示价格范围
    print(f"\n--- 整段数据(全部 {len(df)} 行)的价格统计 ---")
    print(f"  最高价: {df['high'].max():.2f} (日期: {df.loc[df['high'].idxmax(), 'date'].date()})")
    print(f"  最低价: {df['low'].min():.2f} (日期: {df.loc[df['low'].idxmin(), 'date'].date()})")
    print(f"  最新收盘: {df['close'].iloc[-1]:.2f} (日期: {df['date'].iloc[-1].date()})")

    # 按 250 / 60 窗口算
    last_close = float(df['close'].iloc[-1])
    h250 = float(df['high'].tail(250).max())
    l60 = float(df['low'].tail(60).min())
    h250_date = df.loc[df['high'].tail(250).idxmax(), 'date'].date()
    l60_date = df.loc[df['low'].tail(60).idxmin(), 'date'].date()
    drawdown = (h250 - last_close) / h250
    rebound = (last_close - l60) / l60

    print(f"\n--- L1 计算窗口 ---")
    print(f"  250 日高点: {h250:.2f} (日期: {h250_date})")
    print(f"  60 日低点:  {l60:.2f} (日期: {l60_date})")
    print(f"  当前收盘:   {last_close:.2f}")
    print(f"  回撤:       {drawdown:.1%}  {'✓ 通过 L1' if drawdown >= 0.25 else '✗ 不到 25%'}")
    print(f"  反弹:       {rebound:.1%}  {'✓ 通过 L1' if rebound <= 0.20 else '✗ 超过 20%'}")

    # 显示最近 5 天和最早 5 天
    print(f"\n--- 最早 5 天 ---")
    print(df.head(5)[['date', 'open', 'high', 'low', 'close']].to_string(index=False))
    print(f"\n--- 最近 5 天 ---")
    print(df.tail(5)[['date', 'open', 'high', 'low', 'close']].to_string(index=False))

    # 画一个粗略的价格走势(文字版)
    print(f"\n--- 价格走势(每 30 天采样,前复权) ---")
    sampled = df.iloc[::30].copy()
    max_p = df['close'].max()
    min_p = df['close'].min()
    for _, row in sampled.iterrows():
        # 50 字符宽的横向条形图
        ratio = (row['close'] - min_p) / (max_p - min_p) if max_p > min_p else 0.5
        bar = '█' * int(ratio * 50)
        print(f"  {row['date'].date()} {row['close']:>8.2f}  {bar}")


def main():
    bs.login()

    # 重点诊断你说"卡 L1"的几只
    targets = [
        ("002460", "赣锋锂业"),
        ("002709", "天赐材料"),
        ("300390", "天华超净"),
        ("603259", "药明康德"),
        ("300661", "圣邦股份"),
    ]
    for code, name in targets:
        diagnose(code, name)

    bs.logout()


if __name__ == "__main__":
    main()
