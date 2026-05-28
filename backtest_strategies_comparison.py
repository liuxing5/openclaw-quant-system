"""
三策略收益对比回测脚本
=====================
基于 daily_candidates 表的推荐数据，模拟每个策略投入 100,000 元，
按照推荐价格买入卖出，计算到现在的收益情况。

回测规则：
- 初始资金：100,000 元/策略
- 买入：T 日推荐，T+1 日开盘价买入（overnight_8step/afternoon 模式）
- 卖出：T+2 日开盘价卖出（持有 1 天）或 T+3 日开盘价卖出（持有 2 天）
- 仓位：每只股票使用可用资金的等分金额
- 手续费：买入 0.025%，卖出 0.025% + 0.1% 印花税
- 最小交易单位：100 股（1 手）

使用方式：
  python backtest_strategies_comparison.py
  python backtest_strategies_comparison.py --start 2026-01-01 --end 2026-05-27
  python backtest_strategies_comparison.py --strategy overnight_8step
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import baostock as bs
import pandas as pd

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
HOLD_DAYS = 1              # 持有天数（T+1 买入，T+2 卖出）

DB_URL = os.environ.get('DATABASE_URL',
    "postgresql://postgres:wYFBB91zViSrk2vl@db.qoakbxswwjqfsgbcgepr.supabase.co:5432/postgres")


# ============================================================
# 数据库查询
# ============================================================
def fetch_candidates(start: date, end: date, strategy: Optional[str] = None) -> pd.DataFrame:
    """从 daily_candidates 表获取推荐数据"""
    conn = get_db_fresh()
    cur = conn.cursor()
    
    if strategy:
        cur.execute("""
            SELECT snapshot_date, ts_code, stock_name, final_score, 
                   entry_low, entry_high, stop_loss, target_1, target_2,
                   run_mode, source
            FROM daily_candidates
            WHERE snapshot_date >= %s AND snapshot_date <= %s AND source = %s
            ORDER BY snapshot_date, final_score DESC
        """, (start, end, strategy))
    else:
        cur.execute("""
            SELECT snapshot_date, ts_code, stock_name, final_score,
                   entry_low, entry_high, stop_loss, target_1, target_2,
                   run_mode, source
            FROM daily_candidates
            WHERE snapshot_date >= %s AND snapshot_date <= %s
            ORDER BY snapshot_date, final_score DESC
        """, (start, end))
    
    rows = cur.fetchall()
    cols = ['snapshot_date', 'ts_code', 'stock_name', 'final_score',
            'entry_low', 'entry_high', 'stop_loss', 'target_1', 'target_2',
            'run_mode', 'source']
    
    df = pd.DataFrame(rows, columns=cols)
    cur.close()
    conn.close()
    
    print(f"  获取推荐数据: {len(df)} 条记录")
    return df


def convert_code_format(code: str) -> str:
    """将 baostock 格式 (sh.600000) 转换为数据库格式 (600000.SH)"""
    if '.' in code:
        parts = code.split('.')
        prefix = parts[0].lower()
        num = parts[1]
        if prefix == 'sh':
            return f"{num}.SH"
        elif prefix == 'sz':
            return f"{num}.SZ"
    return code


def load_klines_from_local(months: List[str] = None) -> Dict[str, pd.DataFrame]:
    """从本地 JSON 文件加载 K 线数据（优先使用）"""
    from pathlib import Path
    
    data_dir = Path(__file__).parent / "data" / "klines"
    if not data_dir.exists():
        return {}
    
    # 默认加载所有月份
    if months is None:
        months = [d.name for d in data_dir.iterdir() if d.is_dir()]
    
    result = {}
    for month in months:
        month_dir = data_dir / month
        if not month_dir.exists():
            continue
        
        for fp in month_dir.glob("*.json"):
            code_baostock = fp.stem  # 例如: sh.600000
            code_db = convert_code_format(code_baostock)  # 转换为: 600000.SH
            try:
                import json
                with open(fp, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data and isinstance(data, list):
                    df = pd.DataFrame(data)
                    df['date'] = pd.to_datetime(df['date'])
                    for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                    result[code_db] = df
            except Exception:
                pass
    
    print(f"  从本地加载 K 线: {len(result)} 只股票")
    return result


def fetch_klines_from_baostock(codes: List[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
    """从 baostock 批量获取 K 线数据"""
    lg = bs.login()
    if lg.error_code != '0':
        print(f"  baostock 登录失败: {lg.error_msg}")
        return {}
    
    result = {}
    fields = "date,code,open,high,low,close,volume,amount"
    
    for i, code in enumerate(codes, 1):
        if i % 100 == 0:
            print(f"  获取 K 线: {i}/{len(codes)}")
        
        try:
            rs = bs.query_history_k_data_plus(code, fields,
                start_date=start, end_date=end, frequency='d', adjustflag='3')
            
            rows = []
            while rs.next():
                rows.append(dict(zip(rs.fields, rs.get_row_data())))
            
            if rows:
                df = pd.DataFrame(rows)
                df['date'] = pd.to_datetime(df['date'])
                for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                result[code] = df
        except Exception as e:
            print(f"  获取 {code} 失败: {e}")
    
    bs.logout()
    print(f"  获取 K 线完成: {len(result)}/{len(codes)} 只股票")
    return result


# ============================================================
# 回测引擎
# ============================================================
class BacktestEngine:
    """策略回测引擎"""
    
    def __init__(self, strategy_name: str, initial_capital: float = INITIAL_CAPITAL):
        self.strategy_name = strategy_name
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.positions: List[Dict] = []  # 持仓列表
        self.trades: List[Dict] = []     # 交易记录
        self.daily_values: List[Dict] = []  # 每日净值记录
    
    def calculate_buy_shares(self, price: float, available_capital: float) -> int:
        """计算可买入股数（按手取整）"""
        if price <= 0:
            return 0
        shares = int(available_capital / price / MIN_LOTS) * MIN_LOTS
        return max(shares, 0)
    
    def calculate_cost(self, price: float, shares: int, is_buy: bool = True) -> float:
        """计算交易成本"""
        amount = price * shares
        commission = amount * COMMISSION_RATE
        commission = max(commission, 5)  # 最低 5 元佣金
        
        if is_buy:
            return amount + commission
        else:
            stamp_tax = amount * STAMP_TAX_RATE
            return amount - commission - stamp_tax
    
    def run(self, candidates: pd.DataFrame, klines: Dict[str, pd.DataFrame], 
            trading_dates: List[date]) -> Dict:
        """运行回测"""
        # 按日期分组
        date_groups = candidates.groupby('snapshot_date')
        
        # 创建日期索引映射
        date_to_idx = {d: i for i, d in enumerate(trading_dates)}
        
        for i, trade_date in enumerate(trading_dates):
            # 1. 检查是否有持仓需要卖出
            self._check_exit(trade_date, trading_dates, klines, date_to_idx)
            
            # 2. 检查当天是否有推荐
            if trade_date in date_groups.groups:
                day_candidates = date_groups.get_group(trade_date)
                self._check_entry(trade_date, day_candidates, klines, trading_dates, date_to_idx)
            
            # 3. 记录每日净值
            total_value = self._calculate_total_value(trade_date, klines)
            self.daily_values.append({
                'date': trade_date,
                'capital': self.capital,
                'position_value': total_value - self.capital,
                'total_value': total_value,
                'return_pct': (total_value / self.initial_capital - 1) * 100
            })
        
        return self._generate_report()
    
    def _check_exit(self, trade_date: date, trading_dates: List[date], 
                    klines: Dict[str, pd.DataFrame], date_to_idx: Dict):
        """检查持仓是否需要卖出"""
        new_positions = []
        
        for pos in self.positions:
            buy_date = pos['buy_date']
            buy_idx = date_to_idx.get(buy_date, -1)
            current_idx = date_to_idx.get(trade_date, -1)
            
            hold_days = current_idx - buy_idx
            
            # 持有达到目标天数，卖出
            if hold_days >= HOLD_DAYS:
                self._sell_position(pos, trade_date, klines, f"T+{HOLD_DAYS}卖出")
            else:
                new_positions.append(pos)
        
        self.positions = new_positions
    
    def _check_entry(self, trade_date: date, candidates: pd.DataFrame, 
                     klines: Dict[str, pd.DataFrame], trading_dates: List[date],
                     date_to_idx: Dict):
        """检查是否可以买入"""
        if self.capital < 1000:  # 资金不足 1000 元，停止买入
            return
        
        # 按评分排序，取前 5 只
        top_candidates = candidates.nlargest(5, 'final_score')
        
        # 等分资金
        alloc_per_stock = self.capital / len(top_candidates)
        
        # 获取下一个交易日（实际买入日）
        current_idx = date_to_idx.get(trade_date, -1)
        if current_idx + 1 >= len(trading_dates):
            return  # 没有下一个交易日
        
        actual_buy_date = trading_dates[current_idx + 1]
        
        for _, row in top_candidates.iterrows():
            code = row['ts_code']
            
            # 获取实际买入日的开盘价
            buy_price = self._get_open(code, actual_buy_date, klines)
            if buy_price is None or buy_price <= 0:
                continue
            
            # 计算买入股数
            shares = self.calculate_buy_shares(buy_price, alloc_per_stock)
            if shares < MIN_LOTS:
                continue
            
            # 计算买入成本
            cost = self.calculate_cost(buy_price, shares, is_buy=True)
            if cost > self.capital:
                continue
            
            # 执行买入
            self.capital -= cost
            self.positions.append({
                'code': code,
                'name': row['stock_name'],
                'recommend_date': trade_date,  # 推荐日
                'buy_date': actual_buy_date,    # 实际买入日
                'buy_price': buy_price,
                'shares': shares,
                'cost': cost,
                'score': row['final_score']
            })
    
    def _sell_position(self, pos: Dict, sell_date: date, 
                       klines: Dict[str, pd.DataFrame], reason: str):
        """卖出持仓"""
        code = pos['code']
        shares = pos['shares']
        buy_price = pos['buy_price']
        
        # 获取卖出日开盘价
        sell_price = self._get_open(code, sell_date, klines)
        if sell_price is None or sell_price <= 0:
            sell_price = buy_price  # 无法获取价格，按买入价卖出
        
        # 计算卖出收入
        revenue = self.calculate_cost(sell_price, shares, is_buy=False)
        
        # 计算盈亏
        pnl = revenue - pos['cost']
        pnl_pct = (sell_price - buy_price) / buy_price * 100
        
        # 记录交易
        self.trades.append({
            'code': code,
            'name': pos['name'],
            'buy_date': pos['buy_date'],
            'sell_date': sell_date,
            'buy_price': buy_price,
            'sell_price': sell_price,
            'shares': shares,
            'cost': pos['cost'],
            'revenue': revenue,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'reason': reason
        })
        
        # 资金回笼
        self.capital += revenue
    
    def _get_open(self, code: str, trade_date: date, 
                  klines: Dict[str, pd.DataFrame]) -> Optional[float]:
        """获取指定日期的开盘价"""
        if code not in klines:
            return None
        
        df = klines[code]
        mask = df['date'] == pd.Timestamp(trade_date)
        row = df[mask]
        
        if row.empty:
            return None
        
        return row['open'].iloc[0]
    
    def _get_next_open(self, code: str, trade_date: date, 
                       klines: Dict[str, pd.DataFrame]) -> Optional[float]:
        """获取下一交易日的开盘价"""
        if code not in klines:
            return None
        
        df = klines[code]
        mask = df['date'] > pd.Timestamp(trade_date)
        row = df[mask]
        
        if row.empty:
            return None
        
        return row['open'].iloc[0]
    
    def _calculate_total_value(self, trade_date: date, 
                               klines: Dict[str, pd.DataFrame]) -> float:
        """计算总资产（现金 + 持仓市值）"""
        total = self.capital
        
        for pos in self.positions:
            code = pos['code']
            current_price = self._get_open(code, trade_date, klines)
            if current_price and current_price > 0:
                total += current_price * pos['shares']
            else:
                total += pos['buy_price'] * pos['shares']  # 无法获取价格，按买入价计算
        
        return total
    
    def _generate_report(self) -> Dict:
        """生成回测报告"""
        if not self.trades:
            return {
                'strategy': self.strategy_name,
                'total_trades': 0,
                'win_trades': 0,
                'loss_trades': 0,
                'win_rate': 0,
                'total_pnl': 0,
                'total_return_pct': 0,
                'avg_pnl': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'max_win': 0,
                'max_loss': 0,
                'final_value': self.initial_capital,
                'daily_values': self.daily_values
            }
        
        trades_df = pd.DataFrame(self.trades)
        win_trades = trades_df[trades_df['pnl'] > 0]
        loss_trades = trades_df[trades_df['pnl'] <= 0]
        
        total_pnl = trades_df['pnl'].sum()
        final_value = self.initial_capital + total_pnl
        
        return {
            'strategy': self.strategy_name,
            'total_trades': len(trades_df),
            'win_trades': len(win_trades),
            'loss_trades': len(loss_trades),
            'win_rate': len(win_trades) / len(trades_df) * 100 if len(trades_df) > 0 else 0,
            'total_pnl': total_pnl,
            'total_return_pct': (final_value / self.initial_capital - 1) * 100,
            'avg_pnl': trades_df['pnl'].mean(),
            'avg_win': win_trades['pnl'].mean() if len(win_trades) > 0 else 0,
            'avg_loss': loss_trades['pnl'].mean() if len(loss_trades) > 0 else 0,
            'max_win': trades_df['pnl'].max(),
            'max_loss': trades_df['pnl'].min(),
            'final_value': final_value,
            'daily_values': self.daily_values
        }


# ============================================================
# 主函数
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='三策略收益对比回测')
    parser.add_argument('--start', type=str, default='2026-01-01', help='开始日期')
    parser.add_argument('--end', type=str, default='2026-05-27', help='结束日期')
    parser.add_argument('--strategy', type=str, default=None, help='指定策略（可选）')
    args = parser.parse_args()
    
    start = datetime.strptime(args.start, '%Y-%m-%d').date()
    end = datetime.strptime(args.end, '%Y-%m-%d').date()
    
    print("=" * 80)
    print("  三策略收益对比回测")
    print("=" * 80)
    print(f"  日期范围: {start} ~ {end}")
    print(f"  初始资金: {INITIAL_CAPITAL:,.0f} 元/策略")
    print(f"  持有天数: {HOLD_DAYS} 天")
    print(f"  手续费: 佣金 {COMMISSION_RATE*100:.2f}% + 印花税 {STAMP_TAX_RATE*100:.1f}%")
    print()
    
    # 1. 获取交易日历
    trading_dates = get_trading_days_in_range(start, end)
    print(f"  交易日: {len(trading_dates)} 天")
    
    # 2. 获取推荐数据
    strategies = ['overnight_8step', 'llm_multisource', 'funnel_strategy']
    if args.strategy:
        strategies = [args.strategy]
    
    all_candidates = {}
    all_codes = set()
    
    for strategy in strategies:
        print(f"\n  获取 {strategy} 推荐数据...")
        df = fetch_candidates(start, end, strategy)
        all_candidates[strategy] = df
        all_codes.update(df['ts_code'].unique())
    
    # 3. 获取 K 线数据（优先使用本地）
    print(f"\n  加载 K 线数据...")
    klines = load_klines_from_local()
    
    # 只使用本地已有的数据
    available_codes = all_codes & set(klines.keys())
    missing_codes = all_codes - set(klines.keys())
    print(f"  可用: {len(available_codes)} 只, 缺少: {len(missing_codes)} 只（跳过）")
    
    # 4. 运行回测
    print("\n" + "=" * 80)
    print("  开始回测...")
    print("=" * 80)
    
    results = []
    for strategy in strategies:
        print(f"\n  回测 {strategy}...")
        candidates = all_candidates[strategy]
        
        if candidates.empty:
            print(f"    无数据，跳过")
            continue
        
        engine = BacktestEngine(strategy)
        result = engine.run(candidates, klines, trading_dates)
        results.append(result)
        
        print(f"    交易次数: {result['total_trades']}")
        print(f"    胜率: {result['win_rate']:.1f}%")
        print(f"    总盈亏: {result['total_pnl']:,.0f} 元")
        print(f"    收益率: {result['total_return_pct']:.2f}%")
        print(f"    最终资产: {result['final_value']:,.0f} 元")
    
    # 5. 输出对比报告
    print("\n" + "=" * 80)
    print("  三策略收益对比报告")
    print("=" * 80)
    print()
    
    for result in results:
        print(f"  策略: {result['strategy']}")
        print(f"  " + "-" * 40)
        print(f"  交易次数:   {result['total_trades']}")
        print(f"  盈利次数:   {result['win_trades']}")
        print(f"  亏损次数:   {result['loss_trades']}")
        print(f"  胜率:       {result['win_rate']:.1f}%")
        print(f"  总盈亏:     {result['total_pnl']:,.0f} 元")
        print(f"  收益率:     {result['total_return_pct']:.2f}%")
        print(f"  平均盈亏:   {result['avg_pnl']:,.0f} 元")
        print(f"  平均盈利:   {result['avg_win']:,.0f} 元")
        print(f"  平均亏损:   {result['avg_loss']:,.0f} 元")
        print(f"  最大盈利:   {result['max_win']:,.0f} 元")
        print(f"  最大亏损:   {result['max_loss']:,.0f} 元")
        print(f"  最终资产:   {result['final_value']:,.0f} 元")
        print()
    
    # 6. 排名
    print("  " + "=" * 40)
    print("  策略排名（按收益率）")
    print("  " + "=" * 40)
    
    sorted_results = sorted(results, key=lambda x: x['total_return_pct'], reverse=True)
    for i, result in enumerate(sorted_results, 1):
        print(f"  {i}. {result['strategy']}: {result['total_return_pct']:.2f}%")
    
    print("\n" + "=" * 80)
    print("  回测完成!")
    print("=" * 80)


if __name__ == '__main__':
    main()
