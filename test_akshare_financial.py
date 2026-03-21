#!/usr/bin/env python3
"""
测试AKShare财报数据接口
"""
import akshare as ak
import pandas as pd
import sys

def test_financial_functions():
    """测试财报相关函数"""
    
    print("=== 测试1: stock_financial_analysis_indicator ===")
    try:
        # 尝试获取财务分析指标
        df = ak.stock_financial_analysis_indicator(symbol="000001", period="2024")
        print(f"数据形状: {df.shape}")
        print(f"列名: {list(df.columns)[:10]}...")
        print(f"前几行:\n{df.head(2)}")
        
        # 查找关键指标
        key_columns = [col for col in df.columns if any(key in col.lower() for key in ['roe', '净利润', '负债', '现金流', '每股收益'])]
        print(f"\n关键指标列: {key_columns}")
        
        if key_columns:
            print(df[key_columns].head())
    except Exception as e:
        print(f"错误: {e}")
    
    print("\n=== 测试2: stock_balance_sheet_by_report_em ===")
    try:
        # 资产负债表
        df = ak.stock_balance_sheet_by_report_em(symbol="000001", date="2024-09-30")
        print(f"资产负债表形状: {df.shape}")
        print(f"列名: {list(df.columns)[:10]}...")
        print(f"关键字段: {[col for col in df.columns if '负债' in col or '资产' in col][:5]}")
    except Exception as e:
        print(f"错误: {e}")
    
    print("\n=== 测试3: stock_profit_sheet_by_report_em ===")
    try:
        # 利润表
        df = ak.stock_profit_sheet_by_report_em(symbol="000001", date="2024-09-30")
        print(f"利润表形状: {df.shape}")
        print(f"列名: {list(df.columns)[:10]}...")
        print(f"关键字段: {[col for col in df.columns if '利润' in col or '收入' in col][:5]}")
    except Exception as e:
        print(f"错误: {e}")
    
    print("\n=== 测试4: stock_cash_flow_sheet_by_report_em ===")
    try:
        # 现金流量表
        df = ak.stock_cash_flow_sheet_by_report_em(symbol="000001", date="2024-09-30")
        print(f"现金流量表形状: {df.shape}")
        print(f"列名: {list(df.columns)[:10]}...")
        print(f"关键字段: {[col for col in df.columns if '现金' in col or '流量' in col][:5]}")
    except Exception as e:
        print(f"错误: {e}")
    
    print("\n=== 测试5: 获取所有A股列表 ===")
    try:
        stocks = ak.stock_zh_a_spot()
        print(f"A股总数: {len(stocks)}")
        print(f"列名: {list(stocks.columns)}")
        print(f"示例股票: {stocks[['代码', '名称']].head(5)}")
    except Exception as e:
        print(f"错误: {e}")

if __name__ == "__main__":
    test_financial_functions()