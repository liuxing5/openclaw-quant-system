import json
import numpy as np

with open('strategies/funnel_strategy/docs/main_uptrend_backtest.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

signals = data.get('all_signals', [])
print(f"总信号数: {len(signals)}")

env_scores = [s['env_score'] for s in signals if s.get('env_score') is not None]
print(f"\nenv_score 分布:")
for score in [0, 1, 2, 3]:
    n = sum(1 for e in env_scores if e == score)
    print(f"  env={score}: {n} ({n/len(env_scores)*100:.1f}%)")

print("\n--- 按env_score分组的退出收益 ---")
for score in [0, 1, 2, 3]:
    group = [s for s in signals if s.get('env_score') == score and s.get('exit_ret') is not None]
    if group:
        rets = [s['exit_ret'] for s in group]
        wins = sum(1 for r in rets if r > 0)
        stops = sum(1 for s in group if s.get('exit_type') == 'stop_loss')
        print(f"  env={score}: n={len(group)}, 胜率={wins/len(group)*100:.1f}%, 均值={np.mean(rets)*100:.2f}%, 止损率={stops/len(group)*100:.1f}%")

print("\n--- 4-5月信号env_score详情 ---")
apr_may = [s for s in signals if s.get('eval_date', '') >= '2026-04-20' and s.get('exit_ret') is not None]
for s in sorted(apr_may, key=lambda x: x.get('eval_date', '')):
    b20 = s.get('breadth_ma20', 'N/A')
    b5 = s.get('breadth_ma5', 'N/A')
    env = s.get('env_score', 'N/A')
    print(f"  {s['ts_code']} {s['eval_date']} score={s.get('composite_score',0):.1f} env={env} b20={b20} b5={b5} exit_ret={s['exit_ret']*100:.1f}% {s.get('exit_type','')}")

print("\n--- 按月份+env_score分组的退出收益 ---")
for month in ['2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05']:
    month_sigs = [s for s in signals if s.get('eval_date', '').startswith(month) and s.get('exit_ret') is not None]
    if month_sigs:
        rets = [s['exit_ret'] for s in month_sigs]
        wins = sum(1 for r in rets if r > 0)
        env0 = sum(1 for s in month_sigs if s.get('env_score') == 0)
        env1 = sum(1 for s in month_sigs if s.get('env_score') == 1)
        print(f"  {month}: n={len(month_sigs)}, 胜率={wins/len(month_sigs)*100:.1f}%, 均值={np.mean(rets)*100:.2f}%, env0={env0}, env1={env1}")
