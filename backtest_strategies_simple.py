"""
三策略收益对比回测脚本（简化版 - 使用数据库行情数据）
=====================================================
基于 daily_candidates 表的推荐数据，模拟每个策略投入 100,000 元，
按照推荐价格买入卖出，计算到现在的收益情况。

回测规则：
- 初始资金：100,000 元/策略
- 买入：T 日推荐，T+1 日开盘价买入
- 卖出：T+2 日开盘价卖出（持有 1 天）
- 仓位：每只股票使用可用资金的等分金额
- 手续费：买入 0.025%，卖出 0.025% + 0.1% 印花税
- 最小交易单位：100 股（1 手）
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

# ============================================================
# 配置
# ============================================================
INITIAL_CAPITAL = 100_000  # 初始资金
COMMISSION_RATE = 0.00025  # 佣金率（双向）
STAMP_TAX_RATE = 0.001     # 印花税（仅卖出）
MIN_LOTS = 100             # 最小交易单位（股）
HOLD_DAYS = 1              # 持有天数


# ============================================================
# 数据库查询
# ============================================================
def fetch_candidates(start: date, end: date, strategy: str) -> List[Dict]:
    """从 daily_candidates 表获取推荐数据"""
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
            'stock_name': row[2],
            'final_score': row[3]
        })
    
    print(f"  {strategy}: {len(result)} 条记录")
    return result


def fetch_prices_for_codes(codes: List[str], dates: List[date]) -> Dict[str, Dict[date, Dict]]:
    """从数据库批量获取指定股票在指定日期的价格数据"""
    if not codes or not dates:
        return {}
    
    # 按批次查询（避免 SQL 过长）
    result = {}
    conn = get_db_fresh()
    cur = conn.cursor()
    
    # 将日期转换为字符串
    date_strs = [d.strftime('%Y-%m-%d') for d in dates]
    
    # 分批处理（每批 100 只股票）
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
        
        print(f"  获取价格: {min(i+100, len(codes))}/{len(codes)}")
    
    cur.close()
    conn.close()
    print(f"  价格数据: {len(result)} 只股票")
    return result


# ============================================================
# 回测引擎
# ============================================================
class SimpleBacktest:
    """简化版回测引擎"""
    
    def __init__(self, strategy: str):
        self.strategy = strategy
        self.capital = INITIAL_CAPITAL
        self.trades = []
    
    def calc_buy_shares(self, price: float, amount: float) -> int:
        """计算可买入股数"""
        if price <= 0:
            return 0
        return int(amount / price / MIN_LOTS) * MIN_LOTS
    
    def calc_buy_cost(self, price: float, shares: int) -> float:
        """买入成本"""
        amt = price * shares
        commission = max(amt * COMMISSION_RATE, 5)
        return amt + commission
    
    def calc_sell_revenue(self, price: float, shares: int) -> float:
        """卖出收入"""
        amt = price * shares
        commission = max(amt * COMMISSION_RATE, 5)
        stamp_tax = amt * STAMP_TAX_RATE
        return amt - commission - stamp_tax
    
    def run(self, candidates: List[Dict], prices: Dict, trading_dates: List[date]) -> Dict:
        """运行回测"""
        # 按日期分组
        from collections import defaultdict
        date_groups = defaultdict(list)
        for c in candidates:
            date_groups[c['snapshot_date']].append(c)
        
        # 日期索引
        date_to_idx = {d: i for i, d in enumerate(trading_dates)}
        
        positions = []  # 当前持仓
        
        for i, trade_date in enumerate(trading_dates):
            # 1. 卖出到期的持仓
            new_positions = []
            for pos in positions:
                buy_idx = date_to_idx.get(pos['buy_date'], -1)
                if i - buy_idx >= HOLD_DAYS:
                    # 卖出
                    sell_price = self._get_price(pos['code'], trade_date, 'open', prices)
                    if sell_price:
                        revenue = self.calc_sell_revenue(sell_price, pos['shares'])
                        pnl = revenue - pos['cost']
                        self.trades.append({
                            'code': pos['code'],
                            'buy_date': pos['buy_date'],
                            'sell_date': trade_date,
                            'buy_price': pos['buy_price'],
                            'sell_price': sell_price,
                            'pnl': pnl,
                            'pnl_pct': (sell_price - pos['buy_price']) / pos['buy_price'] * 100
                        })
                        self.capital += revenue
                else:
                    new_positions.append(pos)
            positions = new_positions
            
            # 2. 买入新推荐（T+1 买入）
            if trade_date in date_groups and self.capital > 1000:
                day_recs = sorted(date_groups[trade_date], key=lambda x: x['final_score'] or 0, reverse=True)[:5]
                
                # T+1 是下一个交易日
                if i + 1 < len(trading_dates):
                    buy_date = trading_dates[i + 1]
                    alloc = self.capital / len(day_recs)
                    
                    for rec in day_recs:
                        buy_price = self._get_price(rec['ts_code'], buy_date, 'open', prices)
                        if not buy_price or buy_price <= 0:
                            continue
                        
                        shares = self.calc_buy_shares(buy_price, alloc)
                        if shares < MIN_LOTS:
                            continue
                        
                        cost = self.calc_buy_cost(buy_price, shares)
                        if cost > self.capital:
                            continue
                        
                        self.capital -= cost
                        positions.append({
                            'code': rec['ts_code'],
                            'buy_date': buy_date,
                            'buy_price': buy_price,
                            'shares': shares,
                            'cost': cost
                        })
        
        return self._report()
    
    def _get_price(self, code: str, trade_date: date, field: str, prices: Dict) -> Optional[float]:
        """获取价格"""
        if code in prices and trade_date in prices[code]:
            return prices[code][trade_date].get(field)
        return None
    
    def _report(self) -> Dict:
        """生成报告"""
        if not self.trades:
            return {'strategy': self.strategy, 'total_trades': 0, 'win_trades': 0, 'loss_trades': 0,
                    'win_rate': 0, 'total_pnl': 0, 'total_return_pct': 0, 'avg_pnl': 0,
                    'avg_win': 0, 'avg_loss': 0, 'max_win': 0, 'max_loss': 0,
                    'final_value': INITIAL_CAPITAL}
        
        import statistics
        pnls = [t['pnl'] for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        
        total_pnl = sum(pnls)
        final_value = INITIAL_CAPITAL + total_pnl
        
        return {
            'strategy': self.strategy,
            'total_trades': len(self.trades),
            'win_trades': len(wins),
            'loss_trades': len(losses),
            'win_rate': len(wins) / len(self.trades) * 100,
            'total_pnl': total_pnl,
            'total_return_pct': (final_value / INITIAL_CAPITAL - 1) * 100,
            'avg_pnl': statistics.mean(pnls),
            'avg_win': statistics.mean(wins) if wins else 0,
            'avg_loss': statistics.mean(losses) if losses else 0,
            'max_win': max(pnls),
            'max_loss': min(pnls),
            'final_value': final_value
        }


# ============================================================
# 主函数
# ============================================================
def main():
    start = date(2026, 1, 1)
    end = date(2026, 5, 27)
    
    print("=" * 80)
    print("  三策略收益对比回测（简化版）")
    print("=" * 80)
    print(f"  日期范围: {start} ~ {end}")
    print(f"  初始资金: {INITIAL_CAPITAL:,.0f} 元/策略")
    print(f"  持有天数: {HOLD_DAYS} 天")
    print()
    
    # 1. 获取交易日历
    trading_dates = get_trading_days_in_range(start, end)
    print(f"  交易日: {len(trading_dates)} 天")
    
    # 2. 获取推荐数据
    strategies = ['overnight_8step', 'llm_multisource', 'funnel_strategy']
    all_candidates = {}
    all_codes = set()
    
    print("\n  获取推荐数据...")
    for s in strategies:
        recs = fetch_candidates(start, end, s)
        all_candidates[s] = recs
        all_codes.update(r['ts_code'] for r in recs)
    
    # 3. 获取价格数据
    print(f"\n  获取价格数据 ({len(all_codes)} 只股票, {len(trading_dates)} 天)...")
    prices = fetch_prices_for_codes(list(all_codes), trading_dates)
    
    # 4. 运行回测
    print("\n" + "=" * 80)
    print("  开始回测...")
    print("=" * 80)
    
    results = []
    for s in strategies:
        print(f"\n  回测 {s}...")
        engine = SimpleBacktest(s)
        result = engine.run(all_candidates[s], prices, trading_dates)
        results.append(result)
        print(f"    交易: {result['total_trades']} 次, 胜率: {result['win_rate']:.1f}%, "
              f"收益: {result['total_return_pct']:.2f}%")
    
    # 5. 输出报告
    print("\n" + "=" * 80)
    print("  三策略收益对比报告")
    print("=" * 80 + "\n")
    
    for r in results:
        print(f"  策略: {r['strategy']}")
        print(f"  " + "-" * 40)
        print(f"  交易次数:   {r['total_trades']}")
        print(f"  盈利/亏损:  {r['win_trades']} / {r['loss_trades']}")
        print(f"  胜率:       {r['win_rate']:.1f}%")
        print(f"  总盈亏:     {r['total_pnl']:,.0f} 元")
        print(f"  收益率:     {r['total_return_pct']:.2f}%")
        print(f"  平均盈亏:   {r['avg_pnl']:,.0f} 元")
        print(f"  平均盈利:   {r['avg_win']:,.0f} 元")
        print(f"  平均亏损:   {r['avg_loss']:,.0f} 元")
        print(f"  最大盈利:   {r['max_win']:,.0f} 元")
        print(f"  最大亏损:   {r['max_loss']:,.0f} 元")
        print(f"  最终资产:   {r['final_value']:,.0f} 元")
        print()
    
    # 6. 排名
    print("  " + "=" * 40)
    print("  策略排名（按收益率）")
    print("  " + "=" * 40)
    
    sorted_results = sorted(results, key=lambda x: x['total_return_pct'], reverse=True)
    for i, r in enumerate(sorted_results, 1):
        print(f"  {i}. {r['strategy']}: {r['total_return_pct']:+.2f}%")
    
    print("\n" + "=" * 80)


if __name__ == '__main__':
    main()
