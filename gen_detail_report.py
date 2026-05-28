"""v6回测详细报告生成器"""
import json, sys, os
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

# 读取交易数据
with open('bt_trades_v6.json', 'r', encoding='utf-8') as f:
    trades = json.load(f)

# 读取日志中的权益数据
with open('bt_output_v6.log', 'r', encoding='utf-8') as f:
    log_lines = f.readlines()

# 解析权益数据
equity_points = []
for line in log_lines:
    if '持仓' in line and '权益' in line:
        # 格式: [N/28] 2026-04-08: 持仓2只 权益1,004,404
        parts = line.strip().split()
        for p in parts:
            if p.startswith('2026-'):
                date_str = p.rstrip(':')
            if p.startswith('权益'):
                equity_str = p.replace('权益', '').replace(',', '')
                equity_points.append({'date': date_str, 'equity': float(equity_str)})

# 生成详细报告
report = []
report.append("=" * 90)
report.append("  融合元策略回测 v6 详细报告")
report.append("  回测区间: 2026-04-01 ~ 2026-05-15 | 初始资金: 1,000,000")
report.append("=" * 90)

# === 逐笔交易明细 ===
report.append("")
report.append("=" * 90)
report.append("  逐笔交易明细")
report.append("=" * 90)

initial_capital = 1_000_000.0
running_capital = initial_capital
cumulative_pnl = 0.0

for i, t in enumerate(trades, 1):
    entry_cost = t['entry_price'] * t['shares'] + t.get('commission', 0)
    exit_value = t['exit_price'] * t['shares'] - t.get('commission', 0)
    trade_pnl = exit_value - entry_cost
    cumulative_pnl += trade_pnl

    pnl_emoji = "+" if t['pnl_pct'] > 0 else ""
    win_tag = "WIN " if t['pnl_pct'] > 0 else "LOSS"

    report.append("")
    report.append(f"  ┌─ 交易 #{i:02d} {win_tag} ─────────────────────────────────────────────────")
    report.append(f"  │ 股票: {t['ts_code']}")
    report.append(f"  │ 买入: {t['entry_date']} @ ¥{t['entry_price']:.2f}  ({t['shares']}股)")
    report.append(f"  │ 卖出: {t['exit_date']} @ ¥{t['exit_price']:.2f}")
    report.append(f"  │ 持仓: {t['holding_days']}天  |  最高价: ¥{t['highest_price']:.2f}")
    report.append(f"  │ 收益率: {pnl_emoji}{t['pnl_pct']:.2%}  |  盈亏: {pnl_emoji}¥{trade_pnl:,.0f}")
    report.append(f"  │ 退出原因: {t['exit_reason']}")
    report.append(f"  │ meta_score: {t['meta_score']:.1f}")
    report.append(f"  │ 手续费: ¥{t.get('commission', 0):.2f}  |  滑点: {t.get('slippage', 0):.1%}")
    report.append(f"  └─ 累计盈亏: {'+' if cumulative_pnl >= 0 else ''}¥{cumulative_pnl:,.0f}  |  累计收益率: {'+' if cumulative_pnl/initial_capital >= 0 else ''}{cumulative_pnl/initial_capital:.2%}")

# === 交易汇总表 ===
report.append("")
report.append("=" * 90)
report.append("  交易汇总表")
report.append("=" * 90)
report.append("")
report.append(f"  {'#':>2}  {'股票':>12}  {'买入日':>12}  {'卖出日':>12}  {'天数':>4}  {'收益率':>8}  {'退出原因':<20}  {'meta':>5}")
report.append("  " + "-" * 86)

for i, t in enumerate(trades, 1):
    pnl_str = f"{t['pnl_pct']:+.2%}"
    reason_short = t['exit_reason'].split('(')[0]
    report.append(f"  {i:2d}  {t['ts_code']:>12}  {t['entry_date']:>12}  {t['exit_date']:>12}  {t['holding_days']:4d}  {pnl_str:>8}  {reason_short:<20}  {t['meta_score']:5.1f}")

# === 收益分析 ===
report.append("")
report.append("=" * 90)
report.append("  收益分析")
report.append("=" * 90)

wins = [t for t in trades if t['pnl_pct'] > 0]
losses = [t for t in trades if t['pnl_pct'] <= 0]

report.append(f"")
report.append(f"  盈利交易: {len(wins)}笔")
for t in sorted(wins, key=lambda x: -x['pnl_pct']):
    report.append(f"    {t['ts_code']}: {t['pnl_pct']:+.2%} ({t['holding_days']}天) - {t['exit_reason'].split('(')[0]}")

