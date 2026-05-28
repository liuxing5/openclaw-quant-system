"""
查找三个策略在同一天推荐的重合股票
"""
from __future__ import annotations

import os
import sys
from datetime import date
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.utils.env import load_project_env
from core.db.connection import get_db_fresh

load_project_env()


def main():
    conn = get_db_fresh()
    cur = conn.cursor()
    
    # 查询所有策略的推荐数据
    cur.execute("""
        SELECT snapshot_date, ts_code, stock_name, source, final_score
        FROM daily_candidates
        WHERE snapshot_date >= '2026-01-01' AND snapshot_date <= '2026-05-27'
        ORDER BY snapshot_date, source, final_score DESC
    """)
    
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    # 按日期分组
    date_groups = defaultdict(lambda: defaultdict(list))
    for row in rows:
        snapshot_date, ts_code, stock_name, source, final_score = row
        date_groups[snapshot_date][source].append({
            'code': ts_code,
            'name': stock_name or '',
            'score': final_score
        })
    
    # 找出三个策略都推荐的日期
    strategies = ['overnight_8step', 'llm_multisource', 'funnel_strategy']
    
    overlap_dates = []
    
    for trade_date in sorted(date_groups.keys()):
        day_recs = date_groups[trade_date]
        
        # 检查三个策略是否都有推荐
        if all(s in day_recs for s in strategies):
            # 找出重合的股票
            codes_by_strategy = {s: set(r['code'] for r in day_recs[s]) for s in strategies}
            
            # 三个策略的交集
            overlap_codes = codes_by_strategy['overnight_8step'] & codes_by_strategy['llm_multisource'] & codes_by_strategy['funnel_strategy']
            
            if overlap_codes:
                # 获取股票信息
                overlap_info = []
                for code in overlap_codes:
                    # 从任意策略获取股票名称
                    name = ''
                    scores = {}
                    for s in strategies:
                        for r in day_recs[s]:
                            if r['code'] == code:
                                name = r['name']
                                scores[s] = r['score']
                                break
                    overlap_info.append({
                        'code': code,
                        'name': name,
                        'scores': scores
                    })
                
                overlap_dates.append({
                    'date': trade_date,
                    'stocks': overlap_info
                })
    
    # 输出结果
    print("=" * 120)
    print(f"  三策略重合推荐统计")
    print("=" * 120)
    print(f"\n  总交易日: {len(date_groups)} 天")
    print(f"  三策略都有推荐的日期: {len(overlap_dates)} 天")
    print(f"  有重合股票的日期: {len(overlap_dates)} 天\n")
    
    for item in overlap_dates:
        print(f"  📅 {item['date']} ({len(item['stocks'])} 只重合)")
        print(f"  {'代码':>12} | {'名称':>8} | {'overnight_8step':>16} | {'llm_multisource':>16} | {'funnel_strategy':>16}")
        print(f"  {'-'*12}-+-{'-'*8}-+-{'-'*16}-+-{'-'*16}-+-{'-'*16}")
        for stock in sorted(item['stocks'], key=lambda x: x['code']):
            s1 = f"{stock['scores'].get('overnight_8step', 0):.2f}"
            s2 = f"{stock['scores'].get('llm_multisource', 0):.2f}"
            s3 = f"{stock['scores'].get('funnel_strategy', 0):.2f}"
            print(f"  {stock['code']:>12} | {stock['name']:>8} | {s1:>16} | {s2:>16} | {s3:>16}")
        print()
    
    # 统计重合股票出现频率
    stock_freq = defaultdict(int)
    for item in overlap_dates:
        for stock in item['stocks']:
            stock_freq[stock['code']] += 1
    
    if stock_freq:
        print("=" * 80)
        print(f"  重合股票出现频率 TOP 20")
        print("=" * 80)
        print(f"  {'代码':>12} | {'出现次数':>8}")
        print(f"  {'-'*12}-+-{'-'*8}")
        for code, freq in sorted(stock_freq.items(), key=lambda x: x[1], reverse=True)[:20]:
            print(f"  {code:>12} | {freq:>8}")


if __name__ == '__main__':
    main()
