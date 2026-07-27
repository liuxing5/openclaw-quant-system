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
  - 显示当前模拟持仓状态和可用仓位
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

PROFIT_PCT = 11.0
STOP_PCT = 8.0
MAX_HOLD_DAYS = 9
POS_SIZE = 0.95
MAX_CONCURRENT = 3
SCORE_THRESHOLD = 30


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


def fetch_open_positions(snapshot_date: date) -> List[Dict]:
    """从持仓跟踪文件读取当前持仓状态
    
    关键逻辑：
    1. 从 position_tracker.json 读取用户的虚拟交易记录
    2. 返回当前仍在持仓中的股票
    3. 更新持仓天数（基于交易日历）
    """
    tracker_path = os.path.join(os.path.dirname(__file__), 'position_tracker.json')
    
    if not os.path.exists(tracker_path):
        return []
    
    try:
        with open(tracker_path, 'r', encoding='utf-8') as f:
            tracker = json.load(f)
        
        conn = get_db_fresh()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT trade_date FROM daily_quotes
            WHERE trade_date <= %s
            GROUP BY trade_date
            ORDER BY trade_date DESC
        """, (snapshot_date,))
        
        recent_dates = [r[0] for r in cur.fetchall()]
        cur.close()
        conn.close()
        
        positions = []
        for pos in tracker.get('open_positions', []):
            buy_date_str = pos.get('buy_date')
            if not buy_date_str:
                continue
            
            buy_date = datetime.strptime(buy_date_str, '%Y-%m-%d').date()
            days_since_buy = len([d for d in recent_dates if d > buy_date])
            
            positions.append({
                'ts_code': pos.get('ts_code'),
                'stock_name': pos.get('stock_name'),
                'rec_date': pos.get('rec_date'),
                'buy_date': buy_date,
                'buy_price': pos.get('buy_price'),
                'days_held': days_since_buy,
                'days_remaining': MAX_HOLD_DAYS - days_since_buy,
                'final_score': pos.get('final_score', 0),
                'source': pos.get('source'),
                'target_1': pos.get('target_1'),
                'stop_loss': pos.get('stop_loss'),
                'status': '持仓中',
            })
        
        return positions
    except Exception as e:
        print(f"  读取持仓失败: {e}")
        return []


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


def fetch_backtest_stats() -> Dict:
    """获取回测统计信息（用于指导报告）"""
    backtest_report_path = os.path.join(os.path.dirname(__file__), 'backtest', 'backtest_report.html')
    
    if not os.path.exists(backtest_report_path):
        return {}
    
    try:
        with open(backtest_report_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        import re
        
        total_trades_match = re.search(r'<div class="label">实盘交易</div>\s*<div class="value">(\d+)', content)
        win_rate_match = re.search(r'<div class="label">胜率</div>\s*<div class="value">([\d.]+)%', content)
        signal_count_match = re.search(r'信号总数:\s*(\d+)', content)
        
        if not total_trades_match or not win_rate_match:
            return {}
        
        total_trades = int(total_trades_match.group(1))
        win_rate = float(win_rate_match.group(1))
        signal_count = int(signal_count_match.group(1)) if signal_count_match else total_trades
        
        avg_return_match = re.search(r'<div class="label">平均每笔收益</div>\s*<div class="value[^>]*>([+\-]?[\d.]+)%', content)
        avg_return = float(avg_return_match.group(1)) if avg_return_match else 0.0
        
        profit_count = int(total_trades * win_rate / 100)
        loss_count = total_trades - profit_count
        
        return {
            'total_signals': signal_count,
            'win_rate': round(win_rate, 1),
            'avg_return': round(avg_return, 2),
            'profit_count': profit_count,
            'loss_count': loss_count,
            'profit_pct': PROFIT_PCT,
            'stop_pct': STOP_PCT,
            'max_hold_days': MAX_HOLD_DAYS,
            'max_concurrent': MAX_CONCURRENT,
        }
    except Exception as e:
        print(f"  读取回测统计失败: {e}")
        return {}


def generate_trade_guide(candidates: List[Dict], snapshot_date: date, available_slots: int = MAX_CONCURRENT, open_positions: List[Dict] = None) -> List[Dict]:
    """生成交易操作指导"""
    next_trade_day = get_next_trading_day(snapshot_date)
    open_positions = open_positions or []
    held_stocks = {pos['ts_code'] for pos in open_positions}
    
    guide_list = []
    for i, candidate in enumerate(candidates, 1):
        if candidate['ts_code'] in held_stocks:
            continue
        
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
            should_buy = i <= available_slots
            
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
                'should_buy': should_buy,
                'available_slots': available_slots,
                'action': '买入' if should_buy else '观望',
            }
            guide_list.append(guide)
    
    return guide_list


def generate_html_report(guide_list: List[Dict], snapshot_date: date, open_positions: List[Dict] = None, available_slots: int = 0, backtest_stats: Dict = None):
    """生成HTML操作指导报告"""
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    next_trade_day = guide_list[0]['buy_date'] if guide_list else get_next_trading_day(snapshot_date)
    open_positions = open_positions or []
    backtest_stats = backtest_stats or {}
    
    buy_list = [g for g in guide_list if g['should_buy']]
    wait_list = [g for g in guide_list if not g['should_buy']]
    
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
        .stat-card.buy .value {{ color: #22c55e; }}
        .stat-card.hold .value {{ color: #3b82f6; }}
        
        .position-section {{ background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%); border: 2px solid #3b82f6; border-radius: 12px; padding: 20px 24px; margin-bottom: 24px; }}
        .position-section h3 {{ color: #1d4ed8; margin-bottom: 12px; font-size: 18px; }}
        .position-item {{ background: rgba(255,255,255,0.7); padding: 12px 20px; border-radius: 8px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }}
        .position-item:last-child {{ margin-bottom: 0; }}
        
        .action-guide {{ background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%); border: 2px solid #f59e0b; border-radius: 12px; padding: 20px 24px; margin-bottom: 24px; }}
        .action-guide h3 {{ color: #d97706; margin-bottom: 12px; font-size: 18px; }}
        .action-guide .steps {{ display: flex; gap: 24px; flex-wrap: wrap; }}
        .action-guide .step {{ background: rgba(255,255,255,0.6); padding: 12px 20px; border-radius: 8px; font-size: 14px; }}
        
        .stock-card {{ background: #fff; border-radius: 14px; padding: 24px; margin-bottom: 20px; box-shadow: 0 4px 16px rgba(0,0,0,0.08); border-left: 5px solid #6366f1; transition: transform 0.2s; }}
        .stock-card:hover {{ transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.12); }}
        .stock-card.top-rank {{ border-left-color: #f59e0b; }}
        .stock-card.buy-action {{ border-left-color: #22c55e; }}
        .stock-card.wait-action {{ border-left-color: #94a3b8; }}
        .stock-card .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; }}
        .stock-card .rank-badge {{ background: #6366f1; color: #fff; padding: 6px 16px; border-radius: 20px; font-size: 14px; font-weight: 600; }}
        .stock-card.top-rank .rank-badge {{ background: #f59e0b; }}
        .stock-card.buy-action .rank-badge {{ background: #22c55e; }}
        .stock-card.wait-action .rank-badge {{ background: #94a3b8; }}
        .stock-card .action-badge {{ background: #22c55e; color: #fff; padding: 6px 16px; border-radius: 20px; font-size: 14px; font-weight: 600; }}
        .stock-card.wait-action .action-badge {{ background: #f1f5f9; color: #64748b; }}
        
        .grid-3 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; }}
        .info-box {{ background: #f8fafc; border-radius: 10px; padding: 16px; }}
        .info-box .info-label {{ font-size: 12px; color: #64748b; margin-bottom: 6px; }}
        .info-box .info-value {{ font-size: 18px; font-weight: 600; }}
        .info-box .info-sub {{ font-size: 11px; color: #94a3b8; margin-top: 4px; }}
        
        .price-box {{ background: #f8fafc; border-radius: 10px; padding: 16px; }}
        .price-box.buy {{ border-left: 4px solid #22c55e; }}
        .price-box.take-profit {{ border-left: 4px solid #ef4444; }}
        .price-box.stop-loss {{ border-left: 4px solid #3b82f6; }}
        
        .table-section {{ background: #fff; border-radius: 12px; padding: 24px; margin-bottom: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
        .section-title {{ font-size: 18px; font-weight: 600; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 2px solid #e8e8e8; }}
        
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        thead th {{ background: #f1f5f9; padding: 12px 14px; text-align: left; font-weight: 600; color: #475569; border-bottom: 2px solid #e2e8f0; }}
        tbody td {{ padding: 12px 14px; border-bottom: 1px solid #f1f5f9; }}
        
        .tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }}
        .tag {{ background: #e0e7ff; color: #6366f1; padding: 4px 10px; border-radius: 12px; font-size: 11px; }}
        
        .footer {{ text-align: center; padding: 24px; color: #94a3b8; font-size: 13px; }}
        .text-green {{ color: #22c55e; }}
        .text-red {{ color: #ef4444; }}
        .text-blue {{ color: #3b82f6; }}
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
            <div class="time-item">可用仓位: <strong>{available_slots} 个</strong></div>
        </div>
    </div>
    
    <div class="stats-bar" style="margin-bottom: 24px;">
        <div class="stat-card" style="background: #f0fdf4;">
            <div class="label">可用仓位</div>
            <div class="value text-green">{available_slots}/{MAX_CONCURRENT}个</div>
        </div>
        <div class="stat-card" style="background: #fef3c7;">
            <div class="label">当前持仓</div>
            <div class="value text-red">{len(open_positions)}只</div>
        </div>
        <div class="stat-card" style="background: #e0e7ff;">
            <div class="label">止盈比例</div>
            <div class="value text-green">+{PROFIT_PCT}%</div>
        </div>
        <div class="stat-card" style="background: #fecaca;">
            <div class="label">止损比例</div>
            <div class="value text-red">-{STOP_PCT}%</div>
        </div>
        <div class="stat-card" style="background: #f1f5f9;">
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
        <div class="stat-card hold">
            <div class="label">当前持仓</div>
            <div class="value">{len(open_positions)}只</div>
        </div>
        <div class="stat-card buy">
            <div class="label">可用仓位</div>
            <div class="value">{available_slots}个</div>
        </div>
    </div>
    
    {f"""    <div class="stats-bar">
        <div class="stat-card">
            <div class="label">回测信号总数</div>
            <div class="value">{backtest_stats['total_signals']}笔</div>
        </div>
        <div class="stat-card">
            <div class="label">回测胜率</div>
            <div class="value text-green">{backtest_stats['win_rate']}%</div>
        </div>
        <div class="stat-card">
            <div class="label">平均每笔收益</div>
            <div class="value {'text-green' if backtest_stats['avg_return'] > 0 else 'text-red'}">{backtest_stats['avg_return']:+.2f}%</div>
        </div>
        <div class="stat-card">
            <div class="label">盈利/亏损</div>
            <div class="value">{backtest_stats['profit_count']}/{backtest_stats['loss_count']}</div>
        </div>
    </div>""" if backtest_stats else ''}
    
    <div class="position-section">
        <h3>📦 当前持仓状态</h3>
        {'' if open_positions else '<p style="color:#64748b;">暂无持仓，全部仓位可用</p>'}
        {''.join([f'<div class="position-item"><div><strong>{pos["stock_name"]}</strong> ({pos["ts_code"]}) - 来源: {pos["source"]}</div><div style="color:#64748b;">已持有 {pos["days_held"]} 天 / 剩余 {pos["days_remaining"]} 天</div></div>' for pos in open_positions])}
    </div>
    
    <div class="action-guide">
        <h3>📋 明日操作步骤</h3>
        <div class="steps">
            <div class="step"><strong>09:15-09:25</strong> 查看集合竞价情况</div>
            <div class="step"><strong>09:25-09:30</strong> 以开盘价下单买入推荐股票</div>
            <div class="step"><strong>盘中</strong> 股价触及止盈自动卖出</div>
            <div class="step"><strong>盘中</strong> 股价触及止损果断卖出</div>
            <div class="step"><strong>T+{MAX_HOLD_DAYS}</strong> 未触发则到期卖出</div>
        </div>
    </div>
    
    {f'<h2 style="margin-bottom:16px;color:#22c55e;">✅ 明日买入 ({len(buy_list)}只)</h2>' if buy_list else ''}"""
    
    for guide in buy_list:
        is_top = guide['rank'] == 1
        card_class = 'stock-card top-rank buy-action' if is_top else 'stock-card buy-action'
        tags_html = ''.join([f'<span class="tag">{t}</span>' for t in (guide['logic_tags'][:5] if guide['logic_tags'] else [])])
        
        html += f"""    <div class="{card_class}">
        <div class="card-header">
            <div>
                <h2>{guide['stock_name']}</h2>
                <div style="color:#888;font-size:14px;">{guide['ts_code']} | 综合评分: <strong>{guide['final_score']:.1f}</strong></div>
                {tags_html}
            </div>
            <div style="display:flex;gap:10px;">
                <span class="rank-badge">第{guide['rank']}推荐</span>
                <span class="action-badge">✅ 买入</span>
            </div>
        </div>
        <div class="grid-3">
            <div class="price-box buy">
                <div style="font-size:12px;color:#64748b;margin-bottom:8px;">💰 买入价</div>
                <div style="font-size:28px;font-weight:700;color:#22c55e;">¥{guide['buy_price']:.2f}</div>
                <div style="font-size:13px;color:#94a3b8;">{guide['buy_price_range']}</div>
            </div>
            <div class="price-box take-profit">
                <div style="font-size:12px;color:#64748b;margin-bottom:8px;">🚀 止盈价</div>
                <div style="font-size:28px;font-weight:700;color:#ef4444;">¥{guide['take_profit_price']:.2f}</div>
                <div style="font-size:13px;color:#94a3b8;">涨幅 {guide['take_profit_pct']}</div>
            </div>
            <div class="price-box stop-loss">
                <div style="font-size:12px;color:#64748b;margin-bottom:8px;">🛡️ 止损价</div>
                <div style="font-size:28px;font-weight:700;color:#3b82f6;">¥{guide['stop_loss_price']:.2f}</div>
                <div style="font-size:13px;color:#94a3b8;">跌幅 {guide['stop_loss_pct']}</div>
            </div>
            <div class="info-box">
                <div class="info-label">📅 买入日期</div>
                <div class="info-value">{guide['buy_date']}</div>
                <div class="info-sub">操作时间: {guide['buy_time']}</div>
            </div>
            <div class="info-box">
                <div class="info-label">⏱️ 持仓期限</div>
                <div class="info-value">最多{MAX_HOLD_DAYS}天</div>
            </div>
            <div class="info-box">
                <div class="info-label">📊 仓位建议</div>
                <div class="info-value">{guide['position_ratio']*100:.0f}%</div>
            </div>
            <div class="info-box">
                <div class="info-label">📈 最新收盘</div>
                <div class="info-value">¥{guide['latest_close']:.2f}</div>
            </div>
            <div class="info-box">
                <div class="info-label">🔍 数据源</div>
                <div class="info-value">{guide['source']}</div>
            </div>
            <div class="info-box">
                <div class="info-label">🤖 LLM评分</div>
                <div class="info-value">{round(guide['llm_score'], 1) if guide['llm_score'] else '-'}</div>
            </div>
        </div>
    </div>"""
    
    html += f"""    {f'<h2 style="margin-bottom:16px;color:#94a3b8;margin-top:32px;">⏭️ 观望 ({len(wait_list)}只)</h2>' if wait_list else ''}"""
    
    for guide in wait_list:
        html += f"""    <div class="stock-card wait-action">
        <div class="card-header">
            <div>
                <h2>{guide['stock_name']}</h2>
                <div style="color:#888;font-size:14px;">{guide['ts_code']} | 综合评分: <strong>{guide['final_score']:.1f}</strong></div>
            </div>
            <div style="display:flex;gap:10px;">
                <span class="rank-badge">第{guide['rank']}推荐</span>
                <span class="action-badge">⏭️ 观望</span>
            </div>
        </div>
        <div style="color:#94a3b8;font-size:14px;margin-bottom:16px;">当前仓位已满（{MAX_CONCURRENT}只），此股票列为备选，等待仓位释放后可考虑买入。</div>
        <div class="grid-3">
            <div class="info-box">
                <div class="info-label">💰 参考买入价</div>
                <div class="info-value">¥{guide['buy_price']:.2f}</div>
            </div>
            <div class="info-box">
                <div class="info-label">🚀 参考止盈价</div>
                <div class="info-value">¥{guide['take_profit_price']:.2f}</div>
            </div>
            <div class="info-box">
                <div class="info-label">🛡️ 参考止损价</div>
                <div class="info-value">¥{guide['stop_loss_price']:.2f}</div>
            </div>
        </div>
    </div>"""
    
    html += f"""    <div class="table-section">
        <div class="section-title">📊 操作指导汇总表</div>
        <table>
            <thead><tr><th>排名</th><th>代码</th><th>名称</th><th>评分</th><th>买入价</th><th>止盈价</th><th>止损价</th><th>操作</th></tr></thead>
            <tbody>"""
    
    for guide in guide_list:
        action_icon = '✅买入' if guide['should_buy'] else '⏭️观望'
        action_color = '#22c55e' if guide['should_buy'] else '#94a3b8'
        html += f"""
            <tr>
                <td><strong>{guide['rank']}</strong></td>
                <td>{guide['ts_code']}</td>
                <td>{guide['stock_name']}</td>
                <td>{guide['final_score']:.1f}</td>
                <td><span style="color:#22c55e;">¥{guide['buy_price']:.2f}</span></td>
                <td><span style="color:#ef4444;">¥{guide['take_profit_price']:.2f}</span></td>
                <td><span style="color:#3b82f6;">¥{guide['stop_loss_price']:.2f}</span></td>
                <td><strong style="color:{action_color};">{action_icon}</strong></td>
            </tr>"""
    
    html += """            </tbody>
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
    
    <div class="table-section">
        <div class="section-title">🔄 交易流程说明</div>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px;">
            <div style="background: #f8fafc; padding: 20px; border-radius: 10px; border-left: 4px solid #6366f1;">
                <div style="font-size: 24px; margin-bottom: 12px;">📅</div>
                <h4 style="margin-bottom: 8px;">第一步：收市后查看推荐</h4>
                <p style="font-size: 14px; color: #64748b;">每天下午收市后运行脚本，获取今日推荐股票列表</p>
            </div>
            <div style="background: #f8fafc; padding: 20px; border-radius: 10px; border-left: 4px solid #22c55e;">
                <div style="font-size: 24px; margin-bottom: 12px;">💵</div>
                <h4 style="margin-bottom: 8px;">第二步：次日开盘买入</h4>
                <p style="font-size: 14px; color: #64748b;">按评分从高到低，买入排名前N只（N=可用仓位数），满仓则观望</p>
            </div>
            <div style="background: #f8fafc; padding: 20px; border-radius: 10px; border-left: 4px solid #3b82f6;">
                <div style="font-size: 24px; margin-bottom: 12px;">📈</div>
                <h4 style="margin-bottom: 8px;">第三步：持有期间监控</h4>
                <p style="font-size: 14px; color: #64748b;">盘中监控股价，触及+11%止盈或-8%止损则卖出</p>
            </div>
            <div style="background: #f8fafc; padding: 20px; border-radius: 10px; border-left: 4px solid #f59e0b;">
                <div style="font-size: 24px; margin-bottom: 12px;">⏰</div>
                <h4 style="margin-bottom: 8px;">第四步：到期卖出释放仓位</h4>
                <p style="font-size: 14px; color: #64748b;">持有满9天无论盈亏都卖出，释放仓位后可买入新推荐</p>
            </div>
        </div>
        <div style="margin-top: 20px; padding: 16px; background: #fffbeb; border-radius: 8px; border: 1px solid #fef3c7;">
            <strong style="color: #d97706;">❓ 常见疑问：</strong>
            <ul style="margin-top: 8px; padding-left: 20px; color: #64748b; font-size: 14px;">
                <li><strong>Q：每天都有推荐，是不是每天都要买？</strong></li>
                <li>A：不是！只有当有可用仓位（持仓<3只）时才买入排名前N的推荐，已满仓则观望</li>
                <li><strong>Q：明天买还是后天买？</strong></li>
                <li>A：今天的推荐 → 明天开盘买；明天的推荐 → 后天开盘买。按顺序执行即可</li>
                <li><strong>Q：推荐了4只但只能买3只怎么办？</strong></li>
                <li>A：只买评分最高的3只，第4只列为备选，等待仓位释放后可考虑</li>
            </ul>
        </div>
    </div>
    
    <div class="footer">
        <p>数据来源: daily_candidates (LLM多源策略筛选)</p>
        <p>策略参数: 次日开盘价买入 | +11%止盈 | -8%止损 | T+9到期 | 单股95%仓位 | 最多3只持仓 | 评分≥30分</p>
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
    
    pm = PositionManager()
    
    print("\n🔍 正在检查持仓中股票是否触发止盈/止损/到期...")
    sold_positions = pm.update_positions(snapshot_date)
    if sold_positions:
        print(f"  📤 今日卖出 {len(sold_positions)} 只股票:")
        for sold in sold_positions:
            profit_sign = '+' if sold['profit'] > 0 else ''
            print(f"    {sold['stock_name']} ({sold['sell_date']}) - {sold['sell_reason']}")
            print(f"      买入: ¥{sold['buy_price']:.2f} | 卖出: ¥{sold['sell_price']:.2f}")
            print(f"      盈亏: {profit_sign}¥{sold['profit']:.2f} ({sold['profit_pct']:+.2f}%)")
    else:
        print("  ✅ 无股票卖出")
    
    print("\n📦 正在获取当前持仓状态...")
    open_positions = pm.get_open_positions(snapshot_date)
    print(f"当前持仓: {len(open_positions)} 只 / 最多 {MAX_CONCURRENT} 只")
    for pos in open_positions:
        print(f"  {pos['stock_name']} ({pos['ts_code']}) - 持有{pos['days_held']}天, 剩余{pos['days_remaining']}天")
    
    available_slots = pm.get_available_slots()
    print(f"可用仓位: {available_slots} 个")
    
    print("\n正在获取回测统计信息...")
    backtest_stats = fetch_backtest_stats()
    if backtest_stats:
        print(f"  回测信号: {backtest_stats['total_signals']}笔 | 胜率: {backtest_stats['win_rate']}% | 平均收益: {backtest_stats['avg_return']:+.2f}%")
    else:
        print("  未获取到回测统计信息")
    
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
            guide_list = generate_trade_guide(candidates, snapshot_date, available_slots, open_positions)
            print(f"生成了 {len(guide_list)} 条操作指导")
    else:
        print("\n正在生成操作指导...")
        guide_list = generate_trade_guide(candidates, snapshot_date, available_slots, open_positions)
        print(f"生成了 {len(guide_list)} 条操作指导")
    
    buy_count = sum(1 for g in guide_list if g['should_buy'])
    wait_count = len(guide_list) - buy_count
    print(f"  ├── ✅ 明日买入: {buy_count} 只")
    print(f"  └── ⏭️ 观望等待: {wait_count} 只")
    
    print("\n正在生成HTML报告...")
    html = generate_html_report(guide_list, snapshot_date, open_positions, available_slots, backtest_stats)
    
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
            action = "✅ 买入" if guide.get('should_buy') else "⏭️ 观望"
            print(f"\n  {action} {guide['rank']}. {guide['stock_name']} ({guide['ts_code']})")
            print(f"     买入价: ¥{guide['buy_price']:.2f}")
            print(f"     止盈价: ¥{guide['take_profit_price']:.2f}")
            print(f"     止损价: ¥{guide['stop_loss_price']:.2f}")
            print(f"     评分: {guide['final_score']:.1f}")


if __name__ == '__main__':
    main()
