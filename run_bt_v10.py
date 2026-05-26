"""回测 v10：2025-05-01 ~ 2026-05-22
v10 fixes:
- 日内大跌>8%当日收盘卖出（不等次日）
- 趋势延续持仓：收盘>5日均线且盈利时不触发时间止损
- Layer1增加趋势启动+均线突破+连涨股选股
- 移动止盈/破位放量/高量阴线当日收盘卖出
"""
import sys, os, time, logging, json
sys.path.insert(0, '.')

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

# 重定向所有输出到文件
log_file = open('bt_v10_log.txt', 'w', encoding='utf-8')
sys.stdout = log_file
sys.stderr = log_file

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', force=True,
                    handlers=[logging.StreamHandler(log_file)])

from strategies.meta_strategy.fast_backtester import FastBacktester, BacktestConfig
from strategies.meta_strategy.meta_engine import MetaStrategyConfig
from strategies.meta_strategy.position_manager import PositionManagerConfig

# 查最新交易日
from core.db.connection import get_db
conn = get_db()
cur = conn.cursor()
cur.execute("SELECT MAX(trade_date) FROM daily_quotes")
latest_date = cur.fetchone()[0]
conn.close()

meta_cfg = MetaStrategyConfig(
    layer0_min_advancers=1200,
    layer5_min_quant_score=85,
    layer6_trailing_activate_pct=0.08,
    layer6_trailing_stop_pct=0.05,
    layer6_max_holding_days=20,
    max_final_candidates=5,
)

pm_cfg = PositionManagerConfig(
    hard_stop_loss_pct=0.05,
    trailing_activate_pct=0.08,
    trailing_stop_pct=0.05,
    max_holding_days=20,
    overnight_stop_pct=0.03,
    max_positions=5,
)

bt_cfg = BacktestConfig(
    start_date="2025-05-01",
    end_date=str(latest_date),
    initial_capital=1_000_000.0,
    max_positions=5,
)

print(f'Running backtest v10 ({bt_cfg.start_date} ~ {bt_cfg.end_date})...', flush=True)
t0 = time.time()
try:
    backtester = FastBacktester(meta_cfg, pm_cfg, bt_cfg)
    result = backtester.run()
except Exception as e:
    import traceback
    error_msg = traceback.format_exc()
    print(f'ERROR: {e}', flush=True)
    with open('bt_v10_error.txt', 'w', encoding='utf-8') as f:
        f.write(error_msg)
    result = None
elapsed = time.time() - t0
print(f"Backtest elapsed: {elapsed:.0f}s", flush=True)

if result:
    with open('bt_result_v10.txt', 'w', encoding='utf-8') as f:
        f.write(result['summary'])
    with open('bt_trades_v10.json', 'w', encoding='utf-8') as f:
        json.dump(result['trades'], f, ensure_ascii=False, indent=2, default=str)

    trades = result['trades']
    print(f"\n总交易: {len(trades)}笔", flush=True)
    print(result['summary'], flush=True)
    print('DONE', flush=True)
else:
    print('NO RESULT', flush=True)

log_file.close()
