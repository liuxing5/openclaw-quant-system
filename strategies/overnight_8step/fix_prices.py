"""
价格修正工具 — 用 baostock 历史K线数据修正记录文件中的价格和涨跌幅

修复: 原版使用腾讯实时API (qt.gtimg.cn) 永远返回当前价格，
      用当前价格覆盖历史记录导致数据损坏。现改为 baostock 历史数据。

用法:
    python fix_prices.py [filepath]
"""
import os
import re
import sys
import time
import argparse
from collections import defaultdict

import baostock as bs


def to_baostock_code(code: str) -> str:
    """纯数字代码 → baostock 格式"""
    code = code.strip()
    if code.startswith(('sh.', 'sz.', 'bj.')):
        return code.lower()
    if code.startswith('6') or code.startswith('9'):
        return f"sh.{code}"
    return f"sz.{code}"


def fetch_historical_prices(codes: list, date_str: str) -> dict:
    """通过 baostock 获取指定日期的收盘价和涨跌幅"""
    results = {}
    lg = bs.login()
    if lg.error_code != '0':
        print(f"baostock 登录失败: {lg.error_msg}")
        return results
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


def fix_line(line: str, real_data: dict) -> str:
    """修正一行中的价格和涨幅"""
    code_match = re.search(r'(?:sh\.|sz\.)?(\d{6})', line)
    if not code_match:
        return line

    code = code_match.group(1)
    if code not in real_data:
        return line

    new_price = f"{real_data[code]['close']:.2f}"
    new_change = f"{real_data[code]['change']:.2f}"

    # V1: 股票: sh.600118 | 现价: 84.39 | 涨幅: 4.83% | 评分: 7
    m = re.match(r'(股票:\s*(?:sh\.|sz\.)?\d{6}\s*\|\s*现价:\s*)[\d.]+(\s*\|\s*涨幅:\s*)[\d.]+(%\s*\|\s*评分:\s*\d+.*)', line)
    if m:
        return f"{m.group(1)}{new_price}{m.group(2)}{new_change}{m.group(3)}"

    # V3: 代码: sh.600707  价格: 7.19  涨幅: 5.2709%  量比:  1.88  得分: 100
    m = re.match(r'(代码:\s*(?:sh\.|sz\.)?\d{6}\s+价格:\s*)[\d.]+(\s+涨幅:\s*)[\d.]+(%\s+量比:\s*[\d.]+\s+得分:\s*\d+.*)', line)
    if m:
        return f"{m.group(1)}{new_price}{m.group(2)}{real_data[code]['change']:.4f}{m.group(3)}"

    # V9: 688525 xxx 分数:75 仓位:10% 理由:xxx 价格:285.2
    m = re.match(r'(\d{6}\s+\S+\s+分数:\d+\s+仓位:\d+%\s+理由:[^\s]+\s+价格:)[\d.]+', line)
    if m:
        return f"{m.group(1)}{real_data[code]['close']:.1f}"

    # zuiyou1: sh.600977   hs300+zz500  14.91  5.00  3.31  5.00  0  6.14  120  特征
    m = re.match(r'((?:sh\.|sz\.)?\d{6}\s+\S+\s+)[\d.]+(\s+)[\d.]+(\s+.+)', line)
    if m:
        return f"{m.group(1)}{new_price}{m.group(2)}{new_change}{m.group(3)}"

    # V2/V4/V6/V7: sh.688027   619.14   7.12    1.70     特征
    m = re.match(r'((?:sh\.|sz\.)?\d{6}\s+)[\d.]+(\s+)[\d.]+%?(\s+.+)', line)
    if m:
        return f"{m.group(1)}{new_price}{m.group(2)}{new_change}{m.group(3)}"

    return line


def main():
    parser = argparse.ArgumentParser(description="用历史数据修正记录文件中的价格和涨跌幅")
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

    # 按日期分组收集股票代码，用 baostock 获取历史数据
    date_codes = defaultdict(list)
    for i, date_match in enumerate(date_matches):
        date_str = date_match.group(1)
        start_pos = date_match.end()
        end_pos = date_matches[i + 1].start() if i + 1 < len(date_matches) else len(content)
        block = content[start_pos:end_pos]
        codes = re.findall(r'(?:sh\.|sz\.)?(\d{6})', block)
        date_codes[date_str].extend(c for c in set(codes) if c not in date_codes[date_str])

    print("正在获取历史行情数据 (baostock)...")
    all_real_data = {}
    for date, codes in date_codes.items():
        real_data = fetch_historical_prices(codes, date)
        all_real_data[date] = real_data
        print(f"  {date}: 获取 {len(real_data)} 只股票数据")
        time.sleep(0.1)

    # 逐区块修正
    new_content_parts = []
    last_end = 0
    total_fixed = 0

    for i, date_match in enumerate(date_matches):
        date_str = date_match.group(1)
        start_pos = date_match.end()
        end_pos = date_matches[i + 1].start() if i + 1 < len(date_matches) else len(content)

        new_content_parts.append(content[last_end:date_match.start()])
        new_content_parts.append(content[date_match.start():start_pos])

        block = content[start_pos:end_pos]
        real_data = all_real_data.get(date_str, {})

        lines = block.split('\n')
        fixed_lines = []
        for line in lines:
            fixed = fix_line(line, real_data)
            if fixed != line:
                total_fixed += 1
            fixed_lines.append(fixed)

        new_content_parts.append('\n'.join(fixed_lines))
        last_end = end_pos

    new_content_parts.append(content[last_end:])
    new_content = ''.join(new_content_parts)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"\n修正完成！共修正 {total_fixed} 行数据")
    print(f"已保存至: {filepath}")


if __name__ == "__main__":
    main()
