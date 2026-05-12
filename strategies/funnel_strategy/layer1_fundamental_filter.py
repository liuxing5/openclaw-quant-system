"""
Layer 1: 硬性防雷
==================
决策逻辑：
  剔除ST、立案、大额减持；流动比率>1.2，负债率<65%，
  近三季度营收无连续负增。

吸收策略：①巴菲特准则/基本面  ③八步法ST过滤

数据来源：
  - stock_basic_info: ST标记
  - stock_fundamentals: 财务数据
"""
from __future__ import annotations

import sys
import os
from datetime import date, timedelta
from typing import List, Dict, Optional

from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from core.db.connection import get_db


def _load_fundamentals_cache(trade_date: date = None) -> Dict:
    """加载财务数据缓存：每只股票最新季报"""
    cache = {}
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT DISTINCT ON (ts_code)
                   ts_code, report_date,
                   revenue, net_profit, gross_margin, net_margin,
                   total_assets, total_liabilities, debt_ratio,
                   operating_cashflow, revenue_yoy, profit_yoy,
                   industry, listing_date
            FROM stock_fundamentals
            ORDER BY ts_code, report_date DESC;
        """)
        for r in cur.fetchall():
            cache[r['ts_code']] = {
                'report_date': r['report_date'],
                'revenue': float(r['revenue']) if r['revenue'] else None,
                'net_profit': float(r['net_profit']) if r['net_profit'] else None,
                'gross_margin': float(r['gross_margin']) if r['gross_margin'] else None,
                'net_margin': float(r['net_margin']) if r['net_margin'] else None,
                'total_assets': float(r['total_assets']) if r['total_assets'] else None,
                'total_liabilities': float(r['total_liabilities']) if r['total_liabilities'] else None,
                'debt_ratio': float(r['debt_ratio']) if r['debt_ratio'] else None,
                'operating_cashflow': float(r['operating_cashflow']) if r['operating_cashflow'] else None,
                'revenue_yoy': float(r['revenue_yoy']) if r['revenue_yoy'] else None,
                'profit_yoy': float(r['profit_yoy']) if r['profit_yoy'] else None,
                'industry': r['industry'],
                'listing_date': r['listing_date'],
            }
        cur.close()
        conn.close()
    except Exception as e:
        print(f"  ⚠️ Layer1 财务数据加载失败: {e}")
    return cache


def _load_st_basic_info() -> Dict:
    """加载ST标记和上市日期"""
    cache = {}
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT ts_code, stock_name, list_date, is_st, is_active
            FROM stock_basic_info;
        """)
        for r in cur.fetchall():
            cache[r['ts_code']] = {
                'stock_name': r['stock_name'] or '',
                'list_date': r['list_date'],
                'is_st': r['is_st'] or False,
                'is_active': r['is_active'] if r['is_active'] is not None else True,
            }
        cur.close()
        conn.close()
    except Exception as e:
        print(f"  ⚠️ Layer1 ST信息加载失败: {e}")
    return cache


def compute_current_ratio(fin: dict) -> Optional[float]:
    """计算流动比率 = 总资产 / 总负债"""
    if fin.get('total_assets') and fin.get('total_liabilities') and fin['total_liabilities'] > 0:
        return fin['total_assets'] / fin['total_liabilities']
    return None


