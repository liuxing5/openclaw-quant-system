"""
每日复盘模块 — 核心纪律④落地
================================
每晚复盘，按漏斗条件回测当天推票：
  任何一项未严格满足即推倒重来，
  连续3次止损失败则暂停交易一天。

"严格执行纪律，复盘强化规则"——④
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone, timedelta
from typing import Dict, List, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db
from psycopg2.extras import RealDictCursor

BEIJING_TZ = timezone(timedelta(hours=8))

REVIEW_STATE_FILE = os.path.join(os.path.dirname(__file__), 'funnel_review_state.json')


def _ensure_db():
    try:
        return get_db()
    except Exception:
        return None


class DailyReviewer:
    """每日复盘引擎"""

    def __init__(self):
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(REVIEW_STATE_FILE):
            try:
                with open(REVIEW_STATE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            'consecutive_fails': 0,
            'total_trades': 0,
            'total_wins': 0,
            'avg_profit_loss': 0.0,
            'is_suspended': False,
            'suspended_until': None,
            'last_updated': None,
            'review_history': [],
        }

    def _save_state(self):
        self.state['last_updated'] = datetime.now(BEIJING_TZ).isoformat()
        try:
            with open(REVIEW_STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def review_yesterday(
        self,
        trade_date: date = None,
        funnel_result: dict = None,
        verbose: bool = True,
    ) -> dict:
        """
        复盘昨日推荐：
        1. 从 daily_candidates 读取昨日推荐
        2. 从 daily_quotes 读取今日表现
        3. 计算T+1收益
        4. 检查止损触发
        5. 更新连续失败计数
        """
        if trade_date is None:
            trade_date = datetime.now(BEIJING_TZ).date()

        yesterday = trade_date - timedelta(days=1)

        if verbose:
            print(f"\n{'='*70}")
            print(f"  🔍 每日复盘 [{yesterday}] → [{trade_date}]")
            print(f"{'='*70}")

        report = {
            'review_date': str(trade_date),
            'trade_date': str(yesterday),
            'candidates_reviewed': 0,
            'wins': 0,
            'losses': 0,
            'stop_loss_hit': 0,
            'total_pnl': 0.0,
            'action': '',
        }

        # 读取昨日推荐
        conn = _ensure_db()
        if not conn:
            report['action'] = '无法连接数据库，跳过复盘'
            if verbose:
                print(f"  ⚠️ {report['action']}")
            return report

        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 从 funnel_results 读昨日漏斗推荐（JSON candidates 列）
        cur.execute("""
            SELECT candidates
            FROM funnel_results
            WHERE trade_date = %s
            ORDER BY trade_date DESC
            LIMIT 1;
        """, (yesterday,))

        row = cur.fetchone()
        picks_raw = json.loads(row['candidates']) if row and row.get('candidates') else []
        # 映射漏斗字段到 review 期望的字段名
        picks = []
        for c in picks_raw:
            entry_price = c.get('entry_price', 0)
            picks.append({
                'ts_code': c.get('ts_code', ''),
                'stock_name': c.get('stock_name', ''),
                'entry_price': entry_price,
                'entry_low': round(entry_price * 0.99, 2) if entry_price else None,
                'entry_high': round(entry_price * 1.01, 2) if entry_price else None,
                'stop_loss': c.get('stop_loss'),
                'target_1': c.get('target_price'),
                'target_2': None,
                'final_score': c.get('score', 0),
            })

        if not picks:
            report['action'] = '昨日无漏斗推荐，无需复盘'
            if verbose:
                print(f"  ℹ️ {report['action']}")
            cur.close()
            conn.close()
            return report

        report['candidates_reviewed'] = len(picks)

        # 读取今日行情
        codes = [p['ts_code'] for p in picks]
        cur.execute("""
            SELECT ts_code, open, high, low, close, pct_chg
            FROM daily_quotes
            WHERE ts_code = ANY(%s) AND trade_date = %s;
        """, (codes, trade_date))
        quotes = {q['ts_code']: q for q in cur.fetchall()}
        cur.close()
        conn.close()

        if verbose:
            print(f"\n  昨日推荐: {len(picks)} 只")
            print(f"  {'代码':<14} {'入场':>8} {'止损':>8} "
                  f"{'今开':>8} {'今收':>8} {'涨跌%':>7}  结果")
            print(f"  {'─'*70}")

        for p in picks:
            code = p['ts_code']
            entry = float(p.get('entry_price', 0))
            stop_loss = float(p.get('stop_loss', 0))
            q = quotes.get(code)

            if not q or not q.get('open'):
                if verbose:
                    print(f"  {code:<14} {'─':>8} {'─':>8} {'停牌/无数据':>8}")
                continue

            today_open = float(q['open'])
            today_close = float(q['close'])
            today_pct = float(q.get('pct_chg', 0) or 0)

            # 止损触发检查
            stop_hit = today_open <= stop_loss or float(q['low']) <= stop_loss
            if stop_hit:
                report['stop_loss_hit'] += 1
                report['losses'] += 1
                status = '❌止损'
            elif today_close > entry:
                report['wins'] += 1
                status = '✅止盈'
            else:
                report['losses'] += 1
                status = '⚠️亏损'

            pnl_pct = (today_close - entry) / entry * 100 if entry > 0 else 0
            report['total_pnl'] += pnl_pct

            if verbose:
                print(f"  {code:<14} {entry:>8.2f} {stop_loss:>8.2f} "
                      f"{today_open:>8.2f} {today_close:>8.2f} {today_pct:>7.2f}  {status}")

        # 更新状态
        self._update_from_review(report)

        if verbose:
            print(f"\n  复盘总结:")
            print(f"    推荐 {report['candidates_reviewed']} 只")
            print(f"    止盈 {report['wins']} | 亏损 {report['losses']} "
                  f"| 止损触发 {report['stop_loss_hit']}")
            print(f"    累计盈亏: {report['total_pnl']:.2f}%")
            print(f"    连续失败: {self.state['consecutive_fails']} 次")
            if self.state['is_suspended']:
                print(f"    🚫 暂停交易至: {self.state['suspended_until']}")
            print(f"  {report['action']}")

        return report

    def _update_from_review(self, report: dict):
        """根据复盘结果更新状态"""
        had_failures = report['losses'] > 0 and report['wins'] == 0

        if had_failures:
            self.state['consecutive_fails'] += 1
        else:
            self.state['consecutive_fails'] = 0

        self.state['total_trades'] += report['candidates_reviewed']
        self.state['total_wins'] += report['wins']

        if self.state['total_trades'] > 0:
            total_pnl = self.state.get('total_pnl_sum', 0.0) + report['total_pnl']
            self.state['total_pnl_sum'] = total_pnl
            self.state['avg_profit_loss'] = round(total_pnl / self.state['total_trades'], 2)

        # 连续3次失败 → 暂停一天
        if self.state['consecutive_fails'] >= 3 and not self.state['is_suspended']:
            self.state['is_suspended'] = True
            resume_date = datetime.now(BEIJING_TZ).date() + timedelta(days=1)
            self.state['suspended_until'] = resume_date.isoformat()
            report['action'] = f'🚫 连续{self.state["consecutive_fails"]}次失败，暂停交易至{resume_date}'
        elif self.state['is_suspended']:
            suspended_until = date.fromisoformat(self.state['suspended_until']) if self.state['suspended_until'] else datetime.now(BEIJING_TZ).date()
            if datetime.now(BEIJING_TZ).date() >= suspended_until:
                self.state['is_suspended'] = False
                self.state['suspended_until'] = None
                self.state['consecutive_fails'] = 0
                report['action'] = '✅ 暂停期结束，恢复交易'
            else:
                report['action'] = f'⏳ 仍在暂停期(至{self.state["suspended_until"]})'
        else:
            report['action'] = '正常'

        # 记录复盘历史（保留最近30条）
        self.state['review_history'].append({
            'date': report['trade_date'],
            'candidates': report['candidates_reviewed'],
            'wins': report['wins'],
            'losses': report['losses'],
            'stop_loss_hit': report['stop_loss_hit'],
            'total_pnl': report['total_pnl'],
        })
        self.state['review_history'] = self.state['review_history'][-30:]

        self._save_state()

    def is_trading_allowed(self) -> tuple:
        """检查当日是否允许交易"""
        if self.state['is_suspended']:
            return False, f"暂停交易至{self.state['suspended_until']} (连续{self.state['consecutive_fails']}次失败)"
        return True, "正常"

    def get_summary(self) -> dict:
        """获取当前交易统计摘要"""
        return {
            'total_trades': self.state['total_trades'],
            'total_wins': self.state['total_wins'],
            'win_rate': round(self.state['total_wins'] / max(self.state['total_trades'], 1) * 100, 1),
            'avg_profit_loss': self.state['avg_profit_loss'],
            'consecutive_fails': self.state['consecutive_fails'],
            'is_suspended': self.state['is_suspended'],
            'suspended_until': self.state['suspended_until'],
            'recent_history': self.state['review_history'][-5:],
        }


if __name__ == "__main__":
    reviewer = DailyReviewer()
    summary = reviewer.get_summary()
    print(f"\n📊 交易统计")
    print(f"  总交易: {summary['total_trades']} 只")
    print(f"  胜率: {summary['win_rate']}%")
    print(f"  平均盈亏: {summary['avg_profit_loss']}%")
    print(f"  连续失败: {summary['consecutive_fails']} 次")
    print(f"  当前状态: {'🚫暂停' if summary['is_suspended'] else '✅正常'}")

    result = reviewer.review_yesterday()
    print(f"\n  复盘结果: {result['action']}")
