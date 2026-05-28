"""快速测试策略运行"""
import sys, os, logging
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')
os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

from strategies.meta_strategy.meta_engine import MetaStrategyEngine, MetaStrategyConfig
from datetime import date
import time

meta_cfg = MetaStrategyConfig(
    layer0_min_advancers=1200,
    layer1_min_total_score=0.45,
    layer1_rsi_max=70.0,
    layer1_top_n=500,
    max_final_candidates=8,
)

engine = MetaStrategyEngine(meta_cfg)

# 只测试4/13一天
t0 = time.time()
print(f"Running strategy for 2026-04-13...")
result = engine.run(date(2026, 4, 13), verbose=False)
elapsed = time.time() - t0
print(f"Elapsed: {elapsed:.1f}s")
print(f"Candidates: {len(result)}")

if not result.empty:
    for _, row in result.iterrows():
        print(f"  {row['ts_code']}: meta={row.get('meta_score',0):.1f} launch={row.get('launch_score',0):.2f}")
    
    # 检查000026
    found = result[result['ts_code'] == '000026.SZ']
    if not found.empty:
        print(f"\n*** 000026.SZ found! meta_score={found.iloc[0]['meta_score']:.1f} ***")
    else:
        print(f"\n000026.SZ not in final candidates")
