#!/usr/bin/env python3
"""自包含回测脚本 - 直接SQL，单连接复用"""
import sys, os, time, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

import psycopg2
import pandas as pd
import numpy as np
from datetime import date, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

# 单连接复用
_conn = None
def get_conn():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(
            host='aws-1-ap-northeast-1.pooler.supabase.com',
            port=5432,
            user='postgres.qoakbxswwjqfsgbcgepr',
            password='wYFBB91zViSrk2vl',
            dbname='postgres',
            sslmode='require',
            connect_timeout=30,
        )
        _conn.autocommit = True
    return _conn

def sql_query(sql, params=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchall()

def sql_df(sql, params=None, columns=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    if columns is None:
        columns = [desc[0] for desc in cur.description]
    df = pd.DataFrame(rows, columns=columns)
    return df

# ============================================================
# 配置
# ============================================================
@dataclass
class BtConfig:
    start_date: str = '2026-04-01'
    end_date: str = '2026-05-22'
    initial_capital: float = 1_000_000.0
    max_positions: int = 3          # 减少持仓数量，集中火力
    position_pct: float = 0.30      # 单只仓位增大
    commission: float = 0.001
    slippage: float = 0.001
    min_amount: float = 1e8         # 提高最低成交额，排除小盘股
    l0_min_breadth: float = 0.40    # 提高市场宽度要求，弱市不选
    l1_top_n: int = 30              # 减少候选数量
    l1_min_score: float = 0.40      # 提高因子评分门槛
    stop_loss_pct: float = -0.06    # 止损6%
    max_hold_days: int = 3          # 缩短持仓天数
    min_pct_to_buy: float = 3.0     # 最低涨幅要求3%（更严格）

# ============================================================
# 主回测逻辑
# ============================================================
def run_backtest(cfg: BtConfig):
    logger.info("=" * 70)
    logger.info(f"自包含DB回测 {cfg.start_date} ~ {cfg.end_date}")
    logger.info("=" * 70)

    start = date.fromisoformat(cfg.start_date)
    end = date.fromisoformat(cfg.end_date)

    # 1. 获取交易日
    trading_days = [r[0] for r in sql_query(
        "SELECT DISTINCT trade_date FROM daily_quotes WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date",
        (start, end)
    )]
    logger.info(f"交易日: {len(trading_days)} 天")

    # 2. 跳过指数代理查询（太慢），直接用每日市场宽度判断
    # idx_data 不再需要，市场状态由每日breadth动态判断

    capital = cfg.initial_capital
    positions = {}  # ts_code -> {entry_date, entry_price, shares, meta_score}
    trades = []
    daily_equity = []
    stats = {'l0_reject': 0, 'trades_opened': 0, 'trades_closed': 0, 'no_price': 0}

    def calc_equity(td, capital, positions):
        """计算总权益（现金+持仓市值）"""
        pos_value = 0
        if positions:
            pos_codes = list(positions.keys())
            close_rows = sql_query("""
                SELECT ts_code, close FROM daily_quotes
                WHERE trade_date = %s AND ts_code = ANY(%s)
            """, (td, pos_codes))
            close_map = {r[0]: float(r[1]) if r[1] else None for r in close_rows}
            for code, pos in positions.items():
                c = close_map.get(code)
                pos_value += pos['shares'] * c if c else pos['shares'] * pos['entry_price']
        return capital + pos_value

    for i, td in enumerate(trading_days):
        t0 = time.time()

        # ── 退出评估 ──
        closed_today = []
        if positions:
            # 批量获取持仓股票当日价格
            pos_codes = list(positions.keys())
            price_rows = sql_query("""
                SELECT ts_code, open, close FROM daily_quotes
                WHERE trade_date = %s AND ts_code = ANY(%s)
            """, (td, pos_codes))
            price_map = {r[0]: {'open': float(r[1]) if r[1] else None, 'close': float(r[2]) if r[2] else None}
                        for r in price_rows}

            for code, pos in list(positions.items()):
                prices = price_map.get(code)
                if not prices or prices['open'] is None:
                    continue
                cur_price = prices['open']
                pnl_pct = (cur_price - pos['entry_price']) / pos['entry_price']
                hold_days = (td - pos['entry_date']).days

                if pnl_pct < cfg.stop_loss_pct or hold_days >= cfg.max_hold_days:
                    exit_price = cur_price * (1 - cfg.slippage)
                    commission = exit_price * pos['shares'] * cfg.commission
                    proceeds = exit_price * pos['shares'] - commission
                    capital += proceeds
                    pnl = proceeds - pos['entry_price'] * pos['shares']
                    trades.append({
                        'ts_code': code, 'entry_date': pos['entry_date'], 'exit_date': td,
                        'entry_price': pos['entry_price'], 'exit_price': exit_price,
                        'shares': pos['shares'], 'pnl': pnl, 'pnl_pct': pnl_pct,
                        'exit_reason': 'stop_loss' if pnl_pct < cfg.stop_loss_pct else 'max_hold',
                    })
                    closed_today.append(code)
                    stats['trades_closed'] += 1

        for code in closed_today:
            del positions[code]

        # ── L0: 市场风控 ──
        breadth_row = sql_query("""
            SELECT SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END),
                   COUNT(*) FROM daily_quotes WHERE trade_date = %s
        """, (td,))
        if breadth_row:
            advancers = int(breadth_row[0][0] or 0)
            total = int(breadth_row[0][1] or 1)
            breadth = advancers / total
        else:
            breadth = 0

        if breadth < cfg.l0_min_breadth:
            stats['l0_reject'] += 1
            equity = calc_equity(td, capital, positions)
            daily_equity.append({'date': str(td), 'equity': equity, 'positions': len(positions)})
            continue

        # ── L1: 多因子扫描 ──
        daily_df = sql_df("""
            SELECT ts_code, open, close, pct_chg, turnover_rate, amplitude,
                   volume_ratio, pe_ratio, pb_ratio, main_force_net, amount
            FROM daily_quotes WHERE trade_date = %s AND amount >= %s
            ORDER BY amount DESC
        """, (td, cfg.min_amount), columns=[
            'ts_code','open','close','pct_chg','turnover_rate','amplitude',
            'volume_ratio','pe_ratio','pb_ratio','main_force_net','amount'])
        for c in daily_df.columns[1:]:
            daily_df[c] = pd.to_numeric(daily_df[c], errors='coerce')

        if daily_df.empty:
            daily_equity.append({'date': str(td), 'equity': calc_equity(td, capital, positions), 'positions': len(positions)})
            continue

        # 评分
        scores = pd.DataFrame()
        scores['ts_code'] = daily_df['ts_code']
        for col in ['pct_chg', 'turnover_rate', 'volume_ratio']:
            vals = daily_df[col].fillna(0)
            scores[col+'_n'] = (vals - vals.min()) / (vals.max() - vals.min() + 1e-10)
        vals = daily_df['main_force_net'].fillna(0)
        scores['mf_n'] = (vals - vals.min()) / (vals.max() - vals.min() + 1e-10)
        scores['total'] = (scores['pct_chg_n']*0.3 + scores['turnover_rate_n']*0.25 +
                          scores['volume_ratio_n']*0.25 + scores['mf_n']*0.2)

        l1 = scores[scores['total'] >= cfg.l1_min_score].nlargest(cfg.l1_top_n, 'total')
        l1_codes = l1['ts_code'].tolist()

        if not l1_codes:
            daily_equity.append({'date': str(td), 'equity': calc_equity(td, capital, positions), 'positions': len(positions)})
            continue

        # ── L2: 基本面过滤（宽松版：只排除明确亏损） ──
        l2_df = daily_df[daily_df['ts_code'].isin(l1_codes)]
        # PE<0 排除（但PE为NaN时保留）
        l2_df = l2_df[~((l2_df['pe_ratio'].notna()) & (l2_df['pe_ratio'] < 0))]
        l2_codes = l2_df['ts_code'].tolist()

        if not l2_codes:
            daily_equity.append({'date': str(td), 'equity': calc_equity(td, capital, positions), 'positions': len(positions)})
            continue

        # L3+L5 简化评分
        l2_scores = l1[l1['ts_code'].isin(l2_codes)].copy()
        # 给每只股票一个overnight基础分
        l2_daily = daily_df[daily_df['ts_code'].isin(l2_codes)].set_index('ts_code')
        l2_scores['pct_chg'] = l2_scores['ts_code'].map(l2_daily['pct_chg']).fillna(0)

        # 最低涨幅过滤
        l2_scores = l2_scores[l2_scores['pct_chg'] >= cfg.min_pct_to_buy]
        if l2_scores.empty:
            equity = calc_equity(td, capital, positions)
            daily_equity.append({'date': str(td), 'equity': equity, 'positions': len(positions)})
            continue

        l2_scores['overnight_base'] = np.where(
            l2_scores['pct_chg'] > 5, 20,
            np.where(l2_scores['pct_chg'] > 3, 30,
            np.where(l2_scores['pct_chg'] > 1, 25,
            np.where(l2_scores['pct_chg'] > 0, 15, 5))))
        # 综合分
        l2_scores['meta_score'] = l2_scores['total'] * 60 + l2_scores['overnight_base']
        l2_scores = l2_scores.sort_values('meta_score', ascending=False)

        # ── 开仓 ──
        next_idx = i + 1
        if next_idx >= len(trading_days):
            # 最后一天：记录含持仓的权益
            equity = calc_equity(td, capital, positions)
            daily_equity.append({'date': str(td), 'equity': equity, 'positions': len(positions)})
            continue

        next_date = trading_days[next_idx]
        # 批量获取次日开盘价
        candidate_codes = l2_scores['ts_code'].tolist()
        next_price_rows = sql_query("""
            SELECT ts_code, open FROM daily_quotes
            WHERE trade_date = %s AND ts_code = ANY(%s)
        """, (next_date, candidate_codes))
        next_price_map = {r[0]: float(r[1]) if r[1] else None for r in next_price_rows}

        opened = 0
        for _, row in l2_scores.iterrows():
            if len(positions) >= cfg.max_positions:
                break
            code = row['ts_code']
            if code in positions:
                continue

            entry_price = next_price_map.get(code)
            if entry_price is None or entry_price <= 0:
                stats['no_price'] += 1
                continue

            position_value = cfg.initial_capital * cfg.position_pct
            shares = int(position_value / (entry_price * 100)) * 100
            if shares <= 0:
                shares = 100

            entry_adj = entry_price * (1 + cfg.slippage)
            commission = entry_adj * shares * cfg.commission
            cost = entry_adj * shares + commission

            if cost > capital:
                continue

            capital -= cost
            positions[code] = {
                'entry_date': next_date, 'entry_price': entry_adj,
                'shares': shares, 'meta_score': row['meta_score'],
            }
            opened += 1
            stats['trades_opened'] += 1

        # ── 记录权益 ──
        equity = calc_equity(td, capital, positions)
        daily_equity.append({'date': str(td), 'equity': equity, 'positions': len(positions)})

        if (i+1) % 5 == 0 or i == len(trading_days)-1:
            elapsed = time.time() - t0
            logger.info(f"  {i+1}/{len(trading_days)} ({td}): 持仓{len(positions)}只 "
                       f"权益{equity:,.0f} 开{opened}笔 {elapsed:.1f}s")

    # ── 清算剩余持仓 ──
    last_date = trading_days[-1] if trading_days else end
    for code, pos in list(positions.items()):
        price_row = sql_query(
            "SELECT close FROM daily_quotes WHERE ts_code=%s AND trade_date=%s",
            (code, last_date))
        exit_price = float(price_row[0][0]) if price_row and price_row[0][0] else pos['entry_price']
        exit_price_adj = exit_price * (1 - cfg.slippage)
        commission = exit_price_adj * pos['shares'] * cfg.commission
        proceeds = exit_price_adj * pos['shares'] - commission
        capital += proceeds
        pnl = proceeds - pos['entry_price'] * pos['shares']
        pnl_pct = (exit_price_adj - pos['entry_price']) / pos['entry_price']
        trades.append({
            'ts_code': code, 'entry_date': pos['entry_date'], 'exit_date': last_date,
            'entry_price': pos['entry_price'], 'exit_price': exit_price_adj,
            'shares': pos['shares'], 'pnl': pnl, 'pnl_pct': pnl_pct,
            'exit_reason': 'end_of_backtest',
        })
        stats['trades_closed'] += 1
    positions.clear()

    # ── 生成报告 ──
    eq_df = pd.DataFrame(daily_equity)
    total_return = (capital - cfg.initial_capital) / cfg.initial_capital * 100
    max_dd = 0
    if not eq_df.empty:
        peak = eq_df['equity'].expanding().max()
        dd = (eq_df['equity'] - peak) / peak
        max_dd = dd.min() * 100

    report = f"""
======================================================================
  自包含DB回测报告
======================================================================
  回测区间: {cfg.start_date} ~ {cfg.end_date}
  初始资金: {cfg.initial_capital:,.0f}
  最终权益: {capital:,.0f}
  总收益率: {total_return:.2f}%
  最大回撤: {max_dd:.2f}%
  交易次数: {len(trades)} (开{stats['trades_opened']} 平{stats['trades_closed']})
  L0拒绝: {stats['l0_reject']} 天
  无价格: {stats['no_price']} 次
  参数: max_pos={cfg.max_positions} pos_pct={cfg.position_pct}
        stop_loss={cfg.stop_loss_pct} max_hold={cfg.max_hold_days}天
        min_amount={cfg.min_amount/1e8:.0f}亿 min_pct={cfg.min_pct_to_buy}%
"""
    # 交易统计
    if trades:
        wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]
        win_rate = len(wins) / len(trades) * 100 if trades else 0
        avg_win = np.mean([t['pnl_pct'] for t in wins]) * 100 if wins else 0
        avg_loss = np.mean([t['pnl_pct'] for t in losses]) * 100 if losses else 0
        profit_factor = abs(sum(t['pnl'] for t in wins) / sum(t['pnl'] for t in losses)) if losses and sum(t['pnl'] for t in losses) != 0 else float('inf')
        report += f"""  胜率: {win_rate:.1f}% ({len(wins)}/{len(trades)})
  平均盈利: +{avg_win:.2f}%  平均亏损: {avg_loss:.2f}%
  盈亏比: {profit_factor:.2f}
"""
    report += "======================================================================\n"
    logger.info(report)

    # 保存
    from pathlib import Path
    out_dir = Path('./results')
    out_dir.mkdir(parents=True, exist_ok=True)

    if trades:
        pd.DataFrame(trades).to_csv(out_dir / 'standalone_bt_trades.csv', index=False, encoding='utf-8-sig')
    if not eq_df.empty:
        eq_df.to_csv(out_dir / 'standalone_bt_equity.csv', index=False, encoding='utf-8-sig')
    with open(out_dir / 'standalone_bt_report.txt', 'w', encoding='utf-8') as f:
        f.write(report)

    # 关闭连接
    if _conn and not _conn.closed:
        _conn.close()

    return {'summary': report, 'trades': trades, 'daily_equity': eq_df}

if __name__ == '__main__':
    cfg = BtConfig()
    try:
        result = run_backtest(cfg)
        print("SUCCESS!", flush=True)
    except Exception as e:
        import traceback
        print(f"ERROR: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
    finally:
        if _conn and not _conn.closed:
            _conn.close()
