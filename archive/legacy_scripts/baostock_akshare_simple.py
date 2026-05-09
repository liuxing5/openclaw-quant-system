#!/usr/bin/env python3
"""
简化版 Baostock + AKShare 双数据源方案
可直接集成到现有量化系统
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import os
import sys
from typing import Optional, Tuple

class DualDataSource:
    """
    简化双数据源
    优先级：1. Baostock, 2. AKShare
    """
    
    def __init__(self, cache_dir: str = "/tmp/quant_simple_cache"):
        self.cache_dir = cache_dir
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)
        
        # 初始化状态
        self.baostock_available = False
        self.akshare_available = False
        
        # 延迟导入测试
        self._test_sources()
    
    def _test_sources(self):
        """测试数据源可用性"""
        # 测试 Baostock
        try:
            import baostock as bs
            lg = bs.login()
            if lg.error_code == '0':
                self.baostock_available = True
                bs.logout()
                print("✅ Baostock 可用")
            else:
                print(f"⚠️ Baostock 登录失败: {lg.error_msg}")
        except Exception as e:
            print(f"❌ Baostock 不可用: {e}")
        
        # 测试 AKShare
        try:
            import akshare as ak
            # 简单测试导入
            self.akshare_available = True
            print("✅ AKShare 可用")
        except Exception as e:
            print(f"❌ AKShare 不可用: {e}")
    
    def get_daily_baostock(self, symbol: str, start_date: str, end_date: str, adjust: str = 'qfq') -> Optional[pd.DataFrame]:
        """使用 Baostock 获取日线数据"""
        if not self.baostock_available:
            return None
        
        try:
            import baostock as bs
            
            # 登录
            lg = bs.login()
            if lg.error_code != '0':
                return None
            
            # 转换代码格式
            if '.' in symbol:
                code, exchange = symbol.split('.')
                bs_code = f"{exchange.lower()}.{code}"
            else:
                bs_code = f"sh.{symbol}" if symbol.startswith('6') else f"sz.{symbol}"
            
            # 映射复权参数
            adjust_map = {'qfq': '2', 'hfq': '1', 'none': '3'}
            adjust_flag = adjust_map.get(adjust, '2')
            
            # 查询数据
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume,amount",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag=adjust_flag
            )
            
            if rs.error_code != '0':
                bs.logout()
                return None
            
            # 解析数据
            data_list = []
            while (rs.error_code == '0') & rs.next():
                data_list.append(rs.get_row_data())
            
            bs.logout()
            
            if not data_list:
                return None
            
            # 转换为 DataFrame
            df = pd.DataFrame(data_list, columns=rs.fields)
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
            
            # 转换数据类型
            for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            
            return df
            
        except Exception as e:
            print(f"Baostock 获取数据失败: {e}")
            return None
    
    def get_daily_akshare(self, symbol: str, start_date: str, end_date: str, adjust: str = 'qfq') -> Optional[pd.DataFrame]:
        """使用 AKShare 获取日线数据"""
        if not self.akshare_available:
            return None
        
        try:
            import akshare as ak
            
            # 提取纯数字代码
            if '.' in symbol:
                code = symbol.split('.')[0]
            else:
                code = symbol
            
            # 转换日期格式
            start_fmt = start_date.replace('-', '')
            end_fmt = end_date.replace('-', '')
            
            # 获取数据（设置超时）
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_fmt,
                end_date=end_fmt,
                adjust=adjust
            )
            
            if df is None or len(df) == 0:
                return None
            
            # 标准化列名
            df.columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'change', 'turnover']
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
            
            # 确保列顺序一致
            df = df[['open', 'high', 'low', 'close', 'volume', 'amount']]
            
            return df
            
        except Exception as e:
            print(f"AKShare 获取数据失败: {e}")
            return None
    
    def get_daily(self, symbol: str, start_date: str, end_date: str, adjust: str = 'qfq') -> pd.DataFrame:
        """
        获取日线数据（自动切换数据源）
        """
        # 先尝试 Baostock
        if self.baostock_available:
            df = self.get_daily_baostock(symbol, start_date, end_date, adjust)
            if df is not None and len(df) > 0:
                print(f"✅ 使用 Baostock 获取 {symbol} 数据 ({len(df)} 行)")
                return df
        
        # 再尝试 AKShare
        if self.akshare_available:
            df = self.get_daily_akshare(symbol, start_date, end_date, adjust)
            if df is not None and len(df) > 0:
                print(f"✅ 使用 AKShare 获取 {symbol} 数据 ({len(df)} 行)")
                return df
        
        raise RuntimeError(f"无法获取 {symbol} 数据，所有数据源都失败")
    
    def get_daily_with_fallback(self, symbol: str, start_date: str, end_date: str, adjust: str = 'qfq') -> Tuple[pd.DataFrame, str]:
        """
        获取日线数据并返回使用的数据源
        :return: (数据 DataFrame, 数据源名称)
        """
        # 先尝试 Baostock
        if self.baostock_available:
            df = self.get_daily_baostock(symbol, start_date, end_date, adjust)
            if df is not None and len(df) > 0:
                return df, 'baostock'
        
        # 再尝试 AKShare
        if self.akshare_available:
            df = self.get_daily_akshare(symbol, start_date, end_date, adjust)
            if df is not None and len(df) > 0:
                return df, 'akshare'
        
        raise RuntimeError(f"无法获取 {symbol} 数据，所有数据源都失败")

# 使用示例
if __name__ == "__main__":
    print("=== 简化双数据源测试 ===")
    
    # 创建数据源
    ds = DualDataSource()
    
    # 测试数据获取
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    
    symbols = ['600519.SH', '000001.SZ']
    
    for symbol in symbols:
        print(f"\n获取 {symbol} ({start_date} 到 {end_date})...")
        try:
            df, source = ds.get_daily_with_fallback(symbol, start_date, end_date, 'qfq')
            print(f"  成功从 {source} 获取 {len(df)} 行数据")
            print(f"  最新数据:")
            print(df.tail(1))
        except Exception as e:
            print(f"  失败: {e}")
    
    print("\n✅ 双数据源方案测试完成")