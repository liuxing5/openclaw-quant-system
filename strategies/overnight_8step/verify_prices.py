"""
价格验证工具 — 用 baostock 历史K线数据验证记录的价格和涨跌幅是否正确

修复: 原版使用腾讯实时API (qt.gtimg.cn) 永远返回当前价格，
      对比历史记录毫无意义且会误报。现改为 baostock 历史数据。

用法:
    python verify_prices.py [filepath]
"""
import os
import re
import sys
import time
import argparse
from collections import defaultdict

import baostock as bs


def to_baostock_code(code: str) -> str:
    """纯数字代码 → baostock 格式 (sh.600519 / sz.000001)"""
    code = code.strip()
    if code.startswith(('sh.', 'sz.', 'bj.')):
        return code.lower()
    if code.startswith('6') or code.startswith('9'):
        return f"sh.{code}"
    return f"sz.{code}"


def fetch_historical_prices(codes: list, date_str: str) -> dict:
    """通过 baostock 获取指定日期的收盘价和涨跌幅"""
    bs.login()
    results = {}
    try:
        for code in codes:
            bs_code = to_baostock_code(code)
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,code,close,preclose,pctChg",
                start_date=date_str,
                end_date=date_str,
                frequency="d",
                adjustflag="3",
            )
            if rs.error_code != '0':
                continue
            rows = rs.get_data()
            if rows.empty:
                continue
            row = rows.iloc[0]
            try:
                close_val = float(row['close'])
                pct_chg = float(row['pctChg'])
                if close_val > 0:
                    results[code] = {"close": close_val, "change": round(pct_chg, 2)}
            except (ValueError, TypeError):
                continue
            time.sleep(0.05)
    finally:
        bs.logout()
    return results


def main():
    parser = argparse.ArgumentParser(description="验证历史记录中的价格和涨跌幅")
    parser.add_argument("filepath", nargs="?", help="记录文件路径")
    args = parser.parse_args()

    filepath = args.filepath
    if not filepath:
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "recommend", "选股记录汇总.txt")

    if not os.path.exists(filepath):
        print(f"❌ 文件不存在: {filepath}")
        sys.exit(1)

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    date_pattern = r"(?:📅\s*)?(\d{4}-\d{2}-\d{2})\s*\([^)]+\)"
    date_matches = list(re.finditer(date_pattern, content))

    records = []
    for i, date_match in enumerate(date_matches):
        date_str = date_match.group(1)
        start_pos = date_match.end()
        end_pos = date_matches[i + 1].start() if i + 1 < len(date_matches) else len(content)
        block = content[start_pos:end_pos]

        patterns = [
            r'(?:sh\.|sz\.)?(\d{6})\s+(?:hs300\+zz500|zz1000|hs300|zz500)?\s+([\d.]+)\s+([\d.]+)',
            r'股票:\s*(?:sh\.|sz\.)?(\d{6})\s*\|\s*现价:\s*([\d.]+)\s*\|\s*涨幅:\s*([\d.]+)%',
            r'代码[:\s]*(?:sh\.|sz\.)?(\d{6})\s+价格[:\s]*([\d.]+)\s+涨幅[:\s]*([\d.]+)',
            r'(?:sh\.|sz\.)?(\d{6})\s+([\d.]+)\s+([\d.]+)%',
        ]

        for pattern in patterns:
            for code, price, change in re.findall(pattern, block):
                try:
                    price_val = float(price)
                    change_val = float(change)
                    if price_val > 0:
                        records.append({
                            "date": date_str,
                            "code": code,
                            "price": price_val,
                            "change": change_val,
                        })
                except ValueError:
                    continue

    seen = set()
    unique_records = []
    for r in records:
        key = (r['date'], r['code'])
        if key not in seen:
            seen.add(key)
            unique_records.append(r)

    print(f"共找到 {len(unique_records)} 条记录需要验证\n")

    by_date = defaultdict(list)
    for r in unique_records:
        by_date[r['date']].append(r)

    errors = []
    corrections = []

    for date in sorted(by_date.keys()):
        recs = by_date[date]
        codes = [r['code'] for r in recs]

        real_data = fetch_historical_prices(codes, date)

        if not real_data:
            print(f"⚠️ {date}: 无法获取历史数据（可能非交易日或数据未就绪）")
            continue

        for r in recs:
            code = r['code']
            if code in real_data:
                real_close = real_data[code]['close']
                real_change = real_data[code]['change']

                price_diff = abs(r['price'] - real_close)
                change_diff = abs(r['change'] - real_change)

                if price_diff > 0.02 or change_diff > 0.5:
                    errors.append({
                        "date": date,
                        "code": code,
                        "recorded_price": r['price'],
                        "real_price": real_close,
                        "recorded_change": r['change'],
                        "real_change": real_change,
                    })
                    corrections.append({
                        "date": date,
                        "code": code,
                        "old_price": r['price'],
                        "new_price": real_close,
                        "old_change": r['change'],
                        "new_change": real_change,
                    })
                    print(f"❌ {date} | {code} | 记录:{r['price']:.2f} 实际:{real_close:.2f} | 涨幅记录:{r['change']:.2f}% 实际:{real_change:.2f}%")
                else:
                    print(f"✅ {date} | {code} | {r['price']:.2f} ({r['change']:.2f}%)")
            else:
                print(f"⚠️ {date} | {code} | 未在 baostock 找到历史数据")

    print(f"\n{'='*60}")
    print(f"验证完成！共 {len(unique_records)} 条记录")
    print(f"正确: {len(unique_records) - len(errors)} 条")
    print(f"错误: {len(errors)} 条")

    if corrections:
        print(f"\n需要修正的数据:")
        for c in corrections:
            print(f"  {c['date']} | {c['code']}: 价格 {c['old_price']:.2f}→{c['new_price']:.2f}, 涨幅 {c['old_change']:.2f}%→{c['new_change']:.2f}%")


if __name__ == "__main__":
    main()
