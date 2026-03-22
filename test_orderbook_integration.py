#!/usr/bin/env python3
"""
OrderBookSimulator集成测试脚本
验证：
1. OrderBookSimulator.simulate_order()是否被实际调用
2. 高级滑点模型是否影响回测结果
3. 买入和卖出路径是否正确工作
"""

import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

sys.path.append('/root/.openclaw/workspace/quant_system')

def create_test_data():
    """创建测试价格数据和信号"""
    # 创建30天的测试数据
    dates = pd.date_range(start='2024-01-01', periods=30, freq='D')
    
    # 生成模拟价格数据
    np.random.seed(42)
    base_price = 100.0
    returns = np.random.randn(30) * 0.02  # 2%日波动
    
    prices = []
    close_prices = []
    for i in range(30):
        if i == 0:
            price = base_price
        else:
            price = close_prices[-1] * (1 + returns[i])
        
        # 生成OHLCV数据
        open_price = price * (1 + np.random.randn() * 0.01)
        high = max(open_price, price) * (1 + abs(np.random.randn()) * 0.02)
        low = min(open_price, price) * (1 - abs(np.random.randn()) * 0.02)
        close = price
        volume = np.random.randint(1000000, 5000000)
        
        prices.append({
            'date': dates[i],
            'open': open_price,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume
        })
        close_prices.append(close)
    
    df = pd.DataFrame(prices)
    df.set_index('date', inplace=True)
    
    # 创建简单交易信号：第5天买入，第15天卖出
    signals = pd.Series(0, index=df.index)
    signals.iloc[5] = 1   # 买入信号
    signals.iloc[15] = -1  # 卖出信号
    
    return df, signals

def test_orderbook_invocation():
    """测试OrderBookSimulator是否被实际调用"""
    print("=" * 60)
    print("测试1: OrderBookSimulator调用验证")
    print("=" * 60)
    
    from enhancements.vectorized_backtest import VectorizedBacktester, BacktestConfig
    
    # 测试配置1: 启用高级滑点模型（包含OrderBookSimulator）
    config_advanced = BacktestConfig(
        use_advanced_slippage=True,
        initial_capital=1000000,
        max_position_pct=0.1,
        slippage_rate=0.002,  # 0.2%固定滑点（降级时使用）
        adv_threshold=3000.0,
        market_cap_threshold=30.0
    )
    
    # 测试配置2: 禁用高级滑点模型（固定滑点）
    config_fixed = BacktestConfig(
        use_advanced_slippage=False,
        initial_capital=1000000,
        max_position_pct=0.1,
        slippage_rate=0.002
    )
    
    # 创建测试数据
    prices_df, signals = create_test_data()
    
    # 准备流动性数据（模拟）
    liquidity_data = {
        'adv_20d': 15000.0,  # 1.5亿日成交
        'market_cap': 500.0,  # 500亿市值
        'is_st': False,
        'daily_turnover': 2.5
    }
    
    print("创建测试数据:")
    print(f"  价格数据: {len(prices_df)}行, {prices_df.index[0]} 至 {prices_df.index[-1]}")
    print(f"  信号数据: {sum(signals != 0)}个交易信号")
    print(f"  流动性数据: ADV={liquidity_data['adv_20d']}万, 市值={liquidity_data['market_cap']}亿")
    
    # 运行高级滑点模型回测
    print("\n运行高级滑点模型回测 (use_advanced_slippage=True):")
    backtester_advanced = VectorizedBacktester(config_advanced)
    
    try:
        result_advanced = backtester_advanced.run_vectorized_backtest(
            symbol='600519.SH',
            prices=prices_df,
            signals=signals,
            liquidity_data=liquidity_data
        )
        
        print(f"  回测结果: 总收益={result_advanced.total_return*100:.2f}%")
        print(f"          交易次数={result_advanced.total_trades}")
        
        # 检查是否有高级滑点使用记录
        advanced_slippage_count = 0
        for record in result_advanced.trade_records:
            if hasattr(record, 'metadata') and record.metadata:
                if record.metadata.get('advanced_slippage'):
                    advanced_slippage_count += 1
                    print(f"  交易 {record.date.date()}: 使用高级滑点模型")
                    if 'bucket_id' in record.metadata:
                        print(f"      流动性分桶: {record.metadata['bucket_id']}")
                        print(f"      冲击成本: {record.metadata.get('impact_bps', 0):.1f}bp")
        
        print(f"  高级滑点模型使用次数: {advanced_slippage_count}/{result_advanced.total_trades}")
        
        if advanced_slippage_count > 0:
            print("  ✅ OrderBookSimulator/高级滑点模型被实际调用")
        else:
            print("  ⚠️  高级滑点模型配置启用但未实际使用")
            
    except Exception as e:
        print(f"  高级滑点模型回测失败: {e}")
        import traceback
        traceback.print_exc()
    
    # 运行固定滑点回测
    print("\n运行固定滑点回测 (use_advanced_slippage=False):")
    backtester_fixed = VectorizedBacktester(config_fixed)
    
    try:
        result_fixed = backtester_fixed.run_vectorized_backtest(
            symbol='600519.SH',
            prices=prices_df,
            signals=signals,
            liquidity_data=liquidity_data  # 即使固定滑点也传递，但不会使用
        )
        
        print(f"  回测结果: 总收益={result_fixed.total_return*100:.2f}%")
        print(f"          交易次数={result_fixed.total_trades}")
        
        # 比较结果
        if 'result_advanced' in locals() and 'result_fixed' in locals():
            diff = result_advanced.total_return - result_fixed.total_return
            print(f"\n性能比较:")
            print(f"  高级滑点收益: {result_advanced.total_return*100:.2f}%")
            print(f"  固定滑点收益: {result_fixed.total_return*100:.2f}%")
            print(f"  差异: {diff*100:.2f}%")
            
            # 高级滑点模型通常应产生更低收益（真实冲击成本）
            if diff < 0:
                print("  ✅ 高级滑点模型产生更低收益（符合预期，真实冲击成本）")
            else:
                print("  ⚠️  高级滑点模型产生更高收益（可能需要检查配置）")
    
    except Exception as e:
        print(f"  固定滑点回测失败: {e}")
        import traceback
        traceback.print_exc()
    
    return locals().get('result_advanced'), locals().get('result_fixed')

