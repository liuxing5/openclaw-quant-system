#!/usr/bin/env python3
"""
性能基准测试 - 测量当前系统关键操作耗时
"""

import time
import pandas as pd
import numpy as np
from datetime import datetime
import sys
import os

sys.path.append('/root/.openclaw/workspace/quant_system')

def benchmark_data_acquisition():
    """基准测试：数据采集性能"""
    print("测试数据采集性能...")
    
    try:
        from data.sources.data_pipeline import DataPipeline
        from data.sources.data_adapter import DataAdapter
        
        pipeline = DataPipeline()
        adapter = DataAdapter()
        
        symbols = ['600519', '000001', '300750']
        start_date = '2024-01-01'
        end_date = '2024-01-10'
        
        results = []
        for symbol in symbols:
            start_time = time.time()
            result = adapter.safe_get_stock_data(pipeline, symbol, start_date, end_date)
            elapsed = time.time() - start_time
            
            success = result['success']
            rows = len(result['data']) if result['data'] is not None else 0
            source = result['source']
            
            results.append({
                'symbol': symbol,
                'success': success,
                'time_seconds': elapsed,
                'rows': rows,
                'source': source
            })
            
            print(f"  {symbol}: {elapsed:.2f}s, 成功={success}, 行数={rows}, 数据源={source}")
        
        avg_time = np.mean([r['time_seconds'] for r in results])
        success_rate = sum(1 for r in results if r['success']) / len(results)
        
        return {
            'operation': 'data_acquisition',
            'avg_time_seconds': avg_time,
            'success_rate': success_rate,
            'symbols_tested': len(symbols),
            'details': results
        }
    
    except Exception as e:
        print(f"数据采集性能测试失败: {e}")
        return {'error': str(e)}

def benchmark_sentiment_calculation():
    """基准测试：情绪因子计算性能"""
    print("测试情绪因子计算性能...")
    
    try:
        from advanced_sentiment.refined_sentiment import RefinedSentimentFactor
        
        sentiment_calc = RefinedSentimentFactor()
        
        # 创建模拟数据
        np.random.seed(42)
        dates = pd.date_range(start='2024-01-01', end='2024-01-31', freq='B')
        
        # 模拟市场数据
        market_prices = 3000 * (1 + np.cumsum(np.random.randn(len(dates)) * 0.005))
        market_data = pd.DataFrame({'close': market_prices}, index=dates)
        
        # 模拟股票数据
        stock_data = {}
        symbols = ['TEST1', 'TEST2', 'TEST3']
        
        for symbol in symbols:
            base_price = 100 + hash(symbol) % 50
            prices = base_price * (1 + np.cumsum(np.random.randn(len(dates)) * 0.01))
            volumes = np.random.randint(1e6, 1e7, len(dates))
            
            stock_data[symbol] = pd.DataFrame({
                'close': prices,
                'volume': volumes
            }, index=dates)
        
        # 测试市场状态检测性能
        start_time = time.time()
        market_state, state_params = sentiment_calc.detect_market_state(market_data, '2024-01-31')
        market_state_time = time.time() - start_time
        
        # 测试情绪计算性能
        start_time = time.time()
        sentiment_result = sentiment_calc.calculate_refined_sentiment(
            stock_data=stock_data,
            market_data=market_data,
            current_date='2024-01-31'
        )
        sentiment_time = time.time() - start_time
        
        stocks_count = len(sentiment_result.get('individual_results', {}))
        
        print(f"  市场状态检测: {market_state_time:.2f}s, 状态={market_state}")
        print(f"  情绪因子计算: {sentiment_time:.2f}s, 股票数={stocks_count}")
        
        return {
            'operation': 'sentiment_calculation',
            'market_state_time_seconds': market_state_time,
            'sentiment_time_seconds': sentiment_time,
            'total_time_seconds': market_state_time + sentiment_time,
            'stocks_count': stocks_count,
            'market_state': market_state
        }
    
    except Exception as e:
        print(f"情绪因子计算性能测试失败: {e}")
        return {'error': str(e)}

