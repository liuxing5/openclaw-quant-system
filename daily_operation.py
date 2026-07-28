"""
每日操作闭环系统
================
功能: 整合持仓检查、推荐生成、买卖记录的完整闭环

流程:
1. 检查现有持仓是否触发止盈/止损/到期 → 自动卖出
2. 获取今日推荐 → 根据可用仓位决定买入
3. 将买入计划记录到tracker → 等待次日执行
4. 生成今日操作清单报告

使用方法:
- 每天下午收市后运行: python daily_operation.py
- 报告将显示: 今日需卖出的持仓 + 明日需买入的股票
"""
from __future__ import annotations

import os
import sys
import json
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.utils.env import load_project_env
from core.db.connection import get_db_fresh
from core.utils.trading_calendar import is_trading_day, get_next_trading_day

load_project_env()

from position_manager import PositionManager
from daily_trade_guide import (
    fetch_latest_candidates, generate_trade_guide, generate_html_report,
    fetch_backtest_stats, fetch_open_positions
)

PROFIT_PCT = 10.0
STOP_PCT = 7.0
MAX_HOLD_DAYS = 10
POS_SIZE = 0.95
MAX_CONCURRENT = 1  # 单仓模式：与回测一致
SCORE_THRESHOLD = 30

TRACKER_PATH = os.path.join(os.path.dirname(__file__), 'position_tracker.json')


