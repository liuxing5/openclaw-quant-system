"""运行融合元策略回测（使用 PostgreSQL 数据源，完全自包含）"""
import os
import sys
import logging
import time
import traceback

sys.stdout.reconfigure(line_buffering=True)

# 设置数据库连接
os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '6543'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

logging.basicConfig(level=logging.WARNING,
                    format='%(asctime)s %(levelname)s %(message)s')

import numpy as np
import pandas as pd
from datetime import date, datetime, timedelta, timezone
from psycopg2.extras import RealDictCursor
from dataclasses import dataclass
from typing import List, Dict, Optional
from pathlib import Path

from core.db.connection import get_db, close_db_session

BEIJING_TZ = timezone(timedelta(hours=8))


@dataclass
class SimplePosition:
    ts_code: str
    entry_date: date
    entry_price: float
    shares: int
    highest_price: float = 0.0
    meta_score: float = 0.0

    def __post_init__(self):
        if self.highest_price == 0:
            self.highest_price = self.entry_price


def _ema(arr, span):
    alpha = 2.0 / (span + 1)
    out = np.empty(len(arr), dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def _query(sql, params=None):
    """执行SQL查询，返回所有行，自动关闭游标"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def _query_one(sql, params=None):
    """执行SQL查询，返回一行"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql, params)
    row = cur.fetchone()
    cur.close()
    return row


def _check_exit_conditions(pos: SimplePosition, current_price: float,
                           eval_date: date, prices_df: pd.DataFrame) -> Optional[str]:
    """检查退出条件，返回退出原因或None"""
    pnl_pct = (current_price - pos.entry_price) / pos.entry_price
    holding_days = (eval_date - pos.entry_date).days

    # E1: 硬止损 7%
    if pnl_pct <= -0.07:
        return f'硬止损({pnl_pct:.1%})'

    # E2: 移动止盈 - 盈利>3%后从高点回撤>2.5%
    if current_price > pos.highest_price:
        pos.highest_price = current_price
    if pnl_pct >= 0.03:
        drawdown = (pos.highest_price - current_price) / pos.highest_price
        if drawdown >= 0.025:
            return f'移动止盈(回撤{drawdown:.1%})'

    # E3: 时间止损 - 持仓>=14天
    if holding_days >= 14:
        return f'时间止损({holding_days}天)'

    # E7: 止盈 - 盈利>=25%
    if pnl_pct >= 0.25:
        return f'止盈({pnl_pct:.1%})'

    # E4: MACD死叉
    if len(prices_df) >= 30:
        close = prices_df['close'].values.astype(float)
        dif = _ema(close, 12) - _ema(close, 26)
        dea = _ema(dif, 9)
        if len(dif) >= 2 and dif[-2] > dea[-2] and dif[-1] <= dea[-1]:
            return 'MACD死叉'

    # E5: 破位放量 - 跌破5日均线且量比>=1.2
    if len(prices_df) >= 6:
        close = prices_df['close'].values.astype(float)
        amount = prices_df['amount'].values.astype(float)
        ma5 = close[-5:].mean()
        vol_ma5 = amount[-6:-1].mean()
        if close[-1] < ma5 and vol_ma5 > 0 and amount[-1] / vol_ma5 >= 1.2:
            return '破位放量'

    # E6: 高量阴线 - 量比>=3且收阴
    if len(prices_df) >= 6:
        amount = prices_df['amount'].values.astype(float)
        pct_chg = prices_df['pct_chg'].values.astype(float)
        vol_ma5 = amount[-6:-1].mean()
        if vol_ma5 > 0 and amount[-1] / vol_ma5 >= 3 and pct_chg[-1] < 0:
            return '高量阴线'

    return None


def run_optimized_backtest(start_date: str, end_date: str,
                           initial_capital: float = 1_000_000,
                           max_positions: int = 3):
    """优化的融合元策略回测"""

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    # 测试数据库连接
    print("测试数据库连接...")
    row = _query_one("""
        SELECT COUNT(DISTINCT trade_date) as cnt FROM daily_quotes
        WHERE trade_date >= %s AND trade_date <= %s
    """, (start, end))
    print(f"  交易日数: {row['cnt']}")

    # 获取交易日
    rows = _query("""
        SELECT DISTINCT trade_date FROM daily_quotes
        WHERE trade_date >= %s AND trade_date <= %s
        ORDER BY trade_date;
    """, (start, end))
    trading_days = [r['trade_date'] for r in rows]
    print(f"交易日: {len(trading_days)} 天")

    # 回测参数
    capital = initial_capital
    positions: Dict[str, SimplePosition] = {}
    daily_equity = []
    all_trades = []
    layer_stats = {'L0_reject': 0, 'L1_pass': [], 'final': []}
    recent_pnls = []  # 反马丁格尔：追踪最近盈亏

    t_start = time.time()

    for i, trade_date in enumerate(trading_days):
        try:
            # ── 1. 评估退出 ──
            if positions:
                pos_codes = list(positions.keys())

                # 获取当日收盘价
                price_rows = _query("""
                    SELECT ts_code, close as price FROM daily_quotes
                    WHERE trade_date = %s AND ts_code = ANY(%s)
                """, (trade_date, pos_codes))
                close_prices = {r['ts_code']: float(r['price']) for r in price_rows if r['price']}

                # 获取持仓股票的近期K线
                kline_rows = _query("""
                    SELECT ts_code, trade_date, close, amount, pct_chg
                    FROM daily_quotes
                    WHERE trade_date >= %s AND trade_date <= %s
                      AND ts_code = ANY(%s)
                    ORDER BY ts_code, trade_date
                """, (trade_date - timedelta(days=90), trade_date, pos_codes))

                exit_kline = {}
                if kline_rows:
                    kline_df = pd.DataFrame(kline_rows)
                    for c in ['close', 'amount', 'pct_chg']:
                        kline_df[c] = pd.to_numeric(kline_df[c], errors='coerce')
                    for code, grp in kline_df.groupby('ts_code'):
                        exit_kline[code] = grp.sort_values('trade_date')

                # 检查退出
                to_close = []
                for ts_code, pos in positions.items():
                    current_price = close_prices.get(ts_code)
                    if current_price is None or current_price <= 0:
                        continue
                    prices_df = exit_kline.get(ts_code, pd.DataFrame())
                    exit_reason = _check_exit_conditions(pos, current_price, trade_date, prices_df)
                    if exit_reason:
                        to_close.append((ts_code, current_price, exit_reason))

                # 执行退出（T+1开盘卖出）
                next_idx = i + 1
                if next_idx < len(trading_days) and to_close:
                    next_date = trading_days[next_idx]
                    exit_codes = [c for c, _, _ in to_close]
                    open_rows = _query("""
                        SELECT ts_code, open as price FROM daily_quotes
                        WHERE trade_date = %s AND ts_code = ANY(%s)
                    """, (next_date, exit_codes))
                    open_prices = {r['ts_code']: float(r['price']) for r in open_rows if r['price']}

                    for ts_code, signal_price, exit_reason in to_close:
                        exit_price = open_prices.get(ts_code, signal_price)
                        exit_price_adj = exit_price * 0.999
                        pos = positions[ts_code]
                        pnl_pct = (exit_price_adj - pos.entry_price) / pos.entry_price
                        holding_days = (next_date - pos.entry_date).days
                        commission = exit_price_adj * pos.shares * 0.001

                        all_trades.append({
                            'ts_code': ts_code,
                            'entry_date': str(pos.entry_date),
                            'exit_date': str(next_date),
                            'entry_price': round(pos.entry_price, 2),
                            'exit_price': round(exit_price_adj, 2),
                            'shares': pos.shares,
                            'pnl_pct': round(pnl_pct, 4),
                            'holding_days': holding_days,
                            'exit_reason': exit_reason,
                            'meta_score': pos.meta_score,
                            'commission': round(commission, 2),
                        })
                        capital += exit_price_adj * pos.shares - commission
                        recent_pnls.append(pnl_pct)
                        if len(recent_pnls) > 5:
                            recent_pnls = recent_pnls[-5:]
                        del positions[ts_code]

            # ── 2. Layer 0: 大盘风控 ──
            row = _query_one("""
                SELECT COUNT(*) FILTER (WHERE pct_chg > 0) as adv,
                       COUNT(*) as total
                FROM daily_quotes WHERE trade_date = %s
            """, (trade_date,))

            adv = int(row['adv'] or 0)
            total = int(row['total'] or 1)

            if adv / total < 0.45:
                layer_stats['L0_reject'] += 1
                equity = _calc_equity(positions, capital, trade_date)
                daily_equity.append({'date': str(trade_date), 'equity': equity,
                                     'positions': len(positions)})
                if (i + 1) % 10 == 0:
                    print(f"  {i+1}/{len(trading_days)} ({trade_date}): L0风控跳过 权益{equity:,.0f}")
                continue

            # ── 3. 获取活跃标的（限制30只） ──
            active_rows = _query("""
                SELECT ts_code, pct_chg, amount, close
                FROM daily_quotes
                WHERE trade_date = %s
                  AND amount > 300000000
                  AND pct_chg > 1.0
                  AND pct_chg < 7
                ORDER BY amount DESC
                LIMIT 30
            """, (trade_date,))

            if not active_rows:
                equity = _calc_equity(positions, capital, trade_date)
                daily_equity.append({'date': str(trade_date), 'equity': equity,
                                     'positions': len(positions)})
                continue

            active_codes = [r['ts_code'] for r in active_rows]

            # ── 4. 获取60日K线（单次查询） ──
            lookback_start = trade_date - timedelta(days=90)
            kline_rows = _query("""
                SELECT ts_code, trade_date, open, high, low, close, volume, amount, pct_chg
                FROM daily_quotes
                WHERE trade_date >= %s AND trade_date <= %s
                  AND ts_code = ANY(%s)
                ORDER BY ts_code, trade_date
            """, (lookback_start, trade_date, active_codes))

            if not kline_rows:
                equity = _calc_equity(positions, capital, trade_date)
                daily_equity.append({'date': str(trade_date), 'equity': equity,
                                     'positions': len(positions)})
                continue

            df = pd.DataFrame(kline_rows)
            for c in ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']:
                df[c] = pd.to_numeric(df[c], errors='coerce')

            # ── 5. 计算因子+启动信号+隔夜评分 ──
            results = []
            for ts_code, group in df.groupby('ts_code'):
                group = group.sort_values('trade_date').tail(60)
                if len(group) < 20:
                    continue

                close = group['close'].values.astype(float)
                high = group['high'].values.astype(float)
                low = group['low'].values.astype(float)
                amount = group['amount'].values.astype(float)
                pct_chg = group['pct_chg'].values.astype(float)
                n = len(close)

                if close[-1] <= 0:
                    continue

                r = {'ts_code': ts_code, 'close': round(float(close[-1]), 2)}

                # ── 因子评分 ──
                # 动量
                mom = float((close[-1] - close[-21]) / (close[-21] + 1e-9)) if n >= 21 else 0
                r['momentum_score'] = round(min(max(mom / 0.15, 0), 1.0) if mom > 0 else 0, 3)

                # 量比
                vol_mean = float(amount[-21:-1].mean()) if n >= 21 else float(amount.mean())
                vr = float(amount[-1]) / (vol_mean + 1e-9)
                r['volume_score'] = round(min((vr - 1.5) / 8.5, 1.0) * 0.7 if 1.5 <= vr <= 10 else 0, 3)

                # RSI
                d = np.diff(close)
                ag = _ema(np.where(d > 0, d, 0.0), 6)
                al = _ema(np.where(d < 0, -d, 0.0), 6)
                rsi = float(100 - 100 / (1 + ag[-1] / (al[-1] + 1e-9)))
                r['rsi_score'] = 0 if rsi >= 75 else (1.0 if rsi <= 35 else round((75 - rsi) / 40, 3))

                # MACD
                dif = _ema(close, 12) - _ema(close, 26)
                dea = _ema(dif, 9)
                hist = (dif - dea) * 2
                ld, la = float(dif[-1]), float(dea[-1])
                lh, ph = float(hist[-1]), float(hist[-2]) if n > 1 else 0
                if ld > la and ph <= 0 and lh > 0:
                    r['macd_score'] = 1.0
                elif ld > la and lh > 0 and lh > ph:
                    r['macd_score'] = 0.7
                elif ld > la:
                    r['macd_score'] = 0.4
                else:
                    r['macd_score'] = 0.1

                # EMA排列
                e5 = float(_ema(close, 5)[-1])
                e10 = float(_ema(close, 10)[-1])
                e20 = float(_ema(close, 20)[-1])
                lc = float(close[-1])
                if lc > e5 > e10 > e20:
                    r['ema_score'] = 1.0
                elif lc > e10 > e20:
                    r['ema_score'] = 0.7
                elif lc > e20:
                    r['ema_score'] = 0.4
                else:
                    r['ema_score'] = 0.0

                # ADX
                p = 14
                tr = np.maximum(high[1:] - low[1:],
                                np.maximum(np.abs(high[1:] - close[:-1]),
                                           np.abs(low[1:] - close[:-1])))
                up = high[1:] - high[:-1]
                dn = low[:-1] - low[1:]
                atr = _ema(tr, p)
                pdi = 100 * _ema(np.where((up > dn) & (up > 0), up, 0.0), p) / (atr + 1e-9)
                mdi = 100 * _ema(np.where((dn > up) & (dn > 0), dn, 0.0), p) / (atr + 1e-9)
                adx_v = float(_ema(100 * np.abs(pdi - mdi) / (pdi + mdi + 1e-9), p)[-1])
                lpdi, lmdi = float(pdi[-1]), float(mdi[-1])
                if adx_v >= 20 and lpdi > lmdi:
                    r['adx_score'] = round(min(adx_v / 50, 1.0), 3)
                elif lpdi > lmdi:
                    r['adx_score'] = 0.3
                else:
                    r['adx_score'] = 0.0

                # SAR
                af_i, af_m = 0.02, 0.2
                sar = np.zeros(n); trend = 1; ep = high[0]; af = af_i; sar[0] = low[0]
                for j in range(1, n):
                    ps = sar[j-1]
                    if trend == 1:
                        sar[j] = min(ps + af * (ep - ps), low[j-1], low[max(0, j-2)])
                        if low[j] < sar[j]:
                            trend = -1; sar[j] = ep; ep = low[j]; af = af_i
                        elif high[j] > ep:
                            ep = high[j]; af = min(af + af_i, af_m)
                    else:
                        sar[j] = max(ps + af * (ep - ps), high[j-1], high[max(0, j-2)])
                        if high[j] > sar[j]:
                            trend = 1; sar[j] = ep; ep = high[j]; af = af_i
                        elif low[j] < ep:
                            ep = low[j]; af = min(af + af_i, af_m)
                ls = float(sar[-1])
                r['sar_score'] = round(min(1.0, max(0.3, 1.0 - (lc - ls) / lc * 10)), 3) if ls < lc else 0.0

                # 因子总分（动量+量能为重）
                r['factor_score'] = round(
                    r['momentum_score'] * 0.25 + r['volume_score'] * 0.20 +
                    r['rsi_score'] * 0.15 + r['macd_score'] * 0.15 +
                    r['ema_score'] * 0.10 + r['adx_score'] * 0.10 +
                    r['sar_score'] * 0.05, 4)

                # ── Layer 3: 启动信号 ──
                launch_score = 0
                if n >= 20:
                    vol_ma20 = amount[-21:-1].mean()
                    if vol_ma20 > 0 and amount[-1] / vol_ma20 >= 2.0:
                        launch_score += 0.4
                    high_20 = close[-21:-1].max()
                    if close[-1] > high_20 * 1.03:
                        launch_score += 0.3
                    if len(dif) >= 2 and dif[-2] <= dea[-2] and dif[-1] > dea[-1]:
                        launch_score += 0.3
                r['launch_score'] = round(min(launch_score, 1.0), 3)

                # ── Layer 5: 隔夜评分 ──
                ov_score = 0
                last_pct = float(pct_chg[-1]) if len(pct_chg) > 0 else 0
                if 2.0 <= last_pct <= 7.0:
                    ov_score += 30
                elif last_pct > 0:
                    ov_score += 15
                if n >= 20:
                    vol_mean20 = amount[-21:-1].mean()
                    if vol_mean20 > 0:
                        vr5 = amount[-1] / vol_mean20
                        if 1.5 <= vr5 <= 10:
                            ov_score += 25
                        elif vr5 > 1:
                            ov_score += 10
                if n >= 5:
                    ma5 = close[-5:].mean()
                    dist = (close[-1] - ma5) / (ma5 + 1e-9)
                    if 0 <= dist <= 0.03:
                        ov_score += 20
                r['overnight_score'] = min(ov_score, 100)

                # ── 融合评分 ──
                r['factor_score_100'] = round(r['factor_score'] * 100, 2)
                r['launch_score_100'] = round(r['launch_score'] * 100, 2)
                r['llm_score'] = 0

                r['meta_score'] = round(
                    r['factor_score_100'] * 0.45 +
                    r['launch_score_100'] * 0.25 +
                    r['overnight_score'] * 0.30, 2)

                # 多层门槛：突破+动量+放量+趋势
                if n < 20:
                    continue
                ma20 = float(close[-20:].mean())
                ma20_ok = float(close[-1]) > ma20
                high20 = float(close[-21:-1].max())
                breakout = float(close[-1]) > high20 * 0.95
                mom5 = float((close[-1] - close[-6]) / (close[-6] + 1e-9)) if n >= 6 else 0
                mom20_val = float((close[-1] - close[-21]) / (close[-21] + 1e-9)) if n >= 21 else 0
                multi_mom = mom5 > 0 and mom20_val > 0.03
                vol20_mean = float(amount[-21:-1].mean()) if n >= 21 else float(amount.mean())
                vol_boom = float(amount[-1]) > vol20_mean * 1.3
                rsi_ok = 35 <= rsi <= 75
                score_ok = (r['factor_score'] >= 0.40 and r['launch_score'] >= 0.3
                          and r['overnight_score'] >= 35)
                signal_ok = breakout or vol_boom
                if score_ok and ma20_ok and multi_mom and rsi_ok and signal_ok:
                    results.append(r)

            layer_stats['L1_pass'].append(len(results))

            if not results:
                equity = _calc_equity(positions, capital, trade_date)
                daily_equity.append({'date': str(trade_date), 'equity': equity,
                                     'positions': len(positions)})
                if (i + 1) % 10 == 0:
                    print(f"  {i+1}/{len(trading_days)} ({trade_date}): 无信号 权益{equity:,.0f}")
                continue

            # 排序取前10
            results.sort(key=lambda x: x['meta_score'], reverse=True)
            results = results[:10]
            layer_stats['final'].append(len(results))

            # ── 6. 开仓（T+1开盘买入） ──
            next_idx = i + 1
            if next_idx >= len(trading_days):
                equity = _calc_equity(positions, capital, trade_date)
                daily_equity.append({'date': str(trade_date), 'equity': equity,
                                     'positions': len(positions)})
                continue

            next_date = trading_days[next_idx]
            candidate_codes = [r['ts_code'] for r in results
                               if r['ts_code'] not in positions]
            if candidate_codes:
                open_rows = _query("""
                    SELECT ts_code, open as price FROM daily_quotes
                    WHERE trade_date = %s AND ts_code = ANY(%s)
                """, (next_date, candidate_codes))
                open_prices = {r['ts_code']: float(r['price']) for r in open_rows if r['price']}

                for r in results:
                    if len(positions) >= max_positions:
                        break
                    if r['ts_code'] in positions:
                        continue

                    entry_price = open_prices.get(r['ts_code'])
                    if entry_price is None or entry_price <= 0:
                        continue

                    current_eq = capital + sum(
                        (open_prices.get(p.ts_code, p.entry_price) * p.shares)
                        for p in positions.values())
                    position_value = current_eq * 0.30
                    shares = int(position_value / (entry_price * 100)) * 100
                    if shares <= 0:
                        shares = 100

                    entry_price_adj = entry_price * 1.001
                    commission = entry_price_adj * shares * 0.001
                    cost = entry_price_adj * shares + commission

                    if cost > capital:
                        continue

                    capital -= cost
                    positions[r['ts_code']] = SimplePosition(
                        ts_code=r['ts_code'],
                        entry_date=next_date,
                        entry_price=entry_price_adj,
                        shares=shares,
                        meta_score=r.get('meta_score', 0),
                    )

            # ── 记录权益 ──
            equity = _calc_equity(positions, capital, trade_date)
            daily_equity.append({'date': str(trade_date), 'equity': equity,
                                 'positions': len(positions)})

            # 进度
            elapsed = time.time() - t_start
            avg = elapsed / (i + 1)
            remaining = avg * (len(trading_days) - i - 1)
            if (i + 1) % 5 == 0 or i == len(trading_days) - 1:
                print(f"  进度 {i+1}/{len(trading_days)} ({trade_date}): "
                      f"持仓{len(positions)}只 权益{equity:,.0f} "
                      f"信号{len(results)}只 剩余{remaining:.0f}s")

        except Exception as e:
            print(f"  {trade_date} 失败: {e}")
            traceback.print_exc()

    # 强制平仓
    for ts_code in list(positions.keys()):
        pos = positions[ts_code]
        close_rows = _query("""
            SELECT close as price FROM daily_quotes
            WHERE trade_date = %s AND ts_code = %s
        """, (end, ts_code))
        last_price = float(close_rows[0]['price']) if close_rows else pos.entry_price
        pnl_pct = (last_price - pos.entry_price) / pos.entry_price
        holding_days = (end - pos.entry_date).days
        all_trades.append({
            'ts_code': ts_code,
            'entry_date': str(pos.entry_date),
            'exit_date': str(end),
            'entry_price': round(pos.entry_price, 2),
            'exit_price': round(last_price, 2),
            'shares': pos.shares,
            'pnl_pct': round(pnl_pct, 4),
            'holding_days': holding_days,
            'exit_reason': '回测结束',
            'meta_score': pos.meta_score,
            'commission': 0,
        })
    positions.clear()

    total_elapsed = time.time() - t_start

    # ── 汇总 ──
    summary = _build_summary(all_trades, daily_equity, layer_stats,
                             total_elapsed, start_date, end_date, initial_capital)
    print(summary)

    # 保存结果
    out_dir = Path('./results')
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(BEIJING_TZ).strftime('%Y%m%d_%H%M')

    if all_trades:
        trades_df = pd.DataFrame(all_trades)
        trades_path = out_dir / f"meta_bt_trades_{timestamp}.csv"
        trades_df.to_csv(trades_path, index=False, encoding='utf-8-sig')
        print(f"\n  交易记录: {trades_path}")

    if daily_equity:
        eq_df = pd.DataFrame(daily_equity)
        eq_path = out_dir / f"meta_bt_equity_{timestamp}.csv"
        eq_df.to_csv(eq_path, index=False, encoding='utf-8-sig')
        print(f"  权益曲线: {eq_path}")

    report_path = out_dir / f"meta_bt_report_{timestamp}.txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(summary)
    print(f"  回测报告: {report_path}")

    close_db_session()
    return {'trades': all_trades, 'daily_equity': daily_equity, 'summary': summary}


def _calc_equity(positions: Dict[str, SimplePosition], cash: float, eval_date: date) -> float:
    if not positions:
        return cash
    codes = list(positions.keys())
    price_rows = _query("""
        SELECT ts_code, close as price FROM daily_quotes
        WHERE trade_date = %s AND ts_code = ANY(%s)
    """, (eval_date, codes))
    prices = {r['ts_code']: float(r['price']) for r in price_rows if r['price']}
    equity = cash
    for ts_code, pos in positions.items():
        price = prices.get(ts_code, pos.entry_price)
        equity += price * pos.shares
    return equity


def _build_summary(trades, daily_equity, layer_stats, elapsed, start_date, end_date, initial_capital):
    lines = []
    lines.append("=" * 70)
    lines.append("  融合元策略回测汇总")
    lines.append("=" * 70)
    lines.append(f"  回测区间: {start_date} ~ {end_date}")
    lines.append(f"  初始资金: {initial_capital:,.0f}")
    lines.append(f"  回测耗时: {elapsed:.1f}s")
    lines.append("")

    if trades:
        pnls = [t['pnl_pct'] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        lines.append("--- 交易统计 ---")
        lines.append(f"  总交易数: {len(trades)}")
        lines.append(f"  胜率: {len(wins)/len(pnls):.1%}" if pnls else "  胜率: N/A")
        lines.append(f"  平均收益: {np.mean(pnls):.2%}" if pnls else "  平均收益: N/A")
        lines.append(f"  中位数收益: {np.median(pnls):.2%}" if pnls else "  中位数: N/A")
        lines.append(f"  最大单笔盈利: {max(pnls):.2%}" if pnls else "  最大盈利: N/A")
        lines.append(f"  最大单笔亏损: {min(pnls):.2%}" if pnls else "  最大亏损: N/A")
        lines.append(f"  盈利交易平均: {np.mean(wins):.2%}" if wins else "  盈利均: N/A")
        lines.append(f"  亏损交易平均: {np.mean(losses):.2%}" if losses else "  亏损均: N/A")
        if wins and losses:
            lines.append(f"  盈亏比: {abs(np.mean(wins)/np.mean(losses)):.2f}")
        else:
            lines.append("  盈亏比: N/A")
        lines.append(f"  平均持仓天数: {np.mean([t['holding_days'] for t in trades]):.1f}")
        lines.append("")

        exit_reasons = {}
        for t in trades:
            reason = t['exit_reason'].split('(')[0]
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
        lines.append("--- 退出原因分布 ---")
        for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
            lines.append(f"  {reason}: {count} ({count/len(trades):.1%})")
        lines.append("")

        rets = np.array(pnls)
        lines.append("--- 收益分布 ---")
        lines.append(f"  >5%:  {np.mean(rets > 0.05):.1%}")
        lines.append(f"  >3%:  {np.mean(rets > 0.03):.1%}")
        lines.append(f"  >0%:  {np.mean(rets > 0):.1%}")
        lines.append(f"  <-3%: {np.mean(rets < -0.03):.1%}")
        lines.append(f"  <-5%: {np.mean(rets < -0.05):.1%}")
        lines.append(f"  <-8%: {np.mean(rets < -0.08):.1%}")
        lines.append("")
    else:
        lines.append("  无交易记录")
        lines.append("")

    if daily_equity:
        eq_df = pd.DataFrame(daily_equity)
        final = eq_df['equity'].iloc[-1]
        total_return = (final - initial_capital) / initial_capital
        cummax = eq_df['equity'].cummax()
        max_dd = ((eq_df['equity'] - cummax) / cummax).min()

        lines.append("--- 权益曲线 ---")
        lines.append(f"  初始权益: {initial_capital:,.0f}")
        lines.append(f"  最终权益: {final:,.0f}")
        lines.append(f"  总收益率: {total_return:.2%}")
        lines.append(f"  最大回撤: {max_dd:.2%}")
        lines.append("")

    lines.append("--- 各层漏斗统计 ---")
    for layer, counts in layer_stats.items():
        if counts and isinstance(counts, list):
            avg = np.mean(counts) if counts else 0
            lines.append(f"  {layer}: 平均 {avg:.0f} 只/日 (共{len(counts)}日)")
        elif isinstance(counts, (int, float)):
            lines.append(f"  {layer}: {counts}")
    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


if __name__ == "__main__":
    import traceback
    out_path = r"D:\pythonProject\openclaw-quant-system\backtest_result.txt"
    out = open(out_path, "w", encoding="utf-8")
    def w(msg):
        out.write(str(msg) + "\n")
        out.flush()
        print(msg)
    
    try:
        w("Starting backtest...")
        result = run_optimized_backtest("2026-01-01", "2026-05-25")
        w("Backtest completed.")
        if result and result.get('trades'):
            w(f"\nTotal trades: {len(result['trades'])}")
            for t in result['trades']:
                w(f"{t['ts_code']} | entry: {t['entry_date']} @ {t['entry_price']} | exit: {t['exit_date']} @ {t['exit_price']} | PnL: {t['pnl_pct']:.2%} | {t['exit_reason']}")
    except Exception as e:
        w(f"FATAL ERROR: {e}")
        w(traceback.format_exc())
    finally:
        out.close()
