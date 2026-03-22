#!/usr/bin/env python3
"""
P2任务集成测试
测试真实市值数据获取 + OrderBookSimulator统计模块的完整集成
"""

import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import warnings
warnings.filterwarnings('ignore')

sys.path.append('/root/.openclaw/workspace/quant_system')

class P2IntegrationTest:
    """P2任务集成测试"""
    
    def __init__(self):
        self.results = {}
        self.test_start_time = datetime.now()
        
    def log(self, message: str):
        """记录日志"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        print(f"[{timestamp}] {message}")
    
    def test_market_cap_fetcher(self):
        """测试市值数据获取器"""
        self.log("\n测试市值数据获取器...")
        
        try:
            from utils.market_cap_fetcher import MarketCapFetcher
            
            fetcher = MarketCapFetcher()
            
            # 测试股票列表
            test_cases = [
                {'symbol': '600519', 'price': 200.0, 'name': '茅台'},
                {'symbol': '000858', 'price': 150.0, 'name': '五粮液'},
                {'symbol': '300750', 'price': 180.0, 'name': '宁德时代'},
                {'symbol': '000725', 'price': 25.0, 'name': '京东方'},
                {'symbol': '002475', 'price': 45.0, 'name': '立讯精密'}
            ]
            
            all_passed = True
            for case in test_cases:
                try:
                    market_cap_data = fetcher.get_market_cap(case['symbol'], case['price'])
                    
                    # 验证数据
                    valid_checks = []
                    valid_checks.append(('总市值', market_cap_data['total_market_cap'] > 0))
                    valid_checks.append(('数据来源', market_cap_data['data_source'] in ['baostock', 'estimated', 'cache']))
                    valid_checks.append(('缓存状态', isinstance(market_cap_data['cached'], bool)))
                    
                    # 检查ST状态
                    st_status = fetcher.check_st_status(case['symbol'])
                    valid_checks.append(('ST状态', isinstance(st_status['is_st'], bool)))
                    
                    # 打印结果
                    self.log(f"  {case['name']} ({case['symbol']}):")
                    self.log(f"    总市值: {market_cap_data['total_market_cap']:.1f}亿元")
                    self.log(f"    流通市值: {market_cap_data['float_market_cap']:.1f}亿元")
                    self.log(f"    数据来源: {market_cap_data['data_source']}")
                    self.log(f"    是否缓存: {market_cap_data['cached']}")
                    self.log(f"    ST状态: {st_status['is_st']} (来源: {st_status['data_source']})")
                    
                    # 验证所有检查
                    for check_name, check_result in valid_checks:
                        if not check_result:
                            self.log(f"    ❌ {check_name}检查失败")
                            all_passed = False
                        else:
                            self.log(f"    ✅ {check_name}检查通过")
                    
                except Exception as e:
                    self.log(f"  ❌ {case['name']}测试失败: {e}")
                    all_passed = False
            
            self.results['market_cap_fetcher'] = all_passed
            return all_passed
            
        except Exception as e:
            self.log(f"  ❌ 市值数据获取器测试失败: {e}")
            import traceback
            traceback.print_exc()
            self.results['market_cap_fetcher'] = False
            return False
    
    def test_liquidity_calculator_with_real_data(self):
        """测试带真实数据的流动性计算器"""
        self.log("\n测试带真实数据的流动性计算器...")
        
        try:
            from utils.liquidity_calculator import LiquidityCalculator
            
            # 创建测试数据
            dates = pd.date_range(start='2024-01-01', periods=30, freq='D')
            np.random.seed(42)
            
            test_df = pd.DataFrame({
                'close': np.cumprod(1 + np.random.randn(30) * 0.02) * 100,
                'volume': np.random.randint(1000000, 5000000, 30),
                'open': np.random.randn(30) * 0.01 + 100,
                'high': np.random.randn(30) * 0.02 + 102,
                'low': np.random.randn(30) * 0.02 + 98
            }, index=dates)
            
            # 测试带真实数据的计算器
            calculator = LiquidityCalculator(use_real_data=True)
            
            # 测试各种方法
            tests = []
            
            # 1. ADV计算
            adv = calculator.calculate_adv_from_prices(test_df, window=20)
            tests.append(('ADV计算', adv > 0))
            
            # 2. 市值估算（使用真实数据）
            market_cap = calculator.estimate_market_cap('600519', test_df)
            tests.append(('市值估算', market_cap > 0))
            
            # 3. ST状态检查
            is_st = calculator.check_st_status('600519')
            tests.append(('ST状态检查', isinstance(is_st, bool)))
            
            # 4. 换手率计算
            turnover = calculator.calculate_daily_turnover('600519', test_df)
            tests.append(('换手率计算', turnover >= 0))
            
            # 5. 完整流动性数据
            liquidity_data = calculator.get_liquidity_data('600519', test_df)
            tests.append(('完整流动性数据', liquidity_data['data_source'] in ['real', 'estimated', 'default']))
            
            # 打印结果
            self.log(f"  ADV计算: {adv:.1f}万元")
            self.log(f"  市值估算: {market_cap:.1f}亿元")
            self.log(f"  ST状态: {is_st}")
            self.log(f"  换手率: {turnover:.2f}%")
            self.log(f"  完整数据: ADV={liquidity_data['adv_20d']:.1f}万, "
                   f"市值={liquidity_data['market_cap']:.1f}亿, "
                   f"来源={liquidity_data['data_source']}")
            
            # 验证测试
            all_passed = True
            for test_name, test_result in tests:
                if test_result:
                    self.log(f"    ✅ {test_name}通过")
                else:
                    self.log(f"    ❌ {test_name}失败")
                    all_passed = False
            
            self.results['liquidity_calculator_real'] = all_passed
            return all_passed
            
        except Exception as e:
            self.log(f"  ❌ 流动性计算器测试失败: {e}")
            import traceback
            traceback.print_exc()
            self.results['liquidity_calculator_real'] = False
            return False
    
    def test_orderbook_stats_module(self):
        """测试OrderBook统计模块"""
        self.log("\n测试OrderBook统计模块...")
        
        try:
            from utils.orderbook_stats import OrderBookStats
            
            stats = OrderBookStats()
            
            # 模拟一些订单数据
            test_orders = []
            np.random.seed(42)
            
            symbols = ['600519', '000858', '300750', '000725', '002475']
            
            for i in range(20):
                symbol = np.random.choice(symbols)
                order_side = np.random.choice(['buy', 'sell'])
                
                # 模拟不同流动性分桶
                if symbol in ['600519', '000858', '300750']:
                    liquidity_bucket = np.random.choice([1, 2, 3])
                    impact_bps = np.random.uniform(5, 50)
                else:
                    liquidity_bucket = np.random.choice([5, 6, 7, 8])
                    impact_bps = np.random.uniform(50, 200)
                
                # 模拟订单结果
                order_result = {
                    'execution_status': np.random.choice(['fully_executed', 'partially_executed', 'rejected'], p=[0.7, 0.2, 0.1]),
                    'impact_cost_bps': impact_bps,
                    'executed_quantity': np.random.randint(100, 10000),
                    'requested_quantity': np.random.randint(1000, 20000),
                    'avg_execution_price': np.random.uniform(10, 200),
                    'target_price': np.random.uniform(10, 200),
                    'total_impact': impact_bps * np.random.uniform(0.1, 1.0),
                    'metadata': {
                        'adv': np.random.uniform(1000, 50000),
                        'market_cap': np.random.uniform(50, 2000),
                        'liquidity_bucket': liquidity_bucket
                    }
                }
                
                test_orders.append({
                    'order_result': order_result,
                    'symbol': symbol,
                    'order_side': order_side,
                    'liquidity_bucket': liquidity_bucket,
                    'market_regime': np.random.choice(['NORMAL', 'BEAR', 'BULL'])
                })
            
            # 记录订单
            for order in test_orders:
                stats.record_order(**order)
            
            # 保存统计
            stats.save_stats()
            
            # 获取摘要
            summary = stats.get_summary()
            
            # 打印结果
            self.log(f"  总调用次数: {summary['total_calls']}")
            self.log(f"  买入次数: {summary['buy_calls']}")
            self.log(f"  卖出次数: {summary['sell_calls']}")
            self.log(f"  执行成功率: {summary['execution_rate_pct']:.1f}%")
            self.log(f"  平均冲击成本: {summary['avg_impact_cost_bps']:.2f} bp")
            
            # 检查冲击成本统计
            if summary['impact_cost_stats']['count'] > 0:
                self.log(f"  冲击成本分布: 均值={summary['impact_cost_stats']['mean']:.2f}bp, "
                       f"标准差={summary['impact_cost_stats']['std']:.2f}bp")
            
            # 检查流动性分桶分布
            if summary['liquidity_bucket_distribution']:
                self.log(f"  流动性分桶分布:")
                for bucket, count in summary['liquidity_bucket_distribution'].items():
                    self.log(f"    桶{bucket}: {count}次")
            
            # 生成HTML报告
            report_file = stats.generate_report()
            self.log(f"  HTML报告已生成: {report_file}")
            
            # 验证测试
            tests = []
            tests.append(('总调用次数', summary['total_calls'] == 20))
            tests.append(('冲击成本统计', summary['impact_cost_stats']['count'] > 0))
            tests.append(('流动性分桶', len(summary['liquidity_bucket_distribution']) > 0))
            
            all_passed = True
            for test_name, test_result in tests:
                if test_result:
                    self.log(f"    ✅ {test_name}通过")
                else:
                    self.log(f"    ❌ {test_name}失败")
                    all_passed = False
            
            self.results['orderbook_stats'] = all_passed
            return all_passed
            
        except Exception as e:
            self.log(f"  ❌ OrderBook统计模块测试失败: {e}")
            import traceback
            traceback.print_exc()
            self.results['orderbook_stats'] = False
            return False
    
    def test_vectorized_backtest_integration(self):
        """测试vectorized_backtest集成"""
        self.log("\n测试vectorized_backtest集成...")
        
        try:
            from enhancements.vectorized_backtest import VectorizedBacktester, BacktestConfig
            
            # 创建测试配置（启用高级滑点模型和OrderBook统计）
            config = BacktestConfig(
                use_advanced_slippage=True,
                initial_capital=1000000,
                max_position_pct=0.05,
                slippage_rate=0.002,
                adv_threshold=3000.0,
                market_cap_threshold=30.0,
                filter_low_liquidity=True,
                volume_percentage_limit=0.05
            )
            
            # 创建回测器
            backtester = VectorizedBacktester(config)
            
            # 验证回测器初始化
            tests = []
            
            # 检查组件初始化
            if hasattr(backtester, 'order_book_simulator'):
                tests.append(('OrderBookSimulator', backtester.order_book_simulator is not None))
            
            if hasattr(backtester, 'orderbook_stats'):
                tests.append(('OrderBookStats', backtester.orderbook_stats is not None))
            
            if hasattr(backtester, 'liquidity_enforcer'):
                tests.append(('LiquidityEnforcer', backtester.liquidity_enforcer is not None))
            
            # 打印初始化状态
            self.log(f"  回测器初始化状态:")
            for component_name, status in tests:
                if status:
                    self.log(f"    ✅ {component_name}: 已初始化")
                else:
                    self.log(f"    ❌ {component_name}: 未初始化")
            
            # 创建测试数据
            dates = pd.date_range(start='2024-01-01', periods=100, freq='D')
            np.random.seed(42)
            
            test_df = pd.DataFrame({
                'open': np.cumprod(1 + np.random.randn(100) * 0.02) * 100,
                'high': np.cumprod(1 + np.random.randn(100) * 0.025) * 102,
                'low': np.cumprod(1 + np.random.randn(100) * 0.025) * 98,
                'close': np.cumprod(1 + np.random.randn(100) * 0.02) * 100,
                'volume': np.random.randint(1000000, 5000000, 100)
            }, index=dates)
            
            # 创建简单交易信号
            signals = pd.Series(0, index=dates)
            # 每隔20天有一个买入信号
            for i in range(0, len(dates), 20):
                if i < len(dates):
                    signals.iloc[i] = 1
            # 每隔25天有一个卖出信号
            for i in range(10, len(dates), 25):
                if i < len(dates):
                    signals.iloc[i] = -1
            
            # 准备流动性数据
            from utils.liquidity_calculator import LiquidityCalculator
            calculator = LiquidityCalculator(use_real_data=True)
            liquidity_data = calculator.get_liquidity_data('600519', test_df)
            
            # 运行回测
            self.log(f"  运行回测测试...")
            try:
                result = backtester.run_vectorized_backtest(
                    symbol='600519',
                    prices=test_df,
                    signals=signals,
                    liquidity_data=liquidity_data
                )
                
                # 检查结果
                tests.append(('回测执行', result.total_trades > 0))
                tests.append(('投资组合价值', len(result.portfolio_values) == len(dates)))
                
                self.log(f"  回测结果:")
                self.log(f"    总收益: {result.total_return*100:.2f}%")
                self.log(f"    交易次数: {result.total_trades}")
                self.log(f"    最大回撤: {result.max_drawdown*100:.2f}%")
                self.log(f"    夏普比率: {result.sharpe_ratio:.2f}")
                
            except Exception as e:
                self.log(f"    ❌ 回测执行失败: {e}")
                tests.append(('回测执行', False))
            
            # 验证测试
            all_passed = True
            for test_name, test_result in tests:
                if test_result:
                    self.log(f"    ✅ {test_name}通过")
                else:
                    self.log(f"    ❌ {test_name}失败")
                    all_passed = False
            
            self.results['vectorized_backtest_integration'] = all_passed
            return all_passed
            
        except Exception as e:
            self.log(f"  ❌ vectorized_backtest集成测试失败: {e}")
            import traceback
            traceback.print_exc()
            self.results['vectorized_backtest_integration'] = False
            return False
    
    def generate_final_report(self):
        """生成最终测试报告"""
        self.log("\n" + "=" * 70)
        self.log("P2任务集成测试 - 最终报告")
        self.log("=" * 70)
        
        test_duration = (datetime.now() - self.test_start_time).total_seconds()
        
        # 统计测试结果
        total_tests = len(self.results)
        passed_tests = sum(1 for result in self.results.values() if result is True)
        
        self.log(f"测试总结:")
        self.log(f"  总测试数: {total_tests}")
        self.log(f"  通过数: {passed_tests}")
        self.log(f"  失败数: {total_tests - passed_tests}")
        self.log(f"  测试时长: {test_duration:.1f}秒")
        
        self.log(f"\n详细结果:")
        for test_name, result in self.results.items():
            status = "✅ 通过" if result else "❌ 失败"
            self.log(f"  {test_name}: {status}")
        
        # 生成JSON报告
        report_data = {
            'test_timestamp': self.test_start_time.isoformat(),
            'test_duration_seconds': test_duration,
            'results': self.results,
            'summary': {
                'total_tests': total_tests,
                'passed_tests': passed_tests,
                'success_rate': passed_tests / total_tests if total_tests > 0 else 0
            }
        }
        
        # 保存报告
        report_file = f"p2_integration_report_{self.test_start_time.strftime('%Y%m%d_%H%M%S')}.json"
        import json
        with open(report_file, 'w') as f:
            json.dump(report_data, f, indent=2, default=str)
        
        self.log(f"\n详细报告已保存至: {report_file}")
        
        # 总体结论
        success_rate = passed_tests / total_tests if total_tests > 0 else 0
        if success_rate >= 0.8:
            self.log(f"\n🎉 P2任务集成测试通过! 成功率: {success_rate*100:.1f}%")
            self.log("真实市值数据获取 + OrderBookSimulator统计模块已成功集成!")
        elif success_rate >= 0.5:
            self.log(f"\n⚠️  P2任务集成测试部分通过。成功率: {success_rate*100:.1f}%")
            self.log("建议检查失败的项目后再部署生产环境。")
        else:
            self.log(f"\n❌ P2任务集成测试失败。成功率: {success_rate*100:.1f}%")
            self.log("需要修复失败的项目后才能继续P2任务。")
        
        return success_rate >= 0.8
    
    def run_all_tests(self):
        """运行所有测试"""
        self.log("=" * 70)
        self.log("开始P2任务集成测试")
        self.log("=" * 70)
        
        # 1. 测试市值数据获取器
        self.test_market_cap_fetcher()
        
        # 2. 测试带真实数据的流动性计算器
        self.test_liquidity_calculator_with_real_data()
        
        # 3. 测试OrderBook统计模块
        self.test_orderbook_stats_module()
        
        # 4. 测试vectorized_backtest集成
        self.test_vectorized_backtest_integration()
        
        # 5. 生成最终报告
        return self.generate_final_report()


def main():
    """主测试函数"""
    print("P2任务集成测试")
    print("=" * 70)
    
    try:
        tester = P2IntegrationTest()
        success = tester.run_all_tests()
        
        if success:
            print("\n🚀 P2任务测试完成，所有组件已成功集成!")
            return 0
        else:
            print("\n⚠️  P2任务测试完成，但存在一些问题需要修复。")
            return 1
            
    except Exception as e:
        print(f"\n❌ P2任务测试异常终止: {e}")
        import traceback
        traceback.print_exc()
        return 2


if __name__ == '__main__':
    exit_code = main()
    exit(exit_code)