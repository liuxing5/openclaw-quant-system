"""
滚动窗口回测分析器
==================
功能: 从每个交易日开始运行回测到数据末尾，验证策略的稳健性

核心逻辑:
1. 从每个交易日（或每隔N个交易日）作为起始点
2. 运行回测到数据末尾（终点统一）
3. 统计每个起始日的总收益、胜率、最大回撤等指标
4. 生成收益分布图，展示策略在不同起始日的表现分布

这可以证明：策略的统计优势不依赖于特定起始日
"""
from __future__ import annotations

import os
import sys
import json
import statistics
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.utils.env import load_project_env
from core.db.connection import get_db_fresh
from core.utils.trading_calendar import is_trading_day, get_next_trading_day

load_project_env()

from backtest_revenue import (
    simulate_portfolio, load_recommendations, load_trading_dates,
    load_quotes_batch, organize_quotes, validate_and_clean_quotes,
    INITIAL_CAPITAL, MAX_CONCURRENT, PROFIT_PCT, STOP_PCT, MAX_HOLD_DAYS, POSITION_PCT
)


def analyze_trades(trades: List[Dict]) -> Dict:
    """分析交易结果，计算统计指标"""
    if not trades:
        return {
            'total_trades': 0,
            'win_count': 0,
            'loss_count': 0,
            'win_rate': 0,
            'avg_return': 0,
            'max_return': 0,
            'min_return': 0,
            'total_return': 0,
            'profit_sum': 0,
            'loss_sum': 0,
            'avg_hold_days': 0,
        }
    
    wins = [t for t in trades if t.get('return_pct', 0) > 0]
    losses = [t for t in trades if t.get('return_pct', 0) <= 0]
    
    returns = [t.get('return_pct', 0) for t in trades]
    hold_days = [t.get('hold_days', 0) for t in trades]
    
    return {
        'total_trades': len(trades),
        'win_count': len(wins),
        'loss_count': len(losses),
        'win_rate': round(len(wins) / len(trades) * 100, 1),
        'avg_return': round(sum(returns) / len(returns), 2),
        'max_return': round(max(returns), 2),
        'min_return': round(min(returns), 2),
        'total_return': round(sum(returns), 2),
        'profit_sum': round(sum(t.get('return_pct', 0) for t in wins), 2),
        'loss_sum': round(sum(t.get('return_pct', 0) for t in losses), 2),
        'avg_hold_days': round(sum(hold_days) / len(hold_days), 1),
    }


def calculate_max_drawdown(daily_equity: List[Dict]) -> float:
    """计算最大回撤"""
    if not daily_equity:
        return 0
    
    max_dd = 0
    peak = INITIAL_CAPITAL
    for de in daily_equity:
        if de["total_equity"] > peak:
            peak = de["total_equity"]
        dd = (peak - de["total_equity"]) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def run_window_backtest(start_date: date, end_date: date, recs: List[Dict], trading_dates: List[date], quotes_by_stock: Dict) -> Tuple[Dict, List[Dict], List[Dict]]:
    """从指定日期窗口运行回测"""
    recs_window = [r for r in recs if start_date <= r["snapshot_date"] <= end_date]
    trading_dates_window = [d for d in trading_dates if d >= start_date and d <= end_date]
    
    if not recs_window:
        return {}, [], []
    
    daily_equity, skipped_count, executed_trades = simulate_portfolio(
        recs_window, trading_dates_window, quotes_by_stock
    )
    
    stats = analyze_trades(executed_trades)
    max_dd = calculate_max_drawdown(daily_equity)
    
    stats['max_drawdown'] = max_dd
    stats['days'] = len(trading_dates_window)
    
    return stats, executed_trades, daily_equity


