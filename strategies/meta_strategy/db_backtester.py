"""
融合元策略 - 数据库回测引擎 v2.0
====================================
使用 PostgreSQL 数据库数据源，批量SQL查询替代逐个API调用。
严格 PIT 回测：
  - T 日收盘后产生信号（七层漏斗 v2.0）
  - T+1 日开盘买入
  - 持仓管理模块每日评估退出（含 Layer6 持续性评估）
  - T+N 日开盘卖出

核心优化：
  - 批量SQL查询：一次获取所有股票的日线数据
  - 预加载：交易日和指数数据提前加载
  - 按日批量处理：每天一次SQL获取全市场数据
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

from strategies.meta_strategy.db_data_adapter import (
    get_trading_days, get_active_stocks, get_market_overview,
    get_daily_quotes_batch, get_daily_quotes_for_date,
    get_index_quotes, get_fundamental_batch, clear_cache,
)
from strategies.meta_strategy.position_manager import (
    PositionManager, PositionManagerConfig, DEFAULT_PM_CONFIG, Position,
)

logger = logging.getLogger(__name__)
BEIJING_TZ = timezone(timedelta(hours=8))


# ============================================================
# 配置（复用 baostock_backtester 的配置）
# ============================================================

@dataclass
class MetaBacktestConfig:
    """回测配置 v2.0"""
    start_date: str = "2025-06-01"
    end_date: str = "2026-05-22"
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.001
    slippage_pct: float = 0.001
    max_positions: int = 3
    single_position_pct: float = 0.30

    # Layer 0
    layer0_enabled: bool = True
    layer0_min_advancers_ratio: float = 0.30  # 降低门槛，弱市也允许选股
    layer0_regime_lookback: int = 60
    layer0_bull_threshold: float = 0.03
    layer0_bear_threshold: float = -0.05

    # Layer 1
    layer1_enabled: bool = True
    layer1_min_total_score: float = 0.40  # 提高门槛，精选候选
    layer1_top_n: int = 30
    layer1_industry_rotation: bool = True

    # Layer 2
    layer2_enabled: bool = True
    layer2_min_amount: float = 1e8  # 提高最低成交额，排除小盘股
    layer2_max_debt_ratio: float = 65.0
    layer2_min_current_ratio: float = 1.0

    # Layer 3
    layer3_enabled: bool = True
    layer3_volume_breakout_mult: float = 2.0
    layer3_price_breakout_pct: float = 0.03
    layer3_seal_quality_min: float = 0.6
    layer3_min_launch_score: float = 0.15  # 降低门槛，更多启动信号

    # Layer 4
    layer4_enabled: bool = False
    layer4_simulated_bonus_range: Tuple[float, float] = (0, 10)

    # Layer 5
    layer5_enabled: bool = True
    layer5_pct_range_low: float = 2.0
    layer5_pct_range_high: float = 7.0
    layer5_vol_ratio_min: float = 1.5
    layer5_stable_pool_pct_max: float = 5.0
    layer5_upper_pool_pct_max: float = 9.5
    layer5_sentiment_enabled: bool = True
    layer5_industry_score_enabled: bool = True

    # Layer 6
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

    output_dir: str = './results'
    verbose: bool = True


DEFAULT_BT_CONFIG = MetaBacktestConfig()


# ============================================================
# 技术指标计算（复用）
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
    """计算7+1个技术因子得分"""
    n = len(close)
    if n < 30:
        return {'total_score': 0}

    r = {}

    # 1. 动量
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

    # 8. 行业轮动代理
    if n >= 6:
        stock_5d_ret = (close[-1] - close[-6]) / (close[-6] + 1e-9)
        r['industry_rotation_score'] = round(
            min(max(stock_5d_ret / 0.10, 0), 1.0) if stock_5d_ret > 0 else 0.0, 3)
    else:
        r['industry_rotation_score'] = 0.0

    r['total_score'] = round(
        r['momentum_score'] * 0.18 + r['volume_score'] * 0.18 +
        r['rsi_score'] * 0.13 + r['macd_score'] * 0.13 +
        r['ema_score'] * 0.13 + r['adx_score'] * 0.10 +
        r['sar_score'] * 0.05 + r['industry_rotation_score'] * 0.10, 4)

    return r


# ============================================================
# 七层漏斗（数据库优化版 - 批量查询）
# ============================================================

def layer0_market_risk(trade_date: date, cfg: MetaBacktestConfig,
                       index_data: pd.DataFrame = None) -> Dict:
    """Layer 0: 大盘风控 + 市场状态识别"""
    if not cfg.layer0_enabled:
        return {'passed': True, 'position_cap': 1.0, 'regime': 'oscillate',
                'regime_score': 0.0, 'weights': cfg.weights_oscillate}

    overview = get_market_overview(trade_date)
    ratio = overview['breadth_ratio']
    passed = ratio >= cfg.layer0_min_advancers_ratio

    regime = 'oscillate'
    regime_score = 0.0
    if index_data is not None and not index_data.empty and len(index_data) >= 20:
        closes = index_data['close'].values.astype(float)
        lookback = min(cfg.layer0_regime_lookback, len(closes) - 1)
        ret = (closes[-1] - closes[-lookback - 1]) / (closes[-lookback - 1] + 1e-9)
        if ret >= cfg.layer0_bull_threshold:
            regime = 'bull'; regime_score = min(1.0, ret / 0.10)
        elif ret <= cfg.layer0_bear_threshold:
            regime = 'bear'; regime_score = max(-1.0, ret / 0.10)
        else:
            regime = 'oscillate'; regime_score = ret / 0.05

    weights = cfg.weights_bull if regime == 'bull' else (
        cfg.weights_bear if regime == 'bear' else cfg.weights_oscillate)
    position_cap = 1.0 if passed else 0.5
    if regime == 'bear' and position_cap > 0.3:
        position_cap = 0.3

    return {
        'passed': passed, 'position_cap': position_cap,
        'advancers': overview['advancers'], 'decliners': overview['decliners'],
        'breadth_ratio': round(ratio, 4), 'regime': regime,
        'regime_score': round(regime_score, 3), 'weights': weights,
        'reason': '' if passed else f'上涨占比{ratio:.1%}<{cfg.layer0_min_advancers_ratio:.0%}',
    }


def layer1_multi_factor_scan(trade_date: date, cfg: MetaBacktestConfig,
                              daily_df: pd.DataFrame = None,
                              history_data: Dict[str, pd.DataFrame] = None) -> pd.DataFrame:
    """Layer 1: 多因子扫描（数据库优化版 - 使用预加载数据）"""
    if not cfg.layer1_enabled:
        return pd.DataFrame()

    # 使用传入的当日数据或查询
    if daily_df is None:
        daily_df = get_daily_quotes_for_date(trade_date, min_amount=cfg.layer2_min_amount)

    if daily_df.empty:
        return pd.DataFrame()

    # 快速预筛
    prefiltered = daily_df[
        (daily_df['pct_chg'] > -5) & (daily_df['close'] > 0)
    ]['ts_code'].tolist()

    if not prefiltered:
        return pd.DataFrame()

    # 批量获取历史数据
    if history_data is None:
        start_date = trade_date - timedelta(days=150)
        history_data = get_daily_quotes_batch(prefiltered, start_date, trade_date)

    results = []
    for ts_code in prefiltered:
        df = history_data.get(ts_code)
        if df is None or len(df) < 30:
            continue

        close = df['close'].values.astype(float)
        high = df['high'].values.astype(float)
        low = df['low'].values.astype(float)
        amount = df['amount'].values.astype(float) if 'amount' in df.columns else np.zeros(len(df))
        if close[-1] <= 0:
            continue

        factors = compute_factors(close, high, low, amount)
        if factors['total_score'] >= cfg.layer1_min_total_score:
            factors['ts_code'] = ts_code
            factors['close'] = round(float(close[-1]), 2)
            results.append(factors)

    if not results:
        return pd.DataFrame()

    df_result = pd.DataFrame(results)
    df_result = df_result.sort_values('total_score', ascending=False)
    if len(df_result) > cfg.layer1_top_n:
        df_result = df_result.head(cfg.layer1_top_n)
    return df_result.reset_index(drop=True)


def layer2_fundamental_filter(stock_list: List[str], trade_date: date,
                               cfg: MetaBacktestConfig,
                               daily_df: pd.DataFrame = None,
                               fund_data: Dict[str, Dict] = None) -> List[str]:
    """Layer 2: 基本面+流动性过滤（数据库优化版）"""
    if not cfg.layer2_enabled:
        return stock_list

    # 使用当日数据中的PE/PB
    if daily_df is not None and not daily_df.empty:
        daily_pe = dict(zip(daily_df['ts_code'], daily_df.get('pe_ratio', pd.Series())))
        daily_pb = dict(zip(daily_df['ts_code'], daily_df.get('pb_ratio', pd.Series())))
    else:
        daily_pe = {}; daily_pb = {}

    # 批量获取基本面
    if fund_data is None:
        fund_data = get_fundamental_batch(stock_list)

    passed = []
    for ts_code in stock_list:
        # PE/PB过滤
        pe = daily_pe.get(ts_code)
        if pe is not None and not np.isnan(pe) and pe < 0:
            continue  # 亏损股
        pb = daily_pb.get(ts_code)
        if pb is not None and not np.isnan(pb) and pb < 0:
            continue  # 破净股

        # 基本面过滤
        fund = fund_data.get(ts_code, {})
        debt_ratio = fund.get('debt_ratio')
        if debt_ratio is not None and debt_ratio > cfg.layer2_max_debt_ratio:
            continue
        current_ratio = fund.get('current_ratio')
        if current_ratio is not None and current_ratio < cfg.layer2_min_current_ratio:
            continue
        net_margin = fund.get('net_margin')
        if net_margin is not None and net_margin < -10:
            continue
        passed.append(ts_code)

    return passed


def layer3_launch_signals(stock_list: List[str], trade_date: date,
                           cfg: MetaBacktestConfig,
                           history_data: Dict[str, pd.DataFrame] = None,
                           daily_df: pd.DataFrame = None) -> pd.DataFrame:
    """Layer 3: 启动信号识别 v2.0"""
    if not cfg.layer3_enabled:
        return pd.DataFrame({'ts_code': stock_list, 'launch_score': [0.5] * len(stock_list)})

    if history_data is None:
        start_date = trade_date - timedelta(days=150)
        history_data = get_daily_quotes_batch(stock_list, start_date, trade_date)

    results = []
    for ts_code in stock_list:
        df = history_data.get(ts_code)
        if df is None or len(df) < 5:
            # 数据不足，基于当日涨幅给基础分
            if daily_df is not None and not daily_df.empty:
                today = daily_df[daily_df['ts_code'] == ts_code]
                if not today.empty:
                    last_pct = float(today['pct_chg'].iloc[0]) if pd.notna(today['pct_chg'].iloc[0]) else 0
                    base_score = 0.1 if last_pct > 2 else 0
                    if base_score > 0:
                        results.append({
                            'ts_code': ts_code,
                            'launch_score': base_score,
                            'launch_signals': '涨幅基础分',
                        })
            continue

        close = df['close'].values.astype(float)
        amount = df['amount'].values.astype(float) if 'amount' in df.columns else np.zeros(len(df))
        pct_chg = df['pct_chg'].values.astype(float) if 'pct_chg' in df.columns else np.zeros(len(df))
        turnover = df['turnover_rate'].values.astype(float) if 'turnover_rate' in df.columns else np.zeros(len(df))
        n = len(close)

        launch_score = 0.0
        signals = []

        # 1. 放量突破
        if n >= 20:
            vol_ma20 = amount[-21:-1].mean()
            if vol_ma20 > 0:
                vol_ratio = amount[-1] / vol_ma20
                if vol_ratio >= cfg.layer3_volume_breakout_mult:
                    launch_score += 0.30; signals.append(f'放量{vol_ratio:.1f}倍')
                elif vol_ratio >= cfg.layer3_volume_breakout_mult * 0.7:
                    launch_score += 0.15

        # 2. 价格突破
        if n >= 20:
            high_20 = close[-21:-1].max()
            if close[-1] > high_20 * (1 + cfg.layer3_price_breakout_pct):
                launch_score += 0.25; signals.append('突破20日高点')

        # 3. MACD金叉
        if n >= 35:
            dif = _ema(close, 12) - _ema(close, 26)
            dea = _ema(dif, 9)
            if len(dif) >= 2 and dif[-2] <= dea[-2] and dif[-1] > dea[-1]:
                launch_score += 0.15; signals.append('MACD金叉')

        # 4. 主力资金代理
        if n >= 1:
            last_pct = float(pct_chg[-1]) if len(pct_chg) > 0 else 0
            last_turnover = float(turnover[-1]) if len(turnover) > 0 else 0
            if last_turnover > 5 and last_pct > 0 and n >= 20:
                vol_ma = amount[-21:-1].mean()
                if vol_ma > 0 and amount[-1] > vol_ma * 1.5:
                    main_force_score = min(1.0, (last_pct / 5.0) * (last_turnover / 8.0))
                    if main_force_score >= 0.05:
                        launch_score += 0.15; signals.append(f'主力资金{main_force_score:.2f}')

        # 5. 封单质量
        if n >= 1:
            last_pct = float(pct_chg[-1]) if len(pct_chg) > 0 else 0
            last_turnover = float(turnover[-1]) if len(turnover) > 0 else 0
            seal_quality = 0.0
            if 7.0 <= last_pct < 9.8:
                seal_quality = (0.8 if 3.0 <= last_turnover <= 15.0
                                else (0.5 if last_turnover > 15.0 else 0.3))
            elif last_pct >= 9.8:
                seal_quality = (1.0 if last_turnover < 5.0
                                else (0.9 if last_turnover < 10.0 else 0.6))
            if seal_quality >= cfg.layer3_seal_quality_min:
                launch_score += 0.15; signals.append(f'封单{seal_quality:.1f}')

        if launch_score >= cfg.layer3_min_launch_score:
            results.append({
                'ts_code': ts_code,
                'launch_score': round(min(launch_score, 1.0), 3),
                'launch_signals': ', '.join(signals) if signals else '',
            })

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results)


def layer4_llm_boost(stock_list: List[str], trade_date: date,
                      cfg: MetaBacktestConfig,
                      fund_data: Dict[str, Dict] = None) -> Dict[str, Dict]:
    """Layer 4: LLM事件驱动加成（基本面代理+否决）"""
    if not cfg.layer4_enabled:
        return {code: {'llm_bonus': 0.0, 'llm_veto': False, 'veto_reason': ''}
                for code in stock_list}

    if fund_data is None:
        fund_data = get_fundamental_batch(stock_list)

    boosts = {}
    for ts_code in stock_list:
        fund = fund_data.get(ts_code, {})
        roe = fund.get('roe') or 0
        rev_yoy = fund.get('revenue_yoy') or 0
        net_margin = fund.get('net_margin') or 0
        bonus = 0
        if roe and roe > 15: bonus += 5
        if rev_yoy and rev_yoy > 20: bonus += 5

        llm_veto = False; veto_reason = ''
        if net_margin < -20:
            llm_veto = True; veto_reason = 'LLM否决: 严重亏损'; bonus = 0

        boosts[ts_code] = {
            'llm_bonus': min(bonus, 15), 'llm_veto': llm_veto, 'veto_reason': veto_reason,
        }
    return boosts


def layer5_overnight_score(stock_list: List[str], trade_date: date,
                            cfg: MetaBacktestConfig,
                            history_data: Dict[str, pd.DataFrame] = None,
                            daily_df: pd.DataFrame = None) -> pd.DataFrame:
    """Layer 5: 八步法精细评分 v2.0（双池+情绪+行业）"""
    if not cfg.layer5_enabled:
        return pd.DataFrame({'ts_code': stock_list, 'overnight_score': [50] * len(stock_list), 'pool': ['stable'] * len(stock_list)})

    if history_data is None:
        start_date = trade_date - timedelta(days=60)
        history_data = get_daily_quotes_batch(stock_list, start_date, trade_date)

    # 情绪感知
    sentiment_score = 0.0
    if cfg.layer5_sentiment_enabled:
        overview = get_market_overview(trade_date)
        if overview['breadth_ratio'] > 0.6: sentiment_score = 5.0
        elif overview['breadth_ratio'] > 0.5: sentiment_score = 2.5

    results = []
    for ts_code in stock_list:
        df = history_data.get(ts_code)
        if df is None or len(df) < 2:
            # 数据不足，给基础分（基于当日数据）
            if daily_df is not None and not daily_df.empty:
                today = daily_df[daily_df['ts_code'] == ts_code]
                if not today.empty:
                    last_pct = float(today['pct_chg'].iloc[0]) if pd.notna(today['pct_chg'].iloc[0]) else 0
                    base_score = 10 if last_pct > 0 else 0
                    results.append({
                        'ts_code': ts_code, 'overnight_score': base_score,
                        'pool': 'stable', 'pct_chg': round(last_pct, 2),
                    })
            continue

        close = df['close'].values.astype(float)
        amount = df['amount'].values.astype(float) if 'amount' in df.columns else np.zeros(len(df))
        pct_chg = df['pct_chg'].values.astype(float) if 'pct_chg' in df.columns else np.zeros(len(df))
        n = len(close)

        score = 0.0
        last_pct = float(pct_chg[-1]) if len(pct_chg) > 0 else 0

        # 双池分类
        if last_pct <= cfg.layer5_stable_pool_pct_max: pool = 'stable'
        elif last_pct <= cfg.layer5_upper_pool_pct_max: pool = 'upper'
        else: pool = 'extreme'

        # 涨幅评分
        if pool == 'stable':
            if 2.0 <= last_pct <= 4.0: score += 30
            elif 4.0 < last_pct <= 5.0: score += 25
            elif 0 < last_pct < 2.0: score += 15
        elif pool == 'upper':
            if 5.0 < last_pct <= 7.0: score += 22
            elif 7.0 < last_pct <= 9.5: score += 12

        # 量比评分
        if n >= 20:
            vol_mean = amount[-21:-1].mean()
            if vol_mean > 0:
                vr = amount[-1] / vol_mean
                if cfg.layer5_vol_ratio_min <= vr <= 10: score += 25
                elif vr > 1: score += 10

        # MA5距离
        if n >= 5:
            ma5 = close[-5:].mean()
            dist = (close[-1] - ma5) / (ma5 + 1e-9)
            if 0 <= dist <= 0.03: score += 20
            elif dist < 0: score += 5

        # 连涨
        if n >= 3:
            up_days = sum(1 for i in range(-3, 0) if pct_chg[i] > 0)
            if up_days >= 2: score += 15

        # 换手率
        if 'turnover_rate' in df.columns:
            tr = float(df['turnover_rate'].iloc[-1])
            if 3 <= tr <= 15: score += 10

        # 情绪加成
        score += sentiment_score

        # 高位风险
        if pool == 'upper' and last_pct > 8.0: score -= 10
        elif pool == 'extreme': score -= 20

        results.append({
            'ts_code': ts_code, 'overnight_score': min(score, 120),
            'pool': pool, 'pct_chg': round(last_pct, 2),
        })

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results)


def layer6_sustain_eval(stock_list: List[str], trade_date: date,
                         cfg: MetaBacktestConfig,
                         history_data: Dict[str, pd.DataFrame] = None) -> pd.DataFrame:
    """Layer 6: 持续性评估 v2.0"""
    if not cfg.layer6_enabled:
        return pd.DataFrame({'ts_code': stock_list, 'sustain_score': [0.5] * len(stock_list)})

    if history_data is None:
        start_date = trade_date - timedelta(days=150)
        history_data = get_daily_quotes_batch(stock_list, start_date, trade_date)

    results = []
    for ts_code in stock_list:
        df = history_data.get(ts_code)
        if df is None or len(df) < 30:
            continue

        close = df['close'].values.astype(float)
        high = df['high'].values.astype(float)
        low = df['low'].values.astype(float)
        amount = df['amount'].values.astype(float) if 'amount' in df.columns else np.zeros(len(df))
        pct_chg = df['pct_chg'].values.astype(float) if 'pct_chg' in df.columns else np.zeros(len(df))
        n = len(close)

        sustain_score = 0.0

        # ADX趋势
        p = 14
        tr = np.maximum(high[1:] - low[1:],
                        np.maximum(np.abs(high[1:] - close[:-1]),
                                   np.abs(low[1:] - close[:-1])))
        up = high[1:] - high[:-1]; dn = low[:-1] - low[1:]
        atr = _ema(tr, p)
        pdi = 100 * _ema(np.where((up > dn) & (up > 0), up, 0.0), p) / (atr + 1e-9)
        mdi = 100 * _ema(np.where((dn > up) & (dn > 0), dn, 0.0), p) / (atr + 1e-9)
        adx_arr = _ema(100 * np.abs(pdi - mdi) / (pdi + mdi + 1e-9), p)
        adx_val = float(adx_arr[-1]); pdi_val = float(pdi[-1]); mdi_val = float(mdi[-1])

        if adx_val >= cfg.layer6_adx_trend_min and pdi_val > mdi_val:
            sustain_score += 0.30
        elif pdi_val > mdi_val:
            sustain_score += 0.15

        # 连涨天数
        consecutive_up = 0
        for i in range(-1, -min(n, 15), -1):
            if pct_chg[i] > 0: consecutive_up += 1
            else: break
        if consecutive_up <= 3: sustain_score += 0.15
        elif consecutive_up <= 7: sustain_score += 0.08
        else: sustain_score -= 0.10

        # 量能配合
        if n >= 10:
            up_mask = pct_chg[-10:] > 0; down_mask = pct_chg[-10:] < 0
            up_vol = amount[-10:][up_mask].mean() if up_mask.any() else 0
            down_vol = amount[-10:][down_mask].mean() if down_mask.any() else 1e-9
            if up_vol > down_vol * 1.2: sustain_score += 0.15
            elif up_vol > down_vol: sustain_score += 0.08
            else: sustain_score -= 0.05

        # 均线支撑
        if n >= 20:
            ma5 = close[-6:-1].mean() if n >= 6 else close.mean()
            ma10 = close[-11:-1].mean() if n >= 11 else close.mean()
            ma20 = close[-21:-1].mean() if n >= 21 else close.mean()
            last_close = float(close[-1])
            ma_support = sum(1 for m in [ma5, ma10, ma20] if last_close > m)
            if ma_support == 3: sustain_score += 0.10
            elif ma_support >= 2: sustain_score += 0.05
            else: sustain_score -= 0.05

        sustain_score = max(0, min(1, sustain_score))
        results.append({'ts_code': ts_code, 'sustain_score': round(sustain_score, 3)})

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results)


# ============================================================
# 融合评分归一化
# ============================================================

def normalize_and_fuse(factor_df: pd.DataFrame, launch_df: pd.DataFrame,
                       llm_data: Dict[str, Dict], overnight_df: pd.DataFrame,
                       sustain_df: pd.DataFrame = None,
                       weights: Dict[str, float] = None) -> pd.DataFrame:
    """统一评分归一化 v2.0"""
    if factor_df.empty:
        return pd.DataFrame()

    if weights is None:
        weights = {'factor': 0.25, 'launch': 0.15, 'llm': 0.20, 'overnight': 0.40}

    merged = factor_df[['ts_code', 'total_score', 'close']].copy()
    merged = merged.rename(columns={'total_score': 'factor_raw'})

    if merged['factor_raw'].max() > merged['factor_raw'].min():
        merged['factor_score'] = (
            (merged['factor_raw'] - merged['factor_raw'].min()) /
            (merged['factor_raw'].max() - merged['factor_raw'].min()) * 100
        ).round(2)
    else:
        merged['factor_score'] = 50.0

    if not launch_df.empty and 'launch_score' in launch_df.columns:
        launch_map = dict(zip(launch_df['ts_code'], launch_df['launch_score']))
        merged['launch_score'] = merged['ts_code'].map(launch_map).fillna(0) * 100
    else:
        merged['launch_score'] = 0

    merged['llm_score'] = merged['ts_code'].map(
        lambda c: llm_data.get(c, {}).get('llm_bonus', 0)).fillna(0).round(2)
    merged['llm_veto'] = merged['ts_code'].map(
        lambda c: llm_data.get(c, {}).get('llm_veto', False)).fillna(False)

    if not overnight_df.empty and 'overnight_score' in overnight_df.columns:
        merged['overnight_score'] = merged['ts_code'].map(
            dict(zip(overnight_df['ts_code'], overnight_df['overnight_score']))
        ).fillna(0).round(2)
        merged['pool'] = merged['ts_code'].map(
            dict(zip(overnight_df['ts_code'], overnight_df.get('pool', pd.Series())))
        ).fillna('stable')
    else:
        merged['overnight_score'] = 0; merged['pool'] = 'stable'

    if sustain_df is not None and not sustain_df.empty and 'sustain_score' in sustain_df.columns:
        merged['sustain_raw'] = merged['ts_code'].map(
            dict(zip(sustain_df['ts_code'], sustain_df['sustain_score']))
        ).fillna(0.5)
    else:
        merged['sustain_raw'] = 0.5

    merged['meta_score'] = round(
        merged['factor_score'] * weights.get('factor', 0.25) +
        merged['launch_score'] * weights.get('launch', 0.15) +
        merged['llm_score'] * weights.get('llm', 0.20) +
        merged['overnight_score'] * weights.get('overnight', 0.40), 2)

    sustain_penalty = merged['sustain_raw'].apply(
        lambda s: -10 * (0.3 - s) if s < 0.3 else 0)
    merged['meta_score'] = (merged['meta_score'] + sustain_penalty).round(2)

    merged.loc[merged['llm_veto'], 'meta_score'] = 0

    # 融合过滤：需要至少一个正向信号，或者factor_score足够高
    has_signal = (merged['launch_score'] > 0) | (merged['overnight_score'] > 0)
    high_factor = merged['factor_score'] >= 60  # 因子评分高也允许通过
    merged = merged[has_signal | high_factor]
    return merged.sort_values('meta_score', ascending=False).reset_index(drop=True)


# ============================================================
# 回测主类
# ============================================================

class DbBacktester:
    """基于 PostgreSQL 的融合元策略回测器 v2.0"""

    def __init__(self, cfg: MetaBacktestConfig = None,
                 pm_cfg: PositionManagerConfig = None):
        self.cfg = cfg or DEFAULT_BT_CONFIG
        self.pm_cfg = pm_cfg or DEFAULT_PM_CONFIG
        # 价格缓存: (ts_code, date) -> {open, close}
        self._price_cache: Dict[Tuple[str, date], Dict] = {}

    def _prefetch_prices(self, ts_codes: List[str], trade_date: date,
                          lookback: int = 5):
        """预加载价格数据到缓存"""
        missing = [c for c in ts_codes if (c, trade_date) not in self._price_cache]
        if not missing:
            return
        start = trade_date - timedelta(days=lookback)
        batch = get_daily_quotes_batch(missing, start, trade_date)
        for code, df in batch.items():
            if df.empty:
                continue
            for _, row in df.iterrows():
                d = row['trade_date']
                if isinstance(d, str):
                    d = date.fromisoformat(d)
                self._price_cache[(code, d)] = {
                    'open': float(row['open']) if pd.notna(row['open']) else None,
                    'close': float(row['close']) if pd.notna(row['close']) else None,
                }

    def _get_open_price(self, ts_code: str, trade_date: date) -> Optional[float]:
        """获取开盘价（优先缓存）"""
        cached = self._price_cache.get((ts_code, trade_date))
        if cached:
            return cached['open']
        # 回退：单次查询
        start = trade_date - timedelta(days=5)
        df = get_daily_quotes_batch([ts_code], start, trade_date)
        data = df.get(ts_code)
        if data is None or data.empty:
            return None
        last = data.iloc[-1]
        return float(last['open']) if pd.notna(last['open']) else None

    def _get_close_price(self, ts_code: str, trade_date: date) -> Optional[float]:
        """获取收盘价（优先缓存）"""
        cached = self._price_cache.get((ts_code, trade_date))
        if cached:
            return cached['close']
        start = trade_date - timedelta(days=5)
        df = get_daily_quotes_batch([ts_code], start, trade_date)
        data = df.get(ts_code)
        if data is None or data.empty:
            return None
        last = data.iloc[-1]
        return float(last['close']) if pd.notna(last['close']) else None

    def _calc_equity(self, pm: PositionManager, cash: float, eval_date: date) -> float:
        """计算总权益（使用缓存）"""
        equity = cash
        pos_codes = list(pm.positions.keys())
        if pos_codes:
            self._prefetch_prices(pos_codes, eval_date)
        for ts_code, pos in pm.positions.items():
            price = self._get_close_price(ts_code, eval_date)
            if price:
                equity += price * pos.shares
            else:
                equity += pos.entry_price * pos.shares
        return equity

    def run(self) -> Dict:
        """运行回测"""
        logger.info("=" * 70)
        logger.info(f"融合元策略回测 v2.0 (DB) {self.cfg.start_date} ~ {self.cfg.end_date}")
        logger.info("=" * 70)

        start_date = date.fromisoformat(self.cfg.start_date)
        end_date = date.fromisoformat(self.cfg.end_date)

        trading_days = get_trading_days(start_date, end_date)
        if not trading_days:
            logger.error("无交易日数据")
            return {}

        logger.info(f"交易日: {len(trading_days)} 天")

        pm = PositionManager(self.pm_cfg)
        pm.cfg.max_positions = self.cfg.max_positions

        capital = self.cfg.initial_capital
        all_trades = []
        daily_equity = []
        layer_stats = {
            'L0_reject': 0, 'L0_regime': {'bull': 0, 'oscillate': 0, 'bear': 0},
            'L1_count': [], 'L2_count': [], 'L3_count': [],
            'L4_covered': [], 'L4_vetoed': 0,
            'L5_count': [], 'L5_pool': {'stable': 0, 'upper': 0, 'extreme': 0},
            'L6_count': [], 'final_count': [],
        }

        t_start = time.time()

        # 预加载指数数据
        idx_start = start_date - timedelta(days=self.cfg.layer0_regime_lookback + 30)
        index_data = get_index_quotes('000001.SH', idx_start, end_date)
        logger.info(f"指数数据: {len(index_data)} 天")

        for i, trade_date in enumerate(trading_days):
            try:
                # ── 0. 预加载持仓价格 ──
                if pm.positions:
                    self._prefetch_prices(list(pm.positions.keys()), trade_date)

                # ── 1. 评估退出条件 ──
                exit_signals = pm.evaluate_exits(trade_date)
                for sig in exit_signals:
                    exit_price = self._get_open_price(sig.ts_code, trade_date)
                    if exit_price is None:
                        exit_price = sig.current_price
                    exit_price_adj = exit_price * (1 - self.cfg.slippage_pct)
                    pos = pm.positions.get(sig.ts_code)
                    shares = pos.shares if pos else 100
                    commission = exit_price_adj * shares * self.cfg.commission_rate
                    record = pm.close_position(sig.ts_code, trade_date, exit_price_adj, sig.exit_reason)
                    if record:
                        record['commission'] = commission
                        all_trades.append(record)
                        capital += exit_price_adj * shares - commission

                # ── 2. Layer 0: 大盘风控 + 市场状态 ──
                idx_slice = index_data[index_data['trade_date'] <= trade_date].tail(self.cfg.layer0_regime_lookback + 5)
                market_risk = layer0_market_risk(trade_date, self.cfg, idx_slice)
                regime = market_risk.get('regime', 'oscillate')
                layer_stats['L0_regime'][regime] = layer_stats['L0_regime'].get(regime, 0) + 1
                weights = market_risk.get('weights', self.cfg.weights_oscillate)

                if not market_risk['passed']:
                    layer_stats['L0_reject'] += 1
                    equity = self._calc_equity(pm, capital, trade_date)
                    daily_equity.append({'date': str(trade_date), 'equity': equity,
                                         'positions': pm.open_position_count, 'regime': regime})
                    continue

                # ── 3. 获取当日全市场数据（一次SQL） ──
                daily_df = get_daily_quotes_for_date(trade_date, min_amount=self.cfg.layer2_min_amount)
                if daily_df.empty:
                    equity = self._calc_equity(pm, capital, trade_date)
                    daily_equity.append({'date': str(trade_date), 'equity': equity,
                                         'positions': pm.open_position_count, 'regime': regime})
                    continue

                # ── 4. Layer 1: 多因子扫描 ──
                factor_df = layer1_multi_factor_scan(trade_date, self.cfg, daily_df=daily_df)
                l1_codes = factor_df['ts_code'].tolist() if not factor_df.empty else []
                layer_stats['L1_count'].append(len(l1_codes))

                if not l1_codes:
                    equity = self._calc_equity(pm, capital, trade_date)
                    daily_equity.append({'date': str(trade_date), 'equity': equity,
                                         'positions': pm.open_position_count, 'regime': regime})
                    continue

                # ── 5. 批量获取历史数据（供L3/L5/L6使用） ──
                hist_start = trade_date - timedelta(days=150)
                history_data = get_daily_quotes_batch(l1_codes, hist_start, trade_date)

                # ── 6. Layer 2: 基本面过滤 ──
                fund_data = get_fundamental_batch(l1_codes)
                l2_codes = layer2_fundamental_filter(l1_codes, trade_date, self.cfg,
                                                      daily_df=daily_df, fund_data=fund_data)
                layer_stats['L2_count'].append(len(l2_codes))

                if not l2_codes:
                    equity = self._calc_equity(pm, capital, trade_date)
                    daily_equity.append({'date': str(trade_date), 'equity': equity,
                                         'positions': pm.open_position_count, 'regime': regime})
                    continue

                # ── 7. Layer 3: 启动信号 ──
                launch_df = layer3_launch_signals(l2_codes, trade_date, self.cfg,
                                                   history_data=history_data, daily_df=daily_df)
                l3_codes = launch_df['ts_code'].tolist() if not launch_df.empty else []
                layer_stats['L3_count'].append(len(l3_codes))

                # ── 8. Layer 4: LLM加成 ──
                llm_data = layer4_llm_boost(l2_codes, trade_date, self.cfg, fund_data=fund_data)
                l4_covered = sum(1 for v in llm_data.values() if v.get('llm_bonus', 0) > 0)
                l4_vetoed = sum(1 for v in llm_data.values() if v.get('llm_veto', False))
                layer_stats['L4_covered'].append(l4_covered)
                layer_stats['L4_vetoed'] += l4_vetoed

                # ── 9. Layer 5: 八步法评分 ──
                overnight_df = layer5_overnight_score(l2_codes, trade_date, self.cfg,
                                                       history_data=history_data, daily_df=daily_df)
                l5_codes = overnight_df['ts_code'].tolist() if not overnight_df.empty else []
                layer_stats['L5_count'].append(len(l5_codes))
                if not overnight_df.empty and 'pool' in overnight_df.columns:
                    for pool_type in ['stable', 'upper', 'extreme']:
                        layer_stats['L5_pool'][pool_type] += (overnight_df['pool'] == pool_type).sum()

                # ── 10. Layer 6: 持续性评估 ──
                sustain_df = layer6_sustain_eval(l2_codes, trade_date, self.cfg,
                                                  history_data=history_data)
                layer_stats['L6_count'].append(len(sustain_df))

                # ── 11. 融合评分 ──
                result_df = normalize_and_fuse(factor_df, launch_df, llm_data, overnight_df,
                                                sustain_df=sustain_df, weights=weights)
                layer_stats['final_count'].append(len(result_df))

                # LLM否决过滤
                vetoed_codes = {c for c, d in llm_data.items() if d.get('llm_veto', False)}
                if vetoed_codes:
                    result_df = result_df[~result_df['ts_code'].isin(vetoed_codes)]

                # ── 12. 开仓 ──
                if not result_df.empty:
                    # 预加载候选股票次日开盘价
                    next_idx = i + 1
                    if next_idx < len(trading_days):
                        next_date = trading_days[next_idx]
                        candidate_codes = result_df['ts_code'].tolist()
                        self._prefetch_prices(candidate_codes, next_date)

                    for _, row in result_df.iterrows():
                        if pm.open_position_count >= self.cfg.max_positions:
                            break
                        if row['ts_code'] in pm.positions:
                            continue
                        next_idx = i + 1
                        if next_idx >= len(trading_days):
                            continue
                        next_date = trading_days[next_idx]
                        entry_price = self._get_open_price(row['ts_code'], next_date)
                        if entry_price is None or entry_price <= 0:
                            continue

                        position_pct = self.cfg.single_position_pct * market_risk.get('position_cap', 1.0)
                        position_value = self.cfg.initial_capital * position_pct
                        shares = int(position_value / (entry_price * 100)) * 100
                        if shares <= 0:
                            shares = 100
                        entry_price_adj = entry_price * (1 + self.cfg.slippage_pct)
                        commission = entry_price_adj * shares * self.cfg.commission_rate
                        cost = entry_price_adj * shares + commission
                        if cost > capital:
                            continue

                        capital -= cost
                        pm.positions[row['ts_code']] = Position(
                            ts_code=row['ts_code'], entry_date=next_date,
                            entry_price=entry_price_adj, shares=shares,
                            meta_score=row.get('meta_score', 0),
                            launch_score=row.get('launch_score', 0),
                            factor_score=row.get('factor_score', 0),
                        )

                # ── 13. 记录权益 ──
                equity = self._calc_equity(pm, capital, trade_date)
                daily_equity.append({'date': str(trade_date), 'equity': equity,
                                     'positions': pm.open_position_count, 'regime': regime})

                if (i + 1) % 5 == 0 or i == len(trading_days) - 1:
                    elapsed = time.time() - t_start
                    avg = elapsed / (i + 1)
                    remaining = avg * (len(trading_days) - i - 1)
                    logger.info(
                        f"进度 {i+1}/{len(trading_days)} ({trade_date}): "
                        f"持仓{pm.open_position_count}只 权益{equity:,.0f} "
                        f"市场{regime} 剩余{remaining:.0f}s")

                if (i + 1) % 10 == 0:
                    clear_cache()
                    # 清理价格缓存中过期数据（保留最近5天的）
                    cutoff = trade_date - timedelta(days=5)
                    self._price_cache = {k: v for k, v in self._price_cache.items()
                                          if k[1] >= cutoff}

            except Exception as e:
                import traceback
                logger.warning(f"{trade_date} 回测失败: {e}")
                logger.debug(traceback.format_exc())

        # 强制平仓
        for ts_code in list(pm.positions.keys()):
            pos = pm.positions[ts_code]
            last_price = self._get_close_price(ts_code, end_date)
            if last_price:
                record = pm.close_position(ts_code, end_date, last_price, '回测结束')
                if record:
                    all_trades.append(record)

        total_elapsed = time.time() - t_start

        # ── 策略对比回测 ──
        compare_results = {}
        if self.cfg.strategy_compare_enabled:
            logger.info("\n运行策略对比回测...")
            for strategy_name in ['multi_factor_only', 'overnight_only', 'no_layer0']:
                try:
                    logger.info(f"  对比策略: {strategy_name}")
                    cmp_result = self._run_single_strategy(strategy_name, trading_days)
                    compare_results[strategy_name] = cmp_result
                except Exception as e:
                    logger.warning(f"  {strategy_name} 对比失败: {e}")

        summary = self._build_summary(all_trades, daily_equity, layer_stats, total_elapsed, compare_results)

        return {
            'trades': all_trades,
            'daily_equity': pd.DataFrame(daily_equity),
            'summary': summary,
            'layer_stats': layer_stats,
            'compare_results': compare_results,
        }

    def _run_single_strategy(self, strategy_name: str, trading_days: List[date]) -> Dict:
        """运行单个策略的对比回测"""
        capital = self.cfg.initial_capital
        all_trades = []
        positions: Dict[str, Dict] = {}

        for i, trade_date in enumerate(trading_days):
            # 评估退出
            for ts_code in list(positions.keys()):
                pos = positions[ts_code]
                holding_days = (trade_date - pos['entry_date']).days
                close_price = self._get_close_price(ts_code, trade_date)
                if close_price is None:
                    continue
                pnl_pct = (close_price - pos['entry_price']) / pos['entry_price']
                should_exit = False; exit_reason = ''
                if pnl_pct <= -0.08: should_exit = True; exit_reason = '硬止损'
                elif holding_days >= 15: should_exit = True; exit_reason = '时间止损'
                if should_exit:
                    shares = pos['shares']
                    exit_price = close_price * (1 - self.cfg.slippage_pct)
                    commission = exit_price * shares * self.cfg.commission_rate
                    capital += exit_price * shares - commission
                    all_trades.append({
                        'ts_code': ts_code, 'entry_date': str(pos['entry_date']),
                        'exit_date': str(trade_date), 'entry_price': pos['entry_price'],
                        'exit_price': exit_price, 'pnl_pct': round(pnl_pct, 4),
                        'holding_days': holding_days, 'exit_reason': exit_reason,
                    })
                    del positions[ts_code]

            # 生成信号
            daily_df = get_daily_quotes_for_date(trade_date, min_amount=self.cfg.layer2_min_amount)
            if daily_df.empty:
                continue

            if strategy_name == 'multi_factor_only':
                factor_df = layer1_multi_factor_scan(trade_date, self.cfg, daily_df=daily_df)
                candidates = factor_df['ts_code'].tolist() if not factor_df.empty else []
            elif strategy_name == 'overnight_only':
                stock_list = daily_df['ts_code'].tolist()
                overnight_df = layer5_overnight_score(stock_list, trade_date, self.cfg, daily_df=daily_df)
                candidates = overnight_df.nlargest(10, 'overnight_score')['ts_code'].tolist() if not overnight_df.empty else []
            elif strategy_name == 'no_layer0':
                factor_df = layer1_multi_factor_scan(trade_date, self.cfg, daily_df=daily_df)
                l1_codes = factor_df['ts_code'].tolist() if not factor_df.empty else []
                if not l1_codes: continue
                fund_data = get_fundamental_batch(l1_codes)
                l2_codes = layer2_fundamental_filter(l1_codes, trade_date, self.cfg, daily_df=daily_df, fund_data=fund_data)
                if not l2_codes: continue
                hist_start = trade_date - timedelta(days=150)
                history_data = get_daily_quotes_batch(l2_codes, hist_start, trade_date)
                launch_df = layer3_launch_signals(l2_codes, trade_date, self.cfg, history_data=history_data)
                llm_data = layer4_llm_boost(l2_codes, trade_date, self.cfg, fund_data=fund_data)
                overnight_df = layer5_overnight_score(l2_codes, trade_date, self.cfg, history_data=history_data)
                sustain_df = layer6_sustain_eval(l2_codes, trade_date, self.cfg, history_data=history_data)
                result_df = normalize_and_fuse(factor_df, launch_df, llm_data, overnight_df, sustain_df=sustain_df)
                candidates = result_df['ts_code'].tolist() if not result_df.empty else []
            else:
                candidates = []

            for ts_code in candidates:
                if len(positions) >= self.cfg.max_positions: break
                if ts_code in positions: continue
                next_idx = i + 1
                if next_idx >= len(trading_days): continue
                next_date = trading_days[next_idx]
                entry_price = self._get_open_price(ts_code, next_date)
                if entry_price is None or entry_price <= 0: continue
                position_value = self.cfg.initial_capital * self.cfg.single_position_pct
                shares = int(position_value / (entry_price * 100)) * 100
                if shares <= 0: shares = 100
                entry_price_adj = entry_price * (1 + self.cfg.slippage_pct)
                commission = entry_price_adj * shares * self.cfg.commission_rate
                cost = entry_price_adj * shares + commission
                if cost > capital: continue
                capital -= cost
                positions[ts_code] = {'entry_date': next_date, 'entry_price': entry_price_adj, 'shares': shares}

        # 强制平仓
        for ts_code in list(positions.keys()):
            pos = positions[ts_code]
            close_price = self._get_close_price(ts_code, trading_days[-1])
            if close_price:
                pnl_pct = (close_price - pos['entry_price']) / pos['entry_price']
                all_trades.append({
                    'ts_code': ts_code, 'entry_date': str(pos['entry_date']),
                    'exit_date': str(trading_days[-1]), 'entry_price': pos['entry_price'],
                    'exit_price': close_price, 'pnl_pct': round(pnl_pct, 4),
                    'holding_days': (trading_days[-1] - pos['entry_date']).days,
                    'exit_reason': '回测结束',
                })
        return {'trades': all_trades, 'strategy': strategy_name}

    def _build_summary(self, trades: List[Dict], daily_equity: List[Dict],
                       layer_stats: Dict, elapsed: float,
                       compare_results: Dict = None) -> str:
        """构建回测汇总"""
        lines = []
        lines.append("=" * 70)
        lines.append("  融合元策略回测汇总 v2.0 (PostgreSQL)")
        lines.append("=" * 70)
        lines.append(f"  回测区间: {self.cfg.start_date} ~ {self.cfg.end_date}")
        lines.append(f"  初始资金: {self.cfg.initial_capital:,.0f}")
        lines.append(f"  回测耗时: {elapsed:.1f}s")
        lines.append("")

        if trades:
            pnls = [t['pnl_pct'] for t in trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            lines.append("--- 交易统计 ---")
            lines.append(f"  总交易数: {len(trades)}")
            lines.append(f"  胜率: {len(wins)/len(pnls):.1%}" if pnls else "  胜率: N/A")
            lines.append(f"  平均收益: {np.mean(pnls):.2%}" if pnls else "  平均收益: N/A")
            lines.append(f"  中位数收益: {np.median(pnls):.2%}" if pnls else "  中位数: N/A")
            lines.append(f"  最大单笔盈利: {max(pnls):.2%}" if pnls else "  最大盈利: N/A")
            lines.append(f"  最大单笔亏损: {min(pnls):.2%}" if pnls else "  最大亏损: N/A")
            lines.append(f"  盈利交易平均: {np.mean(wins):.2%}" if wins else "  盈利均: N/A")
            lines.append(f"  亏损交易平均: {np.mean(losses):.2%}" if losses else "  亏损均: N/A")
            lines.append(f"  盈亏比: {abs(np.mean(wins)/np.mean(losses)):.2f}" if wins and losses else "  盈亏比: N/A")
            lines.append(f"  平均持仓天数: {np.mean([t['holding_days'] for t in trades]):.1f}")
            lines.append("")

            exit_reasons = {}
            for t in trades:
                reason = t['exit_reason'].split('(')[0]
                exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
            lines.append("--- 退出原因分布 ---")
            for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
                lines.append(f"  {reason}: {count} ({count/len(trades):.1%})")
            lines.append("")

            rets = np.array(pnls)
            lines.append("--- 收益分布 ---")
            lines.append(f"  >5%:  {np.mean(rets > 0.05):.1%}")
            lines.append(f"  >3%:  {np.mean(rets > 0.03):.1%}")
            lines.append(f"  >0%:  {np.mean(rets > 0):.1%}")
            lines.append(f"  <-3%: {np.mean(rets < -0.03):.1%}")
            lines.append(f"  <-5%: {np.mean(rets < -0.05):.1%}")
            lines.append(f"  <-8%: {np.mean(rets < -0.08):.1%}")
            lines.append("")
        else:
            lines.append("  无交易记录")
            lines.append("")

        if daily_equity:
            eq_df = pd.DataFrame(daily_equity)
            initial = self.cfg.initial_capital
            final = eq_df['equity'].iloc[-1]
            total_return = (final - initial) / initial
            cummax = eq_df['equity'].cummax()
            max_dd = ((eq_df['equity'] - cummax) / cummax).min()
            lines.append("--- 权益曲线 ---")
            lines.append(f"  初始权益: {initial:,.0f}")
            lines.append(f"  最终权益: {final:,.0f}")
            lines.append(f"  总收益率: {total_return:.2%}")
            lines.append(f"  最大回撤: {max_dd:.2%}")
            lines.append("")

        if 'L0_regime' in layer_stats:
            lines.append("--- 市场状态分布 ---")
            total_days = sum(layer_stats['L0_regime'].values())
            for regime, count in layer_stats['L0_regime'].items():
                if count > 0:
                    lines.append(f"  {regime}: {count}天 ({count/total_days:.1%})" if total_days > 0 else f"  {regime}: {count}天")
            lines.append("")

        if 'L5_pool' in layer_stats:
            lines.append("--- 双池分布 ---")
            total_pool = sum(layer_stats['L5_pool'].values())
            for pool_type, count in layer_stats['L5_pool'].items():
                if count > 0:
                    lines.append(f"  {pool_type}: {count}只 ({count/total_pool:.1%})" if total_pool > 0 else f"  {pool_type}: {count}只")
            lines.append("")

        lines.append("--- 各层漏斗平均通过数 ---")
        for layer, counts in layer_stats.items():
            if counts and isinstance(counts, list):
                avg = np.mean(counts) if counts else 0
                lines.append(f"  {layer}: 平均 {avg:.0f} 只/日")
            elif isinstance(counts, (int, float)):
                lines.append(f"  {layer}: {counts}")
        lines.append("")

        if compare_results:
            lines.append("--- 策略对比 ---")
            lines.append(f"  {'策略':<25} {'交易数':>6} {'胜率':>8} {'平均收益':>10} {'总收益':>10}")
            lines.append(f"  {'─'*65}")
            if trades:
                pnls = [t['pnl_pct'] for t in trades]
                win_rate = len([p for p in pnls if p > 0]) / len(pnls) if pnls else 0
                avg_ret = np.mean(pnls) if pnls else 0
                total_ret = sum(pnls) if pnls else 0
                lines.append(f"  {'融合策略(v2.0)':<25} {len(trades):>6} {win_rate:>8.1%} {avg_ret:>10.2%} {total_ret:>10.2%}")
            for strategy_name, cmp in compare_results.items():
                cmp_trades = cmp.get('trades', [])
                if cmp_trades:
                    cmp_pnls = [t['pnl_pct'] for t in cmp_trades]
                    cmp_win = len([p for p in cmp_pnls if p > 0]) / len(cmp_pnls) if cmp_pnls else 0
                    cmp_avg = np.mean(cmp_pnls) if cmp_pnls else 0
                    cmp_total = sum(cmp_pnls) if cmp_pnls else 0
                    lines.append(f"  {strategy_name:<25} {len(cmp_trades):>6} {cmp_win:>8.1%} {cmp_avg:>10.2%} {cmp_total:>10.2%}")
                else:
                    lines.append(f"  {strategy_name:<25} {'0':>6} {'N/A':>8} {'N/A':>10} {'N/A':>10}")
            lines.append("")

        lines.append("=" * 70)
        return "\n".join(lines)


# ============================================================
# CLI入口
# ============================================================

def run_backtest():
    """运行回测"""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    cfg = MetaBacktestConfig()
    pm_cfg = PositionManagerConfig()

    bt = DbBacktester(cfg, pm_cfg)
    result = bt.run()

    if result:
        print(result['summary'])

        out_dir = Path(cfg.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(BEIJING_TZ).strftime('%Y%m%d_%H%M')

        if result['trades']:
            trades_df = pd.DataFrame(result['trades'])
            trades_path = out_dir / f"db_bt_v2_trades_{timestamp}.csv"
            trades_df.to_csv(trades_path, index=False, encoding='utf-8-sig')
            print(f"\n  交易记录: {trades_path}")

        if not result['daily_equity'].empty:
            eq_path = out_dir / f"db_bt_v2_equity_{timestamp}.csv"
            result['daily_equity'].to_csv(eq_path, index=False, encoding='utf-8-sig')
            print(f"  权益曲线: {eq_path}")

        report_path = out_dir / f"db_bt_v2_report_{timestamp}.txt"
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(result['summary'])
        print(f"  回测报告: {report_path}")

        if result.get('compare_results'):
            compare_path = out_dir / f"db_bt_v2_compare_{timestamp}.json"
            compare_data = {}
            for strategy_name, cmp in result['compare_results'].items():
                trades = cmp.get('trades', [])
                if trades:
                    pnls = [t['pnl_pct'] for t in trades]
                    compare_data[strategy_name] = {
                        'total_trades': len(trades),
                        'win_rate': round(len([p for p in pnls if p > 0]) / len(pnls), 4) if pnls else 0,
                        'avg_return': round(float(np.mean(pnls)), 4) if pnls else 0,
                        'total_return': round(float(sum(pnls)), 4) if pnls else 0,
                    }
                else:
                    compare_data[strategy_name] = {'total_trades': 0}
            with open(compare_path, 'w', encoding='utf-8') as f:
                json.dump(compare_data, f, ensure_ascii=False, indent=2)
            print(f"  策略对比: {compare_path}")

    return result


if __name__ == "__main__":
    run_backtest()
