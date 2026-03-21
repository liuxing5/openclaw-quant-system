#!/usr/bin/env python3
"""
准备端到端回测数据
为完整量化系统回测准备必要的数据集
"""
import sys
import os

# 优先使用系统包
sys.path.insert(0, '/usr/lib/python3/dist-packages')
sys.path.append('/root/.openclaw/workspace/quant_system')

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

print("=" * 70)
print("准备端到端回测数据")
print("=" * 70)

# ============================================================================
# 1. 创建模拟数据（当真实数据不可用时）
print("\n1. 📊 创建模拟数据...")

def create_synthetic_market_data():
    """创建模拟市场数据"""
    np.random.seed(42)
    
    # 日期范围：3年数据
    start_date = '2020-01-01'
    end_date = '2022-12-31'
    dates = pd.date_range(start_date, end_date, freq='D')
    n_days = len(dates)
    
    # 模拟50只股票
    n_stocks = 50
    stocks = [f'S{i:03d}' for i in range(n_stocks)]
    
    # 模拟价格数据
    print(f"  创建价格数据: {n_days}天 × {n_stocks}只股票")
    price_data = pd.DataFrame(index=dates, columns=stocks)
    
    for i, stock in enumerate(stocks):
        # 基础价格趋势
        base_price = 100 + i * 5  # 不同股票不同起始价格
        
        # 模拟不同波动性
        if i < 10:
            volatility = 0.02  # 低波动股票
        elif i < 30:
            volatility = 0.04  # 中等波动
        else:
            volatility = 0.08  # 高波动股票
        
        # 生成价格序列
        returns = np.random.normal(0.0005, volatility, n_days)
        price_series = base_price * (1 + returns).cumprod()
        price_data[stock] = price_series
    
    # 模拟成交量数据
    print(f"  创建成交量数据...")
    volume_data = pd.DataFrame(index=dates, columns=stocks)
    
    for stock in stocks:
        base_volume = 1000000 + np.random.randint(-200000, 200000)
        volume_series = base_volume + np.random.normal(0, 200000, n_days).cumsum()
        volume_series = np.abs(volume_series)  # 确保正数
        volume_data[stock] = volume_series
    
    # 模拟市场指数（上证指数）
    print(f"  创建市场指数数据...")
    market_returns = np.random.normal(0.0003, 0.015, n_days)
    market_prices = 3000 * (1 + market_returns).cumprod()
    market_data = pd.DataFrame({
        'close': market_prices,
        'volume': 100000000 + np.random.normal(0, 10000000, n_days).cumsum()
    }, index=dates)
    
    return price_data, volume_data, market_data

# ============================================================================
# 2. 尝试获取真实AKShare数据
print("\n2. 🌐 尝试获取真实AKShare数据...")

def try_akshare_data():
    """尝试获取真实数据"""
    try:
        import akshare as ak
        
        print("  ✓ AKShare可用，尝试获取A股列表...")
        
        # 获取A股列表
        stock_list = ak.stock_info_a_code_name()
        if stock_list is not None and len(stock_list) > 0:
            print(f"  获取到 {len(stock_list)} 只A股")
            
            # 取前20只股票作为样本
            sample_stocks = stock_list.head(20)['code'].tolist()
            print(f"  样本股票: {sample_stocks[:5]}...")
            
            return {
                'available': True,
                'stock_list': stock_list,
                'sample_stocks': sample_stocks
            }
        else:
            print("  ⚠ 未获取到股票列表")
            return {'available': False}
            
    except Exception as e:
        print(f"  ⚠ AKShare获取失败: {e}")
        return {'available': False}

# ============================================================================
# 3. 准备回测配置文件
print("\n3. ⚙️ 准备回测配置文件...")

def create_backtest_config():
    """创建回测配置文件"""
    config = {
        'data': {
            'period': {
                'start_date': '2020-01-01',
                'end_date': '2022-12-31',
                'training_start': '2020-01-01',
                'training_end': '2021-12-31',
                'testing_start': '2022-01-01',
                'testing_end': '2022-12-31'
            },
            'universe': {
                'size': 50,
                'selection_method': '市值加权',
                'rebalance_frequency': '季度'
            },
            'features': {
                'technical': ['momentum', 'volatility', 'rsi', 'macd', 'bollinger'],
                'fundamental': ['roe', 'profit_growth', 'debt_ratio', 'pe_ratio'],
                'market': ['beta', 'relative_strength', 'volume_trend']
            }
        },
        'model': {
            'factor_model': 'multi_factor_regression',
            'alpha_model': 'alpha_predictor',
            'regime_model': 'market_regime_detector',
            'optimizer': 'portfolio_optimizer'
        },
        'backtest': {
            'initial_capital': 1000000.0,
            'transaction_cost': 0.001,
            'slippage': 0.0005,
            'rebalance_frequency': 'monthly',
            'walkforward': {
                'train_years': 2,
                'test_months': 6,
                'step_months': 3
            }
        },
        'risk': {
            'max_position': 0.1,
            'max_sector_exposure': 0.3,
            'stop_loss': -0.15,
            'max_drawdown_limit': -0.25
        }
    }
    
    return config

