#!/usr/bin/env python3
"""
测试AKShare数据源可用性
测试范围：A股股票列表、行情数据、基金数据
"""

import akshare as ak
import pandas as pd
import sys

def test_stock_list():
    """测试A股股票列表"""
    print("=== 测试A股股票列表 ===")
    try:
        # 获取A股股票列表
        stock_info_a_code_name_df = ak.stock_info_a_code_name()
        print(f"A股股票数量: {len(stock_info_a_code_name_df)}")
        print("前5只股票:")
        print(stock_info_a_code_name_df.head())
        print("列名:", stock_info_a_code_name_df.columns.tolist())
        return True
    except Exception as e:
        print(f"获取A股股票列表失败: {e}")
        return False

def test_stock_zh_a_hist():
    """测试A股历史行情数据"""
    print("\n=== 测试A股历史行情数据 ===")
    try:
        # 测试获取贵州茅台(600519)的历史数据
        stock_zh_a_hist_df = ak.stock_zh_a_hist(symbol="600519", period="daily", start_date="20240101", end_date="20240313", adjust="qfq")
        print(f"贵州茅台历史数据条数: {len(stock_zh_a_hist_df)}")
        print("数据列名:", stock_zh_a_hist_df.columns.tolist())
        print("最新5条数据:")
        print(stock_zh_a_hist_df.tail())
        return True
    except Exception as e:
        print(f"获取A股历史行情数据失败: {e}")
        return False

def test_fund_data():
    """测试基金数据"""
    print("\n=== 测试基金数据 ===")
    try:
        # 获取基金列表
        fund_em_open_fund_daily_df = ak.fund_em_open_fund_daily()
        print(f"基金数量: {len(fund_em_open_fund_daily_df)}")
        print("前5只基金:")
        print(fund_em_open_fund_daily_df.head())
        print("列名:", fund_em_open_fund_daily_df.columns.tolist())
        return True
    except Exception as e:
        print(f"获取基金数据失败: {e}")
        return False

def test_stock_industry():
    """测试行业分类数据"""
    print("\n=== 测试行业分类数据 ===")
    try:
        # 获取申万行业分类
        stock_industry_sw_df = ak.stock_industry_sw()
        print(f"申万行业分类数量: {len(stock_industry_sw_df)}")
        print("行业分类示例:")
        print(stock_industry_sw_df.head())
        return True
    except Exception as e:
        print(f"获取行业分类数据失败: {e}")
        return False

def test_us_stock():
    """测试美股数据"""
    print("\n=== 测试美股数据 ===")
    try:
        # 获取美股列表
        stock_us_spot_em_df = ak.stock_us_spot_em()
        print(f"美股数量: {len(stock_us_spot_em_df)}")
        print("美股列名:", stock_us_spot_em_df.columns.tolist())
        
        # 筛选科技股（示例：包含'Tech'或'科技'的行业）
        tech_stocks = stock_us_spot_em_df[stock_us_spot_em_df['行业'].str.contains('Tech|科技', case=False, na=False)]
        print(f"科技股数量: {len(tech_stocks)}")
        print("科技股示例:")
        print(tech_stocks[['名称', '行业']].head())
        return True
    except Exception as e:
        print(f"获取美股数据失败: {e}")
        return False

def main():
    print("开始测试AKShare数据源...")
    print(f"AKShare版本: {ak.__version__ if hasattr(ak, '__version__') else '未知'}")
    
    results = []
    results.append(("A股股票列表", test_stock_list()))
    results.append(("A股历史行情", test_stock_zh_a_hist()))
    results.append(("基金数据", test_fund_data()))
    results.append(("行业分类", test_stock_industry()))
    results.append(("美股数据", test_us_stock()))
    
    print("\n=== 测试结果汇总 ===")
    success_count = sum([1 for _, success in results if success])
    total_count = len(results)
    
    for name, success in results:
        status = "✅ 成功" if success else "❌ 失败"
        print(f"{name}: {status}")
    
    print(f"\n总计: {success_count}/{total_count} 个测试通过")
    
    if success_count >= 3:
        print("✅ AKShare数据源测试基本通过，可以开始数据采集")
        return 0
    else:
        print("❌ AKShare数据源测试失败较多，需要检查")
        return 1

if __name__ == "__main__":
    sys.exit(main())