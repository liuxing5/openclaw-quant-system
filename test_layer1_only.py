"""测试Layer1完整流程"""
import sys, os, logging, time
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')
os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

logging.basicConfig(level=logging.WARNING)

from strategies.meta_strategy.meta_engine import run_multi_factor_scan, MetaStrategyConfig
from datetime import date

cfg = MetaStrategyConfig(
    layer1_min_total_score=0.45,
    layer1_rsi_max=70.0,
    layer1_top_n=500,
)

t0 = time.time()
print(f"Running Layer1 for 2026-04-13...")
result = run_multi_factor_scan(date(2026, 4, 13), cfg=cfg)
elapsed = time.time() - t0
print(f"Elapsed: {elapsed:.1f}s")
print(f"Results: {len(result)}")

if not result.empty:
    for _, row in result.iterrows():
        print(f"  {row['ts_code']}: total_score={row.get('total_score',0):.3f} momentum={row.get('momentum',0):.4f} rsi={row.get('rsi',0):.1f}")
    
    found = result[result['ts_code'] == '000026.SZ']
    if not found.empty:
        print(f"\n*** 000026.SZ found! total_score={found.iloc[0]['total_score']:.3f} ***")
    else:
        print(f"\n000026.SZ not in Layer1 results")
