"""
Baostock 数据适配器
====================
为融合元策略提供数据，替代 PostgreSQL 直接查询。
使用 baostock 在线接口获取行情和基本面数据。
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import baostock as bs

logger = logging.getLogger(__name__)

# baostock 登录状态
_logged_in = False


def ensure_login():
    global _logged_in
    if not _logged_in:
        lg = bs.login()
        if lg.error_code != '0':
            raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")
        _logged_in = True
        logger.info("baostock 登录成功")


def logout():
    global _logged_in
    if _logged_in:
        bs.logout()
        _logged_in = False


def ts_to_bs(ts_code: str) -> str:
    """tushare代码 -> baostock代码  e.g. 000001.SZ -> sz.000001"""
    if '.' not in ts_code:
        return ts_code
    code, suffix = ts_code.split('.')
    prefix = suffix.lower()
    return f"{prefix}.{code}"


def bs_to_ts(bs_code: str) -> str:
    """baostock代码 -> tushare代码  e.g. sz.000001 -> 000001.SZ"""
    if '.' not in bs_code:
        return bs_code
    prefix, code = bs_code.split('.')
    suffix = prefix.upper()
    return f"{code}.{suffix}"


def get_trading_days(start_date: date, end_date: date) -> List[date]:
    """获取交易日列表"""
    ensure_login()
    rs = bs.query_trade_dates(start_date=start_date.strftime('%Y-%m-%d'),
                              end_date=end_date.strftime('%Y-%m-%d'))
    days = []
    while rs.error_code == '0' and rs.next():
        days.append(date.fromisoformat(rs.get_row_data()[0]))
    return sorted(days)


def get_daily_quotes(ts_code: str, start_date: date, end_date: date,
                     fields: str = "date,open,high,low,close,volume,amount,turn,pctChg") -> pd.DataFrame:
    """获取日K线数据"""
    ensure_login()
    bs_code = ts_to_bs(ts_code)
    rs = bs.query_history_k_data_plus(
        bs_code, fields,
        start_date=start_date.strftime('%Y-%m-%d'),
        end_date=end_date.strftime('%Y-%m-%d'),
        frequency="d", adjustflag="2")  # 前复权

    rows = []
    while rs.error_code == '0' and rs.next():
        rows.append(rs.get_row_data())

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=fields.split(','))
    # 重命名列以兼容
    rename_map = {
        'date': 'trade_date',
        'turn': 'turnover_rate',
        'pctChg': 'pct_chg',
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    # 转换数值
    numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount',
                    'turnover_rate', 'pct_chg']
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')

    return df


def get_market_overview(trade_date: date) -> Dict:
    """
    获取市场概览（上涨/下跌家数等）
    使用上证指数+深证成指涨跌作为大盘代理，快速判断
    """
    ensure_login()
    result = {'advancers': 0, 'decliners': 0, 'total': 0,
              'breadth_ratio': 0.0, 'sh_close': 0.0, 'sz_close': 0.0}

    date_str = trade_date.strftime('%Y-%m-%d')
    start_str = (trade_date - timedelta(days=5)).strftime('%Y-%m-%d')

    # 快速方案：用上证指数涨跌代理市场广度
    # 指数涨>1%视为普涨(广度0.7)，涨0-1%视为偏涨(0.55)，跌0-1%视为偏跌(0.45)，跌>1%视为普跌(0.3)
    rs = bs.query_history_k_data_plus(
        "sh.000001", "date,close,pctChg",
        start_date=start_str, end_date=date_str, frequency="d")
    rows = []
    while rs.error_code == '0' and rs.next():
        rows.append(rs.get_row_data())

    if rows:
        last = rows[-1]
        pct = float(last[2]) if last[2] else 0
        result['sh_close'] = float(last[1]) if last[1] else 0

        if pct > 1.0:
            breadth = 0.70
        elif pct > 0:
            breadth = 0.55
        elif pct > -1.0:
            breadth = 0.45
        else:
            breadth = 0.30

        # 估算涨跌家数（基于广度比例和全市场约5000只）
        total = 5000
        advancers = int(total * breadth)
        result['advancers'] = advancers
        result['decliners'] = total - advancers
        result['total'] = total
        result['breadth_ratio'] = breadth

    return result


def get_active_stocks(trade_date: date, min_amount: float = 5e7) -> List[str]:
    """
    获取当日活跃标的
    直接使用沪深300+中证500成分股作为股票池，不再逐个查询成交额
    """
    ensure_login()
    date_str = trade_date.strftime('%Y-%m-%d')

    codes = []

    # 获取沪深300
    rs = bs.query_hs300_stocks(date=date_str)
    while rs.error_code == '0' and rs.next():
        row = rs.get_row_data()
        codes.append(row[1])

    # 获取中证500
    rs2 = bs.query_zz500_stocks(date=date_str)
    while rs2.error_code == '0' and rs2.next():
        row = rs2.get_row_data()
        if row[1] not in codes:
            codes.append(row[1])

    # 转换为tushare格式
    return [bs_to_ts(c) for c in codes]


def get_stock_basic(code: str) -> Dict:
    """获取股票基本信息"""
    ensure_login()
    rs = bs.query_stock_basic(code_name="")
    while rs.error_code == '0' and rs.next():
        row = rs.get_row_data()
        if row[0] == code or row[1] == code:
            return {
                'code': row[0],
                'code_name': row[1],
                'ipoDate': row[2],
                'outDate': row[3],
                'type': row[4],
                'status': row[5],
            }
    return {}


def get_fundamental_data(ts_code: str, year: int, quarter: int) -> Dict:
    """获取基本面数据"""
    ensure_login()
    bs_code = ts_to_bs(ts_code)
    code_num = bs_code.split('.')[1]

    result = {}

    # 盈利能力
    rs = bs.query_profit_data(code=bs_code, year=year, quarter=quarter)
    while rs.error_code == '0' and rs.next():
        row = rs.get_row_data()
        result['roe'] = float(row[3]) if row[3] else None
        result['net_margin'] = float(row[4]) if row[4] else None
        result['gross_margin'] = float(row[5]) if row[5] else None
        result['net_profit'] = float(row[6]) if row[6] else None
        result['eps'] = float(row[7]) if row[7] else None
        break

    # 偿债能力
    rs2 = bs.query_balance_data(code=bs_code, year=year, quarter=quarter)
    while rs2.error_code == '0' and rs2.next():
        row = rs2.get_row_data()
        result['current_ratio'] = float(row[3]) if row[3] else None
        result['debt_ratio'] = float(row[6]) if row[6] else None
        result['total_assets'] = float(row[7]) if row[7] else None
        result['total_liabilities'] = float(row[8]) if row[8] else None
        break

    # 现金流
    rs3 = bs.query_cash_flow_data(code=bs_code, year=year, quarter=quarter)
    while rs3.error_code == '0' and rs3.next():
        row = rs3.get_row_data()
        result['operating_cashflow'] = float(row[3]) if row[3] else None
        break

    # 成长能力
    rs4 = bs.query_growth_data(code=bs_code, year=year, quarter=quarter)
    while rs4.error_code == '0' and rs4.next():
        row = rs4.get_row_data()
        result['revenue_yoy'] = float(row[3]) if row[3] else None
        result['profit_yoy'] = float(row[4]) if row[4] else None
        break

    return result


# ============================================================
# 缓存层 - 减少重复查询
# ============================================================

_quotes_cache: Dict[str, pd.DataFrame] = {}
_cache_max_size = 200


def get_daily_quotes_cached(ts_code: str, start_date: date, end_date: date,
                            fields: str = "date,open,high,low,close,volume,amount,pctChg,turn") -> pd.DataFrame:
    """带缓存的日K线查询"""
    key = f"{ts_code}_{start_date}_{end_date}_{fields}"
    if key in _quotes_cache:
        return _quotes_cache[key]

    df = get_daily_quotes(ts_code, start_date, end_date, fields=fields)

    if len(_quotes_cache) >= _cache_max_size:
        # 清理最旧的一半
        keys = list(_quotes_cache.keys())
        for k in keys[:len(keys)//2]:
            del _quotes_cache[k]

    _quotes_cache[key] = df
    return df


def clear_cache():
    _quotes_cache.clear()
