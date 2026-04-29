"""
隔夜选股策略回测分析系统 V2
========================
功能：
1. 解析选股记录汇总.txt中的所有推荐股票
2. 查询T+1、T+2、T+3交易日的实际涨跌幅
3. 计算每个策略的成功率、平均收益等指标
4. 生成策略对比报告
5. 核对并修正日期错误
"""

import baostock as bs
import pandas as pd
import re
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

# ============================================================
#  1. 配置参数
# ============================================================
SUMMARY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "选股记录汇总.txt"
)

# 成功标准：T+1涨幅 >= 1% 算成功
SUCCESS_THRESHOLD = 1.0

# 止损线
STOP_LOSS = -2.5

# 需要查询的后续交易日数量
FOLLOW_UP_DAYS = 3

# 交易日历（2026年4月）
TRADING_CALENDAR = [
    "2026-04-01", "2026-04-02", "2026-04-03",
    "2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10",
    "2026-04-13", "2026-04-14", "2026-04-15", "2026-04-16", "2026-04-17",
    "2026-04-20", "2026-04-21", "2026-04-22", "2026-04-23", "2026-04-24",
    "2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30",
]


def get_next_trading_days(date_str: str, n: int) -> List[str]:
    """获取指定日期后的n个交易日"""
    try:
        idx = TRADING_CALENDAR.index(date_str)
        return TRADING_CALENDAR[idx + 1: idx + 1 + n]
    except (ValueError, IndexError):
        return []


def normalize_code(code: str) -> str:
    """标准化股票代码"""
    code = code.strip()
    if code.startswith(("sh.", "sz.")):
        return code
    if code.startswith("6"):
        return f"sh.{code}"
    else:
        return f"sz.{code}"


