#!/usr/bin/env python3
"""
P5系统集成测试
测试实时交易接口、风险管理系统、报告生成系统的集成功能
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
import json

def test_trading_system():
    """测试交易系统"""
    print("=== 测试交易系统 ===")
    
    try:
        from quant_system.trading.core import (
            TradingEngine, TradingAccount, Order, OrderType, OrderSide,
            OrderStatus, AccountType, SimulationDataFeed, TradingStrategy
        )
        from typing import Dict, Any
        
        # 创建交易引擎
        data_feed = SimulationDataFeed()
        trading_engine = TradingEngine(data_feed)
        
        # 创建账户
        trading_engine.create_account(
            account_id="test_trading_account",
            account_type=AccountType.SIMULATION,
            initial_capital=1000000
        )
        
        # 注册一个测试策略（因为place_order需要策略）
        from quant_system.trading.core import TradingStrategy
        
        class TestStrategy(TradingStrategy):
            def __init__(self, strategy_id: str, trading_engine):
                super().__init__(strategy_id, trading_engine)
            
            def on_market_data(self, symbol: str, data: Dict[str, Any]):
                pass
            
            def on_order_update(self, order: Order):
                pass
        
        trading_engine.register_strategy("test_strategy", TestStrategy("test_strategy", trading_engine))
        
        # 更新市场数据
        trading_engine.update_market_data({
            "600519": {
                'price': 1650.0,
                'timestamp': datetime.now(),
                'volume': 1000000,
                'bid': 1649.5,
                'ask': 1650.5
            }
        })
        
        # 下达订单
        order_id = trading_engine.place_order(
            strategy_id="test_strategy",
            symbol="600519",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100,
            price=1649.0,
            account_id="test_trading_account"
        )
        
        if order_id:
            print(f"✅ 订单下达成功: {order_id}")
        else:
            print("❌ 订单下达失败")
            return False
        
        # 获取账户摘要
        account = trading_engine.get_account("test_trading_account")
        summary = account.get_account_summary()
        print(f"✅ 账户摘要获取成功:")
        print(f"   总资产: {summary.get('total_assets', 0):,.2f}")
        print(f"   现金: {summary.get('cash', 0):,.2f}")
        print(f"   持仓数量: {summary.get('positions_count', 0)}")
        
        return True
        
    except Exception as e:
        print(f"❌ 交易系统测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_risk_management():
    """测试风险管理系统"""
    print("\n=== 测试风险管理系统 ===")
    
    try:
        from quant_system.risk_management.core import (
            RiskManager, PortfolioRiskAnalyzer, RiskMonitor,
            RiskMetricType, AlertType
        )
        import pandas as pd
        import numpy as np
        
        # 创建风险管理器
        risk_manager = RiskManager()
        
        # 准备测试数据
        positions = {
            "600519": {'market_value': 165000, 'weight': 0.4, 'quantity': 100},
            "000858": {'market_value': 120000, 'weight': 0.3, 'quantity': 200},
            "000333": {'market_value': 90000, 'weight': 0.2, 'quantity': 300},
            "000001": {'market_value': 75000, 'weight': 0.1, 'quantity': 500}
        }
        
        # 生成历史收益数据
        dates = pd.date_range(end=datetime.now(), periods=100, freq='D')
        returns_data = {}
        for symbol in positions.keys():
            returns = np.random.normal(0, 0.02, len(dates))
            returns_data[symbol] = returns
        
        historical_returns = pd.DataFrame(returns_data, index=dates)
        
        # 计算风险指标
        risk_metrics, alerts = risk_manager.update_portfolio_risk(
            positions, historical_returns
        )
        
        print(f"✅ 风险计算完成: {len(risk_metrics)}个指标")
        
        # 显示部分风险指标
        if risk_metrics:
            for i, (metric_type, metric) in enumerate(list(risk_metrics.items())[:3]):
                print(f"   {metric_type.value}: {metric.value:.4f}")
        
        # 检查告警
        if alerts:
            print(f"⚠️  发现告警: {len(alerts)}个")
            for alert in alerts[:2]:
                print(f"   {alert.message}")
        else:
            print("✅ 无风险告警")
        
        return True
        
    except Exception as e:
        print(f"❌ 风险管理系统测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_reporting_system():
    """测试报告生成系统"""
    print("\n=== 测试报告生成系统 ===")
    
    try:
        from quant_system.reporting.core import (
            ReportGenerator, ReportType, ReportFormat,
            ChartGenerator, ReportScheduler
        )
        
        # 创建报告生成器
        report_generator = ReportGenerator()
        
        # 准备测试数据
        test_data = {
            'report_title': '测试报告',
            'portfolio_summary': [
                {'label': '总资产', 'value': '1,234,567', 'change': '+2.34%', 'positive': True},
                {'label': '现金', 'value': '234,567', 'change': '-1.23%', 'negative': True},
                {'label': '当日盈亏', 'value': '+23,456', 'change': '+1.89%', 'positive': True}
            ],
            'performance_metrics': [
                {'name': '年化收益', 'value': '15.23%', 'benchmark': '8.45%', 'ranking': '1/10', 'change': '+2.34%', 'positive': True},
                {'name': '夏普比率', 'value': '1.85', 'benchmark': '1.20', 'ranking': '2/10', 'change': '+0.12', 'positive': True}
            ]
        }
        
        # 生成HTML报告
        html_report = report_generator.generate_report(
            ReportType.DAILY, test_data, ReportFormat.HTML
        )
        
        print(f"✅ HTML报告生成成功:")
        print(f"   报告ID: {html_report.report_id}")
        print(f"   文件路径: {html_report.file_path}")
        print(f"   文件大小: {html_report.file_size}字节")
        
        # 生成Markdown报告
        md_report = report_generator.generate_report(
            ReportType.DAILY, test_data, ReportFormat.MARKDOWN
        )
        
        print(f"✅ Markdown报告生成成功:")
        print(f"   报告ID: {md_report.report_id}")
        print(f"   文件大小: {md_report.file_size}字节")
        
        return True
        
    except Exception as e:
        print(f"❌ 报告生成系统测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_p5_integration():
    """测试P5集成系统"""
    print("\n=== 测试P5集成系统 ===")
    
    try:
        from quant_system.p5_integration import p5_manager
        
        # 获取系统状态
        status = p5_manager.get_system_status()
        
        print(f"✅ 系统状态获取成功:")
        print(f"   交易系统: {'✅ 可用' if status['components']['trading']['available'] else '❌ 不可用'}")
        print(f"   风险管理系统: {'✅ 可用' if status['components']['risk_management']['available'] else '❌ 不可用'}")
        print(f"   报告生成系统: {'✅ 可用' if status['components']['reporting']['available'] else '❌ 不可用'}")
        
        # 测试创建账户
        from quant_system.trading.core import AccountType
        
        p5_manager.trading_engine.create_account(
            account_id="p5_test_account",
            account_type=AccountType.SIMULATION,
            initial_capital=500000
        )
        
        print("✅ 集成账户创建成功")
        
        # 测试市场数据更新
        p5_manager.update_market_data("600519", 1650.0, 1000000)
        p5_manager.update_market_data("000858", 600.0, 500000)
        
        print("✅ 集成市场数据更新成功")
        
        # 测试风险计算
        risk_result = p5_manager.calculate_portfolio_risk("p5_test_account")
        
        if risk_result and 'risk_metrics' in risk_result:
            print(f"✅ 集成风险计算成功: {len(risk_result['risk_metrics'])}个指标")
        else:
            print("❌ 集成风险计算失败")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ P5集成系统测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """主测试函数"""
    print("=" * 60)
    print("P5系统集成测试")
    print("=" * 60)
    
    test_results = []
    
    # 运行所有测试
    test_results.append(("交易系统", test_trading_system()))
    test_results.append(("风险管理系统", test_risk_management()))
    test_results.append(("报告生成系统", test_reporting_system()))
    test_results.append(("P5集成系统", test_p5_integration()))
    
    # 统计结果
    print("\n" + "=" * 60)
    print("测试结果统计")
    print("=" * 60)
    
    passed = 0
    total = len(test_results)
    
    for test_name, result in test_results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{test_name:20} {status}")
        if result:
            passed += 1
    
    print("\n" + "=" * 60)
    success_rate = (passed / total) * 100
    print(f"测试通过率: {passed}/{total} ({success_rate:.1f}%)")
    
    if passed == total:
        print("🎉 所有测试通过！P5系统集成成功！")
        return True
    else:
        print("⚠️  部分测试失败，请检查系统配置")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)