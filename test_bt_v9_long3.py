"""回测 v9 长周期：2026-01-01 ~ 最新"""
import sys, os, time, logging, json
sys.path.insert(0, '.')

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', force=True)

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
    layer6_trailing_activate_pct=0.08,
    layer6_trailing_stop_pct=0.05,
    layer6_max_holding_days=15,
    max_final_candidates=8,
)

pm_cfg = PositionManagerConfig(
    hard_stop_loss_pct=0.05,
    trailing_activate_pct=0.08,
    trailing_stop_pct=0.05,
    max_holding_days=15,
    max_positions=8,
)

bt_cfg = BacktestConfig(
    start_date="2026-01-01",
    end_date=str(latest_date),
    initial_capital=1_000_000.0,
    max_positions=8,
)

print(f'Running backtest v9 long ({bt_cfg.start_date} ~ {bt_cfg.end_date})...', flush=True)
t0 = time.time()
backtester = FastBacktester(meta_cfg, pm_cfg, bt_cfg)
result = backtester.run()
elapsed = time.time() - t0
print(f"Backtest elapsed: {elapsed:.0f}s", flush=True)

if result:
    # 保存汇总
    with open('bt_result_v9_long.txt', 'w', encoding='utf-8') as f:
        f.write(result['summary'])

    # 保存交易JSON
    with open('bt_trades_v9_long.json', 'w', encoding='utf-8') as f:
        json.dump(result['trades'], f, ensure_ascii=False, indent=2, default=str)

    # 逐笔交易明细
    trades = result['trades']
    print("\n" + "=" * 100, flush=True)
    print(f"  逐笔交易明细 ({len(trades)}笔)", flush=True)
    print("=" * 100, flush=True)
    print(f"{'序号':>4} {'代码':<12} {'买入日':<12} {'买入价':>8} {'卖出日':<12} {'卖出价':>8} {'数量':>6} {'盈亏%':>8} {'天数':>4} {'退出原因'}", flush=True)
    print("-" * 100, flush=True)

    total_pnl = 0
    win_count = 0
    for idx, t in enumerate(trades, 1):
        pnl = t['pnl_pct']
        total_pnl += pnl
        if pnl > 0:
            win_count += 1
        reason = t['exit_reason'].split('(')[0]
        print(f"{idx:>4} {t['ts_code']:<12} {str(t['entry_date']):<12} {t['entry_price']:>8.2f} {str(t['exit_date']):<12} {t['exit_price']:>8.2f} {t['shares']:>6} {pnl:>+8.2%} {t['holding_days']:>4} {reason}", flush=True)

    print("-" * 100, flush=True)
    print(f"总交易: {len(trades)}笔  胜率: {win_count/len(trades):.1%}  平均盈亏: {total_pnl/len(trades):+.2%}", flush=True)
    print("\n" + result['summary'], flush=True)
    print('DONE', flush=True)
else:
    print('NO RESULT', flush=True)
