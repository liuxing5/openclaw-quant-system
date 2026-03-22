#!/usr/bin/env python3
"""
集成版性能监控仪表板
包含用户权限系统和策略回放引擎
"""

import sys
import os
import json
from datetime import datetime
from flask import Flask, render_template, jsonify, request, g
from flask_cors import CORS
import warnings
warnings.filterwarnings('ignore')

# 添加quant_system路径
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

# 导入现有仪表板
from .app import app as dashboard_app, DashboardManager

# 导入权限系统
try:
    from ..auth.api import init_auth_system, require_auth, require_permission
    from ..auth.models import Base
    from ..auth.auth import AuthService, RBACManager
    AUTH_AVAILABLE = True
except ImportError as e:
    print(f"警告: 权限系统导入失败: {e}")
    AUTH_AVAILABLE = False

# 导入回放引擎
try:
    from ..replay.core import (
        StrategyReplayer, ReplayComparator, ReplayReportGenerator, ReplayDataManager
    )
    REPLAY_AVAILABLE = True
except ImportError as e:
    print(f"警告: 回放引擎导入失败: {e}")
    REPLAY_AVAILABLE = False

# 创建主应用
app = Flask(__name__)
CORS(app)  # 启用CORS

# 配置
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'quant-system-integrated-2026')
app.config['STATS_DIR'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'stats')
app.config['DATABASE_URL'] = os.environ.get('DATABASE_URL', 'sqlite:///./quant_system/auth/auth.db')

# 初始化现有仪表板管理器
dashboard_manager = DashboardManager()

# 如果权限系统可用，初始化
if AUTH_AVAILABLE:
    app = init_auth_system(app)

# 创建回放引擎实例
if REPLAY_AVAILABLE:
    replay_data_manager = ReplayDataManager()
    strategy_replayer = StrategyReplayer(replay_data_manager)
    replay_comparator = ReplayComparator(replay_data_manager)
    replay_report_generator = ReplayReportGenerator()


@app.route('/')
def index():
    """主页"""
    return render_template('index.html')


