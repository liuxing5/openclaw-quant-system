"""生成每日推荐报告 - HTML + 文本格式"""
import os
import json
from datetime import date, timedelta, datetime, timezone
import psycopg2
from psycopg2.extras import RealDictCursor
from jinja2 import Template
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

RUN_MODE = os.getenv('RUN_MODE', 'morning')

# 北京时间时区
BEIJING_TZ = timezone(timedelta(hours=8))


def get_beijing_date():
    """获取北京时间日期（解决 GitHub Actions UTC 时区问题）"""
    return datetime.now(BEIJING_TZ).date()


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=int(os.getenv('POSTGRES_PORT') or '5432'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        dbname=os.getenv('POSTGRES_DB'),
    )


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI 股票推荐 - {{ date }}</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #1a1a1a; margin-bottom: 10px; }
        .subtitle { color: #666; margin-bottom: 30px; }
        .card { background: white; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        .card h2 { color: #333; margin-bottom: 16px; border-bottom: 2px solid #4CAF50; padding-bottom: 8px; }
        .stock-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(350px, 1fr)); gap: 16px; }
        .stock-card { border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; transition: transform 0.2s; }
        .stock-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.15); }
        .stock-card.selected { border-color: #4CAF50; background: #f8fff8; }
        .stock-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
        .stock-code { font-size: 18px; font-weight: bold; color: #333; }
        .stock-name { color: #666; margin-left: 8px; }
        .score { font-size: 24px; font-weight: bold; color: #4CAF50; }
        .score-label { font-size: 12px; color: #999; }
        .metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 12px 0; }
        .metric { text-align: center; padding: 8px; background: #f5f5f5; border-radius: 4px; }
        .metric-value { font-size: 16px; font-weight: bold; color: #333; }
        .metric-label { font-size: 11px; color: #999; }
        .entry-info { margin: 12px 0; padding: 12px; background: #fafafa; border-radius: 4px; }
        .entry-row { display: flex; justify-content: space-between; margin: 4px 0; }
        .entry-label { color: #666; }
        .entry-value { font-weight: bold; }
        .sources { margin-top: 12px; }
        .source-tag { display: inline-block; padding: 4px 8px; background: #e3f2fd; color: #1976D2; border-radius: 4px; margin: 2px; font-size: 12px; }
        .logic-tag { display: inline-block; padding: 4px 8px; background: #fff3e0; color: #F57C00; border-radius: 4px; margin: 2px; font-size: 12px; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: bold; }
        .badge-buy { background: #4CAF50; color: white; }
        .badge-watch { background: #FF9800; color: white; }
        .badge-strong { background: #2196F3; color: white; }
        .badge-llm { background: #2196F3; color: white; font-size: 11px; padding: 2px 6px; border-radius: 10px; margin-left: 4px; }
        .badge-8step { background: #9C27B0; color: white; font-size: 11px; padding: 2px 6px; border-radius: 10px; margin-left: 4px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #e0e0e0; }
        th { background: #f5f5f5; font-weight: 600; }
        .footer { text-align: center; color: #999; margin-top: 40px; padding: 20px; }
        .history-link { display: inline-block; margin: 10px 5px; padding: 8px 16px; background: #4CAF50; color: white; text-decoration: none; border-radius: 4px; }
        .history-link:hover { background: #45a049; }
        .history-nav { background: white; border-radius: 12px; padding: 16px 24px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); display: flex; align-items: center; gap: 12px; }
        .history-nav label { font-weight: 600; color: #333; white-space: nowrap; }
        .history-nav select { padding: 8px 16px; border: 2px solid #4CAF50; border-radius: 6px; font-size: 14px; color: #333; background: white; cursor: pointer; outline: none; }
        .history-nav select:hover { border-color: #45a049; }
        .history-nav select:focus { border-color: #2196F3; box-shadow: 0 0 0 3px rgba(33,150,243,0.2); }
    </style>
</head>
<body>
    <div class="container">
        <h1> AI 股票推荐系统</h1>
        <p class="subtitle">
            {% if run_mode == 'morning' %}
             盘前参考 | 报告日期: {{ date }} | 生成时间: {{ generated_at }}
            {% else %}
             盘后复盘 | 报告日期: {{ date }} | 生成时间: {{ generated_at }}
            {% endif %}
        </p>
        
        <div class="history-nav">
            <label>📅 历史报告:</label>
            <select onchange="if(this.value) window.location.href=this.value;">
                <option value="">选择日期跳转</option>
                {% for d in history_dates %}
                <option value="{{ d }}/index.html" {% if d == date %}selected{% endif %}>{{ d }}</option>
                {% endfor %}
            </select>
        </div>
        
        {% if candidates %}
        <div class="card">
            <h2>
                 LLM 多源策略候选 ({{ candidates|length }} 只)
                {% if llm_date %}<span style="font-size:13px;color:#888;margin-left:8px;">{{ llm_date }}</span>{% endif %}
            </h2>
            <div class="stock-grid">
                {% for c in candidates %}
                <div class="stock-card {% if c.selected %}selected{% endif %}">
                    <div class="stock-header">
                        <div>
                            <span class="stock-code">{{ c.ts_code }}</span>
                            <span class="stock-name">{{ c.stock_name }}</span>
                            <span class="badge-llm">🤖 LLM</span>
                            {% if c.selected %}<span class="badge badge-strong">✓ 选中</span>{% endif %}
                        </div>
                        <div style="text-align: right;">
                            <div class="score">{{ "%.1f"|format(c.final_score) }}</div>
                            <div class="score-label">综合分</div>
                        </div>
                    </div>
                    
                    <div class="metrics">
                        <div class="metric">
                            <div class="metric-value">{{ "%.0f"|format(c.llm_score) }}</div>
                            <div class="metric-label">LLM 分</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value">{{ "%.0f"|format(c.quant_score) }}</div>
                            <div class="metric-label">量化分</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value">{{ "%.2f"|format(c.consensus_score) }}</div>
                            <div class="metric-label">共识度</div>
                        </div>
                    </div>
                    
                    <div class="entry-info">
                        <div class="entry-row">
                            <span class="entry-label">入场区间</span>
                            <span class="entry-value">{{ c.entry_low or '—' }} - {{ c.entry_high or '—' }}</span>
                        </div>
                        <div class="entry-row">
                            <span class="entry-label">止损</span>
                            <span class="entry-value" style="color: #f44336;">{{ c.stop_loss or '—' }}</span>
                        </div>
                        <div class="entry-row">
                            <span class="entry-label">目标</span>
                            <span class="entry-value" style="color: #4CAF50;">{{ c.target_1 or '—' }} / {{ c.target_2 or '—' }}</span>
                        </div>
                        {% if c.position_pct %}
                        <div class="entry-row">
                            <span class="entry-label">建议仓位</span>
                            <span class="entry-value">{{ "%.0f"|format(c.position_pct * 100) }}%</span>
                        </div>
                        {% endif %}
                    </div>
                    
                    <div class="sources">
                        {% for tag in c.logic_tags %}
                        <span class="logic-tag">{{ tag }}</span>
                        {% endfor %}
                        <div style="margin-top: 8px; font-size: 12px; color: #666;">
                            来源: {{ c.source_count }} 个 | 提及: {{ c.mention_count }} 次
                        </div>
                        {% for s in c.sources_detail %}
                        <span class="source-tag">{{ s.source }} ({{ s.tier }})</span>
                        {% endfor %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        {% endif %}
        
        {% if step_date %}
        <div class="card">
            <h2>🔮 八步法候选{% if eight_step_picks %} ({{ eight_step_picks|length }} 只){% endif %}<span style="font-size:13px;color:#888;margin-left:8px;">{{ step_date }}</span></h2>
            {% if eight_step_picks %}
            <div class="stock-grid">
                {% for c in eight_step_picks %}
                <div class="stock-card selected">
                    <div class="stock-header">
                        <div>
                            <span class="stock-code">{{ c.ts_code }}</span>
                            <span class="stock-name">{{ c.stock_name }}</span>
                            <span class="badge-8step">🔮 八步法</span>
                            <span class="badge badge-strong">✓ 选中</span>
                        </div>
                        <div style="text-align: right;">
                            <div class="score">{{ "%.1f"|format(c.final_score) }}</div>
                            <div class="score-label">综合分</div>
                        </div>
                    </div>
                    
                    <div class="metrics">
                        <div class="metric">
                            <div class="metric-value">{{ "%.0f"|format(c.llm_score) }}</div>
                            <div class="metric-label">LLM 加成</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value">{{ "%.0f"|format(c.quant_score) }}</div>
                            <div class="metric-label">量化分</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value">{{ "%.2f"|format(c.consensus_score) }}</div>
                            <div class="metric-label">共识度</div>
                        </div>
                    </div>
                    
                    <div class="entry-info">
                        <div class="entry-row">
                            <span class="entry-label">入场区间</span>
                            <span class="entry-value">{{ c.entry_low or '—' }} - {{ c.entry_high or '—' }}</span>
                        </div>
                        <div class="entry-row">
                            <span class="entry-label">止损</span>
                            <span class="entry-value" style="color: #f44336;">{{ c.stop_loss or '—' }}</span>
                        </div>
                        <div class="entry-row">
                            <span class="entry-label">目标</span>
                            <span class="entry-value" style="color: #4CAF50;">{{ c.target_1 or '—' }} / {{ c.target_2 or '—' }}</span>
                        </div>
                        {% if c.position_pct %}
                        <div class="entry-row">
                            <span class="entry-label">建议仓位</span>
                            <span class="entry-value">{{ "%.0f"|format(c.position_pct * 100) }}%</span>
                        </div>
                        {% endif %}
                    </div>
                    
                    <div class="sources">
                        {% for tag in c.logic_tags %}
                        <span class="logic-tag">{{ tag }}</span>
                        {% endfor %}
                    </div>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <p style="color: #888; text-align: center; padding: 20px;">今日八步法扫描无候选标的</p>
            {% endif %}
        </div>
        {% endif %}
        
        <div class="card">
            <h2>📡 数据源统计</h2>
            <table>
                <tr><th>数据源</th><th>信号数</th><th>平均置信度</th><th>平均强度</th></tr>
                {% for s in source_stats %}
                <tr>
                    <td>{{ s.name }}</td>
                    <td>{{ s.signal_count }}</td>
                    <td>{{ "%.2f"|format(s.avg_confidence or 0) }}</td>
                    <td>{{ "%.1f"|format(s.avg_strength or 0) }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
        
        <div class="card">
            <h2>📰 最新资讯</h2>
            <table>
                <tr><th>标题</th><th>来源</th><th>时间</th><th>链接</th></tr>
                {% for a in articles %}
                <tr>
                    <td>{{ a.title or '无标题' }}</td>
                    <td>{{ a.source_name }}</td>
                    <td>{{ a.pub_time }}</td>
                    <td>{% if a.url %}<a href="{{ a.url }}" target="_blank" style="color: #4CAF50;">查看原文</a>{% else %}—{% endif %}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
        
        <div class="footer">
            <p>AI Stock Recommendation System | LLM多源 + 八步法 | 每日 15:30 自动更新</p>
            <p>历史报告: 
                {% for d in history_dates %}
                <a href="{{ d }}/index.html" class="history-link">{{ d }}</a>
                {% endfor %}
            </p>
        </div>
    </div>
</body>
</html>"""


def _get_report_snapshot_date(cur):
    cur.execute("""
        SELECT snapshot_date
        FROM daily_candidates
        WHERE source IN ('llm_multisource', 'overnight_8step')
        ORDER BY snapshot_date DESC
        LIMIT 1;
    """)
    row = cur.fetchone()
    if row:
        return row['snapshot_date']
    return get_beijing_date()


def _get_latest_source_date(cur, source, lookback_days=7):
    cur.execute("""
        SELECT MAX(snapshot_date) AS max_date
        FROM daily_candidates
        WHERE source = %s
          AND snapshot_date >= CURRENT_DATE - %s;
    """, (source, lookback_days))
    row = cur.fetchone()
    if row and row['max_date']:
        return row['max_date']
    return None


def generate_report():
    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    snapshot_date = _get_report_snapshot_date(cur)
    
    llm_date = _get_latest_source_date(cur, 'llm_multisource')
    step_date = _get_latest_source_date(cur, 'overnight_8step')
    
    if llm_date:
        cur.execute("""
            SELECT * FROM daily_candidates
            WHERE snapshot_date = %s AND source = 'llm_multisource' AND run_mode = %s
            ORDER BY final_score DESC;
        """, (llm_date, RUN_MODE))
        candidates = cur.fetchall()
    else:
        candidates = []
    
    if step_date:
        cur.execute("""
            SELECT * FROM daily_candidates
            WHERE snapshot_date = %s AND source = 'overnight_8step' AND selected = TRUE
            ORDER BY final_score DESC;
        """, (step_date,))
        eight_step_picks = cur.fetchall()
    else:
        eight_step_picks = []
    
    cur.execute("""
        SELECT source_name, COUNT(*) as signal_count, source_tier
        FROM raw_signals
        WHERE fetch_time >= %s
        GROUP BY source_name, source_tier
        ORDER BY signal_count DESC;
    """, (snapshot_date - timedelta(days=2),))
    source_stats = cur.fetchall()
    
    cur.execute("""
        SELECT title, url, pub_time, source_name
        FROM raw_signals
        WHERE pub_time IS NOT NULL
        ORDER BY pub_time DESC LIMIT 20;
    """)
    articles = cur.fetchall()
    
    cur.execute("""
        SELECT DISTINCT snapshot_date FROM daily_candidates
        WHERE source IN ('llm_multisource', 'overnight_8step')
        ORDER BY snapshot_date DESC LIMIT 10;
    """)
    history_dates = [str(r['snapshot_date']) for r in cur.fetchall()]
    
    cur.close(); conn.close()
    
    display_date = str(llm_date or step_date or snapshot_date)
    llm_date_str = str(llm_date) if llm_date else None
    step_date_str = str(step_date) if step_date else None
    
    template = Template(HTML_TEMPLATE)
    html = template.render(
        date=display_date,
        generated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        candidates=[dict(c) for c in candidates],
        eight_step_picks=[dict(c) for c in eight_step_picks],
        source_stats=[dict(s) for s in source_stats],
        articles=[dict(a) for a in articles],
        history_dates=history_dates,
        run_mode=RUN_MODE,
        llm_date=llm_date_str,
        step_date=step_date_str,
    )
    
    output_dir = os.path.join(BASE_DIR, 'docs', display_date)
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    
    latest_dir = os.path.join(BASE_DIR, 'docs', 'latest')
    os.makedirs(latest_dir, exist_ok=True)
    with open(os.path.join(latest_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"Report generated: {output_dir}/index.html")
    print(f"  LLM候选: {len(candidates)} 只 (snapshot_date={llm_date})")
    print(f"  八步法候选: {len(eight_step_picks)} 只 (snapshot_date={step_date})")


def generate_text_report():
    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    snapshot_date = _get_report_snapshot_date(cur)
    
    llm_date = _get_latest_source_date(cur, 'llm_multisource')
    step_date = _get_latest_source_date(cur, 'overnight_8step')
    
    if llm_date:
        cur.execute("""
            SELECT * FROM daily_candidates
            WHERE snapshot_date = %s AND source = 'llm_multisource' AND run_mode = %s
            ORDER BY final_score DESC;
        """, (llm_date, RUN_MODE))
        candidates = cur.fetchall()
    else:
        candidates = []
    
    if step_date:
        cur.execute("""
            SELECT * FROM daily_candidates
            WHERE snapshot_date = %s AND source = 'overnight_8step' AND selected = TRUE
            ORDER BY final_score DESC;
        """, (step_date,))
        eight_step_picks = cur.fetchall()
    else:
        eight_step_picks = []
    
    cur.execute("""
        SELECT source_name, COUNT(*) as signal_count
        FROM raw_signals
        WHERE fetch_time >= %s
        GROUP BY source_name
        ORDER BY signal_count DESC;
    """, (snapshot_date - timedelta(days=2),))
    source_stats = cur.fetchall()
    
    cur.close(); conn.close()
    
    mode_text = "盘后复盘" if RUN_MODE == 'afternoon' else "盘前参考"
    display_date = str(llm_date or step_date or snapshot_date)
    lines = []
    lines.append(f"{display_date} {mode_text}")
    lines.append("")
    lines.append("数据采集开始：")
    
    for s in source_stats:
        lines.append(f"  {s['source_name']}: {s['signal_count']} 条")
    
    lines.append("")
    if candidates:
        lines.append(f"共 {len(candidates)} 只 LLM 候选 ({llm_date})")
    if eight_step_picks:
        lines.append(f"共 {len(eight_step_picks)} 只八步法候选 ({step_date})")
    lines.append("")
    
    if candidates:
        lines.append("🤖 LLM 多源策略:")
        for c in candidates:
            prefix = "🎯 " if c.get('selected') else "  "
            lines.append(f"{prefix}{c['stock_name']} {c['ts_code']}")
            lines.append(f"  综合分: {c['final_score']:.1f} | LLM:{c['llm_score']:.0f} 量化:{c['quant_score']:.0f}")
            if c.get('entry_low') and c.get('entry_high'):
                lines.append(f"  入场: {c['entry_low']:.3f}-{c['entry_high']:.3f}  止损: {c['stop_loss']:.3f}")
            if c.get('target_1') and c.get('target_2'):
                lines.append(f"  目标: {c['target_1']:.3f}/{c['target_2']:.3f}  仓位: {c['position_pct']*100:.0f}%")
            if c.get('logic_tags'):
                tags = c['logic_tags'] if isinstance(c['logic_tags'], list) else []
                lines.append(f"  逻辑: {', '.join(tags)}")
            lines.append("")
    
    if eight_step_picks:
        lines.append("🔮 八步法候选:")
        for c in eight_step_picks:
            lines.append(f"🎯 {c['stock_name']} {c['ts_code']}")
            lines.append(f"  综合分: {c['final_score']:.1f} | LLM加成:{c['llm_score']:.0f} 量化:{c['quant_score']:.0f}")
            if c.get('entry_low') and c.get('entry_high'):
                lines.append(f"  入场: {c['entry_low']:.3f}-{c['entry_high']:.3f}  止损: {c['stop_loss']:.3f}")
            if c.get('target_1') and c.get('target_2'):
                lines.append(f"  目标: {c['target_1']:.3f}/{c['target_2']:.3f}  仓位: {c['position_pct']*100:.0f}%")
            if c.get('logic_tags'):
                tags = c['logic_tags'] if isinstance(c['logic_tags'], list) else []
                lines.append(f"  逻辑: {', '.join(tags)}")
            lines.append("")
    
    return "\n".join(lines)


if __name__ == '__main__':
    from datetime import datetime
    generate_report()
    print("\n" + "="*50 + "\n")
    print(generate_text_report())