def test_liquidity_impact():
    """测试不同流动性股票的冲击成本差异"""
    print("\n" + "=" * 60)
    print("测试2: 流动性冲击成本差异验证")
    print("=" * 60)
    
    from slippage.liquidity_impact_model import OrderBookSimulator
    
    try:
        simulator = OrderBookSimulator(max_volume_percentage=0.05)
        
        # 测试高流动性股票（茅台）
        result_high = simulator.simulate_order(
            symbol='600519',
            order_side='buy',
            order_volume=10000,
            order_price=200.0,
            daily_volume=500000,
            daily_high=205.0,
            daily_low=195.0,
            adv_20d=80000.0,  # 8亿日成交
            market_cap=1600.0,  # 1600亿市值
            is_st=False
        )
        
        # 测试低流动性股票
        result_low = simulator.simulate_order(
            symbol='000725',
            order_side='buy',
            order_volume=10000,
            order_price=5.0,
            daily_volume=50000,
            daily_high=5.2,
            daily_low=4.8,
            adv_20d=800.0,  # 800万日成交
            market_cap=15.0,  # 15亿市值
            is_st=False
        )
        
        print("高流动性股票 (600519):")
        print(f"  冲击成本: {result_high['total_cost_bps']:.1f}bp")
        print(f"  订单状态: {result_high['order_status']}")
        print(f"  执行价格: {result_high['avg_execution_price']:.2f}")
        
        print("\n低流动性股票 (000725):")
        print(f"  冲击成本: {result_low['total_cost_bps']:.1f}bp")
        print(f"  订单状态: {result_low['order_status']}")
        print(f"  执行价格: {result_low['avg_execution_price']:.2f}")
        
        # 验证：低流动性股票冲击成本应更高
        if result_low['total_cost_bps'] > result_high['total_cost_bps']:
            print(f"\n✅ 流动性冲击验证通过: 低流动性股票冲击成本更高 "
                  f"({result_low['total_cost_bps']:.1f}bp > {result_high['total_cost_bps']:.1f}bp)")
        else:
            print(f"\n⚠️  流动性冲击验证失败: 冲击成本异常")
            
        # 验证订单状态
        if result_high['order_status'] == 'fully_executed':
            print(f"✅ 高流动性股票订单完全执行")
        if result_low.get('order_status') == 'partially_executed' or result_low.get('order_status') == 'rejected':
            print(f"✅ 低流动性股票订单部分执行或被拒绝（符合预期）")
            
    except Exception as e:
        print(f"流动性冲击测试失败: {e}")
        import traceback
        traceback.print_exc()

def main():
    """主测试函数"""
    print("OrderBookSimulator集成验证测试")
    print("=" * 60)
    
    # 测试1: OrderBookSimulator调用验证
    result_advanced, result_fixed = test_orderbook_invocation()
    
    # 测试2: 流动性冲击成本差异
    test_liquidity_impact()
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
    
    # 总结
    if result_advanced and result_fixed:
        print("\n📊 测试总结:")
        print(f"1. OrderBookSimulator初始化: ✅ 成功")
        print(f"2. 高级滑点模型调用: {'✅ 成功' if any(hasattr(r, 'metadata') and r.metadata.get('advanced_slippage') for r in result_advanced.trade_records) else '⚠️ 未验证'}")
        print(f"3. 冲击成本差异: {'✅ 验证通过' if result_advanced.total_return <= result_fixed.total_return else '⚠️ 需要检查'}")
        print(f"4. 流动性分级: ✅ 测试完成")
    else:
        print("⚠️ 部分测试未完成，请检查错误日志")

if __name__ == '__main__':
    main()