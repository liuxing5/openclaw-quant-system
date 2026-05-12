"""
Layer 5: 人气精选
==================
决策逻辑：
  近5日综合评分（含涨幅3~5%，贴线，分时平稳）≥80；
  人气榜排名≤100可加分。

吸收策略：③隔夜八步法  ⑥人气榜前30
"""
from __future__ import annotations

import sys
import os
from datetime import date, timedelta
from typing import List, Dict

import pandas as pd
import numpy as np
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from core.db.connection import get_db


def _calc_ema(values: pd.Series, period: int) -> pd.Series:
    return values.ewm(span=period, adjust=False).mean()


def _load_stock_data(ts_code: str, trade_date: date, days: int = 30) -> pd.DataFrame:
    """加载单股近N日数据"""
    start_date = trade_date - timedelta(days=days)
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT trade_date, open, high, low, close, volume, amount,
                   pct_chg, turnover_rate, volume_ratio, amplitude
            FROM daily_quotes
            WHERE ts_code = %s AND trade_date >= %s AND trade_date <= %s
            ORDER BY trade_date ASC;
        """, (ts_code, start_date, trade_date))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
        df.set_index('trade_date', inplace=True)
        for col in ['open', 'high', 'low', 'close', 'volume', 'amount',
                      'pct_chg', 'turnover_rate', 'volume_ratio', 'amplitude']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        return df
    except Exception:
        return pd.DataFrame()


def _load_popularity_ranks(trade_date: date) -> Dict[str, int]:
    """从 strong_stock_rank 加载人气排名"""
    rank_map = {}
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT ts_code, MIN(rank_position) as best_rank
            FROM strong_stock_rank
            WHERE trade_date = %s AND rank_position IS NOT NULL
            GROUP BY ts_code;
        """, (trade_date,))
        for r in cur.fetchall():
            rank_map[r['ts_code']] = int(r['best_rank'])
        cur.close()
        conn.close()
    except Exception:
        pass
    return rank_map


def compute_popularity_score(
    ts_code: str,
    trade_date: date,
    rank_map: Dict[str, int],
    cfg,
    trend_bonus: float = 0.0,
    momentum_bonus: float = 0.0,
) -> dict:
    """
    综合评分计算。
    基础分50 + 涨幅评分(0~20) + 贴线评分(0~15) + 分时平稳(0~10)
    + 人气加分(0~5) + 前层bonus
    
    返回: {score, pct, pct_score, bias_score, stability_score, popularity_bonus, ...}
    """
    result = {
        'score': 0,
        'pct': 0.0,
        'pct_score': 0,
        'bias_score': 0,
        'stability_score': 0,
        'popularity_bonus': 0,
        'trend_bonus': trend_bonus,
        'momentum_bonus': momentum_bonus,
        'tags': [],
    }

    df = _load_stock_data(ts_code, trade_date, days=30)
    if df.empty or len(df) < 3:
        return result

    today = df.iloc[-1]
    close = today['close']

    # A. 涨幅评分（3~5%满分20，1~7范围给分）
    pct = today.get('pct_chg', 0) or 0
    result['pct'] = round(pct, 2)
    if cfg.layer5_pct_range_low <= pct <= cfg.layer5_pct_range_high:
        result['pct_score'] = 20
        result['tags'].append('黄金涨幅')
    elif 1.0 <= pct < cfg.layer5_pct_range_low:
        result['pct_score'] = int((pct - 1.0) / (cfg.layer5_pct_range_low - 1.0) * 15)
        result['tags'].append('涨幅偏低')
    elif cfg.layer5_pct_range_high < pct <= 8.0:
        result['pct_score'] = int((8.0 - pct) / (8.0 - cfg.layer5_pct_range_high) * 10)
        result['tags'].append('涨幅偏高')

    # B. 贴线评分（收盘价 vs MA5偏离度，越小越好）
    close_series = df['close']
    if len(close_series) >= 5:
        ma5 = close_series.rolling(window=5, min_periods=5).mean().iloc[-1]
        bias = abs(close - ma5) / ma5 if ma5 > 0 else 1
        if bias < 0.01:
            result['bias_score'] = 15
            result['tags'].append('紧贴MA5')
        elif bias < 0.02:
            result['bias_score'] = 12
            result['tags'].append('贴MA5')
        elif bias < 0.03:
            result['bias_score'] = 8
        elif bias < 0.05:
            result['bias_score'] = 3
            result['tags'].append('乖离偏大')

    # C. 分时平稳（用振幅倒数衡量，振幅越小越平稳）
    amplitude = today.get('amplitude', 0) or 0
    if 2.0 <= amplitude <= 5.0:
        result['stability_score'] = 10
        result['tags'].append('分时平稳')
    elif amplitude < 8.0:
        result['stability_score'] = 5
    elif amplitude < 12.0:
        result['stability_score'] = 2

    # D. 人气榜加分
    rank = rank_map.get(ts_code, 999)
    if rank <= cfg.layer5_popularity_rank_threshold:
        result['popularity_bonus'] = cfg.layer5_bonus_popularity_rank
        result['tags'].append(f'人气#{rank}')

    # 总分
    base = 50
    result['score'] = (
        base + result['pct_score'] + result['bias_score'] + result['stability_score']
        + result['popularity_bonus'] + result['trend_bonus'] + result['momentum_bonus']
    )
    result['score'] = min(result['score'], 100)

    return result


def run_layer5_popularity_filter(
    stock_items: List[dict],
    trade_date: date = None,
    cfg=None,
    verbose: bool = True,
) -> List[dict]:
    """
    人气精选，返回通过评分阈值的股票详情。
    输入: [{ts_code, score_bonus (from L3), momentum_bonus (from L4)}, ...]
    输出: [{ts_code, score, pct, tags, ...}, ...] 按评分降序
    """
    if cfg is None:
        from .funnel_config import DEFAULT_FUNNEL_CONFIG
        cfg = DEFAULT_FUNNEL_CONFIG

    if not cfg.layer5_enabled:
        return stock_items

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
        print(f"  [Layer 5] 人气精选  — 待评分 {len(stock_items)} 只")
        print(f"{'─'*60}")
        print(f"  综合评分≥{cfg.layer5_min_composite_score}  "
              f"涨幅{cfg.layer5_pct_range_low}~{cfg.layer5_pct_range_high}%  "
              f"人气榜加分≤{cfg.layer5_popularity_rank_threshold}")

    rank_map = _load_popularity_ranks(trade_date)

    scored = []
    for item in stock_items:
        ts_code = item['ts_code']
        trend_bonus = item.get('score_bonus', 0.0)  # L3
        momentum_bonus = item.get('score_bonus', 0.0) if 'signal_type' in item else 0.0  # L4

        pop_result = compute_popularity_score(
            ts_code, trade_date, rank_map, cfg,
            trend_bonus=trend_bonus,
            momentum_bonus=momentum_bonus,
        )

        if pop_result['score'] >= cfg.layer5_min_composite_score:
            item['score'] = pop_result['score']
            item['pct'] = pop_result['pct']
            item['tags'] = pop_result['tags']
            item['pct_score'] = pop_result['pct_score']
            item['bias_score'] = pop_result['bias_score']
            item['stability_score'] = pop_result['stability_score']
            item['popularity_bonus'] = pop_result['popularity_bonus']
            scored.append(item)

    scored.sort(key=lambda x: x['score'], reverse=True)

    if verbose:
        print(f"  ✓ 通过: {len(scored)} 只")
        print(f"  ✗ 淘汰: {len(stock_items) - len(scored)} 只 (得分<{cfg.layer5_min_composite_score})")

    return scored
