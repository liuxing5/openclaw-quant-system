"""
Layer A: 选股池预筛（周频）
=============================
基本面筛选作为入池条件：
  1. 业绩加速：归母净利润同比 > +30%，且二阶导为正
  2. 市值适中：流通市值 50-200 亿
  3. 行业景气：申万二级行业近 20 日涨幅排名全市场前 30%
  4. 股权激励/回购：近 6 个月有相关公告
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import List, Set, Optional, Dict

import pandas as pd
import numpy as np

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db_fresh
from psycopg2.extras import RealDictCursor
from zoneinfo import ZoneInfo

from .config import MainUptrendConfig
from .data_loader import DataLoader

logger = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


class LayerAPrescreener:
    """A 层：基本面选股池预筛"""

    def __init__(self, cfg: MainUptrendConfig,
                 loader: Optional[DataLoader] = None):
        self.cfg = cfg
        self.loader = loader or DataLoader()
        self._pool_cache: Dict[str, Set[str]] = {}
        # A1 业绩加速缓存：键=报告期（ISO 季度），值=该报告期口径下通过的股票集合
        self._profit_cache: Dict[str, Set[str]] = {}

    @staticmethod
    def _report_period_key(as_of_date: str) -> str:
        """eval_date 所属报告期（ISO 季度）。

        stock_fundamentals.report_date 为季末日期（如 2025-09-30），
        `report_date <= eval_date` 的结果集只在季度切换时变化，
        故以季度做缓存键既 PIT 正确又避免逐日查库。
        """
        y, m = int(as_of_date[:4]), int(as_of_date[5:7])
        return f"{y}Q{(m - 1) // 3 + 1}"

    def _pool_cache_key(self, as_of_date: str) -> str:
        """A 层池缓存键。

        2026-10-14 审查 26：旧实现按自然月复用（as_of_date[:7]），月内所有交易日
        共用第一份结果，与"逐日 PIT 预筛"语义不符。默认改为 ISO 周；
        cfg.a_pool_cache_mode = "daily" 时按日（不做跨日复用）。
        """
        if getattr(self.cfg, "a_pool_cache_mode", "iso_week") == "daily":
            return as_of_date
        iso = datetime.strptime(as_of_date, "%Y-%m-%d").isocalendar()
        return f"{iso[0]}-W{int(iso[1]):02d}"

    def prescreen(self, as_of_date: Optional[str] = None) -> Set[str]:
        """
        返回通过 A 层筛选的股票代码集合

        as_of_date: 基准日期 (YYYY-MM-DD)，None 则为最新交易日
        """
        if as_of_date is None:
            as_of_date = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

        # 审查 26：缓存键由"自然月"改为 ISO 周（或按日），见 _pool_cache_key
        cache_key = self._pool_cache_key(as_of_date)
        if cache_key in self._pool_cache:
            return self._pool_cache[cache_key]

        codes: Set[str] = set()

        codes_1 = self._filter_profit_acceleration(as_of_date)
        if codes_1:
            codes.update(codes_1)
            logger.info(f"A1 业绩加速: {len(codes_1)} 只通过")

        codes_2 = self._filter_market_cap(as_of_date)
        if codes_2:
            codes.update(codes_2)
            logger.info(f"A2 市值适中: {len(codes_2)} 只通过")

        codes_3 = self._filter_industry_momentum(as_of_date)
        if codes_3:
            codes.update(codes_3)
            logger.info(f"A3 行业景气: {len(codes_3)} 只通过")

        # 合并逻辑：根据 a_use_union 配置决定取交集还是并集
        active_filters = [f for f in [codes_1, codes_2, codes_3] if f is not None and len(f) > 0]

        if not active_filters:
            codes = set()
        elif len(active_filters) == 1:
            codes = active_filters[0]
        elif self.cfg.a_use_union:
            # 并集模式：通过任一filter即可入池（适合趋势股，放宽入池条件）
            codes = active_filters[0]
            for f in active_filters[1:]:
                codes = codes | f
            logger.info(f"A 层并集模式: {len(codes)} 只入池")
        else:
            # 交集模式：必须通过所有filter
            codes = active_filters[0]
            for f in active_filters[1:]:
                codes = codes & f

        # 如果交集为空但有filter返回了数据，降级为取最大的filter结果
        if not codes and active_filters:
            codes = max(active_filters, key=len)
            logger.info(f"A 层交集为空，降级使用最大filter结果: {len(codes)} 只")

        self._pool_cache[cache_key] = codes
        logger.info(f"A 层预筛完成: {len(codes)} 只入池")
        return codes

    # ================================================================
    # A1: 业绩加速
    # ================================================================
    def _filter_profit_acceleration(self, as_of_date: str) -> Set[str]:
        """A1 业绩加速（Point-in-Time）。

        2026-10-14 审查 4：
          - 旧 SQL 无 report_date 上界，回测早期会用到未来才披露的财报；
          - 旧缓存是整个回测期唯一一份（`self._profit_cache: Set`），首个评估日
            的结果被后续所有日期复用。
        现改为 `report_date <= eval_date` 的 PIT 判定，缓存键含报告期（ISO 季度）。
        """
        period_key = self._report_period_key(as_of_date)
        if period_key in self._profit_cache:
            return self._profit_cache[period_key]

        conn = None
        codes = set()
        try:
            conn = get_db_fresh()
            cur = conn.cursor()
            cur.execute("SET statement_timeout = '300000'")
            cur.close()
            cur = conn.cursor(cursor_factory=RealDictCursor)

            # 使用 stock_fundamentals 表，筛选 profit_yoy > 阈值
            # 审查 4：report_date <= eval_date（PIT），并保留原有现金流质量条件
            cur.execute("""
                SELECT DISTINCT ts_code
                FROM stock_fundamentals
                WHERE profit_yoy IS NOT NULL
                  AND profit_yoy > %s
                  AND report_date <= %s
                  AND report_date >= '2025-01-01'
                  AND net_profit IS NOT NULL
                  AND net_profit > 0
                  AND operating_cashflow IS NOT NULL
                  AND operating_cashflow >= net_profit
            """, (self.cfg.a_profit_growth_min * 100, as_of_date))
            for row in cur.fetchall():
                codes.add(row['ts_code'])
            cur.close()
        except Exception as e:
            logger.warning(f"A1 业绩加速查询失败: {e}")
        finally:
            if conn and not conn.closed:
                conn.close()
        self._profit_cache[period_key] = codes
        logger.info(f"A1 业绩加速: 报告期 {period_key} ({as_of_date} 口径) {len(codes)} 只")
        return codes

    # ================================================================
    # A2: 市值适中
    # ================================================================
    def _filter_market_cap(self, as_of_date: str) -> Set[str]:
        # 优先从预加载数据获取
        if self.loader._preloaded_daily is not None and not self.loader._preloaded_daily.empty:
            return self._filter_market_cap_preloaded(as_of_date)

        conn = None
        codes = set()
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)

            cur.execute("""
                SELECT DISTINCT ts_code
                FROM daily_quotes
                WHERE trade_date = (
                    SELECT MAX(trade_date) FROM daily_quotes
                    WHERE trade_date <= %s
                )
                  AND amount IS NOT NULL
                  AND volume IS NOT NULL
                  AND close IS NOT NULL
            """, (as_of_date,))
            rows = cur.fetchall()
            cur.close()

            for row in rows:
                code = row['ts_code']
                market_cap = self._estimate_market_cap(conn, code, as_of_date)
                if market_cap and self.cfg.a_market_cap_min <= market_cap <= self.cfg.a_market_cap_max:
                    codes.add(code)
        except Exception as e:
            logger.warning(f"A2 市值查询失败: {e}")
        finally:
            if conn and not conn.closed:
                conn.close()
        return codes

    def _filter_market_cap_preloaded(self, as_of_date: str) -> Set[str]:
        """从预加载数据批量估算市值（向量化，无iterrows）"""
        df = self.loader._preloaded_daily
        day_data = df[df['trade_date'] == as_of_date]
        if day_data.empty:
            return set()

        # 向量化计算市值
        turnover_rate = pd.to_numeric(day_data['turnover_rate'], errors='coerce')
        # 审查 6：预加载数据已前复权，市值估算必须用原始收盘价（raw_close），
        # 复权价是为序列连续性缩放的历史价，会系统性低估除权前的市值。
        if 'raw_close' in day_data.columns:
            close = pd.to_numeric(day_data['raw_close'], errors='coerce')
        else:
            close = pd.to_numeric(day_data['close'], errors='coerce')
        volume = pd.to_numeric(day_data['volume'], errors='coerce')

        # 过滤有效数据
        valid = (turnover_rate > 0) & close.notna() & volume.notna()
        valid_data = day_data[valid].copy()

        if valid_data.empty:
            return set()

        # 2026-10-19 A2 口径修复: turnover_rate 存库为百分数(0.51 = 0.51%)、
        # volume 为股 → 流通市值 = close × volume × 100 / turnover_rate(%)。
        # 原式漏乘 100 系统性低估 100×，使 [30e8, 500e8] 实际筛出 ≥3000 亿
        # 超大盘（2026-03-20 与 2026-10-13 各 5 票实证 e1 = 真实市值/100；
        # funnel layer2 同口径一直为 amount*100/turnover_rate，见
        # tests/test_funnel_layers.py——两策略自此一致）。
        est_mcap = close[valid] * volume[valid] * 100.0 / turnover_rate[valid]
        in_range = (est_mcap >= self.cfg.a_market_cap_min) & (est_mcap <= self.cfg.a_market_cap_max)
        return set(valid_data.loc[in_range, 'ts_code'].tolist())

    def _estimate_market_cap(self, conn, ts_code: str,
                             as_of_date: str) -> Optional[float]:
        """估算流通市值（收盘价 × 流通股本）"""
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                -- 2026-10-19 A2 口径修复: ×100（turnover 为百分数），原式漏乘
                -- 系统性低估 100×（详见 _filter_market_cap_preloaded 注释）
                SELECT close * volume * 100 / turnover_rate AS est_mcap
                FROM daily_quotes
                WHERE ts_code = %s
                  AND trade_date = (
                      SELECT MAX(trade_date) FROM daily_quotes
                      WHERE ts_code = %s AND trade_date <= %s
                  )
                  AND turnover_rate IS NOT NULL
                  AND turnover_rate > 0
                  AND volume IS NOT NULL
                  AND close IS NOT NULL
            """, (ts_code, ts_code, as_of_date))
            row = cur.fetchone()
            cur.close()
            if row and row['est_mcap']:
                return float(row['est_mcap'])
        except Exception:
            pass
        return None

    # ================================================================
    # A3: 行业景气
    # ================================================================
    def _filter_industry_momentum(self, as_of_date: str) -> Set[str]:
        # 优先从预加载数据获取
        if self.loader._preloaded_daily is not None and not self.loader._preloaded_daily.empty:
            return self._filter_industry_momentum_preloaded(as_of_date)

        conn = None
        codes = set()
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)

            start_date = (
                datetime.strptime(as_of_date, "%Y-%m-%d") -
                timedelta(days=self.cfg.a_industry_momentum_days * 2)
            ).strftime("%Y-%m-%d")

            # 优化：只查询有行业数据的股票，减少JOIN开销
            cur.execute("""
                SELECT d.ts_code, sf.industry, d.trade_date, d.pct_chg
                FROM daily_quotes d
                INNER JOIN (
                    SELECT DISTINCT ON (ts_code) ts_code, industry
                    FROM stock_fundamentals
                    WHERE industry IS NOT NULL
                    ORDER BY ts_code, report_date DESC
                ) sf ON d.ts_code = sf.ts_code
                WHERE d.trade_date BETWEEN %s AND %s
                  AND d.pct_chg IS NOT NULL
                ORDER BY sf.industry, d.ts_code, d.trade_date
            """, (start_date, as_of_date))
            rows = cur.fetchall()
            cur.close()

            if not rows:
                return codes

            df = pd.DataFrame(rows)
            recent = df[df['trade_date'] > (
                datetime.strptime(as_of_date, "%Y-%m-%d") -
                timedelta(days=self.cfg.a_industry_momentum_days)
            ).strftime("%Y-%m-%d")]

            industry_returns = recent.groupby('industry')['pct_chg'].mean().sort_values(ascending=False)
            threshold_idx = max(1, int(len(industry_returns) * self.cfg.a_industry_momentum_top_pct))
            top_industries = set(industry_returns.head(threshold_idx).index)

            for code, grp in df.groupby('ts_code'):
                ind = grp['industry'].iloc[-1] if len(grp) > 0 else None
                if ind and ind in top_industries:
                    codes.add(code)
        except Exception as e:
            logger.warning(f"A3 行业景气查询失败: {e}")
        finally:
            if conn and not conn.closed:
                conn.close()
        return codes

    def _filter_industry_momentum_preloaded(self, as_of_date: str) -> Set[str]:
        """从预加载数据批量计算行业景气（向量化，无iterrows）"""
        df = self.loader._preloaded_daily
        if 'industry' not in df.columns:
            return set()

        start_date = (
            datetime.strptime(as_of_date, "%Y-%m-%d") -
            timedelta(days=self.cfg.a_industry_momentum_days * 2)
        ).strftime("%Y-%m-%d")

        mask = (df['trade_date'] >= start_date) & (df['trade_date'] <= as_of_date) & \
               df['industry'].notna() & df['pct_chg'].notna()
        sub = df[mask]
        if sub.empty:
            return set()

        recent_start = (
            datetime.strptime(as_of_date, "%Y-%m-%d") -
            timedelta(days=self.cfg.a_industry_momentum_days)
        ).strftime("%Y-%m-%d")
        recent = sub[sub['trade_date'] > recent_start]

        if recent.empty:
            return set()

        industry_returns = recent.assign(pct_chg=recent['pct_chg'].astype(float)).groupby('industry')['pct_chg'].mean().sort_values(ascending=False)
        threshold_idx = max(1, int(len(industry_returns) * self.cfg.a_industry_momentum_top_pct))
        top_industries = set(industry_returns.head(threshold_idx).index)

        # 向量化：找出属于景气行业的股票
        day_data = sub[sub['trade_date'] == as_of_date]
        in_top = day_data['industry'].isin(top_industries)
        return set(day_data.loc[in_top, 'ts_code'].tolist())