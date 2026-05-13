"""
策略整合器 — 5策略共振 + LLM多源 + 八步法
================================================
三层筛选架构：
  第1层：5策略共振过滤（20周线/均线多头/MACD/布林/年线）
  第2层：LLM多源策略（新闻资讯/研报/公告/龙虎榜）
  第3层：隔夜八步法（量价精选）

运行时间：
  盘后：15:10+  CONFIG["MODE"] = "post"

止损铁律：
  稳健路径：次日09:35未维持昨收+1%，直接出局
  高位路径：次日竞价弱于昨收，集合竞价结束即清仓
  全局止损：亏损超2.5%无条件止损
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

from overnight_8step.zuiyou1 import scan_pool, fetch_market_sentiment, CONFIG_STABLE, CONFIG_UPPER
from resonance_filters.technical_filters import ResonanceFilters, run_resonance_filter

BEIJING_TZ = timezone(timedelta(hours=8))


def load_llm_candidates_from_db(trade_date: date = None, min_score: int = 25) -> List[Tuple[str, str]]:
    """
    从数据库加载LLM多源策略候选标的
    
    参数：
      trade_date: 交易日期（默认最新）
      min_score: 最低分数阈值
    
    返回：
      [(ts_code, stock_name), ...]
    """
    from core.db.connection import get_db
    from psycopg2.extras import RealDictCursor

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if trade_date is None:
        cur.execute("SELECT MAX(snapshot_date) as max_date FROM daily_candidates WHERE source = 'llm_multisource';")
        row = cur.fetchone()
        trade_date = row['max_date'] if row else datetime.now(BEIJING_TZ).date()

    cur.execute("""
        SELECT ts_code, stock_name, final_score
        FROM daily_candidates
        WHERE snapshot_date = %s
          AND source = 'llm_multisource'
          AND selected = TRUE
          AND final_score >= %s
        ORDER BY final_score DESC;
    """, (trade_date, min_score))

    candidates = [(row['ts_code'], row['stock_name']) for row in cur.fetchall()]
    cur.close()
    conn.close()

    return candidates


def load_llm_candidates_from_csv(csv_path: str, min_score: int = 5) -> List[Tuple[str, str]]:
    """从CSV文件加载LLM候选标的"""
    candidates = []
    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                score = float(row.get('score', row.get('final_score', 0)))
                if score >= min_score:
                    code = row.get('symbol', row.get('ts_code', ''))
                    name = row.get('name', row.get('stock_name', ''))
                    if code:
                        candidates.append((code, name))
        print(f"✓ 从 {csv_path} 加载 {len(candidates)} 只LLM候选标的")
    except Exception as e:
        print(f"✗ 加载候选文件失败: {e}")
    return candidates


def normalize_code(code: str) -> str:
    """
    标准化股票代码格式，兼容多种输入
    sh.600519 / 600519.SH / 600519 → 600519.SH
    sz.000001 / 000001.SZ / 000001 → 000001.SZ
    bj.430001 / 430001.BJ / 430001 → 430001.BJ
    """
    code = code.strip()
    # 去掉 baostock 前缀 (sh./sz./bj.)
    for prefix in ('sh.', 'sz.', 'bj.', 'SH.', 'SZ.', 'BJ.'):
        if code.startswith(prefix):
            code = code[len(prefix):]
            break
    # 去掉 .SH/.SZ/.BJ 后缀
    code = code.replace('.SH', '').replace('.SZ', '').replace('.BJ', '').replace('.', '')
    if code.startswith(('6', '9')):
        return f"{code}.SH"
    elif code.startswith(('0', '2', '3')):
        return f"{code}.SZ"
    elif code.startswith(('4', '8')):
        return f"{code}.BJ"
    return f"{code}.SH"


def run_resonance_strategy(
    llm_input: str = None,
    output_dir: str = "./results",
    trade_date: date = None,
    min_pass_count: int = 3,
    require_core: bool = True,
    enable_annual_line: bool = True,
    enable_bollinger: bool = True,
    llm_min_score: int = 25,
    use_db: bool = True,
) -> List[Dict]:
    """
    运行5策略共振 + LLM多源 + 八步法整合策略
    
    参数：
      llm_input: LLM候选文件路径（CSV）或 'db' 表示从数据库加载
      output_dir: 输出目录
      trade_date: 交易日期
      min_pass_count: 最少通过的共振策略数量
      require_core: 是否要求核心3策略必须全部通过
      enable_annual_line: 是否启用年线过滤
      enable_bollinger: 是否启用布林带过滤
      llm_min_score: LLM最低分数阈值
      use_db: 是否从数据库加载LLM候选
    
    返回：
      最终入选标的列表
    """
    print("=" * 70)
    print("  策略整合器 — 5策略共振 + LLM多源 + 八步法")
    print("=" * 70)
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  核心策略: 20周线 + 均线多头 + MACD金叉")
    if enable_bollinger:
        print(f"  增强策略: 布林上轨")
    if enable_annual_line:
        print(f"  增强策略: 年线")
    print(f"  最少通过: {min_pass_count}/{3 + int(enable_bollinger) + int(enable_annual_line)}")
    print("=" * 70)

    # 获取最新交易日
    if trade_date is None:
        from core.db.connection import get_db
        from psycopg2.extras import RealDictCursor
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT MAX(trade_date) as max_date FROM daily_quotes;")
        row = cur.fetchone()
        trade_date = row['max_date'] if row else datetime.now(BEIJING_TZ).date()
        cur.close()
        conn.close()
        print(f"\n  最新交易日: {trade_date}")

    # ========== 第1层：5策略共振过滤 ==========
    print(f"\n{'='*70}")
    print(f"  [Layer 1/3] 5策略共振过滤")
    print(f"{'='*70}")

    resonance_results = run_resonance_filter(
        trade_date=trade_date,
        min_pass_count=min_pass_count,
        require_core=require_core,
        enable_annual_line=enable_annual_line,
        enable_bollinger=enable_bollinger,
        verbose=True
    )

    if not resonance_results:
        print("✗ 没有股票通过共振过滤")
        return []

    resonance_codes = [r['ts_code'] for r in resonance_results]
    print(f"✓ 共振过滤通过: {len(resonance_codes)} 只")

    # ========== 第2层：LLM多源策略 ==========
    print(f"\n{'='*70}")
    print(f"  [Layer 2/3] LLM多源策略筛选")
    print(f"{'='*70}")

    llm_candidates = []
    if llm_input and llm_input.lower() == 'db':
        llm_candidates = load_llm_candidates_from_db(trade_date, min_score=llm_min_score)
        print(f"✓ 从数据库加载LLM候选: {len(llm_candidates)} 只")
    elif llm_input and Path(llm_input).exists():
        llm_candidates = load_llm_candidates_from_csv(llm_input, min_score=llm_min_score)
    else:
        print("  ⚠️ 未提供LLM候选输入，跳过LLM层（直接使用共振结果）")

    if llm_candidates:
        # 标准化代码格式
        llm_codes = set(normalize_code(code) for code, _ in llm_candidates)
        # 取共振和LLM的交集
        intersection = [code for code in resonance_codes if code in llm_codes]
        print(f"  共振候选: {len(resonance_codes)} 只")
        print(f"  LLM候选: {len(llm_codes)} 只")
        print(f"  交集: {len(intersection)} 只")

        if not intersection:
            print("  ⚠️ 共振和LLM无交集，使用共振结果继续")
            final_candidates = resonance_codes
        else:
            final_candidates = intersection
    else:
        final_candidates = resonance_codes

    if not final_candidates:
        print("✗ 没有候选标的进入八步法筛选")
        return []

    print(f"\n✓ 进入八步法筛选: {len(final_candidates)} 只")

    # ========== 第3层：隔夜八步法 ==========
    print(f"\n{'='*70}")
    print(f"  [Layer 3/3] 隔夜八步法精选")
    print(f"{'='*70}")

    # 获取市场情绪 (returns Tuple[int, str])
    sentiment_score, mood = fetch_market_sentiment()
    print(f"  市场情绪评分: {sentiment_score}  |  情绪: {mood}")

    # 运行八步法扫描：用 stable + upper 双池扫描，然后按 resonance 结果过滤
    stable_cfg = copy.deepcopy(CONFIG_STABLE)
    upper_cfg = copy.deepcopy(CONFIG_UPPER)
    stable_cfg["MODE"] = "post"
    upper_cfg["MODE"] = "post"

    # scan_pool 返回 6-tuple: (results, reject_stats, name_map, pool_size, real_count, time_weight)
    stable_results, _, stable_name_map, _, _, _ = scan_pool(stable_cfg, sentiment_score, mood, preloaded=False)
    upper_results, _, upper_name_map, _, _, _ = scan_pool(upper_cfg, sentiment_score, mood, preloaded=True)

    # 合并名称映射
    all_name_map = {**stable_name_map, **upper_name_map}

    # 合并去重然后按 resonance 结果过滤
    all_results = {}
    for r in stable_results + upper_results:
        raw_code = r.get('code', '')
        code = normalize_code(raw_code)
        r['code'] = code  # 统一为标准格式
        if code not in all_results or r.get('score', 0) > all_results[code].get('score', 0):
            # 补充 name 和 industry（scan_pool 不返回这些字段）
            if 'name' not in r or not r.get('name'):
                r['name'] = all_name_map.get(raw_code, all_name_map.get(code, ''))
            if 'industry' not in r:
                from overnight_8step.zuiyou1 import get_stock_industry
                industry = get_stock_industry(raw_code)
                r['industry'] = industry
            all_results[code] = r

    resonance_set = {normalize_code(c) for c in final_candidates}
    result = [all_results[code] for code in resonance_set if code in all_results]
    result.sort(key=lambda x: x.get('score', 0), reverse=True)

    # ========== 输出结果 ==========
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
    out_path = out_dir / f"resonance_{today}.csv"

    if result:
        # 构建共振结果字典，O(1)查找替代O(n²)线性扫描
        resonance_map = {r['ts_code']: r for r in resonance_results}

        # 写入CSV
        with open(out_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow([
                'code', 'name', 'pct', 'vol_ratio', 'turn', 'score', 'tags', 'industry',
                'ma_20week', 'ma_bullish', 'macd', 'bollinger', 'annual_line',
                'passed_count', 'total_count'
            ])
            for item in result:
                code = item.get('code', '')
                resonance_info = resonance_map.get(code)

                writer.writerow([
                    item.get('code', ''),
                    item.get('name', ''),
                    item.get('pct', 0),
                    item.get('vol_ratio', 0),
                    item.get('turn', 0),
                    item.get('score', 0),
                    item.get('tags', ''),
                    item.get('industry', ''),
                    resonance_info['filters']['ma_20week'].get('passed', False) if resonance_info else False,
                    resonance_info['filters']['ma_bullish'].get('passed', False) if resonance_info else False,
                    resonance_info['filters']['macd'].get('passed', False) if resonance_info else False,
                    resonance_info['filters'].get('bollinger', {}).get('passed', False) if resonance_info else False,
                    resonance_info['filters'].get('annual_line', {}).get('passed', False) if resonance_info else False,
                    resonance_info['passed_count'] if resonance_info else 0,
                    resonance_info['total_count'] if resonance_info else 0,
                ])

        print(f"\n{'='*70}")
        print(f"  ✅ 整合筛选完成!")
        print(f"  输出文件: {out_path}")
        print(f"  入选标的: {len(result)} 只")
        print(f"{'='*70}")

        # 打印详细结果
        print("\n--- 入选标的详情 ---")
        for item in result:
            code = item.get('code', '')
            resonance_info = resonance_map.get(code)

            print(f"\n  {item['code']} {item['name']}")
            print(f"    涨幅: {item['pct']:.2f}%  量比: {item['vol_ratio']:.2f}  换手: {item['turn']:.2f}%")
            print(f"    评分: {item['score']}  标签: {' | '.join(item.get('tags', []))}")

            if resonance_info:
                print(f"    共振策略: {resonance_info['passed_count']}/{resonance_info['total_count']} 通过")
                if 'ma_20week' in resonance_info['filters']:
                    f = resonance_info['filters']['ma_20week']
                    print(f"      20周线: {'✅' if f.get('passed') else '✗'} (MA100={f.get('ma_100')}, 偏离={f.get('bias_pct')}%)")
                if 'ma_bullish' in resonance_info['filters']:
                    f = resonance_info['filters']['ma_bullish']
                    print(f"      均线多头: {'✅' if f.get('passed') else '✗'} (MA5={f.get('ma_5')}, MA10={f.get('ma_10')}, MA20={f.get('ma_20')})")
                if 'macd' in resonance_info['filters']:
                    f = resonance_info['filters']['macd']
                    print(f"      MACD: {'✅' if f.get('passed') else '✗'} (DIF={f.get('dif')}, DEA={f.get('dea')}, HIST={f.get('hist')})")
                if 'bollinger' in resonance_info['filters']:
                    f = resonance_info['filters']['bollinger']
                    print(f"      布林上轨: {'✅' if f.get('passed') else '✗'} (突破={f.get('breakout_pct')}%, 量比={f.get('volume_ratio')})")
                if 'annual_line' in resonance_info['filters']:
                    f = resonance_info['filters']['annual_line']
                    print(f"      年线: {'✅' if f.get('passed') else '✗'} (MA250={f.get('ma_250')}, 偏离={f.get('bias_pct')}%)")

        print(f"\n{'='*70}")
        print(f"  💡 操作指引")
        print(f"  稳健路径：次日09:35未维持昨收+1%，直接出局")
        print(f"  高位路径：次日竞价弱于昨收，集合竞价结束即清仓")
        print(f"  全局止损：亏损超2.5%无条件止损")
        print(f"{'='*70}\n")

        return result
    else:
        print("\n✗ 没有通过三重筛选的标的")
        return []


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="5策略共振 + LLM多源 + 八步法整合策略")
    parser.add_argument("--input", "-i", type=str, default=None,
                        help="LLM策略扫描结果CSV路径，或 'db' 表示从数据库加载")
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
    parser.add_argument("--llm-score", type=int, default=25,
                        help="LLM最低分数阈值（默认25）")
    args = parser.parse_args()

    trade_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None

    results = run_resonance_strategy(
        llm_input=args.input,
        output_dir=args.output,
        trade_date=trade_date,
        min_pass_count=args.min_pass,
        require_core=not args.no_core,
        enable_annual_line=not args.no_annual,
        enable_bollinger=not args.no_bollinger,
        llm_min_score=args.llm_score,
    )

    if not results:
        sys.exit(1)
