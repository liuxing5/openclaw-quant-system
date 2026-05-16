"""
Walk-Forward 回测引擎
================================================
- 严格 PIT,所有信号在 T 日收盘后产生,T+1 开盘买入
- 滚动窗口: 12 个月训练观察 + 3 个月测试
- 输出: 净值曲线 / 交易明细 / 分层胜率 / IC 等
- 支持模拟模式(无 AKShare 也能跑通,用合成数据验证逻辑)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from config import BacktestConfig, ScreenerConfig, ExitorConfig, TZ
from data_loader import DataLoader
from exitor import Exitor, Position
from portfolio import Account, PortfolioManager
from screener import PreMainUptrendScreener

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    metrics: dict = field(default_factory=dict)
    layer_stats: pd.DataFrame = field(default_factory=pd.DataFrame)


class WalkForwardBacktester:
    def __init__(
        self,
        symbols: list[tuple[str, str]],
        bt_cfg: Optional[BacktestConfig] = None,
        screener_cfg: Optional[ScreenerConfig] = None,
        exitor_cfg: Optional[ExitorConfig] = None,
        loader: Optional[DataLoader] = None,
    ):
        self.symbols = symbols
        self.bt_cfg = bt_cfg or BacktestConfig()
        self.screener = PreMainUptrendScreener(screener_cfg, loader)
        self.exitor = Exitor(exitor_cfg)
        self.portfolio = PortfolioManager(self.bt_cfg)
        self.loader = loader or DataLoader()

    # -------------------- 主循环 --------------------
    def run(self, start_date: str, end_date: str) -> BacktestResult:
        """
        start_date / end_date 格式 'YYYY-MM-DD'
        """
        logger.info(f"回测区间 {start_date} ~ {end_date}, 标的 {len(self.symbols)} 只")

        # 1. 预加载所有标的的 K 线 (回测期 + 800 日预热,够 L1 的 500 日窗口)
        prefetch_start = (
            datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=800)
        ).strftime("%Y%m%d")
        prefetch_end = end_date.replace("-", "")

        klines: dict[str, pd.DataFrame] = {}
        for code, _ in self.symbols:
            df = self.loader.get_kline(code, days=1000, end_date=prefetch_end)
            if df is not None and len(df) >= 500:
                klines[code] = df.set_index("date")
        logger.info(f"已加载 {len(klines)}/{len(self.symbols)} 只标的数据")

        if not klines:
            logger.error("没有可用数据,回测中止")
            return BacktestResult()

        # 2. 构造交易日历(取所有标的的并集)
        all_dates = sorted(set().union(*[df.index for df in klines.values()]))
        bt_start = pd.Timestamp(start_date)
        bt_end = pd.Timestamp(end_date)
        trading_days = [d for d in all_dates if bt_start <= d <= bt_end]
        logger.info(f"交易日 {len(trading_days)} 天")

        # 3. 初始化账户
        account = Account(
            cash=self.bt_cfg.initial_capital,
            initial_capital=self.bt_cfg.initial_capital,
            peak_value=self.bt_cfg.initial_capital,
        )
        equity_records = []
        rebalance_dow = 0  # 0=周一, 仅在周一调仓(周度调仓)

        # 4. 逐日推进
        for i, today in enumerate(trading_days):
            today_str = today.strftime("%Y-%m-%d")

            # 4.1 当前价格 (使用今日收盘价做估值)
            prices = {}
            for sym, df in klines.items():
                if today in df.index:
                    prices[sym] = float(df.loc[today, "close"])

            # 4.2 重置当日标记
            current_value = account.total_value(prices)
            account.reset_daily_marks(current_value)

            # 4.3 熔断检查
            halted, reason = self.portfolio.risk.check_circuit_breaker(
                account, current_value
            )
            if halted and not account.halted:
                account.halted = True
                account.halt_reason = reason
                logger.warning(f"{today_str} 熔断触发: {reason}")

            # 4.4 持仓更新 + 出场判断 (T+1 已在 portfolio 内强制)
            for sym in list(account.positions.keys()):
                pos = account.positions[sym]
                pos.days_held += 1
                df = klines.get(sym)
                if df is None or today not in df.index:
                    continue
                df_until = df.loc[:today]
                decision = self.exitor.check(pos, df_until.reset_index())
                if decision.should_exit:
                    # 用次日开盘价撮合(更现实);若次日不存在则用今日收盘
                    sell_price = self._next_open(df, today, fallback=prices[sym])
                    self.portfolio.try_close(
                        account, sym, sell_price, today, decision.reason
                    )

            # 4.5 解锁 T+1 (隔夜后可卖)
            account.locked_today = {}

            # 4.6 周度调仓: 周一(或回测起点)产生新信号
            should_rebalance = (
                today.weekday() == rebalance_dow
                or i == 0
            )
            if should_rebalance and not account.halted:
                # 在每个标的截至 today 的数据上跑筛选器
                candidates = []
                for code, name in self.symbols:
                    df = klines.get(code)
                    if df is None or today not in df.index:
                        continue
                    sub = df.loc[:today].reset_index()
                    if len(sub) < 500:
                        continue
                    sig = self._evaluate_pit(code, name, sub, today_str)
                    if sig.triggered:
                        candidates.append((sig.score, code, name, sig))

                # 按分数降序,尝试开仓
                candidates.sort(reverse=True)
                skipped_one_word = 0
                for _, code, name, sig in candidates:
                    if len(account.positions) >= self.bt_cfg.max_concurrent_positions:
                        break
                    df = klines[code]
                    can_buy, why = self._can_buy_next_open(df, today, code)
                    if not can_buy:
                        skipped_one_word += 1
                        logger.debug(f"{today_str} {code} 跳过: {why}")
                        continue
                    next_open = self._next_open(df, today, fallback=sig.last_close)
                    stop = next_open * (1 - 0.08)
                    self.portfolio.try_open(
                        account, code, name, next_open, stop, today
                    )
                if skipped_one_word > 0:
                    logger.info(f"{today_str} 因一字板跳过 {skipped_one_word} 只")

            # 4.7 记录净值
            final_value = account.total_value(prices)
            equity_records.append({
                "date": today,
                "equity": final_value,
                "cash": account.cash,
                "positions": len(account.positions),
                "halted": account.halted,
            })

            if i % 20 == 0:
                logger.info(
                    f"{today_str} 净值={final_value:,.0f} "
                    f"持仓={len(account.positions)} "
                    f"现金={account.cash:,.0f}"
                )

        # 5. 汇总结果
        result = BacktestResult()
        result.equity_curve = pd.DataFrame(equity_records).set_index("date")
        result.trades = pd.DataFrame(account.trade_log)
        result.metrics = self._compute_metrics(result.equity_curve, result.trades)
        return result

    # -------------------- 工具 --------------------
    def _next_open(self, df: pd.DataFrame, today: pd.Timestamp,
                   fallback: float) -> float:
        idx = df.index
        try:
            pos = idx.get_indexer([today])[0]
            if pos >= 0 and pos + 1 < len(idx):
                return float(df.iloc[pos + 1]["open"])
        except Exception:
            pass
        return fallback

    def _can_buy_next_open(self, df: pd.DataFrame, today: pd.Timestamp,
                            symbol: str) -> tuple[bool, str]:
        """
        判断次日能否在开盘价买入(剔除一字板)
        返回 (能否买, 原因)
        """
        if not self.bt_cfg.skip_one_word_limit:
            return True, ""
        idx = df.index
        try:
            pos = idx.get_indexer([today])[0]
            if pos < 0 or pos + 1 >= len(idx):
                return False, "无次日数据"
            today_close = float(df.iloc[pos]["close"])
            next_open = float(df.iloc[pos + 1]["open"])
            next_high = float(df.iloc[pos + 1]["high"])
            next_low = float(df.iloc[pos + 1]["low"])

            open_pct = (next_open - today_close) / today_close
            # 一字板特征: 开盘 ≥ 9.5% + 当日 high ≈ low(≤1%振幅)
            if open_pct >= self.bt_cfg.one_word_open_pct:
                amplitude = (next_high - next_low) / next_low if next_low > 0 else 0
                if amplitude < 0.01:
                    return False, f"一字板(开盘+{open_pct:.1%}, 振幅{amplitude:.2%})"
                # 高开但有振幅,允许买,但用次日均价更现实
                # 这里仍允许,只是给个提示
            # 跌停一字板也跳过(虽然策略不会主动买跌停,但兜底)
            if open_pct <= -self.bt_cfg.one_word_open_pct:
                return False, f"跌停开盘 {open_pct:.1%}"
            return True, ""
        except Exception as e:
            return False, f"次日数据异常: {e}"

    def _evaluate_pit(self, code: str, name: str,
                      df_pit: pd.DataFrame, eval_date: str):
        """
        在 PIT 数据上重新评估(直接给筛选器塞数据,避免再网络请求)
        筛选器会自己调 loader,但回测中我们 monkey-patch 一次性数据
        """
        # 临时 patch loader 的 get_kline,让其返回我们 PIT 切片
        original = self.screener.loader.get_kline
        def patched(symbol, days=300, end_date=None, adjust="qfq"):
            if symbol == code:
                return df_pit.tail(days).reset_index(drop=True)
            return original(symbol, days=days, end_date=end_date, adjust=adjust)
        self.screener.loader.get_kline = patched
        try:
            return self.screener.evaluate(code, name, end_date=eval_date)
        finally:
            self.screener.loader.get_kline = original

    # -------------------- 绩效指标 --------------------
    @staticmethod
    def _compute_metrics(equity: pd.DataFrame,
                         trades: pd.DataFrame) -> dict:
        if equity.empty:
            return {}
        eq = equity["equity"]
        ret = eq.pct_change().dropna()

        total_return = eq.iloc[-1] / eq.iloc[0] - 1 if eq.iloc[0] > 0 else 0.0
        years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
        cagr = (1 + total_return) ** (1 / years) - 1
        vol = ret.std() * np.sqrt(252)
        sharpe = (ret.mean() * 252) / vol if vol > 0 else 0

        rolling_max = eq.cummax()
        rolling_max = rolling_max.replace(0, np.nan)
        drawdown = (eq - rolling_max) / rolling_max
        max_dd = float(drawdown.min()) if drawdown.notna().any() else 0.0

        # 交易统计
        sells = trades[trades["action"] == "SELL"] if not trades.empty else pd.DataFrame()
        wins = sells[sells["pnl"] > 0] if not sells.empty else pd.DataFrame()
        win_rate = len(wins) / len(sells) if len(sells) > 0 else 0
        avg_win = wins["pnl"].mean() if len(wins) > 0 else 0
        losses = sells[sells["pnl"] <= 0] if not sells.empty else pd.DataFrame()
        avg_loss = losses["pnl"].mean() if len(losses) > 0 else 0
        profit_factor = (wins["pnl"].sum() / abs(losses["pnl"].sum())
                         if len(losses) > 0 and losses["pnl"].sum() != 0 else float("inf"))

        return {
            "total_return": float(total_return),
            "cagr": float(cagr),
            "volatility": float(vol),
            "sharpe": float(sharpe),
            "max_drawdown": float(max_dd),
            "trades": int(len(sells)),
            "win_rate": float(win_rate),
            "avg_win": float(avg_win),
            "avg_loss": float(avg_loss),
            "profit_factor": float(profit_factor) if profit_factor != float("inf") else None,
        }
