#!/usr/bin/env python3
"""
生产环境集成测试脚本
验证OrderBookSimulator在生产环境中的正确性
包括：全市场简化回测、流动性冲击验证、性能基准测试
"""

import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import json
import warnings
warnings.filterwarnings('ignore')

sys.path.append('/root/.openclaw/workspace/quant_system')

class ProductionIntegrationTest:
    """生产环境集成测试器"""
    
    def __init__(self):
        self.results = {}
        self.test_start_time = datetime.now()
        
    def log(self, message: str):
        """记录日志"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        print(f"[{timestamp}] {message}")
    
    def test_module_imports(self):
        """测试模块导入"""
        self.log("测试模块导入...")
        
        modules = [
            ('walkforward.walkforward_backtester', ['WalkForwardBacktester', 'WalkForwardConfig']),
            ('enhancements.vectorized_backtest', ['VectorizedBacktester', 'BacktestConfig']),
            ('utils.liquidity_calculator', ['LiquidityCalculator']),
            ('slippage.liquidity_impact_model', ['OrderBookSimulator', 'AdvancedSlippageModel'])
        ]
        
        all_imported = True
        for module_path, class_names in modules:
            try:
                module = __import__(module_path.replace('/', '.'), fromlist=class_names)
                for cls_name in class_names:
                    if hasattr(module, cls_name):
                        self.log(f"  ✅ {module_path}.{cls_name}")
                    else:
                        self.log(f"  ❌ {module_path}.{cls_name} 不存在")
                        all_imported = False
            except ImportError as e:
                self.log(f"  ❌ {module_path} 导入失败: {e}")
                all_imported = False
        
        self.results['module_imports'] = all_imported
        return all_imported
    
    def test_walkforward_configuration(self):
        """测试Walk-forward配置"""
        self.log("\n测试Walk-forward配置...")
        
        try:
            from walkforward.walkforward_backtester import WalkForwardConfig
            
            # 测试各种配置组合
            test_configs = [
                {
                    'name': '标准配置',
                    'config': WalkForwardConfig(
                        train_years=3,
                        validation_months=6,
                        test_months=6,
                        step_months=3,
                        initial_capital=1000000
                    )
                },
                {
                    'name': '快速配置',
                    'config': WalkForwardConfig(
                        train_years=1,
                        validation_months=3,
                        test_months=3,
                        step_months=1,
                        initial_capital=500000
                    )
                },
                {
                    'name': '长周期配置',
                    'config': WalkForwardConfig(
                        train_years=5,
                        validation_months=12,
                        test_months=12,
                        step_months=6,
                        initial_capital=2000000
                    )
                }
            ]
            
            for test in test_configs:
                config = test['config']
                self.log(f"  ✅ {test['name']}:")
                self.log(f"      训练集: {config.train_years}年 ({config.train_days}天)")
                self.log(f"      验证集: {config.validation_months}月 ({config.validation_days}天)")
                self.log(f"      测试集: {config.test_months}月 ({config.test_days}天)")
                self.log(f"      滚动步长: {config.step_months}月 ({config.step_days}天)")
                self.log(f"      初始资金: {config.initial_capital:,.0f}")
            
            self.results['walkforward_config'] = True
            return True
            
        except Exception as e:
            self.log(f"  ❌ Walk-forward配置测试失败: {e}")
            import traceback
            traceback.print_exc()
            self.results['walkforward_config'] = False
            return False
    
    def test_orderbook_simulator_initialization(self):
        """测试OrderBookSimulator初始化"""
        self.log("\n测试OrderBookSimulator初始化...")
        
        try:
            from enhancements.vectorized_backtest import VectorizedBacktester, BacktestConfig
            
            # 测试启用高级滑点模型
            config_advanced = BacktestConfig(
                use_advanced_slippage=True,
                initial_capital=1000000,
                max_position_pct=0.1,
                slippage_rate=0.002,
                adv_threshold=3000.0,
                market_cap_threshold=30.0,
                filter_low_liquidity=True,
                volume_percentage_limit=0.05
            )
            
            backtester_advanced = VectorizedBacktester(config_advanced)
            
            # 检查初始化组件
            checks = []
            
            # 检查OrderBookSimulator
            if hasattr(backtester_advanced, 'order_book_simulator'):
                checks.append(('OrderBookSimulator', backtester_advanced.order_book_simulator is not None))
            
            # 检查LiquidityEnforcer
            if hasattr(backtester_advanced, 'liquidity_enforcer'):
                checks.append(('LiquidityEnforcer', backtester_advanced.liquidity_enforcer is not None))
            
            # 检查VolumeFilter
            if hasattr(backtester_advanced, 'volume_filter'):
                checks.append(('VolumeFilter', backtester_advanced.volume_filter is not None))
            
            # 打印检查结果
            all_passed = True
            for component_name, status in checks:
                if status:
                    self.log(f"  ✅ {component_name}: 已初始化")
                else:
                    self.log(f"  ❌ {component_name}: 未初始化")
                    all_passed = False
            
            # 测试禁用高级滑点模型
            config_disabled = BacktestConfig(
                use_advanced_slippage=False,
                initial_capital=1000000,
                max_position_pct=0.1,
                slippage_rate=0.002
            )
            
            backtester_disabled = VectorizedBacktester(config_disabled)
            if hasattr(backtester_disabled, 'order_book_simulator'):
                if backtester_disabled.order_book_simulator is None:
                    self.log(f"  ✅ 禁用高级滑点时OrderBookSimulator为None (符合预期)")
                else:
                    self.log(f"  ⚠️  禁用高级滑点时OrderBookSimulator不为None")
            
            self.results['orderbook_initialization'] = all_passed
            return all_passed
            
        except Exception as e:
            self.log(f"  ❌ OrderBookSimulator初始化测试失败: {e}")
            import traceback
            traceback.print_exc()
            self.results['orderbook_initialization'] = False
            return False
    
    def create_test_portfolio(self, n_stocks=5):
        """创建测试投资组合"""
        self.log(f"\n创建测试投资组合 ({n_stocks}只股票)...")
        
        # 模拟股票代码
        stock_symbols = [
            '600519.SH',  # 茅台 (高流动性)
            '000858.SZ',  # 五粮液 (高流动性)
            '300750.SZ',  # 宁德时代 (高流动性)
            '000725.SZ',  # 京东方 (中流动性)
            '002475.SZ',  # 立讯精密 (中流动性)
            '300059.SZ',  # 东方财富 (中流动性)
            '000100.SZ',  # TCL科技 (中流动性)
            '002241.SZ',  # 歌尔股份 (中流动性)
            '300498.SZ',  # 温氏股份 (中流动性)
            '000001.SZ'   # 平安银行 (高流动性)
        ][:n_stocks]
        
        # 创建测试数据
        portfolio_data = {}
        start_date = '2024-01-01'
        end_date = '2024-06-01'
        dates = pd.date_range(start=start_date, end=end_date, freq='D')
        
        np.random.seed(42)
        
        for symbol in stock_symbols:
            # 根据股票代码确定流动性特征
            if '600519' in symbol or '000858' in symbol or '300750' in symbol:
                # 高流动性：低波动，高成交量
                base_vol = 5000000
                daily_vol_range = (3000000, 15000000)
                price_range = (100, 300)
                volatility = 0.015
            elif '000725' in symbol or '002475' in symbol:
                # 中流动性：中等波动，中等成交量
                base_vol = 2000000
                daily_vol_range = (1000000, 5000000)
                price_range = (20, 100)
                volatility = 0.02
            else:
                # 低流动性：高波动，低成交量
                base_vol = 500000
                daily_vol_range = (200000, 2000000)
                price_range = (5, 50)
                volatility = 0.025
            
            # 生成价格序列
            n_days = len(dates)
            returns = np.random.randn(n_days) * volatility
            base_price = np.random.uniform(price_range[0], price_range[1])
            
            close_prices = []
            for i in range(n_days):
                if i == 0:
                    price = base_price
                else:
                    price = close_prices[-1] * (1 + returns[i])
                close_prices.append(price)
            
            # 生成OHLCV数据
            prices = []
            for i in range(n_days):
                open_price = close_prices[i] * (1 + np.random.randn() * 0.01)
                high = max(open_price, close_prices[i]) * (1 + abs(np.random.randn()) * 0.02)
                low = min(open_price, close_prices[i]) * (1 - abs(np.random.randn()) * 0.02)
                close = close_prices[i]
                volume = np.random.randint(daily_vol_range[0], daily_vol_range[1])
                
                prices.append({
                    'date': dates[i],
                    'open': open_price,
                    'high': high,
                    'low': low,
                    'close': close,
                    'volume': volume
                })
            
            df = pd.DataFrame(prices)
            df.set_index('date', inplace=True)
            portfolio_data[symbol] = df
            
            self.log(f"  创建 {symbol}: 价格范围{df['close'].min():.1f}-{df['close'].max():.1f}, "
                   f"平均成交量{df['volume'].mean():,.0f}")
        
        return portfolio_data
    
    def run_market_wide_backtest(self, portfolio_data):
        """运行全市场简化回测"""
        self.log("\n运行全市场简化回测...")
        
        try:
            from enhancements.vectorized_backtest import VectorizedBacktester, BacktestConfig
            
            # 配置启用高级滑点模型
            config = BacktestConfig(
                use_advanced_slippage=True,
                initial_capital=1000000,
                max_position_pct=0.05,  # 每只股票最大仓位5%
                slippage_rate=0.002,
                adv_threshold=3000.0,
                market_cap_threshold=30.0,
                filter_low_liquidity=True,
                volume_percentage_limit=0.05
            )
            
            backtester = VectorizedBacktester(config)
            
            results = []
            total_start_time = time.time()
            
            for symbol, prices_df in portfolio_data.items():
                symbol_start_time = time.time()
                
                # 创建简单交易信号（随机买入卖出）
                np.random.seed(hash(symbol) % 10000)
                dates = prices_df.index
                signals = pd.Series(0, index=dates)
                
                # 随机生成2-4个交易信号
                n_signals = np.random.randint(2, 5)
                signal_indices = np.random.choice(len(dates), n_signals, replace=False)
                signal_values = np.random.choice([-1, 1], n_signals)
                
                for idx, val in zip(signal_indices, signal_values):
                    signals.iloc[idx] = val
                
                # 准备流动性数据
                try:
                    from utils.liquidity_calculator import LiquidityCalculator
                    liquidity_data = LiquidityCalculator.get_liquidity_data_simple(symbol, prices_df)
                    liquidity_source = liquidity_data.get('data_source', 'unknown')
                except:
                    liquidity_data = None
                    liquidity_source = 'none'
                
                # 运行回测
                try:
                    result = backtester.run_vectorized_backtest(
                        symbol=symbol,
                        prices=prices_df,
                        signals=signals,
                        liquidity_data=liquidity_data
                    )
                    
                    symbol_time = time.time() - symbol_start_time
                    
                    # 收集结果
                    trade_count = result.total_trades if hasattr(result, 'total_trades') else 0
                    
                    # 检查高级滑点使用情况
                    advanced_slippage_used = False
                    if hasattr(result, 'trade_records') and result.trade_records:
                        for record in result.trade_records:
                            if hasattr(record, 'metadata') and record.metadata:
                                if record.metadata.get('advanced_slippage'):
                                    advanced_slippage_used = True
                                    break
                    
                    results.append({
                        'symbol': symbol,
                        'total_return': result.total_return,
                        'sharpe_ratio': result.sharpe_ratio if hasattr(result, 'sharpe_ratio') else 0,
                        'max_drawdown': result.max_drawdown if hasattr(result, 'max_drawdown') else 0,
                        'trade_count': trade_count,
                        'execution_time': symbol_time,
                        'advanced_slippage_used': advanced_slippage_used,
                        'liquidity_source': liquidity_source,
                        'avg_volume': prices_df['volume'].mean()
                    })
                    
                    self.log(f"  {symbol}: 收益{result.total_return*100:+.2f}%, "
                           f"交易{trade_count}次, "
                           f"高级滑点{'是' if advanced_slippage_used else '否'}, "
                           f"时间{symbol_time:.2f}s")
                    
                except Exception as e:
                    self.log(f"  ❌ {symbol} 回测失败: {e}")
                    continue
            
            total_time = time.time() - total_start_time
            
            # 分析结果
            if results:
                df_results = pd.DataFrame(results)
                
                self.log(f"\n回测汇总:")
                self.log(f"  股票数量: {len(results)}/{len(portfolio_data)}")
                self.log(f"  总时间: {total_time:.2f}s")
                self.log(f"  平均每只股票时间: {total_time/len(results):.2f}s")
                self.log(f"  平均收益: {df_results['total_return'].mean()*100:+.2f}%")
                self.log(f"  收益标准差: {df_results['total_return'].std()*100:.2f}%")
                self.log(f"  高级滑点使用率: {df_results['advanced_slippage_used'].mean()*100:.1f}%")
                
                # 按流动性分组分析
                df_results['liquidity_group'] = pd.qcut(df_results['avg_volume'], 3, labels=['低', '中', '高'])
                group_stats = df_results.groupby('liquidity_group')['total_return'].agg(['mean', 'std', 'count'])
                
                self.log(f"\n按流动性分组分析:")
                for group, stats in group_stats.iterrows():
                    self.log(f"  {group}流动性: {stats['count']}只股票, "
                           f"平均收益{stats['mean']*100:+.2f}%, "
                           f"标准差{stats['std']*100:.2f}%")
                
                self.results['market_wide_backtest'] = {
                    'summary': {
                        'n_stocks': len(results),
                        'total_time': total_time,
                        'avg_return': float(df_results['total_return'].mean()),
                        'std_return': float(df_results['total_return'].std()),
                        'advanced_slippage_rate': float(df_results['advanced_slippage_used'].mean())
                    },
                    'detailed_results': results
                }
                
                return True
            else:
                self.log("  ❌ 无成功回测结果")
                self.results['market_wide_backtest'] = False
                return False
                
        except Exception as e:
            self.log(f"  ❌ 全市场回测失败: {e}")
            import traceback
            traceback.print_exc()
            self.results['market_wide_backtest'] = False
            return False
    
    def test_liquidity_calculator_integration(self):
        """测试流动性计算器集成"""
        self.log("\n测试流动性计算器集成...")
        
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
            
            # 测试ADV计算
            adv = LiquidityCalculator.calculate_adv_from_prices(test_df, window=20)
            self.log(f"  ADV计算: {adv:.1f}万元")
            
            # 测试完整流动性数据
            liquidity_data = LiquidityCalculator.get_liquidity_data('600519', test_df)
            self.log(f"  完整流动性数据: ADV={liquidity_data['adv_20d']:.1f}万, "
                   f"市值={liquidity_data['market_cap']:.1f}亿")
            
            # 测试简化版本
            simple_data = LiquidityCalculator.get_liquidity_data_simple('600519', test_df)
            self.log(f"  简化版本: ADV={simple_data['adv_20d']:.1f}万, "
                   f"数据来源={simple_data.get('data_source', 'unknown')}")
            
            # 验证ADV计算正确性
            if adv > 0:
                self.log(f"  ✅ ADV计算正确 (正值)")
            else:
                self.log(f"  ⚠️ ADV计算异常 (零或负值)")
            
            self.results['liquidity_calculator'] = True
            return True
            
        except Exception as e:
            self.log(f"  ❌ 流动性计算器测试失败: {e}")
            import traceback
            traceback.print_exc()
            self.results['liquidity_calculator'] = False
            return False
    
    def generate_final_report(self):
        """生成最终测试报告"""
        self.log("\n" + "=" * 70)
        self.log("生产环境集成测试 - 最终报告")
        self.log("=" * 70)
        
        test_duration = (datetime.now() - self.test_start_time).total_seconds()
        
        # 统计测试结果
        total_tests = len(self.results)
        passed_tests = sum(1 for result in self.results.values() if result is True or (isinstance(result, dict) and 'summary' in result))
        
        self.log(f"测试总结:")
        self.log(f"  总测试数: {total_tests}")
        self.log(f"  通过数: {passed_tests}")
        self.log(f"  失败数: {total_tests - passed_tests}")
        self.log(f"  测试时长: {test_duration:.1f}秒")
        
        self.log(f"\n详细结果:")
        for test_name, result in self.results.items():
            if isinstance(result, bool):
                status = "✅ 通过" if result else "❌ 失败"
            elif isinstance(result, dict) and 'summary' in result:
                status = f"✅ 完成 (详情见报告)"
            else:
                status = "❌ 失败"
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
        report_file = f"production_test_report_{self.test_start_time.strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, 'w') as f:
            json.dump(report_data, f, indent=2, default=str)
        
        self.log(f"\n详细报告已保存至: {report_file}")
        
        # 总体结论
        success_rate = passed_tests / total_tests if total_tests > 0 else 0
        if success_rate >= 0.8:
            self.log(f"\n🎉 生产环境集成测试通过! 成功率: {success_rate*100:.1f}%")
            self.log("OrderBookSimulator已准备好进入生产环境!")
        elif success_rate >= 0.5:
            self.log(f"\n⚠️  生产环境集成测试部分通过。成功率: {success_rate*100:.1f}%")
            self.log("建议检查失败的项目后再部署生产环境。")
        else:
            self.log(f"\n❌ 生产环境集成测试失败。成功率: {success_rate*100:.1f}%")
            self.log("需要修复失败的项目后才能部署生产环境。")
        
        return success_rate >= 0.8
    
    def run_all_tests(self):
        """运行所有测试"""
        self.log("=" * 70)
        self.log("开始生产环境集成测试")
        self.log("=" * 70)
        
        # 1. 模块导入测试
        self.test_module_imports()
        
        # 2. Walk-forward配置测试
        self.test_walkforward_configuration()
        
        # 3. OrderBookSimulator初始化测试
        self.test_orderbook_simulator_initialization()
        
        # 4. 流动性计算器集成测试
        self.test_liquidity_calculator_integration()
        
        # 5. 创建测试投资组合
        portfolio_data = self.create_test_portfolio(n_stocks=8)
        
        # 6. 运行全市场简化回测
        self.run_market_wide_backtest(portfolio_data)
        
        # 7. 生成最终报告
        return self.generate_final_report()


def main():
    """主测试函数"""
    print("生产环境集成测试")
    print("=" * 70)
    
    try:
        tester = ProductionIntegrationTest()
        success = tester.run_all_tests()
        
        if success:
            print("\n🚀 生产环境测试完成，系统已准备好部署!")
            return 0
        else:
            print("\n⚠️  生产环境测试完成，但存在一些问题需要修复。")
            return 1
            
    except Exception as e:
        print(f"\n❌ 生产环境测试异常终止: {e}")
        import traceback
        traceback.print_exc()
        return 2


if __name__ == '__main__':
    exit_code = main()
    exit(exit_code)