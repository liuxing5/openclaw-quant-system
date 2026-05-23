"""
融合元策略回测引擎 v1.0
========================
严格 PIT 回测：
  - T 日收盘后产生信号（六层漏斗）
  - T+1 日开盘买入（用 T+1 开盘价）
  - 持仓管理模块每日评估退出
  - T+N 日开盘卖出（用 T+N 开盘价）

输出：
  - 胜率、平均收益、最大回撤
  - 各层漏斗通过率
  - 退出原因分布
  - 与单一策略对比
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
from psycopg2.extras import RealDictCursor

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db_fresh, close_db_session
from strategies.meta_strategy.meta_engine import (
    MetaStrategyEngine, MetaStrategyConfig, DEFAULT_META_CONFIG,
    check_market_risk, run_multi_factor_scan,
    run_fundamental_liquidity_filter, detect_launch_signals,
    get_llm_boost, compute_overnight_score, normalize_scores,
)
from strategies.meta_strategy.position_manager import (
    PositionManager, PositionManagerConfig, DEFAULT_PM_CONFIG,
)

logger = logging.getLogger(__name__)
BEIJING_TZ = timezone(timedelta(hours=8))


@dataclass
class BacktestConfig:
    """回测配置"""
    start_date: str = "2025-06-01"
    end_date: str = "2026-05-15"
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.001    # 买卖各0.1%
    slippage_pct: float = 0.001       # 滑点0.1%
    max_positions: int = 5
    single_position_pct: float = 0.20
    forward_return_days: List[int] = field(default_factory=lambda: [1, 3, 5, 10, 20])

    # 对比策略
    compare_factor_only: bool = True     # 仅多因子扫描器
    compare_overnight_only: bool = True  # 仅八步法评分


DEFAULT_BT_CONFIG = BacktestConfig()


class MetaBacktester:
    """融合元策略回测器"""

    def __init__(self, meta_cfg: MetaStrategyConfig = None,
                 pm_cfg: PositionManagerConfig = None,
                 bt_cfg: BacktestConfig = None):
        self.meta_cfg = meta_cfg or DEFAULT_META_CONFIG
        self.pm_cfg = pm_cfg or DEFAULT_PM_CONFIG
        self.bt_cfg = bt_cfg or DEFAULT_BT_CONFIG
        self.engine = MetaStrategyEngine(self.meta_cfg)

    def run(self, verbose: bool = True) -> Dict:
        """
        回测主循环

        返回: {
            'trades': List[dict],
            'daily_equity': DataFrame,
            'summary': str,
            'layer_stats': dict,
            'comparison': dict,
        }
        """
        logger.info("=" * 60)
        logger.info(f"融合元策略回测 {self.bt_cfg.start_date} ~ {self.bt_cfg.end_date}")
        logger.info("=" * 60)

        start_date = date.fromisoformat(self.bt_cfg.start_date)
        end_date = date.fromisoformat(self.bt_cfg.end_date)

        # 获取交易日列表
        trading_days = self._get_trading_days(start_date, end_date)
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

        # 对比策略结果
        factor_only_trades = []
        overnight_only_trades = []

        t_total_start = time.time()

        for i, trade_date in enumerate(trading_days):
            try:
                t1 = time.time()

                # ── 1. 评估退出条件 ──
                exit_signals = pm.evaluate_exits(trade_date)
                for sig in exit_signals:
                    # T+1开盘卖出，这里用当日开盘价近似
                    exit_price = self._get_open_price(sig.ts_code, trade_date)
                    if exit_price is None:
                        exit_price = sig.current_price  # 降级用收盘价

                    # 扣除滑点和佣金
                    exit_price_adj = exit_price * (1 - self.bt_cfg.slippage_pct)
                    commission = exit_price_adj * sig.details.get('shares', 100) * self.bt_cfg.commission_rate

                    record = pm.close_position(
                        sig.ts_code, trade_date, exit_price_adj, sig.exit_reason)
                    if record:
                        record['commission'] = commission
                        record['slippage'] = self.bt_cfg.slippage_pct
                        all_trades.append(record)
                        # 回收资金
                        capital += exit_price_adj * record.get('shares', 100) - commission

                # ── 2. 生成新信号（T日收盘后） ──
                # 检查大盘风控
                market_risk = check_market_risk(trade_date, self.meta_cfg)
                if not market_risk['passed']:
                    layer_stats['L0_reject'] += 1
                    # 记录当日权益
                    equity = self._calc_equity(pm, capital, trade_date)
                    daily_equity.append({'date': str(trade_date), 'equity': equity,
                                         'positions': pm.open_position_count})
                    continue

                # 运行六层漏斗
                result_df = self.engine.run(trade_date, verbose=False)

                # 记录各层数据
                stats = self.engine.stats
                layer_stats['L1_count'].append(stats.get('layer1_count', 0))
                layer_stats['L2_count'].append(stats.get('layer2_count', 0))
                layer_stats['L3_count'].append(stats.get('layer3_count', 0))
                layer_stats['L4_covered'].append(stats.get('layer4_covered', 0))
                layer_stats['L5_count'].append(stats.get('layer5_count', 0))
                layer_stats['final_count'].append(stats.get('final_count', 0))

                # ── 3. 开仓（T+1开盘买入） ──
                if not result_df.empty:
                    for _, row in result_df.iterrows():
                        if pm.open_position_count >= self.bt_cfg.max_positions:
                            break
                        if row['ts_code'] in pm.positions:
                            continue

                        # T+1开盘价
                        next_idx = i + 1
                        if next_idx >= len(trading_days):
                            continue
                        next_date = trading_days[next_idx]
                        entry_price = self._get_open_price(row['ts_code'], next_date)
                        if entry_price is None:
                            continue

                        # 仓位计算
                        position_value = self.bt_cfg.initial_capital * self.bt_cfg.single_position_pct
                        shares = int(position_value / (entry_price * 100)) * 100  # 整手
                        if shares <= 0:
                            shares = 100

                        # 扣除滑点和佣金
                        entry_price_adj = entry_price * (1 + self.bt_cfg.slippage_pct)
                        commission = entry_price_adj * shares * self.bt_cfg.commission_rate
                        cost = entry_price_adj * shares + commission

                        if cost > capital:
                            continue

                        capital -= cost

                        pm.positions[row['ts_code']] = __import__(
                            'strategies.meta_strategy.position_manager', fromlist=['Position']
                        ).Position(
                            ts_code=row['ts_code'],
                            entry_date=next_date,
                            entry_price=entry_price_adj,
                            shares=shares,
                            meta_score=row.get('meta_score', 0),
                            tags=row.get('tags', []),
                            launch_score=row.get('launch_score', 0),
                            factor_score=row.get('factor_score', 0),
                        )

                # ── 4. 对比策略信号 ──
                if self.bt_cfg.compare_factor_only:
                    factor_df = run_multi_factor_scan(trade_date, self.meta_cfg)
                    if not factor_df.empty:
                        for _, row in factor_df.head(5).iterrows():
                            factor_only_trades.append({
                                'ts_code': row['ts_code'],
                                'signal_date': str(trade_date),
                                'factor_score': row.get('total_score', 0),
                            })

                if self.bt_cfg.compare_overnight_only:
                    # 用全部活跃标的做八步法评分
                    active_codes = self._get_active_codes(trade_date)
                    if active_codes:
                        ov_df = compute_overnight_score(active_codes[:200], trade_date, self.meta_cfg)
                        if not ov_df.empty:
                            for _, row in ov_df.sort_values('overnight_score', ascending=False).head(5).iterrows():
                                overnight_only_trades.append({
                                    'ts_code': row['ts_code'],
                                    'signal_date': str(trade_date),
                                    'overnight_score': row.get('overnight_score', 0),
                                })

                # ── 5. 记录当日权益 ──
                equity = self._calc_equity(pm, capital, trade_date)
                daily_equity.append({'date': str(trade_date), 'equity': equity,
                                     'positions': pm.open_position_count})

                # 进度
                if (i + 1) % 20 == 0 or i == len(trading_days) - 1:
                    elapsed = time.time() - t_total_start
                    avg_per_day = elapsed / (i + 1)
                    remaining = avg_per_day * (len(trading_days) - i - 1)
                    logger.info(
                        f"进度 {i+1}/{len(trading_days)} ({trade_date}): "
                        f"持仓{pm.open_position_count}只 权益{equity:,.0f} "
                        f"已用{elapsed:.0f}s 剩余{remaining:.0f}s")

            except Exception as e:
                import traceback
                logger.warning(f"{trade_date} 回测失败: {e}")
                logger.warning(traceback.format_exc())

        # 强制平仓所有剩余持仓
        for ts_code in list(pm.positions.keys()):
            pos = pm.positions[ts_code]
            last_price = self._get_close_price(ts_code, end_date)
            if last_price:
                record = pm.close_position(ts_code, end_date, last_price, '回测结束')
                if record:
                    all_trades.append(record)

        total_elapsed = time.time() - t_total_start

        # ── 汇总统计 ──
        summary = self._build_summary(
            all_trades, daily_equity, layer_stats, total_elapsed)

        # ── 对比分析 ──
        comparison = self._build_comparison(
            all_trades, factor_only_trades, overnight_only_trades, trading_days)

        return {
            'trades': all_trades,
            'daily_equity': pd.DataFrame(daily_equity),
            'summary': summary,
            'layer_stats': layer_stats,
            'comparison': comparison,
        }

    def _get_trading_days(self, start: date, end: date) -> List[date]:
        """获取交易日列表"""
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT DISTINCT trade_date
                FROM daily_quotes
                WHERE trade_date >= %s AND trade_date <= %s
                ORDER BY trade_date;
            """, (start, end))
            days = [row['trade_date'] if isinstance(row['trade_date'], date)
                    else date.fromisoformat(str(row['trade_date']))
                    for row in cur.fetchall()]
            cur.close()
            return days
        except Exception as e:
            logger.error(f"获取交易日失败: {e}")
            return []
        finally:
            if conn and not conn.closed:
                conn.close()

    def _get_open_price(self, ts_code: str, trade_date: date) -> Optional[float]:
        """获取开盘价"""
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT open FROM daily_quotes
                WHERE ts_code = %s AND trade_date = %s;
            """, (ts_code, trade_date))
            row = cur.fetchone()
            cur.close()
            if row and row['open']:
                return float(row['open'])
            return None
        except Exception:
            return None
        finally:
            if conn and not conn.closed:
                conn.close()

    def _get_close_price(self, ts_code: str, trade_date: date) -> Optional[float]:
        """获取收盘价"""
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT close FROM daily_quotes
                WHERE ts_code = %s AND trade_date = %s;
            """, (ts_code, trade_date))
            row = cur.fetchone()
            cur.close()
            if row and row['close']:
                return float(row['close'])
            return None
        except Exception:
            return None
        finally:
            if conn and not conn.closed:
                conn.close()

    def _get_active_codes(self, trade_date: date, min_amount: float = 5e7) -> List[str]:
        """获取当日活跃标的"""
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT ts_code FROM daily_quotes
                WHERE trade_date = %s AND amount > %s
                ORDER BY amount DESC;
            """, (trade_date, min_amount))
            codes = [row['ts_code'] for row in cur.fetchall()]
            cur.close()
            return codes
        except Exception:
            return []
        finally:
            if conn and not conn.closed:
                conn.close()

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
        lines.append("  融合元策略回测汇总")
        lines.append("=" * 70)
        lines.append(f"  回测区间: {self.bt_cfg.start_date} ~ {self.bt_cfg.end_date}")
        lines.append(f"  初始资金: {self.bt_cfg.initial_capital:,.0f}")
        lines.append(f"  回测耗时: {elapsed:.1f}s")
        lines.append("")

        # 交易统计
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
                reason = t['exit_reason'].split('(')[0]  # 取原因前缀
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
            initial = self.bt_cfg.initial_capital
            final = eq_df['equity'].iloc[-1]
            total_return = (final - initial) / initial
            peak = eq_df['equity'].max()
            max_dd = (eq_df['equity'] - eq_df['equity'].cummax()).min() / eq_df['equity'].cummax().max()

            lines.append("--- 权益曲线 ---")
            lines.append(f"  初始权益: {initial:,.0f}")
            lines.append(f"  最终权益: {final:,.0f}")
            lines.append(f"  总收益率: {total_return:.2%}")
            lines.append(f"  最大回撤: {max_dd:.2%}")
            lines.append(f"  权益峰值: {peak:,.0f}")
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

    def _build_comparison(self, meta_trades: List[Dict],
                          factor_trades: List[Dict],
                          overnight_trades: List[Dict],
                          trading_days: List[date]) -> Dict:
        """构建策略对比"""
        comparison = {
            'meta_strategy': self._calc_strategy_stats(meta_trades),
            'factor_only_signals': len(factor_trades),
            'overnight_only_signals': len(overnight_trades),
        }

        # 计算对比策略的前向收益
        if factor_trades:
            factor_returns = self._calc_signal_forward_returns(
                [t['ts_code'] for t in factor_trades],
                [t['signal_date'] for t in factor_trades],
                trading_days)
            comparison['factor_only_stats'] = self._calc_return_stats(factor_returns)

        if overnight_trades:
            overnight_returns = self._calc_signal_forward_returns(
                [t['ts_code'] for t in overnight_trades],
                [t['signal_date'] for t in overnight_trades],
                trading_days)
            comparison['overnight_only_stats'] = self._calc_return_stats(overnight_returns)

        return comparison

    def _calc_strategy_stats(self, trades: List[Dict]) -> Dict:
        """计算策略统计"""
        if not trades:
            return {'total_trades': 0, 'win_rate': 0, 'avg_return': 0}

        pnls = [t['pnl_pct'] for t in trades]
        return {
            'total_trades': len(trades),
            'win_rate': round(np.mean([p > 0 for p in pnls]), 4) if pnls else 0,
            'avg_return': round(np.mean(pnls), 4) if pnls else 0,
            'median_return': round(np.median(pnls), 4) if pnls else 0,
            'max_return': round(max(pnls), 4) if pnls else 0,
            'min_return': round(min(pnls), 4) if pnls else 0,
            'avg_holding_days': round(np.mean([t['holding_days'] for t in trades]), 1),
        }

    def _calc_signal_forward_returns(self, ts_codes: List[str],
                                      signal_dates: List[str],
                                      trading_days: List[date]) -> List[float]:
        """计算信号的前向收益（5日后）"""
        trading_day_idx = {str(d): i for i, d in enumerate(trading_days)}
        returns = []

        for ts_code, sig_date in zip(ts_codes, signal_dates):
            idx = trading_day_idx.get(sig_date)
            if idx is None:
                continue
            target_idx = idx + 5  # 5日后
            if target_idx >= len(trading_days):
                continue

            entry_price = self._get_close_price(ts_code, trading_days[idx])
            exit_price = self._get_close_price(ts_code, trading_days[target_idx])
            if entry_price and exit_price and entry_price > 0:
                returns.append((exit_price - entry_price) / entry_price)

        return returns

    def _calc_return_stats(self, returns: List[float]) -> Dict:
        """计算收益统计"""
        if not returns:
            return {'count': 0, 'win_rate': 0, 'avg_return': 0}
        rets = np.array(returns)
        return {
            'count': len(rets),
            'win_rate': round(float(np.mean(rets > 0)), 4),
            'avg_return': round(float(np.mean(rets)), 4),
            'median_return': round(float(np.median(rets)), 4),
        }


# ============================================================
# CLI入口
# ============================================================

def run_backtest():
    """运行回测"""
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    meta_cfg = MetaStrategyConfig()
    pm_cfg = PositionManagerConfig()
    bt_cfg = BacktestConfig()

    backtester = MetaBacktester(meta_cfg, pm_cfg, bt_cfg)
    result = backtester.run(verbose=True)

    if result:
        print(result['summary'])

        # 保存结果
        out_dir = Path('./results')
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(BEIJING_TZ).strftime('%Y%m%d_%H%M')

        # 交易记录
        if result['trades']:
            trades_df = pd.DataFrame(result['trades'])
            trades_path = out_dir / f"meta_backtest_trades_{timestamp}.csv"
            trades_df.to_csv(trades_path, index=False, encoding='utf-8-sig')
            print(f"\n✓ 交易记录: {trades_path}")

        # 权益曲线
        if not result['daily_equity'].empty:
            eq_path = out_dir / f"meta_backtest_equity_{timestamp}.csv"
            result['daily_equity'].to_csv(eq_path, index=False, encoding='utf-8-sig')
            print(f"✓ 权益曲线: {eq_path}")

        # 对比结果
        if result.get('comparison'):
            comp_path = out_dir / f"meta_backtest_comparison_{timestamp}.json"
            with open(comp_path, 'w', encoding='utf-8') as f:
                json.dump(result['comparison'], f, ensure_ascii=False, indent=2)
            print(f"✓ 对比结果: {comp_path}")

        # 汇总报告
        report_path = out_dir / f"meta_backtest_report_{timestamp}.txt"
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(result['summary'])
        print(f"✓ 回测报告: {report_path}")

    return result


if __name__ == "__main__":
    run_backtest()
