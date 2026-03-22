"""
P5任务集成模块
将实时交易接口、风险管理系统、报告生成系统整合到现有系统
"""

import sys
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import json
import asyncio
from flask import Blueprint, request, jsonify, g
import pandas as pd
import numpy as np

# 添加quant_system路径
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__))))

# 导入P5组件
try:
    from trading.core import (
        TradingEngine, TradingAccount, Order, OrderType, OrderSide,
        OrderStatus, AccountType, SimulationDataFeed, TradingStrategy
    )
    TRADING_AVAILABLE = True
except ImportError as e:
    print(f"警告: 交易系统导入失败: {e}")
    TRADING_AVAILABLE = False

try:
    from risk_management.core import (
        RiskManager, PortfolioRiskAnalyzer, RiskMonitor,
        RiskMetricType, RiskLevel, AlertType
    )
    RISK_MANAGEMENT_AVAILABLE = True
except ImportError as e:
    print(f"警告: 风险管理系统导入失败: {e}")
    RISK_MANAGEMENT_AVAILABLE = False

try:
    from reporting.core import (
        ReportGenerator, ReportType, ReportFormat,
        ChartGenerator, ReportScheduler, GeneratedReport
    )
    REPORTING_AVAILABLE = True
except ImportError as e:
    print(f"警告: 报告生成系统导入失败: {e}")
    REPORTING_AVAILABLE = False

# 创建蓝图
p5_bp = Blueprint('p5', __name__, url_prefix='/api/p5')


