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
from datetime import date, datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple

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

BEIJING_TZ = timezone(timedelta(hours=8))
LAYER4_WORKERS = min(8, (os.cpu_count() or 4))

# ── 纯 Python 计算（避开 pandas 开销，适合小数据集） ──

def _fast_ema_last(values: np.ndarray, period: int) -> float:
    """快速计算 EMA 最后一个值（纯 Python，5点数据比 pandas.ewm 快10x）"""
    n = len(values)
    if n == 0:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    ema = float(values[0])
    for i in range(1, n):
        ema = alpha * float(values[i]) + (1.0 - alpha) * ema
    return ema


def _fast_boll_upper(values: np.ndarray, period: int = 20) -> float:
    """快速计算布林上轨最后一个值"""
    n = len(values)
    if n < period:
        return float('nan')
    window = values[-period:]
    mid = window.mean()
    std = window.std(ddof=0)
    return mid + std * 2.0


# ── 批量加载 + 预计算指标 ──

def _batch_load_and_precompute(
    stock_list: List[str], trade_date: date, db_conn, cfg, days: int = 30
) -> Tuple[Dict, Dict]:
    """
    批量加载 OHLCV + 预计算所有股票的技术指标。

    返回:
      ohlcv_cache: {ts_code: arr_dict}  — 仅保留最后5行用于K线形态检测
      precomputed:  {ts_code: {ema12, bias_pct, vol_ratio, boll_upper, ...}}
    """
    if not stock_list:
        return {}, {}

    start_date = trade_date - timedelta(days=days)

    cur = db_conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT ts_code, trade_date, open, high, low, close, volume,
               pct_chg, amplitude, volume_ratio
        FROM daily_quotes
        WHERE ts_code = ANY(%s) AND trade_date >= %s AND trade_date <= %s
        ORDER BY ts_code, trade_date ASC;
    """, (stock_list, start_date, trade_date))
    rows = cur.fetchall()
    cur.close()

    if not rows:
        return {}, {}

    # 一次性转换所有数字列
    df_all = pd.DataFrame(rows)
    for col in ['open', 'high', 'low', 'close', 'volume',
                'pct_chg', 'amplitude', 'volume_ratio']:
        df_all[col] = pd.to_numeric(df_all[col], errors='coerce')

    ohlcv_cache = {}
    precomputed = {}

    for ts_code, group in df_all.groupby('ts_code', sort=False):
        if len(group) < 3:
            continue

        # 转为 numpy 数组（纯 Python 计算，避免 pandas 开销）
        close_arr = group['close'].values
        volume_arr = group['volume'].values
        n = len(close_arr)

        today_close = float(close_arr[-1])
        today_open = float(group['open'].iloc[-1])
        today_high = float(group['high'].iloc[-1])
        today_low = float(group['low'].iloc[-1])
        today_vol = float(volume_arr[-1])
        today_pct = float(group['pct_chg'].iloc[-1] or 0)
        today_amplitude = float(group['amplitude'].iloc[-1] or 0)
        today_vol_ratio = float(group['volume_ratio'].iloc[-1] or 0)

        # EMA12
        ema12 = _fast_ema_last(close_arr, 12)
        bias_pct = (today_close - ema12) / ema12 * 100.0 if ema12 > 0 else 999.0

        # 量比（后备计算）
        vol_ratio = today_vol_ratio
        if vol_ratio <= 0 and n >= 5:
            avg_vol = volume_arr[-6:-1].mean() if n >= 6 else volume_arr[:-1].mean()
            vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0.0

        # 布林上轨 + 20日均量（仅数据充足时计算）
        boll_upper = float('nan')
        avg_vol_20 = 0.0
        if n >= 20:
            boll_upper = _fast_boll_upper(close_arr, 20)
            avg_vol_20 = float(volume_arr[-21:-1].mean()) if n >= 21 else float(volume_arr[:-1].mean())

        precomputed[ts_code] = {
            'ema12': ema12,
            'bias_pct': bias_pct,
            'vol_ratio': vol_ratio,
            'boll_upper': boll_upper,
            'avg_vol_20': avg_vol_20,
            'close': today_close,
            'volume': today_vol,
        }

        # 保留最后 5 行用于 K 线形态检测（转成 list of dict 方便并行处理）
        tail_rows = []
        start_idx = max(0, n - 5)
        for i in range(start_idx, n):
            tail_rows.append({
                'open': float(group['open'].iloc[i]),
                'high': float(group['high'].iloc[i]),
                'low': float(group['low'].iloc[i]),
                'close': float(group['close'].iloc[i]),
                'volume': float(group['volume'].iloc[i]),
                'pct_chg': float(group['pct_chg'].iloc[i] or 0),
            })
        ohlcv_cache[ts_code] = tail_rows

    return ohlcv_cache, precomputed


# ── K 线形态检测（纯 Python，无 pandas 依赖） ──

def _is_hammer(row: dict) -> bool:
    body = abs(row['close'] - row['open'])
    upper_shadow = row['high'] - max(row['open'], row['close'])
    lower_shadow = min(row['open'], row['close']) - row['low']
    if body == 0:
        return (lower_shadow > (row['high'] - row['low']) * 0.6 and
                upper_shadow < lower_shadow * 0.5)
    return (lower_shadow >= body * 2 and
            body < (row['high'] - row['low']) * 0.4 and
            upper_shadow < lower_shadow * 0.5)


def _is_piercing(today: dict, yesterday: dict) -> bool:
    prev_body = yesterday['open'] - yesterday['close']
    if prev_body <= 0:
        return False
    if today['close'] <= today['open']:
        return False
    midpoint = yesterday['close'] + prev_body * 0.5
    return today['close'] > midpoint and today['open'] < yesterday['low']


def _get_limit_pct(ts_code: str) -> float:
    code_part = ts_code.split('.')[0]
    if code_part.startswith(('688', '300', '301')):
        return 19.8
    elif code_part.startswith(('8', '4')):
        return 29.8
    else:
        return 9.8


def _check_single(
    ts_code: str, cfg, ohlcv_cache: Dict, precomputed: Dict
) -> dict:
    """单股检查（纯 Python，无 pandas，适合多线程并行）"""
    result = {
        'passed': False, 'signal_type': 'none',
        'score_bonus': 0.0, 'details': {}, 'reject_reason': '',
    }

    rows = ohlcv_cache.get(ts_code)
    pre = precomputed.get(ts_code)
    if not rows or len(rows) < 3 or pre is None:
        result['reject_reason'] = '数据不足'
        return result

    today = rows[-1]

    # 1. 量比验证（从预计算读取）
    vol_ratio = pre['vol_ratio']
    result['details']['vol_ratio'] = round(vol_ratio, 2)

    if vol_ratio < cfg.layer4_volume_ratio_min:
        result['reject_reason'] = f'量比={vol_ratio:.1f}<{cfg.layer4_volume_ratio_min}'
        return result
    if vol_ratio > cfg.layer4_volume_ratio_max:
        result['reject_reason'] = f'量比={vol_ratio:.1f}>{cfg.layer4_volume_ratio_max}'
        return result

    # 2. 乖离率检查（从预计算读取）
    bias_pct = pre['bias_pct']
    result['details']['bias_pct'] = round(bias_pct, 2)

    if abs(bias_pct) > cfg.layer4_max_bias_pct:
        result['reject_reason'] = f'乖离率={bias_pct:.1f}%>{cfg.layer4_max_bias_pct}%'
        return result

    # 3. 天量上轨禁止（从预计算读取 avg_vol_20，避免 ohlcv_cache 截断导致死代码）
    if cfg.layer4_require_no_upper_boll_blowout:
        boll_upper = pre['boll_upper']
        avg_vol_20 = pre.get('avg_vol_20', 0.0)
        if not np.isnan(boll_upper) and avg_vol_20 > 0:
            close = pre['close']
            vol = pre['volume']
            if close > boll_upper and vol > avg_vol_20 * cfg.layer4_boll_blowout_vol_mult:
                result['reject_reason'] = '天量上轨禁止'
                return result

    # 4. K线形态信号识别
    limit_pct = _get_limit_pct(ts_code)
    signal_found = False

    if cfg.layer4_enable_demand_absorption:
        # EMA12 附近的锤子/刺透 + 放量
        ema12 = pre['ema12']
        close_to_ema = abs(today['close'] - ema12) / ema12 if ema12 > 0 else 1.0
        if close_to_ema <= 0.03:
            yesterday = rows[-2]
            is_pattern = _is_hammer(today) or _is_piercing(today, yesterday)
            if is_pattern:
                vols = [r['volume'] for r in rows]
                avg_vol_5 = sum(vols[-6:-1]) / 5.0 if len(vols) >= 6 else sum(vols[:-1]) / (len(vols) - 1)
                if avg_vol_5 > 0 and today['volume'] > avg_vol_5 * 1.2:
                    result['signal_type'] = 'demand_absorption'
                    result['score_bonus'] += 5.0
                    signal_found = True

    if cfg.layer4_enable_strong_relay:
        yesterday = rows[-2]
        yest_pct = yesterday.get('pct_chg', 0)
        prev_pct_2 = rows[-3].get('pct_chg', 0) if len(rows) >= 3 else 0
        is_first_board = (yest_pct >= limit_pct * 0.95) and (prev_pct_2 < limit_pct * 0.8)
        if is_first_board:
            typical = (today['high'] + today['low'] + today['close']) / 3.0
            if today['close'] >= typical * (1.0 - cfg.layer4_vwap_tolerance):
                if today['close'] > today['open']:
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


# ── 兼容接口 ──

def check_momentum_entry(
    ts_code: str, trade_date: date, limit_pct: float, cfg, db_conn, verbose: bool = False,
) -> dict:
    """单股动能检查（兼容接口）"""
    ohlcv, pre = _batch_load_and_precompute([ts_code], trade_date, db_conn, cfg, days=30)
    return _check_single(ts_code, cfg, ohlcv, pre)


# ── 主流程 ──

def run_layer4_momentum_filter(
    stock_list: List[str],
    trade_date: date = None,
    cfg=None,
    verbose: bool = True,
) -> List[dict]:
    """动能与买入信号过滤。"""
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
        trade_date = row['max_date'] if row else datetime.now(BEIJING_TZ).date()
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

    # ── 阶段1: 批量加载 + 预计算指标 ──
    db_conn = get_db()
    try:
        if verbose:
            print(f"  ⏳ 批量加载K线 + 预计算指标 ({n_total} 只)...")
        ohlcv_cache, precomputed = _batch_load_and_precompute(
            stock_list, trade_date, db_conn, cfg, days=30)
    finally:
        db_conn.close()

    n_loaded = len(ohlcv_cache)
    if verbose:
        print(f"  ✓ 数据就绪: {n_loaded}/{n_total} 只 (EMA/乖离/布林已预计算)")

    # ── 阶段2: 快速预筛选（量比 + 乖离，纯 dict 查找）──
    quick_pass = []
    reject_stats = {'量比不符': 0, '乖离超标': 0, '天量上轨': 0, '无买入信号': 0, '数据不足': 0}

    for ts_code in stock_list:
        pre = precomputed.get(ts_code)
        if pre is None:
            reject_stats['数据不足'] += 1
            continue
        # 量比快速过滤
        vr = pre['vol_ratio']
        if vr < cfg.layer4_volume_ratio_min or vr > cfg.layer4_volume_ratio_max:
            reject_stats['量比不符'] += 1
            continue
        # 乖离快速过滤
        if abs(pre['bias_pct']) > cfg.layer4_max_bias_pct:
            reject_stats['乖离超标'] += 1
            continue
        quick_pass.append(ts_code)

    if verbose:
        print(f"  ⚡ 快速预筛: {len(quick_pass)} 只通过量比+乖离 "
              f"(淘汰量比{reject_stats['量比不符']} + 乖离{reject_stats['乖离超标']})")

    # ── 阶段3: K线形态并行检测（仅对预筛选通过的股票）──
    passed = []
    if not quick_pass:
        return passed

    # 纯 Python 检查极快（~0.01ms/只），串行即可避免线程调度开销
    _run_serial(quick_pass, cfg, ohlcv_cache, precomputed, passed, reject_stats, verbose)

    if verbose:
        print(f"  ✓ 通过: {len(passed)} 只")
        print(f"  ✗ 淘汰: {n_total - len(passed)} 只")
        for reason, count in reject_stats.items():
            if count > 0:
                print(f"    {reason}: {count} 只")

    return passed


def _run_parallel(stock_list, cfg, ohlcv_cache, precomputed, passed, reject_stats, verbose):
    """多线程并行（纯 Python 数据，无 GIL 竞争）"""
    n_total = len(stock_list)

    with ThreadPoolExecutor(max_workers=LAYER4_WORKERS) as executor:
        futures = {
            executor.submit(_check_single, ts_code, cfg, ohlcv_cache, precomputed): ts_code
            for ts_code in stock_list
        }

        completed = 0
        for future in as_completed(futures):
            completed += 1
            if verbose and completed % 200 == 0:
                print(f"  进度: {completed}/{n_total}")

            ts_code = futures[future]
            check = future.result()
            if check['passed']:
                passed.append({
                    'ts_code': ts_code,
                    'score_bonus': check['score_bonus'],
                    'signal_type': check['signal_type'],
                    'details': check['details'],
                })
            else:
                _count_reject(check['reject_reason'], reject_stats)


def _run_serial(stock_list, cfg, ohlcv_cache, precomputed, passed, reject_stats, verbose):
    """串行分析"""
    n_total = len(stock_list)
    for i, ts_code in enumerate(stock_list):
        if verbose and (i + 1) % 100 == 0:
            print(f"  进度: {i+1}/{n_total}")

        check = _check_single(ts_code, cfg, ohlcv_cache, precomputed)
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
