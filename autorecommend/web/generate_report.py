"""生成每日推荐报告 - HTML"""
import os
import json
from datetime import date
import psycopg2
from psycopg2.extras import RealDictCursor
from jinja2 import Template
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(BASE_DIR, '.env'))


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'), user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'), dbname=os.getenv('POSTGRES_DB'),
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
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #e0e0e0; }
        th { background: #f5f5f5; font-weight: 600; }
        .footer { text-align: center; color: #999; margin-top: 40px; padding: 20px; }
        .history-link { display: inline-block; margin: 10px 5px; padding: 8px 16px; background: #4CAF50; color: white; text-decoration: none; border-radius: 4px; }
        .history-link:hover { background: #45a049; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 AI 股票推荐系统</h1>
        <p class="subtitle">报告日期: {{ date }} | 生成时间: {{ generated_at }}</p>
        
        <div class="card">
            <h2>🎯 今日候选池 ({{ candidates|length }} 只)</h2>
            <div class="stock-grid">
                {% for c in candidates %}
                <div class="stock-card {% if c.selected %}selected{% endif %}">
                    <div class="stock-header">
                        <div>
                            <span class="stock-code">{{ c.ts_code }}</span>
                            <span class="stock-name">{{ c.stock_name }}</span>
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
                <tr><th>标题</th><th>来源</th><th>时间</th></tr>
                {% for a in articles %}
                <tr>
                    <td><a href="{{ a.url }}" target="_blank">{{ a.title or '无标题' }}</a></td>
                    <td>{{ a.source_name }}</td>
                    <td>{{ a.pub_time }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
        
        <div class="footer">
            <p>AI Stock Recommendation System | 每日 15:30 自动更新</p>
            <p>历史报告: 
                {% for d in history_dates %}
                <a href="{{ d }}/index.html" class="history-link">{{ d }}</a>
                {% endfor %}
            </p>
        </div>
    </div>
</body>
</html>"""


def generate_report():
    today = date.today()
    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # 候选股
    cur.execute("""
        SELECT * FROM daily_candidates 
        WHERE snapshot_date = %s ORDER BY final_score DESC;
    """, (today,))
    candidates = cur.fetchall()
    
    # 数据源统计
    cur.execute("""
        SELECT s.name, COUNT(*) as signal_count, 
               AVG(e.confidence) as avg_confidence,
               AVG(e.strength) as avg_strength
        FROM feed_sources s
        LEFT JOIN extracted_recommendations e ON s.id = e.source_id
        GROUP BY s.name ORDER BY signal_count DESC;
    """)
    source_stats = cur.fetchall()
    
    # 最新资讯
    cur.execute("""
        SELECT r.title, r.url, r.pub_time, s.name as source_name
        FROM raw_signals r
        JOIN feed_sources s ON r.source_id = s.id
        ORDER BY r.fetch_time DESC LIMIT 20;
    """)
    articles = cur.fetchall()
    
    # 历史日期
    cur.execute("""
        SELECT DISTINCT snapshot_date FROM daily_candidates 
        ORDER BY snapshot_date DESC LIMIT 10;
    """)
    history_dates = [str(r['snapshot_date']) for r in cur.fetchall()]
    
    cur.close(); conn.close()
    
    # 渲染 HTML
    template = Template(HTML_TEMPLATE)
    html = template.render(
        date=str(today),
        generated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        candidates=[dict(c) for c in candidates],
        source_stats=[dict(s) for s in source_stats],
        articles=[dict(a) for a in articles],
        history_dates=history_dates,
    )
    
    # 保存到 docs/ 目录（GitHub Pages）
    output_dir = os.path.join(BASE_DIR, 'docs', str(today))
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    
    # 同时更新最新报告
    latest_dir = os.path.join(BASE_DIR, 'docs', 'latest')
    os.makedirs(latest_dir, exist_ok=True)
    with open(os.path.join(latest_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"Report generated: {output_dir}/index.html")


if __name__ == '__main__':
    from datetime import datetime
    generate_report()
