"""回测 v10：2025-05-01 ~ 2026-05-22
v10 fixes:
- 日内大跌>5%当日收盘卖出（不等次日）
- 趋势延续持仓：收盘>5日均线且盈利时不触发时间止损
- Layer1增加趋势启动选股（连续上涨+量能放大+收盘>5日均线）
- 隔夜止损放宽到5%（减少正常波动被洗出）
"""
import sys, os, time, logging, json
sys.path.insert(0, '.')

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', force=True,
                    handlers=[logging.StreamHandler(sys.stdout)])

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
    layer5_min_quant_score=85,       # 提高量化评分门槛
    layer6_trailing_activate_pct=0.08,
    layer6_trailing_stop_pct=0.05,
    layer6_max_holding_days=20,      # 延长持仓到20天
    max_final_candidates=5,          # 减少每日候选到5只
)

pm_cfg = PositionManagerConfig(
    hard_stop_loss_pct=0.05,
    trailing_activate_pct=0.08,      # 8%激活移动止盈
    trailing_stop_pct=0.05,          # 从高点回撤5%止盈
    max_holding_days=20,             # 延长到20天
    overnight_stop_pct=0.03,         # 次日亏损>3%出局
    max_positions=5,                 # 减少持仓到5只
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
