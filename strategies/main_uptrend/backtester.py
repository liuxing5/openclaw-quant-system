"""
主升浪策略回测引擎
====================
- 严格 PIT：T 日收盘后产生信号，T+1 开盘买入
- 评估启动后 10/20/60 日的胜率和收益分布
- 输出：命中率、胜率、收益分布、最大回撤、IC 分析
"""
from __future__ import annotations

import csv
import json
import logging
import time
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple

import numpy as np
import pandas as pd

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    tqdm = None

from .config import MainUptrendConfig, DEFAULT_CONFIG
from .data_loader import DataLoader, trading_days_before, trading_days_after
from .engine import MainUptrendEngine
from ..shared.backtest_stats import (
    net_return, fwd_stats, format_sig,
    COST_NOTE, SIGNIFICANCE_NOTE,
)

logger = logging.getLogger(__name__)


def _safe_tqdm(iterable, **kwargs):
    if HAS_TQDM:
        return tqdm(iterable, **kwargs)
    return iterable


class MainUptrendBacktester:
    """主升浪检测回测器"""

    def __init__(self, cfg: Optional[MainUptrendConfig] = None):
        self.cfg = cfg or DEFAULT_CONFIG
        self.loader = DataLoader()
        self.engine = MainUptrendEngine(self.cfg, self.loader)

    # ================================================================
    # 主回测循环
    # ================================================================
    def run(self, start_date: str = "2025-01-01",
            end_date: str = "2026-05-15",
            target_stocks: Optional[List[str]] = None) -> Dict:
        """
        回测主循环

        target_stocks: 如果指定，只评估这些标的（用于聚焦验证）；None=全市场

        返回:
          {
            'signals': List[dict],         # 所有发出的信号
            'forward_returns': dict,       # {10: DataFrame, 20: DataFrame, 60: DataFrame}
            'hit_rate': dict,              # {10: float, 20: float, 60: float}
            'win_rate': dict,              # {10: float, 20: float, 60: float}
            'return_distribution': dict,   # {10: [...], 20: [...], 60: [...]}
            'trading_days': list,          # 交易日历（显著性/非重叠子样本采样用）
            'summary': str,
          }
        """
        logger.info(f"=" * 60)
        logger.info(f"主升浪回测 {start_date} ~ {end_date}")
        logger.info(f"=" * 60)

        # ---- 预加载全量数据（核心优化） ----
        # 审查 7/8：回看窗口按交易日历取 ≥260 个交易日（52 周高点需 250 交易日
        # + 60 日均线/箱体预热），前向窗口向后延伸 70 个交易日（60 日持有 +
        # 缓冲），旧版 start-150 自然日 ≈ 100 交易日，既不够算 52 周高点，
        # 也让区间末尾信号的前向收益/退出全部落空。
        preload_start = trading_days_before(start_date, 260)
        preload_end = trading_days_after(end_date, 70)
        logger.info(f"预加载窗口: {preload_start} ~ {preload_end}"
                    f"（回看≥260交易日，前向+70交易日）")
        self.loader.preload_for_backtest(preload_start, preload_end)

        # 审查 20：指标索引为空说明预加载实际没跑成（静默降级会让回测变成
        # "全放行"的假回测），直接断言失败，不产出无意义的结果。
        assert self.loader._indicators_by_date, (
            f"预加载后指标索引为空（{preload_start} ~ {preload_end}），"
            "回测无法运行：B/C/D/E 层会退化为全放行"
        )

        # 通知引擎各层使用预加载模式
        self.engine.layer_c.skip_1min = True  # 回测模式跳过1分钟线

        # D层：一次性预加载全量风险数据
        self.engine.layer_d.preload_for_backtest(start_date, end_date)

        # 信号产生区间 vs 前向计算区间（审查 8）：信号只在 [start, end] 内产生，
        # 但索引列表额外含 end 之后的 70 个交易日，供前向收益/退出模拟取价。
        signal_days = self.loader.get_trading_days(start_date, end_date)
        all_days = self.loader.get_trading_days(start_date, preload_end)
        if not signal_days:
            logger.error("无交易日数据")
            return {}
        if len(all_days) < len(signal_days):   # 日历异常兜底
            all_days = signal_days

        trading_days = signal_days
        logger.info(f"交易日: {len(signal_days)} 天（前向索引共 {len(all_days)} 天）")

        # 构建交易日索引（避免list.index()的O(n)查找）
        trading_day_idx = {d: i for i, d in enumerate(all_days)}

        # 预加载 A 层池（全市场时做一次周频更新）
        pool_a_cache: Dict[str, Set[str]] = {}

        all_signals = []
        forward_data: Dict[int, List[Dict]] = {d: [] for d in self.cfg.forward_return_days}
        day_count = len(trading_days)
        t_day_start = 0
        t_total_start = time.time()

        # 审查 5：漏斗计数（候选 → 冷却后 → 信号 → 确认入场）
        funnel = {'candidates': 0, 'after_cooldown': 0, 'signals': 0, 'confirmed': 0}

        signal_cooldown: Dict[str, str] = {}
        cooldown_days = 20

        for i, trade_date in enumerate(_safe_tqdm(trading_days, desc="回测进度")):
            try:
                t1 = time.time()
                pool_a = self._get_pool_a(pool_a_cache, trade_date, target_stocks)

                candidates = self.engine.evaluate_single_day(trade_date, pool_a)
                t2 = time.time()

                if candidates:
                    funnel['candidates'] += len(candidates)
                    filtered = []
                    for c in candidates:
                        code = c['ts_code']
                        # 审查 17：冷却只检查、不在此写入——冷却日志在信号真正
                        # 产出收益记录（确认入场且有前向收益）后才登记，
                        # 否则一个被 T+1 破位否决的信号会白白吃掉 20 个交易日冷却。
                        last_date = signal_cooldown.get(code)
                        if last_date is not None and last_date in trading_day_idx and trade_date in trading_day_idx:
                            gap = trading_day_idx[trade_date] - trading_day_idx[last_date]
                            if gap < cooldown_days:
                                continue
                        filtered.append(c)

                    if filtered:
                        funnel['after_cooldown'] += len(filtered)
                        signals = self._calc_forward_returns(
                            filtered, trade_date, all_days, i,
                            trading_day_idx,
                        )
                        all_signals.extend(signals)
                        funnel['signals'] += len(signals)
                        funnel['confirmed'] += sum(1 for s in signals
                                                   if s.get('confirmed'))

                        # 审查 17：冷却登记后置——确认入场且至少有一个前向收益
                        # 的信号才进入冷却窗口。
                        for s in signals:
                            if not s.get('confirmed'):
                                continue
                            if any(s.get(f'ret_{d}d') is not None
                                   for d in self.cfg.forward_return_days):
                                signal_cooldown[s['ts_code']] = trade_date

                        # 2026-10-13 修复：这段循环原来在 `if filtered:` 之外，
                        # filtered 为空时会用上一次循环残留的 signals 重复计入前向收益样本
                        for s in signals:
                            for d in self.cfg.forward_return_days:
                                if s.get(f'ret_{d}d') is not None:
                                    forward_data[d].append({
                                        'ts_code': s['ts_code'],
                                        'signal_date': s['eval_date'],
                                        'composite_score': s['composite_score'],
                                        f'ret_{d}d': s[f'ret_{d}d'],
                                        f'ret_{d}d_gross': s.get(f'ret_{d}d_gross'),
                                        f'hit_{d}d': s.get(f'hit_{d}d', False),
                                    })

                # 每50天打印一次进度（避免日志过多）
                if (i + 1) % 50 == 0 or i == day_count - 1:
                    elapsed = time.time() - t_total_start
                    avg_per_day = elapsed / (i + 1) if i > 0 else 0
                    remaining = avg_per_day * (day_count - i - 1)
                    logger.info(f"进度 {i+1}/{day_count} ({trade_date}): "
                               f"单日耗时{t2-t1:.2f}s, "
                               f"候选{len(candidates)}只, "
                               f"总信号{len(all_signals)}个, "
                               f"已用{elapsed:.0f}s, 预计剩余{remaining:.0f}s")

            except Exception as e:
                import traceback
                logger.warning(f"{trade_date} 评估失败: {e}")
                logger.warning(traceback.format_exc())

        # 审查 30：全市场等权基准（须在 clear_cache 之前用预加载行情计算）
        benchmark = self._market_benchmark(trading_days)

        self.loader.clear_cache()

        # 汇总统计
        logger.info(f"信号漏斗: 候选{funnel['candidates']} → 冷却后{funnel['after_cooldown']} "
                    f"→ 信号{funnel['signals']} → 确认{funnel['confirmed']}")
        summary = self._build_summary(all_signals, forward_data,
                                      trading_days=trading_days, funnel=funnel)

        return {
            'signals': all_signals,
            'funnel': funnel,
            'forward_returns': {d: pd.DataFrame(forward_data[d]) for d in self.cfg.forward_return_days},
            'summary': summary,
            # 2026-10-13 参数审查：透出交易日历，供显著性统计做非重叠子样本采样
            'trading_days': trading_days,
            # 审查 30：全市场等权基准（{horizon: {count, mean, win_rate, ...}}）
            'benchmark': benchmark,
        }

    # ================================================================
    # 全市场等权基准（审查 30）
    # ================================================================
    def _market_benchmark(self, signal_days: List[str]) -> Dict[str, Dict]:
        """全市场等权基准：信号日 T+1 开盘入场、T+1+d 收盘出场，扣 0.25% 往返成本。

        口径（与策略同窗口）：
          - 标的池：当日成交额 >= 1e8 的全市场股票（复权价，前复权）；
          - 逐股取 T+1 开盘 / T+1+d 收盘，缺任一价格的样本剔除；
          - 每个信号日先算全市场等权平均毛收益，再统一扣成本得净收益。
        返回 {str(days): {count, mean, win_rate, ...}}。
        """
        out: Dict[str, Dict] = {}
        base = getattr(self.loader, '_preloaded_daily', None)
        if base is None or base.empty:
            logger.warning("基准：无预加载行情，跳过 benchmark 计算")
            return out

        cols = [c for c in ('ts_code', 'trade_date', 'open', 'close', 'amount')
                if c in base.columns]
        q = base[cols].copy()
        if 'open' not in q.columns or 'close' not in q.columns:
            logger.warning("基准：预加载行情缺 open/close，跳过")
            return out
        q = q.sort_values(['ts_code', 'trade_date'], kind='mergesort')
        g = q.groupby('ts_code', sort=False)
        amount_ok = q['amount'].astype(float) >= 1e8 if 'amount' in q.columns \
            else pd.Series(True, index=q.index)

        for days in self.cfg.forward_return_days:
            entry = g['open'].shift(-1)
            exit_ = g['close'].shift(-(1 + days))
            ok = amount_ok & entry.notna() & exit_.notna() & (entry > 0)
            fwd = pd.Series(np.nan, index=q.index)
            fwd[ok] = exit_[ok] / entry[ok] - 1.0
            per_day = pd.DataFrame({'trade_date': q['trade_date'], 'fwd': fwd})
            per_day = per_day[per_day['fwd'].notna()]
            if per_day.empty:
                continue
            by_date = per_day.groupby('trade_date')['fwd'].mean()

            gross, used = [], []
            for d in signal_days:
                v = by_date.get(d)
                if v is not None and np.isfinite(float(v)):
                    gross.append(float(v))
                    used.append(d)
            if not gross:
                continue
            net = [net_return(x) for x in gross]
            arr = np.array(net, dtype=float)
            out[str(days)] = {
                'count': int(len(arr)),
                'mean': float(np.mean(arr)),
                'win_rate': float(np.mean(arr > 0)),
                'median': float(np.median(arr)),
                'signal_days': len(used),
                'filter_note': '全市场等权，当日成交额>=1e8，T+1开盘入场/T+1+d收盘出场，扣0.25%往返成本（前复权价）',
            }
        if out:
            logger.info("基准: " + "; ".join(
                f"{d}日 n={v['count']} 均值={v['mean']:+.2%}" for d, v in out.items()))
        return out

    # ================================================================
    # A 层池管理（周频缓存）
    # ================================================================
    def _get_pool_a(self, cache: Dict[str, Set[str]], trade_date: str,
                    target_stocks: Optional[List[str]] = None) -> Set[str]:
        if target_stocks:
            return set(target_stocks)

        if trade_date in cache:
            return cache[trade_date]

        if self.cfg.a_enabled:
            pool = self.engine.layer_a.prescreen(trade_date)
        else:
            snapshot = self.loader.get_market_snapshot(trade_date, min_amount=0)
            pool = set(snapshot['ts_code'].tolist()) if not snapshot.empty else set()

        cache[trade_date] = pool
        return pool

    # ================================================================
    # 前向收益计算
    # ================================================================
    def _calc_forward_returns(self, candidates: List[Dict],
                               trade_date: str,
                               trading_days: List[str],
                               current_idx: int,
                               trading_day_idx: Optional[Dict[str, int]] = None) -> List[Dict]:
        results = []
        for c in candidates:
            ts_code = c['ts_code']
            entry_price = self._get_close_price(ts_code, trade_date)
            if entry_price is None:
                continue

            signal = {**c, 'entry_price': entry_price}

            # 审查 5：确认日检查——T+1收盘价仍站稳MA10才确认入场。被否决的
            # 信号**保留记录**（confirmed=False、entry_price=None、收益字段
            # 置 None），供漏斗统计"候选N→确认M"，不再静默丢弃。
            confirmed = False
            if current_idx + 1 < len(trading_days):
                next_date = trading_days[current_idx + 1]
                next_price = self._get_close_price(ts_code, next_date)
                next_ma10 = self._get_ma_price(ts_code, next_date, 'ma_10')
                if next_price is not None and next_ma10 is not None:
                    if next_price < next_ma10 * 0.98:
                        # T+1跌破MA10，信号未确认：保留骨架记录
                        signal['confirmed'] = False
                        signal['entry_price'] = None
                        signal['confirm_reject'] = 't1_below_ma10'
                        for days in self.cfg.forward_return_days:
                            signal[f'ret_{days}d'] = None
                            signal[f'ret_{days}d_gross'] = None
                            signal[f'hit_{days}d'] = False
                        results.append(signal)
                        continue
                    # 确认入场价用T+1收盘价（更真实）
                    entry_price = next_price
                    signal['entry_price'] = entry_price
                    confirmed = True
                    signal['confirmed'] = True
                    signal['confirm_date'] = next_date
                else:
                    signal['confirmed'] = False
            else:
                signal['confirmed'] = False

            if not confirmed:
                # 未确认（窗口末端无T+1 / 缺行情）：同样保留记录但不计收益
                signal['entry_price'] = None
                for days in self.cfg.forward_return_days:
                    signal[f'ret_{days}d'] = None
                    signal[f'ret_{days}d_gross'] = None
                    signal[f'hit_{days}d'] = False
                results.append(signal)
                continue

            for days in self.cfg.forward_return_days:
                target_idx = current_idx + days
                if target_idx >= len(trading_days):
                    signal[f'ret_{days}d'] = None
                    signal[f'hit_{days}d'] = False
                    continue

                exit_date = trading_days[target_idx]
                exit_price = self._get_close_price(ts_code, exit_date)
                if exit_price is None:
                    signal[f'ret_{days}d'] = None
                    signal[f'hit_{days}d'] = False
                    continue

                # 2026-10-13 参数审查（交易成本）：前向收益是"买入并持有该股 N 日"
                # 的买入持有口径，基准同口径，统一扣除 0.25% 往返成本后再统计；
                # 同时保留毛收益字段便于成本前后对比。
                gross_ret = (exit_price - entry_price) / entry_price
                ret = net_return(gross_ret)
                signal[f'ret_{days}d'] = ret
                signal[f'ret_{days}d_gross'] = gross_ret
                signal[f'hit_{days}d'] = ret > 0.05

            # 趋势跟踪退出：移动止盈 + 利润保护 + MA10快线
            max_hold = 60
            peak_ret = 0.0
            peak_day = 0
            exit_ret = None
            for offset in range(1, max_hold + 1):
                idx = current_idx + offset
                if idx >= len(trading_days):
                    break
                d = trading_days[idx]
                price = self._get_close_price(ts_code, d)
                if price is None:
                    continue
                ret = (price - entry_price) / entry_price
                if ret > peak_ret:
                    peak_ret = ret
                    peak_day = offset

                # 1. 硬止损：亏损7%无条件退出
                if ret < -0.07:
                    exit_ret = ret
                    signal['exit_type'] = 'stop_loss'
                    signal['exit_day'] = offset
                    signal['exit_ret'] = ret
                    signal['max_ret'] = peak_ret
                    break

                # 2. 移动止盈：从最高点回撤超过阈值则退出
                #    浮盈<5%时回撤3%退出，浮盈5-10%时回撤5%退出，浮盈>10%时回撤7%退出
                drawdown_from_peak = peak_ret - ret
                if peak_ret > 0.10:
                    trailing_stop = 0.07
                elif peak_ret > 0.05:
                    trailing_stop = 0.05
                else:
                    trailing_stop = 0.03

                if peak_ret > 0.02 and drawdown_from_peak > trailing_stop:
                    # 至少持有3天，避免被日内波动洗出
                    if offset >= 3:
                        exit_ret = ret
                        signal['exit_type'] = 'trailing_stop'
                        signal['exit_day'] = offset
                        signal['exit_ret'] = ret
                        signal['max_ret'] = peak_ret
                        break

                # 3. MA10快线退出（第5天后启用）：收盘跌破MA10×0.95
                if offset >= 5:
                    ma10_price = self._get_ma_price(ts_code, d, 'ma_10')
                    if ma10_price is not None and price < ma10_price * 0.95:
                        # 利润保护：已有3%+浮盈时不被MA10洗出
                        if ret < 0.03:
                            exit_ret = ret
                            signal['exit_type'] = 'ma10_exit'
                            signal['exit_day'] = offset
                            signal['exit_ret'] = ret
                            signal['max_ret'] = peak_ret
                            break

                # 4. MA20慢线退出（第10天后启用）：收盘跌破MA20×0.95
                if offset >= 10:
                    ma20_price = self._get_ma_price(ts_code, d, 'ma_20')
                    if ma20_price is not None and price < ma20_price * 0.95:
                        exit_ret = ret
                        signal['exit_type'] = 'ma20_exit'
                        signal['exit_day'] = offset
                        signal['exit_ret'] = ret
                        signal['max_ret'] = peak_ret
                        break

            # 审查 8：前向窗口不足 max_hold 时标注截断，不伪装成 max_hold 退出
            if exit_ret is None:
                last_idx = min(current_idx + max_hold, len(trading_days) - 1)
                last_price = self._get_close_price(ts_code, trading_days[last_idx])
                if last_price is not None:
                    exit_ret = (last_price - entry_price) / entry_price
                    if last_idx - current_idx < max_hold:
                        # 数据窗口到头（非策略规则退出），单独归类
                        signal['exit_type'] = 'window_end'
                        signal['exit_truncated'] = True
                    else:
                        signal['exit_type'] = 'max_hold'
                        signal['exit_truncated'] = False
                    signal['exit_day'] = last_idx - current_idx
                    signal['exit_ret'] = exit_ret
                    signal['max_ret'] = peak_ret

            # 2026-10-13 参数审查（交易成本）：对外报告的已实现退出收益统一扣
            # 0.25% 往返成本；max_ret 是持仓期间的未实现峰值（毛口径），保持不变。
            if exit_ret is not None:
                signal['exit_ret'] = net_return(exit_ret)

            results.append(signal)
        return results

    def _get_ma_price(self, ts_code: str, trade_date: str, ma_col: str) -> Optional[float]:
        ind_df = self.loader.get_indicators_snapshot(trade_date)
        if ind_df.empty:
            return None
        row = ind_df[ind_df['ts_code'] == ts_code]
        if row.empty or ma_col not in row.columns:
            return None
        val = row[ma_col].iloc[0]
        return float(val) if not pd.isna(val) else None

    def _get_close_price(self, ts_code: str, trade_date: str) -> Optional[float]:
        # 优先使用预加载的收盘价查找表
        price = self.loader.get_preloaded_close(ts_code, trade_date)
        if price is not None:
            return price

        # 降级到DB查询
        df = self.loader.get_daily(ts_code, start_date="2024-01-01", end_date=trade_date)
        if df is None or len(df) == 0:
            return None
        last = df[df['trade_date'].astype(str) == trade_date]
        if len(last) > 0:
            return float(last['close'].iloc[0])
        return None

    # ================================================================
    # 汇总统计
    # ================================================================
    def _build_summary(self, all_signals: List[Dict],
                       forward_data: Dict[int, List[Dict]],
                       trading_days: Optional[List[str]] = None,
                       funnel: Optional[Dict] = None) -> str:
        lines = []
        lines.append("=" * 60)
        lines.append("主升浪检测策略 — 回测汇总")
        lines.append("=" * 60)
        lines.append(f"回测区间: {self.cfg.backtest_start} ~ {self.cfg.backtest_end}")
        lines.append(f"总信号数: {len(all_signals)}")
        if funnel:
            # 审查 5：漏斗透明化——未确认信号也计入总数，避免"信号数=确认数"的错觉
            lines.append(f"信号漏斗: 候选{funnel.get('candidates', 0)}"
                         f" → 冷却后{funnel.get('after_cooldown', 0)}"
                         f" → 信号{funnel.get('signals', 0)}"
                         f" → 确认入场{funnel.get('confirmed', 0)}")
        lines.append(f"收益口径: {COST_NOTE}")
        lines.append(f"显著性: {SIGNIFICANCE_NOTE}")
        lines.append("")

        for days in self.cfg.forward_return_days:
            fd = forward_data.get(days, [])
            if not fd:
                continue

            returns = [d[f'ret_{days}d'] for d in fd if d.get(f'ret_{days}d') is not None]
            hits = [d[f'hit_{days}d'] for d in fd if d.get(f'hit_{days}d') is not None]

            if not returns:
                lines.append(f"[{days}日后] 无有效数据")
                continue

            rets = np.array(returns)
            win_rate = np.mean(rets > 0)
            hit_rate = np.mean(hits) if hits else 0
            mean_ret = np.mean(rets)
            median_ret = np.median(rets)
            max_ret = np.max(rets)
            min_ret = np.min(rets)
            std_ret = np.std(rets)

            at_3pct = np.mean(rets > 0.03)
            at_5pct = np.mean(rets > 0.05)
            at_10pct = np.mean(rets > 0.10)

            lines.append(f"--- {days}日后收益分布 ---")
            lines.append(f"  样本数: {len(rets)}")
            lines.append(f"  胜率(>0): {win_rate:.1%}")
            lines.append(f"  命中率(>5%): {hit_rate:.1%}")
            lines.append(f"  均值收益: {mean_ret:.2%}")
            lines.append(f"  中位数收益: {median_ret:.2%}")
            lines.append(f"  最大收益: {max_ret:.2%}")
            lines.append(f"  最小收益: {min_ret:.2%}")
            lines.append(f"  标准差: {std_ret:.2%}")
            lines.append(f"  收益>3%: {at_3pct:.1%}")
            lines.append(f"  收益>5%: {at_5pct:.1%}")
            lines.append(f"  收益>10%: {at_10pct:.1%}")

            # ---- 2026-10-13 参数审查：成本对比 + 显著性 + 非重叠子样本 + 分子期稳定性 ----
            pairs = [(d[f'ret_{days}d'], d.get(f'ret_{days}d_gross')) for d in fd
                     if d.get(f'ret_{days}d') is not None]
            dates = [str(d.get('signal_date', ''))[:10] for d in fd
                     if d.get(f'ret_{days}d') is not None]
            st = fwd_stats(returns, dates=dates, horizon=days, trading_days=trading_days)
            gross_vals = [g for _, g in pairs if g is not None]
            gross_txt = f"毛均值={np.mean(gross_vals):.2%} → " if gross_vals else ""
            lines.append(f"  成本: {COST_NOTE}（{gross_txt}净均值={st['mean']:.2%}）")
            lines.append(f"  显著性: {format_sig(st)}, p={st['p']:.4f}, "
                         f"95%CI=[{st['ci95_low']:.2%}, {st['ci95_high']:.2%}], n_eff={st['n_eff']}")
            no = st.get('nonoverlap', {})
            lines.append(f"  非重叠子样本: n={no.get('count', 0)}, 胜率={no.get('win_rate', 0):.1%}, "
                         f"均值={no.get('mean', 0):.2%}, t={no.get('t', 0):.2f}, p={no.get('p', 1):.4f}")
            # 分子期（按信号年份）稳定性：分年度 count/win_rate/mean
            by_year: Dict[str, List[float]] = {}
            for d0, dd in zip(dates, [p[0] for p in pairs]):
                yr = d0[:4]
                if yr.isdigit():
                    by_year.setdefault(yr, []).append(float(dd))
            if by_year:
                seg = "; ".join(
                    f"{y}: n={len(v)}, 胜率={float(np.mean(np.array(v) > 0)):.1%}, 均值={float(np.mean(v)):.2%}"
                    for y, v in sorted(by_year.items())
                )
                lines.append(f"  分子期稳定性: {seg}")
            lines.append("")

        # Top 10 统计
        if all_signals:
            sorted_signals = sorted(
                all_signals,
                key=lambda x: x.get('composite_score', 0),
                reverse=True,
            )
            top10 = sorted_signals[:min(10, len(sorted_signals))]
            lines.append("--- Top 10 综合分信号 ---")
            for s in top10:
                rets_str = " | ".join(
                    f"{d}d={s.get(f'ret_{d}d', 0):.1%}" if s.get(f'ret_{d}d') is not None else f"{d}d=N/A"
                    for d in self.cfg.forward_return_days
                )
                lines.append(f"  {s['ts_code']} (score={s['composite_score']:.1f}) -> {rets_str}")

        # 趋势跟踪退出统计
        exit_rets = [s['exit_ret'] for s in all_signals if s.get('exit_ret') is not None]
        exit_types = [s.get('exit_type', 'unknown') for s in all_signals if s.get('exit_ret') is not None]
        max_rets = [s['max_ret'] for s in all_signals if s.get('max_ret') is not None]
        exit_days = [s['exit_day'] for s in all_signals if s.get('exit_day') is not None]

        if exit_rets:
            er = np.array(exit_rets)
            mr = np.array(max_rets)
            lines.append("")
            lines.append("--- 趋势跟踪退出统计 ---")
            lines.append(f"  样本数: {len(er)}")
            lines.append(f"  退出收益: 均值={er.mean():.2%}, 中位={np.median(er):.2%}, 胜率={np.mean(er>0):.1%}")
            lines.append(f"  最大浮盈: 均值={mr.mean():.2%}, 中位={np.median(mr):.2%}")
            lines.append(f"  盈利回吐: 均值={(mr-er).mean():.2%}")
            lines.append(f"  平均持有: {np.mean(exit_days):.1f}天")
            from collections import Counter
            type_counts = Counter(exit_types)
            for t, c in type_counts.most_common():
                sub_er = [exit_rets[i] for i in range(len(exit_types)) if exit_types[i] == t]
                if sub_er:
                    lines.append(f"  {t}: {c}次({c/len(exit_types):.0%}), 均值收益={np.mean(sub_er):.2%}")

        # 因子IC分析
        if all_signals:
            lines.append("")
            lines.append("--- 因子IC分析（因子与20日收益的相关性）---")
            score_cols = ['composite_score', 'b_score', 'c_score', 'e_score']
            for days in [10, 20]:
                ret_col = f'ret_{days}d'
                valid_sigs = [s for s in all_signals if s.get(ret_col) is not None]
                if len(valid_sigs) < 20:
                    continue
                rets = np.array([s[ret_col] for s in valid_sigs])
                lines.append(f"  [{days}日收益]")
                for col in score_cols:
                    vals = [s.get(col, 0) for s in valid_sigs]
                    if np.std(vals) < 1e-8:
                        continue
                    ic = np.corrcoef(vals, rets)[0, 1]
                    # 审查 16：RankIC 用标准 Spearman（pandas corr(method=
                    # 'spearman')），旧的 argsort 双重排序在并列值处理上
                    # 与标准定义有偏差
                    rank_ic = pd.Series(vals).corr(pd.Series(rets), method='spearman')
                    lines.append(f"    {col}: IC={ic:.4f}, RankIC={rank_ic:.4f}")

            # 按综合分分组收益
            lines.append("")
            lines.append("--- 综合分分组收益（20日）---")
            valid_20 = [s for s in all_signals if s.get('ret_20d') is not None]
            if len(valid_20) >= 30:
                scores_20 = np.array([s.get('composite_score', 0) for s in valid_20])
                rets_20 = np.array([s['ret_20d'] for s in valid_20])
                q33 = np.percentile(scores_20, 33)
                q66 = np.percentile(scores_20, 66)
                low = rets_20[scores_20 <= q33]
                mid = rets_20[(scores_20 > q33) & (scores_20 <= q66)]
                high = rets_20[scores_20 > q66]
                lines.append(f"    低分组(≤{q33:.1f}): 胜率={np.mean(low>0):.1%}, 均值={np.mean(low):.2%}, n={len(low)}")
                lines.append(f"    中分组({q33:.1f}-{q66:.1f}): 胜率={np.mean(mid>0):.1%}, 均值={np.mean(mid):.2%}, n={len(mid)}")
                lines.append(f"    高分组(>{q66:.1f}): 胜率={np.mean(high>0):.1%}, 均值={np.mean(high):.2%}, n={len(high)}")

            # 按信号类型分组
            lines.append("")
            lines.append("--- 信号类型分组收益（20日）---")
            from collections import defaultdict
            type_rets = defaultdict(list)
            for s in valid_20:
                st = s.get('signal_type', 'unknown')
                type_rets[st].append(s['ret_20d'])
            for st, rets_list in sorted(type_rets.items()):
                arr = np.array(rets_list)
                lines.append(f"    {st}: 胜率={np.mean(arr>0):.1%}, 均值={np.mean(arr):.2%}, n={len(arr)}")

        lines.append("=" * 60)
        return "\n".join(lines)

    # ================================================================
    # 单票深度分析
    # ================================================================
    def analyze_single_stock(self, ts_code: str,
                              start_date: str = "2025-02-01",
                              end_date: str = "2026-05-15") -> Dict:
        """
        对单只标的做深度回测分析，看策略能否在启动后 5 日内选出
        """
        logger.info(f"单票深度分析: {ts_code}")

        # 审查 7：同样按交易日历取 ≥260 交易日回看（与 run() 一致）
        preload_start = trading_days_before(start_date, 260)
        self.loader.preload_for_backtest(preload_start, end_date)
        self.engine.layer_c.skip_1min = True
        self.engine.layer_d.preload_for_backtest(start_date, end_date)

        trading_days = self.loader.get_trading_days(start_date, end_date)
        if not trading_days:
            return {}

        detection_history = []
        for trade_date in _safe_tqdm(trading_days, desc=f"分析 {ts_code}"):
            try:
                candidates = self.engine.evaluate_single_day(
                    trade_date, pool_a={ts_code}
                )
                for c in candidates:
                    if c['ts_code'] == ts_code:
                        entry_price = self._get_close_price(ts_code, trade_date)
                        if entry_price:
                            c['entry_price'] = entry_price
                        detection_history.append(c)
            except Exception as e:
                logger.debug(f"{trade_date} 评估 {ts_code} 失败: {e}")

        self.loader.clear_cache()

        if not detection_history:
            logger.info(f"{ts_code} 在整个区间没有被策略选中")
            return {'ts_code': ts_code, 'detections': [], 'summary': '未被选中'}

        # 按时间排序
        detection_history.sort(key=lambda x: x['eval_date'])

        # 构建交易日索引
        trading_day_idx = {d: i for i, d in enumerate(trading_days)}

        # 第一次检测
        first_detect = detection_history[0]
        first_date = first_detect['eval_date']
        first_price = first_detect.get('entry_price', 0)

        summary_parts = []
        summary_parts.append(f"首次检测日期: {first_date}")
        summary_parts.append(f"首次检测价格: {first_price}")
        summary_parts.append(f"总检测次数: {len(detection_history)}")

        for days in self.cfg.forward_return_days:
            first_idx = trading_day_idx.get(first_date, -1)
            if first_idx >= 0:
                target_idx = first_idx + days
                if target_idx < len(trading_days):
                    exit_date = trading_days[target_idx]
                    exit_price = self._get_close_price(ts_code, exit_date)
                    if exit_price and first_price:
                        fwd_ret = (exit_price - first_price) / first_price
                        summary_parts.append(f"首次检测后{days}日收益: {fwd_ret:.2%}")

        summary_parts.append("")
        summary_parts.append("全部检测记录:")
        for d in detection_history[:10]:
            summary_parts.append(
                f"  {d['eval_date']} score={d['composite_score']:.1f} "
                f"(B={d['b_score']:.1f}, C={d['c_score']:.1f})"
            )

        return {
            'ts_code': ts_code,
            'detections': detection_history,
            'first_detection_date': first_date,
            'first_detection_price': first_price,
            'total_detections': len(detection_history),
            'summary': '\n'.join(summary_parts),
        }