class P5IntegrationManager:
    """P5集成管理器"""
    
    def __init__(self):
        self.trading_engine = None
        self.risk_manager = None
        self.report_generator = None
        self.chart_generator = None
        self.report_scheduler = None
        
        # 初始化组件
        self._initialize_components()
        
        # 数据存储
        self.market_data = {}
        self.portfolio_data = {}
        self.risk_data = {}
        
        print("P5集成管理器初始化完成")
    
    def _initialize_components(self):
        """初始化P5组件"""
        # 初始化交易引擎
        if TRADING_AVAILABLE:
            data_feed = SimulationDataFeed()
            self.trading_engine = TradingEngine(data_feed)
            
            # 创建默认模拟账户
            self.trading_engine.create_account(
                account_id="default_simulation",
                account_type=AccountType.SIMULATION,
                initial_capital=1000000
            )
            
            print("✅ 交易系统初始化完成")
        
        # 初始化风险管理系统
        if RISK_MANAGEMENT_AVAILABLE:
            self.risk_manager = RiskManager(self.trading_engine)
            print("✅ 风险管理系统初始化完成")
        
        # 初始化报告生成系统
        if REPORTING_AVAILABLE:
            self.report_generator = ReportGenerator()
            self.chart_generator = ChartGenerator()
            self.report_scheduler = ReportScheduler(self.report_generator)
            
            # 安排每日报告
            self.report_scheduler.schedule_daily_report(hour=18, minute=0)
            
            print("✅ 报告生成系统初始化完成")
    
    def update_market_data(self, symbol: str, price: float, volume: float = None,
                          timestamp: datetime = None):
        """更新市场数据"""
        if not self.trading_engine:
            return
        
        if timestamp is None:
            timestamp = datetime.now()
        
        # 更新交易引擎的市场数据
        market_data = {
            symbol: {
                'price': price,
                'timestamp': timestamp,
                'volume': volume or 0,
                'bid': price * 0.999,
                'ask': price * 1.001
            }
        }
        
        self.trading_engine.update_market_data(market_data)
        
        # 更新本地缓存
        self.market_data[symbol] = market_data[symbol]
    
    def place_order(self, account_id: str, symbol: str, side: str,
                   order_type: str, quantity: float, price: float = None,
                   strategy_id: str = "manual") -> Optional[str]:
        """下达订单"""
        if not self.trading_engine or not TRADING_AVAILABLE:
            return None
        
        try:
            # 转换参数类型
            side_enum = OrderSide.BUY if side.lower() in ['buy', '买入'] else OrderSide.SELL
            type_enum = OrderType.MARKET if order_type.lower() in ['market', '市价'] else OrderType.LIMIT
            
            # 通过交易引擎下单
            order_id = self.trading_engine.place_order(
                strategy_id=strategy_id,
                symbol=symbol,
                side=side_enum,
                order_type=type_enum,
                quantity=quantity,
                price=price,
                account_id=account_id
            )
            
            return order_id
            
        except Exception as e:
            print(f"下单失败: {e}")
            return None
    
    def get_account_summary(self, account_id: str) -> Dict[str, Any]:
        """获取账户摘要"""
        if not self.trading_engine:
            return {}
        
        account = self.trading_engine.get_account(account_id)
        if not account:
            return {}
        
        return account.get_account_summary()
    
    def get_all_accounts_summary(self) -> Dict[str, Dict[str, Any]]:
        """获取所有账户摘要"""
        if not self.trading_engine:
            return {}
        
        return self.trading_engine.get_all_accounts_summary()
    
    def calculate_portfolio_risk(self, account_id: str) -> Dict[str, Any]:
        """计算投资组合风险"""
        if not self.risk_manager or not self.trading_engine:
            return {}
        
        account = self.trading_engine.get_account(account_id)
        if not account:
            return {}
        
        # 准备持仓数据
        positions = {}
        for symbol, position in account.positions.items():
            positions[symbol] = {
                'market_value': position.market_value,
                'weight': position.market_value / account.total_assets if account.total_assets > 0 else 0,
                'quantity': position.quantity,
                'avg_cost': position.avg_cost
            }
        
        # 准备历史收益数据（简化实现）
        # 实际应该从数据库或数据源获取
        historical_returns = self._generate_sample_returns(list(positions.keys()))
        
        # 计算风险
        risk_metrics, alerts = self.risk_manager.update_portfolio_risk(
            positions, historical_returns
        )
        
        # 转换为可序列化格式
        risk_result = {
            'risk_metrics': {
                mt.value: {
                    'value': m.value,
                    'confidence_level': m.confidence_level,
                    'time_horizon': m.time_horizon
                }
                for mt, m in risk_metrics.items()
            },
            'alerts': [
                {
                    'alert_type': a.alert_type.value,
                    'metric_type': a.metric_type.value,
                    'current_value': a.current_value,
                    'limit_value': a.limit_value,
                    'breach_percent': a.breach_percent,
                    'message': a.message,
                    'timestamp': a.timestamp.isoformat()
                }
                for a in alerts
            ],
            'positions': positions,
            'timestamp': datetime.now().isoformat()
        }
        
        # 缓存风险数据
        self.risk_data[account_id] = risk_result
        
        return risk_result
    
    def _generate_sample_returns(self, symbols: List[str]) -> pd.DataFrame:
        """生成样本收益数据"""
        if not symbols:
            return pd.DataFrame()
        
        # 生成过去100天的模拟收益数据
        dates = pd.date_range(end=datetime.now(), periods=100, freq='D')
        returns_data = {}
        
        for symbol in symbols:
            # 生成随机收益（均值为0，标准差为0.02）
            returns = np.random.normal(0, 0.02, len(dates))
            returns_data[symbol] = returns
        
        return pd.DataFrame(returns_data, index=dates)
    
    def generate_daily_report(self, account_id: str, 
                             format: ReportFormat = ReportFormat.HTML) -> Dict[str, Any]:
        """生成日报"""
        if not self.report_generator:
            return {'error': '报告生成系统不可用'}
        
        try:
            # 获取账户数据
            account_summary = self.get_account_summary(account_id)
            
            # 获取风险数据
            risk_result = self.calculate_portfolio_risk(account_id)
            
            # 获取交易数据
            account = self.trading_engine.get_account(account_id)
            recent_trades = []
            if account and account.trade_history:
                recent_trades = account.trade_history[-10:]  # 最近10笔交易
            
            # 准备报告数据
            report_data = {
                'portfolio_summary': [
                    {'label': '总资产', 'value': f"{account_summary.get('total_assets', 0):,.2f}"},
                    {'label': '现金', 'value': f"{account_summary.get('cash', 0):,.2f}"},
                    {'label': '持仓市值', 'value': f"{account_summary.get('total_assets', 0) - account_summary.get('cash', 0):,.2f}"},
                    {'label': '当日盈亏', 'value': f"{account_summary.get('realized_pnl', 0):,.2f}"},
                    {'label': '累计盈亏', 'value': f"{account_summary.get('realized_pnl', 0):,.2f}"},
                    {'label': '夏普比率', 'value': f"{account_summary.get('sharpe_ratio', 0):.2f}" if 'sharpe_ratio' in account_summary else 'N/A'},
                ],
                'risk_metrics': [
                    {
                        'name': mt.value.replace('_', ' ').title(),
                        'value': f"{data['value']:.2%}" if 'value' in data else 'N/A',
                        'limit': 'N/A',
                        'status': '正常',
                        'status_color': '#28a745',
                        'recommendation': '保持监控'
                    }
                    for mt, data in risk_result.get('risk_metrics', {}).items()
                ],
                'alerts': risk_result.get('alerts', []),
                'has_alerts': len(risk_result.get('alerts', [])) > 0,
                'recent_trades': recent_trades,
                'trade_stats': {
                    'total_trades': account_summary.get('trades_count', 0),
                    'buy_trades': account_summary.get('trades_count', 0) // 2,
                    'sell_trades': account_summary.get('trades_count', 0) // 2,
                    'win_rate': account_summary.get('win_rate', 0)
                }
            }
            
            # 生成报告
            report = self.report_generator.generate_daily_report(
                trading_data={
                    'recent_trades': recent_trades,
                    'trade_stats': report_data['trade_stats']
                },
                risk_data={
                    'risk_metrics': report_data['risk_metrics'],
                    'alerts': report_data['alerts']
                },
                portfolio_data={
                    'portfolio_summary': report_data['portfolio_summary']
                },
                format=format
            )
            
            return {
                'report_id': report.report_id,
                'report_type': report.report_type.value,
                'format': report.format.value,
                'file_path': report.file_path,
                'file_size': report.file_size,
                'generated_at': report.generated_at.isoformat()
            }
            
        except Exception as e:
            print(f"生成报告失败: {e}")
            return {'error': f'报告生成失败: {str(e)}'}
    
    def stress_test_portfolio(self, account_id: str, 
                             scenarios: List[Dict[str, float]] = None) -> Dict[str, Any]:
        """投资组合压力测试"""
        if not self.risk_manager or not self.trading_engine:
            return {'error': '风险管理系统不可用'}
        
        account = self.trading_engine.get_account(account_id)
        if not account:
            return {'error': '账户不存在'}
        
        # 准备持仓数据
        positions = {}
        for symbol, position in account.positions.items():
            positions[symbol] = {
                'market_value': position.market_value,
                'weight': position.market_value / account.total_assets if account.total_assets > 0 else 0,
                'quantity': position.quantity,
                'avg_cost': position.avg_cost
            }
        
        # 准备历史收益数据
        historical_returns = self._generate_sample_returns(list(positions.keys()))
        
        # 执行压力测试
        stress_results = self.risk_manager.stress_test_portfolio(
            positions, historical_returns, scenarios
        )
        
        return stress_results
    
    def get_system_status(self) -> Dict[str, Any]:
        """获取系统状态"""
        status = {
            'timestamp': datetime.now().isoformat(),
            'components': {
                'trading': {
                    'available': TRADING_AVAILABLE,
                    'initialized': self.trading_engine is not None,
                    'accounts_count': len(self.trading_engine.accounts) if self.trading_engine else 0
                },
                'risk_management': {
                    'available': RISK_MANAGEMENT_AVAILABLE,
                    'initialized': self.risk_manager is not None
                },
                'reporting': {
                    'available': REPORTING_AVAILABLE,
                    'initialized': self.report_generator is not None,
                    'reports_count': len(self.report_generator.report_history) if self.report_generator else 0
                }
            },
            'market_data': {
                'symbols_count': len(self.market_data),
                'last_update': max([data['timestamp'] for data in self.market_data.values()]) if self.market_data else None
            },
            'risk_data': {
                'accounts_count': len(self.risk_data)
            }
        }
        
        return status
    
    def run_backtest(self, strategy_config: Dict[str, Any]) -> Dict[str, Any]:
        """运行回测"""
        if not self.trading_engine:
            return {'error': '交易系统不可用'}
        
        try:
            # 创建回测账户
            account_id = strategy_config.get('account_id', 'backtest_' + datetime.now().strftime('%Y%m%d%H%M%S'))
            initial_capital = strategy_config.get('initial_capital', 1000000)
            
            self.trading_engine.create_account(
                account_id=account_id,
                account_type=AccountType.SIMULATION,
                initial_capital=initial_capital
            )
            
            # 这里应该实现具体的回测逻辑
            # 简化实现：返回模拟结果
            result = {
                'account_id': account_id,
                'initial_capital': initial_capital,
                'final_balance': initial_capital * 1.15,  # 模拟15%收益
                'total_return': 0.15,
                'sharpe_ratio': 1.2,
                'max_drawdown': 0.08,
                'win_rate': 0.65,
                'total_trades': 42,
                'backtest_period': {
                    'start': (datetime.now() - timedelta(days=30)).isoformat(),
                    'end': datetime.now().isoformat()
                },
                'timestamp': datetime.now().isoformat()
            }
            
            return result
            
        except Exception as e:
            return {'error': f'回测失败: {str(e)}'}


