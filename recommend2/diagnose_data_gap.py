"""
数据接口诊断: 查清为什么 1652 只股票数据不足
================================================
按上市日期、代码段、名称特征分类,看 baostock 在哪类股票上失败。
"""
from __future__ import annotations
import sys
from collections import Counter
from datetime import datetime

import baostock as bs
import pandas as pd


def main():
    bs.login()

    # 1. 拉全市场列表
    rs = bs.query_all_stock(day=None)
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    all_stocks = pd.DataFrame(rows, columns=rs.fields)
    print(f"全市场 {len(all_stocks)} 行(含指数和北交所等)")

    # 过滤同 main.py
    df = all_stocks.copy()
    df = df.rename(columns={"code": "bs_code", "code_name": "name",
                              "tradeStatus": "trade_status"})
    df = df[df["trade_status"] == "1"]
    df = df[~df["bs_code"].str.startswith(("sh.000", "sz.399", "bj."))]
    df["symbol"] = df["bs_code"].str.split(".").str[1]
    print(f"过滤后 {len(df)} 只股票")

    # 2. 抽样 200 只测数据获取情况
    sample = df.sample(min(200, len(df)), random_state=42)
    print(f"\n随机抽样 {len(sample)} 只测数据...\n")

    end_date = datetime.now().strftime("%Y-%m-%d")

    bucket = {"ok_500+": [], "partial_72ish": [], "partial_other": [],
              "empty_0": [], "error": []}
    sample_data = []

    for i, row in enumerate(sample.itertuples(), 1):
        try:
            rs = bs.query_history_k_data_plus(
                row.bs_code,
                "date,close",
                start_date="2023-01-01",
                end_date=end_date,
                frequency="d",
                adjustflag="2",
            )
            data_rows = []
            while rs.next():
                data_rows.append(rs.get_row_data())
            n = len(data_rows)
            if n == 0:
                bucket["empty_0"].append((row.symbol, row.name))
            elif n < 100:
                if 60 <= n <= 90:
                    bucket["partial_72ish"].append((row.symbol, row.name, n))
                else:
                    bucket["partial_other"].append((row.symbol, row.name, n))
            else:
                bucket["ok_500+"].append((row.symbol, row.name, n))
            sample_data.append((row.symbol, row.name, n))
        except Exception as e:
            bucket["error"].append((row.symbol, row.name, str(e)[:50]))
        if i % 50 == 0:
            print(f"  已测 {i}/200")

    # 3. 统计
    print(f"\n{'='*70}")
    print("数据获取分布")
    print('='*70)
    for label, items in bucket.items():
        pct = len(items) / len(sample) * 100
        print(f"  {label:20s} {len(items):4d} 只 ({pct:.1f}%)")

    # 4. 详细看 0 行和 72 行的票是什么
    print(f"\n--- 返回 0 行的样本(前 15 个)---")
    for s, n in bucket["empty_0"][:15]:
        print(f"  {s} {n}")

    print(f"\n--- 只返回 60-90 行的样本(前 15 个)---")
    for s, n, cnt in bucket["partial_72ish"][:15]:
        print(f"  {s} {n}  ({cnt} 行)")

    # 5. 查代码段分布
    print(f"\n--- 0 行的代码段分布 ---")
    code_prefixes = Counter(s[:3] for s, n in bucket["empty_0"])
    for prefix, count in code_prefixes.most_common():
        print(f"  {prefix}xxx: {count} 只")

    print(f"\n--- 60-90 行的代码段分布 ---")
    code_prefixes = Counter(s[:3] for s, n, cnt in bucket["partial_72ish"])
    for prefix, count in code_prefixes.most_common():
        print(f"  {prefix}xxx: {count} 只")

    # 6. 0 行的票详细查上市日期
    if bucket["empty_0"]:
        print(f"\n--- 验证:0 行的票实际是否最近上市?(取 5 只查 query_stock_basic)---")
        for symbol, name in bucket["empty_0"][:5]:
            bs_code = f"sh.{symbol}" if symbol.startswith("6") else f"sz.{symbol}"
            try:
                rs = bs.query_stock_basic(code=bs_code)
                while rs.next():
                    info = rs.get_row_data()
                    print(f"  {symbol} {name}: 上市日 {info[2] if len(info) > 2 else 'N/A'}, "
                          f"状态 {info[5] if len(info) > 5 else 'N/A'}, "
                          f"类型 {info[4] if len(info) > 4 else 'N/A'}")
                    break
            except Exception as e:
                print(f"  {symbol} 查询失败: {e}")

    bs.logout()


if __name__ == "__main__":
    main()
