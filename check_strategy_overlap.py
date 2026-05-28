"""
检查各策略的数据覆盖情况
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
    
    # 查询各策略的日期分布
    cur.execute("""
        SELECT source, COUNT(DISTINCT snapshot_date) as days, COUNT(*) as recs,
               MIN(snapshot_date) as min_date, MAX(snapshot_date) as max_date
        FROM daily_candidates
        WHERE snapshot_date >= '2026-01-01' AND snapshot_date <= '2026-05-27'
        GROUP BY source
        ORDER BY source
    """)
    
    print("=" * 100)
    print("  各策略数据覆盖情况")
    print("=" * 100)
    print(f"  {'策略':>20} | {'天数':>6} | {'推荐数':>8} | {'最早日期':>12} | {'最晚日期':>12}")
    print(f"  {'-'*20}-+-{'-'*6}-+-{'-'*8}-+-{'-'*12}-+-{'-'*12}")
    
    strategy_dates = {}
    for row in cur.fetchall():
        source, days, recs, min_date, max_date = row
        print(f"  {source:>20} | {days:>6} | {recs:>8} | {str(min_date):>12} | {str(max_date):>12}")
        strategy_dates[source] = set()
    
    # 查询各策略的具体日期
    cur.execute("""
        SELECT DISTINCT source, snapshot_date
        FROM daily_candidates
        WHERE snapshot_date >= '2026-01-01' AND snapshot_date <= '2026-05-27'
        ORDER BY source, snapshot_date
    """)
    
    for row in cur.fetchall():
        source, snapshot_date = row
        strategy_dates[source].add(snapshot_date)
    
    cur.close()
    conn.close()
    
    # 检查两两重合
    strategies = list(strategy_dates.keys())
    print("\n" + "=" * 100)
    print("  策略间重合日期统计")
    print("=" * 100)
    
    for i in range(len(strategies)):
        for j in range(i+1, len(strategies)):
            s1, s2 = strategies[i], strategies[j]
            overlap = strategy_dates[s1] & strategy_dates[s2]
            print(f"\n  {s1} ∩ {s2}: {len(overlap)} 天")
            if overlap:
                print(f"  最早: {min(overlap)}, 最晚: {max(overlap)}")
                # 显示前 10 天
                sorted_dates = sorted(overlap)[:10]
                print(f"  前 10 天: {', '.join(str(d) for d in sorted_dates)}")
    
    # 三个策略的交集
    if len(strategies) >= 3:
        all_overlap = strategy_dates[strategies[0]] & strategy_dates[strategies[1]] & strategy_dates[strategies[2]]
        print(f"\n  三策略交集: {len(all_overlap)} 天")
        if all_overlap:
            print(f"  日期: {', '.join(str(d) for d in sorted(all_overlap))}")
    
    # 检查任意两个策略重合的日期和股票
    print("\n" + "=" * 100)
    print("  两两策略重合的股票详情")
    print("=" * 100)
    
    conn = get_db_fresh()
    cur = conn.cursor()
    
    for i in range(len(strategies)):
        for j in range(i+1, len(strategies)):
            s1, s2 = strategies[i], strategies[j]
            overlap_dates = strategy_dates[s1] & strategy_dates[s2]
            
            if not overlap_dates:
                continue
            
            print(f"\n  [OVERLAP] {s1} ∩ {s2} ({len(overlap_dates)} 天)")
            print(f"  {'-'*100}")
            
            for trade_date in sorted(overlap_dates):
                # 获取两个策略当天的推荐
                cur.execute("""
                    SELECT ts_code, stock_name, source, final_score
                    FROM daily_candidates
                    WHERE snapshot_date = %s AND source IN (%s, %s)
                    ORDER BY source, final_score DESC
                """, (trade_date, s1, s2))
                
                recs = cur.fetchall()
                codes_by_source = defaultdict(set)
                code_info = {}
                for code, name, source, score in recs:
                    codes_by_source[source].add(code)
                    code_info[code] = {'name': name or '', 'scores': {}}
                    code_info[code]['scores'][source] = score
                
                overlap_codes = codes_by_source[s1] & codes_by_source[s2]
                
                if overlap_codes:
                    print(f"\n    [DATE] {trade_date} ({len(overlap_codes)} 只重合)")
                    print(f"    {'代码':>12} | {'名称':>8} | {s1:>16} | {s2:>16}")
                    print(f"    {'-'*12}-+-{'-'*8}-+-{'-'*16}-+-{'-'*16}")
                    for code in sorted(overlap_codes):
                        info = code_info[code]
                        s1_score = f"{info['scores'].get(s1, 0):.2f}"
                        s2_score = f"{info['scores'].get(s2, 0):.2f}"
                        print(f"    {code:>12} | {info['name']:>8} | {s1_score:>16} | {s2_score:>16}")
    
    cur.close()
    conn.close()


if __name__ == '__main__':
    main()