def generate_report(all_results: List[Dict], overall_stats: Dict, first_rec_date: date, last_rec_date: date):
    """生成滚动窗口回测报告"""
    now_str = date.today().strftime('%Y-%m-%d %H:%M:%S')
    
    results_sorted = sorted(all_results, key=lambda x: x['start_date'])
    dates_json = json.dumps([r['start_date'] for r in results_sorted])
    returns_json = json.dumps([r['stats']['total_return'] for r in results_sorted])
    win_rates_json = json.dumps([r['stats']['win_rate'] for r in results_sorted])
    trade_counts_json = json.dumps([r['stats']['total_trades'] for r in results_sorted])
    drawdowns_json = json.dumps([r['stats']['max_drawdown'] for r in results_sorted])
    
    positive_count = sum(1 for r in all_results if r['stats']['total_return'] > 0)
    negative_count = len(all_results) - positive_count
    positive_ratio = round(positive_count / len(all_results) * 100, 1) if all_results else 0
    
    returns = [r['stats']['total_return'] for r in all_results if r['stats']['total_trades'] > 0]
    win_rates = [r['stats']['win_rate'] for r in all_results if r['stats']['total_trades'] > 0]
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>滚动窗口回测分析报告</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f1f5f9; }}
        .container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
        .header {{ background: linear-gradient(135deg, #6366f1, #8b5cf6); color: #fff; padding: 32px; border-radius: 16px; margin-bottom: 24px; }}
        .header h1 {{ font-size: 28px; margin-bottom: 8px; }}
        .header p {{ opacity: 0.9; font-size: 14px; }}
        .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .summary-card {{ background: #fff; padding: 20px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); text-align: center; }}
        .summary-card .label {{ font-size: 13px; color: #64748b; margin-bottom: 8px; }}
        .summary-card .value {{ font-size: 24px; font-weight: 700; }}
        .summary-card .value.text-green {{ color: #22c55e; }}
        .summary-card .value.text-red {{ color: #ef4444; }}
        .summary-card .value.text-blue {{ color: #3b82f6; }}
        .summary-card .value.text-purple {{ color: #8b5cf6; }}
        .table-section {{ background: #fff; border-radius: 12px; padding: 24px; margin-bottom: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
        .section-title {{ font-size: 18px; font-weight: 600; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 2px solid #e8e8e8; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        thead th {{ background: #f1f5f9; padding: 12px 14px; text-align: left; font-weight: 600; color: #475569; border-bottom: 2px solid #e2e8f0; }}
        tbody td {{ padding: 12px 14px; border-bottom: 1px solid #f1f5f9; }}
        tbody tr:hover {{ background: #f8fafc; }}
        .chart-section {{ background: #fff; border-radius: 12px; padding: 24px; margin-bottom: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
        .chart-container {{ height: 350px; }}
        .chart-container-sm {{ height: 280px; }}
        .footer {{ text-align: center; padding: 24px; color: #94a3b8; font-size: 13px; }}
        .distribution-box {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .dist-item {{ background: #f8fafc; padding: 16px; border-radius: 8px; text-align: center; }}
        .dist-item .label {{ font-size: 12px; color: #64748b; margin-bottom: 4px; }}
        .dist-item .value {{ font-size: 18px; font-weight: 600; }}
        .highlight-box {{ padding: 20px; background: #fffbeb; border-radius: 8px; border-left: 4px solid #f59e0b; margin-bottom: 24px; }}
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>📊 滚动窗口回测分析报告</h1>
        <p>从每个交易日开始运行回测到数据末尾 · 参数: 最多{MAX_CONCURRENT}只持仓 | +{PROFIT_PCT}%止盈 | -{STOP_PCT}%止损 | {MAX_HOLD_DAYS}天到期</p>
        <p style="margin-top: 16px; opacity: 0.8; font-size: 12px;">回测区间: {first_rec_date} ~ {last_rec_date} | 测试窗口数: {len(all_results)}个 | 生成时间: {now_str}</p>
    </div>
    
    <div class="summary">
        <div class="summary-card">
            <div class="label">测试窗口数</div>
            <div class="value text-blue">{len(all_results)}</div>
        </div>
        <div class="summary-card">
            <div class="label">正收益起始日</div>
            <div class="value text-green">{positive_count}/{len(all_results)}</div>
            <div style="font-size: 12px; color: #64748b; margin-top: 4px;">占比 {positive_ratio}%</div>
        </div>
        <div class="summary-card">
            <div class="label">平均胜率</div>
            <div class="value text-purple">{overall_stats['avg_win_rate']}%</div>
        </div>
        <div class="summary-card">
            <div class="label">收益中位数</div>
            <div class="value text-green">{overall_stats['median_return']:+.2f}%</div>
        </div>
        <div class="summary-card">
            <div class="label">平均收益</div>
            <div class="value text-green">{overall_stats['avg_return']:+.2f}%</div>
        </div>
        <div class="summary-card">
            <div class="label">平均最大回撤</div>
            <div class="value text-red">{overall_stats['avg_max_drawdown']}%</div>
        </div>
        <div class="summary-card">
            <div class="label">最佳收益</div>
            <div class="value text-green">{overall_stats['best_return']:+.2f}%</div>
        </div>
        <div class="summary-card">
            <div class="label">最差收益</div>
            <div class="value text-red">{overall_stats['worst_return']:+.2f}%</div>
        </div>
    </div>
    
    <div class="highlight-box">
        <h4 style="margin-bottom: 12px;">💡 核心结论</h4>
        <p style="color: #475569; line-height: 1.6;">
            在 {len(all_results)} 个不同起始日的回测中，{positive_ratio}% 的起始日获得了正收益，收益中位数为 {overall_stats['median_return']:+.2f}%。
            这表明策略在大多数情况下能够产生正收益，具有一定的稳健性。但需注意最差收益为 {overall_stats['worst_return']:+.2f}%，
            说明在某些市场环境下可能出现较大亏损。建议结合自身风险承受能力使用。
        </p>
    </div>
    
    <div class="table-section">
        <div class="section-title">📈 收益分布统计</div>
        <div class="distribution-box">
            <div class="dist-item">
                <div class="label">收益中位数</div>
                <div class="value" style="color: {'#22c55e' if overall_stats['median_return'] > 0 else '#ef4444'};">{overall_stats['median_return']:+.2f}%</div>
            </div>
            <div class="dist-item">
                <div class="label">25分位数</div>
                <div class="value" style="color: {'#22c55e' if overall_stats['p25'] > 0 else '#ef4444'};">{overall_stats['p25']:+.2f}%</div>
            </div>
            <div class="dist-item">
                <div class="label">75分位数</div>
                <div class="value" style="color: {'#22c55e' if overall_stats['p75'] > 0 else '#ef4444'};">{overall_stats['p75']:+.2f}%</div>
            </div>
            <div class="dist-item">
                <div class="label">最佳收益</div>
                <div class="value text-green">{overall_stats['best_return']:+.2f}%</div>
            </div>
            <div class="dist-item">
                <div class="label">最差收益</div>
                <div class="value text-red">{overall_stats['worst_return']:+.2f}%</div>
            </div>
            <div class="dist-item">
                <div class="label">标准差</div>
                <div class="value text-blue">{overall_stats['std_dev']:.2f}%</div>
            </div>
        </div>
    </div>
    
    <div class="chart-section">
        <div class="section-title">📊 各起始日收益走势图</div>
        <div class="chart-container"><canvas id="returnChart"></canvas></div>
    </div>
    
    <div class="chart-section">
        <div class="section-title">📊 收益分布直方图</div>
        <div class="chart-container"><canvas id="histogramChart"></canvas></div>
    </div>
    
    <div class="chart-section">
        <div class="section-title">📊 胜率分布</div>
        <div class="chart-container"><canvas id="winRateChart"></canvas></div>
    </div>
    
    <div class="table-section">
        <div class="section-title">📋 详细回测结果（按起始日期排序）</div>
        <div style="overflow-x: auto;">
            <table>
                <thead>
                    <tr>
                        <th>起始日期</th>
                        <th>交易笔数</th>
                        <th>胜率</th>
                        <th>平均收益</th>
                        <th>总收益</th>
                        <th>最大收益</th>
                        <th>最小收益</th>
                        <th>最大回撤</th>
                        <th>持仓天数</th>
                        <th>推荐数</th>
                    </tr>
                </thead>
                <tbody>"""
    
    for r in results_sorted:
        total_ret_color = '#22c55e' if r['stats']['total_return'] > 0 else '#ef4444'
        avg_ret_color = '#22c55e' if r['stats']['avg_return'] > 0 else '#ef4444'
        
        html += f"""
                <tr>
                    <td>{r['start_date']}</td>
                    <td>{r['stats']['total_trades']}</td>
                    <td>{r['stats']['win_rate']}%</td>
                    <td style="color:{avg_ret_color};">{r['stats']['avg_return']:+.2f}%</td>
                    <td style="color:{total_ret_color};font-weight:600;">{r['stats']['total_return']:+.2f}%</td>
                    <td>{r['stats']['max_return']:+.2f}%</td>
                    <td>{r['stats']['min_return']:+.2f}%</td>
                    <td style="color:#ef4444;">-{r['stats']['max_drawdown']}%</td>
                    <td>{r['stats']['avg_hold_days']}</td>
                    <td>{r['signal_count']}</td>
                </tr>"""
    
    html += """
                </tbody>
            </table>
        </div>
    </div>
    
    <div class="table-section">
        <div class="section-title">💡 策略稳健性说明</div>
        <div style="padding: 16px; background: #f0fdf4; border-radius: 8px; border-left: 4px solid #22c55e;">
            <h4 style="margin-bottom: 12px;">回测的统计学意义</h4>
            <ul style="padding-left: 20px; color: #475569; line-height: 1.8;">
                <li><strong>回测不是预测未来具体交易路径</strong>：回测展示的是一条特定路径（特定起始日、特定交易序列），而实盘交易从不同日期开始会走完全不同的路径。这是正常现象。</li>
                <li><strong>回测验证的是策略规则的有效性</strong>：胜率、平均收益、最大回撤等统计指标才是对未来的指导，而非具体的交易路径。</li>
                <li><strong>滚动窗口验证的意义</strong>：通过从每个交易日开始跑回测，可以验证策略在不同时间段的表现分布。如果大多数起始日都能获得正收益，说明策略具有稳健性。</li>
                <li><strong>风险提示</strong>：虽然平均收益为正，但存在最差收益的情况。建议控制仓位，不要把全部资金投入。</li>
            </ul>
        </div>
    </div>
    
    <div class="footer">
        <p>数据来源: daily_candidates | 参数: 最多{MAX_CONCURRENT}只持仓 | +{PROFIT_PCT}%止盈 | -{STOP_PCT}%止损 | {MAX_HOLD_DAYS}天到期 | 单股{POSITION_PCT*100}%仓位</p>
        <p>© openclaw-quant-system</p>
    </div>
</div>

<script>
var dates = {dates_json};
var returns = {returns_json};
var winRates = {win_rates_json};
var tradeCounts = {trade_counts_json};
var drawdowns = {drawdowns_json};

new Chart(document.getElementById('returnChart'), {{
    type: 'line',
    data: {{
        labels: dates,
        datasets: [{{
            label: '总收益',
            data: returns,
            borderColor: '#6366f1',
            backgroundColor: 'rgba(99,102,241,0.08)',
            fill: true,
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 5,
            tension: 0.2
        }}]
    }},
    options: {{
        responsive: true,
        maintainAspectRatio: false,
        interaction: {{ intersect: false, mode: 'index' }},
        plugins: {{
            legend: {{ position: 'top' }},
            tooltip: {{
                callbacks: {{
                    label: function(ctx) {{ return ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(2) + '%'; }}
                }}
            }}
        }},
        scales: {{
            y: {{
                title: {{ display: true, text: '总收益 (%)' }},
                grid: {{ color: '#f1f5f9' }}
            }},
            x: {{
                grid: {{ display: false }},
                ticks: {{ maxTicksLimit: 12 }}
            }}
        }}
    }}
}});

var histData = returns.reduce(function(acc, val) {{
    var bin = Math.floor(val / 10) * 10;
    acc[bin] = (acc[bin] || 0) + 1;
    return acc;
}}, {{}});
var histLabels = Object.keys(histData).map(Number).sort(function(a, b) {{ return a - b; }});
var histValues = histLabels.map(function(bin) {{ return histData[bin]; }});

new Chart(document.getElementById('histogramChart'), {{
    type: 'bar',
    data: {{
        labels: histLabels.map(function(v) {{ return v + '%~' + (v + 10) + '%'; }}),
        datasets: [{{
            label: '起始日数量',
            data: histValues,
            backgroundColor: function(context) {{
                var val = histLabels[context.dataIndex];
                return val >= 0 ? '#22c55e' : '#ef4444';
            }},
            borderWidth: 0
        }}]
    }},
    options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
            legend: {{ display: false }}
        }},
        scales: {{
            y: {{
                title: {{ display: true, text: '起始日数量' }},
                beginAtZero: true,
                grid: {{ color: '#f1f5f9' }}
            }},
            x: {{
                title: {{ display: true, text: '收益区间 (%)' }},
                grid: {{ display: false }}
            }}
        }}
    }}
}});

new Chart(document.getElementById('winRateChart'), {{
    type: 'line',
    data: {{
        labels: dates,
        datasets: [{{
            label: '胜率',
            data: winRates,
            borderColor: '#8b5cf6',
            backgroundColor: 'transparent',
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.2
        }}]
    }},
    options: {{
        responsive: true,
        maintainAspectRatio: false,
        interaction: {{ intersect: false, mode: 'index' }},
        plugins: {{
            legend: {{ position: 'top' }},
            tooltip: {{
                callbacks: {{
                    label: function(ctx) {{ return ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(1) + '%'; }}
                }}
            }}
        }},
        scales: {{
            y: {{
                title: {{ display: true, text: '胜率 (%)' }},
                min: 0,
                max: 100,
                grid: {{ color: '#f1f5f9' }}
            }},
            x: {{
                grid: {{ display: false }},
                ticks: {{ maxTicksLimit: 12 }}
            }}
        }}
    }}
}});
</script>
</body>
</html>"""
    
    output_path = os.path.join(os.path.dirname(__file__), 'backtest', 'backtest_multi_window.html')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"\n✅ 滚动窗口回测报告已生成: {output_path}")


def main():
    print("=" * 80)
    print("滚动窗口回测分析器")
    print("=" * 80)
    print("\n从每个交易日开始运行回测到数据末尾，验证策略的稳健性...")
    
    conn = get_db_fresh()
    
    print("\n📥 加载推荐数据...")
    recs = load_recommendations(conn)
    print(f"  推荐信号: {len(recs)}个")
    
    print("\n📥 加载交易日历...")
    trading_dates = load_trading_dates(conn)
    print(f"  交易日: {len(trading_dates)}天")
    
    ts_codes = set(r["ts_code"] for r in recs)
    min_date = min(r["snapshot_date"] for r in recs)
    max_date = max(r["snapshot_date"] for r in recs)
    
    print("\n📥 加载K线数据...")
    quotes = load_quotes_batch(conn, ts_codes, min_date, max_date)
    print(f"  加载了 {len(quotes)} 条K线记录")
    quotes_by_stock = organize_quotes(quotes)
    print(f"  整理后: {len(quotes_by_stock)} 只股票")
    validate_and_clean_quotes(quotes_by_stock)
    
    conn.close()
    
    if not recs or not trading_dates:
        print("❌ 数据不足，无法运行回测")
        return
    
    first_rec_date = min(r["snapshot_date"] for r in recs)
    last_rec_date = max(r["snapshot_date"] for r in recs)
    
    print(f"\n📅 数据时间范围: {first_rec_date} ~ {last_rec_date}")
    
    trading_dates_filtered = [d for d in trading_dates if d >= first_rec_date and d <= last_rec_date]
    
    step = 1
    start_dates = trading_dates_filtered[::step]
    
    print(f"\n📊 测试窗口: {len(start_dates)}个（每隔{step}个交易日）")
    print(f"  起始日期范围: {start_dates[0]} ~ {start_dates[-1]}")
    
    all_results = []
    total = len(start_dates)
    
    for i, start_date in enumerate(start_dates):
        if i % 10 == 0:
            print(f"\n  [{i+1}/{total}] 正在运行回测...")
        
        stats, trades, daily_equity = run_window_backtest(
            start_date, last_rec_date, recs, trading_dates, quotes_by_stock
        )
        
        recs_window = [r for r in recs if start_date <= r["snapshot_date"] <= last_rec_date]
        
        all_results.append({
            'start_date': str(start_date),
            'signal_count': len(recs_window),
            'stats': stats,
            'trades': trades,
            'daily_equity': daily_equity,
        })
    
    returns = [r['stats']['total_return'] for r in all_results if r['stats']['total_trades'] > 0]
    win_rates = [r['stats']['win_rate'] for r in all_results if r['stats']['total_trades'] > 0]
    drawdowns = [r['stats']['max_drawdown'] for r in all_results if r['stats']['total_trades'] > 0]
    
    positive_count = sum(1 for r in all_results if r['stats']['total_return'] > 0)
    negative_count = len(all_results) - positive_count
    
    overall_stats = {
        'avg_win_rate': round(sum(win_rates) / len(win_rates), 1) if win_rates else 0,
        'avg_return': round(sum(returns) / len(returns), 2) if returns else 0,
        'median_return': round(statistics.median(returns), 2) if returns else 0,
        'p25': round(statistics.quantiles(returns, n=4)[0], 2) if len(returns) >= 4 else 0,
        'p75': round(statistics.quantiles(returns, n=4)[2], 2) if len(returns) >= 4 else 0,
        'best_return': round(max(returns), 2) if returns else 0,
        'worst_return': round(min(returns), 2) if returns else 0,
        'std_dev': round(statistics.stdev(returns), 2) if len(returns) >= 2 else 0,
        'avg_max_drawdown': round(sum(drawdowns) / len(drawdowns), 2) if drawdowns else 0,
        'positive_count': positive_count,
        'negative_count': negative_count,
        'positive_ratio': round(positive_count / len(all_results) * 100, 1) if all_results else 0,
    }
    
    print(f"\n{'='*60}")
    print("📊 滚动窗口回测汇总")
    print("="*60)
    print(f"测试窗口数: {len(all_results)}")
    print(f"正收益起始日: {positive_count}/{len(all_results)} ({overall_stats['positive_ratio']}%)")
    print(f"平均胜率: {overall_stats['avg_win_rate']}%")
    print(f"收益中位数: {overall_stats['median_return']:+.2f}%")
    print(f"平均收益: {overall_stats['avg_return']:+.2f}%")
    print(f"25/75分位数: {overall_stats['p25']:+.2f}% / {overall_stats['p75']:+.2f}%")
    print(f"最佳/最差收益: {overall_stats['best_return']:+.2f}% / {overall_stats['worst_return']:+.2f}%")
    print(f"标准差: {overall_stats['std_dev']:.2f}%")
    print(f"平均最大回撤: {overall_stats['avg_max_drawdown']}%")
    
    generate_report(all_results, overall_stats, first_rec_date, last_rec_date)


if __name__ == '__main__':
    main()