"""
融合元策略 - Baostock回测引擎 v1.0
====================================
使用 baostock 在线数据源，不依赖 PostgreSQL。
严格 PIT 回测：
  - T 日收盘后产生信号（六层漏斗）
  - T+1 日开盘买入
  - 持仓管理模块每日评估退出
  - T+N 日开盘卖出

输出：胜率、平均收益、最大回撤、退出原因分布、各层漏斗通过率
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
    """回测配置"""
    start_date: str = "2025-06-01"
    end_date: str = "2026-05-15"
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.001
    slippage_pct: float = 0.001
    max_positions: int = 5
    single_position_pct: float = 0.20

    # Layer 0: 大盘风控
    layer0_enabled: bool = True
    layer0_min_advancers_ratio: float = 0.50  # 上涨家数占比>50%

    # Layer 1: 多因子扫描
    layer1_enabled: bool = True
    layer1_min_total_score: float = 0.40
    layer1_top_n: int = 50

    # Layer 2: 基本面+流动性
    layer2_enabled: bool = True
    layer2_min_amount: float = 5e7
    layer2_max_debt_ratio: float = 65.0
    layer2_min_current_ratio: float = 1.0

    # Layer 3: 启动信号
    layer3_enabled: bool = True
    layer3_volume_breakout_mult: float = 2.0
    layer3_price_breakout_pct: float = 0.03

    # Layer 4: LLM事件加成（回测中简化为随机模拟）
    layer4_enabled: bool = False
    layer4_simulated_bonus_range: Tuple[float, float] = (0, 10)

    # Layer 5: 八步法精细评分
    layer5_enabled: bool = True
    layer5_pct_range_low: float = 2.0
    layer5_pct_range_high: float = 7.0
    layer5_vol_ratio_min: float = 1.5

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
    """计算7个技术因子得分"""
    n = len(close)
    if n < 30:
        return {'total_score': 0}

    r = {}

    # 1. 动量 (20日涨幅)
    mom = float((close[-1] - close[-21]) / (close[-21] + 1e-9)) if n >= 21 else 0
    r['momentum'] = round(mom, 4)
    r['momentum_score'] = round(min(max(mom / 0.15, 0), 1.0) if mom > 0 else 0, 3)

    # 2. 量比
    vol_mean = float(amount[-21:-1].mean()) if n >= 21 else float(amount.mean())
    vr = float(amount[-1]) / (vol_mean + 1e-9)
    r['volume_ratio'] = round(vr, 2)
    r['volume_score'] = round(min((vr - 1.5) / 8.5, 1.0) * 0.7 if 1.5 <= vr <= 10 else 0, 3)

    # 3. RSI(6)
    d = np.diff(close)
    ag = _ema(np.where(d > 0, d, 0.0), 6)
    al = _ema(np.where(d < 0, -d, 0.0), 6)
    rsi = float(100 - 100 / (1 + ag[-1] / (al[-1] + 1e-9)))
    r['rsi'] = round(rsi, 1)
    r['rsi_score'] = 0 if rsi >= 75 else (1.0 if rsi <= 35 else round((75 - rsi) / 40, 3))

    # 4. MACD
    dif = _ema(close, 12) - _ema(close, 26)
    dea = _ema(dif, 9)
    hist = (dif - dea) * 2
    ld, la = float(dif[-1]), float(dea[-1])
    lh, ph = float(hist[-1]), float(hist[-2]) if n > 1 else 0
    if ld > la and ph <= 0 and lh > 0:
        r['macd_score'] = 1.0
    elif ld > la and lh > 0 and lh > ph:
        r['macd_score'] = 0.7
    elif ld > la:
        r['macd_score'] = 0.4
    else:
        r['macd_score'] = 0.1

    # 5. EMA排列
    e5 = float(_ema(close, 5)[-1])
    e10 = float(_ema(close, 10)[-1])
    e20 = float(_ema(close, 20)[-1])
    lc = float(close[-1])
    if lc > e5 > e10 > e20:
        r['ema_score'] = 1.0
    elif lc > e10 > e20:
        r['ema_score'] = 0.7
    elif lc > e20:
        r['ema_score'] = 0.4
    else:
        r['ema_score'] = 0.0

    # 6. ADX
    p = 14
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    up = high[1:] - high[:-1]
    dn = low[:-1] - low[1:]
    atr = _ema(tr, p)
    pdi = 100 * _ema(np.where((up > dn) & (up > 0), up, 0.0), p) / (atr + 1e-9)
    mdi = 100 * _ema(np.where((dn > up) & (dn > 0), dn, 0.0), p) / (atr + 1e-9)
    adx_v = float(_ema(100 * np.abs(pdi - mdi) / (pdi + mdi + 1e-9), p)[-1])
    lpdi = float(pdi[-1])
    lmdi = float(mdi[-1])
    if adx_v >= 20 and lpdi > lmdi:
        r['adx_score'] = round(min(adx_v / 50, 1.0), 3)
    elif lpdi > lmdi:
        r['adx_score'] = 0.3
    else:
        r['adx_score'] = 0.0

    # 7. SAR
    af_i, af_m = 0.02, 0.2
    sar = np.zeros(n)
    trend = 1
    ep = high[0]
    af = af_i
    sar[0] = low[0]
    for i in range(1, n):
        ps = sar[i - 1]
        if trend == 1:
            sar[i] = min(ps + af * (ep - ps), low[i - 1], low[max(0, i - 2)])
            if low[i] < sar[i]:
                trend = -1
                sar[i] = ep
                ep = low[i]
                af = af_i
            elif high[i] > ep:
                ep = high[i]
                af = min(af + af_i, af_m)
        else:
            sar[i] = max(ps + af * (ep - ps), high[i - 1], high[max(0, i - 2)])
            if high[i] > sar[i]:
                trend = 1
                sar[i] = ep
                ep = high[i]
                af = af_i
            elif low[i] < ep:
                ep = low[i]
                af = min(af + af_i, af_m)
    ls = float(sar[-1])
    if ls < lc:
        r['sar_score'] = round(min(1.0, max(0.3, 1.0 - (lc - ls) / lc * 10)), 3)
    else:
        r['sar_score'] = 0.0

    # 加权总分
    r['total_score'] = round(
        r['momentum_score'] * 0.20 +
        r['volume_score'] * 0.20 +
        r['rsi_score'] * 0.15 +
        r['macd_score'] * 0.15 +
        r['ema_score'] * 0.15 +
        r['adx_score'] * 0.10 +
        r['sar_score'] * 0.05, 4)

    return r


# ============================================================
# 六层漏斗
# ============================================================

def layer0_market_risk(trade_date: date, cfg: MetaBacktestConfig) -> Dict:
    """Layer 0: 大盘风控"""
    if not cfg.layer0_enabled:
        return {'passed': True, 'position_cap': 1.0, 'reason': 'Layer0禁用'}

    overview = get_market_overview(trade_date)
    ratio = overview['breadth_ratio']
    passed = ratio >= cfg.layer0_min_advancers_ratio

    return {
        'passed': passed,
        'position_cap': 1.0 if passed else 0.5,
        'advancers': overview['advancers'],
        'decliners': overview['decliners'],
        'breadth_ratio': round(ratio, 4),
        'reason': '' if passed else f'上涨占比{ratio:.1%}<{cfg.layer0_min_advancers_ratio:.0%}',
    }


def layer1_multi_factor_scan(trade_date: date, cfg: MetaBacktestConfig,
                              stock_pool: List[str] = None) -> pd.DataFrame:
    """Layer 1: 多因子全市场扫描（两阶段：快速预筛 + 精细计算）"""
    if not cfg.layer1_enabled:
        return pd.DataFrame()

    # 获取活跃标的
    if stock_pool is None:
        stock_pool = get_active_stocks(trade_date, min_amount=cfg.layer2_min_amount)

    if not stock_pool:
        return pd.DataFrame()

    start_date = trade_date - timedelta(days=120)

    # ── 阶段1: 快速预筛（仅用最近5日数据判断动量+量比） ──
    quick_start = trade_date - timedelta(days=10)
    prefiltered = []

    for ts_code in stock_pool:
        try:
            df = get_daily_quotes_cached(ts_code, quick_start, trade_date,
                                          fields="date,open,high,low,close,volume,amount,pctChg")
            if df.empty or len(df) < 3:
                continue

            close = df['close'].values.astype(float)
            pct_chg = df['pct_chg'].values.astype(float) if 'pct_chg' in df.columns else None

            # 快速条件：近3日至少1日上涨，且收盘价>0
            if close[-1] <= 0:
                continue

            # 最近涨幅 > -5%（排除大跌）
            if pct_chg is not None and len(pct_chg) > 0:
                last_pct = float(pct_chg[-1])
                if last_pct < -5:
                    continue

            prefiltered.append(ts_code)
        except Exception:
            pass

    logger.info(f"Layer1 预筛: {len(stock_pool)} -> {len(prefiltered)} 只")

    # ── 阶段2: 精细因子计算（仅对预筛通过的标的） ──
    results = []

    for i, ts_code in enumerate(prefiltered):
        try:
            df = get_daily_quotes_cached(ts_code, start_date, trade_date)
            if df.empty or len(df) < 30:
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
        except Exception as e:
            logger.debug(f"Layer1 {ts_code} 计算失败: {e}")

        # 节流
        if (i + 1) % 30 == 0:
            time.sleep(0.1)

    if not results:
        return pd.DataFrame()

    df_result = pd.DataFrame(results)
    df_result = df_result.sort_values('total_score', ascending=False)

    if len(df_result) > cfg.layer1_top_n:
        df_result = df_result.head(cfg.layer1_top_n)

    return df_result.reset_index(drop=True)


def layer2_fundamental_filter(stock_list: List[str], trade_date: date,
                               cfg: MetaBacktestConfig) -> List[str]:
    """Layer 2: 基本面+流动性过滤"""
    if not cfg.layer2_enabled:
        return stock_list

    passed = []
    year = trade_date.year - (1 if trade_date.month < 5 else 0)
    quarter = 4 if trade_date.month < 5 else (trade_date.month - 1) // 3

    for ts_code in stock_list:
        try:
            fund = get_fundamental_data(ts_code, year, quarter)

            # 债务比率过滤
            debt_ratio = fund.get('debt_ratio')
            if debt_ratio is not None and debt_ratio > cfg.layer2_max_debt_ratio:
                continue

            # 流动比率过滤
            current_ratio = fund.get('current_ratio')
            if current_ratio is not None and current_ratio < cfg.layer2_min_current_ratio:
                continue

            # 净利润为负过滤
            net_margin = fund.get('net_margin')
            if net_margin is not None and net_margin < -10:
                continue

            passed.append(ts_code)
        except Exception:
            # 无基本面数据时放行
            passed.append(ts_code)

    return passed


def layer3_launch_signals(stock_list: List[str], trade_date: date,
                           cfg: MetaBacktestConfig) -> pd.DataFrame:
    """Layer 3: 启动信号识别"""
    if not cfg.layer3_enabled:
        return pd.DataFrame({'ts_code': stock_list, 'launch_score': [0.5] * len(stock_list)})

    start_date = trade_date - timedelta(days=120)
    results = []

    for ts_code in stock_list:
        try:
            df = get_daily_quotes_cached(ts_code, start_date, trade_date)
            if df.empty or len(df) < 20:
                continue

            close = df['close'].values.astype(float)
            amount = df['amount'].values.astype(float) if 'amount' in df.columns else np.zeros(len(df))
            n = len(close)

            launch_score = 0.0
            signals = []

            # 1. 放量突破
            if n >= 20:
                vol_ma20 = amount[-21:-1].mean()
                if vol_ma20 > 0:
                    vol_ratio = amount[-1] / vol_ma20
                    if vol_ratio >= cfg.layer3_volume_breakout_mult:
                        launch_score += 0.4
                        signals.append(f'放量{vol_ratio:.1f}倍')

            # 2. 价格突破
            if n >= 20:
                high_20 = close[-21:-1].max()
                if close[-1] > high_20 * (1 + cfg.layer3_price_breakout_pct):
                    launch_score += 0.3
                    signals.append('突破20日高点')

            # 3. MACD金叉
            if n >= 35:
                dif = _ema(close, 12) - _ema(close, 26)
                dea = _ema(dif, 9)
                if len(dif) >= 2 and dif[-2] <= dea[-2] and dif[-1] > dea[-1]:
                    launch_score += 0.3
                    signals.append('MACD金叉')

            if launch_score > 0:
                results.append({
                    'ts_code': ts_code,
                    'launch_score': round(min(launch_score, 1.0), 3),
                    'launch_signals': ', '.join(signals) if signals else '',
                })
        except Exception as e:
            logger.debug(f"Layer3 {ts_code} 失败: {e}")

    if not results:
        return pd.DataFrame()

    return pd.DataFrame(results)


def layer4_llm_boost(stock_list: List[str], trade_date: date,
                      cfg: MetaBacktestConfig) -> Dict[str, float]:
    """Layer 4: LLM事件驱动加成（回测中简化为基于基本面质量的模拟）"""
    if not cfg.layer4_enabled:
        return {code: 0.0 for code in stock_list}

    # 回测中用基本面质量作为LLM加成的代理
    year = trade_date.year - (1 if trade_date.month < 5 else 0)
    quarter = 4 if trade_date.month < 5 else (trade_date.month - 1) // 3

    boosts = {}
    for ts_code in stock_list:
        try:
            fund = get_fundamental_data(ts_code, year, quarter)
            # 基本面越好，LLM加成越高
            roe = fund.get('roe') or 0
            rev_yoy = fund.get('revenue_yoy') or 0
            bonus = 0
            if roe and roe > 15:
                bonus += 5
            if rev_yoy and rev_yoy > 20:
                bonus += 5
            boosts[ts_code] = min(bonus, 15)
        except Exception:
            boosts[ts_code] = 0.0

    return boosts


def layer5_overnight_score(stock_list: List[str], trade_date: date,
                            cfg: MetaBacktestConfig) -> pd.DataFrame:
    """Layer 5: 八步法精细评分"""
    if not cfg.layer5_enabled:
        return pd.DataFrame({'ts_code': stock_list,
                             'overnight_score': [50] * len(stock_list)})

    start_date = trade_date - timedelta(days=60)
    results = []

    for ts_code in stock_list:
        try:
            df = get_daily_quotes_cached(ts_code, start_date, trade_date)
            if df.empty or len(df) < 5:
                continue

            close = df['close'].values.astype(float)
            amount = df['amount'].values.astype(float) if 'amount' in df.columns else np.zeros(len(df))
            pct_chg = df['pct_chg'].values.astype(float) if 'pct_chg' in df.columns else np.zeros(len(df))
            n = len(close)

            score = 0

            # 1. 涨幅评分 (2%-7%最佳)
            if n >= 1:
                pct = float(pct_chg[-1]) if len(pct_chg) > 0 else 0
                if cfg.layer5_pct_range_low <= pct <= cfg.layer5_pct_range_high:
                    score += 30
                elif pct > 0:
                    score += 15

            # 2. 量比评分
            if n >= 20:
                vol_mean = amount[-21:-1].mean()
                if vol_mean > 0:
                    vr = amount[-1] / vol_mean
                    if cfg.layer5_vol_ratio_min <= vr <= 10:
                        score += 25
                    elif vr > 1:
                        score += 10

            # 3. MA5距离评分
            if n >= 5:
                ma5 = close[-5:].mean()
                dist = (close[-1] - ma5) / (ma5 + 1e-9)
                if 0 <= dist <= 0.03:
                    score += 20
                elif dist < 0:
                    score += 5

            # 4. 连涨天数评分
            if n >= 3:
                up_days = sum(1 for i in range(-3, 0) if pct_chg[i] > 0)
                if up_days >= 2:
                    score += 15

            # 5. 换手率评分
            if 'turnover_rate' in df.columns:
                tr = float(df['turnover_rate'].iloc[-1])
                if 3 <= tr <= 15:
                    score += 10

            results.append({
                'ts_code': ts_code,
                'overnight_score': min(score, 100),
            })
        except Exception as e:
            logger.debug(f"Layer5 {ts_code} 失败: {e}")

    if not results:
        return pd.DataFrame()

    return pd.DataFrame(results)


def normalize_and_fuse(factor_df: pd.DataFrame, launch_df: pd.DataFrame,
                       llm_boosts: Dict[str, float],
                       overnight_df: pd.DataFrame) -> pd.DataFrame:
    """统一评分归一化 + 加权融合"""
    if factor_df.empty:
        return pd.DataFrame()

    merged = factor_df[['ts_code', 'total_score', 'close']].copy()
    merged = merged.rename(columns={'total_score': 'factor_score'})

    # 归一化到0-100
    if merged['factor_score'].max() > merged['factor_score'].min():
        merged['factor_score'] = (
            (merged['factor_score'] - merged['factor_score'].min()) /
            (merged['factor_score'].max() - merged['factor_score'].min()) * 100
        ).round(2)
    else:
        merged['factor_score'] = 50.0

    # 合并launch_score
    if not launch_df.empty:
        launch_map = dict(zip(launch_df['ts_code'], launch_df['launch_score']))
        merged['launch_score'] = merged['ts_code'].map(launch_map).fillna(0)
        merged['launch_score'] = (merged['launch_score'] * 100).round(2)
    else:
        merged['launch_score'] = 0

    # 合并llm_score
    merged['llm_score'] = merged['ts_code'].map(llm_boosts).fillna(0).round(2)

    # 合并overnight_score
    if not overnight_df.empty:
        ov_map = dict(zip(overnight_df['ts_code'], overnight_df['overnight_score']))
        merged['overnight_score'] = merged['ts_code'].map(ov_map).fillna(0).round(2)
    else:
        merged['overnight_score'] = 0

    # 加权融合
    merged['meta_score'] = round(
        merged['factor_score'] * 0.30 +
        merged['launch_score'] * 0.20 +
        merged['llm_score'] * 0.15 +
        merged['overnight_score'] * 0.35, 2)

    # 只保留有启动信号或隔夜评分的
    merged = merged[(merged['launch_score'] > 0) | (merged['overnight_score'] > 0)]

    return merged.sort_values('meta_score', ascending=False).reset_index(drop=True)


# ============================================================
# 回测主类
# ============================================================

class BaostockBacktester:
    """基于 Baostock 的融合元策略回测器"""

    def __init__(self, cfg: MetaBacktestConfig = None,
                 pm_cfg: PositionManagerConfig = None):
        self.cfg = cfg or DEFAULT_BT_CONFIG
        self.pm_cfg = pm_cfg or DEFAULT_PM_CONFIG

    def run(self) -> Dict:
        """运行回测"""
        logger.info("=" * 70)
        logger.info(f"融合元策略回测 (Baostock) {self.cfg.start_date} ~ {self.cfg.end_date}")
        logger.info("=" * 70)

        ensure_login()

        start_date = date.fromisoformat(self.cfg.start_date)
        end_date = date.fromisoformat(self.cfg.end_date)

        trading_days = get_trading_days(start_date, end_date)
        if not trading_days:
            logger.error("无交易日数据")
            logout()
            return {}

        logger.info(f"交易日: {len(trading_days)} 天")

        pm = PositionManager(self.pm_cfg)
        pm.cfg.max_positions = self.cfg.max_positions

        capital = self.cfg.initial_capital
        daily_equity = []
        all_trades = []
        layer_stats = {'L0_reject': 0, 'L1_count': [], 'L2_count': [],
                       'L3_count': [], 'L4_covered': [], 'L5_count': [],
                       'final_count': []}

        t_start = time.time()

        for i, trade_date in enumerate(trading_days):
            try:
                # ── 1. 评估退出条件 ──
                # 用baostock数据更新持仓的价格
                self._update_position_prices(pm, trade_date)
                exit_signals = pm.evaluate_exits(trade_date)

                for sig in exit_signals:
                    exit_price = self._get_open_price(sig.ts_code, trade_date)
                    if exit_price is None:
                        exit_price = sig.current_price

                    exit_price_adj = exit_price * (1 - self.cfg.slippage_pct)
                    pos = pm.positions.get(sig.ts_code)
                    shares = pos.shares if pos else 100
                    commission = exit_price_adj * shares * self.cfg.commission_rate

                    record = pm.close_position(
                        sig.ts_code, trade_date, exit_price_adj, sig.exit_reason)
                    if record:
                        record['commission'] = commission
                        all_trades.append(record)
                        capital += exit_price_adj * shares - commission

                # ── 2. Layer 0: 大盘风控 ──
                market_risk = layer0_market_risk(trade_date, self.cfg)
                if not market_risk['passed']:
                    layer_stats['L0_reject'] += 1
                    equity = self._calc_equity(pm, capital, trade_date)
                    daily_equity.append({'date': str(trade_date), 'equity': equity,
                                         'positions': pm.open_position_count})
                    continue

                # ── 3. Layer 1: 多因子扫描 ──
                factor_df = layer1_multi_factor_scan(trade_date, self.cfg)
                l1_codes = factor_df['ts_code'].tolist() if not factor_df.empty else []
                layer_stats['L1_count'].append(len(l1_codes))

                if not l1_codes:
                    equity = self._calc_equity(pm, capital, trade_date)
                    daily_equity.append({'date': str(trade_date), 'equity': equity,
                                         'positions': pm.open_position_count})
                    continue

                # ── 4. Layer 2: 基本面过滤 ──
                l2_codes = layer2_fundamental_filter(l1_codes, trade_date, self.cfg)
                layer_stats['L2_count'].append(len(l2_codes))

                if not l2_codes:
                    equity = self._calc_equity(pm, capital, trade_date)
                    daily_equity.append({'date': str(trade_date), 'equity': equity,
                                         'positions': pm.open_position_count})
                    continue

                # ── 5. Layer 3: 启动信号 ──
                launch_df = layer3_launch_signals(l2_codes, trade_date, self.cfg)
                l3_codes = launch_df['ts_code'].tolist() if not launch_df.empty else []
                layer_stats['L3_count'].append(len(l3_codes))

                # ── 6. Layer 4: LLM加成 ──
                llm_boosts = layer4_llm_boost(l2_codes, trade_date, self.cfg)
                l4_covered = sum(1 for v in llm_boosts.values() if v > 0)
                layer_stats['L4_covered'].append(l4_covered)

                # ── 7. Layer 5: 八步法评分 ──
                # 对L2通过的标的都做评分（不限于L3通过的）
                overnight_df = layer5_overnight_score(l2_codes, trade_date, self.cfg)
                l5_codes = overnight_df['ts_code'].tolist() if not overnight_df.empty else []
                layer_stats['L5_count'].append(len(l5_codes))

                # ── 8. 融合评分 ──
                result_df = normalize_and_fuse(factor_df, launch_df, llm_boosts, overnight_df)
                layer_stats['final_count'].append(len(result_df))

                # ── 9. 开仓 ──
                if not result_df.empty:
                    for _, row in result_df.iterrows():
                        if pm.open_position_count >= self.cfg.max_positions:
                            break
                        if row['ts_code'] in pm.positions:
                            continue

                        # T+1开盘价
                        next_idx = i + 1
                        if next_idx >= len(trading_days):
                            continue
                        next_date = trading_days[next_idx]
                        entry_price = self._get_open_price(row['ts_code'], next_date)
                        if entry_price is None or entry_price <= 0:
                            continue

                        # 仓位
                        position_value = self.cfg.initial_capital * self.cfg.single_position_pct
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
                            ts_code=row['ts_code'],
                            entry_date=next_date,
                            entry_price=entry_price_adj,
                            shares=shares,
                            meta_score=row.get('meta_score', 0),
                            launch_score=row.get('launch_score', 0),
                            factor_score=row.get('factor_score', 0),
                        )

                # ── 10. 记录权益 ──
                equity = self._calc_equity(pm, capital, trade_date)
                daily_equity.append({'date': str(trade_date), 'equity': equity,
                                     'positions': pm.open_position_count})

                # 进度
                if (i + 1) % 5 == 0 or i == len(trading_days) - 1:
                    elapsed = time.time() - t_start
                    avg = elapsed / (i + 1)
                    remaining = avg * (len(trading_days) - i - 1)
                    logger.info(
                        f"进度 {i+1}/{len(trading_days)} ({trade_date}): "
                        f"持仓{pm.open_position_count}只 权益{equity:,.0f} "
                        f"剩余{remaining:.0f}s")

                # 定期清理缓存
                if (i + 1) % 10 == 0:
                    clear_cache()

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
        logout()

        # 汇总
        summary = self._build_summary(all_trades, daily_equity, layer_stats, total_elapsed)

        return {
            'trades': all_trades,
            'daily_equity': pd.DataFrame(daily_equity),
            'summary': summary,
            'layer_stats': layer_stats,
        }

    def _update_position_prices(self, pm: PositionManager, trade_date: date):
        """更新持仓的当前价格（供退出评估用）"""
        start_date = trade_date - timedelta(days=60)
        for ts_code in list(pm.positions.keys()):
            try:
                df = get_daily_quotes_cached(ts_code, start_date, trade_date)
                if not df.empty:
                    # 更新position manager内部需要的价格数据
                    # position_manager.evaluate_exits 会自己加载价格
                    pass
            except Exception:
                pass

    def _get_open_price(self, ts_code: str, trade_date: date) -> Optional[float]:
        """获取开盘价"""
        start = trade_date - timedelta(days=5)
        df = get_daily_quotes(ts_code, start, trade_date,
                              fields="date,open,close")
        if df.empty:
            return None
        # 取最后一天
        last = df.iloc[-1]
        return float(last['open']) if pd.notna(last['open']) else None

    def _get_close_price(self, ts_code: str, trade_date: date) -> Optional[float]:
        """获取收盘价"""
        start = trade_date - timedelta(days=5)
        df = get_daily_quotes(ts_code, start, trade_date,
                              fields="date,close")
        if df.empty:
            return None
        last = df.iloc[-1]
        return float(last['close']) if pd.notna(last['close']) else None

    def _calc_equity(self, pm: PositionManager, cash: float,
                     eval_date: date) -> float:
        """计算总权益"""
        equity = cash
        for ts_code, pos in pm.positions.items():
            price = self._get_close_price(ts_code, eval_date)
            if price:
                equity += price * pos.shares
            else:
                equity += pos.entry_price * pos.shares
        return equity

    def _build_summary(self, trades: List[Dict], daily_equity: List[Dict],
                       layer_stats: Dict, elapsed: float) -> str:
        """构建回测汇总"""
        lines = []
        lines.append("=" * 70)
        lines.append("  融合元策略回测汇总 (Baostock)")
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

            # 退出原因分布
            exit_reasons = {}
            for t in trades:
                reason = t['exit_reason'].split('(')[0]
                exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
            lines.append("--- 退出原因分布 ---")
            for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
                lines.append(f"  {reason}: {count} ({count/len(trades):.1%})")
            lines.append("")

            # 收益分布
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

        # 权益曲线
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

        # 各层漏斗统计
        lines.append("--- 各层漏斗平均通过数 ---")
        for layer, counts in layer_stats.items():
            if counts and isinstance(counts, list):
                avg = np.mean(counts) if counts else 0
                lines.append(f"  {layer}: 平均 {avg:.0f} 只/日")
            elif isinstance(counts, (int, float)):
                lines.append(f"  {layer}: {counts}")
        lines.append("")

        lines.append("=" * 70)
        return "\n".join(lines)


# ============================================================
# CLI入口
# ============================================================

def run_backtest():
    """运行回测"""
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    cfg = MetaBacktestConfig()
    pm_cfg = PositionManagerConfig()

    backtester = BaostockBacktester(cfg, pm_cfg)
    result = backtester.run()

    if result:
        print(result['summary'])

        out_dir = Path('./results')
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(BEIJING_TZ).strftime('%Y%m%d_%H%M')

        if result['trades']:
            trades_df = pd.DataFrame(result['trades'])
            trades_path = out_dir / f"meta_bt_trades_{timestamp}.csv"
            trades_df.to_csv(trades_path, index=False, encoding='utf-8-sig')
            print(f"\n  交易记录: {trades_path}")

        if not result['daily_equity'].empty:
            eq_path = out_dir / f"meta_bt_equity_{timestamp}.csv"
            result['daily_equity'].to_csv(eq_path, index=False, encoding='utf-8-sig')
            print(f"  权益曲线: {eq_path}")

        report_path = out_dir / f"meta_bt_report_{timestamp}.txt"
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(result['summary'])
        print(f"  回测报告: {report_path}")

    return result


if __name__ == "__main__":
    run_backtest()
