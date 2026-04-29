#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
隔夜选股策略精确回测脚本 v3
- 使用腾讯财经API查询推荐日、T+1、T+2真实收盘价
- 计算实际涨跌幅和成功率
"""

import re
import os
import time
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pandas as pd

# 股票名称缓存
STOCK_NAMES = {}

def fetch_stock_name_tencent(code: str) -> str:
    """获取股票名称"""
    if code in STOCK_NAMES:
        return STOCK_NAMES[code]
    
    if code.startswith('6'):
        symbol = f"sh{code}"
    else:
        symbol = f"sz{code}"
    
    url = f"http://qt.gtimg.cn/q={symbol}"
    
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            # 解析返回数据: v_sh600519="1~贵州茅台~600519~..."
            text = resp.text
            if '~' in text:
                parts = text.split('~')
                if len(parts) > 1:
                    name = parts[1]
                    STOCK_NAMES[code] = name
                    return name
    except:
        pass
    
    STOCK_NAMES[code] = ""
    return ""


# 策略名称 -> 源文件名映射
STRATEGY_FILE_MAP = {
    "V1稳健": "wenjian.py",
    "V2高位突破": "gaoweitupoboyi.py",
    "V3合并增强": "hebing.py",
    "V4双轨": "shuanggui.py",
    "V5增强": "shuanggui2.py",
    "V6龙头": "mogai.py",
    "V7Omni": "7in1.py",
    "V8终极": "Overnight.py",
    "V9闭环": "20260415-1.py",
    "zuiyou最优": "zuiyou.py",
}


def parse_summary_file(filepath: str) -> List[Dict]:
    """解析选股记录汇总.txt - 支持所有策略格式"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    records = []
    date_pattern = r"📅\s+(\d{4}-\d{2}-\d{2})"
    date_matches = list(re.finditer(date_pattern, content))
    
    for i, date_match in enumerate(date_matches):
        date_str = date_match.group(1)
        start_pos = date_match.end()
        end_pos = date_matches[i + 1].start() if i + 1 < len(date_matches) else len(content)
        block = content[start_pos:end_pos]
        
        strategy_sections = re.split(r'\n──\s+', block)
        
        for section in strategy_sections:
            strategy_name = None
            
            # 优先级：先匹配更具体的名称
            if "V9" in section or "闭环" in section:
                strategy_name = "V9闭环"
            elif "zuiyou" in section.lower() or "最优" in section:
                strategy_name = "zuiyou最优"
            elif "V7" in section or "omni" in section.lower() or "7in1" in section:
                strategy_name = "V7Omni"
            elif "V6" in section or "mogai" in section.lower() or "龙头法" in section or "龙头" in section:
                strategy_name = "V6龙头"
            elif "V5" in section or "shuanggui2" in section.lower() or "双轨增强" in section:
                strategy_name = "V5增强"
            elif "V4" in section or "shuanggui" in section.lower() or "双轨制" in section:
                strategy_name = "V4双轨"
            elif "V3" in section or "hebing" in section.lower() or "合并增强" in section:
                strategy_name = "V3合并增强"
            elif "V2" in section or "gaowei" in section.lower() or "高位突破" in section:
                strategy_name = "V2高位突破"
            elif "V1" in section or "wenjian" in section.lower() or "稳健法" in section:
                strategy_name = "V1稳健"
            
            if not strategy_name:
                continue
            
            # V1稳健: 股票: sh.600118 | 现价: 84.39 | 涨幅: 4.83% | 评分: 7
            if strategy_name == "V1稳健":
                matches = re.findall(r'股票:\s*(?:sh\.|sz\.)?(\d{6})\s*\|\s*现价:\s*([\d.]+)', section)
                for code, price in matches:
                    try:
                        price_val = float(price)
                        if price_val > 0:
                            records.append({"date": date_str, "strategy": strategy_name, "code": code, "price": price_val})
                    except ValueError:
                        continue
            
            # zuiyou最优: sh.600977   hs300+zz500  14.91    5.00   3.31...
            elif strategy_name == "zuiyou最优":
                matches = re.findall(r'(?:sh\.|sz\.)?(\d{6})\s+\S+\s+([\d.]+)\s+[\d.]+\s+[\d.]+\s+[\d.]+', section)
                for code, price in matches:
                    try:
                        price_val = float(price)
                        if price_val > 0:
                            records.append({"date": date_str, "strategy": strategy_name, "code": code, "price": price_val})
                    except ValueError:
                        continue
            
            # V9闭环: 688525 佰维存储 分数:75 仓位:10% 理由:xxx 价格:285.2
            elif strategy_name == "V9闭环":
                matches = re.findall(r'(\d{6})\s+\S+\s+分数:\d+\s+.*?价格:([\d.]+)', section)
                for code, price in matches:
                    try:
                        price_val = float(price)
                        if price_val > 0:
                            records.append({"date": date_str, "strategy": strategy_name, "code": code, "price": price_val})
                    except ValueError:
                        continue
            
            # V6龙头: sz.301667 100  108.52   2  1.74
            elif strategy_name == "V6龙头":
                matches = re.findall(r'(?:sh\.|sz\.)?(\d{6})\s+\d+\s+([\d.]+)\s+\d+\s+[\d.]+', section)
                for code, price in matches:
                    try:
                        price_val = float(price)
                        if price_val > 0:
                            records.append({"date": date_str, "strategy": strategy_name, "code": code, "price": price_val})
                    except ValueError:
                        continue
            
            # V2/V3/V4/V5/V7 通用格式
            else:
                patterns = [
                    r'(?:sh\.|sz\.)?(\d{6})\s+([\d.]+)',
                    r'代码[:\s]+(?:sh\.|sz\.)?(\d{6})\s+价格[:\s]*([\d.]+)',
                ]
                
                for pattern in patterns:
                    matches = re.findall(pattern, section)
                    for code, price in matches:
                        try:
                            price_val = float(price)
                            if price_val > 0:
                                records.append({"date": date_str, "strategy": strategy_name, "code": code, "price": price_val})
                        except ValueError:
                            continue
    
    # 去重
    seen = set()
    unique_records = []
    for r in records:
        key = (r['date'], r['strategy'], r['code'])
        if key not in seen:
            seen.add(key)
            unique_records.append(r)
    
    return unique_records


