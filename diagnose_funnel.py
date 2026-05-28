#!/usr/bin/env python3
"""诊断漏斗 - 直连模式"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

from datetime import date, timedelta
from strategies.meta_strategy.db_data_adapter import (
    get_trading_days, get_daily_quotes_for_date, get_market_overview,
    get_daily_quotes_batch, get_index_quotes, get_fundamental_batch, clear_cache
)
from strategies.meta_strategy.db_backtester import (
    layer0_market_risk, layer1_multi_factor_scan, layer2_fundamental_filter,
    layer3_launch_signals, layer4_llm_boost, layer5_overnight_score,
    layer6_sustain_eval, normalize_and_fuse, MetaBacktestConfig
)

out = open('diagnose_output.txt', 'w', encoding='utf-8')

def p(msg):
    out.write(msg + '\n')
    out.flush()

cfg = MetaBacktestConfig()
cfg.layer1_min_total_score = 0.40
cfg.layer1_top_n = 50

test_dates = [date(2026, 4, 2), date(2026, 4, 15), date(2026, 5, 6)]

idx_start = date(2026, 1, 1)
idx_end = date(2026, 5, 22)
p("Loading index data...")
index_data = get_index_quotes('000001.SH', idx_start, idx_end)
p(f"Index data: {len(index_data)} rows")

for td in test_dates:
    p(f"\n{'='*60}")
    p(f"日期: {td}")
    
    # L0
    idx_slice = index_data[index_data['trade_date'] <= td].tail(70)
    l0 = layer0_market_risk(td, cfg, idx_slice)
    p(f"  L0: passed={l0['passed']}, regime={l0['regime']}, breadth={l0.get('breadth_ratio', 'N/A')}")
    if not l0['passed']:
        p(f"      原因: {l0.get('reason', '')}")
        continue
    
    # 当日数据
    daily_df = get_daily_quotes_for_date(td, min_amount=cfg.layer2_min_amount)
    p(f"  当日活跃股票: {len(daily_df)}")
    
    # L1
    factor_df = layer1_multi_factor_scan(td, cfg, daily_df=daily_df)
    l1_codes = factor_df['ts_code'].tolist() if not factor_df.empty else []
    p(f"  L1 多因子扫描: {len(l1_codes)} 只")
    if factor_df is not None and not factor_df.empty:
        p(f"      Top3 scores: {factor_df['total_score'].head(3).tolist()}")
    
    if not l1_codes:
        continue
    
    # L2
    fund_data = get_fundamental_batch(l1_codes)
    l2_codes = layer2_fundamental_filter(l1_codes, td, cfg, daily_df=daily_df, fund_data=fund_data)
    p(f"  L2 基本面过滤: {len(l2_codes)} 只 (过滤掉{len(l1_codes)-len(l2_codes)}只)")
    
    if not l2_codes:
        continue
    
    # L3
    hist_start = td - timedelta(days=150)
    history_data = get_daily_quotes_batch(l2_codes, hist_start, td)
    launch_df = layer3_launch_signals(l2_codes, td, cfg, history_data=history_data, daily_df=daily_df)
    l3_codes = launch_df['ts_code'].tolist() if not launch_df.empty else []
    p(f"  L3 启动信号: {len(l3_codes)} 只")
    if not launch_df.empty:
        p(f"      Launch scores: {launch_df['launch_score'].head(5).tolist()}")
        p(f"      Signals: {launch_df['launch_signals'].head(5).tolist()}")
    
    # L5
    overnight_df = layer5_overnight_score(l2_codes, td, cfg, history_data=history_data, daily_df=daily_df)
    l5_codes = overnight_df['ts_code'].tolist() if not overnight_df.empty else []
    p(f"  L5 八步法评分: {len(l5_codes)} 只")
    if not overnight_df.empty:
        top5 = overnight_df.nlargest(5, 'overnight_score')
        p(f"      Top5 scores: {top5['overnight_score'].tolist()}")
        p(f"      Top5 pools: {top5['pool'].tolist()}")
        p(f"      Top5 pct_chg: {top5['pct_chg'].tolist()}")
    
    # 融合
    llm_data = layer4_llm_boost(l2_codes, td, cfg, fund_data=fund_data)
    sustain_df = layer6_sustain_eval(l2_codes, td, cfg, history_data=history_data)
    result_df = normalize_and_fuse(factor_df, launch_df, llm_data, overnight_df, sustain_df=sustain_df, weights=l0.get('weights', cfg.weights_oscillate))
    p(f"  融合结果: {len(result_df)} 只")
    if not result_df.empty:
        p(f"      Top3 meta_score: {result_df['meta_score'].head(3).tolist()}")
    
    clear_cache()

p("\n诊断完成!")
out.close()
