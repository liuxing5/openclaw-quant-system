"""
Layer 2: 流动性筛选
====================
决策逻辑：
  20日日均成交额>1亿，流通市值>20亿，换手3~15%。

吸收策略：③八步法成交额+市值+换手  ⑥人气替代散户版

数据来源：daily_quotes 表
"""
from __future__ import annotations

import sys
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone, timedelta
from typing import List, Dict

import pandas as pd
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from core.db.connection import get_db_fresh

BEIJING_TZ = timezone(timedelta(hours=8))

def _load_liquidity_data(stock_list: List[str], trade_date: date) -> Dict:
    """
    批量加载流动性数据。
    返回 {ts_code: {amount, pct_chg, turnover_rate, circulating_market_cap, total_market_cap}}
    """
    if not stock_list:
        return {}

    cache = {}
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT ts_code, amount, pct_chg, turnover_rate,
                   circulating_market_cap, total_market_cap
            FROM daily_quotes
            WHERE trade_date = %s AND ts_code = ANY(%s);
        """, (trade_date, stock_list))
        for r in cur.fetchall():
            cache[r['ts_code']] = {
                'amount': float(r['amount']) if r['amount'] else 0,
                'pct_chg': float(r['pct_chg']) if r['pct_chg'] else 0,
                'turnover_rate': float(r['turnover_rate']) if r['turnover_rate'] else 0,
                'circulating_market_cap': float(r['circulating_market_cap']) if r['circulating_market_cap'] else 0,
                'total_market_cap': float(r['total_market_cap']) if r['total_market_cap'] else 0,
            }
        cur.close()
    except Exception as e:
        print(f"  ⚠️ Layer2 流动性数据加载失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()
    return cache


def _load_20d_avg_amount(stock_list: List[str], trade_date: date) -> Dict[str, float]:
    """计算20日日均成交额"""
    if not stock_list:
        return {}

    start_date = trade_date - timedelta(days=40)
    cache = {}
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT ts_code, amount
            FROM daily_quotes
            WHERE trade_date >= %s AND trade_date <= %s
              AND ts_code = ANY(%s)
            ORDER BY ts_code, trade_date DESC;
        """, (start_date, trade_date, stock_list))
        rows = cur.fetchall()
        cur.close()

        df = pd.DataFrame(rows, columns=['ts_code', 'amount'])
        df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
        for ts_code, group in df.groupby('ts_code'):
            amounts = group['amount'].dropna().head(20)
            if len(amounts) >= 1:
                cache[ts_code] = float(amounts.mean())
    except Exception as e:
        print(f"  ⚠️ Layer2 均量计算失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()
    return cache


def run_layer2_liquidity_filter(
    stock_list: List[str],
    trade_date: date = None,
    cfg=None,
    verbose: bool = True,
) -> List[str]:
    """
    流动性过滤，返回通过过滤的股票代码列表。
    """
    if cfg is None:
        from .funnel_config import DEFAULT_FUNNEL_CONFIG
        cfg = DEFAULT_FUNNEL_CONFIG

    if not cfg.layer2_enabled:
        return stock_list

    if trade_date is None:
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT MAX(trade_date) as max_date FROM daily_quotes;")
            row = cur.fetchone()
            trade_date = row['max_date'] if row else datetime.now(BEIJING_TZ).date()
            cur.close()
        finally:
            if conn and not conn.closed:
                conn.close()

    if verbose:
        min_amt_yi = cfg.layer2_min_avg_amount_20d / 1e8
        min_mcap_yi = cfg.layer2_min_circulating_mcap / 1e8
        print(f"\n{'─'*60}")
        print(f"  [Layer 2] 流动性筛选  — 待筛选 {len(stock_list)} 只")
        print(f"{'─'*60}")
        print(f"  20日均额>{min_amt_yi:.0f}亿  流通市值>{min_mcap_yi:.0f}亿  "
              f"换手{cfg.layer2_turn_rate_min}~{cfg.layer2_turn_rate_max}%")

    # 并行执行两个独立查询（节省一半DB往返时间）
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_liq = ex.submit(_load_liquidity_data, stock_list, trade_date)
        f_avg = ex.submit(_load_20d_avg_amount, stock_list, trade_date)
        liq_cache = f_liq.result()
        avg_amount_cache = f_avg.result()

    # 数据完整性检测：turnover_rate 全部为0说明数据管道未填充此字段
    has_turnover = any(v.get('turnover_rate', 0) > 0 for v in liq_cache.values())
    if not has_turnover and verbose:
        print(f"  ⚠️ turnover_rate 数据缺失，跳过换手率和市值过滤")

    passed = []
    reject_stats = {'成交额不足': 0, '市值不足': 0, '换手不符': 0}

    for ts_code in stock_list:
        liq = liq_cache.get(ts_code, {})
        avg_amount = avg_amount_cache.get(ts_code, 0)

        # 20日均成交额（始终检查）
        if avg_amount < cfg.layer2_min_avg_amount_20d:
            reject_stats['成交额不足'] += 1
            continue

        if has_turnover:
            # 流通市值（从表读取，缺失时用成交额/换手率估算）
            circ_mcap = liq.get('circulating_market_cap', 0) or liq.get('total_market_cap', 0)
            if circ_mcap <= 0:
                amount = liq.get('amount', 0)
                turn = liq.get('turnover_rate', 0)
                if amount > 0 and turn > 0:
                    circ_mcap = amount / (turn / 100.0)
            if circ_mcap < cfg.layer2_min_circulating_mcap:
                reject_stats['市值不足'] += 1
                continue

            # 换手率
            turn = liq.get('turnover_rate', 0)
            if turn < cfg.layer2_turn_rate_min or turn > cfg.layer2_turn_rate_max:
                reject_stats['换手不符'] += 1
                continue

        passed.append(ts_code)

    if verbose:
        print(f"  ✓ 通过: {len(passed)} 只")
        print(f"  ✗ 淘汰: {len(stock_list) - len(passed)} 只")
        for reason, count in reject_stats.items():
            if count > 0:
                print(f"    {reason}: {count} 只")

    return passed
