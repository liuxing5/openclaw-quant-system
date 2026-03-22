#!/usr/bin/env python3
"""
OrderBookSimulator性能监控仪表板
Web界面展示实时统计和监控指标
"""

import sys
import os
import json
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request
import warnings
warnings.filterwarnings('ignore')

# 添加quant_system路径
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

app = Flask(__name__)
app.config['SECRET_KEY'] = 'orderbook-monitor-dashboard-2026'
app.config['STATS_DIR'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'stats')

class DashboardManager:
    """仪表板数据管理器"""
    
    def __init__(self, stats_dir=None):
        if stats_dir is None:
            stats_dir = app.config['STATS_DIR']
        
        self.stats_dir = stats_dir
        os.makedirs(stats_dir, exist_ok=True)
        
        # 数据文件路径
        self.stats_file = os.path.join(stats_dir, 'orderbook_stats.json')
        self.detailed_file = os.path.join(stats_dir, 'orderbook_detailed.csv')
        self.report_file = os.path.join(stats_dir, 'orderbook_report.html')
    
    def load_stats_summary(self):
        """加载统计摘要"""
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, 'r') as f:
                    stats_data = json.load(f)
                
                # 计算关键指标
                total_calls = stats_data.get('total_calls', 0)
                buy_calls = stats_data.get('buy_calls', 0)
                sell_calls = stats_data.get('sell_calls', 0)
                
                # 执行成功率
                fully_executed = stats_data.get('fully_executed', 0)
                partially_executed = stats_data.get('partially_executed', 0)
                rejected = stats_data.get('rejected', 0)
                
                total_executed = fully_executed + partially_executed
                execution_rate = total_executed / total_calls * 100 if total_calls > 0 else 0
                
                # 平均冲击成本
                total_impact_cost = stats_data.get('total_impact_cost_bps', 0.0)
                avg_impact_cost = total_impact_cost / total_calls if total_calls > 0 else 0
                
                # 流动性分桶分布
                liquidity_buckets = stats_data.get('liquidity_bucket_counts', {})
                
                # 市场状态分布
                market_regimes = stats_data.get('market_regime_counts', {})
                
                # 顶部股票
                symbol_counts = stats_data.get('symbol_counts', {})
                top_symbols = dict(sorted(symbol_counts.items(), key=lambda x: x[1], reverse=True)[:10])
                
                return {
                    'total_calls': total_calls,
                    'buy_calls': buy_calls,
                    'sell_calls': sell_calls,
                    'buy_sell_ratio': buy_calls / total_calls if total_calls > 0 else 0,
                    'fully_executed': fully_executed,
                    'partially_executed': partially_executed,
                    'rejected': rejected,
                    'execution_rate': execution_rate,
                    'avg_impact_cost': avg_impact_cost,
                    'liquidity_buckets': liquidity_buckets,
                    'market_regimes': market_regimes,
                    'top_symbols': top_symbols,
                    'last_updated': stats_data.get('last_update', 'N/A'),
                    'start_time': stats_data.get('start_time', 'N/A'),
                    'data_available': True
                }
            else:
                return {
                    'data_available': False,
                    'message': '暂无统计数据'
                }
                
        except Exception as e:
            print(f"统计加载错误: {e}")
            return {
                'data_available': False,
                'message': f'数据加载错误: {str(e)}'
            }
    
    def load_detailed_records(self, limit=100):
        """加载详细交易记录"""
        try:
            if os.path.exists(self.detailed_file):
                df = pd.read_csv(self.detailed_file)
                
                # 转换时间戳
                if 'timestamp' in df.columns:
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    df = df.sort_values('timestamp', ascending=False)
                
                # 限制记录数
                df = df.head(limit)
                
                # 转换为字典列表
                records = []
                for _, row in df.iterrows():
                    record = row.to_dict()
                    
                    # 处理NaN值
                    for key, value in record.items():
                        if pd.isna(value):
                            record[key] = None
                    
                    records.append(record)
                
                return records
            else:
                return []
                
        except Exception as e:
            print(f"详细记录加载错误: {e}")
            return []
    
    def get_system_health(self):
        """获取系统健康状态"""
        try:
            # 检查文件存在性和新鲜度
            stats_fresh = False
            if os.path.exists(self.stats_file):
                mod_time = os.path.getmtime(self.stats_file)
                stats_age = datetime.now().timestamp() - mod_time
                stats_fresh = stats_age < 3600  # 1小时内更新
            
            detailed_fresh = False
            if os.path.exists(self.detailed_file):
                mod_time = os.path.getmtime(self.detailed_file)
                detailed_age = datetime.now().timestamp() - mod_time
                detailed_fresh = detailed_age < 3600
            
            return {
                'stats_file_exists': os.path.exists(self.stats_file),
                'stats_file_fresh': stats_fresh,
                'detailed_file_exists': os.path.exists(self.detailed_file),
                'detailed_file_fresh': detailed_fresh,
                'stats_dir': self.stats_dir,
                'current_time': datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                'error': str(e),
                'current_time': datetime.now().isoformat()
            }
    
    def generate_chart_data(self):
        """生成图表数据"""
        summary = self.load_stats_summary()
        
        if not summary.get('data_available', False):
            return {
                'execution_status': [],
                'liquidity_distribution': [],
                'market_regime_distribution': [],
                'impact_cost_distribution': []
            }
        
        # 执行状态分布
        execution_status = [
            {'name': '完全执行', 'value': summary.get('fully_executed', 0)},
            {'name': '部分执行', 'value': summary.get('partially_executed', 0)},
            {'name': '被拒绝', 'value': summary.get('rejected', 0)}
        ]
        
        # 流动性分桶分布
        liquidity_distribution = []
        for bucket, count in summary.get('liquidity_buckets', {}).items():
            liquidity_distribution.append({
                'bucket': f'桶{bucket}',
                'count': count
            })
        
        # 市场状态分布
        market_regime_distribution = []
        for regime, count in summary.get('market_regimes', {}).items():
            market_regime_distribution.append({
                'regime': regime,
                'count': count
            })
        
        # 模拟冲击成本分布（实际应用中应从详细记录计算）
        impact_cost_distribution = []
        if summary.get('total_calls', 0) > 0:
            # 生成模拟数据
            avg_cost = summary.get('avg_impact_cost', 10)
            import numpy as np
            np.random.seed(42)
            
            for i in range(10):
                impact_cost_distribution.append({
                    'range': f'{i*10}-{(i+1)*10} bp',
                    'count': int(np.random.exponential(avg_cost))
                })
        
        return {
            'execution_status': execution_status,
            'liquidity_distribution': liquidity_distribution,
            'market_regime_distribution': market_regime_distribution,
            'impact_cost_distribution': impact_cost_distribution
        }

