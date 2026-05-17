"""
Layer 5: 人气精选
==================
决策逻辑：
  近5日综合评分（含涨幅3~5%，贴线，分时平稳）≥80；
  人气榜排名≤100可加分。

吸收策略：③隔夜八步法  ⑥人气榜前30
"""
from __future__ import annotations

import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone, timedelta
from typing import List, Dict

import pandas as pd
import numpy as np
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from core.db.connection import get_db_fresh

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    tqdm = None

BEIJING_TZ = timezone(timedelta(hours=8))
LAYER5_WORKERS = min(8, (os.cpu_count() or 4))


def _batch_load_ohlcv(
    stock_list: List[str], trade_date: date, db_conn, days: int = 30
) -> Dict[str, pd.DataFrame]:
    """批量加载OHLCV数据（1次SQL替代N次单股查询）"""
    if not stock_list:
        return {}

    start_date = trade_date - timedelta(days=days)
    result = {}

    cur = db_conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT ts_code, trade_date, open, high, low, close, volume, amount,
               pct_chg, turnover_rate, volume_ratio, amplitude
        FROM daily_quotes
        WHERE ts_code = ANY(%s) AND trade_date >= %s AND trade_date <= %s
        ORDER BY ts_code, trade_date ASC;
    """, (stock_list, start_date, trade_date))
    rows = cur.fetchall()
    cur.close()

    if not rows:
        return result

    df_all = pd.DataFrame(rows)
    df_all['trade_date'] = pd.to_datetime(df_all['trade_date']).dt.date

    for ts_code, group in df_all.groupby('ts_code'):
        group = group.set_index('trade_date').sort_index()
        for col in ['open', 'high', 'low', 'close', 'volume', 'amount',
                    'pct_chg', 'turnover_rate', 'volume_ratio', 'amplitude']:
            group[col] = pd.to_numeric(group[col], errors='coerce')
        result[ts_code] = group

    return result


def _load_popularity_ranks(trade_date: date, db_conn) -> Dict[str, int]:
    """从 strong_stock_rank 加载人气排名"""
    rank_map = {}
    try:
        cur = db_conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT ts_code, MIN(rank_position) as best_rank
            FROM strong_stock_rank
            WHERE trade_date = %s AND rank_position IS NOT NULL
            GROUP BY ts_code;
        """, (trade_date,))
        for r in cur.fetchall():
            rank_map[r['ts_code']] = int(r['best_rank'])
        cur.close()
    except Exception:
        pass
    return rank_map


def _load_valuation_data(stock_list: List[str], trade_date: date, db_conn) -> Dict[str, dict]:
    """从 daily_quotes 加载 PE/PB 估值数据"""
    val_map = {}
    if not stock_list:
        return val_map
    try:
        cur = db_conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT ts_code, pe_ratio, pb_ratio
            FROM daily_quotes
            WHERE trade_date = %s AND ts_code = ANY(%s);
        """, (trade_date, stock_list))
        for r in cur.fetchall():
            val_map[r['ts_code']] = {
                'pe_ratio': float(r['pe_ratio']) if r['pe_ratio'] else None,
                'pb_ratio': float(r['pb_ratio']) if r['pb_ratio'] else None,
            }
        cur.close()
    except Exception:
        pass
    return val_map


def _load_llm_candidates(trade_date: date, db_conn) -> Dict[str, dict]:
    """从 daily_candidates 加载 LLM 多源候选（source='llm_multisource'）"""
    llm_map = {}
    try:
        cur = db_conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT ts_code, stock_name, mention_count, source_diversity,
                   consensus_score, llm_score, quant_score, final_score,
                   logic_tags, selected, sources
            FROM daily_candidates
            WHERE snapshot_date = %s AND source = 'llm_multisource'
              AND run_mode = 'afternoon'
            ORDER BY final_score ASC;
        """, (trade_date,))
        for r in cur.fetchall():
            llm_map[r['ts_code']] = {
                'stock_name': r['stock_name'],
                'mention_count': r['mention_count'] or 0,
                'source_diversity': r['source_diversity'] or 0,
                'consensus_score': float(r['consensus_score']) if r['consensus_score'] else 0,
                'llm_score': float(r['llm_score']) if r['llm_score'] else 0,
                'quant_score': float(r['quant_score']) if r['quant_score'] else 0,
                'final_score': float(r['final_score']) if r['final_score'] else 0,
                'logic_tags': r['logic_tags'] or [],
                'selected': r['selected'] or False,
            }
        cur.close()
    except Exception:
        pass
    return llm_map


