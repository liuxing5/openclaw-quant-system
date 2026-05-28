"""查000026在4月10-27日的策略信号"""
import sys, os, logging
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')
os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

logging.basicConfig(level=logging.WARNING)

from strategies.meta_strategy.meta_engine import MetaStrategyEngine, MetaStrategyConfig
from datetime import date

meta_cfg = MetaStrategyConfig(
    layer0_min_advancers=1200,
    layer0_partial_cap=0.60,
    layer1_min_total_score=0.45,
    layer1_rsi_max=70.0,
    layer1_top_n=500,
    layer2_min_avg_amount_20d=5e7,
    layer2_turn_rate_min=2.0,
    layer2_turn_rate_max=20.0,
    layer2_min_circulating_mcap=2e9,
    layer3_volume_breakout_mult=2.0,
    layer3_min_launch_score=0.25,
    layer5_min_quant_score=75,
    layer5_pct_range_low=2.0,
    layer5_pct_range_high=6.0,
    max_final_candidates=8,
)

engine = MetaStrategyEngine(meta_cfg)

# 检查4/10, 4/13, 4/15, 4/21, 4/22, 4/23, 4/27
check_dates = [
    date(2026, 4, 10), date(2026, 4, 13), date(2026, 4, 15),
    date(2026, 4, 21), date(2026, 4, 22), date(2026, 4, 23),
    date(2026, 4, 27), date(2026, 4, 28),
]

for d in check_dates:
    print(f"\n=== {d} ===")
    try:
        result = engine.run(d, verbose=False)
        if result.empty:
            print("  无候选")
            continue
        # 查000026是否在结果中
        found = False
        for _, row in result.iterrows():
            if row['ts_code'] == '000026.SZ':
                print(f"  000026.SZ: meta_score={row.get('meta_score',0):.1f} launch={row.get('launch_score',0):.2f} factor={row.get('factor_score',0):.2f} close={row.get('close',0):.2f}")
                found = True
        if not found:
            # 检查前5名
            print(f"  000026不在候选中. 前5名:")
            for _, row in result.head(5).iterrows():
                print(f"    {row['ts_code']}: meta={row.get('meta_score',0):.1f} launch={row.get('launch_score',0):.2f}")
    except Exception as e:
        print(f"  错误: {e}")