# 初始化仪表板管理器
dashboard_manager = DashboardManager()

@app.route('/')
def index():
    """仪表板首页"""
    stats_summary = dashboard_manager.load_stats_summary()
    system_health = dashboard_manager.get_system_health()
    chart_data = dashboard_manager.generate_chart_data()
    
    return render_template('index.html',
                         stats=stats_summary,
                         health=system_health,
                         chart_data=chart_data)

@app.route('/api/stats')
def api_stats():
    """API: 获取统计摘要"""
    stats_summary = dashboard_manager.load_stats_summary()
    return jsonify(stats_summary)

@app.route('/api/records')
def api_records():
    """API: 获取详细记录"""
    limit = request.args.get('limit', default=50, type=int)
    records = dashboard_manager.load_detailed_records(limit=limit)
    return jsonify({'records': records, 'count': len(records)})

@app.route('/api/health')
def api_health():
    """API: 获取系统健康状态"""
    health = dashboard_manager.get_system_health()
    return jsonify(health)

@app.route('/api/charts')
def api_charts():
    """API: 获取图表数据"""
    chart_data = dashboard_manager.generate_chart_data()
    return jsonify(chart_data)

@app.route('/api/realtime')
def api_realtime():
    """API: 实时更新数据"""
    stats_summary = dashboard_manager.load_stats_summary()
    health = dashboard_manager.get_system_health()
    
    return jsonify({
        'timestamp': datetime.now().isoformat(),
        'total_calls': stats_summary.get('total_calls', 0),
        'execution_rate': stats_summary.get('execution_rate', 0),
        'avg_impact_cost': stats_summary.get('avg_impact_cost', 0),
        'stats_fresh': health.get('stats_file_fresh', False),
        'data_available': stats_summary.get('data_available', False)
    })

