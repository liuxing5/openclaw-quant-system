"""
Layer E: 趋势持续型检测（日频）
=================================
检测"慢牛/趋势上行"型标的 — 与 B 层"启动信号"互补：

  B 层检测：某天突然爆发（量能3.5x + 涨停 + 主力涌入）
  E 层检测：连续1-2个月温和上涨（均线多头 + 阶梯放量 + 回撤可控）

E1 均线多头排列：MA5 > MA10 > MA20 > MA60，持续 N 日
E2 价格站稳短期均线：收盘价 > MA5，偏离 < 2%
E3 回撤控制：近20日最大回撤 < 12%
E4 阶梯式放量：5日均量 > 20日均量 > 60日均量
E5 动量一致性：近10/20/60日涨幅至少3个为正
E6 ADX 趋势强度：ADX > 25（趋势明确）
E7 RSI 区间：45 < RSI < 80（趋势健康，非超买超卖）
E8 趋势持续时间：连续站上MA20超过10日
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

logger = logging.getLogger(__name__)


@dataclass
class TrendSignal:
    ts_code: str
    eval_date: str
    score: float = 0.0
    factors: Dict[str, float] = field(default_factory=dict)
    details: Dict[str, str] = field(default_factory=dict)
    passed: bool = False
    trend_type: str = "sustained"


class LayerETrendDetector:
    """E 层：趋势持续型检测"""

    def __init__(self, cfg: MainUptrendConfig,
                 loader: Optional[DataLoader] = None):
        self.cfg = cfg
        self.loader = loader or DataLoader()

    def scan_pool(self, pool: Set[str], eval_date: str,
                  top_n: int = 20) -> List[TrendSignal]:
        if self.loader._indicators_by_date:
            return self._scan_vectorized(pool, eval_date, top_n)
        return self._scan_fallback(pool, eval_date, top_n)

    def _scan_vectorized(self, pool: Set[str], eval_date: str,
                         top_n: int = 20) -> List[TrendSignal]:
        ind_df = self.loader.get_indicators_snapshot(eval_date)
        if ind_df.empty:
            return []

        pool_df = ind_df[ind_df['ts_code'].isin(pool)].copy()
        if pool_df.empty:
            return []

        pct_chg = pool_df['pct_chg'].fillna(0).values
        close = pool_df['close'].fillna(0).values

        scores = {}
        details_map = {}

        # E1: 均线多头排列
        ma5 = pool_df.get('ma_5', pd.Series(np.nan, index=pool_df.index))
        ma10 = pool_df.get('ma_10', pd.Series(np.nan, index=pool_df.index))
        ma20 = pool_df.get('ma_20', pd.Series(np.nan, index=pool_df.index))
        ma60 = pool_df.get('ma_60', pd.Series(np.nan, index=pool_df.index))

        ma_alignment = (ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60)
        ma_alignment_count = pool_df.get('ma_alignment_days', pd.Series(0, index=pool_df.index))
        e1_pass = ma_alignment & (ma_alignment_count >= self.cfg.e_ma_alignment_days)
        e1_score = np.where(e1_pass, 1.0,
                   np.where(ma_alignment, 0.6, 0.0))
        scores['e1_ma_alignment'] = e1_score
        details_map['e1_ma_alignment'] = np.where(
            e1_pass, "均线多头排列",
            np.where(ma_alignment, "部分多头", "非多头排列"))

        # E2: 价格站稳短期均线
        ma5_safe = ma5.fillna(0).values
        ma5_positive = ma5_safe > 0
        above_ma5 = (close > ma5_safe) & ma5_positive
        deviation = np.zeros_like(close)
        np.divide(close - ma5_safe, ma5_safe, out=deviation, where=ma5_positive)
        e2_pass = above_ma5 & (deviation < self.cfg.e_price_above_short_ma_pct) & (deviation > -0.03)
        e2_score = np.where(e2_pass, 1.0,
                   np.where(above_ma5, 0.5, 0.0))
        scores['e2_price_stability'] = e2_score
        details_map['e2_price_stability'] = np.where(
            e2_pass, "站稳MA5",
            np.where(above_ma5, "MA5上方偏离大", "MA5下方"))

        # E3: 回撤控制
        max_dd = pool_df.get('max_drawdown_20d', pd.Series(0, index=pool_df.index)).fillna(0).values
        e3_pass = (max_dd > -self.cfg.e_max_drawdown_20d) & (max_dd < 0)
        e3_score = np.where(max_dd > -0.05, 1.0,
                   np.where(max_dd > -self.cfg.e_max_drawdown_20d, 0.7,
                   np.where(max_dd > -0.20, 0.3, 0.0)))
        scores['e3_drawdown'] = e3_score
        details_map['e3_drawdown'] = np.where(
            max_dd > -0.05, "回撤<5%",
            np.where(max_dd > -self.cfg.e_max_drawdown_20d, f"回撤可控",
                     "回撤过大"))

        # E4: 阶梯式放量
        vol_5 = pool_df.get('vol_ma_5', pd.Series(0, index=pool_df.index)).fillna(0).values
        vol_20 = pool_df.get('vol_ma_20', pd.Series(0, index=pool_df.index)).fillna(0).values
        vol_60 = pool_df.get('vol_ma_60', pd.Series(0, index=pool_df.index)).fillna(0).values
        staircase = (vol_5 > vol_20 * self.cfg.e_volume_staircase_min) & \
                    (vol_20 > vol_60 * self.cfg.e_volume_staircase_min) & \
                    (vol_60 > 0)
        e4_pass = staircase
        e4_score = np.where(staircase, 1.0,
                   np.where(vol_5 > vol_20, 0.5, 0.0))
        scores['e4_volume_staircase'] = e4_score
        details_map['e4_volume_staircase'] = np.where(
            staircase, "阶梯放量",
            np.where(vol_5 > vol_20, "短期放量", "量能不足"))

        # E5: 动量一致性
        ret_10 = pool_df.get('pct_chg_10d', pd.Series(0, index=pool_df.index)).fillna(0).values
        ret_20 = pool_df.get('pct_chg_20d', pd.Series(0, index=pool_df.index)).fillna(0).values
        ret_60 = pool_df.get('pct_chg_60d', pd.Series(0, index=pool_df.index)).fillna(0).values
        positive_count = ((ret_10 > 0).astype(int) + (ret_20 > 0).astype(int) + (ret_60 > 0).astype(int))
        e5_pass = positive_count >= self.cfg.e_momentum_positive_min
        e5_score = positive_count / 3.0
        scores['e5_momentum'] = e5_score
        details_map['e5_momentum'] = np.where(
            e5_pass, "多周期动量一致",
            np.where(positive_count > 0, "部分动量正", "动量不足"))

        # E6: ADX 趋势强度
        adx = pool_df.get('adx_14', pd.Series(0, index=pool_df.index)).fillna(0).values
        e6_pass = adx > self.cfg.e_adx_threshold
        e6_score = np.minimum(1.0, adx / 50.0)
        scores['e6_adx'] = e6_score
        details_map['e6_adx'] = np.where(
            e6_pass,
            np.array([f"趋势强(ADX={v:.0f})" for v in adx]),
            np.array([f"趋势弱(ADX={v:.0f})" for v in adx]))

        # E7: RSI 区间
        rsi = pool_df.get('rsi_14', pd.Series(50, index=pool_df.index)).fillna(50).values
        e7_pass = (rsi > self.cfg.e_rsi_range_min) & (rsi < self.cfg.e_rsi_range_max)
        e7_score = np.where(e7_pass, 1.0,
                   np.where(rsi > 80, 0.2,
                   np.where(rsi < 45, 0.3, 0.6)))
        scores['e7_rsi'] = e7_score
        details_map['e7_rsi'] = np.where(
            e7_pass,
            np.array([f"RSI健康({v:.0f})" for v in rsi]),
            np.where(rsi > 80,
                     np.array([f"超买(RSI={v:.0f})" for v in rsi]),
                     np.array([f"偏弱(RSI={v:.0f})" for v in rsi])))

        # E8: 趋势持续时间
        above_ma20_days = pool_df.get('above_ma20_days', pd.Series(0, index=pool_df.index)).fillna(0).values
        e8_pass = above_ma20_days >= self.cfg.e_trend_duration_min
        e8_score = np.minimum(1.0, above_ma20_days / 30.0)
        scores['e8_trend_duration'] = e8_score
        details_map['e8_trend_duration'] = np.where(
            e8_pass,
            np.array([f"趋势持续{v:.0f}日" for v in above_ma20_days]),
            np.array([f"趋势仅{v:.0f}日" for v in above_ma20_days]))

        # E9: 蓄势信号（主升浪前兆）
        consol_days = pool_df.get('consolidation_days', pd.Series(0, index=pool_df.index)).fillna(0).values
        vol_dry = pool_df.get('volume_drying_days', pd.Series(0, index=pool_df.index)).fillna(0).values
        squeeze = pool_df.get('price_squeeze_days', pd.Series(0, index=pool_df.index)).fillna(0).values
        ma_conv = pool_df.get('ma_converging_score', pd.Series(0, index=pool_df.index)).fillna(0).values
        breakout = pool_df.get('breakout_score', pd.Series(0, index=pool_df.index)).fillna(0).values
        e9_pass = (consol_days >= 5) | ((vol_dry >= 3) & (ma_conv > 0.5)) | (breakout > 0.5)
        e9_score = np.where(e9_pass, 1.0,
                   np.where((consol_days >= 3) | (vol_dry >= 2) | (breakout > 0), 0.5, 0.0))
        scores['e9_accumulation'] = e9_score
        details_map['e9_accumulation'] = np.where(
            breakout > 0.5, "放量突破",
            np.where(e9_pass, "蓄势充分",
            np.where(consol_days >= 3, "蓄势中", "无蓄势")))

        # 综合评分
        weights = {
            'e1_ma_alignment': 1.0,
            'e2_price_stability': 0.8,
            'e3_drawdown': 1.2,
            'e4_volume_staircase': 0.8,
            'e5_momentum': 1.0,
            'e6_adx': 0.8,
            'e7_rsi': 0.5,
            'e8_trend_duration': 1.0,
            'e9_accumulation': 2.0,
        }

        total_score = np.zeros(len(pool_df))
        for key, weight in weights.items():
            total_score += scores[key] * weight

        # 量价背离惩罚：价格在MA20上方但5日均量<20日均量的70%，扣分
        vol_5_vals = pool_df.get('vol_ma_5', pd.Series(0, index=pool_df.index)).fillna(0).values
        vol_20_vals = pool_df.get('vol_ma_20', pd.Series(0, index=pool_df.index)).fillna(0).values
        above_ma20_vals = (close > pool_df.get('ma_20', pd.Series(0, index=pool_df.index)).fillna(0).values)
        vol_divergence = above_ma20_vals & (vol_20_vals > 0) & (vol_5_vals < vol_20_vals * 0.7)
        total_score = np.where(vol_divergence, total_score - 1.5, total_score)

        # 通过条件：(E1或E5通过) 或 (E9蓄势通过且E3回撤可控) + 总分 > 阈值
        trend_confirmed = (e1_pass | e5_pass)
        early_accumulation = e9_pass & e3_pass
        passed = (trend_confirmed | early_accumulation) & (total_score > 3.5)

        pool_df['e_total_score'] = total_score
        pool_df['e_passed'] = passed

        passed_df = pool_df[pool_df['e_passed']].sort_values('e_total_score', ascending=False)
        top = passed_df.head(top_n)

        results = []
        for i in range(len(top)):
            idx = top.index[i]
            code = top.iloc[i]['ts_code']
            score = float(top.iloc[i]['e_total_score'])

            sig = TrendSignal(
                ts_code=code,
                eval_date=eval_date,
                score=score,
                factors={k: float(v[idx]) if idx < len(v) else 0 for k, v in scores.items()},
                details={k: str(v[idx]) if idx < len(v) else "" for k, v in details_map.items()},
                passed=True,
                trend_type="sustained",
            )
            results.append(sig)

        logger.info(f"E层向量化扫描 {len(pool)} 只，通过 {len(passed_df)} 只，输出 Top {len(results)} 只")
        return results

    def _scan_fallback(self, pool: Set[str], eval_date: str,
                       top_n: int = 20) -> List[TrendSignal]:
        results = []
        for code in pool:
            sig = self.evaluate(code, eval_date)
            if sig.passed:
                results.append(sig)

        results.sort(key=lambda x: x.score, reverse=True)
        top = results[:top_n]
        logger.info(f"E层扫描 {len(pool)} 只，通过 {len(results)} 只，输出 Top {len(top)} 只")
        return top

    def evaluate(self, ts_code: str, eval_date: str) -> TrendSignal:
        sig = TrendSignal(ts_code=ts_code, eval_date=eval_date)

        df = self.loader.get_daily(ts_code, start_date="2020-01-01", end_date=eval_date, min_days=10)
        if df is None or len(df) < 10:
            sig.details["error"] = f"数据不足(需10日,仅有{len(df) if df is not None else 0}日)"
            return sig

        df = df.reset_index(drop=True)
        close = df['close'].astype(float)
        volume = df['volume'].astype(float)
        pct_chg = df['pct_chg'].astype(float)
        n = len(df)
        has_ma60 = n >= 60

        scores = {}
        details = {}
        passed_count = 0

        ma5 = close.rolling(5).mean()
        ma10 = close.rolling(10).mean()
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean() if has_ma60 else None

        # E1: 均线多头排列
        if has_ma60:
            alignment = (ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60)
        else:
            alignment = (ma5 > ma10) & (ma10 > ma20)
        alignment_days = alignment.iloc[-min(self.cfg.e_ma_alignment_days, n):].sum()
        e1_pass = alignment.iloc[-1] and alignment_days >= min(self.cfg.e_ma_alignment_days, n) * 0.6
        scores['e1_ma_alignment'] = 1.0 if e1_pass else (0.5 if alignment.iloc[-1] else 0)
        detail_suffix = "" if has_ma60 else "(短模式)"
        details['e1_ma_alignment'] = f"多头{alignment_days:.0f}日{detail_suffix}"
        if e1_pass:
            passed_count += 1

        # E2: 价格站稳短期均线
        last_close = float(close.iloc[-1])
        last_ma5 = float(ma5.iloc[-1]) if pd.notna(ma5.iloc[-1]) else 0
        if last_ma5 > 0:
            deviation = (last_close - last_ma5) / last_ma5
            e2_pass = -0.03 < deviation < self.cfg.e_price_above_short_ma_pct
            scores['e2_price_stability'] = 1.0 if e2_pass else (0.5 if last_close > last_ma5 else 0)
            details['e2_price_stability'] = f"偏离MA5={deviation*100:.1f}%"
        else:
            e2_pass = False
            scores['e2_price_stability'] = 0
            details['e2_price_stability'] = "MA5不可用"
        if e2_pass:
            passed_count += 1

        # E3: 回撤控制
        lookback = min(20, n)
        recent_high = close.iloc[-lookback:].max()
        recent_low = close.iloc[-lookback:].min()
        max_dd = (recent_low - recent_high) / recent_high if recent_high > 0 else 0
        e3_pass = -self.cfg.e_max_drawdown_20d < max_dd < 0
        scores['e3_drawdown'] = 1.0 if max_dd > -0.05 else (0.7 if max_dd > -self.cfg.e_max_drawdown_20d else 0.3)
        details['e3_drawdown'] = f"{lookback}日最大回撤={max_dd*100:.1f}%"
        if e3_pass:
            passed_count += 1

        # E4: 阶梯式放量
        vol_ma5 = volume.iloc[-5:].mean()
        vol_ma20 = volume.iloc[-min(20, n):].mean()
        vol_ma60 = volume.iloc[-min(60, n):].mean() if n >= 20 else 0
        if has_ma60 and vol_ma60 > 0:
            staircase = (vol_ma5 > vol_ma20 * self.cfg.e_volume_staircase_min) and \
                        (vol_ma20 > vol_ma60 * self.cfg.e_volume_staircase_min)
            scores['e4_volume_staircase'] = 1.0 if staircase else (0.5 if vol_ma5 > vol_ma20 else 0)
            details['e4_volume_staircase'] = f"5/20/60均量比={vol_ma5/vol_ma20:.1f}x/{vol_ma20/vol_ma60:.1f}x"
        else:
            staircase = vol_ma5 > vol_ma20 * self.cfg.e_volume_staircase_min
            scores['e4_volume_staircase'] = 0.8 if staircase else (0.4 if vol_ma5 > vol_ma20 else 0)
            details['e4_volume_staircase'] = f"5/20均量比={vol_ma5/vol_ma20:.1f}x(短模式)"
        if staircase:
            passed_count += 1

        # E5: 动量一致性
        ret_10 = float(pct_chg.iloc[-10:].sum()) if n >= 10 else 0
        ret_20 = float(pct_chg.iloc[-20:].sum()) if n >= 20 else 0
        ret_60 = float(pct_chg.iloc[-60:].sum()) if n >= 60 else 0
        momentum_list = [r for r in [ret_10, ret_20, ret_60] if r != 0 or n >= 10]
        positive_count = sum(1 for r in [ret_10, ret_20, ret_60] if r > 0)
        total_periods = 3 if has_ma60 else (2 if n >= 20 else 1)
        e5_pass = positive_count >= min(self.cfg.e_momentum_positive_min, total_periods)
        scores['e5_momentum'] = positive_count / 3.0
        details['e5_momentum'] = f"10/20/60日涨幅={ret_10:.1f}%/{ret_20:.1f}%/{ret_60:.1f}%"
        if e5_pass:
            passed_count += 1

        # E6: ADX (需要至少30日数据)
        if n >= 30:
            adx_val = self._calc_adx(df, period=14)
            e6_pass = adx_val > self.cfg.e_adx_threshold
            scores['e6_adx'] = min(1.0, adx_val / 50.0)
            details['e6_adx'] = f"ADX={adx_val:.1f}"
            if e6_pass:
                passed_count += 1
        else:
            scores['e6_adx'] = 0.5
            details['e6_adx'] = "数据不足(短模式默认0.5)"

        # E7: RSI
        if n >= 20:
            rsi_val = self._calc_rsi(close, period=14)
            e7_pass = self.cfg.e_rsi_range_min < rsi_val < self.cfg.e_rsi_range_max
            scores['e7_rsi'] = 1.0 if e7_pass else (0.3 if rsi_val > 80 or rsi_val < 45 else 0.6)
            details['e7_rsi'] = f"RSI={rsi_val:.1f}"
            if e7_pass:
                passed_count += 1
        else:
            scores['e7_rsi'] = 0.5
            details['e7_rsi'] = "数据不足(短模式默认0.5)"

        # E8: 趋势持续时间
        above_ma20 = close > ma20
        above_ma20_days = 0
        for i in range(len(above_ma20) - 1, -1, -1):
            if above_ma20.iloc[i]:
                above_ma20_days += 1
            else:
                break
        e8_pass = above_ma20_days >= self.cfg.e_trend_duration_min
        scores['e8_trend_duration'] = min(1.0, above_ma20_days / 30.0)
        details['e8_trend_duration'] = f"站上MA20共{above_ma20_days}日"
        if e8_pass:
            passed_count += 1

        weights = {
            'e1_ma_alignment': 1.5, 'e2_price_stability': 1.0,
            'e3_drawdown': 1.2, 'e4_volume_staircase': 1.0,
            'e5_momentum': 1.5, 'e6_adx': 1.0,
            'e7_rsi': 0.8, 'e8_trend_duration': 1.5,
        }

        sig.factors = scores
        sig.details = details
        sig.score = sum(scores.get(k, 0) * w for k, w in weights.items())
        min_pass_count = 3 if has_ma60 else 2
        sig.passed = (e1_pass or e5_pass) and passed_count >= min_pass_count and sig.score > 3.5

        return sig

    @staticmethod
    def _calc_rsi(close: pd.Series, period: int = 14) -> float:
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0

    @staticmethod
    def _calc_adx(df: pd.DataFrame, period: int = 14) -> float:
        high = df['high'].astype(float)
        low = df['low'].astype(float)
        close = df['close'].astype(float)

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        plus_dm = high.diff()
        minus_dm = low.diff().abs()

        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

        atr = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).mean() / atr.replace(0, np.nan))
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, np.nan))

        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
        adx = dx.rolling(period).mean()

        return float(adx.iloc[-1]) if pd.notna(adx.iloc[-1]) else 0.0
