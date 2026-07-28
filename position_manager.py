"""
持仓管理器
==========
功能: 管理用户的虚拟交易账户，跟踪持仓、现金、买卖历史

核心逻辑:
1. 每日运行时先检查持仓中股票是否触发止盈/止损/到期
2. 如果触发，自动卖出并记录到历史
3. 更新现金余额和可用仓位
4. 根据推荐列表和可用仓位决定买入哪些股票

参数:
- MAX_CONCURRENT: 最大并发持仓数 (3)
- PROFIT_PCT: 止盈比例 (+11%)
- STOP_PCT: 止损比例 (-8%)
- MAX_HOLD_DAYS: 最大持仓天数 (9)
- POSITION_PCT: 单只股票仓位比例 (95%)
"""
from __future__ import annotations

import os
import sys
import json
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.utils.env import load_project_env
from core.db.connection import get_db_fresh
from core.utils.trading_calendar import is_trading_day, get_next_trading_day

load_project_env()

MAX_CONCURRENT = 1  # 单仓模式：与回测一致
PROFIT_PCT = 10.0
STOP_PCT = 7.0
MAX_HOLD_DAYS = 10
POSITION_PCT = 0.95

TRACKER_PATH = os.path.join(os.path.dirname(__file__), 'position_tracker.json')


