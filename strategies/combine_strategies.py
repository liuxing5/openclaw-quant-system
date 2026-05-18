"""
策略整合器 — 八步法核心 + LLM/漏斗辅助
================================================
信号链路（严格遵循）：

  Day T 收盘后(15:10):
    LLM策略 + 漏斗策略 → 各自写入 daily_candidates (辅助数据)

  Day T+1 14:30:
    八步法实时获取市场真实数据，辅助读取昨日LLM+漏斗结果 → 生成买入信号

  Day T+1 14:50:
    执行买入（只给出推荐，手动买）

  Day T+1 收盘后(15:10):
    LLM策略 + 漏斗策略 → 写入今日辅助数据

  Day T+2 09:30:
    八步法卖出系统读取昨日LLM+漏斗结果 → 辅助卖出决策（给出推荐，手动卖）

核心原则：
  - 八步法是唯一的核心决策策略
  - LLM和漏斗是辅助输入，提供加成/预过滤放宽，不做独立筛选
  - 共振过滤是八步法内部的技术指标确认，不是独立策略层
"""
from __future__ import annotations

import argparse
import copy
import csv
import sys
import os
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from typing import List, Tuple, Dict, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from overnight_8step.zuiyou1 import (
    scan_pool, fetch_market_sentiment, CONFIG_STABLE, CONFIG_UPPER,
    get_llm_candidates_from_supabase, get_funnel_candidates_from_db,
    is_llm_candidate, is_funnel_candidate,
    get_llm_boost_score, get_funnel_boost_score,
)
from resonance_filters.technical_filters import ResonanceFilters, run_resonance_filter

BEIJING_TZ = timezone(timedelta(hours=8))


def normalize_code(code: str) -> str:
    code = code.strip()
    for prefix in ('sh.', 'sz.', 'bj.', 'SH.', 'SZ.', 'BJ.'):
        if code.startswith(prefix):
            code = code[len(prefix):]
            break
    code = code.replace('.SH', '').replace('.SZ', '').replace('.BJ', '').replace('.', '')
    if code.startswith(('6', '9')):
        return f"{code}.SH"
    elif code.startswith(('0', '2', '3')):
        return f"{code}.SZ"
    elif code.startswith('92') or code.startswith(('4', '8')):
        return f"{code}.BJ"
    return f"{code}.SH"


def load_auxiliary_data(trade_date: date = None) -> Dict:
    """
    从数据库加载LLM和漏斗策略的辅助数据（前一交易日收盘后产出）

    返回:
      {
        'llm': {ts_code: {final_score, llm_score, ...}},
        'funnel': {ts_code: {final_score, ...}},
        'llm_count': int,
        'funnel_count': int,
      }
    """
    from core.db.connection import get_db_fresh
    from psycopg2.extras import RealDictCursor

    result = {'llm': {}, 'funnel': {}, 'llm_count': 0, 'funnel_count': 0}

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        if trade_date is None:
            cur.execute("SELECT MAX(snapshot_date) as max_date FROM daily_candidates;")
            row = cur.fetchone()
            trade_date = row['max_date'] if row else datetime.now(BEIJING_TZ).date()

        cur.execute("""
            SELECT ts_code, stock_name, final_score, llm_score, quant_score,
                   source_diversity, logic_tags
            FROM daily_candidates
            WHERE snapshot_date = %s
              AND source = 'llm_multisource'
              AND selected = TRUE
            ORDER BY final_score DESC;
        """, (trade_date,))
        for row in cur.fetchall():
            result['llm'][row['ts_code']] = dict(row)

        cur.execute("""
            SELECT ts_code, stock_name, final_score
            FROM daily_candidates
            WHERE snapshot_date = %s
              AND source = 'funnel_strategy'
              AND selected = TRUE
            ORDER BY final_score DESC;
        """, (trade_date,))
        for row in cur.fetchall():
            result['funnel'][row['ts_code']] = dict(row)

        cur.close()
        result['llm_count'] = len(result['llm'])
        result['funnel_count'] = len(result['funnel'])
        return result
    except Exception as e:
        print(f"⚠️ 加载辅助数据失败: {e}")
        return result
    finally:
        if conn and not conn.closed:
            conn.close()