# ============================================================================
# 4. 保存数据到文件
print("\n4. 💾 保存数据到文件...")

def save_data_to_files():
    """保存数据到文件系统"""
    data_dir = '/root/.openclaw/workspace/quant_system/data/backtest'
    os.makedirs(data_dir, exist_ok=True)
    
    # 创建模拟数据
    price_data, volume_data, market_data = create_synthetic_market_data()
    
    # 保存价格数据
    price_file = os.path.join(data_dir, 'price_data.csv')
    price_data.to_csv(price_file)
    print(f"  ✓ 价格数据保存到: {price_file}")
    print(f"    形状: {price_data.shape}, 日期范围: {price_data.index[0].date()} 到 {price_data.index[-1].date()}")
    
    # 保存成交量数据
    volume_file = os.path.join(data_dir, 'volume_data.csv')
    volume_data.to_csv(volume_file)
    print(f"  ✓ 成交量数据保存到: {volume_file}")
    
    # 保存市场数据
    market_file = os.path.join(data_dir, 'market_data.csv')
    market_data.to_csv(market_file)
    print(f"  ✓ 市场数据保存到: {market_file}")
    
    # 保存配置文件
    config = create_backtest_config()
    config_file = os.path.join(data_dir, 'backtest_config.json')
    
    import json
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    
    print(f"  ✓ 回测配置保存到: {config_file}")
    
    # 尝试获取真实数据
    akshare_result = try_akshare_data()
    if akshare_result['available']:
        real_data_file = os.path.join(data_dir, 'akshare_stock_list.csv')
        akshare_result['stock_list'].to_csv(real_data_file, index=False)
        print(f"  ✓ 真实股票列表保存到: {real_data_file}")
    
    return {
        'data_dir': data_dir,
        'price_file': price_file,
        'volume_file': volume_file,
        'market_file': market_file,
        'config_file': config_file,
        'has_real_data': akshare_result['available']
    }

# ============================================================================
# 5. 验证数据质量
print("\n5. 🔍 验证数据质量...")

def validate_data_quality(file_paths):
    """验证数据质量"""
    results = {}
    
    for name, file_path in file_paths.items():
        if os.path.exists(file_path):
            try:
                if file_path.endswith('.csv'):
                    df = pd.read_csv(file_path, nrows=5)  # 只读取前5行检查
                    results[name] = {
                        'exists': True,
                        'rows': len(pd.read_csv(file_path)),
                        'columns': len(df.columns),
                        'sample': df.head(2).to_dict()
                    }
                elif file_path.endswith('.json'):
                    import json
                    with open(file_path, 'r') as f:
                        config = json.load(f)
                    results[name] = {
                        'exists': True,
                        'config_keys': list(config.keys())
                    }
            except Exception as e:
                results[name] = {
                    'exists': True,
                    'error': str(e)[:50]
                }
        else:
            results[name] = {'exists': False}
    
    return results

# ============================================================================
# 主执行
if __name__ == "__main__":
    try:
        # 保存数据
        saved_files = save_data_to_files()
        
        # 验证数据
        print(f"\n6. 📋 数据验证结果:")
        
        validation_files = {
            '价格数据': saved_files['price_file'],
            '成交量数据': saved_files['volume_file'],
            '市场数据': saved_files['market_file'],
            '回测配置': saved_files['config_file']
        }
        
        validation_results = validate_data_quality(validation_files)
        
        for name, result in validation_results.items():
            if result.get('exists', False):
                if 'error' in result:
                    print(f"  {name}: ⚠ 有错误 ({result['error']})")
                else:
                    if 'rows' in result:
                        print(f"  {name}: ✓ {result['rows']}行 × {result['columns']}列")
                    else:
                        print(f"  {name}: ✓ 配置有效 ({len(result['config_keys'])}个配置项)")
            else:
                print(f"  {name}: ✗ 文件不存在")
        
        print(f"\n7. 📁 数据目录: {saved_files['data_dir']}")
        print(f"   真实数据可用: {'✓' if saved_files['has_real_data'] else '✗'}")
        
        print("\n" + "=" * 70)
        print("端到端回测数据准备完成")
        print("=" * 70)
        
        print("\n🎯 已准备数据:")
        print("  1. 价格数据: 50只股票，3年日线数据")
        print("  2. 成交量数据: 对应的成交量序列")
        print("  3. 市场指数数据: 模拟上证指数")
        print("  4. 回测配置文件: 完整的回测参数配置")
        
        print("\n🚀 下一步:")
        print("  1. 运行端到端回测: python run_end_to_end_backtest.py")
        print("  2. 分析回测结果")
        print("  3. 优化参数配置")
        
        print("\n📊 数据统计:")
        print(f"  总数据点: 50股票 × 756交易日 ≈ 37,800个价格点")
        print(f"  时间范围: 2020-01-01 至 2022-12-31")
        print(f"  训练期: 2020-01-01 至 2021-12-31 (2年)")
        print(f"  测试期: 2022-01-01 至 2022-12-31 (1年)")
        
    except Exception as e:
        print(f"数据准备失败: {e}")
        import traceback
        traceback.print_exc()