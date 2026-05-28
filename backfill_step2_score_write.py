"""
Step 2: 读取 K 线数据，评分并写入 daily_candidates 表
====================================================
从 data/klines/ 目录读取本地 K 线 JSON 文件，
对每个交易日进行策略评分，写入数据库。

用法:
  python backfill_step2_score_write.py --month 2026-01 --strategy overnight_8step
  python backfill_step2_score_write.py --month 2026-01 --strategy llm_multisource
  python backfill_step2_score_write.py --start 2026-01-01 --end 2026-05-27 --strategy overnight_8step
"""
from __future__ import annotations

import argparse
import gc
import io
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.utils.env import load_project_env
from core.db.candidates import write_candidates

load_project_env()

MIN_SELECT_SCORE = 25
MAX_SELECTED = 5
DATA_DIR = Path(__file__).parent / "data" / "klines"


def baostock_to_ts_code(bs_code: str) -> str:
    parts = bs_code.split('.')
    if len(parts) == 2:
        return f"{parts[1]}.{parts[0].upper()}"
    return bs_code


def calc_price_levels(close, ts_code):
    if not close:
        return {}
    code = ts_code.split('.')[0]
    is_kc_cy = code.startswith(('688', '300', '301'))
    if is_kc_cy:
        return {
            'entry_low': round(close * 0.985, 2), 'entry_high': round(close * 1.015, 2),
            'stop_loss': round(close * 0.95, 2), 'target_1': round(close * 1.08, 2),
            'target_2': round(close * 1.15, 2),
        }
    else:
        return {
            'entry_low': round(close * 0.99, 2), 'entry_high': round(close * 1.01, 2),
            'stop_loss': round(close * 0.97, 2), 'target_1': round(close * 1.05, 2),
            'target_2': round(close * 1.10, 2),
        }


def parse_kline_row(row: dict):
    try:
        close = float(row.get('close', 0))
        pct_chg = float(row.get('pctChg', 0))
        turnover = float(row.get('turn', 0))
        amount = float(row.get('amount', 0))
        is_st = row.get('isST', '0')
    except (ValueError, TypeError):
        return None
    if close <= 0 or is_st == '1':
        return None
    return close, pct_chg, turnover, amount, is_st


def load_klines_from_dir(month: str) -> Dict[str, List[dict]]:
    month_dir = DATA_DIR / month
    if not month_dir.exists():
        print(f"❌ 目录不存在: {month_dir}", flush=True)
        return {}

    all_klines = {}
    for fp in month_dir.glob("*.json"):
        code = fp.stem
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data and isinstance(data, list):
                all_klines[code] = data
        except Exception:
            pass

    print(f"  📂 加载 K 线: {len(all_klines)} 只股票 ({month})", flush=True)
    return all_klines


def load_industries() -> Dict[str, str]:
    cache_file = os.path.expanduser("~/.cache/zuiyou_industry.json")
    if not os.path.exists(cache_file):
        print("  ⚠️ 行业缓存不存在，将使用空行业", flush=True)
        return {}
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        cache = {k: v for k, v in raw.items() if v and not v.startswith('20')}
        print(f"  🏭 行业缓存: {len(cache)} 只", flush=True)
        return cache
    except Exception:
        return {}


def slice_klines_for_date(all_klines: Dict[str, List[dict]], target_date: date,
                          lookback: int = 45) -> Dict[str, List[dict]]:
    td_str = target_date.strftime("%Y-%m-%d")
    cutoff = (target_date - timedelta(days=lookback)).strftime("%Y-%m-%d")
    result = {}
    for code, klines in all_klines.items():
        sliced = [k for k in klines if cutoff <= k.get('date', '') <= td_str]
        if sliced:
            result[code] = sliced
    return result


# ============================================================
# overnight_8step 评分
# ============================================================

