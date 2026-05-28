"""
融合元策略 - Baostock回测引擎 v2.0
====================================
使用 baostock 在线数据源，不依赖 PostgreSQL。
严格 PIT 回测：
  - T 日收盘后产生信号（七层漏斗 v2.0）
  - T+1 日开盘买入
  - 持仓管理模块每日评估退出（含 Layer6 持续性评估）
  - T+N 日开盘卖出

v2.0 升级：
  - Layer 0: 大盘风控 + 市场状态识别（牛市/震荡/熊市）+ 动态权重
  - Layer 1: 多因子扫描 + 行业轮动因子
  - Layer 3: 启动信号 + 封单质量 + 主力资金代理
  - Layer 5: 八步法评分 + 双池分治 + 情绪感知 + 行业评分
  - Layer 6: 持续性评估（新增）
  - 归一化: 动态权重 + 持续性调节 + LLM否决
  - 策略对比回测: 单独策略 vs 融合策略

输出：胜率、平均收益、最大回撤、退出原因分布、各层漏斗通过率、策略对比
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from strategies.meta_strategy.baostock_data import (
    ensure_login, logout, ts_to_bs, bs_to_ts,
    get_trading_days, get_daily_quotes, get_daily_quotes_cached,
    get_market_overview, get_active_stocks, get_fundamental_data,
    clear_cache,
)
from strategies.meta_strategy.position_manager import (
    PositionManager, PositionManagerConfig, DEFAULT_PM_CONFIG, Position,
)

logger = logging.getLogger(__name__)
BEIJING_TZ = timezone(timedelta(hours=8))


# ============================================================
# 配置
# ============================================================

@dataclass
class MetaBacktestConfig:
    """回测配置 v2.0"""
    start_date: str = "2025-06-01"
    end_date: str = "2026-05-15"
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.001
    slippage_pct: float = 0.001
    max_positions: int = 5
    single_position_pct: float = 0.20

    # Layer 0: 大盘风控 + 市场状态
    layer0_enabled: bool = True
    layer0_min_advancers_ratio: float = 0.50
    layer0_regime_lookback: int = 60
    layer0_bull_threshold: float = 0.03
    layer0_bear_threshold: float = -0.05

    # Layer 1: 多因子扫描
    layer1_enabled: bool = True
    layer1_min_total_score: float = 0.40
    layer1_top_n: int = 50
    layer1_industry_rotation: bool = True

    # Layer 2: 基本面+流动性
    layer2_enabled: bool = True
    layer2_min_amount: float = 5e7
    layer2_max_debt_ratio: float = 65.0
    layer2_min_current_ratio: float = 1.0

    # Layer 3: 启动信号
    layer3_enabled: bool = True
    layer3_volume_breakout_mult: float = 2.0
    layer3_price_breakout_pct: float = 0.03
    layer3_seal_quality_min: float = 0.6
    layer3_min_launch_score: float = 0.3

    # Layer 4: LLM事件加成（回测中简化为基本面代理）
    layer4_enabled: bool = False
    layer4_simulated_bonus_range: Tuple[float, float] = (0, 10)

    # Layer 5: 八步法精细评分
    layer5_enabled: bool = True
    layer5_pct_range_low: float = 2.0
    layer5_pct_range_high: float = 7.0
    layer5_vol_ratio_min: float = 1.5
    layer5_stable_pool_pct_max: float = 5.0
    layer5_upper_pool_pct_max: float = 9.5
    layer5_sentiment_enabled: bool = True
    layer5_industry_score_enabled: bool = True

    # Layer 6: 持续性评估
    layer6_enabled: bool = True
    layer6_adx_trend_min: float = 20.0
    layer6_sustain_score_min: float = 0.3

    # 动态权重
    weights_bull: Dict[str, float] = field(default_factory=lambda: {
        'factor': 0.35, 'launch': 0.25, 'llm': 0.10, 'overnight': 0.30
    })
    weights_oscillate: Dict[str, float] = field(default_factory=lambda: {
        'factor': 0.25, 'launch': 0.15, 'llm': 0.20, 'overnight': 0.40
    })
    weights_bear: Dict[str, float] = field(default_factory=lambda: {
        'factor': 0.20, 'launch': 0.10, 'llm': 0.25, 'overnight': 0.45
    })

    # 策略对比
    strategy_compare_enabled: bool = True

    # 输出
    output_dir: str = './results'
    verbose: bool = True


DEFAULT_BT_CONFIG = MetaBacktestConfig()


# ============================================================
# 技术指标计算
# ============================================================

def _ema(arr: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1)
    out = np.empty(len(arr), dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def compute_factors(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                   amount: np.ndarray) -> Dict:
    """计算7+1个技术因子得分（含行业轮动代理）"""
    n = len(close)
    if n < 30:
        return {'total_score': 0}

    r = {}

    # 1. 动量 (20日涨幅)
    mom = float((close[-1] - close[-21]) / (close[-21] + 1e-9)) if n >= 21 else 0
    r['momentum'] = round(mom, 4)
    r['momentum_score'] = round(min(max(mom / 0.15, 0), 1.0) if mom > 0 else 0.0, 3)

    # 2. 量比
    vol_mean = float(amount[-21:-1].mean()) if n >= 21 else float(amount.mean())
    vr = float(amount[-1]) / (vol_mean + 1e-9)
    r['volume_ratio'] = round(vr, 2)
    cw = 10
    if n >= cw:
        cp = close[-cw:] - close[-cw:].mean()
        cv = amount[-cw:] - amount[-cw:].mean()
        pv_corr = float((cp * cv).sum() / (np.sqrt((cp**2).sum() * (cv**2).sum()) + 1e-9))
    else:
        pv_corr = 0.0
    r['pv_corr'] = round(pv_corr, 3)
    r['volume_score'] = round(
        (min((vr - 1.5) / 8.5, 1.0) * 0.7 + min(pv_corr, 1.0) * 0.3)
        if 1.5 <= vr <= 10.0 and pv_corr > 0 else 0.0, 3)

    # 3. RSI(6)
    d = np.diff(close)
    ag = _ema(np.where(d > 0, d, 0.0), 6)
    al = _ema(np.where(d < 0, -d, 0.0), 6)
    rsi = float(100 - 100 / (1 + ag[-1] / (al[-1] + 1e-9)))
    r['rsi'] = round(rsi, 1)
    r['rsi_score'] = (0.0 if rsi >= 75 else 1.0 if rsi <= 35 else round((75 - rsi) / 40, 3))

    # 4. MACD
    dif = _ema(close, 12) - _ema(close, 26)
    dea = _ema(dif, 9)
    hist = (dif - dea) * 2
    ld, la = float(dif[-1]), float(dea[-1])
    lh, ph = float(hist[-1]), float(hist[-2]) if n > 1 else 0
    r['macd_dif'] = round(ld, 4)
    r['macd_dea'] = round(la, 4)
    if ld > la and ph <= 0 and lh > 0:
        r['macd_score'] = 1.0; r['macd_signal'] = '金叉'
    elif ld > la and lh > 0 and lh > ph:
        r['macd_score'] = 0.7; r['macd_signal'] = '多头'
    elif ld > la:
        r['macd_score'] = 0.4; r['macd_signal'] = 'DIF>DEA'
    else:
        r['macd_score'] = 0.1; r['macd_signal'] = '空头'

    # 5. EMA排列
    e5 = float(_ema(close, 5)[-1])
    e10 = float(_ema(close, 10)[-1])
    e20 = float(_ema(close, 20)[-1])
    lc = float(close[-1])
    if lc > e5 > e10 > e20:
        r['ema_score'] = 1.0; r['ema_signal'] = '完美多头'
    elif lc > e10 > e20:
        r['ema_score'] = 0.7; r['ema_signal'] = '中期多头'
    elif lc > e20:
        r['ema_score'] = 0.4; r['ema_signal'] = '站上均线'
    else:
        r['ema_score'] = 0.0; r['ema_signal'] = '空头排列'

    # 6. ADX
    p = 14
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    up = high[1:] - high[:-1]; dn = low[:-1] - low[1:]
    atr = _ema(tr, p)
    pdi = 100 * _ema(np.where((up > dn) & (up > 0), up, 0.0), p) / (atr + 1e-9)
    mdi = 100 * _ema(np.where((dn > up) & (dn > 0), dn, 0.0), p) / (atr + 1e-9)
    adx_v = float(_ema(100 * np.abs(pdi - mdi) / (pdi + mdi + 1e-9), p)[-1])
    lpdi = float(pdi[-1]); lmdi = float(mdi[-1])
    r['plus_di'] = round(lpdi, 1); r['minus_di'] = round(lmdi, 1)
    r['adx'] = round(adx_v, 1)
    if adx_v >= 20 and lpdi > lmdi:
        r['adx_score'] = round(min(adx_v / 50, 1.0), 3)
        r['adx_signal'] = f'强趋势(ADX={adx_v:.0f})'
    elif lpdi > lmdi:
        r['adx_score'] = 0.3; r['adx_signal'] = f'弱趋势(ADX={adx_v:.0f})'
    else:
        r['adx_score'] = 0.0; r['adx_signal'] = f'偏空(ADX={adx_v:.0f})'

    # 7. SAR
    af_i, af_m = 0.02, 0.2
    sar = np.zeros(n); trend = 1; ep = high[0]; af = af_i; sar[0] = low[0]
    for i in range(1, n):
        ps = sar[i-1]
        if trend == 1:
            sar[i] = min(ps + af * (ep - ps), low[i-1], low[max(0, i-2)])
            if low[i] < sar[i]:
                trend = -1; sar[i] = ep; ep = low[i]; af = af_i
            elif high[i] > ep:
                ep = high[i]; af = min(af + af_i, af_m)
        else:
            sar[i] = max(ps + af * (ep - ps), high[i-1], high[max(0, i-2)])
            if high[i] > sar[i]:
                trend = 1; sar[i] = ep; ep = high[i]; af = af_i
            elif low[i] < ep:
                ep = low[i]; af = min(af + af_i, af_m)
    ls = float(sar[-1])
    r['sar'] = round(ls, 2)
    if ls < lc:
        sd = (lc - ls) / lc
        r['sar_score'] = round(min(1.0, max(0.3, 1.0 - sd * 10)), 3)
        r['sar_signal'] = f'SAR支撑({sd*100:.1f}%)'
    else:
        r['sar_score'] = 0.0; r['sar_signal'] = 'SAR压制'

    # 8. 行业轮动代理 (v2.0)
    if n >= 6:
        stock_5d_ret = (close[-1] - close[-6]) / (close[-6] + 1e-9)
        r['industry_rotation_score'] = round(
            min(max(stock_5d_ret / 0.10, 0), 1.0) if stock_5d_ret > 0 else 0.0, 3)
    else:
        r['industry_rotation_score'] = 0.0

    # 加权总分 (v2.0: 行业轮动占10%)
    r['total_score'] = round(
        r['momentum_score'] * 0.18 + r['volume_score'] * 0.18 +
        r['rsi_score'] * 0.13 + r['macd_score'] * 0.13 +
        r['ema_score'] * 0.13 + r['adx_score'] * 0.10 +
        r['sar_score'] * 0.05 + r['industry_rotation_score'] * 0.10, 4)

    return r