@app.route('/admin/refresh')
def admin_refresh():
    """管理员: 手动刷新数据"""
    # 这里可以添加数据刷新逻辑
    return jsonify({
        'status': 'success',
        'message': '数据刷新请求已接收',
        'timestamp': datetime.now().isoformat()
    })

if __name__ == '__main__':
    # 确保模板目录存在
    templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    os.makedirs(templates_dir, exist_ok=True)
    
    # 创建默认模板（如果不存在）
    template_file = os.path.join(templates_dir, 'index.html')
    if not os.path.exists(template_file):
        create_default_template(template_file)
    
    print("=" * 70)
    print("OrderBookSimulator性能监控仪表板")
    print("=" * 70)
    print(f"仪表板URL: http://localhost:5000")
    print(f"API基础URL: http://localhost:5000/api/stats")
    print(f"统计目录: {app.config['STATS_DIR']}")
    print("=" * 70)
    
    app.run(host='0.0.0.0', port=5000, debug=True)

def create_default_template(template_path):
    """创建默认HTML模板"""
    html_content = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OrderBookSimulator性能监控仪表板</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --primary-color: #4361ee;
            --secondary-color: #3a0ca3;
            --success-color: #4cc9f0;
            --warning-color: #f72585;
            --info-color: #7209b7;
        }
        body {
            background-color: #f8f9fa;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        .dashboard-header {
            background: linear-gradient(135deg, var(--primary-color), var(--secondary-color));
            color: white;
            padding: 2rem 0;
            margin-bottom: 2rem;
            border-radius: 0 0 1rem 1rem;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }
        .stat-card {
            background: white;
            border-radius: 0.75rem;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            transition: transform 0.2s;
            border-left: 4px solid var(--primary-color);
        }
        .stat-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.12);
        }
        .stat-value {
            font-size: 2.2rem;
            font-weight: 700;
            color: var(--primary-color);
        }
        .stat-label {
            font-size: 0.9rem;
            color: #6c757d;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .chart-container {
            background: white;
            border-radius: 0.75rem;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }
        .health-indicator {
            display: inline-flex;
            align-items: center;
            padding: 0.25rem 0.75rem;
            border-radius: 2rem;
            font-size: 0.85rem;
            font-weight: 500;
        }
        .health-good {
            background-color: rgba(76, 201, 240, 0.1);
            color: #4cc9f0;
            border: 1px solid rgba(76, 201, 240, 0.3);
        }
        .health-warning {
            background-color: rgba(247, 37, 133, 0.1);
            color: #f72585;
            border: 1px solid rgba(247, 37, 133, 0.3);
        }
        .table-hover tbody tr:hover {
            background-color: rgba(67, 97, 238, 0.05);
        }
        .realtime-badge {
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.7; }
            100% { opacity: 1; }
        }
        .last-updated {
            font-size: 0.85rem;
            color: #6c757d;
        }
    </style>