def generate_operation_report(pm: PositionManager, snapshot_date: date) -> str:
    """生成每日操作报告"""
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    next_trade_day = get_next_trading_day(snapshot_date)
    
    open_positions = pm.get_open_positions(snapshot_date)
    available_slots = pm.get_available_slots()
    summary = pm.get_account_summary()
    
    candidates = fetch_latest_candidates(snapshot_date)
    
    if not candidates:
        d = snapshot_date
        max_back_days = 10
        days_back = 0
        while not candidates and days_back < max_back_days:
            d -= timedelta(days=1)
            days_back += 1
            if is_trading_day(d):
                candidates = fetch_latest_candidates(d)
                if candidates:
                    snapshot_date = d
                    print(f"  使用 {d} 的数据")
    
    buy_candidates = candidates[:available_slots] if available_slots > 0 else []
    wait_candidates = candidates[available_slots:] if len(candidates) > available_slots else []
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>每日操作清单</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #f1f5f9; }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
        .header {{ background: linear-gradient(135deg, #6366f1, #8b5cf6); color: #fff; padding: 32px; border-radius: 16px; margin-bottom: 24px; }}
        .header h1 {{ font-size: 28px; margin-bottom: 8px; }}
        .header p {{ opacity: 0.9; font-size: 14px; }}
        
        .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .summary-card {{ background: #fff; padding: 20px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); text-align: center; }}
        .summary-card .label {{ font-size: 13px; color: #64748b; margin-bottom: 8px; }}
        .summary-card .value {{ font-size: 24px; font-weight: 700; }}
        
        .section {{ background: #fff; border-radius: 12px; padding: 24px; margin-bottom: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
        .section-title {{ font-size: 18px; font-weight: 600; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 2px solid #e8e8e8; display: flex; align-items: center; gap: 8px; }}
        
        .position-card {{ background: #f8fafc; border-radius: 10px; padding: 20px; margin-bottom: 16px; border-left: 4px solid #3b82f6; }}
        .position-card.sell {{ border-left-color: #ef4444; }}
        .position-card h4 {{ margin-bottom: 12px; color: #1e293b; }}
        .price-row {{ display: flex; gap: 16px; margin-bottom: 8px; }}
        .price-item {{ flex: 1; }}
        .price-item .label {{ font-size: 12px; color: #64748b; }}
        .price-item .value {{ font-size: 18px; font-weight: 600; }}
        .tag {{ display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; margin-left: 8px; }}
        .tag-sell {{ background: #fef2f2; color: #dc2626; }}
        .tag-buy {{ background: #f0fdf4; color: #16a34a; }}
        .tag-wait {{ background: #f1f5f9; color: #64748b; }}
        
        .action-banner {{ padding: 20px; border-radius: 10px; margin-bottom: 20px; text-align: center; font-size: 18px; font-weight: 600; }}
        .banner-sell {{ background: #fef2f2; color: #dc2626; border: 2px solid #fecaca; }}
        .banner-buy {{ background: #f0fdf4; color: #16a34a; border: 2px solid #bbf7d0; }}
        .banner-wait {{ background: #fef3c7; color: #d97706; border: 2px solid #fde68a; }}
        
        .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
        @media (max-width: 768px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
        
        .footer {{ text-align: center; padding: 24px; color: #94a3b8; font-size: 13px; }}
        
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #f1f5f9; font-size: 13px; }}
        th {{ background: #f8fafc; font-weight: 600; color: #475569; }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>📋 每日操作清单</h1>
        <p>📅 日期: {snapshot_date} | ⏰ 生成时间: {now_str}</p>
        <p style="margin-top: 12px;">💰 初始资金: ¥{summary['initial_capital']:,.0f} | 💵 当前现金: ¥{summary['cash']:,.2f} | 📊 总盈亏: ¥{summary['total_profit']:,.2f}</p>
    </div>
    
    <div class="summary">
        <div class="summary-card">
            <div class="label">📦 当前持仓</div>
            <div class="value" style="color: #3b82f6;">{summary['open_positions_count']} / {MAX_CONCURRENT}只</div>
        </div>
        <div class="summary-card">
            <div class="label">🔓 可用仓位</div>
            <div class="value" style="color: #22c55e;">{summary['available_slots']}个</div>
        </div>
        <div class="summary-card">
            <div class="label">📈 历史胜率</div>
            <div class="value" style="color: #8b5cf6;">{summary['win_rate']}%</div>
        </div>
        <div class="summary-card">
            <div class="label">📊 历史平均收益</div>
            <div class="value" style="color: {'#22c55e' if summary['avg_profit_pct'] > 0 else '#ef4444'};">{summary['avg_profit_pct']:+.2f}%</div>
        </div>
    </div>
    """
    
    # 持仓状态
    if open_positions:
        html += """
    <div class="section">
        <div class="section-title">📦 当前持仓状态</div>
        <div class="grid-2">"""
        for pos in open_positions:
            html += f"""
            <div class="position-card">
                <h4>{pos['stock_name']} ({pos['ts_code']})</h4>
                <div class="price-row">
                    <div class="price-item">
                        <div class="label">买入价</div>
                        <div class="value" style="color: #22c55e;">¥{pos['buy_price']:.2f}</div>
                    </div>
                    <div class="price-item">
                        <div class="label">止盈价</div>
                        <div class="value" style="color: #ef4444;">¥{pos['target_1']:.2f}</div>
                    </div>
                    <div class="price-item">
                        <div class="label">止损价</div>
                        <div class="value" style="color: #3b82f6;">¥{pos['stop_loss']:.2f}</div>
                    </div>
                </div>
                <div style="color: #64748b; font-size: 13px;">
                    已持有: <strong>{pos['days_held']}</strong> 天 | 剩余: <strong>{pos['days_remaining']}</strong> 天 | 评分: <strong>{pos['final_score']:.1f}</strong>
                </div>
            </div>"""
        html += """
        </div>
    </div>"""
    else:
        html += """
    <div class="section">
        <div class="section-title">📦 当前持仓状态</div>
        <p style="color: #64748b; text-align: center; padding: 20px;">暂无持仓，全部仓位可用</p>
    </div>"""
    
    # 今日卖出提醒（根据当前价格判断）
    if open_positions:
        html += f"""
    <div class="section">
        <div class="section-title">🚨 今日卖出检查（{snapshot_date}收盘后检查）</div>
        <div class="action-banner banner-sell">
            ⚠️ 请检查以下持仓是否触发止盈/止损/到期
        </div>
        <div class="grid-2">"""
        
        for pos in open_positions:
            days_left = MAX_HOLD_DAYS - pos['days_held']
            html += f"""
            <div class="position-card sell">
                <h4>{pos['stock_name']} ({pos['ts_code']})</h4>
                <div class="price-row">
                    <div class="price-item">
                        <div class="label">买入价</div>
                        <div class="value">¥{pos['buy_price']:.2f}</div>
                    </div>
                    <div class="price-item">
                        <div class="label">止盈价</div>
                        <div class="value" style="color: #ef4444;">¥{pos['target_1']:.2f}</div>
                    </div>
                    <div class="price-item">
                        <div class="label">止损价</div>
                        <div class="value" style="color: #3b82f6;">¥{pos['stop_loss']:.2f}</div>
                    </div>
                </div>
                <div style="color: #dc2626; font-size: 13px; margin-top: 8px;">
                    {'📅 到期: 剩余 ' + str(days_left) + ' 天' if days_left > 0 else '🚨 今日到期！必须卖出'}
                </div>
                <div style="color: #64748b; font-size: 12px; margin-top: 4px;">
                    操作: 盘中监控，触及止盈/止损价立即卖出
                </div>
            </div>"""
        
        html += """
        </div>
    </div>"""
    
    # 明日买入计划
    html += f"""
    <div class="section">
        <div class="section-title">📅 明日买入计划（{next_trade_day}开盘执行）</div>"""
    
    if buy_candidates:
        html += f"""
        <div class="action-banner banner-buy">
            ✅ 明日开盘买入以下 {len(buy_candidates)} 只股票
        </div>
        <table>
            <thead>
                <tr>
                    <th>排名</th>
                    <th>股票</th>
                    <th>代码</th>
                    <th>评分</th>
                    <th>买入价（预计）</th>
                    <th>止盈价</th>
                    <th>止损价</th>
                    <th>操作</th>
                </tr>
            </thead>
            <tbody>"""
        
        for i, cand in enumerate(buy_candidates, 1):
            buy_price = cand.get('buy_price', cand.get('close', 0))
            target = round(buy_price * (1 + PROFIT_PCT / 100), 2)
            stop = round(buy_price * (1 - STOP_PCT / 100), 2)
            
            html += f"""
                <tr>
                    <td><strong>{i}</strong></td>
                    <td>{cand['stock_name']}</td>
                    <td>{cand['ts_code']}</td>
                    <td>{cand.get('final_score', 0):.1f}</td>
                    <td style="color: #22c55e;">¥{buy_price:.2f}</td>
                    <td style="color: #ef4444;">¥{target:.2f}</td>
                    <td style="color: #3b82f6;">¥{stop:.2f}</td>
                    <td><span class="tag tag-buy">✅ 买入</span></td>
                </tr>"""
        
        html += """
            </tbody>
        </table>
        <div style="margin-top: 16px; padding: 12px; background: #f0fdf4; border-radius: 8px; color: #16a34a; font-size: 13px;">
            💡 注意: 买入价为次日开盘价，若高开过多可考虑观望。单只仓位不超过可用资金的95%。
        </div>"""
    else:
        html += f"""
        <div class="action-banner banner-wait">
            ⏭️ 已满仓（{summary['open_positions_count']}/{MAX_CONCURRENT}只），明日无买入计划
        </div>
        <p style="color: #64748b; text-align: center; padding: 20px;">等待持仓中股票卖出释放仓位后，再买入新推荐</p>"""
    
    html += """
    </div>"""
    
    # 观望备选
    if wait_candidates:
        html += f"""
    <div class="section">
        <div class="section-title">⏭️ 观望备选（仓位释放后可考虑）</div>
        <table>
            <thead>
                <tr>
                    <th>排名</th>
                    <th>股票</th>
                    <th>代码</th>
                    <th>评分</th>
                    <th>状态</th>
                </tr>
            </thead>
            <tbody>"""
        
        for i, cand in enumerate(wait_candidates, len(buy_candidates) + 1):
            html += f"""
                <tr>
                    <td>{i}</td>
                    <td>{cand['stock_name']}</td>
                    <td>{cand['ts_code']}</td>
                    <td>{cand.get('final_score', 0):.1f}</td>
                    <td><span class="tag tag-wait">⏭️ 观望</span></td>
                </tr>"""
        
        html += """
            </tbody>
        </table>
    </div>"""
    
    # 操作要点
    html += f"""
    <div class="section">
        <div class="section-title">💡 操作要点</div>
        <div style="display: grid; gap: 12px; color: #475569;">
            <div style="padding: 12px; background: #f8fafc; border-radius: 8px;">
                <strong>1️⃣ 今日收盘后：</strong> 检查持仓中股票的收盘价，若触发止盈(+{PROFIT_PCT}%)/止损(-{STOP_PCT}%)/到期({MAX_HOLD_DAYS}天)，则次日开盘卖出
            </div>
            <div style="padding: 12px; background: #f8fafc; border-radius: 8px;">
                <strong>2️⃣ 明日开盘：</strong> 按清单买入排名前N只（N=可用仓位数）股票
            </div>
            <div style="padding: 12px; background: #f8fafc; border-radius: 8px;">
                <strong>3️⃣ 持仓期间：</strong> 盘中监控股价，触及止盈/止损价立即卖出；持有满{MAX_HOLD_DAYS}天必须卖出
            </div>
            <div style="padding: 12px; background: #f8fafc; border-radius: 8px;">
                <strong>4️⃣ 仓位管理：</strong> 单只股票不超过可用资金的{POS_SIZE*100}%，最多同时持有{MAX_CONCURRENT}只
            </div>
        </div>
    </div>
    
    <div class="footer">
        <p>📊 策略参数: 次日开盘买 | +{PROFIT_PCT}%止盈 | -{STOP_PCT}%止损 | T+{MAX_HOLD_DAYS}到期 | 95%仓位 | 最多{MAX_CONCURRENT}只持仓</p>
        <p>🎯 回测收益参考: 2026-01-05 ~ 2026-07-27 | +2.32% | 年化+4.2%</p>
        <p>⚠️ 风险提示: 本报告仅供参考，投资有风险，入市需谨慎</p>
    </div>
</div>
</body>
</html>"""
    
    return html


def main():
    print("=" * 80)
    print("📋 每日操作闭环系统")
    print("=" * 80)
    
    today = date.today()
    
    if not is_trading_day(today):
        print(f"⚠️ 今天 {today} 不是交易日，使用最近交易日数据")
        d = today
        while not is_trading_day(d):
            d -= timedelta(days=1)
        snapshot_date = d
    else:
        snapshot_date = today
    
    print(f"\n📅 推荐日期: {snapshot_date}")
    
    pm = PositionManager()
    
    print("\n🔍 检查持仓止盈/止损/到期...")
    sold = pm.update_positions(snapshot_date)
    if sold:
        print(f"  📤 今日卖出 {len(sold)} 只:")
        for s in sold:
            print(f"    {s['stock_name']} - {s['sell_reason']} @ ¥{s['sell_price']:.2f}")
    else:
        print("  ✅ 无股票卖出")
    
    open_positions = pm.get_open_positions(snapshot_date)
    available_slots = pm.get_available_slots()
    summary = pm.get_account_summary()
    
    print(f"\n📦 当前持仓: {len(open_positions)} 只 / 最多 {MAX_CONCURRENT} 只")
    print(f"🔓 可用仓位: {available_slots} 个")
    print(f"💰 账户状态: 现金 ¥{summary['cash']:,.2f} | 总盈亏 ¥{summary['total_profit']:,.2f}")
    
    for pos in open_positions:
        print(f"  - {pos['stock_name']} ({pos['ts_code']}) | 买入价 ¥{pos['buy_price']:.2f} | 止盈 ¥{pos['target_1']:.2f} | 止损 ¥{pos['stop_loss']:.2f} | {pos['days_held']}/{MAX_HOLD_DAYS}天")
    
    print("\n📊 生成操作报告...")
    html = generate_operation_report(pm, snapshot_date)
    
    output_path = os.path.join(os.path.dirname(__file__), 'backtest', 'daily_operation.html')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"✅ 操作报告已生成: {output_path}")
    
    print("\n" + "=" * 80)
    print("📋 今日操作清单")
    print("=" * 80)
    
    if open_positions:
        print("\n🚨 持仓检查（今日收盘后）:")
        for pos in open_positions:
            days_left = MAX_HOLD_DAYS - pos['days_held']
            status = "🚨 今日到期" if days_left <= 0 else f"剩余 {days_left} 天"
            print(f"  - {pos['stock_name']}: 买入¥{pos['buy_price']:.2f} | 止盈¥{pos['target_1']:.2f} | 止损¥{pos['stop_loss']:.2f} | {status}")
    
    candidates = fetch_latest_candidates(snapshot_date)
    
    if not candidates:
        d = snapshot_date
        while not candidates and d > snapshot_date - timedelta(days=10):
            d -= timedelta(days=1)
            if is_trading_day(d):
                candidates = fetch_latest_candidates(d)
                if candidates:
                    break
    
    buy_list = candidates[:available_slots] if available_slots > 0 else []
    
    if buy_list:
        print(f"\n📅 明日买入计划（{get_next_trading_day(snapshot_date)}开盘）:")
        for i, cand in enumerate(buy_list, 1):
            print(f"  ✅ {i}. {cand['stock_name']} ({cand['ts_code']}) | 评分: {cand.get('final_score', 0):.1f}")
    else:
        print("\n⏭️ 已满仓，无买入计划")
    
    print("\n💡 操作要点:")
    print("  1. 收盘后检查持仓是否触发止盈/止损/到期")
    print("  2. 次日开盘按清单买入")
    print("  3. 持仓期间监控，触及止盈/止损立即卖出")
    
    pm.close()


if __name__ == '__main__':
    main()