def run_combined_strategy(
    output_dir: str = "./results",
    trade_date: date = None,
    min_pass_count: int = 3,
    require_core: bool = True,
    enable_annual_line: bool = True,
    enable_bollinger: bool = True,
    enable_resonance: bool = True,
) -> List[Dict]:
    """
    运行八步法核心策略 + LLM/漏斗辅助

    信号链路：
      1. 加载前一交易日LLM+漏斗辅助数据（收盘后产出）
      2. 运行八步法核心扫描（14:30实时数据），LLM/漏斗作为加成/预过滤放宽
      3. 可选：共振过滤作为技术指标确认
      4. 输出买入推荐

    返回：
      最终入选标的列表
    """
    try:
        from overnight_8step.zuiyou1 import get_stock_industry
    except ImportError:
        get_stock_industry = None

    print("=" * 70)
    print("  策略整合器 — 八步法核心 + LLM/漏斗辅助")
    print("=" * 70)
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  核心策略: 隔夜八步法（量价精选）")
    print(f"  辅助数据: LLM多源 + 漏斗七层（前一交易日收盘后产出）")
    if enable_resonance:
        print(f"  技术确认: 5策略共振过滤")
    print("=" * 70)

    if trade_date is None:
        from core.db.connection import get_db_fresh
        from psycopg2.extras import RealDictCursor
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
        print(f"\n  最新交易日: {trade_date}")

    # ========== Step 1: 加载辅助数据 ==========
    print(f"\n{'='*70}")
    print(f"  [Step 1/3] 加载LLM+漏斗辅助数据（前一交易日收盘后产出）")
    print(f"{'='*70}")

    aux_data = load_auxiliary_data(trade_date)
    print(f"  🤖 LLM辅助: {aux_data['llm_count']} 只")
    print(f"  🔽 漏斗辅助: {aux_data['funnel_count']} 只")

    llm_codes = set(normalize_code(c) for c in aux_data['llm'].keys())
    funnel_codes = set(normalize_code(c) for c in aux_data['funnel'].keys())
    aux_overlap = llm_codes & funnel_codes
    if aux_overlap:
        print(f"  🔗 LLM∩漏斗: {len(aux_overlap)} 只（双重辅助，信号更强）")

    # ========== Step 2: 八步法核心扫描 ==========
    print(f"\n{'='*70}")
    print(f"  [Step 2/3] 八步法核心扫描（LLM/漏斗作为辅助加成）")
    print(f"{'='*70}")

    sentiment_score, mood = fetch_market_sentiment()
    print(f"  市场情绪评分: {sentiment_score}  |  情绪: {mood}")

    stable_cfg = copy.deepcopy(CONFIG_STABLE)
    upper_cfg = copy.deepcopy(CONFIG_UPPER)
    stable_cfg["MODE"] = "post"
    upper_cfg["MODE"] = "post"

    stable_results, _, stable_name_map, _, _, _ = scan_pool(stable_cfg, sentiment_score, mood, preloaded=False)
    upper_results, _, upper_name_map, _, _, _ = scan_pool(upper_cfg, sentiment_score, mood, preloaded=True)

    all_name_map = {**stable_name_map, **upper_name_map}

    all_results = {}
    for r in stable_results + upper_results:
        raw_code = r.get('code', '')
        code = normalize_code(raw_code)
        r['code'] = code
        if code not in all_results or r.get('score', 0) > all_results[code].get('score', 0):
            if 'name' not in r or not r.get('name'):
                lookup_key = raw_code.replace('.', '')
                r['name'] = all_name_map.get(lookup_key, all_name_map.get(code, ''))
            if 'industry' not in r and get_stock_industry is not None:
                r['industry'] = get_stock_industry(raw_code)
            all_results[code] = r

    eight_step_codes = set(all_results.keys())
    print(f"  八步法入选: {len(eight_step_codes)} 只")

    # 标注辅助来源
    for code, item in all_results.items():
        aux_sources = []
        if code in llm_codes:
            aux_sources.append("🤖LLM")
        if code in funnel_codes:
            aux_sources.append("🔽漏斗")
        if code in aux_overlap:
            aux_sources.append("🔗双重辅助")
        if aux_sources:
            item['aux_sources'] = aux_sources
        else:
            item['aux_sources'] = []

    # ========== Step 3: 共振过滤（可选技术确认） ==========
    if enable_resonance:
        print(f"\n{'='*70}")
        print(f"  [Step 3/3] 共振过滤（技术指标确认）")
        print(f"{'='*70}")

        resonance_results = run_resonance_filter(
            trade_date=trade_date,
            min_pass_count=min_pass_count,
            require_core=require_core,
            enable_annual_line=enable_annual_line,
            enable_bollinger=enable_bollinger,
            verbose=True
        )

        if resonance_results:
            resonance_codes = set(normalize_code(r['ts_code']) for r in resonance_results)
            resonance_map = {normalize_code(r['ts_code']): r for r in resonance_results}
            print(f"  共振过滤通过: {len(resonance_codes)} 只")

            both = eight_step_codes & resonance_codes
            only_8step = eight_step_codes - resonance_codes
            only_resonance = resonance_codes - eight_step_codes

            print(f"  八步法∩共振: {len(both)} 只")
            print(f"  仅八步法: {len(only_8step)} 只")
            print(f"  仅共振: {len(only_resonance)} 只")

            # 八步法是核心，共振只是辅助确认
            # 八步法入选但共振未通过的，仍然保留但降低优先级
            for code in both:
                all_results[code]['resonance_confirmed'] = True
                all_results[code]['resonance_info'] = resonance_map.get(code)
            for code in only_8step:
                all_results[code]['resonance_confirmed'] = False
                all_results[code]['resonance_info'] = None
        else:
            print("  ⚠️ 没有股票通过共振过滤，八步法结果不受影响")
            for code in eight_step_codes:
                all_results[code]['resonance_confirmed'] = False
                all_results[code]['resonance_info'] = None
    else:
        for code in eight_step_codes:
            all_results[code]['resonance_confirmed'] = None
            all_results[code]['resonance_info'] = None

    # ========== 排序：共振确认 > 双重辅助 > 单辅助 > 纯八步法 ==========
    def sort_key(item):
        resonance_bonus = 1000 if item.get('resonance_confirmed') else 0
        aux_bonus = len(item.get('aux_sources', [])) * 100
        return resonance_bonus + aux_bonus + item.get('score', 0)

    result = sorted(all_results.values(), key=sort_key, reverse=True)

    # ========== 输出结果 ==========
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
    out_path = out_dir / f"combined_{today}.csv"

    if result:
        with open(out_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow([
                'code', 'name', 'pct', 'vol_ratio', 'turn', 'score', 'tags',
                'industry', 'aux_sources', 'resonance_confirmed',
            ])
            for item in result:
                writer.writerow([
                    item.get('code', ''),
                    item.get('name', ''),
                    item.get('pct', 0),
                    item.get('vol_ratio', 0),
                    item.get('turn', 0),
                    item.get('score', 0),
                    ' | '.join(item.get('tags', [])),
                    item.get('industry', ''),
                    ' | '.join(item.get('aux_sources', [])),
                    item.get('resonance_confirmed', ''),
                ])

        print(f"\n{'='*70}")
        print(f"  ✅ 策略整合完成!")
        print(f"  输出文件: {out_path}")
        print(f"  入选标的: {len(result)} 只")
        print(f"{'='*70}")

        print("\n--- 入选标的详情 ---")
        for item in result:
            aux_str = ' '.join(item.get('aux_sources', []))
            resonance_str = "✅共振确认" if item.get('resonance_confirmed') else ""
            print(f"\n  {item['code']} {item['name']}  {aux_str} {resonance_str}")
            print(f"    涨幅: {item['pct']:.2f}%  量比: {item['vol_ratio']:.2f}  换手: {item['turn']:.2f}%")
            print(f"    评分: {item['score']}  标签: {' | '.join(item.get('tags', []))}")

        print(f"\n{'='*70}")
        print(f"  💡 操作指引")
        print(f"  买入时间: 14:50（14:30信号确认后10分钟）")
        print(f"  稳健路径：次日09:35未维持昨收+1%，直接出局")
        print(f"  高位路径：次日竞价弱于昨收，集合竞价结束即清仓")
        print(f"  全局止损：亏损超2.5%无条件止损")
        print(f"{'='*70}\n")

        return result
    else:
        print("\n✗ 没有通过八步法筛选的标的")
        return []


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="八步法核心 + LLM/漏斗辅助 策略整合器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
信号链路：
  Day T 15:10:  LLM+漏斗产出辅助数据
  Day T+1 14:30: 八步法读取辅助数据+实时行情 → 买入信号
  Day T+1 14:50: 手动买入
  Day T+1 15:10: LLM+漏斗产出新辅助数据
  Day T+2 09:30: 八步法卖出系统读取辅助数据 → 卖出信号
        """
    )
    parser.add_argument("--output", "-o", type=str, default="./results",
                        help="输出目录")
    parser.add_argument("--date", "-d", type=str, default=None,
                        help="交易日期（YYYY-MM-DD），默认最新交易日")
    parser.add_argument("--min-pass", "-m", type=int, default=3,
                        help="最少通过的共振策略数量（默认3）")
    parser.add_argument("--no-core", action="store_true",
                        help="不要求核心3策略必须全部通过")
    parser.add_argument("--no-annual", action="store_true",
                        help="禁用年线过滤")
    parser.add_argument("--no-bollinger", action="store_true",
                        help="禁用布林带过滤")
    parser.add_argument("--no-resonance", action="store_true",
                        help="禁用共振过滤（仅八步法+辅助）")
    args = parser.parse_args()

    trade_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None

    results = run_combined_strategy(
        output_dir=args.output,
        trade_date=trade_date,
        min_pass_count=args.min_pass,
        require_core=not args.no_core,
        enable_annual_line=not args.no_annual,
        enable_bollinger=not args.no_bollinger,
        enable_resonance=not args.no_resonance,
    )

    if not results:
        sys.exit(1)