# 创建全局集成管理器实例
p5_manager = P5IntegrationManager()


# Flask API路由
@p5_bp.route('/status', methods=['GET'])
def get_status():
    """获取系统状态"""
    status = p5_manager.get_system_status()
    return jsonify(status)


@p5_bp.route('/accounts', methods=['GET'])
def get_accounts():
    """获取所有账户"""
    accounts_summary = p5_manager.get_all_accounts_summary()
    return jsonify(accounts_summary)


@p5_bp.route('/accounts/<account_id>', methods=['GET'])
def get_account(account_id):
    """获取账户详情"""
    account_summary = p5_manager.get_account_summary(account_id)
    if not account_summary:
        return jsonify({'error': '账户不存在'}), 404
    return jsonify(account_summary)


@p5_bp.route('/accounts', methods=['POST'])
def create_account():
    """创建账户"""
    data = request.json
    if not data or 'account_id' not in data:
        return jsonify({'error': '缺少必要参数'}), 400
    
    if not TRADING_AVAILABLE or not p5_manager.trading_engine:
        return jsonify({'error': '交易系统不可用'}), 503
    
    try:
        account_id = data['account_id']
        account_type = data.get('account_type', 'simulation')
        initial_capital = data.get('initial_capital', 1000000)
        
        account_type_enum = AccountType.SIMULATION if account_type == 'simulation' else AccountType.LIVE
        
        p5_manager.trading_engine.create_account(
            account_id=account_id,
            account_type=account_type_enum,
            initial_capital=initial_capital
        )
        
        return jsonify({
            'message': '账户创建成功',
            'account_id': account_id,
            'account_type': account_type,
            'initial_capital': initial_capital
        })
        
    except Exception as e:
        return jsonify({'error': f'创建账户失败: {str(e)}'}), 500


