"""
5策略共振技术指标过滤模块
==========================
核心策略（必选）：
  1. 20周线保命法（收盘价站上20周均线）
  2. 均线多头排列（5日>10日>20日）
  3. MACD金叉（零轴上方，红柱放大）

增强策略（强烈推荐）：
  4. 布林上轨追涨（刚突破上轨且量比放大）
  5. 年线定海神针（收盘价站上250日均线）

数据源：PostgreSQL daily_quotes 表
依赖：pandas, numpy, psycopg2
"""
from __future__ import annotations

import sys
import os
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from core.db.connection import get_db


class ResonanceFilters:
    """5策略共振过滤器"""

    def __init__(self, db_conn=None, ma_close_tolerance: float = 0.005,
                 macd_expanding_margin: float = 0.9,
                 ma_20week_trend_days: int = 10,
                 macd_min_dif: float = 0.01,
                 ma_20week_max_bias: float = 0.30):
        self.db_conn = db_conn
        self._close_conn = db_conn is None
        self._quote_cache = {}
        self._ma_cache = {}
        self._macd_cache = {}
        self._boll_cache = {}
        self.ma_close_tolerance = ma_close_tolerance
        self.macd_expanding_margin = macd_expanding_margin
        self.ma_20week_trend_days = ma_20week_trend_days
        self.macd_min_dif = macd_min_dif
        self.ma_20week_max_bias = ma_20week_max_bias

    def _get_conn(self):
        if self.db_conn is None:
            return get_db()
        return self.db_conn

    def _load_history(self, ts_code: str, trade_date: date, days: int = 300) -> Optional[pd.DataFrame]:
        """加载历史K线数据（用于计算技术指标）"""
        cache_key = f"{ts_code}_{trade_date}"
        if cache_key in self._quote_cache:
            return self._quote_cache[cache_key]

        start_date = trade_date - timedelta(days=days)

        conn = self._get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT trade_date, open, high, low, close, volume, amount,
                   pct_chg, turnover_rate
            FROM daily_quotes
            WHERE ts_code = %s
              AND trade_date >= %s
              AND trade_date <= %s
            ORDER BY trade_date ASC;
        """, (ts_code, start_date, trade_date))

        rows = cur.fetchall()
        cur.close()

        if not rows:
            return None

        df = pd.DataFrame(rows)
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
        df.set_index('trade_date', inplace=True)

        for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        self._quote_cache[cache_key] = df
        return df

    def _calc_ma(self, df: pd.DataFrame, period: int) -> pd.Series:
        """计算简单移动平均线"""
        return df['close'].rolling(window=period, min_periods=period).mean()

    def _calc_macd(self, df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        """计算MACD指标"""
        ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=signal, adjust=False).mean()
        hist = (dif - dea) * 2
        return pd.DataFrame({'DIF': dif, 'DEA': dea, 'HIST': hist})

    def _calc_bollinger(self, df: pd.DataFrame, period: int = 20, std_dev: int = 2) -> pd.DataFrame:
        """计算布林带指标"""
        middle = df['close'].rolling(window=period, min_periods=period).mean()
        std = df['close'].rolling(window=period, min_periods=period).std()
        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)
        return pd.DataFrame({'UPPER': upper, 'MIDDLE': middle, 'LOWER': lower})

    def check_20week_ma(self, ts_code: str, trade_date: date, min_bias: float = 0.0) -> dict:
        """
        20周线保命法（约100日线）
        条件：收盘价站上20周均线，且均线向上发散
        
        返回：
          {
            'passed': bool,
            'close': float,
            'ma_100': float,
            'bias_pct': float,  # 偏离度百分比
            'ma_trend': str,    # 'up' / 'down' / 'flat'
          }
        """
        df = self._load_history(ts_code, trade_date, days=150)
        if df is None or len(df) < 100:
            return {'passed': False, 'reason': '数据不足100日'}

        close = df['close'].iloc[-1]
        ma_100 = self._calc_ma(df, 100).iloc[-1]

        if pd.isna(ma_100) or ma_100 == 0:
            return {'passed': False, 'reason': 'MA100计算失败'}

        bias_pct = (close - ma_100) / ma_100 * 100

        # 判断20周线趋势（比较今日和 trend_days 日前的MA100）
        trend_days = self.ma_20week_trend_days
        ma_100_past = None
        if len(df) >= 100 + trend_days:
            ma_100_past = self._calc_ma(df, 100).iloc[-trend_days]
        elif len(df) >= 105:
            ma_100_past = self._calc_ma(df, 100).iloc[-5]

        if pd.isna(ma_100_past):
            ma_trend = 'flat'
        elif ma_100 > ma_100_past * 1.001:
            ma_trend = 'up'
        elif ma_100 < ma_100_past * 0.999:
            ma_trend = 'down'
        else:
            ma_trend = 'flat'

        # 条件1: 收盘价>MA100 + 最小偏离度
        above_ma = close > ma_100 and bias_pct >= min_bias
        # 条件2: 偏离度不超过上限（防止追高）
        not_overextended = bias_pct <= self.ma_20week_max_bias * 100
        # 条件3: 趋势向上
        passed = above_ma and not_overextended and (ma_trend == 'up')

        return {
            'passed': passed,
            'close': round(close, 2),
            'ma_100': round(ma_100, 2),
            'bias_pct': round(bias_pct, 2),
            'ma_trend': ma_trend,
            'min_bias': min_bias,
            'max_bias': round(self.ma_20week_max_bias * 100, 2),
        }

    def check_ma_bullish_alignment(self, ts_code: str, trade_date: date) -> dict:
        """
        均线多头排列（5日>10日>20日）
        条件：5日均线 > 10日均线 > 20日均线，且股价在所有均线上方
        
        返回：
          {
            'passed': bool,
            'ma_5': float,
            'ma_10': float,
            'ma_20': float,
            'close': float,
            'alignment': str,  # 'bullish' / 'bearish' / 'mixed'
          }
        """
        df = self._load_history(ts_code, trade_date, days=60)
        if df is None or len(df) < 20:
            return {'passed': False, 'reason': '数据不足20日'}

        close = df['close'].iloc[-1]
        ma_5 = self._calc_ma(df, 5).iloc[-1]
        ma_10 = self._calc_ma(df, 10).iloc[-1]
        ma_20 = self._calc_ma(df, 20).iloc[-1]

        if any(pd.isna(x) for x in [ma_5, ma_10, ma_20]):
            return {'passed': False, 'reason': '均线计算失败'}

        # 判断排列类型
        if ma_5 > ma_10 > ma_20:
            alignment = 'bullish'
        elif ma_5 < ma_10 < ma_20:
            alignment = 'bearish'
        else:
            alignment = 'mixed'

        # 多头排列：均线顺序正确 + 股价在MA5上方（允许小幅回踩容差内）
        passed = (ma_5 > ma_10 > ma_20) and (close >= ma_5 * (1 - self.ma_close_tolerance))

        return {
            'passed': passed,
            'ma_5': round(ma_5, 2),
            'ma_10': round(ma_10, 2),
            'ma_20': round(ma_20, 2),
            'close': round(close, 2),
            'alignment': alignment,
        }

    def check_macd_golden_cross(self, ts_code: str, trade_date: date) -> dict:
        """
        MACD金叉（零轴上方，红柱放大）
        条件：
          1. DIF > 0（零轴上方）
          2. DIF上穿DEA（金叉）或已经金叉
          3. 红柱持续放大（HIST > 0 且递增）
        
        返回：
          {
            'passed': bool,
            'dif': float,
            'dea': float,
            'hist': float,
            'hist_prev': float,
            'is_golden_cross': bool,
            'is_above_zero': bool,
            'is_expanding': bool,
          }
        """
        df = self._load_history(ts_code, trade_date, days=60)
        if df is None or len(df) < 30:
            return {'passed': False, 'reason': '数据不足30日'}

        macd_df = self._calc_macd(df)

        dif = macd_df['DIF'].iloc[-1]
        dea = macd_df['DEA'].iloc[-1]
        hist = macd_df['HIST'].iloc[-1]
        hist_prev = macd_df['HIST'].iloc[-2] if len(macd_df) >= 2 else 0
        dif_prev = macd_df['DIF'].iloc[-2] if len(macd_df) >= 2 else 0
        dea_prev = macd_df['DEA'].iloc[-2] if len(macd_df) >= 2 else 0

        if any(pd.isna(x) for x in [dif, dea, hist]):
            return {'passed': False, 'reason': 'MACD计算失败'}

        # 金叉判断：昨日DIF<=DEA，今日DIF>DEA
        is_golden_cross = (dif_prev <= dea_prev) and (dif > dea)

        # 零轴上方：DIF需有实质动能，不能太接近零轴
        is_above_zero = (dif > self.macd_min_dif) and (dea > 0)

        # 红柱放大：允许小幅缩短容忍空中加油/旗形整理
        is_expanding = (hist > 0) and (hist >= hist_prev * self.macd_expanding_margin)

        # 通过条件：零轴上方 + （金叉或红柱放大）
        passed = is_above_zero and (is_golden_cross or is_expanding)

        return {
            'passed': passed,
            'dif': round(dif, 4),
            'dea': round(dea, 4),
            'hist': round(hist, 4),
            'hist_prev': round(hist_prev, 4),
            'is_golden_cross': is_golden_cross,
            'is_above_zero': is_above_zero,
            'is_expanding': is_expanding,
        }

    def check_bollinger_upper_breakout(self, ts_code: str, trade_date: date) -> dict:
        """
        布林上轨追涨（刚突破上轨且量比放大）
        条件：
          1. 收盘价突破布林上轨
          2. 量比 > 1.5（放量）
          3. 非回踩（昨日收盘价也在上轨附近或上方）
        
        返回：
          {
            'passed': bool,
            'close': float,
            'upper': float,
            'middle': float,
            'lower': float,
            'breakout_pct': float,  # 突破百分比
            'volume_ratio': float,
            'is_breakout': bool,
            'is_pullback': bool,
          }
        """
        df = self._load_history(ts_code, trade_date, days=60)
        if df is None or len(df) < 20:
            return {'passed': False, 'reason': '数据不足20日'}

        boll_df = self._calc_bollinger(df)

        close = df['close'].iloc[-1]
        upper = boll_df['UPPER'].iloc[-1]
        middle = boll_df['MIDDLE'].iloc[-1]
        lower = boll_df['LOWER'].iloc[-1]

        if any(pd.isna(x) for x in [upper, middle, lower]):
            return {'passed': False, 'reason': '布林带计算失败'}

        # 突破百分比
        breakout_pct = (close - upper) / upper * 100 if upper > 0 else 0

        # 量比计算（今日成交量 / 前20日均量）
        if len(df) >= 21:
            today_vol = df['volume'].iloc[-1]
            avg_vol_20 = df['volume'].iloc[-21:-1].mean()
            volume_ratio = today_vol / avg_vol_20 if avg_vol_20 > 0 else 0
        else:
            volume_ratio = 0

        # 判断是否突破
        is_breakout = close > upper

        # 判断是否回踩（昨日收盘价低于上轨）
        close_prev = df['close'].iloc[-2] if len(df) >= 2 else close
        is_pullback = (close_prev < upper) and (close > upper)

        # 通过条件：突破上轨 + 量比>1.5 + 非回踩
        passed = is_breakout and (volume_ratio > 1.5) and not is_pullback

        return {
            'passed': passed,
            'close': round(close, 2),
            'upper': round(upper, 2),
            'middle': round(middle, 2),
            'lower': round(lower, 2),
            'breakout_pct': round(breakout_pct, 2),
            'volume_ratio': round(volume_ratio, 2),
            'is_breakout': is_breakout,
            'is_pullback': is_pullback,
        }

    def check_annual_line(self, ts_code: str, trade_date: date) -> dict:
        """
        年线定海神针（250日均线）
        条件：收盘价站上250日均线
        
        返回：
          {
            'passed': bool,
            'close': float,
            'ma_250': float,
            'bias_pct': float,
          }
        """
        df = self._load_history(ts_code, trade_date, days=300)
        if df is None or len(df) < 250:
            return {'passed': False, 'reason': '数据不足250日'}

        close = df['close'].iloc[-1]
        ma_250 = self._calc_ma(df, 250).iloc[-1]

        if pd.isna(ma_250) or ma_250 == 0:
            return {'passed': False, 'reason': 'MA250计算失败'}

        bias_pct = (close - ma_250) / ma_250 * 100

        passed = close > ma_250

        return {
            'passed': passed,
            'close': round(close, 2),
            'ma_250': round(ma_250, 2),
            'bias_pct': round(bias_pct, 2),
        }

    def check_all_filters(self, ts_code: str, trade_date: date, 
                          enable_annual_line: bool = True,
                          enable_bollinger: bool = True,
                          ma_min_bias: float = 0.0) -> dict:
        """
        一次性检查所有5策略共振过滤
        
        参数：
          ts_code: 股票代码（如 600519.SH）
          trade_date: 交易日期
          enable_annual_line: 是否启用年线过滤（默认启用）
          enable_bollinger: 是否启用布林带过滤（默认启用）
        
        返回：
          {
            'ts_code': str,
            'trade_date': date,
            'filters': {
              'ma_20week': dict,
              'ma_bullish': dict,
              'macd': dict,
              'bollinger': dict,  # 可选
              'annual_line': dict,  # 可选
            },
            'passed_core': bool,  # 核心3策略是否全部通过
            'passed_all': bool,   # 所有策略是否全部通过
            'passed_count': int,  # 通过的策略数量
            'total_count': int,   # 总策略数量
          }
        """
        # 核心3策略（必选）
        ma_20week = self.check_20week_ma(ts_code, trade_date, min_bias=ma_min_bias)
        ma_bullish = self.check_ma_bullish_alignment(ts_code, trade_date)
        macd = self.check_macd_golden_cross(ts_code, trade_date)

        filters = {
            'ma_20week': ma_20week,
            'ma_bullish': ma_bullish,
            'macd': macd,
        }

        # 增强2策略（可选）
        if enable_bollinger:
            filters['bollinger'] = self.check_bollinger_upper_breakout(ts_code, trade_date)

        if enable_annual_line:
            filters['annual_line'] = self.check_annual_line(ts_code, trade_date)

        # 统计通过情况
        core_passed = (
            ma_20week.get('passed', False) and
            ma_bullish.get('passed', False) and
            macd.get('passed', False)
        )

        all_passed_list = [core_passed]
        if enable_bollinger:
            all_passed_list.append(filters['bollinger'].get('passed', False))
        if enable_annual_line:
            all_passed_list.append(filters['annual_line'].get('passed', False))

        passed_all = all(all_passed_list)
        passed_count = sum(1 for f in filters.values() if f.get('passed', False))
        total_count = len(filters)

        return {
            'ts_code': ts_code,
            'trade_date': trade_date,
            'filters': filters,
            'passed_core': core_passed,
            'passed_all': passed_all,
            'passed_count': passed_count,
            'total_count': total_count,
        }

    def filter_stock_list(self, stock_list: list, trade_date: date,
                          min_pass_count: int = 3,
                          require_core: bool = True,
                          enable_annual_line: bool = True,
                          enable_bollinger: bool = True,
                          ma_min_bias: float = 0.0,
                          max_pe: float = 80.0,
                          max_pb: float = 8.0,
                          verbose: bool = True) -> list:
        """
        批量过滤股票列表

        参数：
          stock_list: 股票代码列表
          trade_date: 交易日期
          min_pass_count: 最少通过的策略数量
          require_core: 是否要求核心3策略必须全部通过
          enable_annual_line: 是否启用年线过滤
          enable_bollinger: 是否启用布林带过滤
          ma_min_bias: 20周线最小偏离度百分比
          max_pe: PE上限（0=不限制）
          max_pb: PB上限（0=不限制）
          verbose: 是否打印详细日志

        返回：
          通过过滤的股票列表（包含详细结果）
        """
        if verbose:
            print(f"\n{'='*70}")
            print(f"  5策略共振过滤")
            print(f"  待筛选: {len(stock_list)} 只")
            if max_pe > 0 or max_pb > 0:
                pe_str = f"PE≤{max_pe}" if max_pe > 0 else ""
                pb_str = f"PB≤{max_pb}" if max_pb > 0 else ""
                sep = " + " if max_pe > 0 and max_pb > 0 else ""
                print(f"  估值预筛: {pe_str}{sep}{pb_str}")
            print(f"  核心策略: 20周线 + 均线多头 + MACD金叉")
            if enable_bollinger:
                print(f"  增强策略: 布林上轨")
            if enable_annual_line:
                print(f"  增强策略: 年线")
            print(f"  最少通过: {min_pass_count}/{3 + int(enable_bollinger) + int(enable_annual_line)}")
            print(f"{'='*70}")

        # P2: 估值预筛（加载 PE/PB 并提前剔除高估股票）
        if max_pe > 0 or max_pb > 0:
            filtered_list = []
            pe_reject = 0
            pb_reject = 0
            try:
                conn = self._get_conn()
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("""
                    SELECT ts_code, pe_ratio, pb_ratio
                    FROM daily_quotes
                    WHERE trade_date = %s AND ts_code = ANY(%s);
                """, (trade_date, stock_list))
                val_map = {}
                for r in cur.fetchall():
                    val_map[r['ts_code']] = {
                        'pe_ratio': float(r['pe_ratio']) if r['pe_ratio'] else None,
                        'pb_ratio': float(r['pb_ratio']) if r['pb_ratio'] else None,
                    }
                cur.close()
                for ts_code in stock_list:
                    val = val_map.get(ts_code, {})
                    pe = val.get('pe_ratio')
                    pb = val.get('pb_ratio')
                    if max_pe > 0 and pe is not None and pe > max_pe:
                        pe_reject += 1
                        continue
                    if max_pb > 0 and pb is not None and pb > max_pb:
                        pb_reject += 1
                        continue
                    filtered_list.append(ts_code)
                if verbose and (pe_reject > 0 or pb_reject > 0):
                    print(f"  ⚡ 估值预筛淘汰: PE>{max_pe} ({pe_reject}只) + PB>{max_pb} ({pb_reject}只)")
                stock_list = filtered_list
            except Exception:
                pass

        results = []
        passed_stocks = []

        for i, ts_code in enumerate(stock_list, 1):
            if verbose and i % 50 == 0:
                print(f"  进度: {i}/{len(stock_list)}")

            result = self.check_all_filters(
                ts_code, trade_date,
                enable_annual_line=enable_annual_line,
                enable_bollinger=enable_bollinger,
                ma_min_bias=ma_min_bias,
            )

            results.append(result)

            # 判断是否通过
            if require_core and not result['passed_core']:
                continue

            if result['passed_count'] < min_pass_count:
                continue

            passed_stocks.append(result)

        if verbose:
            print(f"\n  筛选结果: {len(passed_stocks)}/{len(stock_list)} 只通过")
            print(f"{'='*70}\n")

        return passed_stocks

    def close(self):
        """关闭数据库连接"""
        if self._close_conn and self.db_conn is not None:
            try:
                self.db_conn.close()
            except Exception:
                pass


def run_resonance_filter(trade_date: date = None,
                         stock_list: list = None,
                         min_pass_count: int = 3,
                         require_core: bool = True,
                         enable_annual_line: bool = True,
                         enable_bollinger: bool = True,
                         ma_min_bias: float = 0.0,
                         max_pe: float = 80.0,
                         max_pb: float = 8.0,
                         verbose: bool = True) -> list:
    """
    便捷函数：直接运行5策略共振过滤

    参数：
      trade_date: 交易日期（默认最新交易日）
      stock_list: 股票代码列表（默认从数据库读取全市场）
      min_pass_count: 最少通过的策略数量
      require_core: 是否要求核心3策略必须全部通过
      enable_annual_line: 是否启用年线过滤
      enable_bollinger: 是否启用布林带过滤
      ma_min_bias: 20周线最小偏离度百分比
      max_pe: PE上限（0=不限制）
      max_pb: PB上限（0=不限制）
      verbose: 是否打印详细日志

    返回：
      通过过滤的股票列表
    """
    if trade_date is None:
        conn = None
        try:
            conn = get_db()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT MAX(trade_date) as max_date FROM daily_quotes;")
            row = cur.fetchone()
            trade_date = row['max_date'] if row else date.today()
            cur.close()
        finally:
            if conn and not conn.closed:
                conn.close()

    if stock_list is None:
        conn = None
        try:
            conn = get_db()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT DISTINCT ts_code
                FROM daily_quotes
                WHERE trade_date = %s
                  AND amount > 1e8
                ORDER BY ts_code;
            """, (trade_date,))
            stock_list = [row['ts_code'] for row in cur.fetchall()]
            cur.close()
        finally:
            if conn and not conn.closed:
                conn.close()

    filters = ResonanceFilters()
    try:
        results = filters.filter_stock_list(
            stock_list, trade_date,
            min_pass_count=min_pass_count,
            require_core=require_core,
            enable_annual_line=enable_annual_line,
            enable_bollinger=enable_bollinger,
            ma_min_bias=ma_min_bias,
            max_pe=max_pe,
            max_pb=max_pb,
            verbose=verbose
        )
        return results
    finally:
        filters.close()