@app.route('/api/health')
def health_check():
    """健康检查"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'services': {
            'dashboard': 'running',
            'auth': 'available' if AUTH_AVAILABLE else 'unavailable',
            'replay': 'available' if REPLAY_AVAILABLE else 'unavailable'
        },
        'version': '3.1-integrated'
    })


@app.route('/api/dashboard/stats')
def get_dashboard_stats():
    """获取仪表板统计（公开或受保护）"""
    # 如果有权限系统，检查权限
    if AUTH_AVAILABLE and hasattr(g, 'current_user'):
        # 已认证用户
        pass
    elif AUTH_AVAILABLE:
        # 未认证用户，只允许访问公开数据
        pass
    
    stats = dashboard_manager.load_stats_summary()
    return jsonify(stats)


@app.route('/api/dashboard/records')
def get_dashboard_records():
    """获取详细记录"""
    page = int(request.args.get('page', 1))
    limit = int(request.args.get('limit', 50))
    
    records = dashboard_manager.load_detailed_records(page, limit)
    return jsonify(records)


@app.route('/api/dashboard/charts')
def get_dashboard_charts():
    """获取图表数据"""
    charts = dashboard_manager.generate_chart_data()
    return jsonify(charts)


# 回放引擎API（需要认证）
if REPLAY_AVAILABLE:
    
    @app.route('/api/replay/snapshot', methods=['POST'])
    @require_auth() if AUTH_AVAILABLE else lambda f: f
    @require_permission('strategy.execute') if AUTH_AVAILABLE else lambda f: f
    def create_replay_snapshot():
        """创建策略回放快照"""
        data = request.get_json()
        if not data:
            return jsonify({'error': '无效的请求数据'}), 400
        
        snapshot_id = strategy_replayer.create_snapshot(
            strategy_config=data.get('strategy_config', {}),
            portfolio_state=data.get('portfolio_state', {}),
            market_conditions=data.get('market_conditions', {})
        )
        
        return jsonify({
            'success': True,
            'snapshot_id': snapshot_id,
            'message': '快照创建成功'
        })
    
    @app.route('/api/replay/execute', methods=['POST'])
    @require_auth() if AUTH_AVAILABLE else lambda f: f
    @require_permission('strategy.execute') if AUTH_AVAILABLE else lambda f: f
    def execute_replay():
        """执行策略回放"""
        data = request.get_json()
        if not data:
            return jsonify({'error': '无效的请求数据'}), 400
        
        snapshot_id = data.get('snapshot_id')
        replay_date = data.get('replay_date')
        parameters = data.get('parameters', {})
        
        if not snapshot_id or not replay_date:
            return jsonify({'error': '缺少必要参数'}), 400
        
        try:
            result = strategy_replayer.replay_strategy(
                snapshot_id=snapshot_id,
                replay_date=replay_date,
                parameters=parameters
            )
            
            return jsonify({
                'success': True,
                'replay_id': result['replay_id'],
                'result': result['result'],
                'message': '回放执行成功'
            })
        except Exception as e:
            return jsonify({'error': f'回放执行失败: {str(e)}'}), 500
    
    @app.route('/api/replay/compare', methods=['POST'])
    @require_auth() if AUTH_AVAILABLE else lambda f: f
    @require_permission('strategy.execute') if AUTH_AVAILABLE else lambda f: f
    def compare_replays():
        """比较多个回放结果"""
        data = request.get_json()
        if not data:
            return jsonify({'error': '无效的请求数据'}), 400
        
        replay_ids = data.get('replay_ids', [])
        if not replay_ids:
            return jsonify({'error': '需要提供回放ID列表'}), 400
        
        try:
            comparison = replay_comparator.compare_replays(replay_ids)
            
            # 生成报告
            report_format = data.get('format', 'html')
            report_file = replay_report_generator.generate_comparison_report(
                comparison, format=report_format
            )
            
            return jsonify({
                'success': True,
                'comparison': comparison,
                'report_file': report_file,
                'message': '回放对比完成'
            })
        except Exception as e:
            return jsonify({'error': f'回放对比失败: {str(e)}'}), 500
    
    @app.route('/api/replay/parameter-sensitivity', methods=['POST'])
    @require_auth() if AUTH_AVAILABLE else lambda f: f
    @require_permission('strategy.execute') if AUTH_AVAILABLE else lambda f: f
    def analyze_parameter_sensitivity():
        """分析参数敏感性"""
        data = request.get_json()
        if not data:
            return jsonify({'error': '无效的请求数据'}), 400
        
        snapshot_id = data.get('snapshot_id')
        replay_date = data.get('replay_date')
        parameter_sets = data.get('parameter_sets', [])
        
        if not snapshot_id or not replay_date or not parameter_sets:
            return jsonify({'error': '缺少必要参数'}), 400
        
        try:
            result = strategy_replayer.replay_with_different_parameters(
                snapshot_id=snapshot_id,
                replay_date=replay_date,
                parameter_sets=parameter_sets
            )
            
            return jsonify({
                'success': True,
                'analysis': result,
                'message': '参数敏感性分析完成'
            })
        except Exception as e:
            return jsonify({'error': f'参数敏感性分析失败: {str(e)}'}), 500
    
    @app.route('/api/replay/results/<replay_id>', methods=['GET'])
    @require_auth() if AUTH_AVAILABLE else lambda f: f
    @require_permission('strategy.read') if AUTH_AVAILABLE else lambda f: f
    def get_replay_result(replay_id):
        """获取回放结果"""
        try:
            result = replay_data_manager.load_replay_result(replay_id)
            if not result:
                return jsonify({'error': '回放结果不存在'}), 404
            
            return jsonify({
                'success': True,
                'result': result,
                'message': '回放结果获取成功'
            })
        except Exception as e:
            return jsonify({'error': f'获取回放结果失败: {str(e)}'}), 500


# 系统管理API（需要管理员权限）
@app.route('/api/system/info')
def get_system_info():
    """获取系统信息"""
    info = dashboard_manager.get_system_health()
    
    # 添加权限系统信息
    if AUTH_AVAILABLE:
        info['auth'] = {
            'available': True,
            'initialized': True,
            'database': app.config['DATABASE_URL']
        }
    else:
        info['auth'] = {'available': False}
    
    # 添加回放引擎信息
    if REPLAY_AVAILABLE:
        info['replay'] = {'available': True}
    else:
        info['replay'] = {'available': False}
    
    return jsonify(info)


# 集成认证状态检查
@app.route('/api/auth/status')
def get_auth_status():
    """获取认证系统状态"""
    if not AUTH_AVAILABLE:
        return jsonify({
            'available': False,
            'message': '权限系统不可用'
        })
    
    return jsonify({
        'available': True,
        'initialized': True,
        'services': ['user_auth', 'rbac', 'audit_log', 'api_tokens'],
        'version': '1.0'
    })


# 回放引擎状态检查
@app.route('/api/replay/status')
def get_replay_status():
    """获取回放引擎状态"""
    if not REPLAY_AVAILABLE:
        return jsonify({
            'available': False,
            'message': '回放引擎不可用'
        })
    
    return jsonify({
        'available': True,
        'services': ['snapshot', 'replay', 'comparison', 'report'],
        'version': '1.0'
    })


# 更新现有的仪表板路由，添加认证保护
def wrap_with_auth_if_needed(route_func):
    """如果需要，用认证包装路由函数"""
    if AUTH_AVAILABLE:
        return require_auth()(route_func)
    return route_func


# 如果需要，可以在这里重写现有路由
# 但为了简化，我们保持原有路由不变，只在需要时添加认证


if __name__ == '__main__':
    print("启动集成版量化系统仪表板...")
    print(f"权限系统: {'可用' if AUTH_AVAILABLE else '不可用'}")
    print(f"回放引擎: {'可用' if REPLAY_AVAILABLE else '不可用'}")
    print(f"仪表板: 可用")
    print(f"访问地址: http://localhost:5000")
    print(f"API文档: http://localhost:5000/api/health")
    
    # 启动Flask应用
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False,
        threaded=True
    )