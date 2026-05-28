"""Test meta engine for one day with error handling"""
import sys, os, time, traceback, logging
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

# 配置日志
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(name)s %(message)s', force=True)

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

from datetime import date

print('Importing engine...')
sys.stdout.flush()

try:
    from strategies.meta_strategy.meta_engine import MetaStrategyEngine, MetaStrategyConfig
    print('Import OK')
    sys.stdout.flush()

    cfg = MetaStrategyConfig(
        layer0_min_advancers=2000,
        layer1_min_total_score=0.45,
        layer1_rsi_max=70.0,
        layer2_min_avg_amount_20d=5e7,
        layer2_turn_rate_min=2.0,
        layer2_turn_rate_max=20.0,
        layer3_volume_breakout_mult=2.0,
        layer3_min_launch_score=0.25,
        layer5_min_quant_score=75,
        max_final_candidates=8,
        verbose=False,
    )

    print(f'Running engine for 2026-04-01...')
    sys.stdout.flush()

    t0 = time.time()
    engine = MetaStrategyEngine(cfg)
    print('Engine created')
    sys.stdout.flush()

    result = engine.run(trade_date=date(2026, 4, 1), verbose=False)
    elapsed = time.time() - t0

    print(f'Engine returned {len(result)} candidates in {elapsed:.1f}s')
    print(f'Stats: {engine._stats}')
    sys.stdout.flush()

except Exception as e:
    print(f'ERROR: {e}')
    traceback.print_exc()
    sys.stdout.flush()
