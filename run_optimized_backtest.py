"""优化回测：2026年1月-5月25日，提高收益率，降低回撤"""
import sys, os
sys.path.insert(0, '.')

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '6543'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

import numpy as np
import pandas as pd
from datetime import date, timedelta
from psycopg2.extras import RealDictCursor
from dataclasses import dataclass
from typing import Dict, Optional
from core.db.connection import get_db, close_db_session

@dataclass
class Position:
    ts_code: str
    entry_date: date
    entry_price: float
    shares: int
    highest_price: float = 0.0

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
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return rows

def _query_one(sql, params=None):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql, params)
    row = cur.fetchone()
    cur.close()
    return row

def check_exit(pos, current_price, eval_date, prices_df):
    pnl = (current_price - pos.entry_price) / pos.entry_price
    days = (eval_date - pos.entry_date).days

    if current_price > pos.highest_price:
        pos.highest_price = current_price

    # 硬止损 5%
    if pnl <= -0.05:
        return f'硬止损({pnl:.1%})'
    # 移动止盈：盈利>2%后回撤>2%
    if pnl >= 0.02:
        dd = (pos.highest_price - current_price) / pos.highest_price
        if dd >= 0.02:
            return f'移动止盈(回撤{dd:.1%})'
    # 时间止损 10天
    if days >= 10:
        return f'时间止损({days}天)'
    # 止盈 20%
    if pnl >= 0.20:
        return f'止盈({pnl:.1%})'
    # MACD死叉
    if len(prices_df) >= 30:
        c = prices_df['close'].values.astype(float)
        dif = _ema(c, 12) - _ema(c, 26)
        dea = _ema(dif, 9)
        if len(dif) >= 2 and dif[-2] > dea[-2] and dif[-1] <= dea[-1]:
            return 'MACD死叉'
    # 破位放量
    if len(prices_df) >= 6:
        c = prices_df['close'].values.astype(float)
        a = prices_df['amount'].values.astype(float)
        ma5 = c[-5:].mean()
        vm = a[-6:-1].mean()
        if c[-1] < ma5 and vm > 0 and a[-1] / vm >= 1.2:
            return '破位放量'
    # 高量阴线
    if len(prices_df) >= 6:
        a = prices_df['amount'].values.astype(float)
        p = prices_df['pct_chg'].values.astype(float)
        vm = a[-6:-1].mean()
        if vm > 0 and a[-1] / vm >= 3 and p[-1] < 0:
            return '高量阴线'
    return None

def calc_equity(positions, cash, eval_date):
    if not positions:
        return cash
    codes = list(positions.keys())
    rows = _query("""SELECT ts_code, close as price FROM daily_quotes WHERE trade_date=%s AND ts_code=ANY(%s)""", (eval_date, codes))
    prices = {r['ts_code']: float(r['price']) for r in rows if r['price']}
    eq = cash
    for code, pos in positions.items():
        eq += prices.get(code, pos.entry_price) * pos.shares
    return eq

