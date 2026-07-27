"""
每日荐股操作指导报告
=====================
功能: 根据最新推荐股票生成明日详细操作指导
数据来源: daily_candidates (最新筛选结果)
买入规则: 次日开盘价买入
止盈止损: 基于回测最优参数 (+11%止盈 / -8%止损)
输出: HTML操作指导报告

使用场景:
  - 每日下午收市后运行
  - 获取今日推荐股票
  - 生成明日操作指导（买入时间、价格、止盈止损）
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.utils.env import load_project_env
from core.db.connection import get_db_fresh
from core.utils.trading_calendar import is_trading_day, get_next_trading_day

load_project_env()

PROFIT_PCT = 11.0
STOP_PCT = 8.0
MAX_HOLD_DAYS = 9
POS_SIZE = 0.95
MAX_CONCURRENT = 3
SCORE_THRESHOLD = 30

RECOMMEND_TIME_WINDOW = {
    'morning': '09:00-10:00',
    'afternoon': '14:30-15:00',
}


def fetch_latest_candidates(snapshot_date: date = None) -> List[Dict]:
    """获取最新推荐股票（已筛选）"""
    if snapshot_date is None:
        snapshot_date = date.today()
    
    conn = get_db_fresh()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT snapshot_date, ts_code, stock_name, final_score, 
               llm_score, quant_score, consensus_score,
               entry_low, entry_high, stop_loss, target_1, target_2,
               mention_count, source_diversity, logic_tags,
               selected, position_pct, run_mode, source, created_at
        FROM daily_candidates
        WHERE snapshot_date = %s
          AND selected = true
          AND final_score IS NOT NULL
          AND final_score >= %s
        ORDER BY final_score DESC
    """, (snapshot_date, SCORE_THRESHOLD))
    
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    result = []
    for row in rows:
        logic_tags = row[14] or []
        if isinstance(logic_tags, str):
            try:
                import json
                logic_tags = json.loads(logic_tags.replace("'", '"'))
            except:
                logic_tags = []
        
        result.append({
            'snapshot_date': row[0],
            'ts_code': row[1],
            'stock_name': row[2] or '',
            'final_score': row[3],
            'llm_score': float(row[4]) if row[4] else None,
            'quant_score': float(row[5]) if row[5] else None,
            'consensus_score': float(row[6]) if row[6] else None,
            'entry_low': float(row[7]) if row[7] else None,
            'entry_high': float(row[8]) if row[8] else None,
            'stop_loss': float(row[9]) if row[9] else None,
            'target_1': float(row[10]) if row[10] else None,
            'target_2': float(row[11]) if row[11] else None,
            'mention_count': row[12],
            'source_diversity': row[13],
            'logic_tags': logic_tags,
            'selected': row[15],
            'position_pct': float(row[16]) if row[16] else None,
            'run_mode': row[17],
            'source': row[18],
            'created_at': row[19],
        })
    
    return result


def fetch_latest_price(ts_code: str) -> Optional[Dict]:
    """获取股票最新收盘价"""
    conn = get_db_fresh()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT trade_date, open, high, low, close, pct_chg, turnover_rate
        FROM daily_quotes
        WHERE ts_code = %s
        ORDER BY trade_date DESC
        LIMIT 1
    """, (ts_code,))
    
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    if row:
        return {
            'trade_date': row[0],
            'open': float(row[1]) if row[1] else None,
            'high': float(row[2]) if row[2] else None,
            'low': float(row[3]) if row[3] else None,
            'close': float(row[4]) if row[4] else None,
            'pct_chg': float(row[5]) if row[5] else None,
            'turnover_rate': float(row[6]) if row[6] else None,
        }
    return None


def fetch_stock_info(ts_code: str) -> Optional[Dict]:
    """获取股票基本信息"""
    conn = get_db_fresh()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT stock_name, market, list_date, is_st, is_active
        FROM stock_basic_info
        WHERE ts_code = %s
    """, (ts_code,))
    
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    if row:
        return {
            'stock_name': row[0] or '',
            'market': row[1],
            'list_date': row[2],
            'is_st': bool(row[3]) if row[3] else False,
            'is_active': bool(row[4]) if row[4] else True,
        }
    return None


