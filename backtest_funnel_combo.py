"""
漏斗式组合回测
==============
overnight_8step / llm_multisource 初筛 → funnel_strategy 风控过滤

逻辑：
1. 从 overnight_8step 或 llm_multisource 获取候选股票
2. 用 funnel_strategy 的风控规则过滤（大盘风控、流动性、趋势等）
3. 模拟交易，对比原始策略收益
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
        SELECT snapshot_date, ts_code, stock_name, final_score, sources
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
            'final_score': row[3],
            'sources': row[4]
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
            SELECT ts_code, trade_date, open, close, high, low, volume, amount
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
                'volume': float(row[6]) if row[6] else 0,
                'amount': float(row[7]) if row[7] else 0,
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


def funnel_risk_filter(code: str, trade_date: date, prices: Dict, funnel_recs: Dict[date, List[Dict]]) -> bool:
    """
    funnel_strategy 风控过滤
    
    规则：
    1. 如果 funnel_strategy 当天推荐了这只股票，直接通过
    2. 否则检查基本风控：
       - 价格 > 0
       - 有成交量（非停牌）
       - 近 5 日趋势向上（简单判断）
    """
    # 检查 funnel 是否推荐
    if trade_date in funnel_recs:
        for rec in funnel_recs[trade_date]:
            if rec['ts_code'] == code:
                return True
    
    # 基本风控
    if code not in prices or trade_date not in prices[code]:
        return False
    
    today = prices[code][trade_date]
    if not today['open'] or today['open'] <= 0:
        return False
    
    if today['volume'] <= 0:
        return False
    
    # 简单趋势检查：今日开盘价 > 5 日前收盘价
    # 需要找 5 个交易日前的数据
    return True  # 暂时简化，只做基本风控


def run_backtest(name: str, candidates: List[Dict], prices: Dict, trading_dates: List[date], funnel_recs: Dict = None) -> Dict:
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
            
            # 漏斗过滤
            if funnel_recs is not None:
                filtered_recs = []
                for rec in day_recs:
                    if funnel_risk_filter(rec['ts_code'], trade_date, prices, funnel_recs):
                        filtered_recs.append(rec)
                day_recs = filtered_recs
            
            if not day_recs:
                continue
            
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
    
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    total_pnl = sum(t['pnl'] for t in trades)
    
    return {
        'name': name,
        'trades': trades,
        'total_trades': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': len(wins) / len(trades) * 100 if trades else 0,
        'total_pnl': total_pnl,
        'final_capital': capital,
        'return_pct': (capital / INITIAL_CAPITAL - 1) * 100,
        'avg_pnl': total_pnl / len(trades) if trades else 0,
        'avg_win': sum(t['pnl'] for t in wins) / len(wins) if wins else 0,
        'avg_loss': sum(t['pnl'] for t in losses) / len(losses) if losses else 0,
        'max_win': max((t['pnl'] for t in trades), default=0),
        'max_loss': min((t['pnl'] for t in trades), default=0),
    }


def main():
    start = date(2026, 1, 1)
    end = date(2026, 5, 27)
    
    trading_dates = get_trading_days_in_range(start, end)
    
    print("=" * 100)
    print("  漏斗式组合回测")
    print("=" * 100)
    print(f"\n  日期范围: {start} ~ {end}")
    print(f"  初始资金: {INITIAL_CAPITAL:,} 元")
    print(f"  交易日: {len(trading_dates)} 天\n")
    
    # 获取推荐数据
    print("获取推荐数据...")
    overnight_recs = fetch_candidates(start, end, 'overnight_8step')
    llm_recs = fetch_candidates(start, end, 'llm_multisource')
    funnel_recs_list = fetch_candidates(start, end, 'funnel_strategy')
    
    # 构建 funnel 推荐索引
    from collections import defaultdict
    funnel_recs = defaultdict(list)
    for rec in funnel_recs_list:
        funnel_recs[rec['snapshot_date']].append(rec)
    
    # 合并初筛候选（overnight_8step + llm_multisource）
    all_codes = set()
    for rec in overnight_recs + llm_recs + funnel_recs_list:
        all_codes.add(rec['ts_code'])
    
    print(f"  overnight_8step: {len(overnight_recs)} 条记录")
    print(f"  llm_multisource: {len(llm_recs)} 条记录")
    print(f"  funnel_strategy: {len(funnel_recs_list)} 条记录")
    
    # 获取价格数据
    print(f"\n获取价格数据 ({len(all_codes)} 只股票)...")
    prices = fetch_prices_for_codes(list(all_codes), trading_dates)
    print(f"  价格数据: {len(prices)} 只股票\n")
    
    # 运行回测
    print("=" * 100)
    print("  开始回测...")
    print("=" * 100)
    
    # 1. 原始 overnight_8step
    print("\n  回测 overnight_8step...")
    result_overnight = run_backtest('overnight_8step', overnight_recs, prices, trading_dates)
    print(f"    交易: {result_overnight['total_trades']} 次, 胜率: {result_overnight['win_rate']:.1f}%, 收益: {result_overnight['return_pct']:.2f}%")
    
    # 2. 原始 llm_multisource
    print("\n  回测 llm_multisource...")
    result_llm = run_backtest('llm_multisource', llm_recs, prices, trading_dates)
    print(f"    交易: {result_llm['total_trades']} 次, 胜率: {result_llm['win_rate']:.1f}%, 收益: {result_llm['return_pct']:.2f}%")
    
    # 3. 原始 funnel_strategy
    print("\n  回测 funnel_strategy...")
    result_funnel = run_backtest('funnel_strategy', funnel_recs_list, prices, trading_dates)
    print(f"    交易: {result_funnel['total_trades']} 次, 胜率: {result_funnel['win_rate']:.1f}%, 收益: {result_funnel['return_pct']:.2f}%")
    
    # 4. 漏斗组合：overnight_8step → funnel 过滤
    print("\n  回测 漏斗组合 (overnight_8step → funnel 过滤)...")
    result_overnight_funnel = run_backtest('overnight_8step → funnel', overnight_recs, prices, trading_dates, funnel_recs)
    print(f"    交易: {result_overnight_funnel['total_trades']} 次, 胜率: {result_overnight_funnel['win_rate']:.1f}%, 收益: {result_overnight_funnel['return_pct']:.2f}%")
    
    # 5. 漏斗组合：llm_multisource → funnel 过滤
    print("\n  回测 漏斗组合 (llm_multisource → funnel 过滤)...")
    result_llm_funnel = run_backtest('llm_multisource → funnel', llm_recs, prices, trading_dates, funnel_recs)
    print(f"    交易: {result_llm_funnel['total_trades']} 次, 胜率: {result_llm_funnel['win_rate']:.1f}%, 收益: {result_llm_funnel['return_pct']:.2f}%")
    
    # 6. 漏斗组合：(overnight_8step + llm_multisource) → funnel 过滤
    combined_recs = overnight_recs + llm_recs
    print("\n  回测 漏斗组合 (overnight_8step + llm_multisource → funnel 过滤)...")
    result_combined_funnel = run_backtest('overnight+llm → funnel', combined_recs, prices, trading_dates, funnel_recs)
    print(f"    交易: {result_combined_funnel['total_trades']} 次, 胜率: {result_combined_funnel['win_rate']:.1f}%, 收益: {result_combined_funnel['return_pct']:.2f}%")
    
    # 输出对比报告
    print("\n" + "=" * 100)
    print("  策略收益对比报告")
    print("=" * 100)
    
    results = [
        result_overnight,
        result_llm,
        result_funnel,
        result_overnight_funnel,
        result_llm_funnel,
        result_combined_funnel,
    ]
    
    # 按收益率排序
    results.sort(key=lambda x: x['return_pct'], reverse=True)
    
    print(f"\n  {'排名':>4} | {'策略':>40} | {'交易次数':>8} | {'胜率':>6} | {'总盈亏':>10} | {'收益率':>8} | {'最终资产':>10}")
    print(f"  {'-'*4}-+-{'-'*40}-+-{'-'*8}-+-{'-'*6}-+-{'-'*10}-+-{'-'*8}-+-{'-'*10}")
    
    for i, r in enumerate(results, 1):
        print(f"  {i:>4} | {r['name']:>40} | {r['total_trades']:>8} | {r['win_rate']:>5.1f}% | {r['total_pnl']:>+10,.0f} | {r['return_pct']:>+7.2f}% | {r['final_capital']:>10,.0f}")
    
    print("\n" + "=" * 100)
    print("  详细指标对比")
    print("=" * 100)
    
    for r in results:
        print(f"\n  策略: {r['name']}")
        print(f"  {'-'*60}")
        print(f"    交易次数:   {r['total_trades']}")
        print(f"    盈利/亏损:  {r['wins']} / {r['losses']}")
        print(f"    胜率:       {r['win_rate']:.1f}%")
        print(f"    总盈亏:     {r['total_pnl']:+,.0f} 元")
        print(f"    收益率:     {r['return_pct']:+.2f}%")
        print(f"    平均盈亏:   {r['avg_pnl']:+,.0f} 元")
        print(f"    平均盈利:   {r['avg_win']:,.0f} 元")
        print(f"    平均亏损:   {r['avg_loss']:,.0f} 元")
        print(f"    最大盈利:   {r['max_win']:,.0f} 元")
        print(f"    最大亏损:   {r['max_loss']:,.0f} 元")
        print(f"    最终资产:   {r['final_capital']:,.0f} 元")


if __name__ == '__main__':
    main()
