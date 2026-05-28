"""回测 - v4: 提高止损阈值+meta_score过滤"""
import sys, os, time, logging, traceback, json
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('bt_output_v4.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
    force=True
)

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

from strategies.meta_strategy.fast_backtester import FastBacktester, BacktestConfig
from strategies.meta_strategy.meta_engine import MetaStrategyConfig
from strategies.meta_strategy.position_manager import PositionManagerConfig

# === v4 策略 ===
# v1: 3.28%收益, 0.65%回撤, 30%胜率, 18.94盈亏比, 10笔
# v2: -1.02%收益, 2.28%回撤, 13.3%胜率, 4.10盈亏比, 15笔 (参数太宽)
# v3: 0.84%收益, 1.75%回撤, 33.3%胜率, 2.86盈亏比, 15笔
#
# v3交易分析:
# - "破位放量"退出6次(40%), 量比1.3-1.4就触发, 太敏感
# - 000889.SZ亏-10.78% (meta_score=13.84, 低分股)
# - 000070.SZ赚13.59% (meta_score=49, 高分股)
# - meta_score>30的股票表现明显更好
#
# v4核心改动:
# 1. 破位放量量比阈值 1.2→2.0 (减少假信号)
# 2. 高量阴线量比阈值 3.0→4.0
# 3. meta_score过滤: 只买>25的
# 4. 保持v1的选股精度
# 5. 降低L0门槛到1200

meta_cfg = MetaStrategyConfig(
    # L0: 降低门槛增加机会
    layer0_min_advancers=1200,
    layer0_partial_cap=0.60,

    # L1: 保持精度
    layer1_min_total_score=0.40,
    layer1_rsi_max=75.0,
    layer1_top_n=500,

    # L2: 适度放宽换手率
    layer2_min_avg_amount_20d=5e7,
    layer2_turn_rate_min=1.5,
    layer2_turn_rate_max=30.0,
    layer2_min_circulating_mcap=2e9,

    # L3: 保持精度
    layer3_volume_breakout_mult=2.0,
    layer3_min_launch_score=0.25,

    # L5: 保持精度
    layer5_min_quant_score=70,
    layer5_pct_range_low=1.5,
    layer5_pct_range_high=7.0,

    # L6: 优化止损
    layer6_hard_stop_loss_pct=0.05,  # 回到5%硬止损
    layer6_overnight_stop_pct=0.025,
    layer6_trailing_activate_pct=0.05,  # 回到5%激活
    layer6_trailing_stop_pct=0.03,  # 回到3%回撤
    layer6_max_holding_days=12,

    # 动态权重
    weights_bull={'factor': 0.40, 'launch': 0.25, 'llm': 0.05, 'overnight': 0.30},
    weights_oscillate={'factor': 0.35, 'launch': 0.20, 'llm': 0.10, 'overnight': 0.35},
    weights_bear={'factor': 0.25, 'launch': 0.10, 'llm': 0.20, 'overnight': 0.45},

    max_final_candidates=8,
)

pm_cfg = PositionManagerConfig(
    hard_stop_loss_pct=0.05,
    trailing_activate_pct=0.05,
    trailing_stop_pct=0.03,
    max_holding_days=12,
    overnight_stop_pct=0.025,
    max_positions=8,
    single_position_pct=0.125,
    # 关键优化: 提高破位放量量比阈值
    breakdown_vol_ratio_min=2.0,  # 1.2→2.0
    high_vol_ratio_min=4.0,  # 3.0→4.0
)

bt_cfg = BacktestConfig(
    start_date="2026-04-01",
    end_date="2026-05-15",
    initial_capital=1_000_000.0,
    max_positions=8,
    single_position_pct=0.125,
    # meta_score过滤
    min_meta_score=25.0,
)

print(f'Running backtest v4 ({bt_cfg.start_date} ~ {bt_cfg.end_date})...', flush=True)

try:
    backtester = FastBacktester(meta_cfg, pm_cfg, bt_cfg)
    result = backtester.run()

    if result:
        with open('bt_result_v4.txt', 'w', encoding='utf-8') as f:
            f.write(result['summary'])
        if result['trades']:
            with open('bt_trades_v4.json', 'w', encoding='utf-8') as f:
                json.dump(result['trades'], f, ensure_ascii=False, indent=2, default=str)
        print(result['summary'])
        print('DONE', flush=True)
    else:
        print('NO RESULT', flush=True)
except Exception as e:
    print(f'FATAL ERROR: {e}', flush=True)
    traceback.print_exc()
