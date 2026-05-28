"""
daily_candidates 历史回填脚本（v2 批量优化版）
=============================
从 2026-01-01 到 2026-05-27，按交易日逐日回填 daily_candidates 表。

核心优化：
  - 每只股票一次性获取全量 K 线（整个日期范围），然后按日切片
  - 将 1800 只 × 100+ 天 = 180,000 次查询 → 1800 次查询
  - 全局复用 baostock 连接
  - 使用项目内置交易日历（含 2026 年节假日）

使用方式：
  python backfill_daily_candidates.py
  python backfill_daily_candidates.py --start 2026-03-01 --end 2026-05-27
  python backfill_daily_candidates.py --strategy overnight_8step
  python backfill_daily_candidates.py --skip-existing
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, List, Dict, Optional, Set, Tuple

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.utils.env import load_project_env
from core.db.connection import get_db_fresh, db_configured
from core.db.candidates import write_candidates

load_project_env()

MIN_SELECT_SCORE = 25
MAX_SELECTED = 5
FIELDS_K = "date,code,open,high,low,close,volume,amount,turn,pctChg,isST"


# ============================================================
# 辅助函数
# ============================================================

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
    """解析单行K线数据，返回 (close, pct_chg, turnover, amount, is_st) 或 None"""
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


# ============================================================
# 数据加载：批量获取 K 线 + 行业
# ============================================================

def _fetch_klines_single(bs, code: str, start_date: str, end_date: str, fields: str, timeout: int = 15) -> Optional[List[dict]]:
    """使用子进程获取单只股票的K线数据，超时返回 None。
    baostock 的 rs.next() 可能无限阻塞，子进程超时后可被强制终止。"""
    import subprocess
    # 使用字符串拼接避免 f-string 的 {{ }} 问题
    script = (
        "import baostock as bs\n"
        "import json\n"
        "import sys\n"
        "try:\n"
        "    lg = bs.login()\n"
        "    if lg.error_code != '0':\n"
        "        print(json.dumps({'error': 'login_failed'}))\n"
        "        sys.exit(0)\n"
        f"    rs = bs.query_history_k_data_plus('{code}', '{fields}', start_date='{start_date}', end_date='{end_date}', frequency='d', adjustflag='3')\n"
        "    if rs.error_code != '0':\n"
        "        bs.logout()\n"
        "        print(json.dumps({'error': 'query_failed'}))\n"
        "        sys.exit(0)\n"
        "    rows = []\n"
        "    while rs.next():\n"
        "        rows.append(dict(zip(rs.fields, rs.get_row_data())))\n"
        "    bs.logout()\n"
        "    print(json.dumps({'data': rows}))\n"
        "except Exception as e:\n"
        "    try:\n"
        "        bs.logout()\n"
        "    except:\n"
        "        pass\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, '-c', script],
            capture_output=True, text=True, timeout=timeout,
            encoding='utf-8', errors='replace',
        )
        if result.returncode != 0:
            return None
        # 取最后一行（baostock 的 login/logout 输出可能混入）
        lines = result.stdout.strip().split('\n')
        for line in reversed(lines):
            line = line.strip()
            if line.startswith('{'):
                try:
                    obj = json.loads(line)
                    if 'data' in obj:
                        return obj['data']
                    return None
                except json.JSONDecodeError:
                    continue
        return None
    except subprocess.TimeoutExpired:
        print(f"    ⚠️ {code} 超时 ({timeout}s)", flush=True)
        return None
    except Exception as e:
        print(f"    ⚠️ {code} 异常: {e}", flush=True)
        return None


def _suppress_baostock_output():
    """抑制 baostock 的 login/logout 输出"""
    import sys, io
    baostock_logger = None
    try:
        import baostock.common.contants as _cnt
        if hasattr(_cnt, 'logger'):
            baostock_logger = _cnt.logger
    except Exception:
        pass
    if baostock_logger:
        baostock_logger.setLevel(40)  # CRITICAL only


def load_all_klines(codes: List[str], start_date: str, end_date: str) -> Dict[str, List[dict]]:
    """
    批量获取所有股票的 K 线数据。
    使用子进程获取每只股票，超时自动终止。
    返回: {code: [kline_dict, ...]}，按日期升序排列。
    """
    print(f"  📊 批量获取 K 线数据: {len(codes)} 只股票, {start_date} ~ {end_date}", flush=True)
    all_klines: Dict[str, List[dict]] = {}
    t0 = time.time()
    done_count = 0
    fail_count = 0

    for code in codes:
        try:
            data = _fetch_klines_single(None, code, start_date, end_date, FIELDS_K, timeout=15)
        except Exception as e:
            print(f"    ❌ {code} 异常: {e}", flush=True)
            data = None
        done_count += 1

        if data is not None:
            all_klines[code] = data
        else:
            fail_count += 1

        if done_count <= 5:
            elapsed = time.time() - t0
            print(f"    前5只: {code} ok={data is not None} ({elapsed:.1f}s)", flush=True)
        elif done_count % 100 == 0:
            elapsed = time.time() - t0
            eta = elapsed / done_count * (len(codes) - done_count)
            print(f"    K线进度: {done_count}/{len(codes)} ok={len(all_klines)} fail={fail_count} ({elapsed:.0f}s, ETA {eta:.0f}s)", flush=True)

    elapsed = time.time() - t0
    print(f"  ✅ K线加载完成: {len(all_klines)}/{len(codes)} 只有数据, {fail_count} 失败 ({elapsed:.0f}s)", flush=True)
    return all_klines


def load_all_industries(bs, codes: List[str]) -> Dict[str, str]:
    """批量获取行业数据，返回 {code: industry_name}"""
    # 先尝试从缓存文件加载
    cache_file = os.path.expanduser("~/.cache/zuiyou_industry.json")
    cache: Dict[str, str] = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # 过滤掉无效缓存（值以20开头的日期字符串）
            cache = {k: v for k, v in raw.items() if v and not v.startswith('20')}
        except Exception:
            pass

    # 检查缓存覆盖率
    cached_count = sum(1 for c in codes if c in cache)
    if cached_count > len(codes) * 0.8 or bs is None:
        print(f"  🏭 行业缓存已就绪: {cached_count}/{len(codes)}", flush=True)
        return cache

    # 需要查询的股票
    to_query = [c for c in codes if c not in cache]
    print(f"  🏭 批量获取行业数据: {len(to_query)} 只需查询...", flush=True)
    t0 = time.time()
    loaded = 0

    for code in to_query:
        try:
            rs = bs.query_stock_industry(code=code)
            if rs.error_code == '0':
                row = rs.get_row_data()
                if row and len(row) > 3 and row[3]:
                    cache[code] = row[3]
                    loaded += 1
                    if loaded % 100 == 0:
                        print(f"    行业进度: {loaded}/{len(to_query)}", flush=True)
        except Exception:
            pass

    # 保存缓存
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        tmp = cache_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, cache_file)
    except Exception:
        pass

    elapsed = time.time() - t0
    print(f"  ✅ 行业加载完成: {loaded} 只新增, 缓存总计 {len(cache)} 只 ({elapsed:.0f}s)", flush=True)
    return cache


def get_stock_pool(bs=None) -> List[str]:
    """获取合并股票池（hs300+zz500），使用子进程避免卡住"""
    import subprocess
    script = """
