"""
融合元策略 - 持仓管理模块 v1.0
================================
融合主升浪C/D层 + 八步法止损逻辑

退出条件（任一触发即卖出）：
  E1 硬止损: 浮亏 >= 8%（八步法2.5%硬止损可选）
  E2 移动止盈: 浮盈 >= 8% 后从最高点回撤 >= 5%
  E3 时间止损: 持仓 >= 15 个交易日
  E4 MACD死叉: DIF 下穿 DEA
  E5 破位放量: 跌破5日均线且量比 >= 1.2
  E6 高量阴线: 量比 >= 3 且收阴
  E7 持续性衰减: 主升浪C层指标恶化（缩量下跌+板块走弱）

信号链路：
  买入日 T+1 09:30: 开盘买入
  持仓期间每日盘后: 持仓管理模块评估退出条件
  卖出日 T+N 09:30: 开盘卖出
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

# 优先使用 PostgreSQL 数据源，baostock 不可用时降级
try:
    from core.db.connection import get_db
    _USE_DB = True
except ImportError:
    _USE_DB = False
    try:
        from strategies.meta_strategy.baostock_data import get_daily_quotes_cached
        _USE_BAOSTOCK = True
    except ImportError:
        _USE_BAOSTOCK = False
        get_db = None

logger = logging.getLogger(__name__)
BEIJING_TZ = timezone(timedelta(hours=8))


@dataclass
class Position:
    """持仓记录"""
    ts_code: str
    entry_date: date
    entry_price: float
    shares: int = 100
    highest_price: float = 0.0
    stop_loss_price: float = 0.0
    trailing_stop_price: float = 0.0
    meta_score: float = 0.0
    tags: List[str] = field(default_factory=list)
    # 主升浪C层指标快照
    launch_score: float = 0.0
    factor_score: float = 0.0

    def __post_init__(self):
        if self.highest_price == 0:
            self.highest_price = self.entry_price


@dataclass
class ExitSignal:
    """卖出信号"""
    ts_code: str
    exit_date: date
    exit_reason: str
    entry_price: float
    current_price: float
    pnl_pct: float
    holding_days: int
    details: Dict = field(default_factory=dict)


@dataclass
class PositionManagerConfig:
    """持仓管理配置"""
    # 硬止损
    hard_stop_loss_pct: float = 0.08          # 浮亏8%止损
    aggressive_stop_loss_pct: float = 0.025    # 八步法2.5%硬止损（可选）

    # 移动止盈
    trailing_activate_pct: float = 0.08        # 浮盈8%后激活
    trailing_stop_pct: float = 0.05            # 从最高点回撤5%止盈

    # 时间止损
    max_holding_days: int = 15                 # 最长持仓15天

    # MACD死叉
    enable_macd_exit: bool = True
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # 破位放量
    enable_breakdown_exit: bool = True
    breakdown_ma_period: int = 5
    breakdown_vol_ratio_min: float = 1.2

    # 高量阴线
    enable_high_vol_bearish: bool = True
    high_vol_ratio_min: float = 3.0

    # 持续性衰减
    enable_sustain_decay: bool = True
    sustain_decay_adx_drop: float = 10.0      # ADX从高点下降10点
    sustain_decay_volume_shrink: float = 0.5   # 量缩到50%以下

    # 八步法隔夜止损
    enable_overnight_stop: bool = True
    overnight_stop_pct: float = 0.03          # 次日亏损>3%出局

    # 仓位管理
    max_positions: int = 5                     # 最大持仓数
    single_position_pct: float = 0.20          # 单只仓位20%


DEFAULT_PM_CONFIG = PositionManagerConfig()


def _ema(arr: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1)
    out = np.empty(len(arr), dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


class PositionManager:
    """持仓管理器"""

    def __init__(self, cfg: PositionManagerConfig = None):
        self.cfg = cfg or DEFAULT_PM_CONFIG
        self.positions: Dict[str, Position] = {}
        self.closed_positions: List[Dict] = []

    def open_position(self, ts_code: str, entry_date: date,
                      entry_price: float, meta_score: float = 0,
                      tags: List[str] = None, launch_score: float = 0,
                      factor_score: float = 0) -> bool:
        """开仓"""
        if ts_code in self.positions:
            logger.warning(f"{ts_code} 已持仓，跳过")
            return False
        if len(self.positions) >= self.cfg.max_positions:
            logger.info(f"持仓已满({self.cfg.max_positions})，跳过 {ts_code}")
            return False

        pos = Position(
            ts_code=ts_code,
            entry_date=entry_date,
            entry_price=entry_price,
            highest_price=entry_price,
            stop_loss_price=entry_price * (1 - self.cfg.hard_stop_loss_pct),
            meta_score=meta_score,
            tags=tags or [],
            launch_score=launch_score,
            factor_score=factor_score,
        )
        self.positions[ts_code] = pos
        return True

    def evaluate_exits(self, eval_date: date) -> List[ExitSignal]:
        """
        评估所有持仓的退出条件
        返回需要退出的信号列表
        """
        if not self.positions:
            return []

        exit_signals = []
        ts_codes = list(self.positions.keys())

        # 批量加载行情数据
        price_data = self._load_price_data(ts_codes, eval_date)

        for ts_code in ts_codes:
            pos = self.positions[ts_code]
            prices = price_data.get(ts_code)

            if prices is None or prices.empty:
                continue

            current_price = float(prices['close'].iloc[-1])
            if current_price <= 0:
                continue

            # 更新最高价
            if current_price > pos.highest_price:
                pos.highest_price = current_price

            # 计算盈亏
            pnl_pct = (current_price - pos.entry_price) / pos.entry_price
            holding_days = (eval_date - pos.entry_date).days

            # 逐项检查退出条件
            exit_signal = self._check_exit_conditions(
                pos, eval_date, current_price, pnl_pct, holding_days, prices)

            if exit_signal:
                exit_signals.append(exit_signal)

        return exit_signals

    def _check_exit_conditions(
        self, pos: Position, eval_date: date,
        current_price: float, pnl_pct: float,
        holding_days: int, prices: pd.DataFrame
    ) -> Optional[ExitSignal]:
        """逐项检查退出条件"""

        # A股T+1规则：买入当天(holding_days=0)绝对不能卖出
        if holding_days == 0:
            return None

        # 八步法隔夜止损（次日开盘检查）
        if (self.cfg.enable_overnight_stop and
            holding_days == 1 and
            pnl_pct < -self.cfg.overnight_stop_pct):
            return ExitSignal(
                ts_code=pos.ts_code, exit_date=eval_date,
                exit_reason=f'隔夜止损(亏损{pnl_pct:.1%})',
                entry_price=pos.entry_price,
                current_price=current_price,
                pnl_pct=pnl_pct, holding_days=holding_days,
                details={'trigger': 'overnight_stop'})

        # E2: 移动止盈
        if pnl_pct >= self.cfg.trailing_activate_pct:
            drawdown = (pos.highest_price - current_price) / pos.highest_price
            if drawdown >= self.cfg.trailing_stop_pct:
                return ExitSignal(
                    ts_code=pos.ts_code, exit_date=eval_date,
                    exit_reason=f'移动止盈(从高点回撤{drawdown:.1%})',
                    entry_price=pos.entry_price,
                    current_price=current_price,
                    pnl_pct=pnl_pct, holding_days=holding_days,
                    details={'trigger': 'trailing_stop', 'drawdown': drawdown})

        # 以下条件需要足够的历史数据
        close = prices['close'].values.astype(float)
        n = len(close)

        # E3: 时间止损 — 但如果趋势仍在（收盘>5日均线且盈利），延长持仓
        if holding_days >= self.cfg.max_holding_days:
            # 趋势延续判断：收盘价在5日均线之上且盈利
            if n >= self.cfg.breakdown_ma_period:
                ma5 = pd.Series(close).rolling(self.cfg.breakdown_ma_period).mean().values
                if current_price > ma5[-1] and pnl_pct > 0:
                    # 趋势仍在，不触发时间止损，继续持有
                    pass
                else:
                    return ExitSignal(
                        ts_code=pos.ts_code, exit_date=eval_date,
                        exit_reason=f'时间止损(持仓{holding_days}天)',
                        entry_price=pos.entry_price,
                        current_price=current_price,
                        pnl_pct=pnl_pct, holding_days=holding_days,
                        details={'trigger': 'time_stop'})
            else:
                return ExitSignal(
                    ts_code=pos.ts_code, exit_date=eval_date,
                    exit_reason=f'时间止损(持仓{holding_days}天)',
                    entry_price=pos.entry_price,
                    current_price=current_price,
                    pnl_pct=pnl_pct, holding_days=holding_days,
                    details={'trigger': 'time_stop'})

        # E4: MACD死叉
        if self.cfg.enable_macd_exit and n >= self.cfg.macd_slow + self.cfg.macd_signal:
            dif = _ema(close, self.cfg.macd_fast) - _ema(close, self.cfg.macd_slow)
            dea = _ema(dif, self.cfg.macd_signal)
            if len(dif) >= 2 and len(dea) >= 2:
                if dif[-2] > dea[-2] and dif[-1] <= dea[-1]:
                    return ExitSignal(
                        ts_code=pos.ts_code, exit_date=eval_date,
                        exit_reason='MACD死叉',
                        entry_price=pos.entry_price,
                        current_price=current_price,
                        pnl_pct=pnl_pct, holding_days=holding_days,
                        details={'trigger': 'macd_death_cross'})

        # E5: 破位放量
        if self.cfg.enable_breakdown_exit and n >= self.cfg.breakdown_ma_period + 1:
            ma5 = pd.Series(close).rolling(self.cfg.breakdown_ma_period).mean().values
            amount = prices['amount'].values.astype(float) if 'amount' in prices.columns else None
            if amount is not None and len(amount) >= 20:
                vol_ratio = amount[-1] / (amount[-21:-1].mean() + 1e-9)
                if (current_price < ma5[-1] and
                    vol_ratio >= self.cfg.breakdown_vol_ratio_min):
                    return ExitSignal(
                        ts_code=pos.ts_code, exit_date=eval_date,
                        exit_reason=f'破位放量(量比{vol_ratio:.1f})',
                        entry_price=pos.entry_price,
                        current_price=current_price,
                        pnl_pct=pnl_pct, holding_days=holding_days,
                        details={'trigger': 'breakdown_volume'})

        # E6: 高量阴线
        if self.cfg.enable_high_vol_bearish and n >= 20:
            amount = prices['amount'].values.astype(float) if 'amount' in prices.columns else None
            if amount is not None:
                vol_ratio = amount[-1] / (amount[-21:-1].mean() + 1e-9)
                pct_chg = float(prices['pct_chg'].iloc[-1]) if 'pct_chg' in prices.columns else 0
                if vol_ratio >= self.cfg.high_vol_ratio_min and pct_chg < 0:
                    return ExitSignal(
                        ts_code=pos.ts_code, exit_date=eval_date,
                        exit_reason=f'高量阴线(量比{vol_ratio:.1f}跌幅{pct_chg:.1f}%)',
                        entry_price=pos.entry_price,
                        current_price=current_price,
                        pnl_pct=pnl_pct, holding_days=holding_days,
                        details={'trigger': 'high_vol_bearish'})

        # E7: 持续性衰减
        if self.cfg.enable_sustain_decay and n >= 30:
            high = prices['high'].values.astype(float)
            low = prices['low'].values.astype(float)
            # ADX计算
            p = 14
            tr = np.maximum(high[1:] - low[1:],
                            np.maximum(np.abs(high[1:] - close[:-1]),
                                       np.abs(low[1:] - close[:-1])))
            up = high[1:] - high[:-1]
            dn = low[:-1] - low[1:]
            atr = _ema(tr, p)
            pdi = 100 * _ema(np.where((up > dn) & (up > 0), up, 0.0), p) / (atr + 1e-9)
            mdi = 100 * _ema(np.where((dn > up) & (dn > 0), dn, 0.0), p) / (atr + 1e-9)
            adx = _ema(100 * np.abs(pdi - mdi) / (pdi + mdi + 1e-9), p)

            if len(adx) >= 5:
                adx_drop = adx[-5] - adx[-1]
                amount = prices['amount'].values.astype(float) if 'amount' in prices.columns else None
                if amount is not None and len(amount) >= 2:
                    vol_shrink = amount[-1] / (amount[-2] + 1e-9)
                    if (adx_drop >= self.cfg.sustain_decay_adx_drop and
                        vol_shrink < self.cfg.sustain_decay_volume_shrink):
                        return ExitSignal(
                            ts_code=pos.ts_code, exit_date=eval_date,
                            exit_reason=f'持续性衰减(ADX降{adx_drop:.0f}量缩{vol_shrink:.1%})',
                            entry_price=pos.entry_price,
                            current_price=current_price,
                            pnl_pct=pnl_pct, holding_days=holding_days,
                            details={'trigger': 'sustain_decay'})

        return None

    def close_position(self, ts_code: str, exit_date: date,
                       exit_price: float, exit_reason: str = '') -> Optional[Dict]:
        """平仓"""
        if ts_code not in self.positions:
            return None

        pos = self.positions.pop(ts_code)
        pnl_pct = (exit_price - pos.entry_price) / pos.entry_price
        holding_days = (exit_date - pos.entry_date).days

        record = {
            'ts_code': ts_code,
            'entry_date': str(pos.entry_date),
            'entry_price': pos.entry_price,
            'exit_date': str(exit_date),
            'exit_price': exit_price,
            'shares': pos.shares,
            'pnl_pct': round(pnl_pct, 4),
            'holding_days': holding_days,
            'exit_reason': exit_reason,
            'meta_score': pos.meta_score,
            'highest_price': pos.highest_price,
        }
        self.closed_positions.append(record)
        return record

    def _load_price_data(self, ts_codes: List[str], eval_date: date,
                         lookback: int = 60) -> Dict[str, pd.DataFrame]:
        """批量加载行情数据（优先PostgreSQL，降级baostock）"""
        start_date = eval_date - timedelta(days=lookback * 2)
        cache = {}

        if _USE_DB and get_db is not None:
            # 优先使用 PostgreSQL - 使用 db_data_adapter 的批量查询
            try:
                from strategies.meta_strategy.db_data_adapter import get_daily_quotes_batch
                batch = get_daily_quotes_batch(ts_codes, start_date, eval_date)
                for code, df in batch.items():
                    for c in ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']:
                        if c in df.columns:
                            df[c] = pd.to_numeric(df[c], errors='coerce')
                    cache[code] = df.tail(lookback)
            except Exception as e:
                logger.warning(f"DB行情数据加载失败: {e}")
        elif not _USE_DB:
            # 降级到 baostock
            for ts_code in ts_codes:
                try:
                    df = get_daily_quotes_cached(
                        ts_code, start_date, eval_date,
                        fields="date,open,high,low,close,volume,amount,pctChg,turn")
                    if not df.empty:
                        rename = {'date': 'trade_date', 'pctChg': 'pct_chg',
                                  'turn': 'turnover_rate'}
                        df = df.rename(columns={k: v for k, v in rename.items()
                                                if k in df.columns})
                        for c in ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']:
                            if c in df.columns:
                                df[c] = pd.to_numeric(df[c], errors='coerce')
                        cache[ts_code] = df.tail(lookback)
                except Exception as e:
                    logger.debug(f"baostock加载{ts_code}失败: {e}")

        return cache

    @property
    def open_position_count(self) -> int:
        return len(self.positions)

    @property
    def total_pnl(self) -> float:
        """已平仓总盈亏"""
        if not self.closed_positions:
            return 0.0
        return sum(r['pnl_pct'] for r in self.closed_positions)

    @property
    def win_rate(self) -> float:
        """胜率"""
        if not self.closed_positions:
            return 0.0
        wins = sum(1 for r in self.closed_positions if r['pnl_pct'] > 0)
        return wins / len(self.closed_positions)

    @property
    def avg_holding_days(self) -> float:
        """平均持仓天数"""
        if not self.closed_positions:
            return 0.0
        return sum(r['holding_days'] for r in self.closed_positions) / len(self.closed_positions)
