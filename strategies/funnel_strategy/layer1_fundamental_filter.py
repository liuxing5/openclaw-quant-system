"""
Layer 1: 硬性防雷
==================
决策逻辑：
  剔除ST、立案、大额减持；流动比率>1.2，负债率<65%，
  经营现金流/净利润>=0.5，商誉/净资产<=50%；
  近60天无减持公告，质押比例<=50%；
  应收账款/营收<=50%或营收增速>5%，存货/营收<=60%或营收增速>5%。

吸收策略：①巴菲特准则/基本面  ③八步法ST过滤

数据来源：
  - stock_basic_info: ST标记
  - stock_fundamentals: 财务数据
  - stock_announcements: 减持公告
"""
from __future__ import annotations

import sys
import os
from datetime import date, datetime, timezone, timedelta
from typing import List, Dict, Optional

from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from core.db.connection import get_db

BEIJING_TZ = timezone(timedelta(hours=8))


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
                   total_assets, total_liabilities,
                   current_assets, current_liabilities,
                   debt_ratio, equity,
                   operating_cashflow, accounts_receivable, inventory,
                   goodwill, pledge_ratio,
                   revenue_yoy, profit_yoy,
                   industry, listing_date
            FROM stock_fundamentals
            WHERE report_date <= %(trade_date)s
            ORDER BY ts_code, report_date DESC;
        """, {"trade_date": trade_date})
        for r in cur.fetchall():
            cache[r['ts_code']] = {
                'report_date': r['report_date'],
                'revenue': float(r['revenue']) if r['revenue'] else None,
                'net_profit': float(r['net_profit']) if r['net_profit'] else None,
                'gross_margin': float(r['gross_margin']) if r['gross_margin'] else None,
                'net_margin': float(r['net_margin']) if r['net_margin'] else None,
                'total_assets': float(r['total_assets']) if r['total_assets'] else None,
                'total_liabilities': float(r['total_liabilities']) if r['total_liabilities'] else None,
                'current_assets': float(r['current_assets']) if r['current_assets'] else None,
                'current_liabilities': float(r['current_liabilities']) if r['current_liabilities'] else None,
                'debt_ratio': float(r['debt_ratio']) if r['debt_ratio'] else None,
                'equity': float(r['equity']) if r['equity'] else None,
                'operating_cashflow': float(r['operating_cashflow']) if r['operating_cashflow'] else None,
                'accounts_receivable': float(r['accounts_receivable']) if r['accounts_receivable'] else None,
                'inventory': float(r['inventory']) if r['inventory'] else None,
                'goodwill': float(r['goodwill']) if r['goodwill'] else None,
                'pledge_ratio': float(r['pledge_ratio']) if r['pledge_ratio'] else None,
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


def _load_reduction_announcements(trade_date: date, lookback_days: int = 60) -> set:
    """加载近N天有减持公告的股票代码集合"""
    reduction_codes = set()
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        since = trade_date - timedelta(days=lookback_days)
        cur.execute("""
            SELECT DISTINCT ts_code
            FROM stock_announcements
            WHERE publish_date >= %s
              AND (title ILIKE '%减持%' OR title ILIKE '%reduce%')
              AND ts_code IS NOT NULL;
        """, (since,))
        for r in cur.fetchall():
            reduction_codes.add(r['ts_code'])
        cur.close()
        conn.close()
    except Exception:
        pass
    return reduction_codes


def compute_current_ratio(fin: dict) -> Optional[float]:
    """流动比率 = 流动资产/流动负债；数据不足时降级为总资产/总负债"""
    ca = fin.get('current_assets')
    cl = fin.get('current_liabilities')
    if ca is not None and cl is not None and cl > 0:
        return ca / cl
    ta = fin.get('total_assets')
    tl = fin.get('total_liabilities')
    if ta is not None and tl is not None and tl > 0:
        return ta / tl
    return None


def compute_cashflow_ratio(fin: dict) -> Optional[float]:
    """经营现金流 / 净利润"""
    cf = fin.get('operating_cashflow')
    np_ = fin.get('net_profit')
    if cf is not None and np_ is not None and np_ > 0:
        return cf / np_
    return None


def compute_goodwill_pct(fin: dict) -> Optional[float]:
    """商誉 / 净资产 * 100%"""
    gw = fin.get('goodwill')
    eq = fin.get('equity')
    if gw is not None and eq is not None and eq > 0:
        return gw / eq * 100
    return None


def check_ar_anomaly(fin: dict) -> Optional[bool]:
    """应收账款异常: 应收/营收 > 50% 且营收增速 < 5%"""
    ar = fin.get('accounts_receivable')
    rev = fin.get('revenue')
    rev_yoy = fin.get('revenue_yoy')
    if ar is not None and rev is not None and rev_yoy is not None and rev > 0 and ar > 0:
        if (ar / rev * 100) > 50 and rev_yoy < 5:
            return True
    return None


def check_inventory_anomaly(fin: dict) -> Optional[bool]:
    """存货异常: 存货/营收 > 60% 且营收增速 < 5%"""
    inv = fin.get('inventory')
    rev = fin.get('revenue')
    rev_yoy = fin.get('revenue_yoy')
    if inv is not None and rev is not None and rev_yoy is not None and rev > 0 and inv > 0:
        if (inv / rev * 100) > 60 and rev_yoy < 5:
            return True
    return None


def check_fundamental(
    ts_code: str,
    stock_info: dict,
    fin: dict,
    cfg,
    today: date,
    reduction_codes: set = None,
    verbose: bool = True,
) -> dict:
    """单只股票防雷检查。返回 {'passed': bool, 'reject_reason': str, 'details': dict}"""
    if reduction_codes is None:
        reduction_codes = set()

    result = {'passed': True, 'reject_reason': '', 'details': {}}

    # 1. ST检查
    if cfg.layer1_exclude_st and stock_info.get('is_st'):
        result['passed'] = False
        result['reject_reason'] = 'ST/退市'
        result['details']['st'] = True
        return result

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

    # 3. 无财务数据则放行（记录警告）
    if not fin:
        result['details']['no_fundamental_data'] = True
        return result

    # 3a. 流动比率
    current_ratio = compute_current_ratio(fin)
    result['details']['current_ratio'] = round(current_ratio, 2) if current_ratio is not None else None
    if current_ratio is not None and current_ratio < cfg.layer1_min_current_ratio:
        result['passed'] = False
        result['reject_reason'] = f'流动比率={current_ratio:.2f}<{cfg.layer1_min_current_ratio}'
        return result

    # 3b. 负债率
    debt_ratio = fin.get('debt_ratio')
    result['details']['debt_ratio'] = round(debt_ratio, 2) if debt_ratio is not None else None
    if debt_ratio is not None and debt_ratio > cfg.layer1_max_debt_ratio:
        result['passed'] = False
        result['reject_reason'] = f'负债率={debt_ratio:.1f}%>{cfg.layer1_max_debt_ratio}%'
        return result

    # 3c. 营收同比
    revenue_yoy = fin.get('revenue_yoy')
    result['details']['revenue_yoy'] = round(revenue_yoy, 2) if revenue_yoy is not None else None
    if revenue_yoy is not None and revenue_yoy < cfg.layer1_min_revenue_yoy:
        result['passed'] = False
        result['reject_reason'] = f'营收同比={revenue_yoy:.1f}%<{cfg.layer1_min_revenue_yoy}%'
        return result

    # 3d. P0: 亏损检测（净利润为负直接淘汰）
    net_profit = fin.get('net_profit')
    if net_profit is not None and net_profit <= 0:
        result['passed'] = False
        result['reject_reason'] = f'净利润为负({net_profit:.1f}亿)'
        result['details']['net_profit_negative'] = True
        return result

    # 3e. P0: 现金流质量
    cashflow_ratio = compute_cashflow_ratio(fin)
    result['details']['cashflow_ratio'] = round(cashflow_ratio, 2) if cashflow_ratio is not None else None
    if cashflow_ratio is not None and cashflow_ratio < cfg.layer1_min_cashflow_ratio:
        net_profit = fin.get('net_profit')
        if net_profit and net_profit > 0:
            result['passed'] = False
            result['reject_reason'] = f'现金流比={cashflow_ratio:.2f}<{cfg.layer1_min_cashflow_ratio}'
            return result

    # 3e. P0: 商誉暴雷风险
    goodwill_pct = compute_goodwill_pct(fin)
    result['details']['goodwill_pct'] = round(goodwill_pct, 2) if goodwill_pct is not None else None
    if goodwill_pct is not None and goodwill_pct > cfg.layer1_max_goodwill_pct:
        result['passed'] = False
        result['reject_reason'] = f'商誉/净资产={goodwill_pct:.1f}%>{cfg.layer1_max_goodwill_pct}%'
        return result

    # 3f. P1: 减持检测
    if cfg.layer1_check_reduction and ts_code in reduction_codes:
        result['passed'] = False
        result['reject_reason'] = f'近{cfg.layer1_reduction_days}天有减持公告'
        result['details']['reduction'] = True
        return result

    # 3g. P1: 质押比例
    pledge_ratio = fin.get('pledge_ratio')
    result['details']['pledge_ratio'] = round(pledge_ratio, 2) if pledge_ratio is not None else None
    if cfg.layer1_check_pledge and pledge_ratio is not None and pledge_ratio > cfg.layer1_max_pledge_ratio:
        result['passed'] = False
        result['reject_reason'] = f'质押比例={pledge_ratio:.1f}%>{cfg.layer1_max_pledge_ratio}%'
        return result

    # 3h. P2: 应收账款异常
    if cfg.layer1_check_ar_anomaly:
        ar_anomaly = check_ar_anomaly(fin)
        result['details']['ar_anomaly'] = ar_anomaly
        if ar_anomaly:
            result['passed'] = False
            result['reject_reason'] = '应收/营收>50%且营收低增(坏账风险)'
            return result

    # 3i. P2: 存货异常
    if cfg.layer1_check_inventory_anomaly:
        inv_anomaly = check_inventory_anomaly(fin)
        result['details']['inventory_anomaly'] = inv_anomaly
        if inv_anomaly:
            result['passed'] = False
            result['reject_reason'] = '存货/营收>60%且营收低增(滞销风险)'
            return result

    return result


def run_layer1_fundamental_filter(
    stock_list: List[str],
    trade_date: date = None,
    cfg=None,
    verbose: bool = True,
) -> List[str]:
    """对股票列表执行防雷过滤，返回通过过滤的股票代码列表。"""
    if cfg is None:
        from .funnel_config import DEFAULT_FUNNEL_CONFIG
        cfg = DEFAULT_FUNNEL_CONFIG

    if not cfg.layer1_enabled:
        return stock_list

    if trade_date is None:
        trade_date = datetime.now(BEIJING_TZ).date()

    if verbose:
        print(f"\n{'─'*60}")
        print(f"  [Layer 1] 硬性防雷  — 待筛选 {len(stock_list)} 只")
        print(f"{'─'*60}")
        print(f"  ST过滤: {'✅' if cfg.layer1_exclude_st else '⏭️'}  "
              f"次新: {'✅' if cfg.layer1_exclude_new_ipo_days else '⏭️'}  "
              f"流动比率>{cfg.layer1_min_current_ratio}  负债率<{cfg.layer1_max_debt_ratio}%")
        print(f"  现金流比≥{cfg.layer1_min_cashflow_ratio}  商誉/净资产≤{cfg.layer1_max_goodwill_pct}%")
        if cfg.layer1_check_reduction:
            print(f"  减持检测: ✅ (近{cfg.layer1_reduction_days}天)")
        if cfg.layer1_check_pledge:
            print(f"  质押检测: ✅ (≤{cfg.layer1_max_pledge_ratio}%)")
        if cfg.layer1_check_ar_anomaly:
            print(f"  应收异常: ✅")
        if cfg.layer1_check_inventory_anomaly:
            print(f"  存货异常: ✅")

    st_cache = _load_st_basic_info()
    fin_cache = _load_fundamentals_cache(trade_date)
    reduction_codes = _load_reduction_announcements(
        trade_date, cfg.layer1_reduction_days
    ) if cfg.layer1_check_reduction else set()

    passed = []
    reject_stats = {
        'ST/退市': 0, '次新股': 0, '流动比率': 0, '负债率': 0, '营收同比': 0,
        '现金流质量': 0, '商誉风险': 0, '减持': 0, '质押': 0,
        '应收异常': 0, '存货异常': 0,
    }

    for ts_code in stock_list:
        stock_info = st_cache.get(ts_code, {})
        fin_info = fin_cache.get(ts_code, {})

        check = check_fundamental(
            ts_code, stock_info, fin_info, cfg, trade_date,
            reduction_codes=reduction_codes, verbose=False,
        )

        if check['passed']:
            passed.append(ts_code)
        else:
            reason = check['reject_reason']
            if 'ST' in reason or '退市' in reason:
                reject_stats['ST/退市'] += 1
            elif '次新' in reason:
                reject_stats['次新股'] += 1
            elif '流动比率' in reason:
                reject_stats['流动比率'] += 1
            elif '负债率' in reason:
                reject_stats['负债率'] += 1
            elif '营收' in reason:
                reject_stats['营收同比'] += 1
            elif '现金流' in reason:
                reject_stats['现金流质量'] += 1
            elif '商誉' in reason:
                reject_stats['商誉风险'] += 1
            elif '减持' in reason:
                reject_stats['减持'] += 1
            elif '质押' in reason:
                reject_stats['质押'] += 1
            elif '应收' in reason:
                reject_stats['应收异常'] += 1
            elif '存货' in reason:
                reject_stats['存货异常'] += 1

    if verbose:
        print(f"  ✓ 通过: {len(passed)} 只")
        print(f"  ✗ 淘汰: {len(stock_list) - len(passed)} 只")
        for reason, count in reject_stats.items():
            if count > 0:
                print(f"    {reason}: {count} 只")

    return passed