def score_overnight_8step(code: str, klines: List[dict], industry: str) -> Optional[dict]:
    if len(klines) < 12:
        return None
    latest = klines[-1]
    parsed = parse_kline_row(latest)
    if parsed is None:
        return None
    close, pct_chg, turnover, amount, is_st = parsed
    if amount < 1e8:
        return None

    pure_code = code.split('.')[1]
    is_kc_cy = pure_code.startswith(('688', '300', '301'))
    limit_threshold = 19.5 if is_kc_cy else 9.5

    vol_list = []
    for k in klines[-6:]:
        try:
            vol_list.append(float(k.get('volume', 0)))
        except (ValueError, TypeError):
            pass
    vol_ratio = 0
    if len(vol_list) >= 2:
        avg_vol = sum(vol_list[:-1]) / len(vol_list[:-1]) if vol_list[:-1] else 1
        vol_ratio = vol_list[-1] / avg_vol if avg_vol > 0 else 0

    close_list = []
    for k in klines:
        try:
            close_list.append(float(k.get('close', 0)))
        except (ValueError, TypeError):
            pass
    ma5 = sum(close_list[-6:-1]) / 5 if len(close_list) >= 6 else 0

    circ_mcap_yi = 0
    if turnover > 0 and amount > 0:
        circ_mcap_yi = amount / turnover / 1e6

    try:
        from strategies.overnight_8step.zuiyou1 import analyze_industry
        industry_bonus, category, tags = analyze_industry(industry)
    except Exception:
        industry_bonus, category, tags = 0, '', []

    score = _compute_8step_score(pct_chg, turnover, amount, vol_ratio, close, ma5, industry_bonus)

    if 3 <= pct_chg <= 5 and 100 <= circ_mcap_yi <= 2000 and score >= 60:
        return {
            'pool': 'stable', 'score': score, 'price': close,
            'pct': pct_chg, 'vol_ratio': vol_ratio, 'turn': turnover,
            'tags': tags + ['稳健路径', '黄金涨幅'],
        }
    if 5 <= pct_chg <= limit_threshold - 0.5 and 30 <= circ_mcap_yi <= 300 and score >= 70:
        return {
            'pool': 'upper', 'score': score, 'price': close,
            'pct': pct_chg, 'vol_ratio': vol_ratio, 'turn': turnover,
            'tags': tags + ['高位路径', '强势'],
        }
    return None


def _compute_8step_score(pct_chg, turnover, amount, vol_ratio, close, ma5, industry_bonus) -> float:
    score = 0
    if 3 <= pct_chg <= 5:
        score += 25
    elif 5 < pct_chg <= 7:
        score += 20
    elif 7 < pct_chg <= 9.5:
        score += 10
    if turnover > 3:
        score += min(20, turnover * 1.5)
    if amount > 0:
        score += min(20, 5 * math.log10(amount / 1e8 + 1))
    if vol_ratio > 1.5:
        score += min(10, (vol_ratio - 1.5) * 5)
    elif vol_ratio < 0.8:
        score -= 5
    if ma5 > 0 and close > 0:
        ma5_dist = abs(close - ma5) / ma5
        if ma5_dist < 0.02:
            score += 10
        elif ma5_dist < 0.05:
            score += 5
    score += industry_bonus
    return max(0, score)


def write_overnight_8step_candidates(picks: List[dict], target_date: date, conn=None) -> int:
    items = []
    path_targets = {'stable': (1.03, 1.05), 'upper': (1.05, 1.07)}
    positions = {'stable': 0.08, 'upper': 0.05}

    by_pool: Dict[str, List] = defaultdict(list)
    for p in picks:
        by_pool[p['pool']].append(p)

    for pool_label, pool_picks in by_pool.items():
        t1_mult, t2_mult = path_targets[pool_label]
        position = positions[pool_label]
        pool_picks.sort(key=lambda x: x['score'], reverse=True)
        for pick in pool_picks[:5]:
            price = pick['price']
            ts_code = baostock_to_ts_code(pick['code'])
            logic_tags = pick.get('tags', []) + [f'pool:{pool_label}']
            items.append({
                'ts_code': ts_code,
                'stock_name': '',
                'final_score': float(pick['score']),
                'quant_score': float(pick['score']),
                'llm_score': 0,
                'consensus_score': 1.0,
                'mention_count': 1,
                'source_diversity': 1,
                'logic_tags': logic_tags,
                'selected': True,
                'position_pct': round(position, 4),
                'entry_low': round(price * 0.99, 2),
                'entry_high': round(price * 1.01, 2),
                'stop_loss': round(price * 0.975, 2),
                'target_1': round(price * t1_mult, 2),
                'target_2': round(price * t2_mult, 2),
                'sources': [{'source': 'zuiyou1_backfill', 'pool': pool_label,
                             'pct': pick.get('pct', 0),
                             'vol_ratio': pick.get('vol_ratio', 0),
                             'turn': pick.get('turn', 0)}],
            })

    if items:
        return write_candidates(items, target_date, source='overnight_8step', run_mode='afternoon', conn=conn)
    return 0


# ============================================================
# llm_multisource 评分
# ============================================================

