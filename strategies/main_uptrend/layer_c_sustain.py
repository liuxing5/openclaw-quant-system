"""
Layer C: 持续性判定（日频）
===============================
判定"半天持续上涨"特征 — 跨日延续能力：
  1. 分时形态质量：日内上行占比 > 60% 或午前形态强势
     （回测跳过1分钟线，用"当日涨幅>0、3%满分"日线近似）
  2. 放量确认：成交额 / 20日均额 > 1 且当日上涨（旧称"大单买入占比"；
     2026-10-13 注：大单净买类订单分类伪指标在控制涨跌幅后无预测力，
     未作为硬条件）
  3. 缩量上涨：今量/昨量 0.2~1.5 且当日上涨（0.3~0.85 满分）
  4. 板上量比：封板时间 + 开板次数
  5. 同板块联动：同行业均涨幅 > 0，至少 1 只同行同涨

通过条件（两条路径共享常量，审查 24）：C1/C2/C3/C5 中 >=2 个维度
（CORE_COUNT_MIN）且核心综合分 > CORE_SCORE_MIN（1.8）。

v2: 向量化扫描优化
- 优先使用预计算指标快照，批量DataFrame过滤
- 降级到逐只评估（实盘模式）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set

import numpy as np
import pandas as pd

from .config import MainUptrendConfig
from .data_loader import DataLoader
from .indicators import (
    intraday_up_ratio, intraday_morning_checks,
    sector_peers_rising,
    limit_up_mask,
)
from ..shared.board_rules import (
    is_limit_up as board_is_limit_up,
    limit_pct as board_limit_pct,
)
from .layer_b_launch import LaunchSignal

logger = logging.getLogger(__name__)


# ================================================================
# 审查 24：向量化路径与 fallback 路径共享的判定常量与打分函数
# （旧版两路径各写各的，数值不一致：如 C1 阈值 pct>0 vs pct>3、
#  C3 区间 [0.2,1.5] vs [0.6,0.8]、通过门槛少一个 core_score 条件）
# ================================================================
C1_PCT_GT: float = 0.0        # C1 通过：当日涨幅 > 0%
C1_FULL_PCT: float = 3.0      # C1 满分：涨幅达 3%

C2_AMOUNT_RATIO_GT: float = 1.0   # C2 通过：成交额/20日均额 > 1
C2_PCT_GT: float = 0.0            # C2 通过：当日涨幅 > 0%
C2_FULL_RATIO: float = 3.0        # C2 满分：放量 3 倍

C3_SHRINK_MIN: float = 0.2    # C3 通过的量比（今量/昨量）下限
C3_SHRINK_MAX: float = 1.5    # C3 通过的量比上限
C3_SHRINK_FULL_LO: float = 0.3   # 缩量满分区间下限
C3_SHRINK_FULL_HI: float = 0.85  # 缩量满分区间上限
C3_SHRINK_MID_HI: float = 1.2    # 0.7 分区间上限

CORE_COUNT_MIN: int = 2       # 通过：诚实维度（C1/C2/C3/C5）至少 2 个
CORE_SCORE_MIN: float = 1.8   # 通过：核心综合分下限（3.0 - 旧版恒加的虚高 1.2）

# 各维度在 total_score 中的权重（两条路径一致）
W_INTRADAY = 1.5
W_BIG_ORDER = 1.5
W_VOL_SHRINK = 1.2
W_SECTOR = 1.0
W_SEAL = 1.2


def c1_score(pct) -> np.ndarray:
    """C1 分时维度分：涨幅 > 0 才有分，3% 满分（标量或数组均可用）"""
    pct = np.asarray(pct, dtype=float)
    return np.where(pct > C1_PCT_GT, np.minimum(1.0, pct / C1_FULL_PCT), 0.0)


def c1_pass_mask(pct) -> np.ndarray:
    return np.asarray(pct, dtype=float) > C1_PCT_GT


def c2_pass_mask(amount_ratio, pct) -> np.ndarray:
    return (np.asarray(amount_ratio, dtype=float) > C2_AMOUNT_RATIO_GT) & \
           (np.asarray(pct, dtype=float) > C2_PCT_GT)


def c2_score(amount_ratio) -> np.ndarray:
    return np.minimum(1.0, np.asarray(amount_ratio, dtype=float) / C2_FULL_RATIO)


def c3_pass_mask(shrink, pct) -> np.ndarray:
    """C3 缩量上涨：量比落在 [C3_SHRINK_MIN, C3_SHRINK_MAX] 且当日上涨"""
    shrink = np.asarray(shrink, dtype=float)
    return (np.asarray(pct, dtype=float) > 0) & \
           (shrink >= C3_SHRINK_MIN) & (shrink <= C3_SHRINK_MAX)


def c3_score(shrink) -> np.ndarray:
    """C3 分档打分：0.3-0.85 满分 / 0.85-1.2 给 0.7 / 0.2-1.5 给 0.4"""
    shrink = np.asarray(shrink, dtype=float)
    return np.where((shrink >= C3_SHRINK_FULL_LO) & (shrink <= C3_SHRINK_FULL_HI), 1.0,
           np.where((shrink > C3_SHRINK_FULL_HI) & (shrink <= C3_SHRINK_MID_HI), 0.7,
           np.where((shrink >= C3_SHRINK_MIN) & (shrink <= C3_SHRINK_MAX), 0.4, 0.0)))


@dataclass
class SustainSignal:
    """C 层单只股票的持续性判定"""
    ts_code: str
    eval_date: str
    score: float = 0.0
    factors: Dict[str, float] = field(default_factory=dict)
    details: Dict[str, str] = field(default_factory=dict)
    passed: bool = False
    b_signal: Optional[LaunchSignal] = None


class LayerCSustainAnalyzer:
    """C 层：持续性判定"""

    def __init__(self, cfg: MainUptrendConfig,
                 loader: Optional[DataLoader] = None,
                 skip_1min: bool = False):
        self.cfg = cfg
        self.loader = loader or DataLoader()
        self.skip_1min = skip_1min  # 回测模式下跳过1分钟线查询

    def scan_b_signals(self, b_signals: List[LaunchSignal],
                       top_n: int = 8) -> List[SustainSignal]:
        """
        对 B 层输出的 Top N 信号做持续性二次过滤

        优化：优先使用预计算指标向量化扫描，降级到逐只评估
        """
        # ---- 向量化路径 ----
        if self.loader._indicators_by_date and b_signals:
            return self._scan_b_signals_vectorized(b_signals, top_n)

        # ---- 降级路径：逐只评估 ----
        return self._scan_b_signals_fallback(b_signals, top_n)

    def _scan_b_signals_vectorized(self, b_signals: List[LaunchSignal],
                                    top_n: int = 8) -> List[SustainSignal]:
        """向量化扫描：使用预计算指标，批量DataFrame过滤，无iterrows"""
        eval_date = b_signals[0].eval_date
        b_codes = {s.ts_code: s for s in b_signals}

        ind_df = self.loader.get_indicators_snapshot(eval_date)
        if ind_df.empty:
            return []

        # 过滤到B层信号中的股票
        pool_df = ind_df[ind_df['ts_code'].isin(b_codes)].copy()
        if pool_df.empty:
            return []

        pct_chg = pool_df['pct_chg'].fillna(0).values  # 转为numpy数组

        # ---- C1: 分时形态质量（回测模式用日线近似） ----
        # 审查 24：与 fallback 共享 C1_PCT_GT / C1_FULL_PCT / W_INTRADAY
        if self.skip_1min:
            c1_pass = c1_pass_mask(pct_chg)            # 只要当日上涨即可
            intraday_score = c1_score(pct_chg)         # 3%即满分
        else:
            intraday_score = np.zeros(len(pool_df))
            c1_pass = np.zeros(len(pool_df), dtype=bool)

        # ---- C2: 放量确认 ----
        # 2026-10-13 参数审查：config.c_big_order_net_buy_min_pct（大单净买>=5%）在全代码库
        # 中没有任何引用，不是硬条件，无需降级；这里的 "big_order" 实为放量倍数
        #（成交额/20日均额），属量能指标而非订单分类类伪指标，判定逻辑保持不变。
        # 审查 24：与 fallback 共享 C2_AMOUNT_RATIO_GT / C2_PCT_GT / C2_FULL_RATIO
        amount_ratio = pool_df['amount_ratio_20'].fillna(0).values
        c2_pass = c2_pass_mask(amount_ratio, pct_chg)  # 只要放量+上涨
        big_order_score = c2_score(amount_ratio)       # 3倍即满分

        # ---- C3: 缩量上涨 ----
        # 审查 24：与 fallback 共享 C3_SHRINK_* 区间与 c3_score 分档
        vol_shrink = pool_df['volume_shrink_ratio'].fillna(0).values
        c3_pass = c3_pass_mask(vol_shrink, pct_chg)
        vol_shrink_score = c3_score(vol_shrink)

        # ---- C4: 板上量比 ----
        # 2026-10-13 参数审查（涨停口径）：改用 shared/board_rules 判定，
        # 修复旧写法"小数阈值 0.097/0.197 去比百分数 pct_chg"的单位错位，
        # 并补上 301/302/303/689 与北交所 ±30% 的板块覆盖。
        is_zt = limit_up_mask(pool_df['ts_code'].values, pct_chg)
        if self.skip_1min:
            # 放宽：涨幅>3%就给基础分
            c4_pass = is_zt | (pct_chg > 3.0)
            seal_quality_score = np.where(is_zt, 1.0, np.where(pct_chg > 3.0, 0.4, 0.0))
        else:
            c4_pass = np.zeros(len(pool_df), dtype=bool)
            seal_quality_score = np.zeros(len(pool_df))

        # ---- C5: 同板块联动 ----
        if 'industry_avg_pct' in pool_df.columns:
            industry_avg = pool_df['industry_avg_pct'].fillna(0).values
        else:
            industry_avg = np.zeros(len(pool_df))
        if 'industry_rising_count' in pool_df.columns:
            industry_rising = pool_df['industry_rising_count'].fillna(0).values
        else:
            industry_rising = np.zeros(len(pool_df))
        # 放宽：行业平均涨幅>0%，至少1只同涨
        c5_pass = (industry_avg > 0) & (industry_rising >= 1)
        sector_score = np.minimum(1.0, industry_avg / 2.0)

        # ---- 综合判定 ----
        # 2026-10-14 等效校准：旧版涨停单位错位 bug（小数阈值比百分数）令 is_zt 对
        # 几乎所有上涨股恒为 True，于是 C4 维度几乎恒过、seal 分恒 +1.2——
        # passed_count>=3 与 total_score>3.0 这两个阈值实际是"含虚高 1.2 分"校准出来的。
        # 修复涨停口径后，为保持 C 层漏斗松紧度与旧版等效（不因口径修复而额外收紧）：
        #   维度数要求 = 4 个诚实维度（C1/C2/C3/C5）中 >=2（等价旧版含 C4 的 >=3）；
        #   综合分阈值 = 3.0 - 1.2 = 1.8（去掉旧版恒加的虚高分）；
        #   honest seal 分（真实涨停才有）保留进 total_score 只作排序加分。
        core_count = c1_pass.astype(int) + c2_pass.astype(int) + c3_pass.astype(int) + \
                     c5_pass.astype(int)
        core_score = (intraday_score * W_INTRADAY + big_order_score * W_BIG_ORDER +
                      vol_shrink_score * W_VOL_SHRINK + sector_score * W_SECTOR)

        # 审查 29：删除死变量 passed_count（赋值后从未被读取；C4 是否通过仅
        # 通过 seal_quality_score 进入 total_score，不参与维度计数）
        total_score = core_score + seal_quality_score * W_SEAL

        # 调试日志（审查 29：分母原为硬编码 50，改为实际样本数）
        n_pool = max(1, len(pool_df))
        logger.info(f"C层调试: C1={c1_pass.sum()}/{n_pool}, C2={c2_pass.sum()}/{n_pool}, "
                    f"C3={c3_pass.sum()}/{n_pool}, C4={c4_pass.sum()}/{n_pool}, C5={c5_pass.sum()}/{n_pool}")
        logger.info(f"C层调试: core>=2&core_score>1.8: {((core_count >= CORE_COUNT_MIN) & (core_score > CORE_SCORE_MIN)).sum()}, total_score分布: min={total_score.min():.2f}, max={total_score.max():.2f}, mean={total_score.mean():.2f}")

        # 通过条件（2026-10-14 等效校准，审查 24：与 fallback evaluate() 共享
        # CORE_COUNT_MIN / CORE_SCORE_MIN 两个常量）
        passed = (core_count >= CORE_COUNT_MIN) & (core_score > CORE_SCORE_MIN)

        # 过滤通过的，取Top N
        pool_df['total_score'] = total_score
        pool_df['passed'] = passed.values if hasattr(passed, 'values') else passed
        passed_df = pool_df[pool_df['passed']].sort_values('total_score', ascending=False)
        top = passed_df.head(top_n)

        # 直接从DataFrame构建结果（无iterrows）
        results = []
        top_ts_codes = top['ts_code'].values
        top_scores = top['total_score'].values

        # 构建pool_df行号到score数组位置的映射
        # 因为score数组是按pool_df的顺序，top是从pool_df过滤出来的
        # 需要找到top每行在pool_df中的位置
        pool_idx_map = {idx: i for i, idx in enumerate(pool_df.index)}

        for i in range(len(top)):
            idx = top.index[i]
            code = top_ts_codes[i]
            b_sig = b_codes.get(code)
            pos = pool_idx_map.get(idx, 0)  # score数组中的位置

            vol_shrink_val = float(pool_df.loc[idx, 'volume_shrink_ratio']) if 'volume_shrink_ratio' in pool_df.columns else 0
            amt_ratio_val = float(pool_df.loc[idx, 'amount_ratio_20']) if 'amount_ratio_20' in pool_df.columns else 0
            ind_avg_val = float(pool_df.loc[idx, 'industry_avg_pct']) if 'industry_avg_pct' in pool_df.columns and pd.notna(pool_df.loc[idx, 'industry_avg_pct']) else 0
            ind_rise_val = float(pool_df.loc[idx, 'industry_rising_count']) if 'industry_rising_count' in pool_df.columns else 0
            pct_val = float(pool_df.loc[idx, 'pct_chg'])
            is_zt_val = bool(is_zt[pos]) if pos < len(is_zt) else False

            sig = SustainSignal(
                ts_code=code,
                eval_date=eval_date,
                score=float(top_scores[i]),
                factors={
                    'intraday': float(intraday_score[pos]) if pos < len(intraday_score) else 0,
                    'big_order': float(big_order_score[pos]) if pos < len(big_order_score) else 0,
                    'vol_shrink': float(vol_shrink_score[pos]) if pos < len(vol_shrink_score) else 0,
                    'seal_quality': float(seal_quality_score[pos]) if pos < len(seal_quality_score) else 0,
                    'sector': float(sector_score[pos]) if pos < len(sector_score) else 0,
                },
                details={
                    'intraday': f"回测模式, 日涨幅={pct_val:.1f}%" if self.skip_1min else "1分钟线不可用",
                    'big_order': f"成交额倍数={amt_ratio_val:.1f}x, 涨幅={pct_val:.1f}%",
                    'vol_shrink': f"量比T-1={vol_shrink_val:.1f}x, 涨跌={pct_val:+.1f}%",
                    'seal_quality': f"回测模式, 涨停={pct_val:.1f}%" if is_zt_val else "非涨停日",
                    'sector': f"板块均值={ind_avg_val:.1f}%, 上涨={ind_rise_val:.0f}只" if ind_avg_val != 0 or ind_rise_val > 0 else "板块数据不可用",
                },
                passed=True,
                b_signal=b_sig,
            )
            results.append(sig)

        logger.info(f"C层向量化扫描 {len(b_signals)} 只，通过 {len(passed_df)} 只，输出 Top {len(results)} 只")
        return results

    def _scan_b_signals_fallback(self, b_signals: List[LaunchSignal],
                                  top_n: int = 8) -> List[SustainSignal]:
        """降级路径：逐只评估（实盘模式用）"""
        results = []
        for b_sig in b_signals:
            c_sig = self.evaluate(b_sig.ts_code, b_sig.eval_date, b_sig)
            if c_sig.passed:
                results.append(c_sig)

        results.sort(key=lambda x: x.score, reverse=True)
        top = results[:top_n]
        logger.info(f"C 层扫描 {len(b_signals)} 只，通过 {len(results)} 只，输出 Top {len(top)} 只")
        return top

    def evaluate(self, ts_code: str, eval_date: str,
                 b_signal: Optional[LaunchSignal] = None) -> SustainSignal:
        """评估单只股票的持续性（降级路径，实盘用）"""
        sig = SustainSignal(
            ts_code=ts_code, eval_date=eval_date, b_signal=b_signal,
        )

        df = self.loader.get_daily(ts_code, start_date="2020-01-01", end_date=eval_date)
        if df is None or len(df) < 20:
            sig.details["error"] = "日线数据不足"
            return sig

        df = df.reset_index(drop=True)
        if len(df) < 2:
            sig.details["error"] = "至少需要 2 日数据"
            return sig

        last = df.iloc[-1]
        prev = df.iloc[-2]
        today_close = float(last["close"])
        today_volume = float(last["volume"])
        today_amount = float(last["amount"])
        today_pct = float(last["pct_chg"])
        prev_volume = float(prev["volume"])

        scores = {}
        details = {}

        # C1: 分时形态质量（审查 24：与向量化路径共享 C1_PCT_GT/C1_FULL_PCT）
        if self.skip_1min:
            c1_pass = bool(c1_pass_mask(today_pct))
            scores["intraday"] = float(c1_score(today_pct))
            details["intraday"] = f"回测模式(跳过1min), 日涨幅={today_pct:.1f}%"
        else:
            df_1min = self.loader.get_1min_kline(ts_code, eval_date)
            if df_1min is not None and len(df_1min) >= 30:
                up_ratio = intraday_up_ratio(df_1min)
                morning = intraday_morning_checks(
                    df_1min,
                    morning_pct=self.cfg.c_intraday_morning_pct,
                    morning_amplitude_max=self.cfg.c_intraday_morning_amplitude_max,
                )
                c1_pass = up_ratio > self.cfg.c_intraday_up_ratio_min or morning["passed"]
                scores["intraday"] = max(up_ratio, 1.0 if morning["passed"] else 0)
                details["intraday"] = f"上行占比={up_ratio:.0%}, 午前形态={'强势' if morning['passed'] else '普通'}"
            else:
                c1_pass = False
                scores["intraday"] = 0
                details["intraday"] = "1分钟线不可用"

        # C2: 放量确认（审查 24：与向量化路径共享 C2_* 常量）
        avg_amount_20 = df["amount"].iloc[-21:-1].mean() if len(df) >= 21 else df["amount"].mean()
        if avg_amount_20 > 0:
            amount_ratio = today_amount / avg_amount_20
            c2_pass = bool(c2_pass_mask(amount_ratio, today_pct))
            scores["big_order"] = float(c2_score(amount_ratio))
            details["big_order"] = f"成交额倍数={amount_ratio:.1f}x, 涨幅={today_pct:.1f}%"
        else:
            c2_pass = False
            scores["big_order"] = 0
            details["big_order"] = "数据不足"

        # C3: 缩量上涨（审查 24：与向量化路径共享 C3_SHRINK_* 区间与 c3_score）
        ratio_v = today_volume / prev_volume if prev_volume > 0 else 0.0
        c3_pass = bool(c3_pass_mask(ratio_v, today_pct))
        scores["vol_shrink"] = float(c3_score(ratio_v))
        details["vol_shrink"] = f"量比T-1={ratio_v:.1f}x, 涨跌={today_pct:+.1f}%"

        # C4: 板上量比（2026-10-13 参数审查：改用 shared/board_rules 按板块真实涨停价判断）
        prev_close = float(prev["close"])
        is_zt = board_is_limit_up(ts_code, today_close, prev_close)
        c4_pass = False
        if is_zt:
            if self.skip_1min:
                c4_pass = True
                scores["seal_quality"] = 1.0
                details["seal_quality"] = f"回测模式, 涨停={today_pct:.1f}%"
            else:
                seal_time = self._estimate_seal_time(ts_code, eval_date)
                open_times = self._estimate_open_times(ts_code, eval_date)
                early = seal_time <= self.cfg.c_seal_early_time
                tight = open_times <= self.cfg.c_seal_max_open_times
                c4_pass = early and tight
                scores["seal_quality"] = (1.0 if early else 0.3) + (1.0 if tight else 0.1)
                details["seal_quality"] = f"封板={seal_time}, 开板={open_times}次"
        else:
            c4_pass = False
            scores["seal_quality"] = 0
            details["seal_quality"] = "非涨停日"

        # C5: 同板块联动
        peer_pct = self._get_sector_peers_pct(ts_code, eval_date)
        sector_result = sector_peers_rising(
            peer_pct,
            sector_rise_min=self.cfg.c_sector_rise_min_pct,
            peer_count_min=self.cfg.c_sector_peer_count_min,
        )
        c5_pass = sector_result["passed"]
        # 打分与向量化对齐：sector_score = min(1, 行业均涨幅% / 2)
        scores["sector"] = float(np.minimum(1.0, sector_result["sector_avg"] / 2.0))
        details["sector"] = f"板块均值={sector_result['sector_avg']:.1f}%, 上涨={sector_result['rising_count']}只"

        sig.factors = scores
        sig.details = details
        # 审查 24：core_score/total_score 的权重与通过门槛（CORE_COUNT_MIN /
        # CORE_SCORE_MIN）与向量化路径 _scan_b_signals_vectorized 完全共享，
        # 旧版 fallback 只判 core_count >= 2 且按各维权重 1.0 求和。
        core_count = int(c1_pass) + int(c2_pass) + int(c3_pass) + int(c5_pass)
        core_score = (scores["intraday"] * W_INTRADAY +
                      scores["big_order"] * W_BIG_ORDER +
                      scores["vol_shrink"] * W_VOL_SHRINK +
                      scores["sector"] * W_SECTOR)
        sig.score = core_score + scores["seal_quality"] * W_SEAL
        # 通过条件（2026-10-14 等效校准，与向量化路径对齐）：旧版涨停口径 bug
        # 令 C4 几乎恒过，"5 维 >=3" 实为 "4 个诚实维度 >=2"；修复后直接按
        # 诚实维度判定，C4 真实涨停只作加分不再凑维度数。
        sig.passed = core_count >= CORE_COUNT_MIN and core_score > CORE_SCORE_MIN

        return sig

    def _estimate_seal_time(self, ts_code: str, eval_date: str) -> str:
        """估算封板时间（1分钟线检测首次触板）"""
        df_1min = self.loader.get_1min_kline(ts_code, eval_date)
        if df_1min is None or len(df_1min) < 30:
            return "00:00"
        limit_pct = board_limit_pct(ts_code)  # 2026-10-13: 涨停口径统一走 shared/board_rules
        open_price = float(df_1min["close"].iloc[0])
        limit_price = open_price * (1 + limit_pct) if open_price > 0 else 0
        for _, row in df_1min.iterrows():
            if float(row["high"]) >= limit_price * 0.999:
                return str(row["time"])[:5]
        return "00:00"

    def _estimate_open_times(self, ts_code: str, eval_date: str) -> int:
        """估算开板次数（1分钟线板价上下穿行次数）"""
        df_1min = self.loader.get_1min_kline(ts_code, eval_date)
        if df_1min is None or len(df_1min) < 30:
            return 99
        limit_pct = board_limit_pct(ts_code)  # 2026-10-13: 涨停口径统一走 shared/board_rules
        open_price = float(df_1min["close"].iloc[0])
        limit_price = open_price * (1 + limit_pct) if open_price > 0 else 0
        below_limit = df_1min["high"] < limit_price * 0.999
        open_times = 0
        was_below = True
        for i in range(len(below_limit)):
            if not below_limit.iloc[i] and was_below:
                open_times += 1
            was_below = below_limit.iloc[i]
        return max(0, open_times - 1)

    def _get_sector_peers_pct(self, ts_code: str, eval_date: str) -> List[float]:
        """获取同概念板块其他标的当日涨幅"""
        # 优先从预加载数据获取
        if self.loader._preloaded_daily is not None and not self.loader._preloaded_daily.empty:
            day_data = self.loader._preloaded_daily[
                self.loader._preloaded_daily['trade_date'] == eval_date
            ]
            stock_rows = day_data[day_data['ts_code'] == ts_code]
            if stock_rows.empty or 'industry' not in stock_rows.columns:
                return []
            industry = stock_rows.iloc[0].get('industry')
            if not industry or pd.isna(industry):
                return []
            peers = day_data[
                (day_data['industry'] == industry) &
                (day_data['ts_code'] != ts_code) &
                (day_data['pct_chg'].notna())
            ]
            return peers['pct_chg'].astype(float).tolist()

        # 降级到DB查询
        conn = None
        try:
            from core.db.connection import get_db_fresh
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT sf.industry FROM daily_quotes d
                LEFT JOIN (
                    SELECT DISTINCT ON (ts_code) ts_code, industry
                    FROM stock_fundamentals
                    WHERE industry IS NOT NULL
                    ORDER BY ts_code, report_date DESC
                ) sf ON d.ts_code = sf.ts_code
                WHERE d.ts_code = %s AND d.trade_date = %s AND sf.industry IS NOT NULL
                LIMIT 1
            """, (ts_code, eval_date))
            row = cur.fetchone()
            if not row or not row['industry']:
                cur.close()
                return []
            industry = row['industry']
            cur.execute("""
                SELECT d.pct_chg FROM daily_quotes d
                LEFT JOIN (
                    SELECT DISTINCT ON (ts_code) ts_code, industry
                    FROM stock_fundamentals
                    WHERE industry IS NOT NULL
                    ORDER BY ts_code, report_date DESC
                ) sf ON d.ts_code = sf.ts_code
                WHERE d.trade_date = %s AND sf.industry = %s
                  AND d.pct_chg IS NOT NULL AND d.ts_code != %s
            """, (eval_date, industry, ts_code))
            results = [float(r['pct_chg']) for r in cur.fetchall()]
            cur.close()
            return results
        except Exception:
            return []
        finally:
            if conn and not conn.closed:
                conn.close()