report.append(f"")
report.append(f"  亏损交易: {len(losses)}笔")
for t in sorted(losses, key=lambda x: x['pnl_pct']):
    report.append(f"    {t['ts_code']}: {t['pnl_pct']:+.2%} ({t['holding_days']}天) - {t['exit_reason'].split('(')[0]}")

# === 退出原因分析 ===
report.append("")
report.append("=" * 90)
report.append("  退出原因分析")
report.append("=" * 90)

exit_reasons = {}
for t in trades:
    reason = t['exit_reason'].split('(')[0]
    if reason not in exit_reasons:
        exit_reasons[reason] = {'count': 0, 'avg_pnl': [], 'total_pnl': 0}
    exit_reasons[reason]['count'] += 1
    exit_reasons[reason]['avg_pnl'].append(t['pnl_pct'])
    exit_reasons[reason]['total_pnl'] += t['pnl_pct']

report.append("")
for reason, data in sorted(exit_reasons.items(), key=lambda x: -x[1]['count']):
    avg = sum(data['avg_pnl']) / len(data['avg_pnl'])
    report.append(f"  {reason:<12}: {data['count']:2d}次  平均收益{avg:+.2%}  累计贡献{data['total_pnl']:+.2%}")

# === 权益曲线 ===
report.append("")
report.append("=" * 90)
report.append("  权益曲线（关键节点）")
report.append("=" * 90)
report.append("")

for ep in equity_points:
    ret = (ep['equity'] - initial_capital) / initial_capital
    bar_len = int(ret * 1000)  # 放大1000倍
    bar = "+" * max(bar_len, 0) + "-" * max(-bar_len, 0)
    report.append(f"  {ep['date']}: ¥{ep['equity']:>12,.0f}  ({ret:+.2%})  {bar}")

# === 按周统计 ===
report.append("")
report.append("=" * 90)
report.append("  按周收益统计")
report.append("=" * 90)
report.append("")

# 按周汇总交易
from collections import defaultdict
weekly = defaultdict(list)
for t in trades:
    from datetime import datetime
    dt = datetime.strptime(t['entry_date'], '%Y-%m-%d')
    week_key = dt.strftime('%Y-W%W')
    weekly[week_key].append(t)

for week, week_trades in sorted(weekly.items()):
    week_pnl = sum(t['pnl_pct'] for t in week_trades)
    wins_w = sum(1 for t in week_trades if t['pnl_pct'] > 0)
    report.append(f"  {week}: {len(week_trades)}笔  胜{wins_w}负{len(week_trades)-wins_w}  周收益{week_pnl:+.2%}")
    for t in week_trades:
        report.append(f"    {t['ts_code']}: {t['pnl_pct']:+.2%} ({t['holding_days']}天)")

# === 最终汇总 ===
report.append("")
report.append("=" * 90)
report.append("  最终汇总")
report.append("=" * 90)
report.append("")
report.append(f"  总交易数:     {len(trades)}笔")
report.append(f"  胜率:         {len(wins)/len(trades):.1%} ({len(wins)}胜{len(losses)}负)")
if wins:
    report.append(f"  盈利平均:     {sum(t['pnl_pct'] for t in wins)/len(wins):+.2%}")
if losses:
    report.append(f"  亏损平均:     {sum(t['pnl_pct'] for t in losses)/len(losses):+.2%}")
if wins and losses:
    avg_win = sum(t['pnl_pct'] for t in wins)/len(wins)
    avg_loss = abs(sum(t['pnl_pct'] for t in losses)/len(losses))
    report.append(f"  盈亏比:       {avg_win/avg_loss:.2f}")
report.append(f"  平均持仓:     {sum(t['holding_days'] for t in trades)/len(trades):.1f}天")
report.append(f"  总收益率:     {cumulative_pnl/initial_capital:+.2%}")
report.append(f"  最大单笔盈利: {max(t['pnl_pct'] for t in trades):+.2%}")
report.append(f"  最大单笔亏损: {min(t['pnl_pct'] for t in trades):+.2%}")
report.append("")
report.append("=" * 90)

# 输出
result = "\n".join(report)
print(result)

with open('bt_detail_v6.txt', 'w', encoding='utf-8') as f:
    f.write(result)
print("\n报告已保存: bt_detail_v6.txt")
