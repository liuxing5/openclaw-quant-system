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
from datetime import datetime, timedelta
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
from .data_loader import DataLoader
from .engine import MainUptrendEngine

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
            'summary': str,
          }
        """
        logger.info(f"=" * 60)
        logger.info(f"主升浪回测 {start_date} ~ {end_date}")
        logger.info(f"=" * 60)

        # ---- 预加载全量数据（核心优化） ----
        # 扩展预加载范围：B层需要60日均线，至少往前推4个月
        preload_start = (
            datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=150)
        ).strftime("%Y-%m-%d")
        self.loader.preload_for_backtest(preload_start, end_date)

        # 通知引擎各层使用预加载模式
        self.engine.layer_c.skip_1min = True  # 回测模式跳过1分钟线

        # D层：一次性预加载全量风险数据
        self.engine.layer_d.preload_for_backtest(start_date, end_date)

        trading_days = self.loader.get_trading_days(start_date, end_date)
        if not trading_days:
            logger.error("无交易日数据")
            return {}

        logger.info(f"交易日: {len(trading_days)} 天")

        # 构建交易日索引（避免list.index()的O(n)查找）
        trading_day_idx = {d: i for i, d in enumerate(trading_days)}

        # 预加载 A 层池（全市场时做一次周频更新）
        pool_a_cache: Dict[str, Set[str]] = {}

        all_signals = []
        forward_data: Dict[int, List[Dict]] = {d: [] for d in self.cfg.forward_return_days}
        day_count = len(trading_days)
        t_day_start = 0
        t_total_start = time.time()

        for i, trade_date in enumerate(_safe_tqdm(trading_days, desc="回测进度")):
            try:
                t1 = time.time()
                pool_a = self._get_pool_a(pool_a_cache, trade_date, target_stocks)

                candidates = self.engine.evaluate_single_day(trade_date, pool_a)
                t2 = time.time()

                if candidates:
                    signals = self._calc_forward_returns(
                        candidates, trade_date, trading_days, i,
                        trading_day_idx,
                    )
                    all_signals.extend(signals)

                    for s in signals:
                        for d in self.cfg.forward_return_days:
                            if s.get(f'ret_{d}d') is not None:
                                forward_data[d].append({
                                    'ts_code': s['ts_code'],
                                    'signal_date': s['eval_date'],
                                    'composite_score': s['composite_score'],
                                    f'ret_{d}d': s[f'ret_{d}d'],
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

        self.loader.clear_cache()

        # 汇总统计
        summary = self._build_summary(all_signals, forward_data)

        return {
            'signals': all_signals,
            'forward_returns': {d: pd.DataFrame(forward_data[d]) for d in self.cfg.forward_return_days},
            'summary': summary,
        }

    # ================================================================
    # A 层池管理（周频缓存）
    # ================================================================
    def _get_pool_a(self, cache: Dict[str, Set[str]], trade_date: str,
                    target_stocks: Optional[List[str]] = None) -> Set[str]:
        if target_stocks:
            return set(target_stocks)

        week_key = trade_date[:7] + "_w" + str(
            (datetime.strptime(trade_date, "%Y-%m-%d").isocalendar()[1] // 2)
        )
        if week_key in cache:
            return cache[week_key]

        if self.cfg.a_enabled:
            pool = self.engine.layer_a.prescreen(trade_date)
        else:
            snapshot = self.loader.get_market_snapshot(trade_date)
            pool = set(snapshot['ts_code'].tolist()) if not snapshot.empty else set()

        cache[week_key] = pool
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

                ret = (exit_price - entry_price) / entry_price
                signal[f'ret_{days}d'] = ret
                signal[f'hit_{days}d'] = ret > 0.05

            results.append(signal)
        return results

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
                       forward_data: Dict[int, List[Dict]]) -> str:
        lines = []
        lines.append("=" * 60)
        lines.append("主升浪检测策略 — 回测汇总")
        lines.append("=" * 60)
        lines.append(f"回测区间: {self.cfg.backtest_start} ~ {self.cfg.backtest_end}")
        lines.append(f"总信号数: {len(all_signals)}")
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