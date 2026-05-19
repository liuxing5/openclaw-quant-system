"""
主升浪检测引擎 — 四层漏斗编排器
====================================
串联四层漏斗：
  Layer A → Layer B → Layer C → Layer D → 输出

运行模式：
  - daily: 日频运行（A 层周频更新，B/C/D 日频）
  - backtest: 回测模式（全部历史数据 PIT 串行）
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Set

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db_fresh
from psycopg2.extras import RealDictCursor
from zoneinfo import ZoneInfo

from .config import MainUptrendConfig, DEFAULT_CONFIG
from .data_loader import DataLoader
from .layer_a_prescreen import LayerAPrescreener
from .layer_b_launch import LayerBLaunchDetector, LaunchSignal
from .layer_c_sustain import LayerCSustainAnalyzer, SustainSignal
from .layer_d_risk import LayerDRiskFilter, RiskVerdict

logger = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


class MainUptrendEngine:
    """四层主升浪检测引擎"""

    def __init__(self, cfg: Optional[MainUptrendConfig] = None):
        self.cfg = cfg or DEFAULT_CONFIG
        self.loader = DataLoader()

        self.layer_a = LayerAPrescreener(self.cfg, self.loader)
        self.layer_b = LayerBLaunchDetector(self.cfg, self.loader)
        self.layer_c = LayerCSustainAnalyzer(self.cfg, self.loader)
        self.layer_d = LayerDRiskFilter(self.cfg, self.loader)

    # ================================================================
    # 日频运行（实盘/模拟）
    # ================================================================
    def run_daily(self, eval_date: Optional[str] = None) -> Dict:
        """
        日频运行完整四层漏斗

        返回:
          {
            'date': str,
            'a_pool_size': int,
            'b_signals': int,
            'c_signals': int,
            'd_passed': int,
            'candidates': List[dict],
            'stats': dict,
          }
        """
        if eval_date is None:
            eval_date = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

        stats = {}
        logger.info(f"=" * 60)
        logger.info(f"主升浪检测引擎 - {eval_date}")
        logger.info(f"=" * 60)

        # ---------- Layer A: 预筛池 ----------
        if self.cfg.a_enabled:
            pool_a = self.layer_a.prescreen(eval_date)
            stats['a_pool_size'] = len(pool_a)
            logger.info(f"[Layer A] 预筛池: {len(pool_a)} 只")
        else:
            # 跳过 A 层时用全市场
            snapshot = self.loader.get_market_snapshot(eval_date)
            pool_a = set(snapshot['ts_code'].tolist()) if not snapshot.empty else set()
            stats['a_pool_size'] = len(pool_a)
            logger.info(f"[Layer A] 跳过，使用全市场: {len(pool_a)} 只")

        # ---------- Layer B: 启动信号 ----------
        b_signals: List[LaunchSignal] = []
        if self.cfg.b_enabled and pool_a:
            b_signals = self.layer_b.scan_pool(
                pool_a, eval_date, top_n=self.cfg.b_top_n_daily
            )
        stats['b_signals'] = len(b_signals)
        logger.info(f"[Layer B] 启动信号: {len(b_signals)} 只")

        if not b_signals:
            logger.info("无 B 层信号，退出")
            return {
                'date': eval_date,
                'a_pool_size': stats.get('a_pool_size', 0),
                'b_signals': 0,
                'c_signals': 0,
                'd_passed': 0,
                'candidates': [],
                'stats': stats,
            }

        # ---------- Layer C: 持续性判定 ----------
        c_signals: List[SustainSignal] = []
        if self.cfg.c_enabled:
            c_signals = self.layer_c.scan_b_signals(
                b_signals, top_n=self.cfg.c_top_n_daily
            )
        else:
            c_signals = [
                SustainSignal(ts_code=s.ts_code, eval_date=s.eval_date, passed=True, b_signal=s)
                for s in b_signals[:self.cfg.c_top_n_daily]
            ]
        stats['c_signals'] = len(c_signals)
        logger.info(f"[Layer C] 持续性判定: {len(c_signals)} 只")

        if not c_signals:
            logger.info("无 C 层信号，退出")
            return {
                'date': eval_date,
                'a_pool_size': stats.get('a_pool_size', 0),
                'b_signals': len(b_signals),
                'c_signals': 0,
                'd_passed': 0,
                'candidates': [],
                'stats': stats,
            }

        # ---------- Layer D: 风险过滤 ----------
        c_codes = [s.ts_code for s in c_signals]
        if self.cfg.d_enabled:
            d_passed = self.layer_d.filter_list(c_codes, eval_date)
        else:
            d_passed = c_codes
        stats['d_passed'] = len(d_passed)
        logger.info(f"[Layer D] 风险过滤: {len(d_passed)} 通过")

        # ---------- 组装最终候选 ----------
        d_set = set(d_passed)
        candidates = []
        for c_sig in c_signals:
            if c_sig.ts_code in d_set:
                candidates.append({
                    'ts_code': c_sig.ts_code,
                    'eval_date': eval_date,
                    'b_score': c_sig.b_signal.score if c_sig.b_signal else 0,
                    'c_score': c_sig.score,
                    'b_factors': c_sig.b_signal.factors if c_sig.b_signal else {},
                    'c_factors': c_sig.factors,
                    'b_details': c_sig.b_signal.details if c_sig.b_signal else {},
                    'c_details': c_sig.details,
                })

        candidates.sort(key=lambda x: x['c_score'] + x['b_score'], reverse=True)

        # ---------- 输出统计 ----------
        stats['total_candidates'] = len(candidates)
        return {
            'date': eval_date,
            'a_pool_size': stats.get('a_pool_size', 0),
            'b_signals': len(b_signals),
            'c_signals': len(c_signals),
            'd_passed': len(d_passed),
            'candidates': candidates,
            'stats': stats,
        }

    # ================================================================
    # 回测模式 — 单日评估（无 B5 次日确认，只做 B1-B4 + C + D）
    # ================================================================
    def evaluate_single_day(self, eval_date: str,
                            pool_a: Optional[Set[str]] = None) -> List[Dict]:
        """
        回测用单日评估，返回候选列表（不做次日确认）
        """
        if pool_a is None:
            snapshot = self.loader.get_market_snapshot(eval_date)
            pool_a = set(snapshot['ts_code'].tolist()) if not snapshot.empty else set()

        if not pool_a:
            return []

        b_signals = self.layer_b.scan_pool(pool_a, eval_date, top_n=self.cfg.b_top_n_daily)
        if not b_signals:
            return []

        c_signals = self.layer_c.scan_b_signals(b_signals, top_n=self.cfg.c_top_n_daily)
        if not c_signals:
            return []

        c_codes = [s.ts_code for s in c_signals]
        d_passed_set = set(self.layer_d.filter_list(c_codes, eval_date))

        candidates = []
        for c_sig in c_signals:
            if c_sig.ts_code in d_passed_set:
                candidates.append({
                    'ts_code': c_sig.ts_code,
                    'eval_date': eval_date,
                    'b_score': c_sig.b_signal.score if c_sig.b_signal else 0,
                    'c_score': c_sig.score,
                    'composite_score': (c_sig.b_signal.score if c_sig.b_signal else 0) + c_sig.score,
                })

        candidates.sort(key=lambda x: x['composite_score'], reverse=True)
        return candidates

    # ================================================================
    # 写入 daily_candidates（兼容现有系统）
    # ================================================================
    def write_to_db(self, candidates: List[Dict], run_mode: str = "afternoon"):
        if not candidates:
            return

        from core.db.candidates import write_candidates

        records = []
        for c in candidates:
            records.append({
                'snapshot_date': c['eval_date'],
                'ts_code': c['ts_code'],
                'stock_name': c.get('stock_name', ''),
                'mention_count': 1,
                'source_diversity': 1,
                'consensus_score': c.get('composite_score', c.get('c_score', 0) + c.get('b_score', 0)),
                'llm_score': 0,
                'quant_score': c.get('composite_score', c.get('c_score', 0) + c.get('b_score', 0)),
                'final_score': c.get('composite_score', c.get('c_score', 0) + c.get('b_score', 0)),
                'logic_tags': ['主升浪'],
                'selected': True,
                'position_pct': 5.0,
                'entry_low': None,
                'entry_high': None,
                'stop_loss': None,
                'target_1': None,
                'target_2': None,
                'sources': json.dumps({'b_factors': c.get('b_factors', {}), 'c_factors': c.get('c_factors', {})}),
                'run_mode': run_mode,
                'source': 'main_uptrend',
            })

        write_candidates(records) if records else None
        logger.info(f"写入 daily_candidates: {len(records)} 条 (source=main_uptrend)")