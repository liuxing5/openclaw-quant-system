"""Quick test: write 1 day of overnight_8step candidates."""
from __future__ import annotations

import io
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.utils.env import load_project_env
load_project_env()

from core.utils.trading_calendar import get_trading_days_in_range
from core.db.candidates import write_candidates
from backfill_step2_score_write import (
    score_overnight_8step, load_industries, baostock_to_ts_code,
)

DATA_DIR = Path(__file__).parent / "data" / "klines" / "2026-01"

all_klines = {}
for fp in DATA_DIR.glob("*.json"):
    code = fp.stem
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data and isinstance(data, list):
            all_klines[code] = data
    except Exception:
        pass
print(f"Loaded {len(all_klines)} stocks")

tds = get_trading_days_in_range(date(2026, 1, 1), date(2026, 1, 31))
print(f"Trading days: {len(tds)}")

td = tds[0]
td_str = td.strftime("%Y-%m-%d")
cutoff = (td - timedelta(days=45)).strftime("%Y-%m-%d")

day_klines = {}
for code, klines in all_klines.items():
    sliced = [k for k in klines if cutoff <= k.get('date', '') <= td_str]
    if sliced:
        day_klines[code] = sliced
print(f"Day {td}: {len(day_klines)} stocks with klines")

industries = load_industries()
print(f"Industries: {len(industries)}")

picks = []
for code, klines in day_klines.items():
    industry = industries.get(code, '')
    result = score_overnight_8step(code, klines, industry)
    if result:
        result['code'] = code
        picks.append(result)
print(f"Picks: {len(picks)}")

if picks:
    path_targets = {'stable': (1.03, 1.05), 'upper': (1.05, 1.07)}
    positions = {'stable': 0.08, 'upper': 0.05}
    by_pool = defaultdict(list)
    for p in picks:
        by_pool[p['pool']].append(p)

    items = []
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
                'sources': [{'source': 'zuiyou1_backfill', 'pool': pool_label}],
            })

    print(f"Items to write: {len(items)}")
    for it in items:
        print(f"  {it['ts_code']} score={it['final_score']:.1f}")

    print("Writing to DB...")
    t0 = time.time()
    n = write_candidates(items, td, source='overnight_8step', run_mode='afternoon')
    elapsed = time.time() - t0
    print(f"Written: {n} in {elapsed:.1f}s")
else:
    print("No picks")