if __name__ == "__main__":
    import argparse
    from datetime import datetime

    parser = argparse.ArgumentParser(description="5策略共振技术指标过滤")
    parser.add_argument("--date", "-d", type=str, default=None,
                        help="交易日期（YYYY-MM-DD），默认最新交易日")
    parser.add_argument("--min-pass", "-m", type=int, default=3,
                        help="最少通过的策略数量（默认3）")
    parser.add_argument("--no-core", action="store_true",
                        help="不要求核心3策略必须全部通过")
    parser.add_argument("--no-annual", action="store_true",
                        help="禁用年线过滤")
    parser.add_argument("--no-bollinger", action="store_true",
                        help="禁用布林带过滤")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="输出CSV文件路径")
    args = parser.parse_args()

    trade_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None

    results = run_resonance_filter(
        trade_date=trade_date,
        min_pass_count=args.min_pass,
        require_core=not args.no_core,
        enable_annual_line=not args.no_annual,
        enable_bollinger=not args.no_bollinger,
        verbose=True
    )

    if results:
        print(f"\n✅ 通过共振过滤的股票: {len(results)} 只\n")
        for r in results:
            print(f"  {r['ts_code']}")
            print(f"    通过策略: {r['passed_count']}/{r['total_count']}")
            if 'ma_20week' in r['filters']:
                f = r['filters']['ma_20week']
                print(f"    20周线: {'✅' if f.get('passed') else '✗'} (MA100={f.get('ma_100')}, 偏离={f.get('bias_pct')}%)")
            if 'ma_bullish' in r['filters']:
                f = r['filters']['ma_bullish']
                print(f"    均线多头: {'✅' if f.get('passed') else '✗'} (MA5={f.get('ma_5')}, MA10={f.get('ma_10')}, MA20={f.get('ma_20')})")
            if 'macd' in r['filters']:
                f = r['filters']['macd']
                print(f"    MACD: {'✅' if f.get('passed') else '✗'} (DIF={f.get('dif')}, DEA={f.get('dea')}, HIST={f.get('hist')})")
            if 'bollinger' in r['filters']:
                f = r['filters']['bollinger']
                print(f"    布林上轨: {'✅' if f.get('passed') else '✗'} (突破={f.get('breakout_pct')}%, 量比={f.get('volume_ratio')})")
            if 'annual_line' in r['filters']:
                f = r['filters']['annual_line']
                print(f"    年线: {'✅' if f.get('passed') else '✗'} (MA250={f.get('ma_250')}, 偏离={f.get('bias_pct')}%)")
            print()

        if args.output:
            import csv
            with open(args.output, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(['ts_code', 'passed_count', 'total_count', 'passed_core', 'passed_all'])
                for r in results:
                    writer.writerow([
                        r['ts_code'],
                        r['passed_count'],
                        r['total_count'],
                        r['passed_core'],
                        r['passed_all'],
                    ])
            print(f"📄 结果已保存到: {args.output}")
    else:
        print("\n❌ 没有股票通过共振过滤")