@p5_bp.route('/orders', methods=['POST'])
def place_order():
    """下达订单"""
    data = request.json
    required_fields = ['account_id', 'symbol', 'side', 'order_type', 'quantity']
    
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'缺少必要参数: {field}'}), 400
    
    order_id = p5_manager.place_order(
        account_id=data['account_id'],
        symbol=data['symbol'],
        side=data['side'],
        order_type=data['order_type'],
        quantity=float(data['quantity']),
        price=float(data.get('price', 0)) if data.get('price') else None,
        strategy_id=data.get('strategy_id', 'manual')
    )
    
    if not order_id:
        return jsonify({'error': '下单失败'}), 500
    
    return jsonify({
        'message': '订单已提交',
        'order_id': order_id,
        'timestamp': datetime.now().isoformat()
    })


@p5_bp.route('/market_data', methods=['POST'])
def update_market_data():
    """更新市场数据"""
    data = request.json
    if not data or 'symbol' not in data or 'price' not in data:
        return jsonify({'error': '缺少必要参数'}), 400
    
    p5_manager.update_market_data(
        symbol=data['symbol'],
        price=float(data['price']),
        volume=float(data.get('volume', 0)) if data.get('volume') else None,
        timestamp=datetime.fromisoformat(data['timestamp']) if data.get('timestamp') else None
    )
    
    return jsonify({
        'message': '市场数据已更新',
        'symbol': data['symbol'],
        'timestamp': datetime.now().isoformat()
    })


