"""
输出每笔交易明细
================
基于 daily_candidates 表的推荐数据，输出每个策略的每笔交易详情。
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.utils.env import load_project_env
from core.db.connection import get_db_fresh
from core.utils.trading_calendar import get_trading_days_in_range

load_project_env()

INITIAL_CAPITAL = 100_000
COMMISSION_RATE = 0.00025
STAMP_TAX_RATE = 0.001
MIN_LOTS = 100
HOLD_DAYS = 1


def fetch_candidates(start: date, end: date, strategy: str) -> List[Dict]:
    conn = get_db_fresh()
    cur = conn.cursor()
    cur.execute("""
        SELECT snapshot_date, ts_code, stock_name, final_score
        FROM daily_candidates
        WHERE snapshot_date >= %s AND snapshot_date <= %s AND source = %s
        ORDER BY snapshot_date, final_score DESC
    """, (start, end, strategy))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = []
    for row in rows:
        result.append({
            'snapshot_date': row[0],
            'ts_code': row[1],
            'stock_name': row[2] or '',
            'final_score': row[3]
        })
    return result


def fetch_prices_for_codes(codes: List[str], dates: List[date]) -> Dict[str, Dict[date, Dict]]:
    if not codes or not dates:
        return {}
    result = {}
    conn = get_db_fresh()
    cur = conn.cursor()
    date_strs = [d.strftime('%Y-%m-%d') for d in dates]
    for i in range(0, len(codes), 100):
        batch_codes = codes[i:i+100]
        placeholders = ','.join(['%s'] * len(batch_codes))
        date_placeholders = ','.join(['%s'] * len(date_strs))
        query = f"""
            SELECT ts_code, trade_date, open, close, high, low
            FROM daily_quotes
            WHERE ts_code IN ({placeholders})
              AND trade_date IN ({date_placeholders})
        """
        params = batch_codes + date_strs
        cur.execute(query, params)
        for row in cur.fetchall():
            code = row[0]
            trade_date = row[1]
            if code not in result:
                result[code] = {}
            result[code][trade_date] = {
                'open': float(row[2]) if row[2] else None,
                'close': float(row[3]) if row[3] else None,
                'high': float(row[4]) if row[4] else None,
                'low': float(row[5]) if row[5] else None,
            }
    cur.close()
    conn.close()
    return result


def calc_buy_shares(price: float, amount: float) -> int:
    if price <= 0:
        return 0
    return int(amount / price / MIN_LOTS) * MIN_LOTS

def calc_buy_cost(price: float, shares: int) -> float:
    amt = price * shares
    commission = max(amt * COMMISSION_RATE, 5)
    return amt + commission

def calc_sell_revenue(price: float, shares: int) -> float:
    amt = price * shares
    commission = max(amt * COMMISSION_RATE, 5)
    stamp_tax = amt * STAMP_TAX_RATE
    return amt - commission - stamp_tax


def run_backtest_with_details(strategy: str, candidates: List[Dict], prices: Dict, trading_dates: List[date]):
    from collections import defaultdict
    date_groups = defaultdict(list)
    for c in candidates:
        date_groups[c['snapshot_date']].append(c)
    
    date_to_idx = {d: i for i, d in enumerate(trading_dates)}
    
    capital = INITIAL_CAPITAL
    positions = []
    trades = []
    
    for i, trade_date in enumerate(trading_dates):
        # 卖出到期的持仓
        new_positions = []
        for pos in positions:
            buy_idx = date_to_idx.get(pos['buy_date'], -1)
            if i - buy_idx >= HOLD_DAYS:
                sell_price = prices.get(pos['code'], {}).get(trade_date, {}).get('open')
                if sell_price:
                    revenue = calc_sell_revenue(sell_price, pos['shares'])
                    pnl = revenue - pos['cost']
                    pnl_pct = (sell_price - pos['buy_price']) / pos['buy_price'] * 100
                    trades.append({
                        'code': pos['code'],
                        'name': pos['name'],
                        'buy_date': pos['buy_date'],
                        'sell_date': trade_date,
                        'buy_price': pos['buy_price'],
                        'sell_price': sell_price,
                        'shares': pos['shares'],
                        'cost': pos['cost'],
                        'revenue': revenue,
                        'pnl': pnl,
                        'pnl_pct': pnl_pct
                    })
                    capital += revenue
            else:
                new_positions.append(pos)
        positions = new_positions
        
        # 买入新推荐
        if trade_date in date_groups and capital > 1000:
            day_recs = sorted(date_groups[trade_date], key=lambda x: x['final_score'] or 0, reverse=True)[:5]
            if i + 1 < len(trading_dates):
                buy_date = trading_dates[i + 1]
                alloc = capital / len(day_recs)
                for rec in day_recs:
                    buy_price = prices.get(rec['ts_code'], {}).get(buy_date, {}).get('open')
                    if not buy_price or buy_price <= 0:
                        continue
                    shares = calc_buy_shares(buy_price, alloc)
                    if shares < MIN_LOTS:
                        continue
                    cost = calc_buy_cost(buy_price, shares)
                    if cost > capital:
                        continue
                    capital -= cost
                    positions.append({
                        'code': rec['ts_code'],
                        'name': rec['stock_name'],
                        'buy_date': buy_date,
                        'buy_price': buy_price,
                        'shares': shares,
                        'cost': cost
                    })
    
    return trades, capital


def main():
    start = date(2026, 1, 1)
    end = date(2026, 5, 27)
    
    trading_dates = get_trading_days_in_range(start, end)
    
    strategies = ['overnight_8step', 'llm_multisource', 'funnel_strategy']
    all_candidates = {}
    all_codes = set()
    
    print("获取推荐数据...")
    for s in strategies:
        recs = fetch_candidates(start, end, s)
        all_candidates[s] = recs
        all_codes.update(r['ts_code'] for r in recs)
    
    print(f"获取价格数据 ({len(all_codes)} 只股票)...")
    prices = fetch_prices_for_codes(list(all_codes), trading_dates)
    print(f"价格数据: {len(prices)} 只股票\n")
    
    for s in strategies:
        print("=" * 100)
        print(f"  策略: {s}")
        print("=" * 100)
        
        trades, final_capital = run_backtest_with_details(s, all_candidates[s], prices, trading_dates)
        
        if not trades:
            print("  无交易记录\n")
            continue
        
        # 输出表头
        print(f"{'序号':>4} | {'代码':>10} | {'名称':>8} | {'买入日期':>10} | {'买入价':>7} | {'股数':>6} | {'成本':>9} | {'卖出日期':>10} | {'卖出价':>7} | {'收入':>9} | {'盈亏':>9} | {'盈亏%':>7}")
        print("-" * 130)
        
        for j, t in enumerate(trades, 1):
            print(f"{j:>4} | {t['code']:>10} | {t['name']:>8} | {str(t['buy_date']):>10} | {t['buy_price']:>7.2f} | {t['shares']:>6} | {t['cost']:>9.0f} | {str(t['sell_date']):>10} | {t['sell_price']:>7.2f} | {t['revenue']:>9.0f} | {t['pnl']:>+9.0f} | {t['pnl_pct']:>+6.1f}%")
        
        print("-" * 130)
        
        # 统计
        wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]
        total_pnl = sum(t['pnl'] for t in trades)
        
        print(f"\n  总交易: {len(trades)} 次")
        print(f"  盈利: {len(wins)} 次, 亏损: {len(losses)} 次")
        print(f"  胜率: {len(wins)/len(trades)*100:.1f}%")
        print(f"  总盈亏: {total_pnl:+,.0f} 元")
        print(f"  最终资产: {final_capital:,.0f} 元")
        print(f"  收益率: {(final_capital/INITIAL_CAPITAL-1)*100:+.2f}%")
        print()


if __name__ == '__main__':
    main()
