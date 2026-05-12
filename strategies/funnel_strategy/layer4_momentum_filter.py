"""
Layer 4: 动能与买入信号
========================
决策逻辑：
  出现【需求吸收K线（EMA12附近锤子/刺透+放量）】或
  【强势接力（昨日首板，今日回踩VWAP翘头）】；
  同时量比1.5~3；乖离率<6%；未出现天量上轨禁止信号。

吸收策略：⑤价格行为/VWAP/一进二改良/红三兵限制/布林上轨反用/箱体反转K线
"""
from __future__ import annotations

import sys
import os
from datetime import date, timedelta
from typing import List, Optional

import pandas as pd
import numpy as np
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from core.db.connection import get_db


def _calc_ema(values: pd.Series, period: int) -> pd.Series:
    return values.ewm(span=period, adjust=False).mean()


def _load_history(ts_code: str, trade_date: date, days: int = 120) -> Optional[pd.DataFrame]:
    start_date = trade_date - timedelta(days=days)
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT trade_date, open, high, low, close, volume, amount,
                   pct_chg, turnover_rate, amplitude, volume_ratio
            FROM daily_quotes
            WHERE ts_code = %s AND trade_date >= %s AND trade_date <= %s
            ORDER BY trade_date ASC;
        """, (ts_code, start_date, trade_date))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            return None

        df = pd.DataFrame(rows)
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
        df.set_index('trade_date', inplace=True)
        for col in ['open', 'high', 'low', 'close', 'volume', 'amount',
                      'pct_chg', 'turnover_rate', 'amplitude', 'volume_ratio']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        return df
    except Exception:
        return None


def _is_hammer(row) -> bool:
    """判断是否为锤子线：下影线≥实体2倍，实体较小"""
    body = abs(row['close'] - row['open'])
    lower_shadow = min(row['open'], row['close']) - row['low']
    if body == 0:
        return lower_shadow > (row['high'] - row['low']) * 0.6
    return (lower_shadow >= body * 2) and (body < (row['high'] - row['low']) * 0.4)


def _is_piercing(row, prev_row) -> bool:
    """判断是否为刺透形态：昨阴今阳，今收>昨实体中点"""
    if prev_row is None:
        return False
    prev_body = prev_row['open'] - prev_row['close']
    if prev_body <= 0:
        return False
    if row['close'] <= row['open']:
        return False
    midpoint = prev_row['close'] + prev_body * 0.5
    return row['close'] > midpoint and row['open'] < prev_row['close']


def _check_demand_absorption(df: pd.DataFrame, cfg) -> bool:
    """
    需求吸收K线：EMA12附近的锤子/刺透 + 放量
    """
    if len(df) < 3:
        return False

    close = df['close']
    ema12 = _calc_ema(close, 12)
    if ema12.iloc[-1] <= 0:
        return False

    today = df.iloc[-1]
    yesterday = df.iloc[-2]

    # 收盘价在EMA12附近（±3%）
    ema12_val = ema12.iloc[-1]
    close_to_ema = abs(today['close'] - ema12_val) / ema12_val
    if close_to_ema > 0.03:
        return False

    # 锤子或刺透
    is_pattern = _is_hammer(today) or _is_piercing(today, yesterday)
    if not is_pattern:
        return False

    # 放量确认：成交量>近5日均量的1.2倍
    avg_vol_5 = df['volume'].iloc[-6:-1].mean() if len(df) >= 6 else df['volume'].iloc[:-1].mean()
    if avg_vol_5 > 0 and today['volume'] > avg_vol_5 * 1.2:
        return True

    return False


def _check_strong_relay(df: pd.DataFrame, limit_pct: float, cfg) -> bool:
    """
    强势接力：昨日首板，今日回踩VWAP翘头
    VWAP用 (high+low+close)/3 × volume 的累计来近似
    """
    if len(df) < 5:
        return False

    today = df.iloc[-1]
    yesterday = df.iloc[-2]

    # 昨日是否首板（涨停，且前日未涨停）
    yest_pct = yesterday.get('pct_chg', 0) or 0
    prev_close_2 = df.iloc[-3]['close'] if len(df) >= 3 else None
    prev_pct_2 = df.iloc[-3].get('pct_chg', 0) if len(df) >= 3 else 0

    is_first_board = (yest_pct >= limit_pct * 0.95) and (prev_pct_2 < limit_pct * 0.8)
    if not is_first_board:
        return False

    # 今日回踩VWAP翘头（用近似VWAP）
    # VWAP ≈ sum(typical_price * volume) / sum(volume)
    typical_today = (today['high'] + today['low'] + today['close']) / 3
    # 简化：收盘价 > 今日VWAP（盘中翘头）
    approx_vwap = typical_today  # 单日简化
    if today['close'] < approx_vwap * (1 - cfg.layer4_vwap_tolerance):
        return False

    # 今日收阳
    if today['close'] <= today['open']:
        return False

    return True


def _check_boll_blowout(df: pd.DataFrame, cfg) -> bool:
    """
    天量上轨禁止信号：成交量>均量N倍 + 突破上轨，禁止买入
    """
    if len(df) < 20:
        return False

    close = df['close']
    boll_mid = close.rolling(window=20, min_periods=20).mean()
    boll_std = close.rolling(window=20, min_periods=20).std()
    boll_upper = boll_mid + boll_std * 2

    today_close = close.iloc[-1]
    today_vol = df['volume'].iloc[-1]
    avg_vol_20 = df['volume'].iloc[-21:-1].mean()

    boll_upper_now = boll_upper.iloc[-1]
    if pd.isna(boll_upper_now):
        return False

    # 突破上轨且量能N倍 → 天量上轨
    is_breakout = today_close > boll_upper_now
    is_blowout_vol = today_vol > avg_vol_20 * cfg.layer4_boll_blowout_vol_mult

    return is_breakout and is_blowout_vol


def check_momentum_entry(
    ts_code: str,
    trade_date: date,
    limit_pct: float,
    cfg,
    verbose: bool = False,
) -> dict:
    """
    单股动能与买入信号检查。
    
    返回:
      {
        'passed': bool,
        'signal_type': str,        # 'demand_absorption' / 'strong_relay' / 'none'
        'score_bonus': float,
        'details': dict,
        'reject_reason': str,
      }
    """
    result = {
        'passed': False,
        'signal_type': 'none',
        'score_bonus': 0.0,
        'details': {},
        'reject_reason': '',
    }

    df = _load_history(ts_code, trade_date, days=120)
    if df is None or len(df) < 5:
        result['reject_reason'] = '数据不足'
        return result

    today = df.iloc[-1]
    close = today['close']

    # 1. 量比验证
    vol_ratio = today.get('volume_ratio', 0) or 0
    if vol_ratio <= 0 and len(df) >= 21:
        avg_vol_20 = df['volume'].iloc[-21:-1].mean()
        vol_ratio = today['volume'] / avg_vol_20 if avg_vol_20 > 0 else 0

    result['details']['vol_ratio'] = round(vol_ratio, 2)

    if vol_ratio < cfg.layer4_volume_ratio_min:
        result['reject_reason'] = f'量比={vol_ratio:.1f}<{cfg.layer4_volume_ratio_min}'
        return result
    if vol_ratio > cfg.layer4_volume_ratio_max:
        result['reject_reason'] = f'量比={vol_ratio:.1f}>{cfg.layer4_volume_ratio_max}'
        return result

    # 2. 乖离率检查（以EMA12为基准）
    ema12 = _calc_ema(df['close'], 12)
    ema12_now = ema12.iloc[-1]
    bias_pct = (close - ema12_now) / ema12_now * 100 if ema12_now > 0 else 0
    result['details']['bias_pct'] = round(bias_pct, 2)

    if abs(bias_pct) > cfg.layer4_max_bias_pct:
        result['reject_reason'] = f'乖离率={bias_pct:.1f}%>{cfg.layer4_max_bias_pct}%'
        return result

    # 3. 天量上轨禁止信号
    if cfg.layer4_require_no_upper_boll_blowout and _check_boll_blowout(df, cfg):
        result['reject_reason'] = '天量上轨禁止'
        return result

    # 4. K线形态信号识别
    signal_found = False

    if cfg.layer4_enable_demand_absorption and _check_demand_absorption(df, cfg):
        result['signal_type'] = 'demand_absorption'
        result['score_bonus'] += 5.0
        signal_found = True

    if cfg.layer4_enable_strong_relay and _check_strong_relay(df, limit_pct, cfg):
        if signal_found:
            result['score_bonus'] += 3.0  # 双信号叠加
        else:
            result['signal_type'] = 'strong_relay'
            result['score_bonus'] += 8.0
        signal_found = True

    if not signal_found:
        result['reject_reason'] = '无买入信号(K线形态不符)'
        return result

    result['passed'] = True
    return result


def run_layer4_momentum_filter(
    stock_list: List[str],
    trade_date: date = None,
    cfg=None,
    verbose: bool = True,
) -> List[dict]:
    """
    动能与买入信号过滤，返回通过过滤的股票详情。
    """
    if cfg is None:
        from .funnel_config import DEFAULT_FUNNEL_CONFIG
        cfg = DEFAULT_FUNNEL_CONFIG

    if not cfg.layer4_enabled:
        return [{'ts_code': c} for c in stock_list]

    if trade_date is None:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT MAX(trade_date) as max_date FROM daily_quotes;")
        row = cur.fetchone()
        trade_date = row['max_date'] if row else date.today()
        cur.close()
        conn.close()

    if verbose:
        print(f"\n{'─'*60}")
        print(f"  [Layer 4] 动能与买入信号  — 待筛选 {len(stock_list)} 只")
        print(f"{'─'*60}")
        print(f"  量比{cfg.layer4_volume_ratio_min}~{cfg.layer4_volume_ratio_max}  "
              f"乖离<{cfg.layer4_max_bias_pct}%  "
              f"信号: 需求吸收{'✅' if cfg.layer4_enable_demand_absorption else '⏭️'}"
              f" 强势接力{'✅' if cfg.layer4_enable_strong_relay else '⏭️'}")

    passed = []
    reject_stats = {'量比不符': 0, '乖离超标': 0, '天量上轨': 0, '无买入信号': 0}

    for i, ts_code in enumerate(stock_list):
        if verbose and (i + 1) % 50 == 0:
            print(f"  进度: {i+1}/{len(stock_list)}")

        code_part = ts_code.split('.')[0]
        if code_part.startswith(('688', '300', '301')):
            limit_pct = 19.8
        elif code_part.startswith(('8', '4')):
            limit_pct = 29.8
        else:
            limit_pct = 9.8

        check = check_momentum_entry(ts_code, trade_date, limit_pct, cfg, verbose=False)

        if check['passed']:
            item = {
                'ts_code': ts_code,
                'score_bonus': check['score_bonus'],
                'signal_type': check['signal_type'],
                'details': check['details'],
            }
            passed.append(item)
        else:
            reason = check['reject_reason']
            if '量比' in reason:
                reject_stats['量比不符'] += 1
            elif '乖离' in reason:
                reject_stats['乖离超标'] += 1
            elif '天量' in reason:
                reject_stats['天量上轨'] += 1
            elif '信号' in reason:
                reject_stats['无买入信号'] += 1

    if verbose:
        print(f"  ✓ 通过: {len(passed)} 只")
        print(f"  ✗ 淘汰: {len(stock_list) - len(passed)} 只")
        for reason, count in reject_stats.items():
            if count > 0:
                print(f"    {reason}: {count} 只")

    return passed