def run():
    start = date(2026, 1, 1)
    end = date(2026, 5, 25)
    capital = 1_000_000
    max_pos = 3

    print("获取交易日...")
    rows = _query("""SELECT DISTINCT trade_date FROM daily_quotes WHERE trade_date>=%s AND trade_date<=%s ORDER BY trade_date""", (start, end))
    trading_days = [r['trade_date'] for r in rows]
    print(f"交易日: {len(trading_days)} 天")

    positions: Dict[str, Position] = {}
    all_trades = []
    daily_equity = []
    recent_pnls = []

    for i, tdate in enumerate(trading_days):
        # 1. 检查退出
        if positions:
            pcodes = list(positions.keys())
            price_rows = _query("""SELECT ts_code, close as price FROM daily_quotes WHERE trade_date=%s AND ts_code=ANY(%s)""", (tdate, pcodes))
            close_px = {r['ts_code']: float(r['price']) for r in price_rows if r['price']}

            kline_rows = _query("""SELECT ts_code, trade_date, close, amount, pct_chg FROM daily_quotes WHERE trade_date>=%s AND trade_date<=%s AND ts_code=ANY(%s) ORDER BY ts_code, trade_date""", (tdate - timedelta(days=90), tdate, pcodes))
            kline_df = pd.DataFrame(kline_rows)
            for c in ['close','amount','pct_chg']:
                kline_df[c] = pd.to_numeric(kline_df[c], errors='coerce')
            exit_kline = {code: grp.sort_values('trade_date') for code, grp in kline_df.groupby('ts_code')}

            to_close = []
            for code, pos in positions.items():
                cp = close_px.get(code)
                if cp is None or cp <= 0:
                    continue
                reason = check_exit(pos, cp, tdate, exit_kline.get(code, pd.DataFrame()))
                if reason:
                    to_close.append((code, cp, reason))

            next_idx = i + 1
            if next_idx < len(trading_days) and to_close:
                next_date = trading_days[next_idx]
                exit_codes = [c for c, _, _ in to_close]
                open_rows = _query("""SELECT ts_code, open as price FROM daily_quotes WHERE trade_date=%s AND ts_code=ANY(%s)""", (next_date, exit_codes))
                open_px = {r['ts_code']: float(r['price']) for r in open_rows if r['price']}

                for code, sig_px, reason in to_close:
                    exit_px = open_px.get(code, sig_px) * 0.999
                    pos = positions[code]
                    pnl = (exit_px - pos.entry_price) / pos.entry_price
                    days = (next_date - pos.entry_date).days
                    comm = exit_px * pos.shares * 0.001
                    all_trades.append({
                        'ts_code': code, 'entry_date': str(pos.entry_date), 'exit_date': str(next_date),
                        'entry_price': round(pos.entry_price, 2), 'exit_price': round(exit_px, 2),
                        'shares': pos.shares, 'pnl_pct': round(pnl, 4), 'holding_days': days,
                        'exit_reason': reason, 'commission': round(comm, 2),
                    })
                    capital += exit_px * pos.shares - comm
                    recent_pnls.append(pnl)
                    if len(recent_pnls) > 5:
                        recent_pnls = recent_pnls[-5:]
                    del positions[code]

        # 2. 大盘风控
        row = _query_one("""SELECT COUNT(*) FILTER (WHERE pct_chg>0) as adv, COUNT(*) as total FROM daily_quotes WHERE trade_date=%s""", (tdate,))
        adv = int(row['adv'] or 0)
        total = int(row['total'] or 1)
        if adv / total < 0.50:
            eq = calc_equity(positions, capital, tdate)
            daily_equity.append({'date': str(tdate), 'equity': eq, 'positions': len(positions)})
            continue

        # 3. 获取活跃标的
        active_rows = _query("""SELECT ts_code, pct_chg, amount, close FROM daily_quotes WHERE trade_date=%s AND amount>300000000 AND pct_chg>1.0 AND pct_chg<7 ORDER BY amount DESC LIMIT 30""", (tdate,))
        if not active_rows:
            eq = calc_equity(positions, capital, tdate)
            daily_equity.append({'date': str(tdate), 'equity': eq, 'positions': len(positions)})
            continue

        active_codes = [r['ts_code'] for r in active_rows]
        lookback = tdate - timedelta(days=90)
        kline_rows = _query("""SELECT ts_code, trade_date, open, high, low, close, volume, amount, pct_chg FROM daily_quotes WHERE trade_date>=%s AND trade_date<=%s AND ts_code=ANY(%s) ORDER BY ts_code, trade_date""", (lookback, tdate, active_codes))
        if not kline_rows:
            eq = calc_equity(positions, capital, tdate)
            daily_equity.append({'date': str(tdate), 'equity': eq, 'positions': len(positions)})
            continue

        df = pd.DataFrame(kline_rows)
        for c in ['open','high','low','close','volume','amount','pct_chg']:
            df[c] = pd.to_numeric(df[c], errors='coerce')

        # 4. 计算因子+信号
        results = []
        for ts_code, grp in df.groupby('ts_code'):
            grp = grp.sort_values('trade_date').tail(60)
            if len(grp) < 20:
                continue
            close = grp['close'].values.astype(float)
            high = grp['high'].values.astype(float)
            low = grp['low'].values.astype(float)
            amount = grp['amount'].values.astype(float)
            pct_chg = grp['pct_chg'].values.astype(float)
            n = len(close)
            if close[-1] <= 0:
                continue

            r = {'ts_code': ts_code, 'close': round(float(close[-1]), 2)}

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
            tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
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

            # 因子总分
            r['factor_score'] = round(
                r['momentum_score'] * 0.25 + r['volume_score'] * 0.20 +
                r['rsi_score'] * 0.15 + r['macd_score'] * 0.15 +
                r['ema_score'] * 0.10 + r['adx_score'] * 0.10 +
                r['sar_score'] * 0.05, 4)

            # 启动信号
            launch = 0
            if n >= 20:
                vol_ma20 = amount[-21:-1].mean()
                if vol_ma20 > 0 and amount[-1] / vol_ma20 >= 2.0:
                    launch += 0.4
                high_20 = close[-21:-1].max()
                if close[-1] > high_20 * 1.03:
                    launch += 0.3
                if len(dif) >= 2 and dif[-2] <= dea[-2] and dif[-1] > dea[-1]:
                    launch += 0.3
            r['launch_score'] = round(min(launch, 1.0), 3)

            # 隔夜评分
            ov = 0
            last_pct = float(pct_chg[-1]) if len(pct_chg) > 0 else 0
            if 2.0 <= last_pct <= 7.0:
                ov += 30
            elif last_pct > 0:
                ov += 15
            if n >= 20:
                vol_mean20 = amount[-21:-1].mean()
                if vol_mean20 > 0:
                    vr5 = amount[-1] / vol_mean20
                    if 1.5 <= vr5 <= 10:
                        ov += 25
                    elif vr5 > 1:
                        ov += 10
            if n >= 5:
                ma5 = close[-5:].mean()
                dist = (close[-1] - ma5) / (ma5 + 1e-9)
                if 0 <= dist <= 0.03:
                    ov += 20
            r['overnight_score'] = min(ov, 100)

            r['factor_score_100'] = round(r['factor_score'] * 100, 2)
            r['launch_score_100'] = round(r['launch_score'] * 100, 2)
            r['meta_score'] = round(r['factor_score_100'] * 0.45 + r['launch_score_100'] * 0.25 + r['overnight_score'] * 0.30, 2)

            # 多层门槛（收紧）
            ma20 = float(close[-20:].mean())
            ma20_ok = float(close[-1]) > ma20 * 1.01
            high20 = float(close[-21:-1].max())
            breakout = float(close[-1]) > high20 * 0.97
            mom5 = float((close[-1] - close[-6]) / (close[-6] + 1e-9)) if n >= 6 else 0
            mom20_val = float((close[-1] - close[-21]) / (close[-21] + 1e-9)) if n >= 21 else 0
            multi_mom = mom5 > 0.01 and mom20_val > 0.05
            vol20_mean = float(amount[-21:-1].mean()) if n >= 21 else float(amount.mean())
            vol_boom = float(amount[-1]) > vol20_mean * 1.5
            rsi_ok = 40 <= rsi <= 70
            score_ok = (r['factor_score'] >= 0.45 and r['launch_score'] >= 0.4 and r['overnight_score'] >= 40)
            signal_ok = breakout or vol_boom
            if score_ok and ma20_ok and multi_mom and rsi_ok and signal_ok:
                results.append(r)

        if not results:
            eq = calc_equity(positions, capital, tdate)
            daily_equity.append({'date': str(tdate), 'equity': eq, 'positions': len(positions)})
            continue

        # 排序取前10
        results.sort(key=lambda x: x['meta_score'], reverse=True)
        results = results[:10]

        # 5. 开仓（T+1开盘）
        next_idx = i + 1
        if next_idx >= len(trading_days):
            eq = calc_equity(positions, capital, tdate)
            daily_equity.append({'date': str(tdate), 'equity': eq, 'positions': len(positions)})
            continue

        next_date = trading_days[next_idx]
        candidate_codes = [r['ts_code'] for r in results if r['ts_code'] not in positions]
        if candidate_codes:
            open_rows = _query("""SELECT ts_code, open as price FROM daily_quotes WHERE trade_date=%s AND ts_code=ANY(%s)""", (next_date, candidate_codes))
            open_px = {r['ts_code']: float(r['price']) for r in open_rows if r['price']}

            for r in results:
                if len(positions) >= max_pos:
                    break
                if r['ts_code'] in positions:
                    continue
                entry_px = open_px.get(r['ts_code'])
                if entry_px is None or entry_px <= 0:
                    continue

                current_eq = capital + sum(open_px.get(p.ts_code, p.entry_price) * p.shares for p in positions.values())

                # 反马丁格尔仓位
                base_pct = 0.30
                if len(recent_pnls) >= 3:
                    avg = np.mean(recent_pnls)
                    if avg > 0.02:
                        pos_pct = min(base_pct * 1.3, 0.40)
                    elif avg < -0.02:
                        pos_pct = max(base_pct * 0.7, 0.15)
                    else:
                        pos_pct = base_pct
                else:
                    pos_pct = base_pct

                pos_val = current_eq * pos_pct
                shares = int(pos_val / (entry_px * 100)) * 100
                if shares <= 0:
                    shares = 100
                entry_adj = entry_px * 1.001
                comm = entry_adj * shares * 0.001
                cost = entry_adj * shares + comm
                if cost > capital:
                    continue
                capital -= cost
                positions[r['ts_code']] = Position(
                    ts_code=r['ts_code'], entry_date=next_date,
                    entry_price=entry_adj, shares=shares,
                )

        eq = calc_equity(positions, capital, tdate)
        daily_equity.append({'date': str(tdate), 'equity': eq, 'positions': len(positions)})

    # 强制平仓
    for code in list(positions.keys()):
        pos = positions[code]
        close_rows = _query("""SELECT close as price FROM daily_quotes WHERE trade_date=%s AND ts_code=%s""", (end, code))
        last_px = float(close_rows[0]['price']) if close_rows else pos.entry_price
        pnl = (last_px - pos.entry_price) / pos.entry_price
        days = (end - pos.entry_date).days
        all_trades.append({
            'ts_code': code, 'entry_date': str(pos.entry_date), 'exit_date': str(end),
            'entry_price': round(pos.entry_price, 2), 'exit_price': round(last_px, 2),
            'shares': pos.shares, 'pnl_pct': round(pnl, 4), 'holding_days': days,
            'exit_reason': '回测结束', 'commission': 0,
        })
    positions.clear()

    # 输出结果
    print("=" * 80)
    print("  优化回测汇总 (2026-01-01 ~ 2026-05-25)")
    print("=" * 80)
    print(f"  初始资金: {1_000_000:,.0f}")

    if all_trades:
        pnls = [t['pnl_pct'] for t in all_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        print(f"\n--- 交易统计 ---")
        print(f"  总交易数: {len(all_trades)}")
        print(f"  胜率: {len(wins)/len(pnls):.1%}")
        print(f"  平均收益: {np.mean(pnls):.2%}")
        print(f"  中位数收益: {np.median(pnls):.2%}")
        print(f"  最大单笔盈利: {max(pnls):.2%}")
        print(f"  最大单笔亏损: {min(pnls):.2%}")
        if wins and losses:
            print(f"  盈亏比: {abs(np.mean(wins)/np.mean(losses)):.2f}")
        print(f"  平均持仓天数: {np.mean([t['holding_days'] for t in all_trades]):.1f}")

        # 退出原因
        reasons = {}
        for t in all_trades:
            r = t['exit_reason'].split('(')[0]
            reasons[r] = reasons.get(r, 0) + 1
        print(f"\n--- 退出原因 ---")
        for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"  {r}: {c} ({c/len(all_trades):.1%})")

        # 权益
        eq_df = pd.DataFrame(daily_equity)
        final = eq_df['equity'].iloc[-1]
        total_ret = (final - 1_000_000) / 1_000_000
        cummax = eq_df['equity'].cummax()
        max_dd = ((eq_df['equity'] - cummax) / cummax).min()

        print(f"\n--- 权益 ---")
        print(f"  最终权益: {final:,.0f}")
        print(f"  总收益率: {total_ret:.2%}")
        print(f"  最大回撤: {max_dd:.2%}")

        print(f"\n--- 逐笔交易 ---")
        for t in all_trades:
            print(f"  {t['ts_code']} | 买入: {t['entry_date']} @ {t['entry_price']:.2f} | 卖出: {t['exit_date']} @ {t['exit_price']:.2f} | 收益: {t['pnl_pct']:+.2%} | {t['exit_reason']}")
    else:
        print("  无交易记录")

    close_db_session()

if __name__ == "__main__":
    import io
    # Capture all output to a string
    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured
    
    try:
        run()
    finally:
        sys.stdout = old_stdout
        output = captured.getvalue()
        # Write to file using the Write tool approach
        with open(r'D:\pythonProject\openclaw-quant-system\backtest_results.txt', 'w', encoding='utf-8') as f:
            f.write(output)
        # Also print
        print(output)
