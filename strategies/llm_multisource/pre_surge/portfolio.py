"""
风控 + 组合管理
================================================
项目硬规则:
  - 单笔最大风险 2%
  - 30% 现金储备(雷打不动)
  - 单日亏损 5% 自动熔断
  - 总回撤 15% 自动熔断
  - 严格 T+1
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from config import BacktestConfig
from exitor import Position

logger = logging.getLogger(__name__)


@dataclass
class Account:
    cash: float
    initial_capital: float
    peak_value: float = 0.0
    daily_start_value: float = 0.0
    positions: dict = field(default_factory=dict)   # symbol -> Position
    locked_today: dict = field(default_factory=dict)  # T+1: 当日买入不能卖
    trade_log: list = field(default_factory=list)
    halted: bool = False
    halt_reason: str = ""

    def total_value(self, prices: dict) -> float:
        equity = self.cash
        for sym, pos in self.positions.items():
            equity += pos.shares * prices.get(sym, pos.entry_price)
        return equity

    def reset_daily_marks(self, current_value: float) -> None:
        self.daily_start_value = current_value
        if current_value > self.peak_value:
            self.peak_value = current_value
        self.locked_today = {}


class RiskManager:
    def __init__(self, cfg: Optional[BacktestConfig] = None):
        self.cfg = cfg or BacktestConfig()

    # -------------------- 头寸规模 --------------------
    def position_size(self, account: Account, entry_price: float,
                      stop_price: float) -> int:
        """
        基于固定风险公式:
            risk_per_trade × equity = (entry - stop) × shares
        约束:
            1. 不能动用现金储备 (cash_reserve_ratio)
            2. 单仓不超过总资产 / max_concurrent_positions × 1.2
        """
        if entry_price <= stop_price:
            return 0
        if entry_price <= 0:
            return 0
        equity = account.total_value({})  # 现金口径已足够保守
        risk_dollar = equity * self.cfg.risk_per_trade
        per_share_risk = entry_price - stop_price
        raw_shares = int(risk_dollar / per_share_risk)

        # 现金储备约束
        usable_cash = account.cash - account.initial_capital * self.cfg.cash_reserve_ratio
        if usable_cash <= 0:
            return 0
        max_by_cash = int(usable_cash / entry_price)

        # 单仓上限约束
        max_by_concentration = int(
            (equity / self.cfg.max_concurrent_positions * 1.2) / entry_price
        )

        shares = min(raw_shares, max_by_cash, max_by_concentration)
        # A 股 100 股一手
        shares = (shares // 100) * 100
        return max(shares, 0)

    # -------------------- 熔断检查 --------------------
    def check_circuit_breaker(self, account: Account,
                              current_value: float) -> tuple[bool, str]:
        # 单日亏损
        if account.daily_start_value > 0:
            daily_loss = (account.daily_start_value - current_value) / account.daily_start_value
            if daily_loss >= self.cfg.daily_loss_circuit_breaker:
                return True, f"单日亏损熔断 {daily_loss:.1%}"

        # 总回撤
        if account.peak_value > 0:
            drawdown = (account.peak_value - current_value) / account.peak_value
            if drawdown >= self.cfg.drawdown_circuit_breaker:
                return True, f"总回撤熔断 {drawdown:.1%}"
        return False, ""

    # -------------------- 交易成本 --------------------
    def buy_cost(self, price: float, shares: int) -> float:
        gross = price * shares
        commission = max(gross * self.cfg.commission_rate, 5.0)  # 最低 5 元
        slippage = gross * self.cfg.slippage_bps / 10000
        return gross + commission + slippage

    def sell_proceeds(self, price: float, shares: int) -> float:
        gross = price * shares
        commission = max(gross * self.cfg.commission_rate, 5.0)
        stamp = gross * self.cfg.stamp_tax_rate
        slippage = gross * self.cfg.slippage_bps / 10000
        return gross - commission - stamp - slippage


class PortfolioManager:
    def __init__(self, cfg: Optional[BacktestConfig] = None):
        self.cfg = cfg or BacktestConfig()
        self.risk = RiskManager(cfg)

    # -------------------- 开仓 --------------------
    def try_open(self, account: Account, symbol: str, name: str,
                 entry_price: float, stop_price: float,
                 trade_date: datetime) -> bool:
        if account.halted:
            return False
        if symbol in account.positions:
            return False
        if len(account.positions) >= self.cfg.max_concurrent_positions:
            return False

        shares = self.risk.position_size(account, entry_price, stop_price)
        if shares <= 0:
            return False

        cost = self.risk.buy_cost(entry_price, shares)
        if cost > account.cash:
            return False

        account.cash -= cost
        pos = Position(
            symbol=symbol, name=name,
            entry_date=trade_date, entry_price=entry_price,
            shares=shares, high_water_mark=entry_price,
        )
        account.positions[symbol] = pos
        account.locked_today[symbol] = True   # T+1 锁定
        account.trade_log.append({
            "date": trade_date, "action": "BUY", "symbol": symbol,
            "name": name, "price": entry_price, "shares": shares,
            "cost": cost, "cash_after": account.cash,
        })
        return True

    # -------------------- 平仓 --------------------
    def try_close(self, account: Account, symbol: str,
                  exit_price: float, trade_date: datetime,
                  reason: str) -> bool:
        if symbol not in account.positions:
            return False
        # T+1 检查
        if self.cfg.enforce_t_plus_1 and account.locked_today.get(symbol):
            return False

        pos = account.positions.pop(symbol)
        proceeds = self.risk.sell_proceeds(exit_price, pos.shares)
        account.cash += proceeds
        pnl = proceeds - pos.entry_price * pos.shares
        account.trade_log.append({
            "date": trade_date, "action": "SELL", "symbol": symbol,
            "name": pos.name, "price": exit_price, "shares": pos.shares,
            "proceeds": proceeds, "pnl": pnl, "reason": reason,
            "holding_days": pos.days_held,
            "cash_after": account.cash,
        })
        return True
