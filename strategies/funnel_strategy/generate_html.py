"""
漏斗策略 HTML 报告生成器
=========================
将漏斗策略结果渲染为静态 HTML，用于 GitHub Pages 展示。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from dotenv import load_dotenv
for _env_path in [Path('.env'), Path('strategies/llm_multisource/.env')]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

from core.db.connection import get_db
from core.utils.env import load_project_env
import pandas as pd

load_project_env()


def query_df(sql, params=None):
    conn = get_db()
    df = pd.read_sql(sql, conn, params=params)
    conn.close()
    return df


def clean_nan(obj):
    """清理 NaN 值"""
    if isinstance(obj, dict):
        return {k: clean_nan(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_nan(item) for item in obj]
    elif isinstance(obj, float):
        if obj != obj:
            return None
        return obj
    return obj


def generate_funnel_html(trade_date: date = None, output_dir: str = None):
    """生成漏斗策略 HTML 报告"""
    if output_dir is None:
        output_dir = Path(__file__).parent / "docs"
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 查询最新漏斗结果
    if trade_date:
        df = query_df("""
            SELECT * FROM funnel_results
            WHERE trade_date = %s
            ORDER BY trade_date DESC LIMIT 1;
        """, (trade_date,))
    else:
        df = query_df("""
            SELECT * FROM funnel_results
            ORDER BY trade_date DESC LIMIT 1;
        """)

    if df.empty:
        print("  ⚠️ 数据库中无漏斗策略数据")
        return

    row = df.iloc[0]
    actual_date = row['trade_date']
    if hasattr(actual_date, 'strftime'):
        date_str = actual_date.strftime('%Y-%m-%d')
    else:
        date_str = str(actual_date)

    # 查询历史日期列表
    history_df = query_df("""
        SELECT trade_date FROM funnel_results
        ORDER BY trade_date DESC LIMIT 30;
    """)
    history_dates = []
    for _, hrow in history_df.iterrows():
        d = hrow['trade_date']
        if hasattr(d, 'strftime'):
            history_dates.append(d.strftime('%Y-%m-%d'))
        else:
            history_dates.append(str(d))

    candidates = row['candidates']
    if isinstance(candidates, str):
        candidates = json.loads(candidates)
    candidates = clean_nan(candidates)

    # 信号翻译
    SIGNAL_MAP = {
        'demand_absorption': '需求吸收（EMA12附近锤子/刺透+放量）',
        'strong_relay': '强势接力（昨日首板，今日回踩VWAP翘头）',
        'none': '无信号',
    }

    def translate_signal(sig):
        return SIGNAL_MAP.get(sig, sig)

    # 各层筛选规则
    LAYER_RULES = {
        'L0': '上涨家数≥2500 且 全A指数>20EMA → 满仓；否则半仓或休战',
        'L1': '剔除ST/退市/次新股；流动比率>1.2；负债率<65%；营收同比>0%',
        'L2': '20日均成交额>1亿；流通市值>20亿；换手率3~15%',
        'L3': '周线CLOSE>20MA；EMA12>26>50多头排列；股价>EMA12；上升平台/回踩支撑',
        'L4': '量比1.5~3.0；乖离率<6%；需求吸收K线 或 强势接力形态',
        'L5': '综合评分≥80；涨幅3~5%；贴MA5；分时平稳；人气榜加分',
        'L6': '14:30后买入；止损=入场价-1ATR；目标价=入场价+2ATR；盈亏比≥2:1',
    }

    # 构建漏斗数据
    total = row['total_stocks']
    funnel_steps = [
        {"name": "全市场初筛", "pass_count": total, "eliminated": 0, "icon": "📊", "color": "#e3f2fd", "rule": "A股全市场，不做任何过滤"},
        {"name": "L0 大盘风控", "pass_count": total, 
         "eliminated": 0, "note": "✅满仓" if row['layer0_pass'] else "️半仓", "icon": "️", "color": "#fff3e0",
         "rule": LAYER_RULES['L0']},
        {"name": "L1 硬性防雷", "pass_count": row['layer1_pass'], 
         "eliminated": total - row['layer1_pass'], "icon": "⚡", "color": "#fce4ec",
         "rule": LAYER_RULES['L1']},
        {"name": "L2 流动性", "pass_count": row['layer2_pass'], 
         "eliminated": row['layer1_pass'] - row['layer2_pass'], "icon": "💧", "color": "#f3e5f5",
         "rule": LAYER_RULES['L2']},
        {"name": "L3 趋势结构", "pass_count": row['layer3_pass'], 
         "eliminated": row['layer2_pass'] - row['layer3_pass'], "icon": "📈", "color": "#e8eaf6",
         "rule": LAYER_RULES['L3']},
        {"name": "L4 动能信号", "pass_count": row['layer4_pass'], 
         "eliminated": row['layer3_pass'] - row['layer4_pass'], "icon": "🚀", "color": "#e0f2f1",
         "rule": LAYER_RULES['L4']},
        {"name": "L5 人气精选", "pass_count": row['layer5_pass'], 
         "eliminated": row['layer4_pass'] - row['layer5_pass'], "icon": "🔥", "color": "#fff8e1",
         "rule": LAYER_RULES['L5']},
        {"name": "L6 刚性风控", "pass_count": row['layer6_pass'], 
         "eliminated": row['layer5_pass'] - row['layer6_pass'], "icon": "🎯", "color": "#e8f5e9",
         "rule": LAYER_RULES['L6']},
    ]

    # 仓位状态
    if row['layer0_pass']:
        position_status = "✅ 满仓"
        position_color = "#4caf50"
    else:
        position_status = "⚠️ 半仓"
        position_color = "#ff9800"

    # 生成历史日期选择器 HTML
    history_options = ""
    for hd in history_dates:
        selected = 'selected' if hd == date_str else ''
        history_options += f'<option value="{hd}" {selected}>{hd}</option>'

    # 生成候选股票卡片
    candidates_html = ""
    if candidates:
        for c in candidates:
            tags = c.get('tags', [])
            if isinstance(tags, list):
                tags_str = ', '.join(str(t) for t in tags[:3])
            else:
                tags_str = str(tags)

            signal = c.get('signal_type', c.get('signal', ''))
            signal_text = translate_signal(signal)
            score = c.get('score', 0)
            entry = c.get('entry_price', 0)
            stop = c.get('stop_loss', 0)
            target = c.get('target_price', 0)
            plr = c.get('profit_loss_ratio', 0)
            atr = c.get('atr', 0)
            llm_bonus = c.get('llm_bonus', 0)
            llm_details = c.get('llm_details', {})

            # LLM bonus info row
            llm_info_html = ''
            if llm_bonus > 0 and llm_details:
                detail_parts = []
                if llm_details.get('consensus'):
                    detail_parts.append(f'共识{llm_details["consensus"]:.0f}')
                if llm_details.get('final_score'):
                    detail_parts.append(f'LLM评{llm_details["final_score"]:.0f}')
                if llm_details.get('mention'):
                    detail_parts.append(f'{llm_details["mention"]}源提及')
                if llm_details.get('selected'):
                    detail_parts.append('LLM精选')
                if llm_details.get('concepts'):
                    concept_names = ', '.join(llm_details['concepts'][:3])
                    detail_parts.append(f'概念: {concept_names}')
                if detail_parts:
                    llm_info_html = f"""
                    <div class="detail-row">
                        <span class="label">LLM联动</span>
                        <span class="value llm-bonus">+{llm_bonus}分 ({'; '.join(detail_parts)})</span>
                    </div>"""

            candidates_html += f"""
            <div class="stock-card">
                <div class="stock-header">
                    <span class="stock-code">{c.get('ts_code', '')}</span>
                    <span class="stock-score">{score}</span>
                </div>
                <div class="stock-details">
                    <div class="detail-row">
                        <span class="label">入场价</span>
                        <span class="value">{entry:.2f}</span>
                    </div>
                    <div class="detail-row">
                        <span class="label">止损价</span>
                        <span class="value stop">{stop:.2f}</span>
                    </div>
                    <div class="detail-row">
                        <span class="label">目标价</span>
                        <span class="value target">{target:.2f}</span>
                    </div>
                    <div class="detail-row">
                        <span class="label">ATR</span>
                        <span class="value">{atr:.3f}</span>
                    </div>
                    <div class="detail-row">
                        <span class="label">盈亏比</span>
                        <span class="value">{plr:.1f}:1</span>
                    </div>
                    <div class="detail-row">
                        <span class="label">信号</span>
                        <span class="value signal">{signal_text}</span>
                    </div>{llm_info_html}
                </div>
                {f'<div class="stock-tags">{tags_str}</div>' if tags_str else ''}
            </div>
            """
    else:
        candidates_html = '<div class="no-candidates">今日无推荐</div>'

    # 生成漏斗步骤 HTML
    funnel_html = ""
    for step in funnel_steps:
        note_html = f'<span class="note-badge">{step["note"]}</span>' if step.get('note') else ''
        eliminated_html = f'<span class="eliminated-count">✗ 淘汰 {step["eliminated"]} 只</span>' if step.get('eliminated', 0) > 0 else ''
        is_first = step['name'] == '全市场初筛'
        if is_first and step.get('rule'):
            name_html = f'<span class="step-name">{step["name"]}</span><span class="step-rule-inline">{step["rule"]}</span>'
            rule_html = ''
        else:
            name_html = f'<div class="step-name">{step["name"]}</div>'
            rule_html = f'<div class="step-rule">{step.get("rule", "")}</div>' if step.get('rule') else ''
        funnel_html += f"""
        <div class="funnel-step" style="background: {step['color']}">
            <div class="step-icon">{step['icon']}</div>
            <div class="step-info">
                {name_html}
                <div class="step-counts">
                    <span class="pass-count">✓ {step['pass_count']} 只</span>
                    {note_html}
                    {eliminated_html}
                </div>
                {rule_html}
            </div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>漏斗策略 - {date_str}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f5f5;
            color: #333;
            line-height: 1.6;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }}
        header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px 20px;
            text-align: center;
            border-radius: 12px;
            margin-bottom: 20px;
        }}
        header h1 {{ font-size: 24px; margin-bottom: 10px; }}
        header .date-info {{ font-size: 14px; opacity: 0.9; }}
        .date-selector {{
            margin: 15px 0;
            text-align: center;
        }}
        .date-selector select {{
            padding: 8px 15px;
            border-radius: 6px;
            border: 1px solid #ddd;
            font-size: 14px;
            background: white;
            cursor: pointer;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }}
        .stat-card {{
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            text-align: center;
        }}
        .stat-card .stat-value {{
            font-size: 28px;
            font-weight: bold;
            color: #667eea;
        }}
        .stat-card .stat-label {{
            font-size: 12px;
            color: #666;
            margin-top: 5px;
        }}
        .section {{
            background: white;
            padding: 25px;
            border-radius: 10px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }}
        .section h2 {{
            font-size: 18px;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid #eee;
        }}
        .funnel-step {{
            display: flex;
            align-items: center;
            padding: 15px;
            margin-bottom: 8px;
            border-radius: 8px;
            transition: transform 0.2s;
        }}
        .funnel-step:hover {{ transform: translateX(5px); }}
        .step-icon {{
            font-size: 24px;
            margin-right: 15px;
        }}
        .step-info {{ flex: 1; }}
        .step-name {{
            font-weight: bold;
            font-size: 14px;
        }}
        .step-rule-inline {{
            font-size: 11px;
            color: #888;
            margin-left: 8px;
        }}
        .step-counts {{
            font-size: 12px;
            margin-top: 3px;
        }}
        .pass-count {{ color: #4caf50; margin-right: 10px; }}
        .eliminated-count {{ color: #f44336; }}
        .step-rule {{
            font-size: 11px;
            color: #555;
            margin-top: 6px;
            padding: 4px 8px;
            background: rgba(255,255,255,0.6);
            border-radius: 4px;
            line-height: 1.4;
        }}
        .candidates-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
        }}
        .stock-card {{
            background: #fafafa;
            border-radius: 10px;
            padding: 15px;
            border-left: 4px solid #667eea;
        }}
        .stock-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }}
        .stock-code {{
            font-weight: bold;
            font-size: 16px;
        }}
        .stock-score {{
            background: #667eea;
            color: white;
            padding: 4px 10px;
            border-radius: 15px;
            font-size: 14px;
        }}
        .stock-details {{ margin-bottom: 10px; }}
        .detail-row {{
            display: flex;
            justify-content: space-between;
            padding: 5px 0;
            font-size: 13px;
            border-bottom: 1px solid #eee;
        }}
        .detail-row .label {{ color: #666; }}
        .detail-row .value {{ font-weight: bold; }}
        .detail-row .value.stop {{ color: #f44336; }}
        .detail-row .value.target {{ color: #4caf50; }}
        .detail-row .value.signal {{ color: #ff9800; }}
        .detail-row .value.llm-bonus {{ color: #9c27b0; }}
        .stock-tags {{
            font-size: 12px;
            color: #666;
            background: #eee;
            padding: 5px 10px;
            border-radius: 5px;
            display: inline-block;
        }}
        .no-candidates {{
            text-align: center;
            padding: 40px;
            color: #999;
            font-size: 16px;
        }}
        .market-env {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 10px;
        }}
        .env-item {{
            text-align: center;
            padding: 10px;
            background: #f9f9f9;
            border-radius: 6px;
        }}
        .env-item .env-value {{
            font-size: 18px;
            font-weight: bold;
            color: #333;
        }}
        .env-item .env-label {{
            font-size: 11px;
            color: #666;
            margin-top: 3px;
        }}
        .position-badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 15px;
            color: white;
            font-weight: bold;
            font-size: 14px;
            background: {position_color};
        }}
        footer {{
            text-align: center;
            padding: 20px;
            color: #999;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🎯 七步漏斗选股策略</h1>
            <div class="date-info">报告日期: {date_str} | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
            <div class="date-selector">
                <select onchange="location.href='?date=' + this.value">
                    {history_options}
                </select>
            </div>
        </header>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{row['total_stocks']}</div>
                <div class="stat-label">全市场初筛</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{row['layer6_pass']}</div>
                <div class="stat-label">最终推荐</div>
            </div>
            <div class="stat-card">
                <div class="stat-value"><span class="position-badge">{position_status}</span></div>
                <div class="stat-label">仓位上限 {int(row['layer0_max_position']*100)}%</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{row['elapsed_seconds']:.1f}s</div>
                <div class="stat-label">总耗时</div>
            </div>
        </div>

        <div class="section">
            <h2>📊 市场环境</h2>
            <div class="market-env">
                <div class="env-item">
                    <div class="env-value">{row['market_advancers']}</div>
                    <div class="env-label">上涨家数</div>
                </div>
                <div class="env-item">
                    <div class="env-value">{row['market_decliners']}</div>
                    <div class="env-label">下跌家数</div>
                </div>
                <div class="env-item">
                    <div class="env-value">{row['market_index_close']}</div>
                    <div class="env-label">指数收盘</div>
                </div>
                <div class="env-item">
                    <div class="env-value">{row['market_index_ema']}</div>
                    <div class="env-label">指数EMA</div>
                </div>
            </div>
        </div>

        <div class="section">
            <h2>🔄 漏斗过滤过程</h2>
            {funnel_html}
        </div>

        <div class="section">
            <h2>🎯 最终推荐 ({row['layer6_pass']} 只)</h2>
            <div class="candidates-grid">
                {candidates_html}
            </div>
        </div>

        <footer>
            AI Stock Recommendation System | 漏斗策略每日 15:10 自动更新
        </footer>
    </div>
</body>
</html>"""

    output_file = output_dir / "funnel.html"
    output_file.write_text(html, encoding='utf-8')
    print(f"  ✓ 漏斗策略 HTML 已生成: {output_file}")

    # 同时生成 index.html 用于重定向
    index_file = output_dir / "index.html"
    index_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta http-equiv="refresh" content="0; url=funnel.html">
    <title>漏斗策略</title>
</head>
<body>
    <p>跳转到 <a href="funnel.html">漏斗策略页面</a></p>
</body>
</html>"""
    index_file.write_text(index_html, encoding='utf-8')

    return output_file


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="生成漏斗策略 HTML 报告")
    parser.add_argument("--date", "-d", type=str, default=None, help="交易日期 (YYYY-MM-DD)")
    parser.add_argument("--output", "-o", type=str, default=None, help="输出目录")
    args = parser.parse_args()

    trade_date = date.fromisoformat(args.date) if args.date else None
    generate_funnel_html(trade_date=trade_date, output_dir=args.output)