class PositionManager:
    def __init__(self):
        self.tracker = self._load_tracker()
        self.conn = get_db_fresh()
    
    def _load_tracker(self) -> Dict:
        if os.path.exists(TRACKER_PATH):
            with open(TRACKER_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        return {
            'start_date': str(date.today()),
            'current_date': str(date.today()),
            'initial_capital': 100000.0,
            'cash': 100000.0,
            'open_positions': [],
            'history': [],
            'config': {
                'max_concurrent': MAX_CONCURRENT,
                'profit_pct': PROFIT_PCT,
                'stop_pct': STOP_PCT,
                'max_hold_days': MAX_HOLD_DAYS,
                'position_pct': POSITION_PCT,
            }
        }
    
    def _save_tracker(self):
        with open(TRACKER_PATH, 'w', encoding='utf-8') as f:
            json.dump(self.tracker, f, ensure_ascii=False, indent=2)
    
    def _get_trading_dates(self, end_date: date) -> List[date]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT trade_date FROM daily_quotes
            WHERE trade_date <= %s
            GROUP BY trade_date
            ORDER BY trade_date DESC
        """, (end_date,))
        dates = [r[0] for r in cur.fetchall()]
        cur.close()
        return dates
    
    def _get_stock_price(self, ts_code: str, trade_date: date) -> Optional[float]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT open, close FROM daily_quotes
            WHERE ts_code = %s AND trade_date = %s
        """, (ts_code, trade_date))
        result = cur.fetchone()
        cur.close()
        return result
    
    def update_positions(self, current_date: date) -> List[Dict]:
        """更新持仓状态，检查止盈/止损/到期
        
        返回: 今日卖出的股票列表
        """
        sold_positions = []
        trading_dates = self._get_trading_dates(current_date)
        
        new_open_positions = []
        for pos in self.tracker.get('open_positions', []):
            buy_date = datetime.strptime(pos['buy_date'], '%Y-%m-%d').date()
            buy_price = pos['buy_price']
            
            days_since_buy = len([d for d in trading_dates if d > buy_date])
            
            if days_since_buy >= MAX_HOLD_DAYS:
                price_info = self._get_stock_price(pos['ts_code'], current_date)
                sell_price = price_info[1] if price_info and price_info[1] else buy_price
                sell_reason = '到期平仓'
                sold = self._sell_position(pos, sell_price, sell_reason, current_date)
                sold_positions.append(sold)
                continue
            
            price_info = self._get_stock_price(pos['ts_code'], current_date)
            if price_info:
                close_price = price_info[1]
                if close_price:
                    return_pct = (close_price - buy_price) / buy_price * 100
                    
                    if return_pct >= PROFIT_PCT:
                        sold = self._sell_position(pos, close_price, '止盈', current_date)
                        sold_positions.append(sold)
                        continue
                    
                    if return_pct <= -STOP_PCT:
                        sold = self._sell_position(pos, close_price, '止损', current_date)
                        sold_positions.append(sold)
                        continue
            
            new_open_positions.append(pos)
        
        self.tracker['open_positions'] = new_open_positions
        self.tracker['current_date'] = str(current_date)
        self._save_tracker()
        
        return sold_positions
    
    def _sell_position(self, pos: Dict, sell_price: float, sell_reason: str, sell_date: date) -> Dict:
        shares = pos.get('shares', 0)
        sell_amount = shares * sell_price
        buy_amount = shares * pos['buy_price']
        profit = sell_amount - buy_amount
        profit_pct = (sell_price - pos['buy_price']) / pos['buy_price'] * 100
        
        self.tracker['cash'] += sell_amount
        
        trade_record = {
            'ts_code': pos['ts_code'],
            'stock_name': pos['stock_name'],
            'rec_date': pos.get('rec_date'),
            'buy_date': pos['buy_date'],
            'buy_price': pos['buy_price'],
            'sell_date': str(sell_date),
            'sell_price': round(sell_price, 2),
            'shares': shares,
            'buy_amount': round(buy_amount, 2),
            'sell_amount': round(sell_amount, 2),
            'profit': round(profit, 2),
            'profit_pct': round(profit_pct, 2),
            'sell_reason': sell_reason,
            'final_score': pos.get('final_score', 0),
            'source': pos.get('source'),
        }
        
        self.tracker['history'].append(trade_record)
        
        return trade_record
    
    def buy_position(self, candidate: Dict, buy_date: date) -> Optional[Dict]:
        """买入股票
        
        参数:
            candidate: 推荐股票信息
            buy_date: 买入日期（应为推荐日的下一个交易日）
        
        返回: 买入记录，如果已满仓或现金不足则返回None
        """
        open_positions = self.tracker.get('open_positions', [])
        
        if len(open_positions) >= MAX_CONCURRENT:
            print(f"  ❌ 已满仓({MAX_CONCURRENT}只)，无法买入 {candidate['stock_name']}")
            return None
        
        held_stocks = {pos['ts_code'] for pos in open_positions}
        if candidate['ts_code'] in held_stocks:
            print(f"  ❌ 已持有 {candidate['stock_name']}，跳过")
            return None
        
        price_info = self._get_stock_price(candidate['ts_code'], buy_date)
        if not price_info or price_info[0] is None:
            print(f"  ❌ {buy_date} 无开盘价数据，无法买入 {candidate['stock_name']}")
            return None
        
        buy_price = price_info[0]
        available_cash = self.tracker['cash'] * POSITION_PCT
        shares = int(available_cash / buy_price / 100) * 100
        
        if shares <= 0:
            print(f"  ❌ 现金不足，无法买入 {candidate['stock_name']}")
            return None
        
        buy_amount = shares * buy_price
        self.tracker['cash'] -= buy_amount
        
        target_1 = round(buy_price * (1 + PROFIT_PCT / 100), 2)
        stop_loss = round(buy_price * (1 - STOP_PCT / 100), 2)
        
        position = {
            'ts_code': candidate['ts_code'],
            'stock_name': candidate['stock_name'],
            'rec_date': str(candidate.get('rec_date', date.today())),
            'buy_date': str(buy_date),
            'buy_price': round(buy_price, 2),
            'shares': shares,
            'buy_amount': round(buy_amount, 2),
            'target_1': target_1,
            'stop_loss': stop_loss,
            'final_score': candidate.get('final_score', 0),
            'source': candidate.get('source', 'daily_candidates'),
        }
        
        self.tracker['open_positions'].append(position)
        self._save_tracker()
        
        print(f"  ✅ 买入 {candidate['stock_name']} ({candidate['ts_code']})")
        print(f"     价格: ¥{buy_price:.2f} | 数量: {shares}股 | 金额: ¥{buy_amount:.2f}")
        print(f"     止盈: ¥{target_1:.2f} | 止损: ¥{stop_loss:.2f}")
        
        return position
    
    def get_open_positions(self, current_date: date) -> List[Dict]:
        """获取当前持仓状态（包含持仓天数）"""
        trading_dates = self._get_trading_dates(current_date)
        
        positions = []
        for pos in self.tracker.get('open_positions', []):
            buy_date = datetime.strptime(pos['buy_date'], '%Y-%m-%d').date()
            days_since_buy = len([d for d in trading_dates if d > buy_date])
            
            positions.append({
                'ts_code': pos['ts_code'],
                'stock_name': pos['stock_name'],
                'rec_date': pos.get('rec_date'),
                'buy_date': buy_date,
                'buy_price': pos['buy_price'],
                'days_held': days_since_buy,
                'days_remaining': MAX_HOLD_DAYS - days_since_buy,
                'final_score': pos.get('final_score', 0),
                'source': pos.get('source'),
                'target_1': pos.get('target_1'),
                'stop_loss': pos.get('stop_loss'),
                'shares': pos.get('shares', 0),
                'status': '持仓中',
            })
        
        return positions
    
    def get_available_slots(self) -> int:
        """获取可用仓位数量"""
        return MAX_CONCURRENT - len(self.tracker.get('open_positions', []))
    
    def get_account_summary(self) -> Dict:
        """获取账户摘要"""
        open_positions = self.tracker.get('open_positions', [])
        history = self.tracker.get('history', [])
        
        total_buy = sum(pos.get('buy_amount', 0) for pos in open_positions)
        total_value = sum(pos.get('buy_amount', 0) for pos in open_positions)
        
        wins = [t for t in history if t.get('profit', 0) > 0]
        losses = [t for t in history if t.get('profit', 0) <= 0]
        
        return {
            'cash': round(self.tracker['cash'], 2),
            'initial_capital': self.tracker['initial_capital'],
            'current_date': self.tracker['current_date'],
            'open_positions_count': len(open_positions),
            'available_slots': self.get_available_slots(),
            'total_trades': len(history),
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': round(len(wins) / len(history) * 100, 1) if history else 0,
            'total_profit': round(sum(t.get('profit', 0) for t in history), 2),
            'avg_profit_pct': round(sum(t.get('profit_pct', 0) for t in history) / len(history), 2) if history else 0,
        }
    
    def reset_tracker(self, start_date: date = None, capital: float = 100000.0):
        """重置跟踪器（开始新的跟单周期）"""
        self.tracker = {
            'start_date': str(start_date or date.today()),
            'current_date': str(start_date or date.today()),
            'initial_capital': capital,
            'cash': capital,
            'open_positions': [],
            'history': [],
            'config': {
                'max_concurrent': MAX_CONCURRENT,
                'profit_pct': PROFIT_PCT,
                'stop_pct': STOP_PCT,
                'max_hold_days': MAX_HOLD_DAYS,
                'position_pct': POSITION_PCT,
            }
        }
        self._save_tracker()
        print(f"  ✅ 跟踪器已重置，起始日期: {self.tracker['start_date']}")
    
    def close(self):
        """关闭数据库连接"""
        self.conn.close()


