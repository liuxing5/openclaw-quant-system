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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import List, Dict, Optional

import pandas as pd
import numpy as np
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from core.db.connection import get_db

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    tqdm = None

LAYER4_WORKERS = min(8, (os.cpu_count() or 4))


def _calc_ema(values: pd.Series, period: int) -> pd.Series:
    return values.ewm(span=period, adjust=False).mean()


def _batch_load_history(
    stock_list: List[str], trade_date: date, db_conn, days: int = 60
) -> Dict[str, pd.DataFrame]:
    """批量加载所有股票的OHLCV历史数据（1次SQL查询，替代N次单股查询）"""
    if not stock_list:
        return {}

    start_date = trade_date - timedelta(days=days)
    result = {}

    cur = db_conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT ts_code, trade_date, open, high, low, close, volume, amount,
               pct_chg, turnover_rate, amplitude, volume_ratio
        FROM daily_quotes
        WHERE ts_code = ANY(%s) AND trade_date >= %s AND trade_date <= %s
        ORDER BY ts_code, trade_date ASC;
    """, (stock_list, start_date, trade_date))
    rows = cur.fetchall()
    cur.close()

    if not rows:
        return result

    df_all = pd.DataFrame(rows)
    df_all['trade_date'] = pd.to_datetime(df_all['trade_date']).dt.date

    for ts_code, group in df_all.groupby('ts_code'):
        group = group.set_index('trade_date').sort_index()
        for col in ['open', 'high', 'low', 'close', 'volume', 'amount',
                    'pct_chg', 'turnover_rate', 'amplitude', 'volume_ratio']:
            group[col] = pd.to_numeric(group[col], errors='coerce')
        result[ts_code] = group

    return result


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
    """需求吸收K线：EMA12附近的锤子/刺透 + 放量"""
    if len(df) < 3:
        return False

    close = df['close']
    ema12 = _calc_ema(close, 12)
    if ema12.iloc[-1] <= 0:
        return False

    today = df.iloc[-1]
    yesterday = df.iloc[-2]

    ema12_val = ema12.iloc[-1]
    close_to_ema = abs(today['close'] - ema12_val) / ema12_val
    if close_to_ema > 0.03:
        return False

    is_pattern = _is_hammer(today) or _is_piercing(today, yesterday)
    if not is_pattern:
        return False

    avg_vol_5 = df['volume'].iloc[-6:-1].mean() if len(df) >= 6 else df['volume'].iloc[:-1].mean()
    if avg_vol_5 > 0 and today['volume'] > avg_vol_5 * 1.2:
        return True

    return False


def _check_strong_relay(df: pd.DataFrame, limit_pct: float, cfg) -> bool:
    """强势接力：昨日首板，今日回踩VWAP翘头"""
    if len(df) < 5:
        return False

    today = df.iloc[-1]
    yesterday = df.iloc[-2]

    yest_pct = yesterday.get('pct_chg', 0) or 0
    prev_pct_2 = df.iloc[-3].get('pct_chg', 0) if len(df) >= 3 else 0

    is_first_board = (yest_pct >= limit_pct * 0.95) and (prev_pct_2 < limit_pct * 0.8)
    if not is_first_board:
        return False

    typical_today = (today['high'] + today['low'] + today['close']) / 3
    approx_vwap = typical_today
    if today['close'] < approx_vwap * (1 - cfg.layer4_vwap_tolerance):
        return False

    if today['close'] <= today['open']:
        return False

    return True


def _check_boll_blowout(df: pd.DataFrame, cfg) -> bool:
    """天量上轨禁止信号：成交量>均量N倍 + 突破上轨，禁止买入"""
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

    is_breakout = today_close > boll_upper_now
    is_blowout_vol = today_vol > avg_vol_20 * cfg.layer4_boll_blowout_vol_mult

    return is_breakout and is_blowout_vol


def _get_limit_pct(ts_code: str) -> float:
    """根据板块判断涨停幅度"""
    code_part = ts_code.split('.')[0]
    if code_part.startswith(('688', '300', '301')):
        return 19.8
    elif code_part.startswith(('8', '4')):
        return 29.8
    else:
        return 9.8


def _check_single(
    ts_code: str, cfg, ohlcv_cache: Dict[str, pd.DataFrame]
) -> dict:
    """单股检查（从已加载的内存缓存读取，无DB访问）"""
    result = {
        'passed': False,
        'signal_type': 'none',
        'score_bonus': 0.0,
        'details': {},
        'reject_reason': '',
    }

    df = ohlcv_cache.get(ts_code)
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
    limit_pct = _get_limit_pct(ts_code)
    signal_found = False

    if cfg.layer4_enable_demand_absorption and _check_demand_absorption(df, cfg):
        result['signal_type'] = 'demand_absorption'
        result['score_bonus'] += 5.0
        signal_found = True

    if cfg.layer4_enable_strong_relay and _check_strong_relay(df, limit_pct, cfg):
        if signal_found:
            result['score_bonus'] += 3.0
        else:
            result['signal_type'] = 'strong_relay'
            result['score_bonus'] += 8.0
        signal_found = True

    if not signal_found:
        result['reject_reason'] = '无买入信号(K线形态不符)'
        return result

    result['passed'] = True
    return result


def check_momentum_entry(
    ts_code: str,
    trade_date: date,
    limit_pct: float,
    cfg,
    db_conn,
    verbose: bool = False,
) -> dict:
    """单股动能与买入信号检查（兼容接口，内部用batch load）"""
    cache = _batch_load_history([ts_code], trade_date, db_conn, days=60)
    return _check_single(ts_code, cfg, cache)


def run_layer4_momentum_filter(
    stock_list: List[str],
    trade_date: date = None,
    cfg=None,
    verbose: bool = True,
) -> List[dict]:
    """
    动能与买入信号过滤，返回通过过滤的股票详情。

    优化: 批量加载OHLCV(1次SQL) + ThreadPoolExecutor并行分析，
          1975只从 ~8分钟 降至 ~30秒。
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

    n_total = len(stock_list)

    if verbose:
        print(f"\n{'─'*60}")
        print(f"  [Layer 4] 动能与买入信号  — 待筛选 {n_total} 只")
        print(f"{'─'*60}")
        print(f"  量比{cfg.layer4_volume_ratio_min}~{cfg.layer4_volume_ratio_max}  "
              f"乖离<{cfg.layer4_max_bias_pct}%  "
              f"信号: 需求吸收{'✅' if cfg.layer4_enable_demand_absorption else '⏭️'}"
              f" 强势接力{'✅' if cfg.layer4_enable_strong_relay else '⏭️'}")

    # ── 阶段1: 批量加载 OHLCV（1 次 SQL 替代 N 次单股查询）──
    db_conn = get_db()
    try:
        if verbose:
            print(f"  ⏳ 批量加载K线数据 ({n_total} 只)...")
        ohlcv_cache = _batch_load_history(stock_list, trade_date, db_conn, days=60)
    finally:
        db_conn.close()

    if verbose:
        loaded = len(ohlcv_cache)
        print(f"  ✓ 数据加载完成: {loaded}/{n_total} 只有效K线")

    # ── 阶段2: 并行分析 ──
    passed = []
    reject_stats = {'量比不符': 0, '乖离超标': 0, '天量上轨': 0, '无买入信号': 0, '数据不足': 0}

    if len(stock_list) > 100:
        _run_parallel(stock_list, cfg, ohlcv_cache, passed, reject_stats, verbose)
    else:
        _run_serial(stock_list, cfg, ohlcv_cache, passed, reject_stats, verbose)

    if verbose:
        print(f"  ✓ 通过: {len(passed)} 只")
        print(f"  ✗ 淘汰: {n_total - len(passed)} 只")
        for reason, count in reject_stats.items():
            if count > 0:
                print(f"    {reason}: {count} 只")

    return passed