</head>
<body>
    <!-- 头部 -->
    <div class="dashboard-header">
        <div class="container">
            <div class="row align-items-center">
                <div class="col-md-8">
                    <h1><i class="bi bi-speedometer2 me-2"></i>OrderBookSimulator性能监控仪表板</h1>
                    <p class="lead mb-0">实时监控订单簿模拟器的执行性能、冲击成本和系统健康状态</p>
                </div>
                <div class="col-md-4 text-end">
                    <div class="d-inline-block p-3 bg-white bg-opacity-10 rounded-3">
                        <div class="h4 mb-0" id="current-time">--:--:--</div>
                        <div class="small">服务器时间</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- 主内容区 -->
    <div class="container">
        <!-- 系统健康状态 -->
        <div class="row mb-4">
            <div class="col-12">
                <div class="chart-container">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h5 class="mb-0"><i class="bi bi-heart-pulse me-2"></i>系统健康状态</h5>
                        <span id="health-status" class="health-indicator health-good">健康</span>
                    </div>
                    <div class="row" id="health-details">
                        <!-- 通过JS动态加载 -->
                    </div>
                </div>
            </div>
        </div>

        <!-- 关键指标 -->
        <div class="row mb-4">
            <div class="col-md-3">
                <div class="stat-card">
                    <div class="stat-value" id="total-calls">0</div>
                    <div class="stat-label">总调用次数</div>
                    <div class="last-updated mt-2" id="last-updated-time">最后更新: --</div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="stat-card">
                    <div class="stat-value" id="execution-rate">0%</div>
                    <div class="stat-label">执行成功率</div>
                    <div class="small mt-2">
                        完全执行: <span id="fully-executed">0</span>
                        部分执行: <span id="partially-executed">0</span>
                    </div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="stat-card">
                    <div class="stat-value" id="avg-impact">0.0 bp</div>
                    <div class="stat-label">平均冲击成本</div>
                    <div class="small mt-2">
                        买入: <span id="buy-calls">0</span>次
                        卖出: <span id="sell-calls">0</span>次
                    </div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="stat-card">
                    <div class="stat-value" id="buy-sell-ratio">0%</div>
                    <div class="stat-label">买入卖出比例</div>
                    <div class="small mt-2">
                        统计周期: <span id="stats-period">--</span>
                    </div>
                </div>
            </div>
        </div>

        <!-- 图表区 -->
        <div class="row mb-4">
            <div class="col-md-6">
                <div class="chart-container">
                    <h5><i class="bi bi-pie-chart me-2"></i>执行状态分布</h5>
                    <canvas id="executionChart" height="250"></canvas>
                </div>
            </div>
            <div class="col-md-6">
                <div class="chart-container">
                    <h5><i class="bi bi-bar-chart me-2"></i>流动性分桶分布</h5>
                    <canvas id="liquidityChart" height="250"></canvas>
                </div>
            </div>
        </div>

        <div class="row mb-4">
            <div class="col-md-6">
                <div class="chart-container">
                    <h5><i class="bi bi-graph-up me-2"></i>市场状态分布</h5>
                    <canvas id="marketRegimeChart" height="250"></canvas>
                </div>
            </div>
            <div class="col-md-6">
                <div class="chart-container">
                    <h5><i class="bi bi-activity me-2"></i>冲击成本分布</h5>
                    <canvas id="impactChart" height="250"></canvas>
                </div>
            </div>
        </div>

        <!-- 最新交易记录 -->
        <div class="row">
            <div class="col-12">
                <div class="chart-container">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h5 class="mb-0"><i class="bi bi-list-ul me-2"></i>最新交易记录</h5>
                        <button class="btn btn-sm btn-outline-primary" onclick="loadRecords()">
                            <i class="bi bi-arrow-clockwise"></i> 刷新
                        </button>
                    </div>
                    <div class="table-responsive">
                        <table class="table table-hover" id="records-table">
                            <thead>
                                <tr>
                                    <th>时间</th>
                                    <th>股票</th>
                                    <th>方向</th>
                                    <th>执行状态</th>
                                    <th>冲击成本</th>
                                    <th>流动性分桶</th>
                                </tr>
                            </thead>
                            <tbody id="records-body">
                                <!-- 通过JS动态加载 -->
                            </tbody>
                        </table>
                    </div>
                    <div class="text-center py-3" id="loading-records">
                        <div class="spinner-border spinner-border-sm text-primary" role="status">
                            <span class="visually-hidden">加载中...</span>
                        </div>
                        <span class="ms-2">加载交易记录...</span>
                    </div>
                </div>
            </div>
        </div>

        <!-- 页脚 -->
        <footer class="mt-5 py-4 text-center text-muted border-top">
            <p class="mb-1">OrderBookSimulator性能监控仪表板 v1.0</p>
            <p class="small mb-0">生成时间: <span id="generation-time">--</span> | 数据来源: OrderBookStats模块</p>
        </footer>
    </div>

    <!-- JavaScript -->
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // 全局变量
        let charts = {};
        let autoRefreshInterval = null;

        // 页面加载完成
        document.addEventListener('DOMContentLoaded', function() {
            // 更新时间显示
            updateCurrentTime();
            setInterval(updateCurrentTime, 1000);

            // 初始加载数据
            loadDashboardData();
            loadHealthStatus();
            loadChartData();
            loadRecords();

            // 设置自动刷新（每30秒）
            autoRefreshInterval = setInterval(loadDashboardData, 30000);
        });

        // 更新当前时间
        function updateCurrentTime() {
            const now = new Date();
            document.getElementById('current-time').textContent = 
                now.toLocaleTimeString('zh-CN');
            document.getElementById('generation-time').textContent = 
                now.toLocaleString('zh-CN');
        }

        // 加载仪表板数据
        async function loadDashboardData() {
            try {
                const response = await fetch('/api/stats');
                const data = await response.json();
                
                if (data.data_available) {
                    // 更新关键指标
                    document.getElementById('total-calls').textContent = 
                        data.total_calls.toLocaleString();
                    document.getElementById('execution-rate').textContent = 
                        data.execution_rate.toFixed(1) + '%';
                    document.getElementById('avg-impact').textContent = 
                        data.avg_impact_cost.toFixed(1) + ' bp';
                    document.getElementById('buy-sell-ratio').textContent = 
                        (data.buy_sell_ratio * 100).toFixed(1) + '%';
                    
                    document.getElementById('fully-executed').textContent = data.fully_executed;
                    document.getElementById('partially-executed').textContent = data.partially_executed;
                    document.getElementById('buy-calls').textContent = data.buy_calls;
                    document.getElementById('sell-calls').textContent = data.sell_calls;
                    
                    // 更新最后更新时间
                    if (data.last_updated && data.last_updated !== 'N/A') {
                        const lastUpdated = new Date(data.last_updated);
                        document.getElementById('last-updated-time').textContent = 
                            '最后更新: ' + lastUpdated.toLocaleString('zh-CN');
                    }
                    
                    // 更新统计周期
                    if (data.start_time && data.start_time !== 'N/A') {
                        const startTime = new Date(data.start_time);
                        const now = new Date();
                        const diffHours = Math.round((now - startTime) / (1000 * 60 * 60));
                        document.getElementById('stats-period').textContent = diffHours + '小时';
                    }
                } else {
                    // 无数据可用
                    document.getElementById('total-calls').textContent = '0';
                    document.getElementById('execution-rate').textContent = '0%';
                    document.getElementById('last-updated-time').textContent = '暂无数据';
                }
            } catch (error) {
                console.error('加载仪表板数据失败:', error);
            }
        }

        // 加载健康状态
        async function loadHealthStatus() {
            try {
                const response = await fetch('/api/health');
                const data = await response.json();
                
                const healthDetails = document.getElementById('health-details');
                let healthHtml = '';
                
                // 检查文件状态
                if (data.stats_file_exists) {
                    healthHtml += `
                        <div class="col-md-3">
                            <div class="d-flex align-items-center">
                                <i class="bi bi-file-earmark-text text-success fs-4 me-2"></i>
                                <div>
                                    <div class="fw-bold">统计文件</div>
                                    <div class="small">${data.stats_file_fresh ? '✓ 数据新鲜' : '⚠️ 需要更新'}</div>
                                </div>
                            </div>
                        </div>
                    `;
                } else {
                    healthHtml += `
                        <div class="col-md-3">
                            <div class="d-flex align-items-center">
                                <i class="bi bi-file-earmark-x text-danger fs-4 me-2"></i>
                                <div>
                                    <div class="fw-bold">统计文件</div>
                                    <div class="small">❌ 不存在</div>
                                </div>
                            </div>
                        </div>
                    `;
                }
                
                if (data.detailed_file_exists) {
                    healthHtml += `
                        <div class="col-md-3">
                            <div class="d-flex align-items-center">
                                <i class="bi bi-file-earmark-spreadsheet text-success fs-4 me-2"></i>
                                <div>
                                    <div class="fw-bold">详细记录</div>
                                    <div class="small">${data.detailed_file_fresh ? '✓ 数据新鲜' : '⚠️ 需要更新'}</div>
                                </div>
                            </div>
                        </div>
                    `;
                } else {
                    healthHtml += `
                        <div class="col-md-3">
                            <div class="d-flex align-items-center">
                                <i class="bi bi-file-earmark-x text-danger fs-4 me-2"></i>
                                <div>
                                    <div class="fw-bold">详细记录</div>
                                    <div class="small">❌ 不存在</div>
                                </div>
                            </div>
                        </div>
                    `;
                }
                
                // 服务器时间
                healthHtml += `
                    <div class="col-md-3">
                        <div class="d-flex align-items-center">
                            <i class="bi bi-clock text-primary fs-4 me-2"></i>
                            <div>
                                <div class="fw-bold">服务器时间</div>
                                <div class="small">${new Date(data.current_time).toLocaleTimeString('zh-CN')}</div>
                            </div>
                        </div>
                    </div>
                `;
                
                // 目录信息
                healthHtml += `
                    <div class="col-md-3">
                        <div class="d-flex align-items-center">
                            <i class="bi bi-folder text-info fs-4 me-2"></i>
                            <div>
                                <div class="fw-bold">统计目录</div>
                                <div class="small text-truncate" title="${data.stats_dir}">${data.stats_dir.split('/').slice(-2).join('/')}</div>
                            </div>
                        </div>
                    </div>
                `;
                
                healthDetails.innerHTML = healthHtml;
                
                // 更新健康状态指示器
                const healthIndicator = document.getElementById('health-status');
                if (data.stats_file_exists && data.stats_file_fresh) {
                    healthIndicator.className = 'health-indicator health-good';
                    healthIndicator.textContent = '系统健康';
                } else {
                    healthIndicator.className = 'health-indicator health-warning';
                    healthIndicator.textContent = '需要关注';
                }
                
            } catch (error) {
                console.error('加载健康状态失败:', error);
            }
        }

        // 加载图表数据
        async function loadChartData() {
            try {
                const response = await fetch('/api/charts');
                const data = await response.json();
                
                // 创建或更新图表
                createOrUpdateChart('executionChart', 'pie', data.execution_status, '执行状态分布');
                createOrUpdateChart('liquidityChart', 'bar', data.liquidity_distribution, '流动性分桶分布', 'bucket');
                createOrUpdateChart('marketRegimeChart', 'doughnut', data.market_regime_distribution, '市场状态分布', 'regime');
                createOrUpdateChart('impactChart', 'bar', data.impact_cost_distribution, '冲击成本分布 (bp)', 'range');
                
            } catch (error) {
                console.error('加载图表数据失败:', error);
            }
        }

        // 创建或更新图表
        function createOrUpdateChart(canvasId, chartType, data, label, labelKey = 'name') {
            const canvas = document.getElementById(canvasId);
            const ctx = canvas.getContext('2d');
            
            // 如果图表已存在，销毁它
            if (charts[canvasId]) {
                charts[canvasId].destroy();
            }
            
            // 准备图表数据
            const labels = data.map(item => item[labelKey]);
            const values = data.map(item => item.value || item.count);
            
            // 颜色配置
            const backgroundColors = [
                'rgba(67, 97, 238, 0.7)',
                'rgba(76, 201, 240, 0.7)',
                'rgba(114, 9, 183, 0.7)',
                'rgba(247, 37, 133, 0.7)',
                'rgba(58, 12, 163, 0.7)',
                'rgba(0, 184, 148, 0.7)',
                'rgba(253, 203, 110, 0.7)',
                'rgba(225, 112, 85, 0.7)'
            ];
            
            const borderColors = backgroundColors.map(color => color.replace('0.7', '1'));
            
            // 创建新图表
            charts[canvasId] = new Chart(ctx, {
                type: chartType,
                data: {
                    labels: labels,
                    datasets: [{
                        label: label,
                        data: values,
                        backgroundColor: backgroundColors.slice(0, data.length),
                        borderColor: borderColors.slice(0, data.length),
                        borderWidth: 1
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            position: 'bottom',
                            labels: {
                                padding: 20,
                                usePointStyle: true
                            }
                        },
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    return `${context.label}: ${context.raw}`;
                                }
                            }
                        }
                    }
                }
            });
        }

        // 加载交易记录
        async function loadRecords() {
            const loadingEl = document.getElementById('loading-records');
            const recordsBody = document.getElementById('records-body');
            
            loadingEl.style.display = 'block';
            recordsBody.innerHTML = '';
            
            try {
                const response = await fetch('/api/records?limit=20');
                const data = await response.json();
                
                if (data.records && data.records.length > 0) {
                    let html = '';
                    
                    data.records.forEach(record => {
                        const timestamp = record.timestamp ? 
                            new Date(record.timestamp).toLocaleString('zh-CN') : '--';
                        
                        // 执行状态样式
                        let statusClass = '';
                        switch(record.execution_status) {
                            case 'fully_executed':
                                statusClass = 'badge bg-success';
                                break;
                            case 'partially_executed':
                                statusClass = 'badge bg-warning';
                                break;
                            case 'rejected':
                                statusClass = 'badge bg-danger';
                                break;
                            default:
                                statusClass = 'badge bg-secondary';
                        }
                        
                        // 买卖方向样式
                        let sideClass = record.order_side === 'buy' ? 
                            'badge bg-primary' : 'badge bg-info';
                        
                        html += `
                            <tr>
                                <td>${timestamp}</td>
                                <td><strong>${record.symbol || '--'}</strong></td>
                                <td><span class="${sideClass}">${record.order_side || '--'}</span></td>
                                <td><span class="${statusClass}">${record.execution_status || '--'}</span></td>
                                <td>${record.impact_cost_bps ? record.impact_cost_bps.toFixed(1) + ' bp' : '--'}</td>
                                <td>${record.liquidity_bucket || '--'}</td>
                            </tr>
                        `;
                    });
                    
                    recordsBody.innerHTML = html;
                } else {
                    recordsBody.innerHTML = `
                        <tr>
                            <td colspan="6" class="text-center py-4 text-muted">
                                <i class="bi bi-inbox fs-1 d-block mb-2"></i>
                                暂无交易记录
                            </td>
                        </tr>
                    `;
                }
            } catch (error) {
                console.error('加载交易记录失败:', error);
                recordsBody.innerHTML = `
                    <tr>
                        <td colspan="6" class="text-center py-4 text-danger">
                            <i class="bi bi-exclamation-triangle fs-1 d-block mb-2"></i>
                            加载失败: ${error.message}
                        </td>
                    </tr>
                `;
            } finally {
                loadingEl.style.display = 'none';
            }
        }

        // 手动刷新所有数据
        function refreshAll() {
            loadDashboardData();
            loadHealthStatus();
            loadChartData();
            loadRecords();
            
            // 显示刷新提示
            const refreshBtn = event?.target || document.querySelector('[onclick="loadRecords()"]');
            if (refreshBtn) {
                const originalHtml = refreshBtn.innerHTML;
                refreshBtn.innerHTML = '<i class="bi bi-check-circle"></i> 已刷新';
                refreshBtn.disabled = true;
                
                setTimeout(() => {
                    refreshBtn.innerHTML = originalHtml;
                    refreshBtn.disabled = false;
                }, 2000);
            }
        }
    </script>
</body>
</html>'''
    
    with open(template_path, 'w') as f:
        f.write(html_content)
    
    print(f"默认模板已创建: {template_path}")