def _load_concept_map(stock_list: List[str], db_conn) -> Dict[str, list]:
    """加载股票的概念标签"""
    concept_map = {}
    if not stock_list:
        return concept_map
    try:
        cur = db_conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT ts_code, concept_name
            FROM concept_membership
            WHERE ts_code = ANY(%s);
        """, (stock_list,))
        for r in cur.fetchall():
            code = r['ts_code']
            if code not in concept_map:
                concept_map[code] = []
            concept_map[code].append(r['concept_name'])
        cur.close()
    except Exception:
        pass
    return concept_map


def _score_single(
    ts_code: str, cfg, ohlcv_cache: Dict[str, pd.DataFrame],
    rank_map: Dict[str, int], trend_bonus: float = 0.0, momentum_bonus: float = 0.0,
    llm_map: Dict[str, dict] = None, concept_map: Dict[str, list] = None,
) -> dict:
    """单股综合评分（从内存缓存读取，无DB访问）"""
    if llm_map is None:
        llm_map = {}
    if concept_map is None:
        concept_map = {}

    result = {
        'score': 0,
        'pct': 0.0,
        'pct_score': 0,
        'bias_score': 0,
        'stability_score': 0,
        'popularity_bonus': 0,
        'llm_bonus': 0,
        'llm_details': {},
        'trend_bonus': trend_bonus,
        'momentum_bonus': momentum_bonus,
        'tags': [],
    }

    df = ohlcv_cache.get(ts_code)
    if df is None or df.empty or len(df) < 3:
        return result

    today = df.iloc[-1]
    close = today['close']

    # A. 涨幅评分（3~5%满分20，1~7范围给分）
    pct = today.get('pct_chg', 0)
    if pd.isna(pct) or pct is None:
        pct = 0.0
    result['pct'] = round(pct, 2)
    if cfg.layer5_pct_range_low <= pct <= cfg.layer5_pct_range_high:
        result['pct_score'] = 20
        result['tags'].append('黄金涨幅')
    elif 1.0 <= pct < cfg.layer5_pct_range_low:
        result['pct_score'] = int((pct - 1.0) / (cfg.layer5_pct_range_low - 1.0) * 15)
        result['tags'].append('涨幅偏低')
    elif cfg.layer5_pct_range_high < pct <= 8.0:
        result['pct_score'] = int((8.0 - pct) / (8.0 - cfg.layer5_pct_range_high) * 10)
        result['tags'].append('涨幅偏高')

    # B. 贴线评分（收盘价 vs MA5偏离度）
    close_series = df['close']
    if len(close_series) >= 5:
        ma5 = close_series.rolling(window=5, min_periods=5).mean().iloc[-1]
        bias = abs(close - ma5) / ma5 if ma5 > 0 else 1
        if bias < 0.01:
            result['bias_score'] = 15
            result['tags'].append('紧贴MA5')
        elif bias < 0.02:
            result['bias_score'] = 12
            result['tags'].append('贴MA5')
        elif bias < 0.03:
            result['bias_score'] = 8
        elif bias < 0.05:
            result['bias_score'] = 3
            result['tags'].append('乖离偏大')

    # C. 分时平稳（振幅倒数，越小越平稳）
    amplitude = today.get('amplitude', 0)
    if pd.isna(amplitude) or amplitude is None:
        amplitude = 0.0
    if amplitude < 2.0:
        result['stability_score'] = 15
        result['tags'].append('极度平稳')
    elif 2.0 <= amplitude <= 5.0:
        result['stability_score'] = 10
        result['tags'].append('分时平稳')
    elif amplitude < 8.0:
        result['stability_score'] = 5
    elif amplitude < 12.0:
        result['stability_score'] = 2

    # D. 人气榜加分
    rank = rank_map.get(ts_code, 999)
    if rank <= cfg.layer5_popularity_rank_threshold:
        result['popularity_bonus'] = cfg.layer5_bonus_popularity_rank
        result['tags'].append(f'人气#{rank}')

    # E. LLM多源联动加分
    if cfg.layer5_llm_bonus_enabled:
        llm_data = llm_map.get(ts_code)
        if llm_data:
            llm_bonus = 0.0
            llm_details = {}

            # E1. LLM共识评分（多源一致性）
            if llm_data['consensus_score'] >= cfg.layer5_llm_consensus_threshold:
                llm_bonus += cfg.layer5_llm_consensus_bonus
                llm_details['consensus'] = round(llm_data['consensus_score'], 1)
                result['tags'].append(f'LLM共识{llm_data["consensus_score"]:.0f}')

            # E2. LLM最终评分
            if llm_data['final_score'] >= cfg.layer5_llm_finalscore_threshold:
                llm_bonus += cfg.layer5_llm_finalscore_bonus
                llm_details['final_score'] = round(llm_data['final_score'], 1)
                result['tags'].append(f'LLM评{llm_data["final_score"]:.0f}')

            # E3. 多源提及次数
            if llm_data['mention_count'] >= cfg.layer5_llm_mention_threshold:
                llm_bonus += cfg.layer5_llm_mention_bonus
                llm_details['mention'] = llm_data['mention_count']
                result['tags'].append(f'多源×{llm_data["mention_count"]}')

            # E4. LLM标记为精选(selected)
            if llm_data['selected']:
                llm_bonus += cfg.layer5_llm_selected_bonus
                llm_details['selected'] = True
                result['tags'].append('LLM精选')

            result['llm_bonus'] = round(llm_bonus, 1)
            result['llm_details'] = llm_details

        # E5. 概念共振：当前股票的概念是否与LLM热门概念匹配
        stock_concepts = set(concept_map.get(ts_code, []))
        if stock_concepts:
            # 收集LLM候选的热门概念（出现次数最多的概念）
            llm_concept_counts = {}
            for code, info in llm_map.items():
                for name in concept_map.get(code, []):
                    llm_concept_counts[name] = llm_concept_counts.get(name, 0) + 1
            # 取Top 10热门概念
            hot_concepts = set(
                c for c, _ in sorted(llm_concept_counts.items(), key=lambda x: -x[1])[:10]
            )
            matched = stock_concepts & hot_concepts
            if matched:
                result['llm_bonus'] += cfg.layer5_llm_concept_bonus
                if 'llm_details' not in result:
                    result['llm_details'] = {}
                result['llm_details']['concepts'] = sorted(matched)[:5]
                result['tags'].append(f'概念共振({len(matched)})')

    # 总分
    base = 50
    result['score'] = (
        base + result['pct_score'] + result['bias_score'] + result['stability_score']
        + result['popularity_bonus'] + result['trend_bonus'] + result['momentum_bonus']
        + result['llm_bonus']
    )
    result['score'] = min(result['score'], 100)

    return result


def compute_popularity_score(
    ts_code: str,
    trade_date: date,
    rank_map: Dict[str, int],
    cfg,
    trend_bonus: float = 0.0,
    momentum_bonus: float = 0.0,
) -> dict:
    conn = None
    try:
        conn = get_db_fresh()
        cache = _batch_load_ohlcv([ts_code], trade_date, conn, days=30)
    finally:
        if conn and not conn.closed:
            conn.close()
    return _score_single(ts_code, cfg, cache, rank_map, trend_bonus, momentum_bonus)


def run_layer5_popularity_filter(
    stock_items: List[dict],
    trade_date: date = None,
    cfg=None,
    verbose: bool = True,
) -> List[dict]:
    """
    人气精选，返回通过评分阈值的股票详情。

    优化: 批量加载OHLCV(1次SQL) + ThreadPoolExecutor并行评分，
          154只从 ~131s 降至 ~2s。
    """
    if cfg is None:
        from .funnel_config import DEFAULT_FUNNEL_CONFIG
        cfg = DEFAULT_FUNNEL_CONFIG

    if not cfg.layer5_enabled:
        return stock_items

    if trade_date is None:
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT MAX(trade_date) as max_date FROM daily_quotes;")
            row = cur.fetchone()
            trade_date = row['max_date'] if row else datetime.now(BEIJING_TZ).date()
            cur.close()
        finally:
            if conn and not conn.closed:
                conn.close()

    n_total = len(stock_items)

    if verbose:
        print(f"\n{'─'*60}")
        print(f"  [Layer 5] 人气精选  — 待评分 {n_total} 只")
        print(f"{'─'*60}")
        print(f"  综合评分≥{cfg.layer5_min_composite_score}  "
              f"涨幅{cfg.layer5_pct_range_low}~{cfg.layer5_pct_range_high}%  "
              f"人气榜加分≤{cfg.layer5_popularity_rank_threshold}")
        if cfg.layer5_llm_bonus_enabled:
            print(f"  🤖 LLM联动: 共识≥{cfg.layer5_llm_consensus_threshold}加分"
                  f"  终评≥{cfg.layer5_llm_finalscore_threshold}加分"
                  f"  提及≥{cfg.layer5_llm_mention_threshold}源加分"
                  f"  精选+{cfg.layer5_llm_selected_bonus}"
                  f"  概念共振+{cfg.layer5_llm_concept_bonus}")

    stock_list_all = [item['ts_code'] for item in stock_items]
    db_conn = None
    try:
        db_conn = get_db_fresh()
        if verbose:
            llm_note = " + LLM候选 + 概念" if cfg.layer5_llm_bonus_enabled else ""
            print(f"  ⏳ 加载人气排名{llm_note} + 估值 + 批量K线 ({n_total} 只)...")
        rank_map = _load_popularity_ranks(trade_date, db_conn)
        llm_map = _load_llm_candidates(trade_date, db_conn) if cfg.layer5_llm_bonus_enabled else {}
        concept_map = _load_concept_map(stock_list_all, db_conn) if cfg.layer5_llm_bonus_enabled else {}
        val_map = _load_valuation_data(stock_list_all, trade_date, db_conn)
        ohlcv_cache = _batch_load_ohlcv(stock_list_all, trade_date, db_conn, days=10)
    finally:
        if db_conn and not db_conn.closed:
            db_conn.close()

    if verbose:
        llm_info = f", LLM候选{len(llm_map)}只, 概念{len(concept_map)}只" if cfg.layer5_llm_bonus_enabled else ""
        print(f"  ✓ 数据就绪: 人气{len(rank_map)}只, 估值{len(val_map)}只, K线{len(ohlcv_cache)}只{llm_info}")

    # ── 阶段1.5: PE/PB 估值预筛（在评分前剔除高估股票）──
    stock_items_filtered = []
    pe_reject = 0
    pb_reject = 0
    for item in stock_items:
        ts_code = item['ts_code']
        val = val_map.get(ts_code, {})
        pe = val.get('pe_ratio')
        pb = val.get('pb_ratio')
        # 拒绝PE异常：负PE（亏损）或PE过高
        if pe is not None and (pe <= 0 or pe > cfg.layer5_max_pe):
            pe_reject += 1
            continue
        # 拒绝PB异常：负PB（资不抵债）或PB过高
        if pb is not None and (pb <= 0 or pb > cfg.layer5_max_pb):
            pb_reject += 1
            continue
        stock_items_filtered.append(item)

    if verbose and (pe_reject > 0 or pb_reject > 0):
        print(f"  ⚡ 估值预筛淘汰: PE>{cfg.layer5_max_pe} ({pe_reject}只) + PB>{cfg.layer5_max_pb} ({pb_reject}只)")

    stock_list = [item['ts_code'] for item in stock_items_filtered]

    # ── 阶段2: 评分 ──
    scored = []
    n_filtered = len(stock_items_filtered)

    if n_filtered > 50:
        # 多线程并行
        with ThreadPoolExecutor(max_workers=LAYER5_WORKERS) as executor:
            futures = {}
            for item in stock_items_filtered:
                ts_code = item['ts_code']
                trend_bonus = item.get('trend_bonus', 0.0)
                momentum_bonus = item.get('momentum_bonus', 0.0)
                futures[executor.submit(
                    _score_single, ts_code, cfg, ohlcv_cache, rank_map,
                    trend_bonus, momentum_bonus, llm_map, concept_map,
                )] = item

            for future in as_completed(futures):
                item = futures[future]
                pop_result = future.result()
                if pop_result['score'] >= cfg.layer5_min_composite_score:
                    item['score'] = pop_result['score']
                    item['pct'] = pop_result['pct']
                    item['tags'] = pop_result['tags']
                    item['pct_score'] = pop_result['pct_score']
                    item['bias_score'] = pop_result['bias_score']
                    item['stability_score'] = pop_result['stability_score']
                    item['popularity_bonus'] = pop_result['popularity_bonus']
                    item['llm_bonus'] = pop_result['llm_bonus']
                    item['llm_details'] = pop_result['llm_details']
                    scored.append(item)
    else:
        for item in stock_items_filtered:
            ts_code = item['ts_code']
            trend_bonus = item.get('trend_bonus', 0.0)
            momentum_bonus = item.get('momentum_bonus', 0.0)

            pop_result = _score_single(
                ts_code, cfg, ohlcv_cache, rank_map,
                trend_bonus, momentum_bonus, llm_map, concept_map,
            )
            if pop_result['score'] >= cfg.layer5_min_composite_score:
                item['score'] = pop_result['score']
                item['pct'] = pop_result['pct']
                item['tags'] = pop_result['tags']
                item['pct_score'] = pop_result['pct_score']
                item['bias_score'] = pop_result['bias_score']
                item['stability_score'] = pop_result['stability_score']
                item['popularity_bonus'] = pop_result['popularity_bonus']
                item['llm_bonus'] = pop_result['llm_bonus']
                item['llm_details'] = pop_result['llm_details']
                scored.append(item)

    scored.sort(key=lambda x: x['score'], reverse=True)

    if verbose:
        print(f"  ✓ 通过: {len(scored)} 只")
        total_rejected = n_total - len(scored)
        print(f"  ✗ 淘汰: {total_rejected} 只 (含估值{pe_reject + pb_reject} + 评分{total_rejected - pe_reject - pb_reject})")

    return scored