def main():
    print("=" * 80)
    print("持仓管理器")
    print("=" * 80)
    
    pm = PositionManager()
    today = date.today()
    
    print(f"\n📅 当前日期: {today}")
    
    summary = pm.get_account_summary()
    print(f"\n💰 账户状态:")
    print(f"  初始资金: ¥{summary['initial_capital']:,.2f}")
    print(f"  当前现金: ¥{summary['cash']:,.2f}")
    print(f"  当前持仓: {summary['open_positions_count']} / {MAX_CONCURRENT} 只")
    print(f"  可用仓位: {summary['available_slots']} 个")
    
    print(f"\n📊 交易统计:")
    print(f"  总交易数: {summary['total_trades']} 笔")
    print(f"  盈利: {summary['wins']} 笔 | 亏损: {summary['losses']} 笔")
    print(f"  胜率: {summary['win_rate']}%")
    print(f"  总盈亏: ¥{summary['total_profit']:,.2f}")
    print(f"  平均收益: {summary['avg_profit_pct']:+.2f}%")
    
    open_positions = pm.get_open_positions(today)
    if open_positions:
        print(f"\n📦 当前持仓:")
        for pos in open_positions:
            print(f"  {pos['stock_name']} ({pos['ts_code']})")
            print(f"     买入价: ¥{pos['buy_price']:.2f}")
            print(f"     止盈价: ¥{pos['target_1']:.2f}")
            print(f"     止损价: ¥{pos['stop_loss']:.2f}")
            print(f"     持有天数: {pos['days_held']} / {MAX_HOLD_DAYS}")
    
    history = pm.tracker.get('history', [])
    if history:
        print(f"\n📜 最近5笔交易:")
        for trade in history[-5:]:
            profit_sign = '+' if trade['profit'] > 0 else ''
            print(f"  {trade['stock_name']} ({trade['trade_date'] if 'trade_date' in trade else trade.get('sell_date', '-')})")
            print(f"     买入: ¥{trade['buy_price']:.2f} | 卖出: ¥{trade['sell_price']:.2f}")
            print(f"     盈亏: {profit_sign}¥{trade['profit']:.2f} ({trade['profit_pct']:+.2f}%)")
            print(f"     原因: {trade['sell_reason']}")
    
    pm.close()


if __name__ == '__main__':
    main()