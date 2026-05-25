"""
数据库数据适配器 v1.0
====================
为融合元策略回测提供数据，使用 PostgreSQL 数据库替代 baostock 在线接口。
速度优势：批量SQL查询 >> 逐个API调用。

数据源：daily_quotes 表（含 OHLCV + pct_chg + turnover_rate + volume_ratio +
         pe_ratio + pb_ratio + main_force_net + amplitude 等完整字段）
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from core.db.connection import get_db  # 使用复用连接，避免Supabase连接数限制

logger = logging.getLogger(__name__)

# 环境变量初始化
def _ensure_env():
    if not os.getenv('POSTGRES_HOST'):
        os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
        os.environ['POSTGRES_PORT'] = '5432'  # 直连模式，避免连接池耗尽
        os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
        os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
        os.environ['POSTGRES_DB'] = 'postgres'
        os.environ['POSTGRES_SSLMODE'] = 'require'

_ensure_env()

# ============================================================
# 缓存
# ============================================================
_cache: Dict[str, pd.DataFrame] = {}
_trading_days_cache: Optional[List[date]] = None
_index_cache: Dict[str, pd.DataFrame] = {}


def clear_cache():
    global _cache, _trading_days_cache, _index_cache
    _cache.clear()
    _trading_days_cache = None
    _index_cache.clear()


# ============================================================
# 核心数据接口
# ============================================================

def get_trading_days(start_date: date, end_date: date) -> List[date]:
    """获取交易日列表"""
    global _trading_days_cache
    if _trading_days_cache is not None:
        return [d for d in _trading_days_cache if start_date <= d <= end_date]

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT trade_date FROM daily_quotes "
            "WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date",
            (start_date, end_date))
        days = [row[0] for row in cur.fetchall()]
        _trading_days_cache = days
        return days
    finally:
        conn.close()


def get_daily_quotes_batch(ts_codes: List[str], start_date: date,
                            end_date: date) -> Dict[str, pd.DataFrame]:
    """批量获取多只股票的日线数据（一次SQL查询）"""
    if not ts_codes:
        return {}

    cache_key = f"batch_{len(ts_codes)}_{start_date}_{end_date}"
    if cache_key in _cache:
        return _cache[cache_key]

    conn = get_db()
    try:
        cur = conn.cursor()
        # 使用 IN 列表批量查询
        placeholders = ','.join(['%s'] * len(ts_codes))
        cur.execute(f"""
            SELECT ts_code, trade_date, open, high, low, close, 
                   volume, amount, pct_chg, turnover_rate, amplitude,
                   volume_ratio, pe_ratio, pb_ratio, main_force_net
            FROM daily_quotes
            WHERE ts_code IN ({placeholders})
              AND trade_date BETWEEN %s AND %s
            ORDER BY ts_code, trade_date
        """, ts_codes + [start_date, end_date])

        columns = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close',
                   'volume', 'amount', 'pct_chg', 'turnover_rate', 'amplitude',
                   'volume_ratio', 'pe_ratio', 'pb_ratio', 'main_force_net']
        df = pd.DataFrame(cur.fetchall(), columns=columns)

        # 转换类型
        for col in ['open', 'high', 'low', 'close', 'amount', 'pct_chg',
                     'turnover_rate', 'amplitude', 'volume_ratio', 'pe_ratio',
                     'pb_ratio', 'main_force_net']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce')

        # 按股票分组
        result = {}
        for code, group in df.groupby('ts_code'):
            result[code] = group.sort_values('trade_date').reset_index(drop=True)

        _cache[cache_key] = result
        return result
    finally:
        conn.close()


def get_daily_quotes(ts_code: str, start_date: date,
                      end_date: date) -> pd.DataFrame:
    """获取单只股票的日线数据"""
    batch = get_daily_quotes_batch([ts_code], start_date, end_date)
    return batch.get(ts_code, pd.DataFrame())


def get_active_stocks(trade_date: date, min_amount: float = 5e7) -> List[str]:
    """获取当日活跃标的（成交额>阈值）"""
    cache_key = f"active_{trade_date}_{min_amount}"
    if cache_key in _cache:
        return _cache[cache_key]

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT ts_code FROM daily_quotes
            WHERE trade_date = %s AND amount >= %s
            ORDER BY amount DESC
        """, (trade_date, min_amount))
        codes = [row[0] for row in cur.fetchall()]
        _cache[cache_key] = codes
        return codes
    finally:
        conn.close()


