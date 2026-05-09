#!/usr/bin/env python3
"""
真实数据回测脚本
使用AKShare真实股票数据进行回测
"""
import sys
import os
import json
import warnings
warnings.filterwarnings('ignore')

# 优先使用系统包
sys.path.insert(0, '/usr/lib/python3/dist-packages')
sys.path.append('/root/.openclaw/workspace/quant_system')

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

print("=" * 80)
print("真实数据回测开始")
print("=" * 80)

# ============================================================================
# 1. 加载AKShare股票列表
print("\n1. 📋 加载AKShare股票列表...")

def load_akshare_stocks():
    """加载AKShare股票列表"""
    stock_list_file = '/root/.openclaw/workspace/quant_system/data/backtest/akshare_stock_list.csv'
    
    if os.path.exists(stock_list_file):
        # 读取时保持code列为字符串
        stock_list = pd.read_csv(stock_list_file, dtype={'code': str})
        print(f"  ✓ 加载股票列表: {len(stock_list)}只A股")
        
        # 确保code列是字符串
        stock_list['code'] = stock_list['code'].astype(str)
        
        # 过滤掉ST股票和异常代码
        valid_stocks = stock_list[
            (~stock_list['name'].str.contains('ST')) & 
            (~stock_list['name'].str.contains('退市')) &
            (stock_list['code'].str.len() == 6)
        ]
        
        print(f"  ✓ 有效股票: {len(valid_stocks)}只 (过滤ST/退市)")
        
        # 取前20只作为样本
        sample_stocks = valid_stocks.head(20)
        print(f"  ✓ 样本股票: {len(sample_stocks)}只")
        
        # 显示前5只
        print(f"    前5只样本: {sample_stocks.head().to_string(index=False)}")
        
        return sample_stocks
    else:
        print("  ✗ 股票列表文件不存在")
        return None

# ============================================================================
# 2. 获取真实股票数据
print("\n2. 🌐 获取真实股票数据...")

def fetch_real_stock_data(stock_codes, start_date='2023-01-01', end_date='2023-12-31'):
    """获取真实股票数据"""
    try:
        import akshare as ak
        
        print(f"  获取{len(stock_codes)}只股票数据 ({start_date} 至 {end_date})...")
        
        all_data = {}
        
        for i, stock_code in enumerate(stock_codes[:10]):  # 限制10只以加快速度
            try:
                print(f"    [{i+1}/{min(10, len(stock_codes))}] 获取 {stock_code}...", end=' ', flush=True)
                
                # 获取日线数据
                stock_data = ak.stock_zh_a_hist(
                    symbol=stock_code,
                    period="daily",
                    start_date=start_date.replace('-', ''),
                    end_date=end_date.replace('-', ''),
                    adjust="qfq"
                )
                
                if stock_data is not None and len(stock_data) > 0:
                    # 重命名列
                    stock_data.columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'amplitude', 'change_pct', 'change', 'turnover']
                    
                    # 设置日期索引
                    stock_data['date'] = pd.to_datetime(stock_data['date'])
                    stock_data.set_index('date', inplace=True)
                    
                    # 只保留需要的列
                    stock_data = stock_data[['open', 'high', 'low', 'close', 'volume']]
                    
                    all_data[stock_code] = stock_data
                    print(f"✓ {len(stock_data)}个数据点")
                else:
                    print(f"✗ 无数据")
                    
            except Exception as e:
                print(f"✗ 错误: {str(e)[:30]}...")
                continue
        
        print(f"  ✓ 成功获取 {len(all_data)} 只股票数据")
        
        if all_data:
            # 合并数据
            close_prices = pd.DataFrame({
                code: data['close'] for code, data in all_data.items()
            })
            
            volumes = pd.DataFrame({
                code: data['volume'] for code, data in all_data.items()
            })
            
            return close_prices, volumes, all_data
        else:
            return None, None, None
            
    except ImportError:
        print("  ✗ AKShare不可用")
        return None, None, None
    except Exception as e:
        print(f"  ✗ 数据获取失败: {e}")
        return None, None, None

# ============================================================================
# 3. 准备回测数据
print("\n3. 📊 准备回测数据...")