def generate_trade_guide(candidates: List[Dict], snapshot_date: date) -> List[Dict]:
    """生成交易操作指导"""
    next_trade_day = get_next_trading_day(snapshot_date)
    
    guide_list = []
    for i, candidate in enumerate(candidates[:MAX_CONCURRENT], 1):
        latest_price = fetch_latest_price(candidate['ts_code'])
        stock_info = fetch_stock_info(candidate['ts_code'])
        
        if latest_price and latest_price['close']:
            base_price = latest_price['close']
            
            entry_low = candidate['entry_low']
            entry_high = candidate['entry_high']
            db_stop_loss = candidate['stop_loss']
            db_target_1 = candidate['target_1']
            db_target_2 = candidate['target_2']
            
            if entry_low and entry_high:
                buy_price = (entry_low + entry_high) / 2
                buy_price_range = f"¥{entry_low:.2f} - ¥{entry_high:.2f}"
            elif entry_low:
                buy_price = entry_low
                buy_price_range = f"¥{entry_low:.2f}"
            elif entry_high:
                buy_price = entry_high
                buy_price_range = f"¥{entry_high:.2f}"
            else:
                buy_price = base_price
                buy_price_range = f"¥{base_price:.2f}"
            
            if db_target_1:
                take_profit_price = db_target_1
                take_profit_pct = f"+{((db_target_1 - buy_price) / buy_price * 100):.1f}%"
            else:
                take_profit_price = base_price * (1 + PROFIT_PCT / 100)
                take_profit_pct = f"+{PROFIT_PCT}%"
            
            if db_stop_loss:
                stop_loss_price = db_stop_loss
                stop_loss_pct = f"-{((buy_price - db_stop_loss) / buy_price * 100):.1f}%"
            else:
                stop_loss_price = base_price * (1 - STOP_PCT / 100)
                stop_loss_pct = f"-{STOP_PCT}%"
            
            position_ratio = candidate['position_pct'] or POS_SIZE
            priority = i
            
            guide = {
                'rank': priority,
                'ts_code': candidate['ts_code'],
                'stock_name': candidate['stock_name'] or (stock_info['stock_name'] if stock_info else ''),
                'latest_close': base_price,
                'latest_pct_chg': latest_price['pct_chg'],
                'latest_turnover': latest_price['turnover_rate'],
                'final_score': candidate['final_score'],
                'llm_score': candidate['llm_score'],
                'quant_score': candidate['quant_score'],
                'consensus_score': candidate['consensus_score'],
                'buy_date': next_trade_day,
                'buy_time': '09:25-09:30',
                'buy_price': buy_price,
                'buy_price_range': buy_price_range,
                'buy_price_strategy': '集合竞价/开盘价买入',
                'take_profit_price': take_profit_price,
                'take_profit_pct': take_profit_pct,
                'stop_loss_price': stop_loss_price,
                'stop_loss_pct': stop_loss_pct,
                'max_hold_days': MAX_HOLD_DAYS,
                'expiry_date': get_next_trading_day(next_trade_day, MAX_HOLD_DAYS),
                'position_ratio': position_ratio,
                'source': candidate['source'],
                'run_mode': candidate['run_mode'],
                'logic_tags': candidate['logic_tags'],
                'mention_count': candidate['mention_count'],
                'source_diversity': candidate['source_diversity'],
                'target_1': db_target_1,
                'target_2': db_target_2,
                'entry_low': entry_low,
                'entry_high': entry_high,
                'snapshot_date': candidate['snapshot_date'],
                'created_at': candidate['created_at'],
                'stock_info': stock_info,
            }
            guide_list.append(guide)
    
    return guide_list