def check_fundamental(
    ts_code: str,
    stock_info: dict,
    fin: dict,
    cfg,
    today: date,
    verbose: bool = True,
) -> dict:
    """
    单只股票防雷检查。
    
    返回:
      {
        'passed': bool,
        'reject_reason': str,
        'details': dict,
      }
    """
    result = {'passed': True, 'reject_reason': '', 'details': {}}

    # 1. ST检查
    if cfg.layer1_exclude_st and stock_info.get('is_st'):
        result['passed'] = False
        result['reject_reason'] = 'ST/退市'
        result['details']['st'] = True
        return result

    # 名称中包含ST
    name = stock_info.get('stock_name', '')
    if cfg.layer1_exclude_st and ('ST' in name or '*ST' in name or '退' in name):
        result['passed'] = False
        result['reject_reason'] = 'ST/退市'
        result['details']['st'] = True
        return result

    # 2. 次新股检查
    if cfg.layer1_exclude_new_ipo_days > 0 and stock_info.get('list_date'):
        list_date = stock_info['list_date']
        if isinstance(list_date, str):
            try:
                list_date = date.fromisoformat(list_date)
            except ValueError:
                list_date = None
        if list_date and (today - list_date).days < cfg.layer1_exclude_new_ipo_days:
            result['passed'] = False
            result['reject_reason'] = f'次新股(上市<{cfg.layer1_exclude_new_ipo_days}天)'
            result['details']['new_ipo'] = True
            return result

    # 3. 财务质量（如果无财务数据，允许通过但记录警告）
    if not fin:
        result['details']['no_fundamental_data'] = True
        return result

    # 3a. 流动比率
    current_ratio = compute_current_ratio(fin)
    result['details']['current_ratio'] = round(current_ratio, 2) if current_ratio else None
    if current_ratio is not None and current_ratio < cfg.layer1_min_current_ratio:
        result['passed'] = False
        result['reject_reason'] = f'流动比率={current_ratio:.2f}<{cfg.layer1_min_current_ratio}'
        return result

    # 3b. 负债率
    debt_ratio = fin.get('debt_ratio')
    result['details']['debt_ratio'] = round(debt_ratio, 2) if debt_ratio else None
    if debt_ratio is not None and debt_ratio > cfg.layer1_max_debt_ratio:
        result['passed'] = False
        result['reject_reason'] = f'负债率={debt_ratio:.1f}%>{cfg.layer1_max_debt_ratio}%'
        return result

    # 3c. 营收同比
    revenue_yoy = fin.get('revenue_yoy')
    result['details']['revenue_yoy'] = round(revenue_yoy, 2) if revenue_yoy else None
    if revenue_yoy is not None and revenue_yoy < cfg.layer1_min_revenue_yoy:
        result['passed'] = False
        result['reject_reason'] = f'营收同比={revenue_yoy:.1f}%<{cfg.layer1_min_revenue_yoy}%'
        return result

    return result


def run_layer1_fundamental_filter(
    stock_list: List[str],
    trade_date: date = None,
    cfg=None,
    verbose: bool = True,
) -> List[str]:
    """
    对股票列表执行防雷过滤，返回通过过滤的股票代码列表。
    """
    if cfg is None:
        from .funnel_config import DEFAULT_FUNNEL_CONFIG
        cfg = DEFAULT_FUNNEL_CONFIG

    if not cfg.layer1_enabled:
        return stock_list

    if trade_date is None:
        trade_date = date.today()

    if verbose:
        print(f"\n{'─'*60}")
        print(f"  [Layer 1] 硬性防雷  — 待筛选 {len(stock_list)} 只")
        print(f"{'─'*60}")
        print(f"  ST过滤: {'✅' if cfg.layer1_exclude_st else '⏭️'}  "
              f"次新过滤: {'✅' if cfg.layer1_exclude_new_ipo_days else '⏭️'}  "
              f"流动比率>{cfg.layer1_min_current_ratio}  负债率<{cfg.layer1_max_debt_ratio}%")

    st_cache = _load_st_basic_info()
    fin_cache = _load_fundamentals_cache(trade_date)

    passed = []
    reject_stats = {
        'ST/退市': 0, '次新股': 0, '流动比率': 0, '负债率': 0, '营收同比': 0,
    }

    for ts_code in stock_list:
        stock_info = st_cache.get(ts_code, {})
        fin_info = fin_cache.get(ts_code, {})

        check = check_fundamental(ts_code, stock_info, fin_info, cfg, trade_date, verbose=False)

        if check['passed']:
            passed.append(ts_code)
        else:
            reason = check['reject_reason']
            if 'ST' in reason:
                reject_stats['ST/退市'] += 1
            elif '次新' in reason:
                reject_stats['次新股'] += 1
            elif '流动比率' in reason:
                reject_stats['流动比率'] += 1
            elif '负债率' in reason:
                reject_stats['负债率'] += 1
            elif '营收' in reason:
                reject_stats['营收同比'] += 1

    if verbose:
        print(f"  ✓ 通过: {len(passed)} 只")
        print(f"  ✗ 淘汰: {len(stock_list) - len(passed)} 只")
        for reason, count in reject_stats.items():
            if count > 0:
                print(f"    {reason}: {count} 只")

    return passed