def parse_summary_file(filepath: str) -> List[Dict]:
    """解析选股记录汇总.txt，提取所有推荐股票"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    records = []
    
    # 按日期分割
    date_pattern = r"📅\s+(\d{4}-\d{2}-\d{2})"
    date_matches = list(re.finditer(date_pattern, content))
    
    for i, date_match in enumerate(date_matches):
        date_str = date_match.group(1)
        # 获取该日期区块的内容
        start_pos = date_match.end()
        end_pos = date_matches[i + 1].start() if i + 1 < len(date_matches) else len(content)
        block = content[start_pos:end_pos]
        
        # 分割成不同的策略区块
        # 策略区块以 ── 开头
        strategy_sections = re.split(r'\n──\s+', block)
        
        for section in strategy_sections:
            section = section.strip()
            if not section:
                continue
            
            # 提取策略名称（第一行）
            first_line = section.split('\n')[0].strip()
            # 移除末尾的 ── 和时间信息
            strategy_name = re.sub(r'\s*─+\s*$', '', first_line)
            strategy_name = re.sub(r'\s*\(\d+:\d+\)\s*$', '', strategy_name)
            strategy_name = re.sub(r'\s*\(\d+\s*只\)\s*.*$', '', strategy_name)
            strategy_name = strategy_name.strip()
            
            # 简化策略名称
            for key, value in {
                "zuiyou最优版·稳健路径": "zuiyou最优",
                "V1 稳健法": "V1稳健",
                "V2 高位突破": "V2高位",
                "V3 合并增强": "V3合并",
                "V4 双轨制": "V4双轨",
                "V5 双轨增强": "V5增强",
                "V6 龙头法": "V6龙头",
                "V7 Omni": "V7Omni",
                "V8 终极": "V8终极",
                "V9 闭环系统": "V9闭环",
            }.items():
                if key in strategy_name:
                    strategy_name = value
                    break
            
            # 提取股票行 - 使用多种模式匹配
            lines = section.split('\n')
            for line in lines[1:]:  # 跳过第一行（策略名称）
                line = line.strip()
                if not line or line.startswith('──') or line.startswith('股票:') is False and line.startswith('代码:') is False and not re.match(r'(sh\.|sz\.)', line):
                    # 尝试匹配 "股票: sh.600118" 格式
                    if '股票:' in line:
                        pass  # 会在下面处理
                    else:
                        continue
                
                # 格式1：zuiyou最优版
                # sh.600977   hs300+zz500  14.91    5.00   3.31    5.00     0    6.14   120  稳健蓄势|黄金放量|...
                m = re.match(r'(sh\.\d{6}|sz\.\d{6})\s+\S+\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s+([\d.-]+)\s+(\d+)\s+(.+)', line)
                if m:
                    records.append({
                        "date": date_str,
                        "strategy": strategy_name,
                        "code": normalize_code(m.group(1)),
                        "price": float(m.group(2)),
                        "pct": float(m.group(3)),
                        "vol_ratio": float(m.group(4)),
                        "turn": float(m.group(5)),
                        "streak": int(m.group(6)),
                        "bias": float(m.group(7)),
                        "score": int(m.group(8)),
                        "tags": m.group(9).strip(),
                    })
                    continue
                
                # 格式2：V1稳健法
                # 股票: sh.600118 | 现价: 84.39 | 涨幅: 4.83% | 评分: 7
                m = re.search(r'股票:\s*(sh\.\d{6}|sz\.\d{6})\s*\|\s*现价:\s*([\d.]+)\s*\|\s*涨幅:\s*([\d.]+)%\s*\|\s*评分:\s*(\d+)', line)
                if m:
                    records.append({
                        "date": date_str,
                        "strategy": strategy_name,
                        "code": normalize_code(m.group(1)),
                        "price": float(m.group(2)),
                        "pct": float(m.group(3)),
                        "vol_ratio": 0,
                        "turn": 0,
                        "streak": 0,
                        "bias": 0,
                        "score": int(m.group(4)),
                        "tags": "",
                    })
                    continue
                
                # 格式3：V3合并增强
                # 代码: sh.688615  价格: 187.21   涨幅: 4.7622%  量比:  1.61  得分: 100
                m = re.search(r'代码:\s*(sh\.\d{6}|sz\.\d{6})\s*价格:\s*([\d.]+)\s*涨幅:\s*([\d.]+)%\s*量比:\s*([\d.]+)\s*得分:\s*(\d+)', line)
                if m:
                    records.append({
                        "date": date_str,
                        "strategy": strategy_name,
                        "code": normalize_code(m.group(1)),
                        "price": float(m.group(2)),
                        "pct": float(m.group(3)),
                        "vol_ratio": float(m.group(4)),
                        "turn": 0,
                        "streak": 0,
                        "bias": 0,
                        "score": int(m.group(5)),
                        "tags": "",
                    })
                    continue
                
                # 格式4：V4双轨制/V2高位突破
                # sh.600959   4.20 7.97% 2.77 4.96%    60  强势上攻 | 主力扫盘
                m = re.match(r'(sh\.\d{6}|sz\.\d{6})\s+([\d.]+)\s+([\d.]+)%\s+([\d.]+)\s+([\d.]+)%\s+(\d+)\s+(.+)', line)
                if m:
                    records.append({
                        "date": date_str,
                        "strategy": strategy_name,
                        "code": normalize_code(m.group(1)),
                        "price": float(m.group(2)),
                        "pct": float(m.group(3)),
                        "vol_ratio": float(m.group(4)),
                        "turn": float(m.group(5)),
                        "streak": 0,
                        "bias": 0,
                        "score": int(m.group(6)),
                        "tags": m.group(7).strip(),
                    })
                    continue
                
                # 格式5：V5双轨增强
                # sh.600497  8.94 6.56  2.25  85  爆发区|量能健康
                m = re.match(r'(sh\.\d{6}|sz\.\d{6})\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s+(.+)', line)
                if m:
                    # 检查是否已经被其他格式匹配过
                    records.append({
                        "date": date_str,
                        "strategy": strategy_name,
                        "code": normalize_code(m.group(1)),
                        "price": float(m.group(2)),
                        "pct": float(m.group(3)),
                        "vol_ratio": float(m.group(4)),
                        "turn": 0,
                        "streak": 0,
                        "bias": 0,
                        "score": int(m.group(5)),
                        "tags": m.group(6).strip(),
                    })
                    continue
                
                # 格式6：V6龙头法
                # sz.301667 100  108.52   2  1.74  2连板核心|缩量蓄势
                m = re.match(r'(sh\.\d{6}|sz\.\d{6})\s+(\d+)\s+([\d.]+)\s+(\d+)\s+([\d.]+)\s+(.+)', line)
                if m:
                    records.append({
                        "date": date_str,
                        "strategy": strategy_name,
                        "code": normalize_code(m.group(1)),
                        "price": float(m.group(3)),
                        "pct": 0,
                        "vol_ratio": float(m.group(5)),
                        "turn": 0,
                        "streak": int(m.group(4)),
                        "bias": 0,
                        "score": int(m.group(2)),
                        "tags": m.group(6).strip(),
                    })
                    continue
                
                # 格式7：V7 Omni
                # sh.603688  50.57 7.19% 1.61 7.57%  80  高位博弈 | 黄金放量 | 换手活跃
                m = re.match(r'(sh\.\d{6}|sz\.\d{6})\s+([\d.]+)\s+([\d.]+)%\s+([\d.]+)\s+([\d.]+)%\s+(\d+)\s+(.+)', line)
                if m:
                    records.append({
                        "date": date_str,
                        "strategy": strategy_name,
                        "code": normalize_code(m.group(1)),
                        "price": float(m.group(2)),
                        "pct": float(m.group(3)),
                        "vol_ratio": float(m.group(4)),
                        "turn": float(m.group(5)),
                        "streak": 0,
                        "bias": 0,
                        "score": int(m.group(6)),
                        "tags": m.group(7).strip(),
                    })
                    continue
                
                # 格式8：V9闭环系统
                # 688525 佰维存储 分数:75 仓位:10% 理由:趋势|强势|尾盘资金 价格:285.2
                m = re.search(r'(\d{6})\s+\S+\s+分数:(\d+)\s+仓位:(\d+)%\s+理由:(.+?)\s+价格:([\d.]+)', line)
                if m:
                    code = m.group(1)
                    if code.startswith("6"):
                        code = f"sh.{code}"
                    else:
                        code = f"sz.{code}"
                    records.append({
                        "date": date_str,
                        "strategy": strategy_name,
                        "code": code,
                        "price": float(m.group(5)),
                        "pct": 0,
                        "vol_ratio": 0,
                        "turn": 0,
                        "streak": 0,
                        "bias": 0,
                        "score": int(m.group(2)),
                        "tags": m.group(4).strip(),
                    })
                    continue

    return records


def fetch_stock_prices(codes: List[str], start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
    """批量获取股票价格数据"""
    lg = bs.login()
    if lg.error_code != "0":
        print(f"登录失败: {lg.error_msg}")
        return {}

    result = {}
    total = len(codes)
    for idx, code in enumerate(codes):
        try:
            if idx % 10 == 0:
                print(f"  进度: {idx}/{total}")
            
            rs = bs.query_history_k_data_plus(
                code, "date,close,pctChg",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="3"
            )
            if rs.error_code != "0":
                continue

            data = []
            while rs.next():
                data.append(rs.get_row_data())

            if data:
                df = pd.DataFrame(data, columns=rs.fields)
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                df["pctChg"] = pd.to_numeric(df["pctChg"], errors="coerce")
                result[code] = df
        except Exception as e:
            print(f"获取 {code} 数据失败: {e}")

    bs.logout()
    return result


def calculate_performance(records: List[Dict]) -> pd.DataFrame:
    """计算每个策略的性能指标"""
    # 按策略分组
    strategy_stats = defaultdict(lambda: {
        "total": 0,
        "success": 0,
        "fail": 0,
        "t1_returns": [],
        "t2_returns": [],
        "t3_returns": [],
    })

    for record in records:
        strategy = record["strategy"]
        stats = strategy_stats[strategy]
        stats["total"] += 1

        # T+1收益
        if "t1_return" in record:
            ret = record["t1_return"]
            stats["t1_returns"].append(ret)
            if ret >= SUCCESS_THRESHOLD:
                stats["success"] += 1
            else:
                stats["fail"] += 1

        # T+2收益
        if "t2_return" in record:
            stats["t2_returns"].append(record["t2_return"])

        # T+3收益
        if "t3_return" in record:
            stats["t3_returns"].append(record["t3_return"])

    # 计算统计指标
    results = []
    for strategy, stats in strategy_stats.items():
        t1_returns = stats["t1_returns"]
        t2_returns = stats["t2_returns"]
        t3_returns = stats["t3_returns"]

        results.append({
            "策略": strategy,
            "总推荐数": stats["total"],
            "T+1成功数": stats["success"],
            "T+1失败数": stats["fail"],
            "T+1成功率(%)": round(stats["success"] / len(t1_returns) * 100, 2) if t1_returns else 0,
            "T+1平均收益(%)": round(sum(t1_returns) / len(t1_returns), 2) if t1_returns else 0,
            "T+1最大收益(%)": round(max(t1_returns), 2) if t1_returns else 0,
            "T+1最小收益(%)": round(min(t1_returns), 2) if t1_returns else 0,
            "T+2平均收益(%)": round(sum(t2_returns) / len(t2_returns), 2) if t2_returns else 0,
            "T+3平均收益(%)": round(sum(t3_returns) / len(t3_returns), 2) if t3_returns else 0,
        })

    return pd.DataFrame(results)


def generate_report(records: List[Dict], strategy_df: pd.DataFrame, output_file: str):
    """生成回测报告"""
    lines = []
    lines.append("=" * 100)
    lines.append("隔夜选股策略回测报告")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 100)
    lines.append("")

    # 策略对比
    lines.append("一、策略性能对比")
    lines.append("-" * 100)
    lines.append(strategy_df.to_string(index=False))
    lines.append("")

    # 策略排名
    lines.append("二、策略排名（按T+1成功率）")
    lines.append("-" * 100)
    ranked = strategy_df.sort_values("T+1成功率(%)", ascending=False)
    for idx, (_, row) in enumerate(ranked.iterrows(), 1):
        lines.append(f"{idx}. {row['策略']}: 成功率 {row['T+1成功率(%)']}%, "
                     f"平均收益 {row['T+1平均收益(%)']}%, 推荐数 {row['总推荐数']}")
    lines.append("")

    # 建议
    lines.append("三、策略建议")
    lines.append("-" * 100)
    for _, row in ranked.iterrows():
        if row["T+1成功率(%)"] >= 60:
            lines.append(f"✅ {row['策略']}: 成功率优秀，建议保留")
        elif row["T+1成功率(%)"] >= 50:
            lines.append(f"⚠️ {row['策略']}: 成功率一般，建议优化")
        else:
            lines.append(f"❌ {row['策略']}: 成功率较低，建议舍弃")
    lines.append("")

    # 详细记录
    lines.append("四、详细推荐记录")
    lines.append("-" * 100)
    for record in records:
        t1 = record.get("t1_return", "N/A")
        t2 = record.get("t2_return", "N/A")
        t3 = record.get("t3_return", "N/A")
        lines.append(f"{record['date']} | {record['strategy']} | {record['code']} | "
                     f"推荐价 {record['price']} | T+1: {t1}% | T+2: {t2}% | T+3: {t3}%")
    lines.append("")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"报告已生成: {output_file}")


def main():
    print("=" * 80)
    print("隔夜选股策略回测分析系统 V2")
    print("=" * 80)

    # 1. 解析选股记录
    print("\n1. 解析选股记录汇总.txt...")
    records = parse_summary_file(SUMMARY_FILE)
    print(f"   共解析到 {len(records)} 条推荐记录")
    
    # 打印策略分布
    strategy_counts = defaultdict(int)
    for r in records:
        strategy_counts[r["strategy"]] += 1
    print("   策略分布:")
    for strat, count in sorted(strategy_counts.items()):
        print(f"     {strat}: {count} 条")

    # 2. 收集所有需要查询的股票和日期
    print("\n2. 收集需要查询的股票数据...")
    all_codes = set()
    all_dates = set()
    for record in records:
        all_codes.add(record["code"])
        all_dates.add(record["date"])
        # 添加后续交易日
        next_days = get_next_trading_days(record["date"], FOLLOW_UP_DAYS)
        all_dates.update(next_days)

    print(f"   共 {len(all_codes)} 只股票，{len(all_dates)} 个日期")
    print(f"   日期范围: {min(all_dates)} 至 {max(all_dates)}")

    # 3. 获取股票价格数据
    print("\n3. 获取股票价格数据...")
    start_date = min(all_dates)
    end_date = max(all_dates)
    price_data = fetch_stock_prices(list(all_codes), start_date, end_date)
    print(f"   获取到 {len(price_data)} 只股票的数据")

    # 4. 计算T+1、T+2、T+3收益
    print("\n4. 计算后续交易日收益...")
    success_count = 0
    for record in records:
        code = record["code"]
        date = record["date"]
        recommend_price = record["price"]

        if code not in price_data:
            continue

        df = price_data[code]
        df = df.sort_values("date")

        # 找到推荐日期的索引
        try:
            idx = df[df["date"] == date].index[0]
        except (IndexError, KeyError):
            continue

        # T+1收益
        if idx + 1 < len(df):
            t1_price = df.iloc[idx + 1]["close"]
            record["t1_return"] = round((t1_price - recommend_price) / recommend_price * 100, 2)
            record["t1_price"] = t1_price
            if record["t1_return"] >= SUCCESS_THRESHOLD:
                success_count += 1

        # T+2收益
        if idx + 2 < len(df):
            t2_price = df.iloc[idx + 2]["close"]
            record["t2_return"] = round((t2_price - recommend_price) / recommend_price * 100, 2)
            record["t2_price"] = t2_price

        # T+3收益
        if idx + 3 < len(df):
            t3_price = df.iloc[idx + 3]["close"]
            record["t3_return"] = round((t3_price - recommend_price) / recommend_price * 100, 2)
            record["t3_price"] = t3_price

    print(f"   成功计算 {success_count} 条T+1成功记录")

    # 5. 计算策略性能
    print("\n5. 计算策略性能指标...")
    strategy_df = calculate_performance(records)
    print("\n策略性能对比:")
    print(strategy_df.to_string(index=False))

    # 6. 生成报告
    print("\n6. 生成回测报告...")
    output_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "策略回测报告.txt"
    )
    generate_report(records, strategy_df, output_file)

    print("\n回测完成！")


if __name__ == "__main__":
    main()
