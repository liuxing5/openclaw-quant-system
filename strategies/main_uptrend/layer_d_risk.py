"""
Layer D: 风险过滤
===================
提前发现不等于盲目追入，必须过滤掉以下风险：
  1. ST（按日 PIT，见 shared.st_history）、退市风险警示股
  2. 近 30 日内有重大减持公告（按 eval_date 前 30 日窗口判定）
  3. 诱多型涨停：量比 > 5 但放量强度 < 3（审查 15：旧称"封单比"）
  4. 大股东质押比例 > 50% 的票，连板 > 3 后剔除

v2: 极致优化回测速度
- 一次性预加载全量风险数据（减持/质押/退市名称），避免每日查DB
- ST 改为按日 is_st_mask 查询（全模块复用同一连接，见 _shared_conn）
- 向量化诱多检测，使用预计算指标替代逐只股票get_daily+创建LayerB实例
- filter_list_vectorized: 批量DataFrame过滤

数据局限（回测 JSON limitations 同步披露）：
- 大股东质押比例仅有当前快照（financial_data.pledge_ratio），无 PIT 历史，
  D4 存在未来函数；退市风险警示仅有当前名称快照，同样非 PIT。
- ST 走 stock_st_history 区间表（PIT），表缺失时回退当前快照并 warning。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional, Set, Dict

import numpy as np
import pandas as pd

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db
from psycopg2.extras import RealDictCursor
from zoneinfo import ZoneInfo

from .config import MainUptrendConfig
from .data_loader import DataLoader
from .indicators import limit_up_mask
from ..shared.board_rules import is_limit_up as board_is_limit_up
from ..shared.st_history import is_st_mask

logger = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _to_date(d) -> date:
    """str/date → datetime.date（is_st_mask 只接受 date）"""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


@dataclass
class RiskVerdict:
    """D 层单只股票的风险判定"""
    ts_code: str
    eval_date: str
    passed: bool = True
    blacklist_reasons: List[str] = field(default_factory=list)


class LayerDRiskFilter:
    """D 层：风险过滤"""

    def __init__(self, cfg: MainUptrendConfig,
                 loader: Optional[DataLoader] = None):
        self.cfg = cfg
        self.loader = loader or DataLoader()
        # ---- 全量风险数据缓存（回测期间只加载一次）----
        # 审查 13：ST 不再用"当前名称全历史集合"（未来函数），改为按日
        # shared.st_history.is_st_mask（PIT），故不再有 _st_cache；
        # 保留 _delist_cache 承接 d_exclude_delist_warning（无 PIT 数据源，见局限）。
        self._delist_cache: Optional[Set[str]] = None
        # 审查 3：减持缓存改为 {ts_code: [pub_date, ...]}，判定时按
        # [eval_date - d_share_reduction_days, eval_date] 窗口过滤，
        # 旧版"整个回测区间出现过减持就每天剔除"同时存在未来函数与窗口错误。
        self._reduction_cache: Optional[Dict[str, List[str]]] = None
        self._pledge_cache: Optional[Set[str]] = None
        # D 层专用连接（审查 13/31：ST 与所有批量风险查询复用同一连接，
        # statement_timeout 只在连接建立后 SET 一次，不再每个 cursor 重复 SET）
        self._shared_conn = None
        # 回测模式标记
        self._backtest_mode: bool = False

    def _conn(self):
        """返回 D 层共享连接（会话级缓存，.close() 为 no-op，不占连接池名额）"""
        if self._shared_conn is None or self._shared_conn.closed:
            conn = get_db()
            cur = conn.cursor()
            try:
                cur.execute("SET statement_timeout = '300000'")  # 审查 31：统一 SET 一次
            finally:
                cur.close()
            self._shared_conn = conn
        return self._shared_conn

    def preload_for_backtest(self, start_date: str, end_date: str):
        """回测开始前一次性加载全量风险数据，避免每日重复查DB"""
        self._backtest_mode = True
        logger.info("D层: 预加载全量风险数据...")

        # D1: 退市风险警示（名称快照，非 PIT；ST 由 is_st_mask 按日查询）
        if self.cfg.d_exclude_delist_warning and self._delist_cache is None:
            self._delist_cache = self._batch_load_delist()
            logger.info(f"  退市风险警示(名称快照): {len(self._delist_cache)} 只")
        # D1: ST 按日 is_st_mask（stock_st_history 区间表，PIT），
        # 表存在性与回退逻辑由 shared.st_history 内部处理并 warning 一次
        if self.cfg.d_exclude_st:
            logger.info("  ST: 按日 is_st_mask（stock_st_history 区间表，PIT）")

        # D2: 减持（覆盖整个回测区间+30天，按公告日缓存，判定时再开窗口）
        start_dt = datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=self.cfg.d_share_reduction_days)
        self._reduction_cache = self._batch_load_reductions_range(
            start_dt.strftime("%Y-%m-%d"), end_date
        )
        logger.info(f"  减持: {len(self._reduction_cache)} 只（含公告日，逐日开窗判定）")

        # D4: 高质押
        if self._pledge_cache is None:
            self._pledge_cache = self._batch_load_high_pledge()
            logger.info(f"  高质押: {len(self._pledge_cache)} 只（当前快照，非 PIT）")

        logger.info("D层: 风险数据预加载完成")

    def preload_for_date(self, eval_date: str):
        """单日预加载（实盘模式用）"""
        if self.cfg.d_exclude_delist_warning and self._delist_cache is None:
            self._delist_cache = self._batch_load_delist()
        if self._reduction_cache is None:
            self._reduction_cache = self._batch_load_reductions(eval_date)
        if self._pledge_cache is None:
            self._pledge_cache = self._batch_load_high_pledge()

    def filter_list(self, ts_codes: List[str], eval_date: str,
                    consecutive_limits_map: Optional[dict] = None) -> List[str]:
        """
        批量过滤，返回通过 D 层的标的列表

        优化：回测模式使用向量化过滤
        """
        if not ts_codes:
            return []

        # 回测模式：向量化过滤
        if self._backtest_mode and self.loader._indicators_by_date:
            return self._filter_list_vectorized(ts_codes, eval_date, consecutive_limits_map)

        # 实盘模式：逐只检查
        self.preload_for_date(eval_date)

        if consecutive_limits_map is None:
            consecutive_limits_map = {}
        passed = []
        rejected = 0
        for code in ts_codes:
            cons_limits = consecutive_limits_map.get(code, 0)
            verdict = self.check(code, eval_date, cons_limits)
            if verdict.passed:
                passed.append(code)
            else:
                rejected += 1
                logger.debug(f"D 层剔除 {code}: {verdict.blacklist_reasons}")
        logger.info(f"D 层过滤: {len(passed)} 通过 / {rejected} 剔除")
        return passed

    def _filter_list_vectorized(self, ts_codes: List[str], eval_date: str,
                                 consecutive_limits_map: Optional[dict] = None) -> List[str]:
        """向量化过滤：使用预计算指标批量检测，无iterrows"""
        if consecutive_limits_map is None:
            consecutive_limits_map = {}

        ind_df = self.loader.get_indicators_snapshot(eval_date)
        if ind_df.empty:
            return ts_codes  # 无指标数据时全部放行

        pool_df = ind_df[ind_df['ts_code'].isin(ts_codes)].copy()
        if pool_df.empty:
            return ts_codes

        # ---- D1: ST/退市 ----
        # 审查 13：ST 改用 shared.st_history.is_st_mask 按日 PIT 判定
        # （旧实现 _batch_load_st 把"当前名称含 ST/退"的股票全历史剔除，
        # 既漏掉历史上曾 ST 的票，又把摘帽后的票全期误杀——未来函数）。
        d1_fail = pd.Series(False, index=pool_df.index)
        codes = pool_df['ts_code'].tolist()
        if self.cfg.d_exclude_st:
            st_mask = is_st_mask(self._conn(), codes, _to_date(eval_date))
            d1_fail = d1_fail | pd.Series(np.asarray(st_mask, dtype=bool), index=pool_df.index)
        if self.cfg.d_exclude_delist_warning and self._delist_cache:
            d1_fail = d1_fail | pool_df['ts_code'].isin(self._delist_cache)

        # ---- D2: 减持 ----
        # 审查 3：缓存是 {ts_code: [pub_date,...]}，这里按 eval_date 开 30 日窗口
        # （旧实现用整个回测区间出现过减持的股票集合，未来函数+窗口过宽）。
        d2_fail = pd.Series(False, index=pool_df.index)
        if self._reduction_cache:
            recent_codes = self._recent_reduction_codes(eval_date)
            d2_fail = pool_df['ts_code'].isin(recent_codes)

        # ---- D3: 诱多型涨停（向量化） ----
        # 2026-10-13 参数审查（涨停口径）：改用 shared/board_rules 判定，
        # 修复旧写法"小数阈值 0.097/0.197 去比百分数 pct_chg"的单位错位，
        # 并补上 301/302/303/689 与北交所 ±30% 的板块覆盖。
        pct_chg = pool_df['pct_chg'].fillna(0)
        is_zt = limit_up_mask(pool_df['ts_code'].values, pct_chg.values)

        vol_ratio = pool_df.get('volume_ratio_20', pd.Series(0, index=pool_df.index)).fillna(0)
        # 审查 15：伪封单比列 seal_quality_est → 放量强度 volume_strength
        # （成交额/60日均额，无 ×0.001 缩放），阈值同步改 d_trap_volume_strength_max。
        if 'volume_strength' in pool_df.columns:
            vol_strength = pool_df['volume_strength'].fillna(0)
        else:
            vol_strength = pool_df.get('seal_quality_est', pd.Series(0, index=pool_df.index)).fillna(0)
        d3_fail = is_zt & (vol_ratio > self.cfg.d_trap_volume_ratio) & \
                  (vol_strength < self.cfg.d_trap_volume_strength_max)

        # ---- D4: 高质押+连板 ----
        d4_fail = pd.Series(False, index=pool_df.index)
        if self._pledge_cache:
            pledge_codes = pool_df['ts_code'].isin(self._pledge_cache)
            # 检查连板数
            if consecutive_limits_map:
                cons_limits_arr = pool_df['ts_code'].map(
                    lambda c: consecutive_limits_map.get(c, 0)
                ).fillna(0)
                d4_fail = pledge_codes & (cons_limits_arr > self.cfg.d_pledge_consecutive_limit_days)

        # ---- D5: 高位接盘风险 ----
        # 收盘价接近前 52 周高点（shift-1，不含当日）的 d_near_high_pct 以上
        # 且并未创新高 → 剔除（避免高位接盘）。
        # 审查 14：阈值 0.95（旧值 1.0 等价于"只有超过前高才剔"，恰好把 B2 突破
        # 信号全否决，与注释意图矛盾）；close > high_52w 的创 52 周新高属突破信号，
        # 显式豁免不剔除。
        # 审查 7：high_52w/close 为 NaN（上市不足 52 周或数据缺失）时不可判定，
        # 跳过（不剔除）并记录条数，不再用 fillna(0) 把 NaN 伪装成 0 分母。
        high_52w = pool_df.get('high_52w', pd.Series(np.nan, index=pool_df.index)).astype(float)
        close = pool_df['close'].astype(float)
        d5_valid = high_52w.notna() & (high_52w > 0) & close.notna()
        d5_ratio = pd.Series(np.nan, index=pool_df.index)
        d5_ratio[d5_valid] = close[d5_valid] / high_52w[d5_valid]
        d5_fail = d5_valid & (d5_ratio > self.cfg.d_near_high_pct) & (close <= high_52w)
        d5_skipped = int((~d5_valid).sum())
        if d5_skipped:
            logger.debug(f"D5: {d5_skipped} 只因 high_52w/close 缺失跳过（不可判定）")

        # ---- D6: 接飞刀风险 ----
        # 近5日跌幅超过15%，剔除（避免接飞刀）
        pct_chg_5d = pool_df.get('pct_chg_5d', pd.Series(0, index=pool_df.index)).fillna(0)
        d6_fail = pct_chg_5d < -self.cfg.d_max_drop_5d

        # ---- D7: 追高风险 ----
        # 20日涨幅超过50%或60日涨幅超过80%，剔除（避免追高接盘）
        pct_chg_20d = pool_df.get('pct_chg_20d', pd.Series(0, index=pool_df.index)).fillna(0)
        pct_chg_60d = pool_df.get('pct_chg_60d', pd.Series(0, index=pool_df.index)).fillna(0)
        d7_fail = (pct_chg_20d > 50) | (pct_chg_60d > 80)

        # ---- 综合判定 ----
        all_pass = ~(d1_fail | d2_fail | d3_fail | d4_fail | d5_fail | d6_fail | d7_fail)
        passed_codes = pool_df.loc[all_pass, 'ts_code'].tolist()

        rejected = len(ts_codes) - len(passed_codes)
        logger.info(f"D层向量化过滤: {len(passed_codes)} 通过 / {rejected} 剔除")
        return passed_codes

    def check(self, ts_code: str, eval_date: str,
              consecutive_limits: int = 0) -> RiskVerdict:
        """单只股票风险检查（实盘模式用）"""
        verdict = RiskVerdict(ts_code=ts_code, eval_date=eval_date)

        # D1: ST / 退市风险
        # 审查 13：ST 走 shared.st_history.is_st_mask（按日 PIT，复用 D 层共享连接）
        if self.cfg.d_exclude_st:
            if bool(is_st_mask(self._conn(), [ts_code], _to_date(eval_date))[0]):
                verdict.passed = False
                verdict.blacklist_reasons.append("D1: ST/退市风险")
        if self.cfg.d_exclude_delist_warning and self._delist_cache and ts_code in self._delist_cache:
            verdict.passed = False
            verdict.blacklist_reasons.append("D1: 退市风险警示(名称快照)")

        # D2: 近 30 日重大减持（审查 3：按 eval_date 窗口判定，非"全期出现过"）
        if self._reduction_cache is not None:
            if ts_code in self._recent_reduction_codes(eval_date):
                verdict.passed = False
                verdict.blacklist_reasons.append("D2: 近30日有减持")
        else:
            if self._has_recent_reduction(ts_code, eval_date):
                verdict.passed = False
                verdict.blacklist_reasons.append("D2: 近30日有减持")

        # D3: 诱多型涨停
        if self._is_trap_limit_up(ts_code, eval_date):
            verdict.passed = False
            verdict.blacklist_reasons.append("D3: 诱多型涨停")

        # D4: 高质押 + 连板 > 3
        if consecutive_limits > self.cfg.d_pledge_consecutive_limit_days:
            if self._pledge_cache is not None:
                if ts_code in self._pledge_cache:
                    verdict.passed = False
                    verdict.blacklist_reasons.append("D4: 高质押连板")
            else:
                if self._is_high_pledge(ts_code, eval_date):
                    verdict.passed = False
                    verdict.blacklist_reasons.append("D4: 高质押连板")

        return verdict

    # ================================================================
    # 批量加载方法
    # ================================================================
    def _batch_load_delist(self) -> Set[str]:
        """批量加载"退市风险警示"股（按当前名称含'退'）。

        审查 13：旧 `_batch_load_st` 把名称含 ST/*/退 的股票一次拉成全历史集合，
        既是未来函数（用当前名称回溯历史）又把 ST 判定和退市判定混在一起。
        ST 已改走 shared.st_history.is_st_mask（PIT）；本方法只保留
        d_exclude_delist_warning 对应的退市风险警示，且**无 PIT 数据源**，
        只能用当前名称快照——已在回测 JSON limitations 中披露。
        """
        codes = set()
        conn = None
        try:
            conn = self._conn()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT DISTINCT d.ts_code FROM daily_quotes d
                LEFT JOIN stock_basic_info sb ON d.ts_code = sb.ts_code
                WHERE sb.stock_name IS NOT NULL AND sb.stock_name LIKE '%%退%%'
            """)
            for row in cur.fetchall():
                codes.add(row['ts_code'])
            cur.close()
        except Exception:
            pass
        return codes

    def _recent_reduction_codes(self, eval_date: str) -> Set[str]:
        """缓存中在 [eval_date - d_share_reduction_days, eval_date] 有减持公告的股票"""
        if not self._reduction_cache:
            return set()
        start = (
            datetime.strptime(eval_date[:10], "%Y-%m-%d") -
            timedelta(days=self.cfg.d_share_reduction_days)
        ).strftime("%Y-%m-%d")
        end = eval_date[:10]
        return {
            code for code, dates in self._reduction_cache.items()
            if any(start <= d <= end for d in dates)
        }

    def _batch_load_reductions(self, eval_date: str) -> Dict[str, List[str]]:
        """批量加载近期有减持的股票（单日模式），返回 {ts_code: [pub_date, ...]}"""
        return self._batch_load_reductions_range(
            (datetime.strptime(eval_date, "%Y-%m-%d") -
             timedelta(days=self.cfg.d_share_reduction_days)).strftime("%Y-%m-%d"),
            eval_date,
        )

    def _batch_load_reductions_range(self, start_date: str, end_date: str) -> Dict[str, List[str]]:
        """批量加载区间内所有减持公告（回测模式用），返回 {ts_code: [pub_date, ...]}

        审查 3：旧版只返回 ts_code 集合，导致"区间内任一天出现减持"的股票
        在整个回测期每天都被剔除（未来函数 + 窗口过宽）。改为保留公告日，
        由 _recent_reduction_codes(eval_date) 按日开窗判定。
        """
        result: Dict[str, List[str]] = {}
        conn = None
        try:
            conn = self._conn()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT ts_code, pub_date FROM announcement_data
                WHERE pub_date BETWEEN %s AND %s
                  AND (title ILIKE '%%减持%%' OR title ILIKE '%%reduction%%')
            """, (start_date, end_date))
            for row in cur.fetchall():
                code = row['ts_code']
                pd_ = str(row['pub_date'])[:10]
                result.setdefault(code, []).append(pd_)
            cur.close()
            for dates in result.values():
                dates.sort()
        except Exception:
            pass
        return result

    def _batch_load_high_pledge(self) -> Set[str]:
        """批量加载高质押股票（当前快照，非 PIT——见模块 docstring 局限）"""
        codes = set()
        try:
            conn = self._conn()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT DISTINCT ts_code FROM financial_data
                WHERE indicator = 'pledge_ratio'
                  AND value > %s
            """, (self.cfg.d_pledge_ratio_max,))
            for row in cur.fetchall():
                codes.add(row['ts_code'])
            cur.close()
        except Exception:
            pass
        return codes

    # ================================================================
    # 逐只检查方法（实盘降级用）
    # ================================================================
    def _has_recent_reduction(self, ts_code: str, eval_date: str) -> bool:
        try:
            conn = self._conn()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            start_date = (
                datetime.strptime(eval_date, "%Y-%m-%d") -
                timedelta(days=self.cfg.d_share_reduction_days)
            ).strftime("%Y-%m-%d")
            cur.execute("""
                SELECT 1 FROM announcement_data
                WHERE ts_code = %s
                  AND pub_date BETWEEN %s AND %s
                  AND (title ILIKE '%%减持%%' OR title ILIKE '%%reduction%%')
                LIMIT 1
            """, (ts_code, start_date, eval_date))
            exists = cur.fetchone() is not None
            cur.close()
            return exists
        except Exception:
            return False

    def _is_trap_limit_up(self, ts_code: str, eval_date: str) -> bool:
        """诱多型涨停检测（实盘模式用，回测模式用向量化版本）"""
        df = self.loader.get_daily(ts_code, start_date="2024-01-01", end_date=eval_date)
        if df is None or len(df) < 20:
            return False

        last = df.iloc[-1]
        today_close = float(last["close"])
        prev_close = float(df["close"].iloc[-2])

        # 2026-10-13 参数审查（涨停口径）：改用 shared/board_rules 按板块真实涨停价判断
        #（旧 detect_limit_up_pct 只认 300/688，漏掉 301/302/303/689 与北交所 ±30%）
        if not board_is_limit_up(ts_code, today_close, prev_close):
            return False

        vol_ratio = (
            float(last["volume"]) / df["volume"].iloc[-21:-1].mean()
            if len(df) >= 21 and df["volume"].iloc[-21:-1].mean() > 0
            else 0
        )
        if vol_ratio <= self.cfg.d_trap_volume_ratio:
            return False

        # 审查 15：伪封单比 → 放量强度（成交额/60日均额），阈值同步改名
        from .layer_b_launch import LayerBLaunchDetector
        detector = LayerBLaunchDetector(self.cfg, self.loader)
        vol_strength = detector._estimate_volume_strength(
            ts_code, eval_date, float(last["amount"])
        )
        return vol_strength < self.cfg.d_trap_volume_strength_max

    def _is_high_pledge(self, ts_code: str, eval_date: str) -> bool:
        """单票高质押查询（当前快照，非 PIT）"""
        try:
            conn = self._conn()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT 1 FROM financial_data
                WHERE ts_code = %s
                  AND indicator = 'pledge_ratio'
                  AND value > %s
                LIMIT 1
            """, (ts_code, self.cfg.d_pledge_ratio_max))
            exists = cur.fetchone() is not None
            cur.close()
            return exists
        except Exception:
            return False