def _run_parallel(stock_list, cfg, ohlcv_cache, passed, reject_stats, verbose):
    """多线程并行分析（>100只时启用）"""
    n_total = len(stock_list)
    passed_codes = set()

    with ThreadPoolExecutor(max_workers=LAYER4_WORKERS) as executor:
        futures = {
            executor.submit(_check_single, ts_code, cfg, ohlcv_cache): ts_code
            for ts_code in stock_list
        }

        completed = 0
        for future in as_completed(futures):
            completed += 1
            if verbose and not HAS_TQDM and completed % 100 == 0:
                print(f"  进度: {completed}/{n_total}")

            ts_code = futures[future]
            check = future.result()
            if check['passed']:
                passed_codes.add(ts_code)
                passed.append({
                    'ts_code': ts_code,
                    'score_bonus': check['score_bonus'],
                    'signal_type': check['signal_type'],
                    'details': check['details'],
                })
            else:
                _count_reject(check['reject_reason'], reject_stats)

    # 保持原始输入顺序
    if passed_codes:
        ordered = [item for item in passed if item['ts_code'] in passed_codes]
        passed.clear()
        passed.extend(sorted(ordered, key=lambda x: stock_list.index(x['ts_code'])))


def _run_serial(stock_list, cfg, ohlcv_cache, passed, reject_stats, verbose):
    """串行分析（≤100只时使用，避免线程开销）"""
    n_total = len(stock_list)
    for i, ts_code in enumerate(stock_list):
        if verbose and (i + 1) % 50 == 0:
            print(f"  进度: {i+1}/{n_total}")

        check = _check_single(ts_code, cfg, ohlcv_cache)
        if check['passed']:
            passed.append({
                'ts_code': ts_code,
                'score_bonus': check['score_bonus'],
                'signal_type': check['signal_type'],
                'details': check['details'],
            })
        else:
            _count_reject(check['reject_reason'], reject_stats)


def _count_reject(reason: str, stats: dict):
    """统计淘汰原因"""
    if '量比' in reason:
        stats['量比不符'] += 1
    elif '乖离' in reason:
        stats['乖离超标'] += 1
    elif '天量' in reason:
        stats['天量上轨'] += 1
    elif '不足' in reason:
        stats['数据不足'] += 1
    else:
        stats['无买入信号'] += 1
