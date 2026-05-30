"""
数据访问层
===========
v4: 极致优化回测速度
- 预加载时按股票分组构建索引字典，O(1)查找
- 预加载包含历史数据（B层需要60日均线）
- 一次性预计算所有B/C/D层技术指标（向量化groupby+rolling）
- 按日期构建指标快照索引，每日扫描变为简单DataFrame过滤
- 收盘价查找表直接用dict，无pandas开销
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Tuple

import numpy as np
import pandas as pd
from psycopg2.extras import RealDictCursor

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db_fresh

from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


class DataLoader:
    """从 Supabase daily_quotes 加载行情数据"""

    @staticmethod
    def normalize_ts_code(ts_code: str) -> str:
        if ts_code.startswith('sh.'):
            return ts_code[3:] + '.SH'
        if ts_code.startswith('sz.'):
            return ts_code[3:] + '.SZ'
        if ts_code.startswith('bj.'):
            return ts_code[3:] + '.BJ'
        return ts_code

    def __init__(self, cache_dir: Optional[str] = None):
        self._cache: Dict[str, pd.DataFrame] = {}
        # 批量预加载存储
        self._preloaded_daily: Optional[pd.DataFrame] = None
        self._preloaded_by_code: Optional[Dict[str, pd.DataFrame]] = None  # {ts_code: DataFrame}
        self._preloaded_close_map: Optional[Dict[str, Dict[str, float]]] = None
        self._preloaded_main_flow: Optional[pd.DataFrame] = None
        self._preloaded_main_flow_by_code: Optional[Dict[str, pd.DataFrame]] = None
        # 快照索引：{trade_date: DataFrame}
        self._snapshot_cache: Dict[str, pd.DataFrame] = {}
        # 预计算指标：{trade_date: DataFrame}（含所有B/C/D层指标）
        self._indicators_df: Optional[pd.DataFrame] = None
        self._indicators_by_date: Dict[str, pd.DataFrame] = {}

    # ----------------------------------------------------------
    # 批量预加载（回测专用）
    # ----------------------------------------------------------
    def preload_for_backtest(self, start_date: str, end_date: str):
        """一次性加载回测区间全量日线数据到内存，并构建索引

        注意：调用方应负责扩展start_date以包含足够历史数据（B层需要60日均线等）
        """
        logger.info(f"预加载回测数据 {start_date} ~ {end_date} ...")

        all_rows = []
        industry_map = {}
        name_map = {}

        try:
            conn = get_db_fresh()

            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SET statement_timeout = '300000'")
            cur.execute("SELECT ts_code, industry FROM stock_fundamentals WHERE industry IS NOT NULL")
            for row in cur.fetchall():
                if row['ts_code'] not in industry_map:
                    industry_map[row['ts_code']] = row['industry']
            cur.close()

            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SET statement_timeout = '300000'")
            cur.execute("SELECT ts_code, stock_name FROM stock_basic_info")
            for row in cur.fetchall():
                name_map[row['ts_code']] = row['stock_name']
            cur.close()

            from datetime import datetime as _dt, timedelta as _td
            from dateutil.relativedelta import relativedelta
            sd = _dt.strptime(start_date, "%Y-%m-%d")
            ed = _dt.strptime(end_date, "%Y-%m-%d")
            batch_start_dt = sd
            batch_num = 0
            total_batches = max(1, (ed - sd).days // 7)
            while batch_start_dt < ed:
                batch_end_dt = min(batch_start_dt + _td(days=7), ed)
                batch_num += 1
                bs = batch_start_dt.strftime("%Y-%m-%d")
                be = batch_end_dt.strftime("%Y-%m-%d")

                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("SET statement_timeout = '300000'")
                cur.execute("""
                    SELECT ts_code, trade_date, open, high, low, close,
                           volume, amount, pct_chg, turnover_rate,
                           main_force_net AS main_net_inflow
                    FROM daily_quotes
                    WHERE trade_date >= %s
                      AND trade_date < %s
                      AND amount IS NOT NULL
                    ORDER BY ts_code, trade_date
                """, (bs, be))
                batch_rows = cur.fetchall()
                all_rows.extend(batch_rows)
                cur.close()
                if batch_num % 10 == 0 or batch_num == total_batches:
                    logger.info(f"  已加载 {batch_num} 周, 累计 {len(all_rows)} 行")
                batch_start_dt = batch_end_dt

            conn.close()

            if all_rows:
                df = pd.DataFrame(all_rows)
                df['trade_date'] = df['trade_date'].astype(str)
                df['industry'] = df['ts_code'].map(industry_map)
                df['name'] = df['ts_code'].map(name_map)
                self._preloaded_daily = df
                n_stocks = df['ts_code'].nunique()
                logger.info(f"  预加载日线: {len(df)} 条, {n_stocks} 只股票")

                logger.info("  构建股票索引...")
                self._preloaded_by_code = {
                    code: grp.reset_index(drop=True)
                    for code, grp in df.groupby('ts_code', sort=False)
                }

                logger.info("  构建收盘价查找表...")
                self._preloaded_close_map = {}
                for code, grp in self._preloaded_by_code.items():
                    self._preloaded_close_map[code] = dict(
                        zip(grp['trade_date'], grp['close'].astype(float))
                    )

                logger.info("  构建快照索引...")
                self._snapshot_cache = {
                    date_val: grp.reset_index(drop=True)
                    for date_val, grp in df.groupby('trade_date', sort=False)
                }

                logger.info("  索引构建完成")
            else:
                self._preloaded_daily = pd.DataFrame()
                self._preloaded_by_code = {}
                self._preloaded_close_map = {}

            if all_rows:
                df_flow = self._preloaded_daily[self._preloaded_daily['main_net_inflow'].notna()].copy()
                if not df_flow.empty:
                    self._preloaded_main_flow = df_flow[['ts_code', 'trade_date', 'main_net_inflow']]
                    self._preloaded_main_flow_by_code = {}
                    for code, grp in self._preloaded_main_flow.groupby('ts_code'):
                        self._preloaded_main_flow_by_code[code] = grp.reset_index(drop=True)
                    logger.info(f"  主力资金: {len(self._preloaded_main_flow)} 条")
                else:
                    self._preloaded_main_flow = None
                    self._preloaded_main_flow_by_code = None
            else:
                self._preloaded_main_flow = None
                self._preloaded_main_flow_by_code = None

        except Exception as e:
            logger.error(f"预加载失败: {e}")
            self._preloaded_daily = pd.DataFrame()
            self._preloaded_by_code = {}
            self._preloaded_close_map = {}

        self._precompute_indicators()

    # ----------------------------------------------------------
    # 预计算技术指标（核心优化：纯numpy rolling替代groupby+lambda）
    # ----------------------------------------------------------
    def _precompute_indicators(self):
        """一次性向量化计算所有B/C/D层技术指标

        核心优化：
        1. 用 _fast_rolling 替代 groupby+transform(lambda: rolling)
           - groupby+transform+lambda 每个group调用Python函数，极慢
           - _fast_rolling 用纯numpy按group边界切片计算，快10-50x
        2. 行业统计用 map 替代 merge，避免大表join
        3. _indicators_by_date 用 groupby indices 避免复制DataFrame
        """
        if self._preloaded_daily is None or self._preloaded_daily.empty:
            return

        import time
        t0 = time.time()
        logger.info("  预计算技术指标...")

        df = self._preloaded_daily

        # 确保按股票和日期排序
        df.sort_values(['ts_code', 'trade_date'], inplace=True)
        df.reset_index(drop=True, inplace=True)

        # 数值类型转换（一次性，用astype比pd.to_numeric快）
        for col in ['close', 'high', 'low', 'volume', 'amount', 'pct_chg', 'turnover_rate', 'main_net_inflow']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # ---- 预计算group边界（一次性，避免重复groupby） ----
        t1 = time.time()
        logger.info("    计算group边界...")
        ts_codes = df['ts_code'].values
        # 找到每只股票的起止行号
        code_change = np.empty(len(ts_codes), dtype=bool)
        code_change[0] = True
        code_change[1:] = ts_codes[1:] != ts_codes[:-1]
        group_starts = np.where(code_change)[0]
        group_ends = np.append(group_starts[1:], len(ts_codes))
        logger.info(f"    group边界: {len(group_starts)} 只股票, 耗时{time.time()-t1:.1f}s")

        # ---- B层指标 ----
        t1 = time.time()
        logger.info("    B1: 量能指标...")
        amount_arr = df['amount'].values.astype(np.float64)
        vol_ma_60 = self._fast_rolling_mean(amount_arr, group_starts, group_ends, 60)
        df['vol_ma_60'] = vol_ma_60
        df['vol_breakout_ratio'] = amount_arr / np.where(vol_ma_60 != 0, vol_ma_60, np.nan)

        logger.info("    B2: 价格突破指标...")
        high_arr = df['high'].values.astype(np.float64)
        low_arr = df['low'].values.astype(np.float64)
        close_arr = df['close'].values.astype(np.float64)

        box_60_high = self._fast_rolling_max_shifted(high_arr, group_starts, group_ends, 60)
        box_60_low = self._fast_rolling_min_shifted(low_arr, group_starts, group_ends, 60)
        ma_120 = self._fast_rolling_mean(close_arr, group_starts, group_ends, 120)
        high_52w = self._fast_rolling_max_shifted(high_arr, group_starts, group_ends, 250)  # 52周高点
        df['box_60_high'] = box_60_high
        df['box_60_low'] = box_60_low
        df['ma_120'] = ma_120
        df['high_52w'] = high_52w
        df['above_ma_120_pct'] = (close_arr - ma_120) / np.where(ma_120 != 0, ma_120, np.nan)

        # MA120斜率（用于B2趋势确认）- 比较当前MA120与5日前MA120
        ma_120_shifted = np.roll(ma_120, 5)
        for g_start in group_starts:
            ma_120_shifted[g_start:g_start + 5] = np.nan
        ma_120_slope = np.where(ma_120_shifted != 0, (ma_120 - ma_120_shifted) / ma_120_shifted, np.nan)
        df['ma_120_slope'] = ma_120_slope

        # 5日收益率（用于D6接飞刀过滤）- 向量化计算
        close_shifted = np.roll(close_arr, 5)
        # 处理跨股票边界：将每个股票组前5个位置设为NaN
        for g_start in group_starts:
            close_shifted[g_start:g_start + 5] = np.nan
        pct_chg_5d = np.where(close_shifted != 0, (close_arr - close_shifted) / close_shifted, np.nan)
        df['pct_chg_5d'] = pct_chg_5d

        logger.info("    B3: 主力资金指标...")
        if self._preloaded_main_flow is not None:
            flow = self._preloaded_main_flow[['ts_code', 'trade_date', 'main_net_inflow']].drop_duplicates(
                subset=['ts_code', 'trade_date']
            )
            # 用map替代merge：构建查找字典
            flow_map = {}
            for _, row in flow.iterrows():
                val = row['main_net_inflow']
                flow_map[(row['ts_code'], row['trade_date'])] = float(val) if val is not None else np.nan
            df['main_net_inflow'] = [
                flow_map.get((tc, td), np.nan)
                for tc, td in zip(df['ts_code'].values, df['trade_date'].values)
            ]
        else:
            df['main_net_inflow'] = np.nan
        df['main_inflow_ratio'] = df['main_net_inflow'].values / np.where(amount_arr != 0, amount_arr, np.nan)

        # ---- C层指标 ----
        logger.info("    C层指标...")
        amount_ma_20 = self._fast_rolling_mean_shifted(amount_arr, group_starts, group_ends, 20)
        df['amount_ma_20'] = amount_ma_20
        df['amount_ratio_20'] = amount_arr / np.where(amount_ma_20 != 0, amount_ma_20, np.nan)

        volume_arr = df['volume'].values.astype(np.float64)
        prev_volume = np.empty_like(volume_arr)
        prev_volume[0] = np.nan
        prev_volume[1:] = volume_arr[:-1]
        # 修复group边界：每只股票第一行的prev_volume应为nan
        prev_volume[group_starts] = np.nan
        df['prev_volume'] = prev_volume
        df['volume_shrink_ratio'] = volume_arr / np.where(prev_volume != 0, prev_volume, np.nan)

        # ---- D层指标 ----
        logger.info("    D层指标...")
        volume_ma_20 = self._fast_rolling_mean_shifted(volume_arr, group_starts, group_ends, 20)
        df['volume_ma_20'] = volume_ma_20
        df['volume_ratio_20'] = volume_arr / np.where(volume_ma_20 != 0, volume_ma_20, np.nan)
        df['seal_quality_est'] = amount_arr / np.where(vol_ma_60 != 0, vol_ma_60, np.nan) * 0.001

        # D5: 20日涨幅（用于追高风险过滤）
        close_20 = self._fast_rolling_mean_shifted(close_arr, group_starts, group_ends, 20)
        df['pct_chg_20d'] = (close_arr - close_20) / np.where(close_20 != 0, close_20, np.nan) * 100

        # ---- Layer E: 趋势持续型指标 ----
        logger.info("    E层趋势指标...")
        ma_5 = self._fast_rolling_mean(close_arr, group_starts, group_ends, 5)
        ma_10 = self._fast_rolling_mean(close_arr, group_starts, group_ends, 10)
        ma_20 = self._fast_rolling_mean(close_arr, group_starts, group_ends, 20)
        df['ma_5'] = ma_5
        df['ma_10'] = ma_10
        df['ma_20'] = ma_20

        df['ma_alignment'] = (ma_5 > ma_10) & (ma_10 > ma_20) & (ma_20 > ma_120)

        ma_alignment_days = np.zeros(len(df), dtype=np.float64)
        align_vals = df['ma_alignment'].values.astype(float)
        for i in range(len(group_starts)):
            s, e = group_starts[i], group_ends[i]
            chunk = align_vals[s:e]
            n = len(chunk)
            if n == 0:
                continue
            result = np.zeros(n, dtype=np.float64)
            result[0] = 1 if chunk[0] else 0
            for j in range(1, n):
                if chunk[j]:
                    result[j] = result[j-1] + 1
                else:
                    result[j] = 0
            ma_alignment_days[s:e] = result
        df['ma_alignment_days'] = ma_alignment_days

        vol_ma_5 = self._fast_rolling_mean(volume_arr, group_starts, group_ends, 5)
        vol_ma_20_e = self._fast_rolling_mean(volume_arr, group_starts, group_ends, 20)
        df['vol_ma_5'] = vol_ma_5
        df['vol_ma_20'] = vol_ma_20_e

        max_dd_20d = np.zeros(len(df), dtype=np.float64)
        for i in range(len(group_starts)):
            s, e = group_starts[i], group_ends[i]
            chunk = close_arr[s:e]
            n = len(chunk)
            if n < 20:
                continue
            for j in range(20, n):
                window = chunk[j-20:j]
                high_val = np.max(window)
                low_val = np.min(window)
                if high_val > 0:
                    max_dd_20d[s + j] = (low_val - high_val) / high_val
        df['max_drawdown_20d'] = max_dd_20d

        above_ma20_days = np.zeros(len(df), dtype=np.float64)
        above_mask = (close_arr > ma_20) & ~np.isnan(ma_20)
        for i in range(len(group_starts)):
            s, e = group_starts[i], group_ends[i]
            chunk = above_mask[s:e].astype(float)
            n = len(chunk)
            if n == 0:
                continue
            result = np.zeros(n, dtype=np.float64)
            result[0] = 1 if chunk[0] else 0
            for j in range(1, n):
                if chunk[j]:
                    result[j] = result[j-1] + 1
                else:
                    result[j] = 0
            above_ma20_days[s:e] = result
        df['above_ma20_days'] = above_ma20_days

        close_10 = self._fast_rolling_mean_shifted(close_arr, group_starts, group_ends, 10)
        df['pct_chg_10d'] = (close_arr - close_10) / np.where(close_10 != 0, close_10, np.nan) * 100

        close_60 = self._fast_rolling_mean_shifted(close_arr, group_starts, group_ends, 60)
        df['pct_chg_60d'] = (close_arr - close_60) / np.where(close_60 != 0, close_60, np.nan) * 100

        logger.info("    E层 RSI/ADX...")
        rsi_14 = np.full(len(df), 50.0, dtype=np.float64)
        adx_14 = np.full(len(df), 0.0, dtype=np.float64)
        for i in range(len(group_starts)):
            s, e = group_starts[i], group_ends[i]
            cc = close_arr[s:e]
            ch = high_arr[s:e]
            cl = low_arr[s:e]
            n = len(cc)
            if n < 30:
                continue
            try:
                delta = np.empty(n, dtype=np.float64)
                delta[0] = 0
                delta[1:] = cc[1:] - cc[:-1]
                gain = np.where(delta > 0, delta, 0.0)
                loss = np.where(delta < 0, -delta, 0.0)
                avg_gain = np.full(n, np.nan, dtype=np.float64)
                avg_loss = np.full(n, np.nan, dtype=np.float64)
                if n > 14:
                    avg_gain[14] = np.mean(gain[1:15])
                    avg_loss[14] = np.mean(loss[1:15])
                    for j in range(15, n):
                        avg_gain[j] = (avg_gain[j-1] * 13 + gain[j]) / 14
                        avg_loss[j] = (avg_loss[j-1] * 13 + loss[j]) / 14
                    rs = np.where(avg_loss != 0, avg_gain / avg_loss, np.nan)
                    rsi_vals = np.where(np.isnan(rs), 50.0, 100 - 100 / (1 + rs))
                    rsi_vals[:14] = 50.0
                    rsi_14[s:e] = rsi_vals

                tr = np.empty(n, dtype=np.float64)
                tr[0] = ch[0] - cl[0]
                tr[1:] = np.maximum(
                    ch[1:] - cl[1:],
                    np.maximum(
                        np.abs(ch[1:] - cc[:-1]),
                        np.abs(cl[1:] - cc[:-1])
                    )
                )
                plus_dm_raw = np.empty(n, dtype=np.float64)
                minus_dm_raw = np.empty(n, dtype=np.float64)
                plus_dm_raw[0] = 0
                minus_dm_raw[0] = 0
                hdiff = np.empty(n, dtype=np.float64)
                ldiff = np.empty(n, dtype=np.float64)
                hdiff[0] = 0
                ldiff[0] = 0
                hdiff[1:] = ch[1:] - ch[:-1]
                ldiff[1:] = cl[:-1] - cl[1:]
                plus_dm_raw = np.where((hdiff > ldiff) & (hdiff > 0), hdiff, 0.0)
                minus_dm_raw = np.where((ldiff > hdiff) & (ldiff > 0), ldiff, 0.0)

                if n > 14:
                    atr_vals = np.full(n, np.nan, dtype=np.float64)
                    atr_vals[14] = np.mean(tr[1:15])
                    for j in range(15, n):
                        atr_vals[j] = (atr_vals[j-1] * 13 + tr[j]) / 14

                    smooth_pdm = np.full(n, np.nan, dtype=np.float64)
                    smooth_mdm = np.full(n, np.nan, dtype=np.float64)
                    smooth_pdm[14] = np.mean(plus_dm_raw[1:15])
                    smooth_mdm[14] = np.mean(minus_dm_raw[1:15])
                    for j in range(15, n):
                        smooth_pdm[j] = (smooth_pdm[j-1] * 13 + plus_dm_raw[j]) / 14
                        smooth_mdm[j] = (smooth_mdm[j-1] * 13 + minus_dm_raw[j]) / 14

                    plus_di = np.where(atr_vals != 0, 100 * smooth_pdm / atr_vals, 0.0)
                    minus_di = np.where(atr_vals != 0, 100 * smooth_mdm / atr_vals, 0.0)
                    dx = np.where(
                        (plus_di + minus_di) != 0,
                        100 * np.abs(plus_di - minus_di) / (plus_di + minus_di),
                        0.0
                    )
                    adx_vals = np.full(n, 0.0, dtype=np.float64)
                    if n > 28:
                        adx_vals[28] = np.mean(dx[15:29])
                        for j in range(29, n):
                            adx_vals[j] = (adx_vals[j-1] * 13 + dx[j]) / 14
                    adx_14[s:e] = adx_vals
            except Exception:
                pass
        df['rsi_14'] = rsi_14
        df['adx_14'] = adx_14

        # ---- 蓄势指标（主升浪前兆） ----
        logger.info("    蓄势指标...")
        ma_60_e = self._fast_rolling_mean(close_arr, group_starts, group_ends, 60)

        consolidation_days = np.zeros(len(df), dtype=np.float64)
        volume_drying = np.zeros(len(df), dtype=np.float64)
        price_squeeze = np.zeros(len(df), dtype=np.float64)
        ma_converging = np.zeros(len(df), dtype=np.float64)
        breakout_signal = np.zeros(len(df), dtype=np.float64)

        for i in range(len(group_starts)):
            s, e = group_starts[i], group_ends[i]
            cc = close_arr[s:e]
            vv = volume_arr[s:e]
            n = len(cc)
            if n < 30:
                continue

            m5 = ma_5[s:e]
            m10 = ma_10[s:e]
            m20 = ma_20[s:e]
            m60 = ma_60_e[s:e]
            vm5 = vol_ma_5[s:e]
            vm20 = vol_ma_20_e[s:e]
            pct = pct_chg_arr[s:e] if 'pct_chg_arr' in dir() else np.zeros(n)

            for j in range(20, n):
                if np.isnan(m20[j]) or np.isnan(m60[j]):
                    continue

                above_ma20 = cc[j] > m20[j]
                near_ma20 = above_ma20 and (cc[j] - m20[j]) / m20[j] < 0.05
                ma20_up = j > 0 and m20[j] > m20[j-1]
                vol_shrink = vm5[j] < vm20[j] * 0.8 if vm20[j] > 0 else False
                narrow_range = (m5[j] - m20[j]) / m20[j] < 0.03 if m20[j] > 0 else False

                if near_ma20 and ma20_up:
                    consolidation_days[s + j] = consolidation_days[s + j - 1] + 1 if j > 0 else 1
                else:
                    consolidation_days[s + j] = 0

                if vol_shrink:
                    volume_drying[s + j] = volume_drying[s + j - 1] + 1 if j > 0 else 1
                else:
                    volume_drying[s + j] = 0

                if narrow_range and above_ma20:
                    price_squeeze[s + j] = price_squeeze[s + j - 1] + 1 if j > 0 else 1
                else:
                    price_squeeze[s + j] = 0

                if m5[j] > m20[j] and m20[j] > m60[j]:
                    spread = (m5[j] - m60[j]) / m60[j] if m60[j] > 0 else 0
                    if spread < 0.08:
                        ma_converging[s + j] = 1.0 - spread / 0.08

                # 放量突破：蓄势后放量站上MA20
                if above_ma20 and vm20[j] > 0:
                    vol_ratio = vm5[j] / vm20[j]
                    price_above = (cc[j] - m20[j]) / m20[j]
                    had_consolidation = consolidation_days[s + j - 1] >= 3 if j > 0 else False
                    had_drying = volume_drying[s + j - 1] >= 2 if j > 0 else False
                    if (vol_ratio > 1.5 and price_above > 0.01 and price_above < 0.05
                            and (had_consolidation or had_drying)):
                        breakout_signal[s + j] = min(1.0, vol_ratio / 3.0)

        df['consolidation_days'] = consolidation_days
        df['volume_drying_days'] = volume_drying
        df['price_squeeze_days'] = price_squeeze
        df['ma_converging_score'] = ma_converging
        df['breakout_score'] = breakout_signal

        # ---- 辅助列 ----
        df['is_kcb_cyb'] = df['ts_code'].str.startswith(('300', '688'))

        # ---- C5: 行业统计（用pandas groupby + map替代merge） ----
        logger.info("    行业统计...")
        if 'industry' in df.columns and df['industry'].notna().any():
            # groupby计算行业统计（比Python循环快）
            ind_grp = df.groupby(['trade_date', 'industry'], sort=False)
            industry_stats = ind_grp.agg(
                industry_avg_pct=('pct_chg', 'mean'),
                industry_rising_count=('pct_chg', lambda x: (x.dropna() > 0).sum())
            )
            # 构建map用于快速查找
            industry_avg_map = industry_stats['industry_avg_pct'].to_dict()
            industry_rising_map = industry_stats['industry_rising_count'].to_dict()

            # 用map填充列（比merge快得多）
            trade_date_vals = df['trade_date'].values
            industry_vals = df['industry'].values
            df['industry_avg_pct'] = [
                industry_avg_map.get((trade_date_vals[i], industry_vals[i]), np.nan)
                for i in range(len(df))
            ]
            df['industry_rising_count'] = [
                industry_rising_map.get((trade_date_vals[i], industry_vals[i]), 0)
                for i in range(len(df))
            ]
        else:
            df['industry_avg_pct'] = np.nan
            df['industry_rising_count'] = 0

        # 存储指标数据
        self._indicators_df = df

        # 按日期构建索引（需要先按日期排序）
        t3 = time.time()
        logger.info("    构建日期索引...")
        
        # 调试：打印amount数据分布
        if 'amount' in df.columns:
            amt = df['amount'].dropna()
            if len(amt) > 0:
                logger.info(f"    [DEBUG] amount统计: min={amt.min()}, max={amt.max()}, mean={amt.mean():.0f}, median={amt.median():.0f}")
                logger.info(f"    [DEBUG] amount >= 1e8 的股票数: {(amt >= 1e8).sum()}/{len(amt)}")
                logger.info(f"    [DEBUG] amount >= 1e7 的股票数: {(amt >= 1e7).sum()}/{len(amt)}")
                logger.info(f"    [DEBUG] pct_chg >= 3 的股票数: {(df['pct_chg'].abs() >= 3).sum()}/{len(df)}")
        
        # 修复：按日期排序后构建索引
        df_by_date = df.sort_values(['trade_date', 'ts_code']).reset_index(drop=True)
        self._indicators_by_date = {}
        date_vals = df_by_date['trade_date'].values
        date_change = np.empty(len(date_vals), dtype=bool)
        date_change[0] = True
        date_change[1:] = date_vals[1:] != date_vals[:-1]
        date_starts = np.where(date_change)[0]
        date_ends = np.append(date_starts[1:], len(date_vals))
        for i in range(len(date_starts)):
            d = date_vals[date_starts[i]]
            self._indicators_by_date[d] = df_by_date.iloc[date_starts[i]:date_ends[i]]
        logger.info(f"    日期索引: {len(self._indicators_by_date)} 天, 耗时{time.time()-t3:.1f}s")

        t2 = time.time()
        n_indicators = len([c for c in df.columns if c not in
                            ['ts_code', 'trade_date', 'open', 'close', 'high', 'low',
                             'volume', 'amount', 'pct_chg', 'turnover_rate', 'industry', 'name']])
        logger.info(f"  预计算完成: {n_indicators} 个指标列, {len(df)} 条记录, {len(self._indicators_by_date)} 个交易日, 耗时{t2-t0:.1f}s")

    # ----------------------------------------------------------
    # 快速rolling计算（纯numpy，避免groupby+transform+lambda）
    # ----------------------------------------------------------
    @staticmethod
    def _fast_rolling_mean(arr: np.ndarray, group_starts: np.ndarray,
                           group_ends: np.ndarray, window: int) -> np.ndarray:
        """按group边界计算rolling mean，纯numpy实现"""
        result = np.full_like(arr, np.nan, dtype=np.float64)
        for i in range(len(group_starts)):
            s, e = group_starts[i], group_ends[i]
            chunk = arr[s:e]
            n = len(chunk)
            if n < window:
                continue
            # cumsum trick for fast rolling mean
            cs = np.empty(n + 1, dtype=np.float64)
            cs[0] = 0
            np.cumsum(chunk, out=cs[1:])
            result[s + window - 1:e] = (cs[window:] - cs[:n - window + 1]) / window
        return result

    @staticmethod
    def _fast_rolling_mean_shifted(arr: np.ndarray, group_starts: np.ndarray,
                                    group_ends: np.ndarray, window: int) -> np.ndarray:
        """rolling mean + shift(1)，即T-1日的均值"""
        result = np.full_like(arr, np.nan, dtype=np.float64)
        for i in range(len(group_starts)):
            s, e = group_starts[i], group_ends[i]
            chunk = arr[s:e]
            n = len(chunk)
            if n < window + 1:
                continue
            cs = np.empty(n + 1, dtype=np.float64)
            cs[0] = 0
            np.cumsum(chunk, out=cs[1:])
            # rolling mean of [0..window-1] goes to position window (shifted by 1)
            rolling_vals = (cs[window:] - cs[:n - window + 1]) / window
            result[s + window:e] = rolling_vals[:n - window]
        return result

    @staticmethod
    def _fast_rolling_max_shifted(arr: np.ndarray, group_starts: np.ndarray,
                                   group_ends: np.ndarray, window: int) -> np.ndarray:
        """rolling max + shift(1)"""
        result = np.full_like(arr, np.nan, dtype=np.float64)
        for i in range(len(group_starts)):
            s, e = group_starts[i], group_ends[i]
            chunk = arr[s:e]
            n = len(chunk)
            if n < window + 1:
                continue
            # 使用sliding_window_view（numpy 1.20+）
            from numpy.lib.stride_tricks import sliding_window_view
            windows = sliding_window_view(chunk, window)
            max_vals = np.max(windows, axis=1)
            result[s + window:e] = max_vals[:n - window]
        return result

    @staticmethod
    def _fast_rolling_min_shifted(arr: np.ndarray, group_starts: np.ndarray,
                                   group_ends: np.ndarray, window: int) -> np.ndarray:
        """rolling min + shift(1)"""
        result = np.full_like(arr, np.nan, dtype=np.float64)
        for i in range(len(group_starts)):
            s, e = group_starts[i], group_ends[i]
            chunk = arr[s:e]
            n = len(chunk)
            if n < window + 1:
                continue
            from numpy.lib.stride_tricks import sliding_window_view
            windows = sliding_window_view(chunk, window)
            min_vals = np.min(windows, axis=1)
            result[s + window:e] = min_vals[:n - window]
        return result

    def get_indicators_snapshot(self, trade_date: str) -> pd.DataFrame:
        """获取某日的预计算指标快照"""
        snap = self._indicators_by_date.get(trade_date, pd.DataFrame())
        # 调试：首次调用打印快照信息
        if not snap.empty and not hasattr(self, '_snap_debug_done'):
            logger.info(f"[DEBUG] 快照 {trade_date}: {len(snap)}行, columns={list(snap.columns)}")
            logger.info(f"[DEBUG] 快照 amount: min={snap['amount'].min()}, max={snap['amount'].max()}, nan={snap['amount'].isna().sum()}")
            logger.info(f"[DEBUG] 快照 pct_chg: min={snap['pct_chg'].min()}, max={snap['pct_chg'].max()}")
            self._snap_debug_done = True
        return snap

    def get_preloaded_close(self, ts_code: str, trade_date: str) -> Optional[float]:
        ts_code = self.normalize_ts_code(ts_code)
        if self._preloaded_close_map and ts_code in self._preloaded_close_map:
            return self._preloaded_close_map[ts_code].get(trade_date)
        return None

    def get_preloaded_daily(self, ts_code: str, start_date: str = "2020-01-01",
                            end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        ts_code = self.normalize_ts_code(ts_code)
        if self._preloaded_by_code is None or ts_code not in self._preloaded_by_code:
            return None
        df = self._preloaded_by_code[ts_code]
        mask = pd.Series(True, index=df.index)
        if start_date:
            mask = mask & (df['trade_date'] >= start_date)
        if end_date:
            mask = mask & (df['trade_date'] <= end_date)
        filtered = df[mask]
        if len(filtered) == 0:
            return None
        return filtered.reset_index(drop=True)

    def get_preloaded_snapshot(self, trade_date: str, min_amount: float = 1e8) -> pd.DataFrame:
        """从预加载数据获取全市场快照 - O(1)查找"""
        if trade_date in self._snapshot_cache:
            snap = self._snapshot_cache[trade_date]
            if min_amount > 0:
                snap = snap[snap['amount'] > min_amount]
            return snap[snap['pct_chg'].notna()].reset_index(drop=True) if not snap.empty else pd.DataFrame()
        return pd.DataFrame()

    def get_preloaded_main_flow(self, ts_code: str, start_date: str = "2025-01-01",
                                end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        ts_code = self.normalize_ts_code(ts_code)
        if self._preloaded_main_flow_by_code is None or ts_code not in self._preloaded_main_flow_by_code:
            return None
        df = self._preloaded_main_flow_by_code[ts_code]
        mask = pd.Series(True, index=df.index)
        if start_date:
            mask = mask & (df['trade_date'] >= start_date)
        if end_date:
            mask = mask & (df['trade_date'] <= end_date)
        filtered = df[mask]
        return filtered if not filtered.empty else None

    # ----------------------------------------------------------
    # 日线行情（带预加载优先）
    # ----------------------------------------------------------
    def get_daily(self, ts_code: str, start_date: str = "2020-01-01",
                  end_date: Optional[str] = None,
                  min_days: int = 100) -> Optional[pd.DataFrame]:
        ts_code = self.normalize_ts_code(ts_code)
        # 优先使用预加载数据
        if self._preloaded_by_code is not None and ts_code in self._preloaded_by_code:
            df = self.get_preloaded_daily(ts_code, start_date, end_date)
            if df is not None and len(df) > 0:
                return df

        # 使用宽范围缓存
        cache_key = f"{ts_code}:daily_full"
        if cache_key in self._cache:
            full_df = self._cache[cache_key]
            mask = pd.Series(True, index=full_df.index)
            if start_date:
                mask = mask & (full_df['trade_date'].astype(str) >= start_date)
            if end_date:
                mask = mask & (full_df['trade_date'].astype(str) <= end_date)
            filtered = full_df[mask].reset_index(drop=True)
            if len(filtered) > 0:
                return filtered

        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT trade_date, open, high, low, close, volume, amount,
                       pct_chg, turnover_rate
                FROM daily_quotes
                WHERE ts_code = %s
                  AND trade_date >= '2020-01-01'
                ORDER BY trade_date
            """, (ts_code,))
            rows = cur.fetchall()
            cur.close()
            if not rows or len(rows) < min(min_days, 60):
                return None
            full_df = pd.DataFrame(rows)
            full_df = full_df.sort_values("trade_date").reset_index(drop=True)
            self._cache[cache_key] = full_df

            mask = pd.Series(True, index=full_df.index)
            if start_date:
                mask = mask & (full_df['trade_date'].astype(str) >= start_date)
            if end_date:
                mask = mask & (full_df['trade_date'].astype(str) <= end_date)
            filtered = full_df[mask].reset_index(drop=True)
            return filtered if len(filtered) > 0 else None
        except Exception as e:
            logger.error(f"加载 {ts_code} 日线失败: {e}")
            return None
        finally:
            if conn and not conn.closed:
                conn.close()

    def get_market_breadth(self, trade_date: str) -> dict:
        """计算市场宽度指标：MA5/MA20上方占比、5日动量等"""
        ind_df = self.get_indicators_snapshot(trade_date)
        if ind_df.empty:
            return {'breadth_ma20': 0.5, 'breadth_ma5': 0.5, 'avg_pct_chg': 0.0, 'avg_pct_chg_5d': 0.0, 'stock_count': 0}
        close = ind_df.get('close', pd.Series(dtype=float))
        ma5 = ind_df.get('ma_5', pd.Series(dtype=float))
        ma20 = ind_df.get('ma_20', pd.Series(dtype=float))
        pct_chg = ind_df.get('pct_chg', pd.Series(dtype=float))
        pct_chg_5d = ind_df.get('pct_chg_5d', pd.Series(dtype=float))
        valid20 = close.notna() & ma20.notna() & (ma20 > 0)
        valid5 = close.notna() & ma5.notna() & (ma5 > 0)
        above_ma20 = float((close[valid20] > ma20[valid20]).mean()) if valid20.sum() > 0 else 0.5
        above_ma5 = float((close[valid5] > ma5[valid5]).mean()) if valid5.sum() > 0 else 0.5
        avg_chg = float(pct_chg.dropna().mean()) / 100.0 if pct_chg.notna().any() else 0.0
        avg_chg_5d = float(pct_chg_5d.dropna().mean()) if pct_chg_5d is not None and pct_chg_5d.notna().any() else 0.0
        return {
            'breadth_ma20': above_ma20,
            'breadth_ma5': above_ma5,
            'avg_pct_chg': avg_chg,
            'avg_pct_chg_5d': avg_chg_5d,
            'stock_count': int(valid20.sum()),
        }

    # ----------------------------------------------------------
    # 全市场日线快照（某日的所有标的）
    # ----------------------------------------------------------
    def get_market_snapshot(self, trade_date: str,
                            min_amount: float = 1e8) -> pd.DataFrame:
        # 优先使用预加载快照
        if self._snapshot_cache:
            snap = self.get_preloaded_snapshot(trade_date, min_amount)
            if not snap.empty:
                return snap

        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT ts_code, trade_date, open, high, low, close, volume, amount,
                       pct_chg, turnover_rate
                FROM daily_quotes
                WHERE trade_date = %s
                  AND amount > %s
                  AND pct_chg IS NOT NULL
                ORDER BY amount DESC
            """, (trade_date, min_amount))
            rows = cur.fetchall()
            cur.close()
            return pd.DataFrame(rows) if rows else pd.DataFrame()
        except Exception as e:
            logger.error(f"加载 {trade_date} 全市场快照失败: {e}")
            return pd.DataFrame()
        finally:
            if conn and not conn.closed:
                conn.close()

    # ----------------------------------------------------------
    # 分时数据（1分钟线）— 回测模式下不使用
    # ----------------------------------------------------------
    def get_1min_kline(self, ts_code: str, trade_date: str) -> Optional[pd.DataFrame]:
        return None  # 回测模式直接跳过

    # ----------------------------------------------------------
    # 主力资金流（带预加载优先）
    # ----------------------------------------------------------
    def get_main_force_flow(self, ts_code: str,
                            start_date: str = "2025-01-01",
                            end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        ts_code = self.normalize_ts_code(ts_code)
        if self._preloaded_main_flow_by_code is not None:
            df = self.get_preloaded_main_flow(ts_code, start_date, end_date)
            if df is not None:
                return df

        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            if end_date:
                cur.execute("""
                    SELECT trade_date, main_net_inflow
                    FROM daily_quotes
                    WHERE ts_code = %s
                      AND trade_date >= %s
                      AND trade_date <= %s
                      AND main_net_inflow IS NOT NULL
                    ORDER BY trade_date
                """, (ts_code, start_date, end_date))
            else:
                cur.execute("""
                    SELECT trade_date, main_net_inflow
                    FROM daily_quotes
                    WHERE ts_code = %s
                      AND trade_date >= %s
                      AND main_net_inflow IS NOT NULL
                    ORDER BY trade_date
                """, (ts_code, start_date))
            rows = cur.fetchall()
            cur.close()
            if not rows:
                return None
            return pd.DataFrame(rows)
        except Exception:
            return None
        finally:
            if conn and not conn.closed:
                conn.close()

    # ----------------------------------------------------------
    # 交易日历
    # ----------------------------------------------------------
    def get_trading_days(self, start_date: str, end_date: str) -> List[str]:
        if self._snapshot_cache:
            days = sorted(self._snapshot_cache.keys())
            return [d for d in days if start_date <= d <= end_date]

        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT DISTINCT trade_date::text as date
                FROM daily_quotes
                WHERE trade_date >= %s
                  AND trade_date <= %s
                ORDER BY date
            """, (start_date, end_date))
            rows = cur.fetchall()
            cur.close()
            return [r['date'] for r in rows]
        except Exception:
            return []
        finally:
            if conn and not conn.closed:
                conn.close()

    # ----------------------------------------------------------
    # 清除缓存
    # ----------------------------------------------------------
    def clear_cache(self):
        self._cache.clear()
