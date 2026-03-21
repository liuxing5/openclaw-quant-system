#!/usr/bin/env python3
"""
探索AKShare API的正确用法
"""
import akshare as ak
import inspect
import pandas as pd

def explore_function(func_name):
    """探索函数签名和用法"""
    if not hasattr(ak, func_name):
        print(f"函数 {func_name} 不存在")
        return
    
    func = getattr(ak, func_name)
    
    print(f"\n=== {func_name} ===")
    
    # 获取函数签名
    try:
        sig = inspect.signature(func)
        print(f"签名: {sig}")
    except:
        print("无法获取签名")
    
    # 尝试无参数调用查看错误信息
    try:
        result = func()
        print(f"无参数调用结果类型: {type(result)}")
        if isinstance(result, pd.DataFrame):
            print(f"数据形状: {result.shape}")
            print(f"列名: {list(result.columns)[:10]}")
    except TypeError as e:
        print(f"无参数调用错误: {e}")
    
    # 尝试常见参数
    test_cases = [
        {"symbol": "000001"},
        {"stock": "000001"},
        {"code": "000001"},
        {"ts_code": "000001.SZ"},
        {"symbol": "000001", "period": "2023"},
        {"symbol": "000001", "date": "2023-12-31"},
        {"symbol": "sz000001"},
        {"symbol": "sh600000"},
    ]
    
    for i, params in enumerate(test_cases):
        try:
            result = func(**params)
            print(f"\n测试用例 {i+1} 成功: {params}")
            print(f"  结果类型: {type(result)}")
            if isinstance(result, pd.DataFrame):
                print(f"  数据形状: {result.shape}")
                if not result.empty:
                    print(f"  前几列: {list(result.columns)[:5]}")
                    print(f"  首行数据: {result.iloc[0].to_dict() if len(result) > 0 else '空'}")
            elif isinstance(result, list):
                print(f"  列表长度: {len(result)}")
                if result:
                    print(f"  首元素: {result[0]}")
            break  # 找到可行参数就停止
        except Exception as e:
            continue
    
    # 如果所有测试都失败，尝试打印函数文档
    try:
        doc = inspect.getdoc(func)
        if doc and len(doc) > 0:
            print(f"\n文档摘要: {doc[:200]}...")
    except:
        pass

# 探索财报相关函数
financial_functions = [
    'stock_financial_analysis_indicator',
    'stock_financial_analysis_indicator_em',
    'stock_balance_sheet_by_report_em',
    'stock_profit_sheet_by_report_em',
    'stock_cash_flow_sheet_by_report_em',
    'stock_financial_report_sz_stock',
    'stock_financial_report_sh_stock',
]

print("开始探索AKShare财报API...")
for func_name in financial_functions:
    explore_function(func_name)

# 探索实时行情
print("\n=== 探索实时行情 ===")
try:
    # 先获取A股列表
    stock_list = ak.stock_info_a_code_name()
    print(f"A股列表形状: {stock_list.shape if hasattr(stock_list, 'shape') else '未知'}")
    if isinstance(stock_list, pd.DataFrame) and not stock_list.empty:
        print(f"示例股票: {stock_list.head(3)}")
except Exception as e:
    print(f"获取股票列表错误: {e}")

# 探索财务指标
print("\n=== 探索财务指标 ===")
try:
    # 尝试获取财务指标
    indicators = ak.stock_financial_analysis_indicator(stock="000001", indicator="每股收益")
    print(f"财务指标类型: {type(indicators)}")
    if isinstance(indicators, pd.DataFrame):
        print(f"指标数据形状: {indicators.shape}")
        print(f"指标列: {list(indicators.columns)}")
except Exception as e:
    print(f"财务指标错误: {e}")