def score_llm_multisource(code: str, klines: List[dict]) -> Optional[dict]:
    if not klines:
        return None
    latest = klines[-1]
    parsed = parse_kline_row(latest)
    if parsed is None:
        return None
    close, pct_chg, turnover, amount, is_st = parsed
    if amount < 1e8:
        return None
    if not (-3 < pct_chg < 7):
        return None
    if turnover < 3:
        return None

    pure_code = code.split('.')[1]
    is_kc_cy = pure_code.startswith(('688', '300', '301'))
    limit_threshold = 19.5 if is_kc_cy else 9.5
    if pct_chg >= limit_threshold:
        return None

    vol_list = []
    for k in klines[-6:]:
        try:
            vol_list.append(float(k.get('volume', 0)))
        except (ValueError, TypeError):
            pass
    vol_ratio = 0
    if len(vol_list) >= 2:
        avg_vol = sum(vol_list[:-1]) / len(vol_list[:-1]) if vol_list[:-1] else 1
        vol_ratio = vol_list[-1] / avg_vol if avg_vol > 0 else 0

    quant_score = _compute_llm_quant_score(pct_chg, turnover, amount, vol_ratio)
    quant_score = max(0, quant_score)
    quant_n = min(quant_score / 100.0, 1.0)
    final = quant_n * 100 * 0.9

    if final >= MIN_SELECT_SCORE:
        return {
            'final_score': final, 'quant_score': quant_score,
            'close': close, 'pct_chg': pct_chg, 'turnover_rate': turnover,
            'logic_tags': ['量化选股'],
        }
    return None


def _compute_llm_quant_score(pct_chg, turnover, amount, vol_ratio) -> float:
    s = 0
    if -3 < pct_chg < 7:
        s += 30 * (1 - abs(pct_chg - 2) / 5)
    elif 7 <= pct_chg < 9.5:
        s += 10
    if turnover > 0:
        s += min(30, turnover * 2)
    if amount > 0:
        s += min(40, 10 * math.log10(amount / 1e8 + 1))
    if vol_ratio > 1.5:
        s += min(15, (vol_ratio - 1.5) * 10)
    elif vol_ratio < 0.8:
        s -= 5
    return s


def write_llm_multisource_candidates(candidates: List[dict], target_date: date, conn=None) -> int:
    seen = {}
    for c in candidates:
        ts = c['ts_code']
        if ts not in seen or c['final_score'] > seen[ts]['final_score']:
            seen[ts] = c
    sorted_cands = sorted(seen.values(), key=lambda x: x['final_score'], reverse=True)

    qualified = [c for c in sorted_cands if c['final_score'] >= MIN_SELECT_SCORE]
    if not qualified:
        observation = sorted_cands[:10]
        for c in observation:
            c['selected'] = False
            c['position_pct'] = 0
            levels = calc_price_levels(c.get('close'), c['ts_code'])
            if levels:
                c.update(levels)
        if observation:
            return write_candidates(observation, target_date, source='llm_multisource', run_mode='afternoon', conn=conn)
        return 0

    selected_list = qualified[:MAX_SELECTED]
    items = []
    for c in qualified[:15]:
        is_selected = c in selected_list
        levels = calc_price_levels(c.get('close'), c['ts_code'])
        c['selected'] = is_selected
        c['position_pct'] = round(0.08 * c.get('consensus_score', 50) / 100, 4) if is_selected else 0
        if levels:
            c.update(levels)
        items.append(c)

    return write_candidates(items, target_date, source='llm_multisource', run_mode='afternoon', conn=conn)


# ============================================================
# 数据库连接
# ============================================================

DB_URL = os.environ.get('DATABASE_URL',
    "postgresql://postgres:wYFBB91zViSrk2vl@db.qoakbxswwjqfsgbcgepr.supabase.co:5432/postgres")


def get_db_conn():
    import psycopg2
    conn = psycopg2.connect(DB_URL, connect_timeout=30)
    conn.autocommit = True
    return conn