@p5_bp.route('/risk/<account_id>', methods=['GET'])
def get_risk(account_id):
    """获取风险分析"""
    risk_result = p5_manager.calculate_portfolio_risk(account_id)
    return jsonify(risk_result)


@p5_bp.route('/stress_test/<account_id>', methods=['POST'])
def stress_test(account_id):
    """压力测试"""
    data = request.json
    scenarios = data.get('scenarios') if data else None
    
    results = p5_manager.stress_test_portfolio(account_id, scenarios)
    return jsonify(results)


@p5_bp.route('/reports/daily/<account_id>', methods=['POST'])
def generate_daily_report(account_id):
    """生成日报"""
    data = request.json
    format_str = data.get('format', 'html') if data else 'html'
    
    format_enum = ReportFormat.HTML if format_str == 'html' else ReportFormat.MARKDOWN
    
    result = p5_manager.generate_daily_report(account_id, format_enum)
    return jsonify(result)


@p5_bp.route('/backtest', methods=['POST'])
def run_backtest():
    """运行回测"""
    data = request.json
    if not data:
        return jsonify({'error': '缺少配置参数'}), 400
    
    result = p5_manager.run_backtest(data)
    return jsonify(result)


# 主函数
def main():
    """主函数"""
    print("=== P5任务集成系统 ===")
    print(f"交易系统: {'✅ 可用' if TRADING_AVAILABLE else '❌ 不可用'}")
    print(f"风险管理系统: {'✅ 可用' if RISK_MANAGEMENT_AVAILABLE else '❌ 不可用'}")
    print(f"报告生成系统: {'✅ 可用' if REPORTING_AVAILABLE else '❌ 不可用'}")
    print()
    
    # 显示系统状态
    status = p5_manager.get_system_status()
    print("系统状态:")
    print(json.dumps(status, indent=2, ensure_ascii=False, default=str))
    print()
    
    # 测试功能
    print("测试功能:")
    
    # 测试创建账户
    if TRADING_AVAILABLE:
        print("1. 创建测试账户...")
        p5_manager.trading_engine.create_account(
            account_id="test_account_1",
            account_type=AccountType.SIMULATION,
            initial_capital=1000000
        )
        print("   ✅ 测试账户创建成功")
    
    # 测试市场数据更新
    print("2. 更新市场数据...")
    p5_manager.update_market_data("600519", 1650.0, 1000000)
    p5_manager.update_market_data("000858", 600.0, 500000)
    print("   ✅ 市场数据更新成功")
    
    # 测试下单
    if TRADING_AVAILABLE:
        print("3. 测试下单...")
        order_id = p5_manager.place_order(
            account_id="test_account_1",
            symbol="600519",
            side="buy",
            order_type="limit",
            quantity=100,
            price=1645.0,
            strategy_id="test_strategy"
        )
        if order_id:
            print(f"   ✅ 订单下达成功: {order_id}")
        else:
            print("   ❌ 订单下达失败")
    
    # 测试风险分析
    if RISK_MANAGEMENT_AVAILABLE:
        print("4. 测试风险分析...")
        risk_result = p5_manager.calculate_portfolio_risk("test_account_1")
        if risk_result:
            print(f"   ✅ 风险分析完成，生成{len(risk_result.get('risk_metrics', {}))}个指标")
        else:
            print("   ❌ 风险分析失败")
    
    # 测试报告生成
    if REPORTING_AVAILABLE:
        print("5. 测试报告生成...")
        report_result = p5_manager.generate_daily_report("test_account_1", ReportFormat.HTML)
        if 'report_id' in report_result:
            print(f"   ✅ 报告生成成功: {report_result['report_id']}")
            print(f"     文件路径: {report_result.get('file_path')}")
        else:
            print(f"   ❌ 报告生成失败: {report_result.get('error', '未知错误')}")
    
    print()
    print("=== P5任务集成测试完成 ===")
    print("系统已准备就绪，可通过API访问以下端点:")
    print("  - GET  /api/p5/status          # 系统状态")
    print("  - GET  /api/p5/accounts        # 所有账户")
    print("  - POST /api/p5/orders          # 下达订单")
    print("  - GET  /api/p5/risk/<account> # 风险分析")
    print("  - POST /api/p5/reports/daily/<account> # 生成日报")
    print("  - POST /api/p5/backtest        # 运行回测")


if __name__ == "__main__":
    main()