def generate_html_report(guide_list: List[Dict], snapshot_date: date):
    """生成HTML操作指导报告"""
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    next_trade_day = guide_list[0]['buy_date'] if guide_list else get_next_trading_day(snapshot_date)
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>每日荐股操作指导报告</title>
    <script src="https://cdn.bootcdn.net/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background: #f0f2f5; color: #333; line-height: 1.6; }}
        .container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
        
        .header {{ background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); color: #fff; padding: 30px 40px; border-radius: 16px; margin-bottom: 24px; box-shadow: 0 8px 32px rgba(0,0,0,0.2); }}
        .header h1 {{ font-size: 32px; margin-bottom: 8px; }}
        .header .subtitle {{ font-size: 16px; opacity: 0.8; }}
        .header .time-info {{ display: flex; gap: 24px; margin-top: 16px; flex-wrap: wrap; }}
        .header .time-item {{ background: rgba(255,255,255,0.1); padding: 8px 20px; border-radius: 8px; font-size: 14px; }}
        .header .time-item strong {{ color: #fbbf24; }}
        
        .stats-bar {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }}
        .stat-card {{ background: #fff; padding: 16px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); text-align: center; }}
        .stat-card .label {{ font-size: 12px; color: #888; margin-bottom: 4px; }}
        .stat-card .value {{ font-size: 22px; font-weight: 700; color: #1e293b; }}
        
        .action-guide {{ background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%); border: 2px solid #f59e0b; border-radius: 12px; padding: 20px 24px; margin-bottom: 24px; }}
        .action-guide h3 {{ color: #d97706; margin-bottom: 12px; font-size: 18px; }}
        .action-guide .steps {{ display: flex; gap: 24px; flex-wrap: wrap; }}
        .action-guide .step {{ background: rgba(255,255,255,0.6); padding: 12px 20px; border-radius: 8px; font-size: 14px; }}
        .action-guide .step strong {{ color: #b45309; }}
        
        .stock-card {{ background: #fff; border-radius: 14px; padding: 24px; margin-bottom: 20px; box-shadow: 0 4px 16px rgba(0,0,0,0.08); border-left: 5px solid #6366f1; transition: transform 0.2s; }}
        .stock-card:hover {{ transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.12); }}
        .stock-card.top-rank {{ border-left-color: #f59e0b; }}
        .stock-card .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; }}
        .stock-card .rank-badge {{ background: #6366f1; color: #fff; padding: 6px 16px; border-radius: 20px; font-size: 14px; font-weight: 600; }}
        .stock-card.top-rank .rank-badge {{ background: #f59e0b; }}
        .stock-card .stock-info h2 {{ font-size: 24px; margin-bottom: 4px; }}
        .stock-card .stock-info .code {{ font-size: 14px; color: #888; }}
        
        .grid-3 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; }}
        .info-box {{ background: #f8fafc; border-radius: 10px; padding: 16px; }}
        .info-box .info-label {{ font-size: 12px; color: #64748b; margin-bottom: 6px; }}
        .info-box .info-value {{ font-size: 18px; font-weight: 600; }}
        .info-box .info-sub {{ font-size: 11px; color: #94a3b8; margin-top: 4px; }}
        
        .price-box {{ background: #f8fafc; border-radius: 10px; padding: 16px; }}
        .price-box.buy {{ border-left: 4px solid #22c55e; }}
        .price-box.take-profit {{ border-left: 4px solid #ef4444; }}
        .price-box.stop-loss {{ border-left: 4px solid #3b82f6; }}
        
        .price-label {{ font-size: 12px; color: #64748b; margin-bottom: 8px; }}
        .price-value {{ font-size: 28px; font-weight: 700; }}
        .price-sub {{ font-size: 13px; color: #94a3b8; margin-top: 4px; }}
        
        .table-section {{ background: #fff; border-radius: 12px; padding: 24px; margin-bottom: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
        .section-title {{ font-size: 18px; font-weight: 600; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 2px solid #e8e8e8; }}
        
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        thead th {{ background: #f1f5f9; padding: 12px 14px; text-align: left; font-weight: 600; color: #475569; border-bottom: 2px solid #e2e8f0; }}
        tbody td {{ padding: 12px 14px; border-bottom: 1px solid #f1f5f9; }}
        tbody tr:hover {{ background: #f8fafc; }}
        .text-green {{ color: #22c55e; }}
        .text-red {{ color: #ef4444; }}
        .text-blue {{ color: #3b82f6; }}
        .text-orange {{ color: #f59e0b; }}
        
        .tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }}
        .tag {{ background: #e0e7ff; color: #6366f1; padding: 4px 10px; border-radius: 12px; font-size: 11px; }}
        
        .footer {{ text-align: center; padding: 24px; color: #94a3b8; font-size: 13px; }}
        .footer a {{ color: #6366f1; text-decoration: none; }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>📊 每日荐股操作指导报告</h1>
        <div class="subtitle">基于回测最优策略 · 次日开盘价买入 · 固定+11%止盈/-8%止损</div>
        <div class="time-info">
            <div class="time-item">推荐日期: <strong>{snapshot_date}</strong></div>
            <div class="time-item">操作日期: <strong>{next_trade_day}</strong></div>
            <div class="time-item">买入时间: <strong>09:25-09:30（集合竞价）</strong></div>
            <div class="time-item">更新时间: <strong>{now_str}</strong></div>
            <div class="time-item">推荐数量: <strong>{len(guide_list)} 只</strong></div>
        </div>
    </div>
    
    <div class="stats-bar">
        <div class="stat-card">
            <div class="label">推荐股票</div>
            <div class="value">{len(guide_list)}只</div>
        </div>
        <div class="stat-card">
            <div class="label">止盈比例</div>
            <div class="value text-green">+{PROFIT_PCT}%</div>
        </div>
        <div class="stat-card">
            <div class="label">止损比例</div>
            <div class="value text-red">-{STOP_PCT}%</div>
        </div>
        <div class="stat-card">
            <div class="label">最大持仓</div>
            <div class="value">{MAX_HOLD_DAYS}天</div>
        </div>
        <div class="stat-card">
            <div class="label">单股仓位</div>
            <div class="value">{POS_SIZE*100}%</div>
        </div>
        <div class="stat-card">
            <div class="label">最大持仓数</div>
            <div class="value">{MAX_CONCURRENT}只</div>
        </div>
    </div>
    
    <div class="action-guide">
        <h3>📋 明日操作步骤</h3>
        <div class="steps">
            <div class="step"><strong>09:15-09:25</strong> 查看集合竞价情况</div>
            <div class="step"><strong>09:25-09:30</strong> 以开盘价下单买入</div>
            <div class="step"><strong>盘中</strong> 股价触及止盈自动卖出</div>
            <div class="step"><strong>盘中</strong> 股价触及止损果断卖出</div>
            <div class="step"><strong>T+{MAX_HOLD_DAYS}</strong> 未触发则到期卖出</div>
        </div>
    </div>
"""

    for guide in guide_list:
        is_top = guide['rank'] == 1
        card_class = 'stock-card top-rank' if is_top else 'stock-card'
        
        tags_html = ''.join([f'<span class="tag">{t}</span>' for t in (guide['logic_tags'][:5] if guide['logic_tags'] else [])])
        
        html += f"""    <div class="{card_class}">
        <div class="card-header">
            <div class="stock-info">
                <h2>{guide['stock_name']}</h2>
                <div class="code">{guide['ts_code']} | 综合评分: <strong>{guide['final_score']:.1f}</strong></div>
                {tags_html}
            </div>
            <div class="rank-badge">第{guide['rank']}推荐</div>
        </div>
        
        <div class="grid-3">
            <div class="price-box buy">
                <div class="price-label">💰 买入价</div>
                <div class="price-value text-green">¥{guide['buy_price']:.2f}</div>
                <div class="price-sub">{guide['buy_price_range']}</div>
            </div>
            
            <div class="price-box take-profit">
                <div class="price-label">🚀 止盈价</div>
                <div class="price-value text-red">¥{guide['take_profit_price']:.2f}</div>
                <div class="price-sub">涨幅 {guide['take_profit_pct']}</div>
            </div>
            
            <div class="price-box stop-loss">
                <div class="price-label">🛡️ 止损价</div>
                <div class="price-value text-blue">¥{guide['stop_loss_price']:.2f}</div>
                <div class="price-sub">跌幅 {guide['stop_loss_pct']}</div>
            </div>
            
            <div class="info-box">
                <div class="info-label">📅 买入日期</div>
                <div class="info-value">{guide['buy_date']}</div>
                <div class="info-sub">操作时间: {guide['buy_time']}</div>
            </div>
            
            <div class="info-box">
                <div class="info-label">🎯 买入策略</div>
                <div class="info-value">{guide['buy_price_strategy']}</div>
                <div class="info-sub">集合竞价下单</div>
            </div>
            
            <div class="info-box">
                <div class="info-label">⏱️ 持仓期限</div>
                <div class="info-value">最多{MAX_HOLD_DAYS}天</div>
                <div class="info-sub">到期日: {guide['expiry_date']}</div>
            </div>
            
            <div class="info-box">
                <div class="info-label">📊 仓位建议</div>
                <div class="info-value">{guide['position_ratio']*100:.0f}%</div>
                <div class="info-sub">单股仓位控制</div>
            </div>
            
            <div class="info-box">
                <div class="info-label">📈 最新收盘</div>
                <div class="info-value">¥{guide['latest_close']:.2f}</div>
                <div class="info-sub">涨跌幅: {('+' if guide['latest_pct_chg'] and guide['latest_pct_chg'] > 0 else '') + f'{guide["latest_pct_chg"]:.2f}%' if guide['latest_pct_chg'] else '-'}</div>
            </div>
            
            <div class="info-box">
                <div class="info-label">📉 换手率</div>
                <div class="info-value">{f'{guide["latest_turnover"]:.2f}%' if guide['latest_turnover'] else '-'}</div>
                <div class="info-sub">昨日换手率</div>
            </div>
            
            <div class="info-box">
                <div class="info-label">🔍 数据源</div>
                <div class="info-value">{guide['source']}</div>
                <div class="info-sub">提及{guide['mention_count']}次 · 来源{guide['source_diversity']}个</div>
            </div>
            
            <div class="info-box">
                <div class="info-label">🤖 LLM评分</div>
                <div class="info-value">{guide['llm_score']:.1f}</div>
                <div class="info-sub">AI综合评估</div>
            </div>
            
            <div class="info-box">
                <div class="info-label">📐 量化评分</div>
                <div class="info-value">{guide['quant_score']:.1f}</div>
                <div class="info-sub">技术面分析</div>
            </div>
            
            <div class="info-box">
                <div class="info-label">🤝 共识评分</div>
                <div class="info-value">{guide['consensus_score']:.1f}</div>
                <div class="info-sub">多源一致性</div>
            </div>
        </div>
    </div>
"""

    html += """    <div class="table-section">
        <div class="section-title">📊 操作指导汇总表</div>
        <table>
            <thead>
                <tr>
                    <th>排名</th>
                    <th>代码</th>
                    <th>名称</th>
                    <th>评分</th>
                    <th>买入价</th>
                    <th>买入时间</th>
                    <th>止盈价</th>
                    <th>止损价</th>
                    <th>持仓天数</th>
                    <th>仓位</th>
                    <th>来源</th>
                </tr>
            </thead>
            <tbody>"""

    for guide in guide_list:
        html += f"""
                <tr>
                    <td><strong>{guide['rank']}</strong></td>
                    <td>{guide['ts_code']}</td>
                    <td>{guide['stock_name']}</td>
                    <td>{guide['final_score']:.1f}</td>
                    <td><span class="text-green">¥{guide['buy_price']:.2f}</span></td>
                    <td>{guide['buy_date']} {guide['buy_time']}</td>
                    <td><span class="text-red">¥{guide['take_profit_price']:.2f}</span></td>
                    <td><span class="text-blue">¥{guide['stop_loss_price']:.2f}</span></td>
                    <td>{MAX_HOLD_DAYS}天</td>
                    <td>{guide['position_ratio']*100:.0f}%</td>
                    <td>{guide['source']}</td>
                </tr>"""

    html += """            </tbody>
        </table>
    </div>
    
    <div class="table-section">
        <div class="section-title">📋 策略参数说明</div>
        <table>
            <thead>
                <tr>
                    <th>参数</th>
                    <th>取值</th>
                    <th>说明</th>
                </tr>
            </thead>
            <tbody>
                <tr><td>买入时机</td><td>次日开盘价</td><td>推荐日T的下一个交易日T+1开盘买入</td></tr>
                <tr><td>止盈条件</td><td>+11%</td><td>股价达到买入价的+11%时自动卖出</td></tr>
                <tr><td>止损条件</td><td>-8%</td><td>股价下跌至买入价的-8%时止损卖出</td></tr>
                <tr><td>持仓期限</td><td>T+9</td><td>最长持有9个交易日，到期未触发则平仓</td></tr>
                <tr><td>单股仓位</td><td>95%</td><td>每只股票投入可用资金的95%</td></tr>
                <tr><td>最大持仓</td><td>3只</td><td>同一时间最多持有3只推荐股票</td></tr>
                <tr><td>评分门槛</td><td>≥30</td><td>仅推荐综合评分≥30分的股票</td></tr>
            </tbody>
        </table>
    </div>
    
    <div class="action-guide">
        <h3>💡 操作要点</h3>
        <div class="steps">
            <div class="step"><strong>集合竞价</strong> 关注开盘价是否符合预期，异常高开则谨慎</div>
            <div class="step"><strong>止盈优先</strong> 达到止盈价果断卖出，不贪心</div>
            <div class="step"><strong>止损坚决</strong> 触及止损价立即执行，控制风险</div>
            <div class="step"><strong>到期平仓</strong> 持有满9天无论盈亏都卖出，保持资金流动性</div>
            <div class="step"><strong>仓位管理</strong> 单只股票不超过可用资金的95%</div>
        </div>
    </div>
    
    <div class="footer">
        <p>数据来源: daily_candidates (LLM多源策略筛选)</p>
        <p>策略参数: 次日开盘价买入 | +11%止盈 | -8%止损 | T+9到期 | 单股95%仓位 | 最多3只持仓</p>
        <p>生成时间: {now_str} | © openclaw-quant-system</p>
    </div>
</div>
</body>
</html>"""

    return html


def main():
    print("=" * 80)
    print("每日荐股操作指导报告生成器")
    print("=" * 80)
    
    today = date.today()
    
    if not is_trading_day(today):
        print(f"⚠️ 今天 {today} 不是交易日，将使用最近一个交易日的数据")
        d = today
        while not is_trading_day(d):
            d -= timedelta(days=1)
        snapshot_date = d
    else:
        snapshot_date = today
    
    print(f"\n推荐日期: {snapshot_date}")
    
    print("\n正在获取推荐股票数据...")
    candidates = fetch_latest_candidates(snapshot_date)
    print(f"获取到 {len(candidates)} 只推荐股票")
    
    if not candidates:
        print(f"\n⚠️ {snapshot_date} 未找到推荐股票，尝试回退到最近有数据的日期")
        d = snapshot_date
        max_back_days = 10
        days_back = 0
        while not candidates and days_back < max_back_days:
            d -= timedelta(days=1)
            days_back += 1
            if is_trading_day(d):
                candidates = fetch_latest_candidates(d)
                print(f"  尝试 {d}: {len(candidates)} 只")
                if candidates:
                    snapshot_date = d
                    print(f"  ✅ 使用 {d} 的数据")
        
        if not candidates:
            print(f"\n⚠️ 最近{max_back_days}天都未找到推荐股票，生成空报告")
            guide_list = []
        else:
            print(f"\n使用 {snapshot_date} 的推荐数据")
            guide_list = generate_trade_guide(candidates, snapshot_date)
            print(f"生成了 {len(guide_list)} 条操作指导")
    else:
        print("\n正在生成操作指导...")
        guide_list = generate_trade_guide(candidates, snapshot_date)
        print(f"生成了 {len(guide_list)} 条操作指导")
    
    print("\n正在生成HTML报告...")
    html = generate_html_report(guide_list, snapshot_date)
    
    output_path = os.path.join(os.path.dirname(__file__), 'backtest', 'daily_trade_guide.html')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"\n✅ 报告已生成: {output_path}")
    print("\n" + "=" * 80)
    print("操作指导报告生成完成!")
    print("=" * 80)
    
    if guide_list:
        print("\n📊 明日操作清单:")
        for guide in guide_list:
            print(f"\n  {guide['rank']}. {guide['stock_name']} ({guide['ts_code']})")
            print(f"     买入价: ¥{guide['buy_price']:.2f}")
            print(f"     止盈价: ¥{guide['take_profit_price']:.2f} (+{PROFIT_PCT}%)")
            print(f"     止损价: ¥{guide['stop_loss_price']:.2f} (-{STOP_PCT}%)")
            print(f"     操作日期: {guide['buy_date']}")
            print(f"     评分: {guide['final_score']:.1f}")


if __name__ == '__main__':
    main()