import baostock as bs
import json
import sys
try:
    lg = bs.login()
    if lg.error_code != '0':
        print(json.dumps({"error": "login_failed"}))
        sys.exit(0)
    rs = bs.query_hs300_stocks()
    hs300 = set()
    while rs.next():
        hs300.add(rs.get_row_data()[1])
    rs = bs.query_zz500_stocks()
    zz500 = set()
    while rs.next():
        zz500.add(rs.get_row_data()[1])
    bs.logout()
    all_codes = sorted(hs300 | zz500)
    print(json.dumps({"codes": all_codes, "hs300": len(hs300), "zz500": len(zz500)}))
except Exception as e:
    try:
        bs.logout()
    except:
        pass
    print(json.dumps({"error": str(e)}))
"""
    print("  📋 获取股票池...", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, '-c', script],
            capture_output=True, text=True, timeout=30,
            encoding='utf-8', errors='replace',
        )
        lines = result.stdout.strip().split('\n')
        # 调试：打印子进程输出行数
        print(f"  📋 子进程输出 {len(lines)} 行", flush=True)
        for line in reversed(lines):
            line_stripped = line.strip()
            if line_stripped.startswith('{'):
                try:
                    obj = json.loads(line_stripped)
                    if 'codes' in obj:
                        codes = obj['codes']
                        print(f"  📋 股票池: hs300={obj.get('hs300',0)}, zz500={obj.get('zz500',0)}, 合并={len(codes)}", flush=True)
                        return codes
                    else:
                        print(f"  📋 JSON 无 codes 字段: {list(obj.keys())}", flush=True)
                except json.JSONDecodeError as e:
                    print(f"  📋 JSON 解析失败: {e}", flush=True)
        print("  📋 股票池获取失败", flush=True)
        return []
    except subprocess.TimeoutExpired:
        print("  📋 股票池获取超时", flush=True)
        return []
    except Exception as e:
        print(f"  📋 股票池获取异常: {e}", flush=True)
        return []


# ============================================================
# 按日切片处理
# ============================================================

def slice_klines_by_date(all_klines: Dict[str, List[dict]], trading_days: List[date],
                         lookback: int = 45) -> Dict[date, Dict[str, List[dict]]]:
    """
    将全量 K 线按交易日切片。
    返回: {date: {code: [kline_rows_up_to_date]}}
    """
    print("  🔪 按日切片 K 线数据...", flush=True)
    trading_set = {d.strftime("%Y-%m-%d") for d in trading_days}
    result: Dict[date, Dict[str, List[dict]]] = {d: {} for d in trading_days}

    for code, klines in all_klines.items():
        # K 线已按日期升序
        # 为每个交易日，收集该日及之前 lookback 天内的数据
        for td in trading_days:
            td_str = td.strftime("%Y-%m-%d")
            cutoff = (td - timedelta(days=lookback)).strftime("%Y-%m-%d")
            sliced = [k for k in klines if cutoff <= k.get('date', '') <= td_str]
            if sliced:
                result[td][code] = sliced

    total_slices = sum(len(v) for v in result.values())
    print(f"  ✅ 切片完成: {len(trading_days)} 天, 总计 {total_slices} 个股票-日组合", flush=True)
    return result


# ============================================================
# 策略 1: overnight_8step 评分
# ============================================================

def score_overnight_8step(code: str, klines: List[dict], industry: str) -> Optional[dict]:
    """对单只股票进行八步法评分，返回候选信息或 None"""
    if len(klines) < 12:
        return None

    latest = klines[-1]
    parsed = parse_kline_row(latest)
    if parsed is None:
        return None
    close, pct_chg, turnover, amount, is_st = parsed

    # 流动性过滤
    if amount < 1e8:
        return None

    # 涨停阈值
    pure_code = code.split('.')[1]
    is_kc_cy = pure_code.startswith(('688', '300', '301'))
    limit_threshold = 19.5 if is_kc_cy else 9.5

    # 量比
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

    # MA5（不含今日）
    close_list = []
    for k in klines:
        try:
            close_list.append(float(k.get('close', 0)))
        except (ValueError, TypeError):
            pass
    ma5 = sum(close_list[-6:-1]) / 5 if len(close_list) >= 6 else 0

    # 市值估算
    circ_mcap_yi = 0
    if turnover > 0 and amount > 0:
        circ_mcap_yi = amount / turnover / 1e6

    # 行业评分
    from strategies.overnight_8step.zuiyou1 import analyze_industry
    industry_bonus, category, tags = analyze_industry(industry)

    # 评分
    score = _compute_8step_score(pct_chg, turnover, amount, vol_ratio, close, ma5,
                                  industry_bonus)

    # 稳健池筛选
    if 3 <= pct_chg <= 5 and 100 <= circ_mcap_yi <= 2000 and score >= 60:
        return {
            'pool': 'stable', 'score': score, 'price': close,
            'pct': pct_chg, 'vol_ratio': vol_ratio, 'turn': turnover,
            'tags': tags + ['稳健路径', '黄金涨幅'],
        }

    # 高位池筛选
    upper_pct_max = limit_threshold - 0.5
    if 5 <= pct_chg <= upper_pct_max and 30 <= circ_mcap_yi <= 300 and score >= 70:
        return {
            'pool': 'upper', 'score': score, 'price': close,
            'pct': pct_chg, 'vol_ratio': vol_ratio, 'turn': turnover,
            'tags': tags + ['高位路径', '强势'],
        }

    return None


def _compute_8step_score(pct_chg, turnover, amount, vol_ratio, close, ma5,
                          industry_bonus) -> float:
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


# ============================================================
# 策略 2: llm_multisource 评分
# ============================================================

def score_llm_multisource(code: str, klines: List[dict]) -> Optional[dict]:
    """对单只股票进行 LLM 多源量化评分，返回候选信息或 None"""
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

    # 量比
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


# ============================================================
# 写入 daily_candidates
# ============================================================

def write_overnight_8step_candidates(picks: List[dict], target_date: date, conn=None) -> int:
    """将 overnight_8step 候选写入数据库"""
    items = []
    path_targets = {'stable': (1.03, 1.05), 'upper': (1.05, 1.07)}
    positions = {'stable': 0.08, 'upper': 0.05}

    # 按池分组
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


def write_llm_multisource_candidates(candidates: List[dict], target_date: date, conn=None) -> int:
    """将 llm_multisource 候选写入数据库"""
    # 去重排序
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
# 策略 3: funnel_strategy 回填
# ============================================================

def backfill_funnel(trade_date: date) -> int:
    try:
        from strategies.funnel_strategy.funnel_engine import run_funnel_strategy
        from strategies.funnel_strategy.funnel_config import DEFAULT_FUNNEL_CONFIG
        cfg = DEFAULT_FUNNEL_CONFIG
        cfg.output_dir = './results'
        result = run_funnel_strategy(trade_date=trade_date, cfg=cfg)
        candidates = result.get('candidates', [])
        return len(candidates)
    except Exception as e:
        print(f"  ❌ funnel_strategy 回填失败 ({trade_date}): {e}", flush=True)
        import traceback
        traceback.print_exc()
        return 0


# ============================================================
# 主流程
# ============================================================

def get_existing_candidate_dates(start: date, end: date, source: str = None) -> set:
    conn = None
    try:
        import psycopg2
        conn = psycopg2.connect(
            "postgresql://postgres:wYFBB91zViSrk2vl@db.qoakbxswwjqfsgbcgepr.supabase.co:5432/postgres",
            connect_timeout=30,
        )
        conn.autocommit = True
        cur = conn.cursor()
        if source:
            cur.execute("""
                SELECT DISTINCT snapshot_date FROM daily_candidates
                WHERE snapshot_date >= %s AND snapshot_date <= %s AND source = %s;
            """, (start, end, source))
        else:
            cur.execute("""
                SELECT DISTINCT snapshot_date FROM daily_candidates
                WHERE snapshot_date >= %s AND snapshot_date <= %s;
            """, (start, end))
        dates = {row[0] for row in cur.fetchall()}
        cur.close()
        return dates
    except Exception as e:
        print(f"  ⚠️ 查询已有数据失败: {e}")
        return set()
    finally:
        if conn and not conn.closed:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description="daily_candidates 历史回填")
    parser.add_argument("--start", type=str, default="2026-01-01")
    parser.add_argument("--end", type=str, default="2026-05-27")
    parser.add_argument("--strategy", type=str, default=None,
                        choices=['funnel_strategy', 'llm_multisource', 'overnight_8step'])
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)

    if not db_configured():
        print("❌ 数据库未配置")
        sys.exit(1)

    print("=" * 70, flush=True)
    print("  daily_candidates 历史回填 v3（按月分批版）", flush=True)
    print("=" * 70, flush=True)
    print(f"  日期范围: {start_date} ~ {end_date}", flush=True)
    print(f"  策略: {args.strategy or '全部'}", flush=True)
    print(f"  跳过已有: {args.skip_existing}", flush=True)
    print("=" * 70, flush=True)

    # 交易日历
    from core.utils.trading_calendar import get_trading_days_in_range
    all_trading_days = get_trading_days_in_range(start_date, end_date)
    print(f"\n📅 交易日: {len(all_trading_days)} 天", flush=True)
    if not all_trading_days:
        print("❌ 无可用交易日")
        sys.exit(1)

    strategies = [args.strategy] if args.strategy else ['overnight_8step', 'llm_multisource', 'funnel_strategy']

    # 预加载行业分析模块
    if 'overnight_8step' in strategies:
        from strategies.overnight_8step.zuiyou1 import analyze_industry

    # ===== 按月分批处理 =====
    # 将交易日按月分组
    months: Dict[Tuple[int, int], List[date]] = defaultdict(list)
    for td in all_trading_days:
        months[(td.year, td.month)].append(td)

    total_written = 0
    total_days = 0
    errors = []

    for (year, month), trading_days in sorted(months.items()):
        month_start = trading_days[0]
        month_end = trading_days[-1]
        print(f"\n{'='*70}", flush=True)
        print(f"  📆 处理月份: {year}-{month:02d} ({len(trading_days)} 个交易日)", flush=True)
        print(f"{'='*70}", flush=True)

        # 已有数据检查
        existing_dates = set()
        if args.skip_existing:
            existing_dates = get_existing_candidate_dates(month_start, month_end, args.strategy)
            print(f"  已有数据覆盖 {len(existing_dates)} 天", flush=True)

        # 股票池（子进程方式，不需要主 baostock 连接）
        all_codes = get_stock_pool()

        # K 线（子进程方式，覆盖当月 + 45 天 lookback）
        kline_start = (month_start - timedelta(days=60)).strftime("%Y-%m-%d")
        kline_end = month_end.strftime("%Y-%m-%d")
        all_klines = load_all_klines(all_codes, kline_start, kline_end)

        # 行业（从缓存加载，不需要 baostock 连接）
        industries = load_all_industries(None, all_codes)

        # 按日切片
        daily_klines = slice_klines_by_date(all_klines, trading_days, lookback=45)

        # 逐日评分 + 写入（使用直连数据库，避免 Transaction pooler 卡住）
        db_conn = None

        def _get_db_conn():
            nonlocal db_conn
            # 检查连接是否有效
            if db_conn is not None and not db_conn.closed:
                try:
                    cur = db_conn.cursor()
                    cur.execute("SELECT 1")
                    cur.close()
                    return db_conn
                except Exception:
                    try:
                        db_conn.close()
                    except Exception:
                        pass
                    db_conn = None
            # 创建直连（绕过 pooler）
            import psycopg2
            db_conn = psycopg2.connect(
                "postgresql://postgres:wYFBB91zViSrk2vl@db.qoakbxswwjqfsgbcgepr.supabase.co:5432/postgres",
                connect_timeout=30,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=3,
            )
            db_conn.autocommit = True
            return db_conn

        try:
            _get_db_conn()  # 初始连接

            for i, td in enumerate(trading_days):
                if args.skip_existing and td in existing_dates:
                    print(f"  ⏭️ [{i+1}/{len(trading_days)}] {td} 已有数据，跳过", flush=True)
                    continue

                day_klines = daily_klines.get(td, {})
                if not day_klines:
                    print(f"  ⚠️ [{i+1}/{len(trading_days)}] {td} 无K线数据，跳过", flush=True)
                    continue

                print(f"\n  📅 [{i+1}/{len(trading_days)}] {td} ({len(day_klines)} 只股票)", flush=True)
                day_written = 0

                for strategy in strategies:
                    t0 = time.time()
                    try:
                        conn = _get_db_conn()  # 确保连接有效

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

                        elif strategy == 'funnel_strategy':
                            n = backfill_funnel(td)

                        else:
                            n = 0

                        elapsed = time.time() - t0
                        print(f"    ✓ {strategy}: {n} 条 ({elapsed:.1f}s)", flush=True)
                        day_written += n

                    except Exception as e:
                        elapsed = time.time() - t0
                        print(f"    ❌ {strategy}: 失败 ({elapsed:.1f}s) - {e}", flush=True)
                        errors.append((td, strategy, str(e)))
                        # 重连数据库
                        try:
                            if db_conn and not db_conn.closed:
                                db_conn.close()
                        except Exception:
                            pass
                        db_conn = None

                total_written += day_written
                if day_written > 0:
                    total_days += 1

        finally:
            if db_conn and not db_conn.closed:
                db_conn.close()

        # 释放内存
        del all_klines, daily_klines, industries
        import gc
        gc.collect()

    # 汇总
    print(f"\n{'='*70}", flush=True)
    print(f"  回填完成!", flush=True)
    print(f"  总写入: {total_written} 条", flush=True)
    print(f"  覆盖天数: {total_days}", flush=True)
    if errors:
        print(f"  错误: {len(errors)} 个", flush=True)
        for td, strategy, err in errors[:20]:
            print(f"    {td} {strategy}: {err[:80]}", flush=True)
    print(f"{'='*70}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"\n❌ FATAL ERROR: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