def prepare_real_backtest_data():
    """准备真实回测数据"""
    # 加载股票列表
    sample_stocks = load_akshare_stocks()
    
    if sample_stocks is None or len(sample_stocks) == 0:
        print("  ⚠ 无有效股票数据，使用模拟数据")
        return None
    
    # 获取股票代码
    stock_codes = sample_stocks['code'].tolist()
    
    # 设置日期范围（最近1年）
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    
    # 获取真实数据
    close_prices, volumes, raw_data = fetch_real_stock_data(
        stock_codes, start_date, end_date
    )
    
    if close_prices is not None and len(close_prices) > 0:
        print(f"  ✓ 回测数据准备完成")
        print(f"    时间范围: {close_prices.index[0].date()} 至 {close_prices.index[-1].date()}")
        print(f"    股票数量: {len(close_prices.columns)}只")
        print(f"    数据点数: {len(close_prices)}天")
        
        # 保存数据
        data_dir = '/root/.openclaw/workspace/quant_system/data/real_backtest'
        os.makedirs(data_dir, exist_ok=True)
        
        close_prices.to_csv(os.path.join(data_dir, 'real_close_prices.csv'))
        volumes.to_csv(os.path.join(data_dir, 'real_volumes.csv'))
        
        print(f"  ✓ 数据保存到: {data_dir}/")
        
        return {
            'close_prices': close_prices,
            'volumes': volumes,
            'raw_data': raw_data,
            'stock_codes': stock_codes[:10],  # 只使用前10只
            'data_dir': data_dir
        }
    else:
        print("  ⚠ 真实数据获取失败，使用模拟数据")
        return None

# ============================================================================
# 4. 运行真实数据回测
print("\n4. 🚀 运行真实数据回测...")

def run_real_backtest(backtest_data):
    """运行真实数据回测"""
    if backtest_data is None:
        print("  ⚠ 无回测数据，跳过")
        return None
    
    try:
        # 初始化量化模块
        print("  初始化量化模块...")
        
        from real_factor_manager import RealFactorManager
        from regime_detection import MarketRegimeDetector
        from portfolio_optimizer import PortfolioOptimizer
        
        # 因子管理器
        factor_manager = RealFactorManager()
        print("  ✓ 真实因子管理器初始化")
        
        # 市场状态识别器
        regime_detector = MarketRegimeDetector(n_regimes=3)
        print("  ✓ 市场状态识别器初始化")
        
        # 组合优化器
        portfolio_optimizer = PortfolioOptimizer(
            risk_free_rate=0.03,
            max_position=0.1,
            min_position=0.0
        )
        print("  ✓ 组合优化器初始化")
        
        # 提取数据
        close_prices = backtest_data['close_prices']
        volumes = backtest_data['volumes']
        stock_codes = backtest_data['stock_codes']
        
        print(f"\n5. 📈 分析真实数据...")
        
        # 计算收益率
        returns = close_prices.pct_change().dropna()
        
        if len(returns) > 0:
            # 基本统计
            print(f"  收益率统计:")
            print(f"    均值: {returns.mean().mean():.6f}")
            print(f"    标准差: {returns.std().mean():.6f}")
            print(f"    夏普比率: {(returns.mean().mean() * 252) / (returns.std().mean() * np.sqrt(252)):.4f}")
            
            # 计算协方差矩阵
            print(f"  计算协方差矩阵...")
            cov_matrix = returns.cov()
            print(f"    协方差矩阵形状: {cov_matrix.shape}")
            
            # 运行组合优化
            print(f"\n6. ⚖️ 运行组合优化...")
            expected_returns = returns.mean()
            
            opt_result = portfolio_optimizer.mean_variance_optimization(
                expected_returns, cov_matrix, objective='sharpe'
            )
            
            if opt_result['success']:
                stats = opt_result['stats']
                print(f"  ✓ 组合优化成功")
                print(f"    预期年化收益: {stats['expected_return'] * 252:.2%}")
                print(f"    预期年化风险: {stats['expected_risk'] * np.sqrt(252):.2%}")
                print(f"    夏普比率: {stats['sharpe_ratio']:.4f}")
                
                # 显示权重
                weights = opt_result['weights']
                top_5 = weights.nlargest(5)
                print(f"    前5大权重:")
                for code, weight in top_5.items():
                    print(f"      {code}: {weight:.2%}")
            
            # 市场状态识别
            print(f"\n7. 🌍 识别市场状态...")
            
            # 创建市场指数（等权重组合）
            market_index = close_prices.mean(axis=1)
            market_returns = market_index.pct_change().dropna()
            
            regimes = regime_detector.detect_regimes_gmm(market_returns)
            
            if regime_detector.regime_stats:
                print(f"  ✓ 市场状态识别完成")
                for regime_id, stats in regime_detector.regime_stats.items():
                    print(f"    {stats['label']}: {stats['count']}天 ({stats['percentage']:.1f}%)")
        
        return {
            'returns': returns,
            'cov_matrix': cov_matrix if 'cov_matrix' in locals() else None,
            'opt_result': opt_result if 'opt_result' in locals() else None,
            'regimes': regimes if 'regimes' in locals() else None
        }
        
    except Exception as e:
        print(f"  ✗ 回测执行失败: {e}")
        import traceback
        traceback.print_exc()
        return None