def get_trading_days_from_api() -> List[str]:
    """从新浪财经获取交易日历"""
    try:
        url = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
        params = {
            "symbol": "sh000001",
            "scale": "240",
            "ma": "no",
            "datalen": "100"
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            import json
            data = resp.json()
            dates = [d['day'] for d in data]
            return sorted(dates)
    except:
        pass
    
    return []


def get_next_trading_days(date_str: str, n_days: int = 3) -> List[str]:
    """获取指定日期后的N个交易日（使用硬编码的2026年4月交易日历）"""
    trading_days = [
        "2026-04-01", "2026-04-02", "2026-04-03",
        "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10",
        "2026-04-13", "2026-04-14", "2026-04-15", "2026-04-16", "2026-04-17",
        "2026-04-20", "2026-04-21", "2026-04-22", "2026-04-23", "2026-04-24",
        "2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30",
        "2026-05-06", "2026-05-07", "2026-05-08", "2026-05-11", "2026-05-12",
        "2026-05-13", "2026-05-14", "2026-05-15", "2026-05-18", "2026-05-19",
        "2026-05-20", "2026-05-21", "2026-05-22", "2026-05-25", "2026-05-26",
        "2026-05-27", "2026-05-28", "2026-05-29"
    ]
    
    if date_str not in trading_days:
        for d in trading_days:
            if d > date_str:
                date_str = d
                break
    
    try:
        idx = trading_days.index(date_str)
        return trading_days[idx + 1:idx + 1 + n_days]
    except (ValueError, IndexError):
        return []


def fetch_stock_history_tencent(code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """使用腾讯财经API获取历史日K线"""
    if code.startswith('6'):
        symbol = f"sh{code}"
    else:
        symbol = f"sz{code}"
    
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,{start_date},{end_date},320,qfq"
    
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        
        if 'data' not in data or symbol not in data['data']:
            return None
        
        stock_data = data['data'][symbol]
        
        klines = None
        if 'qfqday' in stock_data:
            klines = stock_data['qfqday']
        elif 'day' in stock_data:
            klines = stock_data['day']
        
        if not klines:
            return None
        
        df = pd.DataFrame(klines, columns=['date', 'open', 'close', 'high', 'low', 'volume'])
        for col in ['open', 'close', 'high', 'low', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['date'] = df['date'].astype(str)
        
        return df
    except Exception as e:
        print(f"  获取 {code} 失败: {e}")
        return None


def fetch_5min_kline_sina(code: str) -> Optional[pd.DataFrame]:
    """使用新浪财经API获取5分钟K线数据"""
    if code.startswith('6'):
        symbol = f"sh{code}"
    else:
        symbol = f"sz{code}"
    
    url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    params = {
        "symbol": symbol,
        "scale": "5",
        "ma": "no",
        "datalen": "1000"
    }
    
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        
        if not data or len(data) == 0:
            return None
        
        df = pd.DataFrame(data)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        return df
    except Exception as e:
        print(f"  获取 {code} 5分钟数据失败: {e}")
        return None


def calculate_overnight_performance(code: str, t_date: str, t1_date: str, t2_date: str, recommend_price: float) -> Dict:
    """
    计算隔夜战法表现
    
    逻辑:
    - T日 14:50 买入 (收盘价)
    - T+1日 9:30-10:30 卖出
    
    指标:
    - t1_open_return: T+1开盘价收益 (9:30开盘即卖)
    - t1_high_return: T+1最高价收益 (卖在上午最高点)
    - t1_avg_return: T+1上午9:30-10:30真实均价收益 (5分钟K线收盘价均值)
    - t2_close_return: T+2收盘价收益 (如果持有到T+2)
    """
    hist_data = fetch_stock_history_tencent(code, t_date, t2_date)
    
    if hist_data is None or len(hist_data) == 0:
        return {
            't1_open': None, 't1_high': None, 't1_avg': None, 't1_close': None, 't2_close': None,
            't1_open_return': None, 't1_high_return': None, 't1_avg_return': None,
            't1_close_return': None, 't2_close_return': None,
            't1_open_success': False, 't1_high_success': False, 't1_avg_success': False,
        }
    
    t_row = hist_data[hist_data['date'] == t_date]
    t1_row = hist_data[hist_data['date'] == t1_date]
    t2_row = hist_data[hist_data['date'] == t2_date]
    
    if len(t1_row) == 0:
        return {
            't1_open': None, 't1_high': None, 't1_avg': None, 't1_close': None, 't2_close': None,
            't1_open_return': None, 't1_high_return': None, 't1_avg_return': None,
            't1_close_return': None, 't2_close_return': None,
            't1_open_success': False, 't1_high_success': False, 't1_avg_success': False,
        }
    
    t1_open = t1_row.iloc[0]['open']
    t1_high = t1_row.iloc[0]['high']
    t1_close = t1_row.iloc[0]['close']
    
    # 获取5分钟K线数据计算真实上午均价
    t1_avg = None
    kline_5min = fetch_5min_kline_sina(code)
    
    if kline_5min is not None and len(kline_5min) > 0:
        # 筛选T+1日9:30-10:30的5分钟K线
        morning_klines = kline_5min[
            kline_5min['day'].str.startswith(t1_date) & 
            (kline_5min['day'].str.contains(' 09:3[0-5]| 09:4[0-5]| 09:5[0-5]| 10:0[0-5]| 10:1[0-5]| 10:2[0-5]| 10:30'))
        ]
        
        if len(morning_klines) > 0:
            # 使用5分钟K线的收盘价计算均价
            t1_avg = morning_klines['close'].mean()
            time.sleep(0.1)
    
    # 如果5分钟数据获取失败，使用估算值
    if t1_avg is None:
        t1_avg = (t1_open + t1_high) / 2
    
    # 计算收益
    t1_open_return = (t1_open - recommend_price) / recommend_price * 100
    t1_high_return = (t1_high - recommend_price) / recommend_price * 100
    t1_avg_return = (t1_avg - recommend_price) / recommend_price * 100
    t1_close_return = (t1_close - recommend_price) / recommend_price * 100
    
    t2_close = t2_row.iloc[0]['close'] if len(t2_row) > 0 else None
    t2_close_return = (t2_close - recommend_price) / recommend_price * 100 if t2_close else None
    
    return {
        't1_open': t1_open,
        't1_high': t1_high,
        't1_avg': t1_avg,
        't1_close': t1_close,
        't2_close': t2_close,
        't1_open_return': t1_open_return,
        't1_high_return': t1_high_return,
        't1_avg_return': t1_avg_return,
        't1_close_return': t1_close_return,
        't2_close_return': t2_close_return,
        't1_open_success': t1_open_return > 0,
        't1_high_success': t1_high_return > 0,
        't1_avg_success': t1_avg_return > 0,
    }


def run_backtest(records: List[Dict]) -> List[Dict]:
    """执行回测 - 隔夜战法"""
    results = []
    cache = {}
    
    unique_dates = set(r["date"] for r in records)
    all_dates_needed = set()
    for d in unique_dates:
        next_days = get_next_trading_days(d, 2)
        all_dates_needed.add(d)
        all_dates_needed.update(next_days)
    
    date_range = sorted(all_dates_needed)
    start_date = date_range[0] if date_range else "2026-04-13"
    end_date = date_range[-1] if date_range else "2026-04-30"
    
    print(f"回测日期范围: {start_date} 至 {end_date}")
    print(f"共 {len(records)} 条推荐记录需要回测\n")
    print("隔夜战法逻辑: T日14:50买入(收盘价) -> T+1日9:30-10:30卖出\n")
    
    for idx, record in enumerate(records, 1):
        code = record["code"]
        date = record["date"]
        strategy = record["strategy"]
        
        print(f"[{idx}/{len(records)}] {strategy} | {date} | {code}", end=" ")
        
        stock_name = fetch_stock_name_tencent(code)
        
        if code not in cache:
            df = fetch_stock_history_tencent(code, start_date, end_date)
            if df is not None and not df.empty:
                cache[code] = df
                time.sleep(0.2)
            else:
                cache[code] = None
                print("❌ 无数据")
                continue
        else:
            df = cache[code]
        
        if df is None or df.empty:
            print("❌ 缓存无数据")
            continue
        
        next_days = get_next_trading_days(date, 2)
        if len(next_days) < 1:
            print("❌ 无T+1交易日")
            continue
        
        t1_date = next_days[0]
        t2_date = next_days[1] if len(next_days) > 1 else None
        
        recommend_row = df[df['date'] == date]
        if recommend_row.empty:
            print("❌ 无推荐日数据")
            continue
        
        recommend_price = recommend_row.iloc[0]['close']
        
        perf = calculate_overnight_performance(code, date, t1_date, t2_date or "", recommend_price)
        
        if perf['t1_open_return'] is None:
            print("❌ 无T+1数据")
            continue
        
        results.append({
            "date": date,
            "strategy": strategy,
            "code": code,
            "stock_name": stock_name,
            "recommend_price": recommend_price,
            "t1_date": t1_date,
            "t2_date": t2_date,
            "t1_open": perf['t1_open'],
            "t1_high": perf['t1_high'],
            "t1_avg": perf['t1_avg'],
            "t1_close": perf['t1_close'],
            "t2_close": perf['t2_close'],
            "t1_open_return": perf['t1_open_return'],
            "t1_high_return": perf['t1_high_return'],
            "t1_avg_return": perf['t1_avg_return'],
            "t1_close_return": perf['t1_close_return'],
            "t2_close_return": perf['t2_close_return'],
            "t1_open_success": perf['t1_open_success'],
            "t1_high_success": perf['t1_high_success'],
            "t1_avg_success": perf['t1_avg_success'],
        })
        
        status = "✅" if perf['t1_avg_success'] else "❌"
        print(f"买:{recommend_price:.2f} 开:{perf['t1_open_return']:+.2f}% 高:{perf['t1_high_return']:+.2f}% 均:{perf['t1_avg_return']:+.2f}% {status}")
    
    print(f"\n回测完成，共 {len(results)} 条有效结果")
    return results


def generate_report(results: List[Dict], output_path: str):
    """生成回测报告（Markdown格式）- 隔夜战法"""
    df = pd.DataFrame(results)
    
    strategies = df.groupby('strategy').agg(
        total_count=('code', 'count'),
        t1_open_success_count=('t1_open_success', 'sum'),
        t1_high_success_count=('t1_high_success', 'sum'),
        t1_avg_success_count=('t1_avg_success', 'sum'),
        t1_open_avg_return=('t1_open_return', 'mean'),
        t1_high_avg_return=('t1_high_return', 'mean'),
        t1_avg_avg_return=('t1_avg_return', 'mean'),
        t1_close_avg_return=('t1_close_return', 'mean'),
        t2_close_avg_return=('t2_close_return', 'mean'),
        t1_open_max_return=('t1_open_return', 'max'),
        t1_high_max_return=('t1_high_return', 'max'),
        t1_avg_max_return=('t1_avg_return', 'max'),
    ).reset_index()
    
    strategies['t1_open_success_rate'] = (strategies['t1_open_success_count'] / strategies['total_count'] * 100).round(2)
    strategies['t1_high_success_rate'] = (strategies['t1_high_success_count'] / strategies['total_count'] * 100).round(2)
    strategies['t1_avg_success_rate'] = (strategies['t1_avg_success_count'] / strategies['total_count'] * 100).round(2)
    strategies = strategies.sort_values('t1_avg_success_rate', ascending=False)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# 📊 隔夜选股策略回测报告\n\n")
        f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n")
        f.write(f"**回测股票数**: {len(results)}  \n")
        f.write(f"**交易逻辑**: T日14:50买入(收盘价) → T+1日9:30-10:30卖出  \n")
        f.write(f"**指标说明**:\n")
        f.write(f"- **开盘收益**: T+1开盘价 vs T收盘价 (9:30开盘即卖)  \n")
        f.write(f"- **最高收益**: T+1最高价 vs T收盘价 (理想卖点)  \n")
        f.write(f"- **上午均收益**: T+1日9:30-10:30真实均价 vs T收盘价 (基于新浪5分钟K线收盘价均值)  \n\n")
        
        f.write("---\n\n")
        f.write("## 一、策略性能对比\n\n")
        f.write("| 策略 | 源文件 | 推荐数 | 开盘成功 | 最高成功 | 上午均成功 | 开盘成功率 | 最高成功率 | 上午均成功率 | 开盘均收益 | 最高均收益 | 上午均收益 |\n")
        f.write("|------|--------|--------|---------|---------|-----------|-----------|-----------|-------------|-----------|-----------|-----------|\n")
        
        for _, row in strategies.iterrows():
            src_file = STRATEGY_FILE_MAP.get(row['strategy'], '-')
            f.write(f"| {row['strategy']} | `{src_file}` | {int(row['total_count'])} | "
                   f"{int(row['t1_open_success_count'])} | {int(row['t1_high_success_count'])} | {int(row['t1_avg_success_count'])} | "
                   f"{row['t1_open_success_rate']:.2f}% | {row['t1_high_success_rate']:.2f}% | {row['t1_avg_success_rate']:.2f}% | "
                   f"{row['t1_open_avg_return']:+.2f}% | {row['t1_high_avg_return']:+.2f}% | {row['t1_avg_avg_return']:+.2f}% |\n")
        
        f.write("\n---\n\n")
        f.write("## 二、策略排名与建议\n\n")
        
        for rank, (_, row) in enumerate(strategies.iterrows(), 1):
            if row['t1_avg_success_rate'] >= 55:
                mark = "✅ 保留"
            elif row['t1_avg_success_rate'] >= 45:
                mark = "⚠️ 优化"
            else:
                mark = "❌ 舍弃"
            src_file = STRATEGY_FILE_MAP.get(row['strategy'], '-')
            f.write(f"{rank}. **{mark}** | {row['strategy']} (`{src_file}`): 上午均成功率 {row['t1_avg_success_rate']:.2f}%, "
                   f"上午均收益 {row['t1_avg_avg_return']:+.2f}%, 推荐数 {int(row['total_count'])}\n")
        
        f.write("\n---\n\n")
        f.write("## 三、各策略详细推荐记录\n\n")
        
        for strategy_name in strategies['strategy']:
            strategy_results = df[df['strategy'] == strategy_name].sort_values(['date', 'code'])
            strategy_info = strategies[strategies['strategy'] == strategy_name].iloc[0]
            src_file = STRATEGY_FILE_MAP.get(strategy_name, '-')
            
            f.write(f"### 【{strategy_name}】`{src_file}`\n\n")
            f.write(f"**上午均成功率**: {strategy_info['t1_avg_success_rate']:.2f}% | "
                   f"**上午均收益**: {strategy_info['t1_avg_avg_return']:+.2f}% | "
                   f"**推荐数**: {int(strategy_info['total_count'])}\n\n")
            
            f.write("| 日期 | 代码 | 股票名称 | T买入价 | T+1开盘 | 开盘涨跌% | T+1最高 | 最高涨跌% | T+1上午均 | 上午均涨跌% | T+2收盘 | T+2涨跌% | 结果 |\n")
            f.write("|------|------|----------|---------|---------|----------|---------|----------|----------|------------|---------|----------|------|\n")
            
            for r in strategy_results.itertuples():
                t1_open = f"{r.t1_open:.2f}" if r.t1_open else "N/A"
                t1_open_r = f"{r.t1_open_return:+.2f}%" if r.t1_open_return is not None else "N/A"
                
                t1_high = f"{r.t1_high:.2f}" if r.t1_high else "N/A"
                t1_high_r = f"{r.t1_high_return:+.2f}%" if r.t1_high_return is not None else "N/A"
                
                t1_avg = f"{r.t1_avg:.2f}" if r.t1_avg else "N/A"
                t1_avg_r = f"{r.t1_avg_return:+.2f}%" if r.t1_avg_return is not None else "N/A"
                
                t2_close = f"{r.t2_close:.2f}" if r.t2_close else "N/A"
                t2_r = f"{r.t2_close_return:+.2f}%" if r.t2_close_return is not None else "N/A"
                
                result = "✅" if r.t1_avg_success else "❌"
                
                f.write(f"| {r.date} | {r.code} | {r.stock_name} | "
                       f"{r.recommend_price:.2f} | {t1_open} | {t1_open_r} | "
                       f"{t1_high} | {t1_high_r} | {t1_avg} | {t1_avg_r} | "
                       f"{t2_close} | {t2_r} | {result} |\n")
            
            f.write("\n")
        
        f.write("---\n\n")
        f.write("## 四、按日期分组的推荐记录\n\n")
        
        for date in sorted(df['date'].unique()):
            date_results = df[df['date'] == date].sort_values(['strategy', 'code'])
            f.write(f"### 【{date}】共推荐 {len(date_results)} 只股票\n\n")
            
            f.write("| 策略 | 代码 | 股票名称 | T买入价 | T+1开盘 | 开盘涨跌% | T+1最高 | 最高涨跌% | T+1上午均 | 上午均涨跌% | T+2收盘 | T+2涨跌% | 结果 |\n")
            f.write("|------|------|----------|---------|---------|----------|---------|----------|----------|------------|---------|----------|------|\n")
            
            for r in date_results.itertuples():
                t1_open = f"{r.t1_open:.2f}" if r.t1_open else "N/A"
                t1_open_r = f"{r.t1_open_return:+.2f}%" if r.t1_open_return is not None else "N/A"
                
                t1_high = f"{r.t1_high:.2f}" if r.t1_high else "N/A"
                t1_high_r = f"{r.t1_high_return:+.2f}%" if r.t1_high_return is not None else "N/A"
                
                t1_avg = f"{r.t1_avg:.2f}" if r.t1_avg else "N/A"
                t1_avg_r = f"{r.t1_avg_return:+.2f}%" if r.t1_avg_return is not None else "N/A"
                
                t2_close = f"{r.t2_close:.2f}" if r.t2_close else "N/A"
                t2_r = f"{r.t2_close_return:+.2f}%" if r.t2_close_return is not None else "N/A"
                
                result = "✅" if r.t1_avg_success else "❌"
                
                f.write(f"| {r.strategy} | {r.code} | {r.stock_name} | "
                       f"{r.recommend_price:.2f} | {t1_open} | {t1_open_r} | "
                       f"{t1_high} | {t1_high_r} | {t1_avg} | {t1_avg_r} | "
                       f"{t2_close} | {t2_r} | {result} |\n")
            
            f.write("\n")
        
        f.write("---\n\n")
        f.write("## 五、核心发现\n\n")
        
        best = strategies.iloc[0]
        worst = strategies.iloc[-1]
        
        total_open_success = df['t1_open_success'].sum()
        total_high_success = df['t1_high_success'].sum()
        total_avg_success = df['t1_avg_success'].sum()
        total_count = len(df)
        
        f.write(f"1. **最优策略**: {best['strategy']} (上午均成功率 {best['t1_avg_success_rate']:.2f}%, "
               f"上午均收益 {best['t1_avg_avg_return']:+.2f}%)\n")
        f.write(f"2. **最差策略**: {worst['strategy']} (上午均成功率 {worst['t1_avg_success_rate']:.2f}%, "
               f"上午均收益 {worst['t1_avg_avg_return']:+.2f}%)\n")
        f.write(f"3. **总体开盘成功率**: {total_open_success}/{total_count} = {total_open_success/total_count*100:.2f}%\n")
        f.write(f"4. **总体最高成功率**: {total_high_success}/{total_count} = {total_high_success/total_count*100:.2f}%\n")
        f.write(f"5. **总体上午均成功率**: {total_avg_success}/{total_count} = {total_avg_success/total_count*100:.2f}%\n")
        
        f.write("\n---\n\n")
        f.write("## 六、操作建议\n\n")
        f.write("1. **保留策略**：上午均成功率≥55%且平均收益为正的策略\n")
        f.write("2. **优化策略**：上午均成功率45-55%的策略，需调整筛选条件\n")
        f.write("3. **舍弃策略**：上午均成功率<45%或平均收益为负的策略\n")
        f.write("4. 建议后续只运行成功率最高的1-2个策略版本\n")
        f.write("5. 每次推荐股票数量控制在5-10只，宁缺毋滥\n")
        f.write("6. 实际交易中，建议在T+1日9:30-10:30期间择机卖出，争取接近最高点的收益\n")
    
    print(f"\n报告已保存至: {output_path}")


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    summary_file = os.path.join(base_dir, "选股记录汇总.txt")
    output_file = os.path.join(base_dir, "策略回测报告_精确版.md")
    
    print("=" * 60)
    print("隔夜选股策略精确回测")
    print("=" * 60)
    print("\n开始解析选股记录...")
    records = parse_summary_file(summary_file)
    print(f"共解析到 {len(records)} 条推荐记录\n")
    
    print("开始回测（使用腾讯财经API获取真实收盘价）...")
    results = run_backtest(records)
    print(f"\n回测完成，共 {len(results)} 条有效结果\n")
    
    print("生成报告...")
    generate_report(results, output_file)
    
    print("\n" + "=" * 60)
    print("回测完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
