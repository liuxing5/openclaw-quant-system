"""回测 v11：2025-05-01 ~ latest
v11 fixes:
- 信号衰减阈值：创业板/科创板从-3%放宽到-5%
- Layer1过滤：关闭MACD多头和SAR支撑强制要求，降低total_score门槛0.40→0.30
- 保留v10所有改进（日内止损、趋势延续持仓、多维度选股）
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
log_file = open('bt_v11_log.txt', 'w', encoding='utf-8')
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
    # v11: 放宽Layer1过滤条件
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
print(f'  Layer1: total_score>=0.30, MACD=False, SAR=False', flush=True)
print(f'  Signal decay: GEM/STAR -5%, Main -3%', flush=True)
t0 = time.time()
try:
    backtester = FastBacktester(meta_cfg, pm_cfg, bt_cfg)
    result = backtester.run()
except Exception as e:
    import traceback
    error_msg = traceback.format_exc()
    print(f'ERROR: {e}', flush=True)
    with open('bt_v11_error.txt', 'w', encoding='utf-8') as f:
        f.write(error_msg)
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

    # 检查关键股票是否被选到
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

log_file.close()