# ============================================================================
# 5. 生成回测报告
print("\n8. 📊 生成回测报告...")

def generate_real_backtest_report(results, backtest_data):
    """生成回测报告"""
    if results is None or backtest_data is None:
        return None
    
    report = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'data_summary': {
            'stock_count': len(backtest_data['close_prices'].columns),
            'period_days': len(backtest_data['close_prices']),
            'date_range': f"{backtest_data['close_prices'].index[0].date()} 至 {backtest_data['close_prices'].index[-1].date()}",
            'data_dir': backtest_data['data_dir']
        },
        'performance': {},
        'recommendations': []
    }
    
    if 'returns' in results and results['returns'] is not None:
        returns = results['returns']
        report['performance']['return_mean'] = returns.mean().mean()
        report['performance']['return_std'] = returns.std().mean()
        report['performance']['sharpe_annual'] = (returns.mean().mean() * 252) / (returns.std().mean() * np.sqrt(252))
    
    if 'opt_result' in results and results['opt_result'] is not None:
        opt_result = results['opt_result']
        if opt_result['success']:
            stats = opt_result['stats']
            report['performance']['portfolio_return'] = stats['expected_return']
            report['performance']['portfolio_risk'] = stats['expected_risk']
            report['performance']['portfolio_sharpe'] = stats['sharpe_ratio']
            
            # 添加建议
            if stats['sharpe_ratio'] > 0:
                report['recommendations'].append("✅ 组合夏普比率为正，建议进一步优化")
            else:
                report['recommendations'].append("⚠ 组合夏普比率为负，建议调整风险参数")
    
    if 'regimes' in results and results['regimes'] is not None:
        report['market_regimes'] = "已识别"
        report['recommendations'].append("✅ 市场状态识别成功，建议实施自适应策略")
    
    return report

# ============================================================================
# 主执行
if __name__ == "__main__":
    try:
        print("\n" + "=" * 80)
        print("真实数据回测执行")
        print("=" * 80)
        
        # 准备数据
        backtest_data = prepare_real_backtest_data()
        
        if backtest_data is None:
            print("\n⚠ 使用模拟数据进行回测...")
            # 加载模拟数据
            data_dir = '/root/.openclaw/workspace/quant_system/data/backtest'
            close_prices = pd.read_csv(os.path.join(data_dir, 'price_data.csv'), index_col=0, parse_dates=True)
            volumes = pd.read_csv(os.path.join(data_dir, 'volume_data.csv'), index_col=0, parse_dates=True)
            
            backtest_data = {
                'close_prices': close_prices,
                'volumes': volumes,
                'stock_codes': close_prices.columns[:10].tolist(),
                'data_dir': data_dir + '_simulated'
            }
        
        # 运行回测
        results = run_real_backtest(backtest_data)
        
        # 生成报告
        report = generate_real_backtest_report(results, backtest_data)
        
        # 输出报告
        if report:
            print("\n" + "=" * 80)
            print("真实数据回测报告")
            print("=" * 80)
            
            print(f"\n📅 报告时间: {report['timestamp']}")
            print(f"📊 数据摘要:")
            print(f"  股票数量: {report['data_summary']['stock_count']}只")
            print(f"  回测期间: {report['data_summary']['date_range']}")
            print(f"  交易日数: {report['data_summary']['period_days']}天")
            
            if 'performance' in report and report['performance']:
                print(f"\n📈 绩效指标:")
                for key, value in report['performance'].items():
                    if 'sharpe' in key:
                        print(f"  {key}: {value:.4f}")
                    elif 'return' in key:
                        print(f"  {key}: {value:.6f}")
                    else:
                        print(f"  {key}: {value:.6f}")
            
            if 'recommendations' in report and report['recommendations']:
                print(f"\n💡 建议:")
                for i, rec in enumerate(report['recommendations'], 1):
                    print(f"  {i}. {rec}")
            
            print(f"\n📁 数据目录: {report['data_summary']['data_dir']}")
        
        print("\n" + "=" * 80)
        print("真实数据回测完成")
        print("=" * 80)
        
        print("\n🎯 下一步:")
        print("  1. 扩展数据: 增加更多股票和更长时间范围")
        print("  2. 优化模型: 调整因子和模型参数")
        print("  3. 风险分析: 深入分析组合风险")
        print("  4. 实盘测试: 小资金实盘验证")
        
    except Exception as e:
        print(f"真实数据回测失败: {e}")
        import traceback
        traceback.print_exc()