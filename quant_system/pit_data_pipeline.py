#!/usr/bin/env python3
"""
PIT (Point-In-Time) 数据管道 - 防止未来函数
确保回测时只能使用当时已公布的数据
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
import warnings
warnings.filterwarnings('ignore')

class PITDataPipeline:
    """PIT数据管道 - 防止未来函数"""
    
    def __init__(self):
        # 数据发布日期延迟映射（单位：天）
        self.report_release_delays = {
            '季报': 30,      # 季报通常在季度结束后30天内发布
            '半年报': 60,    # 半年报在半年结束后60天内发布  
            '年报': 90,      # 年报在年度结束后90天内发布
            '快报': 15,      # 业绩快报通常在15天内发布
            '预告': 10       # 业绩预告通常在10天内发布
        }
        
        # 数据缓存
        self.data_cache = {}
        
    def get_pit_stock_data(self, 
                          symbol: str, 
                          date: str,
                          lookback_days: int = 0) -> Dict[str, Any]:
        """
        获取指定日期的PIT数据
        
        Args:
            symbol: 股票代码
            date: 查询日期 (YYYY-MM-DD)
            lookback_days: 向前回溯天数
            
        Returns:
            PIT数据字典
        """
        query_date = pd.to_datetime(date)
        start_date = (query_date - pd.Timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        end_date = date
        
        # 获取原始数据
        raw_data = self._get_raw_data(symbol, start_date, end_date)
        
        if raw_data is None or raw_data.empty:
            return {
                'success': False,
                'error': f'无{symbol}在{date}的数据',
                'pit_date': date
            }
        
        # 应用PIT过滤
        pit_data = self._apply_pit_filter(raw_data, query_date)
        
        return {
            'success': True,
            'data': pit_data,
            'pit_date': date,
            'lookback_days': lookback_days,
            'original_rows': len(raw_data),
            'pit_rows': len(pit_data) if pit_data is not None else 0
        }
    
    def get_pit_fundamentals(self,
                           symbol: str,
                           date: str) -> Dict[str, Any]:
        """
        获取指定日期的PIT基本面数据
        
        Args:
            symbol: 股票代码
            date: 查询日期
            
        Returns:
            PIT基本面数据
        """
        query_date = pd.to_datetime(date)
        
        # 模拟基本面数据（实际应从数据库获取）
        # 这里简化处理，实际需要根据报告发布日期进行PIT过滤
        fundamentals = self._simulate_fundamentals(symbol, query_date)
        
        if fundamentals is None:
            return {
                'success': False,
                'error': f'无{symbol}在{date}的基本面数据',
                'pit_date': date
            }
        
        # 应用PIT过滤：移除在查询日期后发布的数据
        pit_fundamentals = {}
        for report_type, report_data in fundamentals.items():
            if self._is_data_available_at_date(report_data, query_date):
                pit_fundamentals[report_type] = report_data
        
        return {
            'success': True,
            'data': pit_fundamentals,
            'pit_date': date,
            'fundamental_types': list(pit_fundamentals.keys())
        }
    
    def get_all_stocks_at_date(self, 
                              date: str,
                              min_price: float = 1.0,
                              min_volume: float = 1000000) -> List[str]:
        """
        获取指定日期全市场可交易股票列表
        
        Args:
            date: 查询日期
            min_price: 最低价格限制（过滤低价股）
            min_volume: 最低成交量限制（过滤僵尸股）
            
        Returns:
            可交易股票代码列表
        """
        # 这里应该从数据库获取全市场股票
        # 简化处理：返回一个模拟的股票列表
        query_date = pd.to_datetime(date)
        
        # 模拟全市场股票（实际应来自数据库）
        all_stocks = self._get_all_stocks_from_db()
        
        # 过滤条件
        tradable_stocks = []
        for stock in all_stocks:
            # 检查是否有交易数据
            stock_data = self._get_raw_data(stock, date, date)
            if stock_data is None or stock_data.empty:
                continue
            
            # 价格过滤
            if 'close' in stock_data.columns:
                price = stock_data['close'].iloc[0]
                if price < min_price:
                    continue
            
            # 成交量过滤
            if 'volume' in stock_data.columns:
                volume = stock_data['volume'].iloc[0]
                if volume < min_volume:
                    continue
            
            # 上市时间过滤（确保股票已上市）
            listing_date = self._get_listing_date(stock)
            if listing_date and query_date < listing_date:
                continue
            
            tradable_stocks.append(stock)
        
        return tradable_stocks[:4000]  # 限制4000只股票
    
    def _apply_pit_filter(self, data: pd.DataFrame, query_date: pd.Timestamp) -> pd.DataFrame:
        """应用PIT过滤"""
        if data is None or data.empty:
            return data
        
        # 复制数据以避免修改原始数据
        filtered_data = data.copy()
        
        # 确保数据索引是日期类型
        if not isinstance(filtered_data.index, pd.DatetimeIndex):
            if 'date' in filtered_data.columns:
                filtered_data['date'] = pd.to_datetime(filtered_data['date'])
                filtered_data.set_index('date', inplace=True)
        
        # 过滤掉查询日期之后的数据
        filtered_data = filtered_data[filtered_data.index <= query_date]
        
        return filtered_data
    
    def _is_data_available_at_date(self, report_data: Dict, query_date: pd.Timestamp) -> bool:
        """检查数据在查询日期是否可用"""
        if 'announce_date' not in report_data:
            return True  # 没有发布日期信息，假设可用
        
        announce_date = pd.to_datetime(report_data['announce_date'])
        report_type = report_data.get('report_type', '季报')
        
        # 考虑报告发布延迟
        delay_days = self.report_release_delays.get(report_type, 30)
        available_date = announce_date + pd.Timedelta(days=delay_days)
        
        return query_date >= available_date
    
    def _get_raw_data(self, symbol: str, start_date: str, end_date: str):
        """获取原始数据（简化实现）"""
        # 简化实现：返回None
        # 实际应该从数据库或API获取数据
        return None
    
    def _get_all_stocks_from_db(self):
        """从数据库获取所有股票（简化实现）"""
        # 返回测试股票列表
        return [f"600{i:03d}" for i in range(1, 101)] + \
               [f"000{i:03d}" for i in range(1, 101)] + \
               [f"300{i:03d}" for i in range(1, 101)]
    
    def _get_listing_date(self, symbol: str):
        """获取上市日期（简化实现）"""
        # 返回一个很早的日期，确保股票已上市
        return pd.Timestamp('2000-01-01')