def ensure_conn(conn):
    if conn is not None and not conn.closed:
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
    return get_db_conn()


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", type=str, default=None, help="单月处理, 如 2026-01")
    parser.add_argument("--start", type=str, default=None, help="起始日期")
    parser.add_argument("--end", type=str, default=None, help="结束日期")
    parser.add_argument("--strategy", type=str, required=True,
                        choices=['overnight_8step', 'llm_multisource'])
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    strategy = args.strategy

    if args.month:
        year, month = args.month.split('-')
        start_date = date(int(year), int(month), 1)
        if int(month) == 12:
            end_date = date(int(year) + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(int(year), int(month) + 1, 1) - timedelta(days=1)
        months = [args.month]
    elif args.start and args.end:
        start_date = date.fromisoformat(args.start)
        end_date = date.fromisoformat(args.end)
        months = []
        current = start_date
        while current <= end_date:
            m = current.strftime("%Y-%m")
            if m not in months:
                months.append(m)
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)
    else:
        print("❌ 请指定 --month 或 --start/--end", flush=True)
        sys.exit(1)

    print("=" * 60, flush=True)
    print(f"  评分写入: {strategy}", flush=True)
    print(f"  日期范围: {start_date} ~ {end_date}", flush=True)
    print(f"  月份: {months}", flush=True)
    print("=" * 60, flush=True)

    from core.utils.trading_calendar import get_trading_days_in_range
    all_trading_days = get_trading_days_in_range(start_date, end_date)
    print(f"📅 交易日: {len(all_trading_days)} 天", flush=True)

    industries = load_industries()

    total_written = 0
    total_days = 0
    errors = []

    for month_str in months:
        print(f"\n{'='*60}", flush=True)
        print(f"  📆 处理月份: {month_str}", flush=True)
        print(f"{'='*60}", flush=True)

        all_klines = load_klines_from_dir(month_str)
        if not all_klines:
            print(f"  ⚠️ {month_str} 无 K 线数据，跳过", flush=True)
            continue

        month_trading_days = [td for td in all_trading_days if td.strftime("%Y-%m") == month_str]
        if not month_trading_days:
            print(f"  ⚠️ {month_str} 无交易日，跳过", flush=True)
            continue

        conn = None
        try:
            conn = get_db_conn()

            for i, td in enumerate(month_trading_days):
                t0 = time.time()

                if args.skip_existing:
                    try:
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT 1 FROM daily_candidates WHERE snapshot_date = %s AND source = %s LIMIT 1",
                            (td, strategy))
                        if cur.fetchone():
                            cur.close()
                            print(f"  ⏭️ [{i+1}/{len(month_trading_days)}] {td} 已有数据", flush=True)
                            continue
                        cur.close()
                    except Exception:
                        pass

                day_klines = slice_klines_for_date(all_klines, td, lookback=45)
                if not day_klines:
                    print(f"  ⚠️ [{i+1}/{len(month_trading_days)}] {td} 无K线", flush=True)
                    continue

                try:
                    conn = ensure_conn(conn)

                    if strategy == 'overnight_8step':
                        picks = []
                        for code, klines in day_klines.items():
                            industry = industries.get(code, '')
                            result = score_overnight_8step(code, klines, industry)
                            if result:
                                result['code'] = code
                                picks.append(result)
                        n = write_overnight_8step_candidates(picks, td, conn=conn)

                    elif strategy == 'llm_multisource':
                        candidates = []
                        for code, klines in day_klines.items():
                            result = score_llm_multisource(code, klines)
                            if result:
                                ts_code = baostock_to_ts_code(code)
                                result['ts_code'] = ts_code
                                result['stock_name'] = ''
                                result['mention_count'] = 1
                                result['source_diversity'] = 1
                                result['consensus_score'] = 30.0
                                result['llm_score'] = 0
                                candidates.append(result)
                        n = write_llm_multisource_candidates(candidates, td, conn=conn)
                    else:
                        n = 0

                    elapsed = time.time() - t0
                    print(f"  ✓ [{i+1}/{len(month_trading_days)}] {td}: {n} 条 ({elapsed:.1f}s)", flush=True)
                    total_written += n
                    if n > 0:
                        total_days += 1

                except Exception as e:
                    elapsed = time.time() - t0
                    print(f"  ❌ [{i+1}/{len(month_trading_days)}] {td}: 失败 ({elapsed:.1f}s) - {e}", flush=True)
                    errors.append((td, strategy, str(e)))
                    try:
                        if conn and not conn.closed:
                            conn.close()
                    except Exception:
                        pass
                    conn = None

        finally:
            if conn and not conn.closed:
                conn.close()

        del all_klines
        gc.collect()

    print(f"\n{'='*60}", flush=True)
    print(f"  完成! 策略={strategy}", flush=True)
    print(f"  总写入: {total_written} 条", flush=True)
    print(f"  覆盖天数: {total_days}", flush=True)
    if errors:
        print(f"  错误: {len(errors)} 个", flush=True)
        for td, strat, err in errors[:10]:
            print(f"    {td} {strat}: {err[:80]}", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