def benchmark_risk_calculation():
    """基准测试：风险计算性能"""
    print("测试风险计算性能...")
    
    try:
        from advanced_risk.advanced_risk_manager import AdvancedRiskManager
        
        risk_manager = AdvancedRiskManager()
        
        # 创建模拟组合
        portfolio = {
            'TEST1': 0.3,
            'TEST2': 0.2,
            'TEST3': 0.25,
            'TEST4': 0.15,
            'TEST5': 0.1
        }
        
        # 创建模拟股票数据
        np.random.seed(42)
        dates = pd.date_range(start='2024-01-01', end='2024-01-31', freq='B')
        
        stock_data = {}
        for symbol in portfolio.keys():
            base_price = 100 + hash(symbol) % 50
            returns = np.random.randn(len(dates)) * 0.02
            prices = base_price * np.exp(np.cumsum(returns))
            
            df = pd.DataFrame({'close': prices}, index=dates)
            stock_data[symbol] = df
        
        # 测试风格因子暴露计算性能
        start_time = time.time()
        exposure_result = risk_manager.calculate_style_exposures(
            portfolio=portfolio,
            stock_data=stock_data,
            factor_source='barra'
        )
        exposure_time = time.time() - start_time
        
        # 测试压力测试性能
        start_time = time.time()
        stress_result = risk_manager.run_stress_tests(
            portfolio=portfolio,
            stock_data=stock_data,
            scenarios=['2008_financial_crisis', '2022_small_cap_crash']
        )
        stress_time = time.time() - start_time
        
        print(f"  风格因子暴露计算: {exposure_time:.2f}s")
        print(f"  压力测试: {stress_time:.2f}s")
        
        return {
            'operation': 'risk_calculation',
            'exposure_time_seconds': exposure_time,
            'stress_test_time_seconds': stress_time,
            'total_time_seconds': exposure_time + stress_time,
            'portfolio_size': len(portfolio),
            'scenarios_tested': len(['2008_financial_crisis', '2022_small_cap_crash'])
        }
    
    except Exception as e:
        print(f"风险计算性能测试失败: {e}")
        return {'error': str(e)}

def main():
    """主函数"""
    print("=" * 60)
    print("量化系统性能基准测试")
    print("=" * 60)
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    benchmarks = []
    
    # 运行各个基准测试
    benchmarks.append(benchmark_data_acquisition())
    print()
    
    benchmarks.append(benchmark_sentiment_calculation())
    print()
    
    benchmarks.append(benchmark_risk_calculation())
    print()
    
    # 汇总结果
    print("=" * 60)
    print("性能基准测试汇总")
    print("=" * 60)
    
    total_time = 0
    successful_tests = 0
    
    for bench in benchmarks:
        if 'error' not in bench:
            successful_tests += 1
            
            if 'total_time_seconds' in bench:
                total_time += bench['total_time_seconds']
                print(f"{bench['operation']}: {bench['total_time_seconds']:.2f}s")
            elif 'avg_time_seconds' in bench:
                total_time += bench['avg_time_seconds']
                print(f"{bench['operation']}: {bench['avg_time_seconds']:.2f}s (平均)")
        else:
            print(f"{bench.get('operation', '未知操作')}: 失败 - {bench['error']}")
    
    print(f"\n总耗时: {total_time:.2f}s")
    print(f"成功测试: {successful_tests}/{len(benchmarks)}")
    
    # 保存结果
    result_file = '/root/.openclaw/workspace/quant_system/performance_baseline.json'
    import json
    with open(result_file, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'benchmarks': benchmarks,
            'summary': {
                'total_time_seconds': total_time,
                'successful_tests': successful_tests,
                'total_tests': len(benchmarks)
            }
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n详细结果已保存至: {result_file}")
    
    return successful_tests == len(benchmarks)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)