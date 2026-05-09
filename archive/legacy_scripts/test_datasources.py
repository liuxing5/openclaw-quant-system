#!/usr/bin/env python3
"""
测试 Baostock 和 AKShare 双数据源
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 使用虚拟环境
venv_python = "/root/.openclaw/workspace/quant_venv/bin/python"
if not os.path.exists(venv_python):
    print("虚拟环境未找到，使用系统Python")
else:
    # 重新使用虚拟环境执行
    print("使用虚拟环境执行")
    os.execv(venv_python, [venv_python] + sys.argv)

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import traceback

def test_baostock():
    """测试 Baostock 数据获取"""
    print("\n=== 测试 Baostock ===")
    try:
        import baostock as bs
        
        # 登录
        lg = bs.login()
        if lg.error_code != '0':
            print(f"Baostock 登录失败: {lg.error_msg}")
            return None
        
        print("Baostock 登录成功")
        
        # 获取贵州茅台数据
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        
        rs = bs.query_history_k_data_plus(
            "sh.600519",
            "date,open,high,low,close,volume,amount",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2"  # 前复权
        )
        
        if rs.error_code != '0':
            print(f"Baostock 数据获取失败: {rs.error_msg}")
            return None
        
        data_list = []
        while (rs.error_code == '0') & rs.next():
            data_list.append(rs.get_row_data())
        
        df = pd.DataFrame(data_list, columns=rs.fields)
        if len(df) > 0:
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
            df = df.apply(pd.to_numeric, errors='coerce')
            print(f"Baostock 获取到 {len(df)} 行数据")
            print(df.head())
            print(f"数据范围: {df.index.min()} 到 {df.index.max()}")
            return df
        else:
            print("Baostock 返回空数据")
            return None
            
    except Exception as e:
        print(f"Baostock 测试异常: {e}")
        traceback.print_exc()
        return None
    finally:
        try:
            bs.logout()
        except:
            pass

def test_akshare():
    """测试 AKShare 数据获取"""
    print("\n=== 测试 AKShare ===")
    try:
        import akshare as ak
        
        # 获取贵州茅台数据
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        
        df = ak.stock_zh_a_hist(
            symbol="600519",
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq"
        )
        
        if df is not None and len(df) > 0:
            df.columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'change', 'turnover']
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
            df = df[['open', 'high', 'low', 'close', 'volume', 'amount']]
            print(f"AKShare 获取到 {len(df)} 行数据")
            print(df.head())
            print(f"数据范围: {df.index.min()} 到 {df.index.max()}")
            return df
        else:
            print("AKShare 返回空数据")
            return None
            
    except Exception as e:
        print(f"AKShare 测试异常: {e}")
        traceback.print_exc()
        return None

def compare_data(bs_df, ak_df):
    """比较两个数据源的数据"""
    print("\n=== 数据对比 ===")
    
    if bs_df is None or ak_df is None:
        print("数据源缺失，无法对比")
        return
    
    # 确保索引对齐
    common_dates = bs_df.index.intersection(ak_df.index)
    print(f"共有 {len(common_dates)} 个共同交易日")
    
    if len(common_dates) == 0:
        print("无共同交易日，无法对比")
        return
    
    # 对比收盘价
    bs_close = bs_df.loc[common_dates, 'close']
    ak_close = ak_df.loc[common_dates, 'close']
    
    # 计算差异
    diff = bs_close - ak_close
    diff_pct = diff / ak_close * 100
    
    print(f"收盘价平均差异: {diff.mean():.4f}")
    print(f"收盘价最大差异: {diff.max():.4f}")
    print(f"收盘价最小差异: {diff.min():.4f}")
    print(f"收盘价差异百分比平均: {diff_pct.mean():.4f}%")
    
    # 检查差异是否在可接受范围内（<1%）
    if diff_pct.abs().max() < 1.0:
        print("✅ 数据一致性良好（差异 < 1%）")
    else:
        print("⚠️ 数据差异较大，需进一步检查")

def test_baostock_finance():
    """测试 Baostock 财务数据"""
    print("\n=== 测试 Baostock 财务数据 ===")
    try:
        import baostock as bs
        
        lg = bs.login()
        if lg.error_code != '0':
            print(f"Baostock 登录失败: {lg.error_msg}")
            return
        
        # 查询季报
        rs = bs.query_profit_data(
            code="sh.600519",
            year=2023,
            quarter=4
        )
        
        if rs.error_code != '0':
            print(f"Baostock 财务数据获取失败: {rs.error_msg}")
            return
        
        data_list = []
        while (rs.error_code == '0') & rs.next():
            data_list.append(rs.get_row_data())
        
        if data_list:
            print(f"获取到财务数据 {len(data_list)} 条")
            for row in data_list[:5]:  # 显示前5条
                print(row)
        else:
            print("无财务数据")
            
        bs.logout()
        
    except Exception as e:
        print(f"Baostock 财务数据测试异常: {e}")
        traceback.print_exc()

def test_akshare_finance():
    """测试 AKShare 财务数据"""
    print("\n=== 测试 AKShare 财务数据 ===")
    try:
        import akshare as ak
        
        # 获取资产负债表
        df = ak.stock_financial_report_sina(
            stock="600519",
            symbol="balance"
        )
        
        if df is not None and len(df) > 0:
            print(f"获取到资产负债表 {len(df)} 行，{len(df.columns)} 列")
            print("列名:", df.columns.tolist()[:10])
        else:
            print("无财务数据")
            
    except Exception as e:
        print(f"AKShare 财务数据测试异常: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    print("开始测试双数据源...")
    print(f"Python 版本: {sys.version}")
    print(f"当前目录: {os.getcwd()}")
    
    # 测试K线数据
    bs_data = test_baostock()
    ak_data = test_akshare()
    
    # 对比数据
    compare_data(bs_data, ak_data)
    
    # 测试财务数据
    test_baostock_finance()
    test_akshare_finance()
    
    print("\n=== 测试完成 ===")