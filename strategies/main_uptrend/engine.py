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
from .layer_e_trend import LayerETrendDetector, TrendSignal
from .llm_refiner import LLMRefiner

logger = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


class MainUptrendEngine:
    """四层主升浪检测引擎"""

    def __init__(self, cfg: Optional[MainUptrendConfig] = None,
                 loader: Optional[DataLoader] = None):
        self.cfg = cfg or DEFAULT_CONFIG
        self.loader = loader or DataLoader()

        self.layer_a = LayerAPrescreener(self.cfg, self.loader)
        self.layer_b = LayerBLaunchDetector(self.cfg, self.loader)
        self.layer_c = LayerCSustainAnalyzer(self.cfg, self.loader)
        self.layer_d = LayerDRiskFilter(self.cfg, self.loader)
        self.layer_e = LayerETrendDetector(self.cfg, self.loader)
        self.llm_refiner = LLMRefiner(self.cfg, self.loader)

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

        # 预加载数据（加速 A/B/C/D 层查询）
        import time
        preload_start = time.time()
        lookback_days = 300  # 覆盖 120日均线 + 60日箱体 + 52周高点
        preload_start_date = (
            datetime.strptime(eval_date, "%Y-%m-%d") - timedelta(days=lookback_days)
        ).strftime("%Y-%m-%d")
        self.loader.preload_for_backtest(preload_start_date, eval_date)
        logger.info(f"数据预加载完成，耗时 {time.time()-preload_start:.1f}s")

        # ---------- Layer A: 预筛池 ----------
        if self.cfg.a_enabled:
            pool_a = self.layer_a.prescreen(eval_date)
            stats['a_pool_size'] = len(pool_a)
            logger.info(f"[Layer A] 预筛池: {len(pool_a)} 只")
            # A 层返回空时降级为全市场（避免所有filter都失败导致无候选）
            if not pool_a:
                logger.warning("[Layer A] 预筛池为空，降级使用全市场")
                snapshot = self.loader.get_market_snapshot(eval_date)
                pool_a = set(snapshot['ts_code'].tolist()) if not snapshot.empty else set()
                stats['a_pool_size'] = len(pool_a)
                stats['a_fallback'] = True
                logger.info(f"[Layer A] 全市场: {len(pool_a)} 只")
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

        # ---------- Layer E: 趋势持续型检测（与B层并行） ----------
        e_signals: List[TrendSignal] = []
        if self.cfg.e_enabled and pool_a:
            e_signals = self.layer_e.scan_pool(
                pool_a, eval_date, top_n=self.cfg.e_top_n_daily
            )
        stats['e_signals'] = len(e_signals)
        logger.info(f"[Layer E] 趋势持续: {len(e_signals)} 只")

        # 合并 B 层和 E 层的候选
        b_codes = {s.ts_code for s in b_signals}
        e_only_signals = [s for s in e_signals if s.ts_code not in b_codes]
        all_signal_codes = b_codes | {s.ts_code for s in e_signals}
        logger.info(f"[合并] B层{len(b_signals)}只 + E层独有{len(e_only_signals)}只 = {len(all_signal_codes)}只")

        if not b_signals and not e_signals:
            logger.info("无 B/E 层信号，退出")
            return {
                'date': eval_date,
                'a_pool_size': stats.get('a_pool_size', 0),
                'b_signals': 0,
                'c_signals': 0,
                'e_signals': 0,
                'd_passed': 0,
                'candidates': [],
                'stats': stats,
            }

        # ---------- Layer C: 持续性判定 ----------
        c_signals: List[SustainSignal] = []
        if self.cfg.c_enabled and b_signals:
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

        # E 层信号自带持续性判定，直接进入 D 层
        e_passed_codes = {s.ts_code for s in e_only_signals if s.passed}
        e_score_map = {s.ts_code: s.score for s in e_only_signals if s.passed}
        e_detail_map = {s.ts_code: s.details for s in e_only_signals if s.passed}
        logger.info(f"[Layer E] 通过持续性: {len(e_passed_codes)} 只")

        # 合并 C 层和 E 层通过的标的
        c_codes_set = {s.ts_code for s in c_signals}
        all_c_e_codes = list(c_codes_set | e_passed_codes)
        logger.info(f"[合并C+E] C层{len(c_codes_set)}只 + E层{len(e_passed_codes)}只 = {len(all_c_e_codes)}只")

        if not c_signals and not e_passed_codes:
            logger.info("无 C/E 层信号，退出")
            return {
                'date': eval_date,
                'a_pool_size': stats.get('a_pool_size', 0),
                'b_signals': len(b_signals),
                'c_signals': 0,
                'e_signals': len(e_signals),
                'd_passed': 0,
                'candidates': [],
                'stats': stats,
            }

        # ---------- Layer D: 风险过滤 ----------
        c_codes = [s.ts_code for s in c_signals]
        all_codes_for_d = list(set(c_codes) | e_passed_codes)
        if self.cfg.d_enabled:
            d_passed = self.layer_d.filter_list(all_codes_for_d, eval_date)
        else:
            d_passed = all_codes_for_d
        stats['d_passed'] = len(d_passed)
        logger.info(f"[Layer D] 风险过滤: {len(d_passed)} 通过")

        # ---------- 组装最终候选 ----------
        d_set = set(d_passed)
        candidates = []
        seen_codes = set()  # 防止重复添加
        
        for c_sig in c_signals:
            if c_sig.ts_code in d_set and c_sig.ts_code not in seen_codes:
                candidates.append({
                    'ts_code': c_sig.ts_code,
                    'eval_date': eval_date,
                    'b_score': c_sig.b_signal.score if c_sig.b_signal else 0,
                    'c_score': c_sig.score,
                    'e_score': 0,
                    'signal_type': 'launch',
                    'b_factors': c_sig.b_signal.factors if c_sig.b_signal else {},
                    'c_factors': c_sig.factors,
                    'e_factors': {},
                    'b_details': c_sig.b_signal.details if c_sig.b_signal else {},
                    'c_details': c_sig.details,
                    'e_details': {},
                })
                seen_codes.add(c_sig.ts_code)

        for code in e_passed_codes:
            if code in d_set and code not in c_codes_set and code not in seen_codes:
                candidates.append({
                    'ts_code': code,
                    'eval_date': eval_date,
                    'b_score': 0,
                    'c_score': 0,
                    'e_score': e_score_map.get(code, 0),
                    'signal_type': 'trend',
                    'b_factors': {},
                    'c_factors': {},
                    'e_factors': {},
                    'b_details': {},
                    'c_details': {},
                    'e_details': e_detail_map.get(code, {}),
                })
                seen_codes.add(code)

        candidates.sort(key=lambda x: x['c_score'] + x['b_score'] + x['e_score'], reverse=True)

        # ---------- LLM 优选 ----------
        if self.cfg.llm_enabled and candidates:
            candidates = self.llm_refiner.refine(candidates, eval_date)
            logger.info(f"[LLM] 优选完成: {len(candidates)} 只")

        # ---------- 输出统计 ----------
        stats['total_candidates'] = len(candidates)
        return {
            'date': eval_date,
            'a_pool_size': stats.get('a_pool_size', 0),
            'b_signals': len(b_signals),
            'c_signals': len(c_signals),
            'e_signals': len(e_signals),
            'd_passed': len(d_passed),
            'candidates': candidates,
            'stats': stats,
        }

    # ================================================================
    # 回测模式 — 单日评估（无 B5 次日确认，只做 B1-B4 + C + D）
    # ================================================================
    def evaluate_single_day(self, eval_date: str,
                            pool_a: Optional[Set[str]] = None) -> List[Dict]:
        if pool_a is None:
            snapshot = self.loader.get_market_snapshot(eval_date)
            pool_a = set(snapshot['ts_code'].tolist()) if not snapshot.empty else set()
        else:
            pool_a = {self.loader.normalize_ts_code(c) for c in pool_a}

        if not pool_a:
            return []

        b_signals = self.layer_b.scan_pool(pool_a, eval_date, top_n=self.cfg.b_top_n_daily)

        e_signals = []
        if self.cfg.e_enabled:
            e_signals = self.layer_e.scan_pool(pool_a, eval_date, top_n=self.cfg.e_top_n_daily)

        if not b_signals and not e_signals:
            logger.info(f"[Engine调试] {eval_date}: B/E层均返回0个信号")
            return []

        c_signals = []
        if b_signals:
            c_signals = self.layer_c.scan_b_signals(b_signals, top_n=self.cfg.c_top_n_daily)

        e_passed_codes = {s.ts_code for s in e_signals if s.passed}
        e_score_map = {s.ts_code: s.score for s in e_signals if s.passed}

        if not c_signals and not e_passed_codes:
            logger.info(f"[Engine调试] {eval_date}: B层{len(b_signals)}个信号，C/E层均返回0个")
            return []

        c_codes = [s.ts_code for s in c_signals]
        all_codes_for_d = list(set(c_codes) | e_passed_codes)
        d_passed_set = set(self.layer_d.filter_list(all_codes_for_d, eval_date))
        logger.info(f"[Engine调试] {eval_date}: B层{len(b_signals)}个, C层{len(c_signals)}个, E层{len(e_signals)}个, D层通过{len(d_passed_set)}个")

        candidates = []
        for c_sig in c_signals:
            if c_sig.ts_code in d_passed_set:
                b_s = c_sig.b_signal.score if c_sig.b_signal else 0
                c_s = c_sig.score
                e_s = e_score_map.get(c_sig.ts_code, 0)
                composite = e_s * 2.0 + c_s * 0.5 + b_s * 0.2
                # launch信号必须有E层蓄势确认（e_s > 0）或综合分足够高
                if e_s <= 0 and composite < 5.0:
                    continue
                candidates.append({
                    'ts_code': c_sig.ts_code,
                    'eval_date': eval_date,
                    'b_score': b_s,
                    'c_score': c_s,
                    'e_score': e_s,
                    'composite_score': composite,
                    'signal_type': 'launch',
                })

        c_codes_set = {s.ts_code for s in c_signals}
        for code in e_passed_codes:
            if code in d_passed_set and code not in c_codes_set:
                e_s = e_score_map.get(code, 0)
                composite = e_s * 2.0
                # trend信号综合分必须>4.0
                if composite < 4.0:
                    continue
                candidates.append({
                    'ts_code': code,
                    'eval_date': eval_date,
                    'b_score': 0,
                    'c_score': 0,
                    'e_score': e_s,
                    'composite_score': composite,
                    'signal_type': 'trend',
                })

        candidates.sort(key=lambda x: x['composite_score'], reverse=True)

        # 入场量能确认：过滤掉当日量比<1.2的候选（无量上涨不可靠）
        if candidates:
            ind_df = self.loader.get_indicators_snapshot(eval_date)
            if not ind_df.empty:
                cand_codes = {c['ts_code'] for c in candidates}
                sub = ind_df[ind_df['ts_code'].isin(cand_codes)]
                vol_ratio_map = {}
                amt_ratio_map = {}
                if 'volume_ratio_20' in sub.columns:
                    for _, row in sub.iterrows():
                        v = row.get('volume_ratio_20', 0)
                        vol_ratio_map[row['ts_code']] = float(v) if v is not None and str(v) != 'nan' else 0
                if 'amount_ratio_20' in sub.columns:
                    for _, row in sub.iterrows():
                        v = row.get('amount_ratio_20', 0)
                        amt_ratio_map[row['ts_code']] = float(v) if v is not None and str(v) != 'nan' else 0

                filtered = []
                for c in candidates:
                    vr = vol_ratio_map.get(c['ts_code'], 0)
                    ar = amt_ratio_map.get(c['ts_code'], 0)
                    # 至少量比>1.2 或 成交额比>1.5
                    if vr > 1.2 or ar > 1.5:
                        c['vol_ratio'] = vr
                        c['amt_ratio'] = ar
                        filtered.append(c)
                candidates = filtered

        # 行业集中度限制：同行业最多保留2只（按综合分排序）
        if candidates:
            ind_df = self.loader.get_indicators_snapshot(eval_date)
            if not ind_df.empty and 'industry' in ind_df.columns:
                cand_codes = {c['ts_code'] for c in candidates}
                sub = ind_df[ind_df['ts_code'].isin(cand_codes)][['ts_code', 'industry']]
                industry_map = dict(zip(sub['ts_code'], sub['industry']))
                industry_count = {}
                filtered = []
                for c in candidates:
                    ind = industry_map.get(c['ts_code'], 'unknown')
                    if industry_count.get(ind, 0) < 2:
                        filtered.append(c)
                        industry_count[ind] = industry_count.get(ind, 0) + 1
                candidates = filtered

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

        write_candidates(records, snapshot_date=records[0]['snapshot_date'], source='main_uptrend', run_mode=run_mode) if records else None
        logger.info(f"写入 daily_candidates: {len(records)} 条 (source=main_uptrend)")

    # ================================================================
    # 写入运行统计（用于 HTML 报告展示）
    # ================================================================
    def write_run_stats(self, result: Dict):
        """保存每日运行统计到 main_uptrend_runs 表"""
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO main_uptrend_runs (run_date, a_pool_size, b_signals, c_signals, d_passed, candidates, details)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_date) DO UPDATE SET
                    a_pool_size = EXCLUDED.a_pool_size,
                    b_signals = EXCLUDED.b_signals,
                    c_signals = EXCLUDED.c_signals,
                    d_passed = EXCLUDED.d_passed,
                    candidates = EXCLUDED.candidates,
                    details = EXCLUDED.details,
                    created_at = NOW()
            """, (
                result['date'],
                result['a_pool_size'],
                result['b_signals'],
                result['c_signals'],
                result['d_passed'],
                len(result['candidates']),
                json.dumps({
                    'e_signals': result.get('e_signals', 0),
                    'stats': result.get('stats', {}),
                }),
            ))
            conn.commit()
            cur.close()
            logger.info(f"已写入 main_uptrend_runs: {result['date']}")
        except Exception as e:
            logger.warning(f"写入 main_uptrend_runs 失败: {e}")
            if conn:
                try:
                    conn.rollback()
                except:
                    pass
        finally:
            if conn and not conn.closed:
                conn.close()