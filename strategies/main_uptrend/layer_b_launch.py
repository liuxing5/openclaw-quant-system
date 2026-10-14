"""
Layer B: 启动信号识别（日频）
===============================
"启动度"复合因子，每日盘后扫描 A 层预筛池：
  1. 量能突破：成交额 > 60日均值的 2.5 倍，换手率 > 5%
  2. 价格突破：突破前 60 日箱体上沿，或突破半年线且距半年线涨幅 < 8%
  3. 主力资金净流入（2026-10-13 起降级为弱确认项，不再参与通过判定）
  4. 放量强度（涨停日）：成交额 / 60日均额（审查 15：旧称"封单质量"，
     实际无 L2 封单数据，指标为伪封单比，语义是放量强度）
  5. 次日强度：次日不破当日均价，缩量整理或继续放量上攻

v2: 向量化扫描优化
- 优先使用预计算指标快照，每日扫描变为DataFrame过滤
- 降级到逐只评估（实盘模式）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set

import pandas as pd
import numpy as np

from .config import MainUptrendConfig
from .data_loader import DataLoader
from .indicators import (
    sma, volume_ma, box_range, near_ma,
    limit_up_mask,
)
from ..shared.board_rules import is_limit_up as board_is_limit_up

logger = logging.getLogger(__name__)


@dataclass
class LaunchSignal:
    """B 层单只股票的启动信号"""
    ts_code: str
    eval_date: str
    score: float = 0.0
    factors: Dict[str, float] = field(default_factory=dict)
    details: Dict[str, str] = field(default_factory=dict)
    passed: bool = False


class LayerBLaunchDetector:
    """B 层：启动信号识别"""

    def __init__(self, cfg: MainUptrendConfig,
                 loader: Optional[DataLoader] = None):
        self.cfg = cfg
        self.loader = loader or DataLoader()

    def evaluate(self, ts_code: str, eval_date: str) -> LaunchSignal:
        """
        评估单只股票在 eval_date 的启动信号（降级路径，实盘用）

        返回 LaunchSignal，含 composite score
        """
        sig = LaunchSignal(ts_code=ts_code, eval_date=eval_date)

        df = self.loader.get_daily(ts_code, start_date="2020-01-01", end_date=eval_date, min_days=self.cfg.b_volume_ma_days + 10)
        if df is None or len(df) < self.cfg.b_volume_ma_days + 10:
            sig.details["error"] = "数据不足"
            return sig

        df = df.reset_index(drop=True)
        last = df.iloc[-1]
        today_close = float(last["close"])
        today_volume = float(last["volume"])
        today_amount = float(last["amount"])
        today_pct = float(last["pct_chg"])
        today_turn = float(last.get("turnover_rate") or 0)

        scores = {}
        details = {}

        # --------------------------------------------------
        # B1: 量能突破
        # --------------------------------------------------
        vol_ma = volume_ma(df["amount"], self.cfg.b_volume_ma_days).iloc[-1]
        if pd.notna(vol_ma) and vol_ma > 0:
            vol_breakout = today_amount / vol_ma
            # 换手门槛：单票路径拿不到当日全市场截面，回退到绝对阈值 b_turnover_min
            #（向量化路径已改为"截面 P30 且 >=2%"分位数口径，见 _scan_pool_vectorized）
            b1_pass = vol_breakout > self.cfg.b_volume_breakout_mult and today_turn > self.cfg.b_turnover_min
            # 2026-10-14 修正：换手条件只作展示/排序参考，不门控量能分——
            # 门控接入 has_score 会把信号量压掉一个数量级（见向量化路径注释）。
            # b1_pass 保留用于 details 展示与排序参考，不参与 passed 判定。
            scores["vol_breakout"] = min(1.0, vol_breakout / (self.cfg.b_volume_breakout_mult * 2))
            details["vol_breakout"] = (f"量比MA60={vol_breakout:.1f}x, 换手={today_turn:.1f}%"
                                       + ("" if b1_pass else "（换手未达标）"))
        else:
            b1_pass = False
            scores["vol_breakout"] = 0
            details["vol_breakout"] = "量比MA60数据不足"

        # --------------------------------------------------
        # B2: 价格突破
        # --------------------------------------------------
        box = box_range(df["high"], df["low"], self.cfg.b_price_breakout_box_days)
        box_high_val = box["box_high"].iloc[-2] if len(box) > 1 else 0
        box_low_val = box["box_low"].iloc[-2] if len(box) > 1 else 0
        ma_half_year = sma(df["close"], self.cfg.b_price_ma_period)
        ma_half_val = ma_half_year.iloc[-1] if len(ma_half_year) > 0 and pd.notna(ma_half_year.iloc[-1]) else 0

        breakout_box = today_close > box_high_val if box_high_val > 0 else False
        # 破半年线必须站上均线，并允许距均线不超过最大偏离阈值
        breakout_ma = (
            ma_half_val > 0
            and today_close > ma_half_val
            and ((today_close - ma_half_val) / ma_half_val) <= self.cfg.b_price_above_ma_max_pct
        )

        b2_pass = breakout_box or breakout_ma
        if breakout_box:
            scores["price_breakout"] = 0.8
            details["price_breakout"] = f"突破{self.cfg.b_price_breakout_box_days}日箱体上沿"
        elif breakout_ma:
            scores["price_breakout"] = 0.6
            details["price_breakout"] = f"突破半年线(距MA={((today_close - ma_half_val) / ma_half_val * 100):.1f}%)"
        else:
            scores["price_breakout"] = 0
            details["price_breakout"] = "未突破"

        # --------------------------------------------------
        # B3: 主力资金净流入（弱确认项）
        # 2026-10-13 参数审查：主力净流入在控制当日涨跌幅后预测力≈0，
        # 已从 passed 硬条件降级为弱加权项——保留展示与评分，但评分打 5 折且不参与通过判定。
        # --------------------------------------------------
        flow_df = self.loader.get_main_force_flow(ts_code,
                                                   start_date="2025-01-01",
                                                   end_date=eval_date)
        if flow_df is not None and len(flow_df) > 0:
            today_flow = flow_df[flow_df["trade_date"].astype(str) == eval_date]
            if len(today_flow) > 0:
                net_inflow = float(today_flow["main_net_inflow"].iloc[0] or 0)
                # 审查 19：去掉 abs()。净流出也会被算成"满分流入"，
                # 与向量化路径（main_inflow_ratio 带符号、见 data_loader）口径不一致；
                # 这里改为带符号比值（净流出为负分），两路径一致。
                inflow_pct = net_inflow / today_amount if today_amount > 0 else 0.0
                scores["main_force"] = min(1.0, inflow_pct / (self.cfg.b_main_force_inflow_min_pct * 5))
                details["main_force"] = f"主力净流入={net_inflow / 1e4:.0f}万({inflow_pct * 100:.1f}%)[弱确认项]"
            else:
                scores["main_force"] = 0
                details["main_force"] = "当日无主力资金数据[弱确认项]"
        else:
            scores["main_force"] = 0
            details["main_force"] = "主力资金数据不可用[弱确认项]"
        # 弱加权：打 5 折，仅参与排序，不参与 passed 判定
        scores["main_force"] = scores.get("main_force", 0) * 0.5

        # --------------------------------------------------
        # B4: 封单质量（涨停日）
        # 2026-10-13 参数审查：改用 shared/board_rules 按板块真实涨跌幅限制判断
        # （旧 detect_limit_up_pct 只认 300/688，遗漏 301/302/303/689 与北交所 ±30%）
        # --------------------------------------------------
        prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else 0.0
        is_zt = board_is_limit_up(ts_code, today_close, prev_close)
        if is_zt:
            seal_score = self._estimate_volume_strength(ts_code, eval_date, today_amount)
            scores["seal_quality"] = min(1.0, seal_score / (self.cfg.b_volume_strength_min * 2))
            details["seal_quality"] = f"涨停放量强度={seal_score:.1f}x(60日均额)"
        else:
            # 与向量化路径一致：非涨停日仅当当日涨幅 > 3% 才给 0.3 基础分，
            # 否则 0 分（旧版无条件给 0.3，等于该维度恒有分）。
            scores["seal_quality"] = 0.3 if today_pct > 3.0 else 0.0
            details["seal_quality"] = "非涨停日"

        # 通过条件（与向量化路径 _scan_pool_vectorized 的定稿口径对齐，审查 24）：
        # 主力净流入降级为弱确认项后，硬维度只剩 量能/价格/封单；封单在非涨停日
        # 恒有 0.3 基础分，故 >=2 等效于"量能或价格有分"。
        # 向量化路径实测口径为 >=1 维有分 + 近5日动量>0（见下），此处照抄同一规则，
        # 避免实盘降级路径与回测向量化路径给出不同的通过判定。
        hard_dims = [
            scores.get("vol_breakout", 0) > 0,
            scores.get("price_breakout", 0) > 0,
            scores.get("seal_quality", 0) > 0,
        ]
        # 近5日动量：close / close[-6] - 1（与 data_loader 的 pct_chg_5d 同口径）
        if len(df) >= 6 and float(df["close"].iloc[-6]) > 0:
            momentum_5d = today_close / float(df["close"].iloc[-6]) - 1.0
        else:
            momentum_5d = 0.0
        details["momentum_5d"] = f"近5日涨幅={momentum_5d * 100:.1f}%"
        passed_b1_b4 = (sum(hard_dims) >= 1) and (momentum_5d > 0)

        # --------------------------------------------------
        # B5: 次日强度（需要 T+1 数据验证，此处返回到外部判断）
        # --------------------------------------------------
        sig.factors = scores
        sig.details = details
        sig.score = sum(scores.values())
        sig.passed = passed_b1_b4

        return sig

    def _estimate_volume_strength(self, ts_code: str, eval_date: str,
                                  today_amount: float) -> float:
        """估算涨停日放量强度（成交额 / 60日均额，倍数）。

        审查 15：旧名 _estimate_seal_quality 返回 成交额/60日均额×0.001 的
        "伪封单比"（无 L2 封单数据，真实封单金额不可得），语义实为放量强度。
        去掉 ×0.001 缩放，阈值相应改为 b_volume_strength_min（0.005×1000=5.0）。
        """
        try:
            df = self.loader.get_daily(ts_code, start_date="2025-01-01", end_date=eval_date)
            if df is None or len(df) < 60:
                return 0
            avg_amount = df["amount"].iloc[-60:].mean()
            if avg_amount > 0:
                return today_amount / avg_amount
        except Exception:
            pass
        return 0

    def scan_pool(self, pool: Set[str], eval_date: str,
                  top_n: int = 20) -> List[LaunchSignal]:
        """
        扫描 A 层预筛池，返回 Top N 启动信号

        优化：优先使用预计算指标向量化扫描，降级到逐只评估
        """
        # 调试：入口日志
        has_indicators = bool(self.loader._indicators_by_date)
        logger.info(f"[B层入口] eval_date={eval_date}, pool_size={len(pool)}, has_indicators={has_indicators}")
        
        # ---- 向量化路径：使用预计算指标 ----
        if has_indicators:
            return self._scan_pool_vectorized(pool, eval_date, top_n)

        # ---- 降级路径：逐只评估 ----
        logger.info(f"[B层入口] 使用fallback路径")
        return self._scan_pool_fallback(pool, eval_date, top_n)

    def _scan_pool_vectorized(self, pool: Set[str], eval_date: str,
                               top_n: int = 20) -> List[LaunchSignal]:
        """向量化扫描：使用预计算指标，每日扫描变为DataFrame过滤，无iterrows"""
        ind_df = self.loader.get_indicators_snapshot(eval_date)
        if ind_df.empty:
            return []

        # 过滤到pool中的股票
        pool_df = ind_df[ind_df['ts_code'].isin(pool)].copy()
        if pool_df.empty:
            logger.info(f"B层调试 - pool_df为空! ind_df有{len(ind_df)}行, pool有{len(pool)}只股票")
            if not ind_df.empty:
                logger.info(f"B层调试 - ind_df columns: {list(ind_df.columns)}, 前5行ts_code: {ind_df['ts_code'].head().tolist()}")
            return []

        # 审查 29：删除一次性调试块（原会把 pool_df 统计写入仓库根目录
        # b_layer_debug.txt），改为一条常规 info 日志。
        amt_nan = int(pool_df['amount'].isna().sum()) if 'amount' in pool_df.columns else -1
        logger.info(f"B层向量化 pool_df={len(pool_df)}行, ind_df={len(ind_df)}行, amount NaN={amt_nan}")

        # 快速预筛：至少满足量能或涨幅门槛之一
        quick_mask = (pool_df['amount'] >= 1e8) | (pool_df['pct_chg'].abs() >= 3)
        quick = pool_df[quick_mask].copy()
        if quick.empty:
            return []

        # ---- B1: 量能突破 ----
        # 换手率门槛（2026-10-13 参数审查：绝对阈值 -> 当日截面分位数）：
        #   换手率 >= 当日全市场截面 P30 且 >= 2%（双重条件，防低流动性）；
        #   截面取自当日全市场指标快照 ind_df（比只在 A 层池内排名更接近真实截面），
        #   截面数据不可用时回退到绝对阈值 b_turnover_min。
        # 2026-10-14 修正：该门控只作用于**排序权重**（total_score），不参与
        # has_score 通过判定——旧版 b1_pass 是死代码，直接接入通过判定会把
        # 日均信号从 ~1.7 压到 ~0.1（回测实测 191 -> 13），远超"分位数化"本意。
        turn_min = self.cfg.b_turnover_min
        if 'turnover_rate' in ind_df.columns:
            turn_p30 = ind_df['turnover_rate'].astype(float).quantile(0.30)
            if pd.notna(turn_p30):
                turn_min = max(float(turn_p30), 2.0)
        b1_turn_ok = quick['turnover_rate'].fillna(0) >= turn_min
        b1_pass = (quick['vol_breakout_ratio'] > self.cfg.b_volume_breakout_mult) & b1_turn_ok
        # 通过判定用的量能分：只看量能突破本身（沿用旧版语义，vol_ratio>0 即有分）
        vol_breakout_score = pd.Series(
            np.minimum(1.0, quick['vol_breakout_ratio'].fillna(0) / (self.cfg.b_volume_breakout_mult * 2)),
            index=quick.index,
        )
        # 排序用的量能分：换手达标才给满分，低换手折半（分位数门槛在此生效）
        vol_score_rank = np.where(b1_turn_ok, vol_breakout_score.values, vol_breakout_score.values * 0.5)

        # ---- B2: 价格突破 ----
        # 突破60日箱体高点，且突破幅度>1%（避免假突破）
        box_high = quick['box_60_high'].fillna(0)
        ma120_up = quick.get('ma_120_slope', pd.Series(0, index=quick.index)).fillna(0) > 0
        # 放宽：MA120向上是加分项而非强制条件
        breakout_box = (quick['close'] > box_high) & \
                       ((quick['close'] - box_high) / np.where(box_high != 0, box_high, 1) > 0.01) & \
                       (quick['pct_chg'].fillna(0) > 2.0)  # 放宽：涨幅门槛 3→2%
        # 或刚站上120日均线（偏离<5%，放宽从3%到5%）
        ma120_val = quick.get('ma_120', pd.Series(0, index=quick.index)).fillna(0)
        above_ma = (ma120_val > 0) & (quick['close'] > ma120_val) & \
                   ((quick['close'] - ma120_val) / ma120_val < 0.05) & \
                   (quick['pct_chg'].fillna(0) > 1.0)  # 放宽：涨幅门槛 2→1%
        b2_pass = breakout_box | above_ma
        price_breakout_score = pd.Series(
            np.where(breakout_box, 0.8, np.where(above_ma, 0.6, 0.0)), index=quick.index
        )

        # ---- B3: 主力资金（弱确认项，2026-10-13 参数审查降级）----
        # 依据：控制当日涨跌幅后主力净流入预测力≈0。
        # 处理：不再作为硬条件/通过维度，评分权重 0.8 -> 0.4（打 5 折），仅作排序弱因子。
        main_force_score = np.minimum(
            1.0, quick['main_inflow_ratio'].fillna(0) / (self.cfg.b_main_force_inflow_min_pct * 5)
        )

        # ---- B4: 封单质量 ----
        # 2026-10-13 参数审查（涨停口径）：改用 shared/board_rules 判定，
        # 修复旧写法"小数阈值 0.097 去比百分数 pct_chg（=涨幅>=0.09%就算涨停）"
        # 的单位错位，以及只认 300/688、漏掉北交所 ±30% 的问题。
        is_zt = pd.Series(
            limit_up_mask(quick['ts_code'].values, quick['pct_chg'].fillna(0).values),
            index=quick.index,
        )
        # 放宽：涨停给高分，非涨停但涨幅>3%也给基础分
        # 审查 15：列 seal_quality_est（伪封单比）已改为 volume_strength（放量强度，
        # 无 ×0.001 缩放），阈值相应改为 b_volume_strength_min。
        vol_strength_col = (quick['volume_strength'] if 'volume_strength' in quick.columns
                            else quick['seal_quality_est'])
        seal_quality_score = pd.Series(
            np.where(
                is_zt,
                np.minimum(1.0, vol_strength_col.fillna(0) / (self.cfg.b_volume_strength_min * 2)),
                np.where(quick['pct_chg'].fillna(0) > 3.0, 0.3, 0.0)  # 非涨停但涨幅>3%给基础分
            ),
            index=quick.index,
        )

        # 反转评分权重：涨停质量最重要；主力净流入降级为弱加权项，权重 0.8 -> 0.4（打5折）
        # 量能用排序口径（换手分位数折半在此生效），通过判定用原始量能分
        total_score = (np.asarray(vol_score_rank) * 1.0 + price_breakout_score * 1.0 + 
                      main_force_score.values * 0.4 + seal_quality_score * 1.5)

        # 通过条件（2026-10-14 定稿）：
        #   旧版 = 量能/价格/主力/封单 4 维中至少 2 维有分 + 近5日动量>0。
        #   由于主力净流入>0 覆盖约半数样本、量能分又几乎恒>0，旧条件等效于
        #   "动量确认"单条件（B 层近似空筛，信号量由 top_b 排序 + C/D 层决定）。
        #   主力资金剔除出判定后，为保持漏斗松紧度与旧版可比（回测实测：
        #   3 维>=2 会把信号量从 ~191 压到 13），通过门槛调整为 >=1 维有分，
        #   即量能分（几乎恒有分）+ 近5日动量为必要条件——与旧版等效语义一致，
        #   价格/封单/主力/换手分位数均只影响排序权重（total_score），不再做一票否决。
        has_score = ((np.asarray(vol_breakout_score) > 0).astype(int) +
                     (price_breakout_score > 0).astype(int) +
                     (seal_quality_score > 0).astype(int))

        # 动量确认：近5日涨幅>0%即可（只要有正向动量）
        momentum_confirm = quick.get('pct_chg_5d', pd.Series(0, index=quick.index)).fillna(0) > 0

        passed = (has_score >= 1) & momentum_confirm

        # 组装结果到DataFrame
        quick['total_score'] = total_score
        quick['passed'] = passed

        # 过滤通过的，取Top N
        passed_df = quick[quick['passed']].sort_values('total_score', ascending=False)
        top = passed_df.head(top_n)

        # 直接从DataFrame构建结果（无iterrows）
        # 审查 29：删除未被使用的 vol_scores / turn_rates / price_scores 三个死变量
        # （原 price_scores 重复计算价格分，且从不被读取）。
        results = []
        ts_codes_arr = top['ts_code'].values
        scores_arr = top['total_score'].values

        for i in range(len(top)):
            idx = top.index[i]
            code = ts_codes_arr[i]
            score = float(scores_arr[i])

            # 各因子分按 idx 对齐取值（旧版用 values[i] 按位置取，top 与 quick
            # 顺序不一致时会串行——展示层 bug，2026-10-13 一并修复）
            def _fs(series, _idx=idx):
                try:
                    v = series.loc[_idx]
                except KeyError:
                    return 0.0
                return float(v) if pd.notna(v) else 0.0

            # 构造detail字符串
            vol_ratio_val = float(quick.loc[idx, 'vol_breakout_ratio']) if idx in quick.index else 0
            turn_val = float(quick.loc[idx, 'turnover_rate']) if idx in quick.index and 'turnover_rate' in quick.columns else 0
            turn_display = f"{turn_val:.1f}" if not np.isnan(turn_val) else "N/A"
            above_pct_val = float(quick.loc[idx, 'above_ma_120_pct']) if idx in quick.index and 'above_ma_120_pct' in quick.columns else 0

            # 判断价格突破类型
            is_box_break = bool(breakout_box.get(idx, False)) if idx in breakout_box.index else False
            is_ma_break = bool(above_ma.get(idx, False)) if idx in above_ma.index else False
            if is_box_break:
                price_detail = "突破箱体"
            elif is_ma_break:
                price_detail = f"突破半年线(距MA={above_pct_val * 100:.1f}%)"
            else:
                price_detail = "未突破"

            main_ratio_val = float(quick.loc[idx, 'main_inflow_ratio']) if idx in quick.index and 'main_inflow_ratio' in quick.columns else 0
            main_detail = f"主力净流入比={main_ratio_val * 100:.1f}%[弱确认项]" if not np.isnan(main_ratio_val) and main_ratio_val != 0 else "无数据[弱确认项]"

            is_zt_val = bool(is_zt.get(idx, False)) if idx in is_zt.index else False

            sig = LaunchSignal(
                ts_code=code,
                eval_date=eval_date,
                score=score,
                factors={
                    'vol_breakout': _fs(vol_breakout_score),
                    'price_breakout': _fs(price_breakout_score),
                    'main_force': _fs(main_force_score),
                    'seal_quality': _fs(seal_quality_score),
                },
                details={
                    'vol_breakout': f"量比MA60={vol_ratio_val:.1f}x, 换手={turn_display}%",
                    'price_breakout': price_detail,
                    'main_force': main_detail,
                    'seal_quality': "涨停日" if is_zt_val else "非涨停日",
                },
                passed=True,
            )
            results.append(sig)

        logger.info(f"B层向量化扫描 {len(pool)} 只，预筛 {len(quick)} 只，通过 {len(passed_df)} 只，输出 Top {len(results)} 只，换手门槛={turn_min:.2f}%（截面P30/绝对阈值）")
        return results

    def _scan_pool_fallback(self, pool: Set[str], eval_date: str,
                             top_n: int = 20) -> List[LaunchSignal]:
        """降级路径：逐只评估（实盘模式用）"""
        # ---- 快速路径：用快照批量预筛 ----
        snapshot = self.loader.get_market_snapshot(eval_date, min_amount=0)
        if snapshot.empty:
            logger.info(f"B 层扫描 {len(pool)} 只，快照为空，跳过")
            return []

        # 构建快照索引
        snap_map: Dict[str, dict] = {}
        for _, row in snapshot.iterrows():
            snap_map[row['ts_code']] = row.to_dict()

        # 批量计算量能指标（用快照数据）
        # 先收集需要详细评估的候选
        quick_candidates = []
        for code in pool:
            if code not in snap_map:
                continue
            row = snap_map[code]
            today_amount = float(row.get('amount') or 0)
            today_turn = float(row.get('turnover_rate') or 0)
            today_pct = float(row.get('pct_chg') or 0)

            # 快速预筛：至少满足量能或涨幅门槛之一
            if today_amount < 1e8 and abs(today_pct) < 3:
                continue
            quick_candidates.append(code)

        # 限制预筛数量，避免全市场扫描超时
        if len(quick_candidates) > 200:
            # 按成交额排序，取最强的200只
            quick_candidates.sort(
                key=lambda c: float(snap_map[c].get('amount') or 0),
                reverse=True
            )
            quick_candidates = quick_candidates[:200]
            logger.info(f"B 层预筛结果过多，截断至 Top 200")

        logger.info(f"B 层扫描 {len(pool)} 只，快照命中 {len(quick_candidates)} 只，进入详细评估")

        # ---- 详细评估（只对预筛通过的） ----
        results = []
        for code in quick_candidates:
            sig = self.evaluate(code, eval_date)
            if sig.passed:
                results.append(sig)

        results.sort(key=lambda x: x.score, reverse=True)
        top = results[:top_n]
        logger.info(f"B 层扫描 {len(pool)} 只，通过 {len(results)} 只，输出 Top {len(top)} 只")
        return top
