"""融合元策略回测引擎 v4.1 - 简化版
====================================
核心优化：
  1. 不预加载全部数据，按需查询
  2. 使用db_data_adapter按天查询
  3. 优化止损参数和仓位管理
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

# Ensure DB env vars are set before any imports
if not os.getenv('POSTGRES_HOST'):
    os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
    os.environ['POSTGRES_PORT'] = '5432'
    os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
    os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
    os.environ['POSTGRES_DB'] = 'postgres'
    os.environ['POSTGRES_SSLMODE'] = 'require'

from strategies.meta_strategy.meta_engine import (
    MetaStrategyEngine, MetaStrategyConfig, DEFAULT_META_CONFIG,
    check_market_risk,
)
from strategies.meta_strategy.position_manager import (
    PositionManager, PositionManagerConfig, DEFAULT_PM_CONFIG, Position,
)
from strategies.meta_strategy.db_data_adapter import (
    get_trading_days, get_active_stocks, get_market_overview,
    get_daily_quotes_for_date, get_daily_quotes, get_daily_quotes_batch, clear_cache,
)

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """回测配置"""
    start_date: str = "2026-01-01"
    end_date: str = "2026-05-15"
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.001
    slippage_pct: float = 0.001
    max_positions: int = 8
    single_position_pct: float = 0.125
    forward_return_days: List[int] = field(default_factory=lambda: [1, 3, 5, 10, 20])
    min_meta_score: float = 0.0  # 最低meta_score过滤


DEFAULT_BT_CONFIG = BacktestConfig()


class FastBacktester:
    """高性能回测器 - 按需加载"""

    def __init__(self, meta_cfg: MetaStrategyConfig = None,
                 pm_cfg: PositionManagerConfig = None,
                 bt_cfg: BacktestConfig = None):
        self.meta_cfg = meta_cfg or DEFAULT_META_CONFIG
        self.pm_cfg = pm_cfg or DEFAULT_PM_CONFIG
        self.bt_cfg = bt_cfg or DEFAULT_BT_CONFIG
        self.engine = MetaStrategyEngine(self.meta_cfg)

        # 价格缓存：只缓存需要的股票
        self._price_cache: Dict[str, Dict[date, dict]] = {}

    def _preload_prices(self, ts_codes: List[str], start_date: date, end_date: date):
        """批量预加载股票价格数据到缓存，避免逐日逐股查DB"""
        if not ts_codes:
            return
        # 过滤已缓存的股票
        missing = [c for c in ts_codes if c not in self._price_cache]
        if not missing:
            return

        try:
            batch_data = get_daily_quotes_batch(missing, start_date, end_date)
            for code, df in batch_data.items():
                if code not in self._price_cache:
                    self._price_cache[code] = {}
                for _, row in df.iterrows():
                    td = row['trade_date'] if isinstance(row['trade_date'], date) else date.fromisoformat(str(row['trade_date']))
                    self._price_cache[code][td] = {
                        'open': float(row['open']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'close': float(row['close']),
                        'amount': float(row['amount']),
                        'pct_chg': float(row['pct_chg']),
                    }
            logger.info(f"预加载{len(missing)}只股票价格数据完成")
        except Exception as e:
            logger.warning(f"预加载价格数据失败: {e}")

    def _get_price(self, ts_code: str, trade_date: date, field: str) -> Optional[float]:
        """获取价格字段，缓存未命中时从DB查询"""
        if ts_code in self._price_cache and trade_date in self._price_cache[ts_code]:
            return self._price_cache[ts_code][trade_date].get(field)
        
        # 从DB查询该股票该天的数据
        try:
            df = get_daily_quotes(ts_code, trade_date - timedelta(days=1), trade_date)
            if not df.empty:
                last_row = df.iloc[-1]
                if ts_code not in self._price_cache:
                    self._price_cache[ts_code] = {}
                self._price_cache[ts_code][trade_date] = {
                    'open': float(last_row['open']),
                    'high': float(last_row['high']),
                    'low': float(last_row['low']),
                    'close': float(last_row['close']),
                    'amount': float(last_row['amount']),
                    'pct_chg': float(last_row['pct_chg']),
                }
                return self._price_cache[ts_code][trade_date].get(field)
            else:
                logger.warning(f"查询 {ts_code} {trade_date} 价格: 无数据 (查询范围 {trade_date - timedelta(days=1)} ~ {trade_date})")
        except Exception as e:
            logger.warning(f"查询 {ts_code} {trade_date} 价格失败: {e}")
        return None

    def _calc_equity(self, pm: PositionManager, cash: float, eval_date: date) -> float:
        equity = cash
        for ts_code, pos in pm.positions.items():
            price = self._get_price(ts_code, eval_date, 'close')
            if price:
                equity += price * pos.shares
            else:
                equity += pos.entry_price * pos.shares
        return equity

    def run(self, verbose: bool = True) -> Dict:
        """回测主循环"""
        logger.info("=" * 60)
        logger.info(f"融合元策略回测 v4.1 {self.bt_cfg.start_date} ~ {self.bt_cfg.end_date}")
        logger.info("=" * 60)

        start_date = date.fromisoformat(self.bt_cfg.start_date)
        end_date = date.fromisoformat(self.bt_cfg.end_date)

        # 获取交易日
        logger.info("获取交易日...")
        trading_days = get_trading_days(start_date, end_date)
        if not trading_days:
            logger.error("无交易日数据")
            return {}
        logger.info(f"交易日: {len(trading_days)} 天")

        # 初始化
        pm = PositionManager(self.pm_cfg)
        pm.cfg.max_positions = self.bt_cfg.max_positions

        capital = self.bt_cfg.initial_capital
        daily_equity = []
        all_trades = []
        layer_stats = {'L0_reject': 0, 'L1_count': [], 'L2_count': [],
                       'L3_count': [], 'L4_covered': [], 'L5_count': [],
                       'final_count': []}

        t_total_start = time.time()

        for i, trade_date in enumerate(trading_days):
            try:
                # ── 1. 评估退出条件 ──
                # 1a. 日内大跌止损：如果当日跌幅>8%（相对买入价），当日收盘价卖出
                #     用户反馈：002565在1月13日跌-10%未卖，14日又跌-10%才卖
                #     关键改进：当日收盘卖出而非次日开盘，避免连续跌停扩大亏损
                for ts_code in list(pm.positions.keys()):
                    pos = pm.positions[ts_code]
                    close_price = self._get_price(ts_code, trade_date, 'close')
                    if close_price is None:
                        continue
                    holding_days = (trade_date - pos.entry_date).days
                    if holding_days == 0:
                        continue  # A股T+1，买入当天不能卖
                    intraday_pnl = (close_price - pos.entry_price) / pos.entry_price
                    if intraday_pnl < -0.08:
                        # 日内大跌>8%，当日收盘价卖出
                        exit_price_adj = close_price * (1 - self.bt_cfg.slippage_pct)
                        commission = exit_price_adj * pos.shares * self.bt_cfg.commission_rate
                        record = pm.close_position(
                            ts_code, trade_date, exit_price_adj,
                            f'日内止损(亏损{intraday_pnl:.1%})')
                        if record:
                            record['commission'] = commission
                            record['slippage'] = self.bt_cfg.slippage_pct
                            all_trades.append(record)
                            capital += exit_price_adj * record.get('shares', pos.shares) - commission
                            logger.info(f"  日内止损 {ts_code}: {trade_date} @ {exit_price_adj:.2f} 亏损{intraday_pnl:+.2%}")

                # 1b. 常规退出条件评估
                # 关键改进：如果退出原因是移动止盈/破位放量/高量阴线（当日大跌触发），
                # 应当日收盘卖出而非次日开盘，避免次日继续大跌扩大亏损
                exit_signals = pm.evaluate_exits(trade_date)
                for sig in exit_signals:
                    # 判断是否当日收盘卖出
                    sell_same_day = any(trigger in sig.exit_reason for trigger in
                        ['移动止盈', '破位放量', '高量阴线', '隔夜止损'])

                    if sell_same_day:
                        # 当日收盘卖出
                        exit_date = trade_date
                        exit_price = self._get_price(sig.ts_code, trade_date, 'close')
                        if exit_price is None:
                            exit_price = sig.current_price
                    else:
                        # T日评估，T+1日开盘卖出（MACD死叉、时间止损等非紧急退出）
                        next_idx = i + 1
                        if next_idx < len(trading_days):
                            exit_date = trading_days[next_idx]
                            exit_price = self._get_price(sig.ts_code, exit_date, 'open')
                        else:
                            exit_date = trade_date
                            exit_price = sig.current_price

                    if exit_price is None:
                        exit_price = sig.current_price
                        exit_date = trade_date

                    exit_price_adj = exit_price * (1 - self.bt_cfg.slippage_pct)
                    pos = pm.positions.get(sig.ts_code)
                    shares = pos.shares if pos else 100
                    commission = exit_price_adj * shares * self.bt_cfg.commission_rate

                    record = pm.close_position(
                        sig.ts_code, exit_date, exit_price_adj, sig.exit_reason)
                    if record:
                        record['commission'] = commission
                        record['slippage'] = self.bt_cfg.slippage_pct
                        all_trades.append(record)
                        capital += exit_price_adj * record.get('shares', shares) - commission
                        logger.info(f"  卖出 {sig.ts_code}: {exit_date} @ {exit_price_adj:.2f} {sig.exit_reason} 盈亏{record['pnl_pct']:+.2%}")
                    else:
                        logger.warning(f"  卖出失败 {sig.ts_code}: 不在持仓中")

                # ── 2. 生成新信号 ──
                market_risk = check_market_risk(trade_date, self.meta_cfg)
                if not market_risk['passed']:
                    layer_stats['L0_reject'] += 1
                    equity = self._calc_equity(pm, capital, trade_date)
                    daily_equity.append({'date': str(trade_date), 'equity': equity,
                                         'positions': pm.open_position_count})
                    if (i + 1) % 10 == 0:
                        logger.info(f"  [{i+1}/{len(trading_days)}] {trade_date} 风控不通过 权益{equity:,.0f}")
                    continue

                result_df = self.engine.run(trade_date, verbose=False)

                stats = self.engine._stats
                layer_stats['L1_count'].append(stats.get('layer1_count', 0))
                layer_stats['L2_count'].append(stats.get('layer2_count', 0))
                layer_stats['L3_count'].append(stats.get('layer3_count', 0))
                layer_stats['L4_covered'].append(stats.get('layer4_covered', 0))
                layer_stats['L5_count'].append(stats.get('layer5_count', 0))
                layer_stats['final_count'].append(stats.get('final_count', 0))

                # ── 3. 开仓 ──
                if not result_df.empty:
                    for _, row in result_df.iterrows():
                        ts_code = row['ts_code']
                        if pm.open_position_count >= self.bt_cfg.max_positions:
                            break
                        if ts_code in pm.positions:
                            continue

                        # meta_score过滤：只买高质量候选
                        if self.bt_cfg.min_meta_score > 0:
                            score = row.get('meta_score', 0)
                            if score < self.bt_cfg.min_meta_score:
                                logger.debug(f"  {ts_code} meta_score过滤: {score:.1f} < {self.bt_cfg.min_meta_score}")
                                continue

                        next_idx = i + 1
                        if next_idx >= len(trading_days):
                            continue
                        next_date = trading_days[next_idx]

                        entry_price = self._get_price(ts_code, next_date, 'open')
                        if entry_price is None or (isinstance(entry_price, float) and entry_price != entry_price):  # NaN check
                            logger.debug(f"  {ts_code} 无T+1开盘价: {next_date}")
                            continue

                        if entry_price <= 0:
                            logger.debug(f"  {ts_code} 开盘价无效: {entry_price}")
                            continue

                        signal_close = row.get('close', 0)
                        signal_pct = float(row.get('pct_chg', 0))

                        # 信号衰减保护：信号14:30产生，14:50买入，如果次日开盘相对信号日收盘跌>阈值，信号作废
                        # 防止涨停后连续下跌时仍买入（如000030: 3/31涨停→4/1跌-6.47%→4/2仍买入）
                        # 创业板/科创板波动大，阈值放宽到-5%；主板-3%
                        is_kcb_or_cyb = ts_code.startswith('300') or ts_code.startswith('301') or ts_code.startswith('688')
                        decay_threshold = -0.05 if is_kcb_or_cyb else -0.03
                        if signal_close > 0:
                            gap_pct = (entry_price - signal_close) / signal_close
                            if gap_pct < decay_threshold:
                                logger.info(f"  {ts_code} 信号衰减: 次日开盘{entry_price:.2f}比信号日收盘{signal_close:.2f}跌{gap_pct:+.2%}>{decay_threshold:.0%}, 放弃买入")
                                continue

                        # 涨停板保护：如果信号日已涨停（主板≥9.8% / 创业板≥19.8%），次日不追涨
                        limit_pct = 19.8 if is_kcb_or_cyb else 9.8
                        if signal_pct >= limit_pct:
                            logger.info(f"  {ts_code} 涨停板保护: 信号日涨{signal_pct:+.2f}%已达涨停(>={limit_pct}%), 不追涨")
                            continue

                        position_value = capital * self.bt_cfg.single_position_pct
                        shares = int(position_value / (entry_price * 100)) * 100
                        if shares <= 0:
                            shares = 100

                        entry_price_adj = entry_price * (1 + self.bt_cfg.slippage_pct)
                        commission = entry_price_adj * shares * self.bt_cfg.commission_rate
                        cost = entry_price_adj * shares + commission

                        if cost > capital:
                            logger.info(f"  {ts_code} 资金不足: 需要{cost:,.0f} 可用{capital:,.0f}")
                            continue

                        logger.info(f"  买入 {ts_code}: {next_date} @ {entry_price_adj:.2f} x{shares} 持仓{pm.open_position_count+1}")
                        capital -= cost

                        pm.positions[row['ts_code']] = Position(
                            ts_code=row['ts_code'],
                            entry_date=next_date,
                            entry_price=entry_price_adj,
                            shares=shares,
                            meta_score=row.get('meta_score', 0),
                            tags=row.get('tags', []),
                            launch_score=row.get('launch_score', 0),
                            factor_score=row.get('factor_score', 0),
                        )

                # 预加载持仓股票的价格数据（批量查询，避免逐日逐股查DB）
                held_codes = list(pm.positions.keys())
                if held_codes:
                    self._preload_prices(held_codes, trade_date, end_date)

                # ── 4. 记录当日权益 ──
                equity = self._calc_equity(pm, capital, trade_date)
                daily_equity.append({'date': str(trade_date), 'equity': equity,
                                     'positions': pm.open_position_count})

                if (i + 1) % 5 == 0 or i == len(trading_days) - 1:
                    elapsed = time.time() - t_total_start
                    logger.info(
                        f"  [{i+1}/{len(trading_days)}] {trade_date}: "
                        f"持仓{pm.open_position_count}只 权益{equity:,.0f} "
                        f"已用{elapsed:.0f}s")

            except Exception as e:
                import traceback
                logger.warning(f"{trade_date} 回测失败: {e}")
                logger.warning(traceback.format_exc())

        # 强制平仓
        for ts_code in list(pm.positions.keys()):
            pos = pm.positions[ts_code]
            last_price = self._get_price(ts_code, end_date, 'close')
            if last_price:
                record = pm.close_position(ts_code, end_date, last_price, '回测结束')
                if record:
                    all_trades.append(record)
                    logger.info(f"  强制平仓 {ts_code}: {end_date} @ {last_price:.2f} 盈亏{record['pnl_pct']:+.2%}")
            else:
                logger.warning(f"  强制平仓 {ts_code}: 无法获取{end_date}收盘价")

        total_elapsed = time.time() - t_total_start

        summary = self._build_summary(all_trades, daily_equity, layer_stats, total_elapsed)

        return {
            'trades': all_trades,
            'daily_equity': pd.DataFrame(daily_equity),
            'summary': summary,
            'layer_stats': layer_stats,
        }

    def _build_summary(self, trades: List[Dict], daily_equity: List[Dict],
                       layer_stats: Dict, elapsed: float) -> str:
        lines = []
        lines.append("=" * 70)
        lines.append("  融合元策略回测汇总 v4.1")
        lines.append("=" * 70)
        lines.append(f"  回测区间: {self.bt_cfg.start_date} ~ {self.bt_cfg.end_date}")
        lines.append(f"  初始资金: {self.bt_cfg.initial_capital:,.0f}")
        lines.append(f"  回测耗时: {elapsed:.1f}s")
        lines.append("")

        if trades:
            pnls = [t['pnl_pct'] for t in trades if isinstance(t.get('pnl_pct'), (int, float)) and t['pnl_pct'] == t['pnl_pct']]  # filter NaN
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
            eq_df['equity'] = pd.to_numeric(eq_df['equity'], errors='coerce')
            eq_df = eq_df.dropna(subset=['equity'])
            if not eq_df.empty:
                initial = self.bt_cfg.initial_capital
                final = eq_df['equity'].iloc[-1]
                total_return = (final - initial) / initial
                max_dd = ((eq_df['equity'] - eq_df['equity'].cummax()) / eq_df['equity'].cummax()).min()

                lines.append("--- 权益曲线 ---")
                lines.append(f"  初始权益: {initial:,.0f}")
                lines.append(f"  最终权益: {final:,.0f}")
                lines.append(f"  总收益率: {total_return:.2%}")
                lines.append(f"  最大回撤: {max_dd:.2%}")
                lines.append("")

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


def run_backtest():
    """运行回测"""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    meta_cfg = MetaStrategyConfig(
        layer0_min_advancers=1200,
        layer1_min_total_score=0.45,
        layer1_rsi_max=70.0,
        layer2_min_avg_amount_20d=5e7,
        layer2_turn_rate_min=2.0,
        layer2_turn_rate_max=20.0,
        layer3_volume_breakout_mult=2.0,
        layer3_min_launch_score=0.25,
        layer5_min_quant_score=85,
        layer6_hard_stop_loss_pct=0.05,
        layer6_overnight_stop_pct=0.03,
        layer6_trailing_activate_pct=0.08,
        layer6_trailing_stop_pct=0.05,
        layer6_max_holding_days=20,
        max_final_candidates=5,
    )

    pm_cfg = PositionManagerConfig(
        hard_stop_loss_pct=0.05,
        trailing_activate_pct=0.08,
        trailing_stop_pct=0.05,
        max_holding_days=20,
        overnight_stop_pct=0.03,
        max_positions=5,
        single_position_pct=0.20,
    )

    bt_cfg = BacktestConfig(
        start_date="2025-05-01",
        end_date="2026-05-26",
        initial_capital=1_000_000.0,
        max_positions=5,
        single_position_pct=0.20,
    )

    backtester = FastBacktester(meta_cfg, pm_cfg, bt_cfg)
    result = backtester.run()

    if result:
        print(result['summary'])

        output_dir = Path('./results')
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M')
        report_path = output_dir / f"meta_bt_report_{ts}.txt"
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(result['summary'])
        logger.info(f"报告已保存: {report_path}")

        if result['trades']:
            # JSON 格式保存
            trades_path = output_dir / f"meta_bt_trades_{ts}.json"
            with open(trades_path, 'w', encoding='utf-8') as f:
                json.dump(result['trades'], f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"交易明细已保存: {trades_path}")

            # CSV 格式保存
            import pandas as pd
            trades_df = pd.DataFrame(result['trades'])
            csv_path = output_dir / f"meta_bt_trades_{ts}.csv"
            trades_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
            logger.info(f"交易CSV已保存: {csv_path}")

            # 控制台输出逐笔交易
            print("\n" + "=" * 100)
            print("  逐笔交易明细")
            print("=" * 100)
            print(f"{'序号':<5} {'代码':<12} {'买入日期':<12} {'买入价':<10} {'卖出日期':<12} {'卖出价':<10} {'收益%':<10} {'持仓天':<8} {'退出原因':<20}")
            print("-" * 100)
            for idx, t in enumerate(result['trades'], 1):
                pnl = t.get('pnl_pct', 0)
                if isinstance(pnl, float) and pnl == pnl:  # not NaN
                    pnl_str = f"{pnl:+.2%}"
                else:
                    pnl_str = "N/A"
                print(f"{idx:<5} {t.get('ts_code',''):<12} {str(t.get('entry_date','')):<12} {t.get('entry_price',0):<10.2f} {str(t.get('exit_date','')):<12} {t.get('exit_price',0):<10.2f} {pnl_str:<10} {t.get('holding_days',0):<8} {t.get('exit_reason',''):<20}")
            print("=" * 100)


if __name__ == '__main__':
    run_backtest()
