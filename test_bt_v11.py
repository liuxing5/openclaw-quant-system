"""回测 v11 - 不重定向输出版本"""
import sys, os, time, logging, json
sys.path.insert(0, '.')

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', force=True)

print('Importing modules...', flush=True)

from strategies.meta_strategy.fast_backtester import FastBacktester, BacktestConfig
from strategies.meta_strategy.meta_engine import MetaStrategyConfig
from strategies.meta_strategy.position_manager import PositionManagerConfig

print('Connecting to DB...', flush=True)

from core.db.connection import get_db
conn = get_db()
cur = conn.cursor()
cur.execute("SELECT MAX(trade_date) FROM daily_quotes")
latest_date = cur.fetchone()[0]
conn.close()

print(f'Latest date: {latest_date}', flush=True)

meta_cfg = MetaStrategyConfig(
    layer0_min_advancers=1200,
    layer5_min_quant_score=85,
    layer6_trailing_activate_pct=0.08,
    layer6_trailing_stop_pct=0.05,
    layer6_max_holding_days=20,
    max_final_candidates=5,
    layer1_min_total_score=0.30,
    layer1_macd_bullish=False,
    layer1_sar_support=False,
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

print(f'Running backtest v11 ({bt_cfg.start_date} ~ {bt_cfg.end_date})...', flush=True)
t0 = time.time()
try:
    backtester = FastBacktester(meta_cfg, pm_cfg, bt_cfg)
    result = backtester.run()
except Exception as e:
    import traceback
    traceback.print_exc()
    result = None
elapsed = time.time() - t0
print(f"Backtest elapsed: {elapsed:.0f}s", flush=True)

if result:
    with open('bt_result_v11.txt', 'w', encoding='utf-8') as f:
        f.write(result['summary'])
    with open('bt_trades_v11.json', 'w', encoding='utf-8') as f:
        json.dump(result['trades'], f, ensure_ascii=False, indent=2, default=str)

    trades = result['trades']
    print(f"\n总交易: {len(trades)}笔", flush=True)
    print(result['summary'], flush=True)

    key_stocks = ['002565.SZ', '002361.SZ', '002384.SZ', '301308.SZ']
    for s in key_stocks:
        matched = [t for t in trades if t['ts_code'] == s]
        if matched:
            for m in matched:
                print(f"  {s}: 买入{m['entry_date']}@{m['entry_price']:.2f} 卖出{m['exit_date']}@{m['exit_price']:.2f} 收益{m['pnl_pct']:+.2%} 原因:{m['exit_reason']}", flush=True)
        else:
            print(f"  {s}: 未交易", flush=True)
    print('DONE', flush=True)
else:
    print('NO RESULT', flush=True)