def get_market_overview(trade_date: date) -> Dict:
    """获取市场广度（上涨/下跌家数）"""
    cache_key = f"overview_{trade_date}"
    if cache_key in _cache:
        return _cache[cache_key]

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END) as advancers,
                SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END) as decliners,
                COUNT(*) as total
            FROM daily_quotes
            WHERE trade_date = %s
        """, (trade_date,))
        row = cur.fetchone()
        advancers = int(row[0]) if row[0] else 0
        decliners = int(row[1]) if row[1] else 0
        total = int(row[2]) if row[2] else 1
        result = {
            'advancers': advancers,
            'decliners': decliners,
            'breadth_ratio': advancers / total if total > 0 else 0,
        }
        _cache[cache_key] = result
        return result
    finally:
        conn.close()


def get_index_quotes(index_code: str = '000001.SH',
                     start_date: date = None,
                     end_date: date = None) -> pd.DataFrame:
    """获取指数日线数据（从daily_quotes中取上证指数ETF或用市场均值代理）
    
    优化：使用每日平均pct_chg构建指数代理，比AVG(close)快10倍以上。
    """
    cache_key = f"idx_{index_code}_{start_date}_{end_date}"
    if cache_key in _index_cache:
        return _index_cache[cache_key]

    conn = get_db()
    try:
        cur = conn.cursor()
        # 尝试直接获取指数数据
        cur.execute("""
            SELECT trade_date, close FROM daily_quotes
            WHERE ts_code = %s AND trade_date BETWEEN %s AND %s
            ORDER BY trade_date
        """, (index_code, start_date, end_date))
        rows = cur.fetchall()
        if rows:
            df = pd.DataFrame(rows, columns=['trade_date', 'close'])
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            _index_cache[cache_key] = df
            return df

        # 代理：用每日平均pct_chg构建指数代理（比AVG(close)快得多）
        # 先获取基准日的平均close作为起点
        cur.execute("""
            SELECT AVG(close) FROM daily_quotes WHERE trade_date = %s
        """, (start_date,))
        base_close = cur.fetchone()[0]
        if not base_close:
            base_close = 10.0  # fallback

        # 获取每日平均pct_chg
        cur.execute("""
            SELECT trade_date, AVG(pct_chg) as avg_pct
            FROM daily_quotes
            WHERE trade_date BETWEEN %s AND %s
            GROUP BY trade_date
            ORDER BY trade_date
        """, (start_date, end_date))
        rows = cur.fetchall()
        if not rows:
            df = pd.DataFrame(columns=['trade_date', 'close'])
            _index_cache[cache_key] = df
            return df

        df = pd.DataFrame(rows, columns=['trade_date', 'avg_pct'])
        df['avg_pct'] = pd.to_numeric(df['avg_pct'], errors='coerce')
        # 用累计涨跌幅构建代理指数
        df['close'] = base_close * (1 + df['avg_pct'] / 100).cumprod()
        df = df[['trade_date', 'close']]
        _index_cache[cache_key] = df
        return df
    finally:
        conn.close()


def get_fundamental_data(ts_code: str, year: int, quarter: int) -> Dict:
    """获取基本面数据（从daily_quotes中的pe/pb + stock_fundamentals表）"""
    conn = get_db()
    try:
        cur = conn.cursor()
        result = {}

        # 从 daily_quotes 获取 PE/PB
        cur.execute("""
            SELECT pe_ratio, pb_ratio FROM daily_quotes
            WHERE ts_code = %s ORDER BY trade_date DESC LIMIT 1
        """, (ts_code,))
        row = cur.fetchone()
        if row:
            result['pe_ratio'] = float(row[0]) if row[0] else None
            result['pb_ratio'] = float(row[1]) if row[1] else None

        # 从 stock_fundamentals 获取更多数据
        try:
            cur.execute("""
                SELECT debt_ratio, current_ratio, net_margin, roe, revenue_yoy
                FROM stock_fundamentals
                WHERE ts_code = %s
                ORDER BY report_date DESC LIMIT 1
            """, (ts_code,))
            row = cur.fetchone()
            if row:
                result['debt_ratio'] = float(row[0]) if row[0] else None
                result['current_ratio'] = float(row[1]) if row[1] else None
                result['net_margin'] = float(row[2]) if row[2] else None
                result['roe'] = float(row[3]) if row[3] else None
                result['revenue_yoy'] = float(row[4]) if row[4] else None
        except Exception:
            pass

        return result
    finally:
        conn.close()


def get_fundamental_batch(ts_codes: List[str]) -> Dict[str, Dict]:
    """批量获取基本面数据"""
    if not ts_codes:
        return {}

    conn = get_db()
    try:
        cur = conn.cursor()
        placeholders = ','.join(['%s'] * len(ts_codes))
        result = {}

        # PE/PB from daily_quotes
        cur.execute(f"""
            SELECT ts_code, pe_ratio, pb_ratio FROM (
                SELECT ts_code, pe_ratio, pb_ratio,
                       ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) as rn
                FROM daily_quotes
                WHERE ts_code IN ({placeholders})
            ) t WHERE rn = 1
        """, ts_codes)
        for row in cur.fetchall():
            result[row[0]] = {
                'pe_ratio': float(row[1]) if row[1] else None,
                'pb_ratio': float(row[2]) if row[2] else None,
            }

        # Fundamentals from stock_fundamentals
        try:
            cur.execute(f"""
                SELECT ts_code, debt_ratio, current_ratio, net_margin, roe, revenue_yoy
                FROM (
                    SELECT ts_code, debt_ratio, current_ratio, net_margin, roe, revenue_yoy,
                           ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY report_date DESC) as rn
                    FROM stock_fundamentals
                    WHERE ts_code IN ({placeholders})
                ) t WHERE rn = 1
            """, ts_codes)
            for row in cur.fetchall():
                if row[0] not in result:
                    result[row[0]] = {}
                result[row[0]].update({
                    'debt_ratio': float(row[1]) if row[1] else None,
                    'current_ratio': float(row[2]) if row[2] else None,
                    'net_margin': float(row[3]) if row[3] else None,
                    'roe': float(row[4]) if row[4] else None,
                    'revenue_yoy': float(row[5]) if row[5] else None,
                })
        except Exception:
            pass

        return result
    finally:
        conn.close()


def get_daily_quotes_for_date(trade_date: date,
                               min_amount: float = 0) -> pd.DataFrame:
    """获取某一天所有股票的日线数据（一次查询）"""
    cache_key = f"daily_{trade_date}_{min_amount}"
    if cache_key in _cache:
        return _cache[cache_key]

    conn = get_db()
    try:
        cur = conn.cursor()
        sql = """
            SELECT ts_code, open, high, low, close, volume, amount, pct_chg,
                   turnover_rate, amplitude, volume_ratio, pe_ratio, pb_ratio, main_force_net
            FROM daily_quotes
            WHERE trade_date = %s
        """
        params = [trade_date]
        if min_amount > 0:
            sql += " AND amount >= %s"
            params.append(min_amount)

        cur.execute(sql, params)
        columns = ['ts_code', 'open', 'high', 'low', 'close', 'volume', 'amount',
                   'pct_chg', 'turnover_rate', 'amplitude', 'volume_ratio',
                   'pe_ratio', 'pb_ratio', 'main_force_net']
        df = pd.DataFrame(cur.fetchall(), columns=columns)
        for col in columns[1:]:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        _cache[cache_key] = df
        return df
    finally:
        conn.close()


def get_multi_day_quotes(ts_codes: List[str], end_date: date,
                          lookback: int = 120) -> Dict[str, pd.DataFrame]:
    """获取多只股票的多日数据（用于因子计算）"""
    start_date = end_date - timedelta(days=lookback + 30)
    return get_daily_quotes_batch(ts_codes, start_date, end_date)
