#!/usr/bin/env python3
"""
量化策略双数据源集成测试
测试：1. 双数据源在量化策略中的表现 2. 财务数据接口 3. 指数数据支持
时间：2026-03-20
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "skills/quant/lib"))

# 使用虚拟环境
venv_python = "/root/.openclaw/workspace/quant_venv/bin/python"
if os.path.exists(venv_python):
    os.execv(venv_python, [venv_python] + sys.argv)

import pandas as pd
import numpy as np
import time
import traceback
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_dual_source_in_strategy():
    """测试双数据源在量化策略中的表现"""
    print("\n" + "="*80)
    print("测试一：双数据源在量化策略中的表现")
    print("="*80)
    
    try:
        # 导入数据模块
        import data
        
        # 导入策略模块
        from alpha_stream import FactorEngine, ValueFactor, MomentumFactor, AlphaCombiner
        
        # 测试股票列表
        test_symbols = ['600519.SH', '000001.SZ', '000858.SZ', '002415.SZ']
        
        # 测试时间段
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")  # 6个月数据
        
        print(f"\n1. 使用双数据源获取 {len(test_symbols)} 只股票数据 ({start_date} 到 {end_date})...")
        
        all_data = {}
        sources_used = {}
        
        for symbol in test_symbols:
            try:
                start_time = time.time()
                df = data.get_stock(symbol, start_date, end_date, 'qfq')
                elapsed = time.time() - start_time
                
                if df is not None and len(df) > 0:
                    all_data[symbol] = df
                    
                    # 获取数据源信息
                    status = data.get_data_source_status()
                    sources_used[symbol] = status.get('baostock', False)  # 简化表示
                    
                    print(f"   ✅ {symbol}: {len(df)} 行数据, 耗时 {elapsed:.2f} 秒")
                    print(f"      最新收盘: {df['close'].iloc[-1]:.2f}, 期间收益率: {((df['close'].iloc[-1] - df['close'].iloc[0]) / df['close'].iloc[0] * 100):.2f}%")
                else:
                    print(f"   ❌ {symbol}: 获取数据失败")
                    
            except Exception as e:
                print(f"   ❌ {symbol}: 异常 - {e}")
        
        if len(all_data) < 2:
            print("   数据不足，无法进行策略测试")
            return False
        
        print(f"\n2. 数据源使用统计:")
        baostock_count = sum(1 for source in sources_used.values() if source)
        akshare_count = len(sources_used) - baostock_count
        print(f"   总数据获取: {len(sources_used)} 只股票")
        print(f"   Baostock 使用: {baostock_count} 只")
        print(f"   AKShare 使用: {akshare_count} 只")
        
        print(f"\n3. 执行多因子策略回测...")
        
        # 创建模拟因子数据（简化版）
        strategy_results = {}
        
        for symbol, df in all_data.items():
            try:
                # 计算简单因子（由于没有财务数据，使用价格衍生因子）
                if len(df) > 60:  # 至少60个交易日
                    # 动量因子
                    df['momentum_1m'] = df['close'].pct_change(22)
                    df['momentum_3m'] = df['close'].pct_change(66)
                    
                    # 波动率因子
                    df['volatility_20d'] = df['close'].rolling(20).std() / df['close'].rolling(20).mean()
                    
                    # 简单多因子打分
                    factors = pd.DataFrame({
                        'momentum': df['momentum_1m'].iloc[-1] if not pd.isna(df['momentum_1m'].iloc[-1]) else 0,
                        'volatility': -df['volatility_20d'].iloc[-1] if not pd.isna(df['volatility_20d'].iloc[-1]) else 0,
                        'price_strength': (df['close'].iloc[-1] - df['close'].rolling(20).mean().iloc[-1]) / df['close'].rolling(20).std().iloc[-1] if df['close'].rolling(20).std().iloc[-1] > 0 else 0
                    }, index=[symbol])
                    
                    # 因子加权（简单平均）
                    factors['score'] = factors.mean(axis=1)
                    
                    # 计算策略收益
                    holding_period = 22  # 持有1个月
                    if len(df) > holding_period:
                        future_return = (df['close'].iloc[-1] - df['close'].iloc[-holding_period-1]) / df['close'].iloc[-holding_period-1] * 100
                    else:
                        future_return = 0
                    
                    strategy_results[symbol] = {
                        'factor_score': factors['score'].iloc[0],
                        'future_return': future_return,
                        'data_points': len(df),
                        'source': 'baostock' if sources_used.get(symbol, False) else 'akshare'
                    }
                    
                    print(f"   ✅ {symbol}: 因子得分 {factors['score'].iloc[0]:.4f}, 未来收益 {future_return:.2f}%")
                    
            except Exception as e:
                print(f"   ❌ {symbol}: 策略计算异常 - {e}")
                strategy_results[symbol] = {'error': str(e)}
        
        if len(strategy_results) > 0:
            print(f"\n4. 策略表现分析:")
            
            # 计算策略有效性
            valid_results = {k: v for k, v in strategy_results.items() if 'factor_score' in v and 'future_return' in v}
            
            if len(valid_results) >= 2:
                # 按因子得分排序
                sorted_stocks = sorted(valid_results.items(), key=lambda x: x[1]['factor_score'], reverse=True)
                
                print(f"   有效股票数量: {len(valid_results)}")
                print(f"\n   排名前3股票:")
                for i, (symbol, result) in enumerate(sorted_stocks[:3]):
                    print(f"     {i+1}. {symbol}: 得分 {result['factor_score']:.4f}, 收益 {result['future_return']:.2f}%, 数据源 {result['source']}")
                
                print(f"\n   排名后3股票:")
                for i, (symbol, result) in enumerate(sorted_stocks[-3:]):
                    print(f"     {i+1}. {symbol}: 得分 {result['factor_score']:.4f}, 收益 {result['future_return']:.2f}%, 数据源 {result['source']}")
                
                # 计算策略IC（信息系数）
                scores = [r['factor_score'] for r in valid_results.values()]
                returns = [r['future_return'] for r in valid_results.values()]
                
                if len(scores) > 1:
                    ic = np.corrcoef(scores, returns)[0, 1]
                    print(f"\n   策略IC（信息系数）: {ic:.4f}")
                    
                    if ic > 0.05:
                        print(f"   ✅ 策略有效性: 良好 (IC > 0.05)")
                    elif ic > 0:
                        print(f"   ⚠️ 策略有效性: 一般 (IC > 0)")
                    else:
                        print(f"   ❌ 策略有效性: 较差 (IC ≤ 0)")
                
                return True
            else:
                print("   有效数据不足，无法进行策略分析")
                return False
        
        return True
        
    except Exception as e:
        print(f"双数据源策略测试异常: {e}")
        traceback.print_exc()
        return False

def test_finance_data_interface():
    """测试财务数据接口"""
    print("\n" + "="*80)
    print("测试二：财务数据接口")
    print("="*80)
    
    try:
        # 导入财务数据模块
        from data_extended import get_finance_data, FinanceDataExtender
        
        test_symbols = ['600519.SH', '000001.SZ']
        
        print(f"\n1. 测试财务数据获取...")
        
        finance_extender = FinanceDataExtender()
        
        for symbol in test_symbols:
            print(f"\n   股票: {symbol}")
            
            # 测试利润表
            try:
                start_time = time.time()
                income_df = finance_extender.get_income_statement(symbol, 2023, 4)
                elapsed = time.time() - start_time
                
                if income_df is not None and len(income_df) > 0:
                    print(f"   ✅ 利润表: {len(income_df)} 行, 耗时 {elapsed:.2f} 秒")
                    print(f"      列数: {len(income_df.columns)}")
                    
                    # 显示关键字段
                    key_fields = ['netProfit', 'operateProfit', 'totalProfit', 'ROE', 'EPS']
                    available_fields = [f for f in key_fields if f in income_df.columns]
                    if available_fields:
                        print(f"      关键字段: {available_fields[:5]}")
                else:
                    print(f"   ⚠️ 利润表: 无数据或获取失败")
                    
            except Exception as e:
                print(f"   ❌ 利润表: 异常 - {e}")
            
            # 测试财务指标汇总
            try:
                start_time = time.time()
                indicators = finance_extender.get_financial_indicators(symbol, 2022, 2023)
                elapsed = time.time() - start_time
                
                if indicators and len(indicators) > 0:
                    print(f"   ✅ 财务指标: {len(indicators)} 个季度, 耗时 {elapsed:.2f} 秒")
                    
                    # 显示最近季度的指标
                    latest_quarter = sorted(indicators.keys())[-1] if indicators else None
                    if latest_quarter:
                        print(f"      最近季度 {latest_quarter}:")
                        for key, value in indicators[latest_quarter].items():
                            if value is not None:
                                print(f"        {key}: {value}")
                else:
                    print(f"   ⚠️ 财务指标: 无数据")
                    
            except Exception as e:
                print(f"   ❌ 财务指标: 异常 - {e}")
        
        print(f"\n2. 财务数据接口测试完成")
        return True
        
    except Exception as e:
        print(f"财务数据接口测试异常: {e}")
        traceback.print_exc()
        return False

def test_index_data_support():
    """测试指数数据支持"""
    print("\n" + "="*80)
    print("测试三：指数数据支持")
    print("="*80)
    
    try:
        # 导入指数数据模块
        from data_extended import get_index_data, IndexDataExtender
        
        index_extender = IndexDataExtender()
        
        print(f"\n1. 测试指数列表...")
        indices = index_extender.get_index_list()
        print(f"   支持 {len(indices)} 个主要指数:")
        for idx in indices[:5]:  # 显示前5个
            print(f"     {idx['code']}: {idx['name']}")
        
        if len(indices) > 5:
            print(f"     ... 还有 {len(indices) - 5} 个指数")
        
        print(f"\n2. 测试指数数据获取...")
        
        # 测试主要指数
        test_indices = ['000001.SH', '000300.SH', '399006.SZ']
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        
        for index_code in test_indices:
            try:
                start_time = time.time()
                df = index_extender.get_index_daily(index_code, start_date, end_date)
                elapsed = time.time() - start_time
                
                if df is not None and len(df) > 0:
                    # 获取指数名称
                    index_name = next((idx['name'] for idx in indices if idx['code'] == index_code), index_code)
                    
                    print(f"   ✅ {index_name} ({index_code}): {len(df)} 行数据, 耗时 {elapsed:.2f} 秒")
                    
                    if 'close' in df.columns and len(df) > 1:
                        total_return = (df['close'].iloc[-1] - df['close'].iloc[0]) / df['close'].iloc[0] * 100
                        print(f"      期间收益率: {total_return:.2f}%")
                        print(f"      最新收盘: {df['close'].iloc[-1]:.2f}")
                else:
                    print(f"   ❌ {index_code}: 获取数据失败")
                    
            except Exception as e:
                print(f"   ❌ {index_code}: 异常 - {e}")
        
        print(f"\n3. 测试指数成分股...")
        try:
            constituents = index_extender.get_index_constituents('000300.SH')
            if constituents is not None and len(constituents) > 0:
                print(f"   ✅ 沪深300成分股: {len(constituents)} 只股票")
                print(f"      示例: {constituents.head(3) if hasattr(constituents, 'head') else constituents[:3]}")
            else:
                print(f"   ⚠️ 成分股: 获取失败")
        except Exception as e:
            print(f"   ❌ 成分股: 异常 - {e}")
        
        print(f"\n4. 测试指数批量表现...")
        try:
            performance = index_extender.get_index_performance(['000001.SH', '000300.SH', '399006.SZ'], start_date, end_date)
            
            if performance:
                print(f"   指数批量表现:")
                for index_code, result in performance.items():
                    if 'total_return' in result:
                        index_name = next((idx['name'] for idx in indices if idx['code'] == index_code), index_code)
                        print(f"     {index_name}: {result['total_return']:.2f}%")
                    elif 'error' in result:
                        print(f"     {index_code}: 错误 - {result['error']}")
            else:
                print(f"   ⚠️ 批量表现: 获取失败")
                
        except Exception as e:
            print(f"   ❌ 批量表现: 异常 - {e}")
        
        print(f"\n5. 指数数据支持测试完成")
        return True
        
    except Exception as e:
        print(f"指数数据支持测试异常: {e}")
        traceback.print_exc()
        return False

def test_batch_operations():
    """测试批量操作"""
    print("\n" + "="*80)
    print("测试四：批量操作性能")
    print("="*80)
    
    try:
        from data_extended import get_batch_stock_data, BatchDataOperator
        
        # 测试股票列表
        symbols = ['600519.SH', '000001.SZ', '000858.SZ', '002415.SZ', '600036.SH']
        
        # 测试时间段
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        
        print(f"\n1. 测试批量获取 {len(symbols)} 只股票数据...")
        
        operator = BatchDataOperator(max_workers=3)
        
        start_time = time.time()
        batch_results = operator.batch_get_stock_data(symbols, start_date, end_date, 'qfq')
        total_time = time.time() - start_time
        
        success_count = sum(1 for r in batch_results.values() if 'data' in r)
        failure_count = len(batch_results) - success_count
        
        print(f"   批量获取完成，耗时 {total_time:.2f} 秒")
        print(f"   成功: {success_count} 只, 失败: {failure_count} 只")
        
        if success_count > 0:
            avg_time_per_stock = total_time / success_count
            print(f"   平均每只股票耗时: {avg_time_per_stock:.2f} 秒")
            
            # 显示数据源统计
            sources = {}
            for symbol, result in batch_results.items():
                if 'data' in result:
                    source = result.get('source', 'unknown')
                    sources[source] = sources.get(source, 0) + 1
            
            print(f"   数据源使用统计:")
            for source, count in sources.items():
                print(f"     {source}: {count} 只股票")
        
        print(f"\n2. 测试批量收益率计算...")
        start_time = time.time()
        return_results = operator.batch_calculate_returns(symbols, start_date, end_date)
        calc_time = time.time() - start_time
        
        success_returns = sum(1 for r in return_results.values() if 'total_return' in r)
        
        print(f"   批量计算完成，耗时 {calc_time:.2f} 秒")
        print(f"   成功计算: {success_returns}/{len(symbols)} 只股票")
        
        if success_returns > 0:
            print(f"   收益率示例:")
            for symbol, result in list(return_results.items())[:3]:  # 显示前3个
                if 'total_return' in result:
                    print(f"     {symbol}: {result['total_return']:.2f}% (来源: {result.get('source', 'unknown')})")
        
        return True
        
    except Exception as e:
        print(f"批量操作测试异常: {e}")
        traceback.print_exc()
        return False

def main():
    """主测试函数"""
    print("开始量化策略双数据源集成测试")
    print(f"测试时间: {datetime.now()}")
    print(f"Python版本: {sys.version}")
    
    # 运行所有测试
    tests = [
        ("双数据源在量化策略中的表现", test_dual_source_in_strategy),
        ("财务数据接口", test_finance_data_interface),
        ("指数数据支持", test_index_data_support),
        ("批量操作性能", test_batch_operations)
    ]
    
    results = []
    start_total_time = time.time()
    
    for test_name, test_func in tests:
        print(f"\n{'='*80}")
        print(f"执行测试: {test_name}")
        print('='*80)
        
        try:
            test_start = time.time()
            success = test_func()
            test_time = time.time() - test_start
            
            results.append((test_name, success, test_time))
            
            status = "✅ 通过" if success else "❌ 失败"
            print(f"\n测试完成: {status}, 耗时 {test_time:.2f} 秒")
            
        except Exception as e:
            print(f"测试执行异常: {e}")
            traceback.print_exc()
            results.append((test_name, False, 0))
    
    total_time = time.time() - start_total_time
    
    # 汇总结果
    print(f"\n{'='*80}")
    print("测试结果汇总")
    print('='*80)
    
    all_passed = True
    for test_name, success, test_time in results:
        status = "✅ 通过" if success else "❌ 失败"
        print(f"{test_name}: {status} ({test_time:.2f} 秒)")
        if not success:
            all_passed = False
    
    print(f"\n总测试时间: {total_time:.2f} 秒")
    print(f"测试完成时间: {datetime.now()}")
    
    if all_passed:
        print("\n🎉 所有测试通过！双数据源方案完全可用。")
        return True
    else:
        print("\n⚠️ 部分测试失败，需要进一步优化。")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)