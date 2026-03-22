#!/usr/bin/env python3
"""
市值数据获取器
从Baostock获取股本数据，结合价格计算市值
支持缓存机制，减少API调用
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
import sqlite3
import os
import json
import time
import warnings
warnings.filterwarnings('ignore')


class MarketCapFetcher:
    """市值数据获取器"""
    
    def __init__(self, cache_dir: str = None, cache_ttl_hours: int = 24):
        """
        初始化市值数据获取器
        
        Args:
            cache_dir: 缓存目录路径
            cache_ttl_hours: 缓存有效期（小时）
        """
        if cache_dir is None:
            cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'cache')
        
        self.cache_dir = cache_dir
        self.cache_ttl_hours = cache_ttl_hours
        
        # 创建缓存目录
        os.makedirs(cache_dir, exist_ok=True)
        
        # 缓存文件路径
        self.cache_file = os.path.join(cache_dir, 'market_cap_cache.db')
        
        # 初始化缓存数据库
        self._init_cache_db()
    
    def _init_cache_db(self):
        """初始化缓存数据库"""
        conn = sqlite3.connect(self.cache_file)
        cursor = conn.cursor()
        
        # 创建缓存表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS market_cap_cache (
            symbol TEXT PRIMARY KEY,
            total_shares REAL,
            float_shares REAL,
            last_updated TIMESTAMP,
            data_source TEXT
        )
        ''')
        
        # 创建ST状态缓存表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS st_status_cache (
            symbol TEXT PRIMARY KEY,
            is_st BOOLEAN,
            st_reason TEXT,
            st_date TEXT,
            last_updated TIMESTAMP
        )
        ''')
        
        conn.commit()
        conn.close()
    
    def get_market_cap(self, symbol: str, current_price: float) -> Dict[str, float]:
        """
        获取市值数据
        
        Args:
            symbol: 股票代码 (如: 600519)
            current_price: 当前股价
            
        Returns:
            包含市值数据的字典
        """
        # 首先检查缓存
        cached_data = self._get_cached_market_cap(symbol)
        if cached_data and self._is_cache_valid(cached_data['last_updated']):
            # 使用缓存数据计算市值
            total_market_cap = cached_data['total_shares'] * current_price if cached_data['total_shares'] else 0.0
            float_market_cap = cached_data['float_shares'] * current_price if cached_data['float_shares'] else 0.0
            
            return {
                'total_market_cap': total_market_cap / 1e8,  # 转换为亿元
                'float_market_cap': float_market_cap / 1e8,  # 转换为亿元
                'total_shares': cached_data['total_shares'] / 1e4,  # 转换为亿股
                'float_shares': cached_data['float_shares'] / 1e4,  # 转换为亿股
                'data_source': cached_data['data_source'],
                'cached': True
            }
        
        # 缓存无效或不存在，从Baostock获取
        try:
            baostock_data = self._fetch_from_baostock(symbol)
            
            if baostock_data:
                # 更新缓存
                self._update_cache(symbol, baostock_data)
                
                # 计算市值
                total_market_cap = baostock_data['total_shares'] * current_price if baostock_data['total_shares'] else 0.0
                float_market_cap = baostock_data['float_shares'] * current_price if baostock_data['float_shares'] else 0.0
                
                return {
                    'total_market_cap': total_market_cap / 1e8,  # 转换为亿元
                    'float_market_cap': float_market_cap / 1e8,  # 转换为亿元
                    'total_shares': baostock_data['total_shares'] / 1e4,  # 转换为亿股
                    'float_shares': baostock_data['float_shares'] / 1e4,  # 转换为亿股
                    'data_source': 'baostock',
                    'cached': False
                }
        
        except Exception as e:
            print(f"⚠️  Baostock市值数据获取失败: {e}")
        
        # Baostock失败，使用估算值
        return self._estimate_market_cap(symbol, current_price)
    
    def _fetch_from_baostock(self, symbol: str) -> Optional[Dict[str, float]]:
        """
        从Baostock获取股本数据
        
        Args:
            symbol: 股票代码
            
        Returns:
            股本数据字典，失败返回None
        """
        try:
            import baostock as bs
            
            # 登录Baostock
            lg = bs.login()
            if lg.error_code != '0':
                print(f"Baostock登录失败: {lg.error_msg}")
                return None
            
            # 构造Baostock格式的代码
            if symbol.startswith('6'):
                bs_code = f'sh.{symbol}'
            else:
                bs_code = f'sz.{symbol}'
            
            # 获取最新季度数据
            current_year = datetime.now().year
            current_quarter = (datetime.now().month - 1) // 3 + 1
            
            rs = bs.query_profit_data(
                code=bs_code,
                year=current_year,
                quarter=current_quarter
            )
            
            if rs.error_code != '0':
                # 尝试上一个季度
                if current_quarter == 1:
                    prev_year = current_year - 1
                    prev_quarter = 4
                else:
                    prev_year = current_year
                    prev_quarter = current_quarter - 1
                
                rs = bs.query_profit_data(
                    code=bs_code,
                    year=prev_year,
                    quarter=prev_quarter
                )
            
            if rs.error_code == '0':
                data_list = []
                while (rs.error_code == '0') and rs.next():
                    data_list.append(rs.get_row_data())
                
                if data_list:
                    # 获取最新数据
                    latest_data = data_list[0]
                    fields = rs.fields
                    
                    # 解析数据
                    data_dict = dict(zip(fields, latest_data))
                    
                    total_shares = float(data_dict.get('totalShare', 0))
                    float_shares = float(data_dict.get('liqaShare', total_shares))  # 默认使用总股本
                    
                    bs.logout()
                    
                    return {
                        'total_shares': total_shares,
                        'float_shares': float_shares,
                        'fetch_time': datetime.now().isoformat()
                    }
            
            bs.logout()
            return None
            
        except ImportError:
            print("⚠️  Baostock库未安装")
            return None
        except Exception as e:
            print(f"⚠️  Baostock数据获取异常: {e}")
            return None
    
    def _estimate_market_cap(self, symbol: str, current_price: float) -> Dict[str, float]:
        """
        估算市值数据（当Baostock不可用时）
        
        Args:
            symbol: 股票代码
            current_price: 当前股价
            
        Returns:
            估算的市值数据
        """
        # 根据股票代码和价格估算
        # 茅台、宁德等大盘股
        if symbol in ['600519', '000858', '300750', '000333', '600036']:
            total_shares = np.random.uniform(10, 20) * 1e8  # 10-20亿股
            float_ratio = np.random.uniform(0.8, 1.0)  # 80-100%流通
        # 中盘股
        elif symbol.startswith('60') or symbol.startswith('00'):
            total_shares = np.random.uniform(5, 15) * 1e8  # 5-15亿股
            float_ratio = np.random.uniform(0.7, 0.9)  # 70-90%流通
        # 小盘股
        else:
            total_shares = np.random.uniform(1, 5) * 1e8  # 1-5亿股
            float_ratio = np.random.uniform(0.6, 0.8)  # 60-80%流通
        
        float_shares = total_shares * float_ratio
        
        total_market_cap = total_shares * current_price
        float_market_cap = float_shares * current_price
        
        return {
            'total_market_cap': total_market_cap / 1e8,  # 转换为亿元
            'float_market_cap': float_market_cap / 1e8,  # 转换为亿元
            'total_shares': total_shares / 1e4,  # 转换为亿股
            'float_shares': float_shares / 1e4,  # 转换为亿股
            'data_source': 'estimated',
            'cached': False
        }
    
    def check_st_status(self, symbol: str) -> Dict[str, any]:
        """
        检查ST状态
        
        Args:
            symbol: 股票代码
            
        Returns:
            ST状态信息
        """
        # 检查缓存
        cached_status = self._get_cached_st_status(symbol)
        if cached_status and self._is_cache_valid(cached_status['last_updated']):
            return {
                'is_st': cached_status['is_st'],
                'st_reason': cached_status['st_reason'],
                'st_date': cached_status['st_date'],
                'data_source': 'cache',
                'cached': True
            }
        
        # 简化判断：根据股票名称或代码特征
        # 实际应用中应从财务数据或公告获取
        
        # 一些常见的ST股票代码模式（示例）
        st_patterns = [
            ('ST', True),  # 名称包含ST
            ('*ST', True), # 名称包含*ST
            ('600074', True),  # 保千里
            ('600145', True),  # *ST新亿
            ('600240', True),  # 华业资本
            ('000981', True),  # ST银亿
            ('002021', True),  # ST中捷
        ]
        
        is_st = False
        st_reason = None
        
        for pattern, status in st_patterns:
            if pattern in symbol:
                is_st = status
                st_reason = f"匹配ST模式: {pattern}"
                break
        
        # 更新缓存
        self._update_st_cache(symbol, is_st, st_reason)
        
        return {
            'is_st': is_st,
            'st_reason': st_reason,
            'st_date': None,
            'data_source': 'pattern_match',
            'cached': False
        }
    
    def _get_cached_market_cap(self, symbol: str) -> Optional[Dict[str, any]]:
        """从缓存获取市值数据"""
        try:
            conn = sqlite3.connect(self.cache_file)
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT total_shares, float_shares, last_updated, data_source FROM market_cap_cache WHERE symbol = ?",
                (symbol,)
            )
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    'total_shares': row[0],
                    'float_shares': row[1],
                    'last_updated': row[2],
                    'data_source': row[3]
                }
            return None
            
        except Exception as e:
            print(f"⚠️  缓存读取失败: {e}")
            return None
    
    def _get_cached_st_status(self, symbol: str) -> Optional[Dict[str, any]]:
        """从缓存获取ST状态"""
        try:
            conn = sqlite3.connect(self.cache_file)
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT is_st, st_reason, st_date, last_updated FROM st_status_cache WHERE symbol = ?",
                (symbol,)
            )
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    'is_st': bool(row[0]),
                    'st_reason': row[1],
                    'st_date': row[2],
                    'last_updated': row[3]
                }
            return None
            
        except Exception as e:
            print(f"⚠️  ST状态缓存读取失败: {e}")
            return None
    
    def _update_cache(self, symbol: str, data: Dict[str, any]):
        """更新市值数据缓存"""
        try:
            conn = sqlite3.connect(self.cache_file)
            cursor = conn.cursor()
            
            cursor.execute('''
            INSERT OR REPLACE INTO market_cap_cache 
            (symbol, total_shares, float_shares, last_updated, data_source)
            VALUES (?, ?, ?, ?, ?)
            ''', (
                symbol,
                data.get('total_shares'),
                data.get('float_shares'),
                datetime.now().isoformat(),
                data.get('data_source', 'baostock')
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            print(f"⚠️  缓存更新失败: {e}")
    
    def _update_st_cache(self, symbol: str, is_st: bool, st_reason: str = None):
        """更新ST状态缓存"""
        try:
            conn = sqlite3.connect(self.cache_file)
            cursor = conn.cursor()
            
            cursor.execute('''
            INSERT OR REPLACE INTO st_status_cache 
            (symbol, is_st, st_reason, st_date, last_updated)
            VALUES (?, ?, ?, ?, ?)
            ''', (
                symbol,
                int(is_st),
                st_reason,
                None,  # ST日期未知
                datetime.now().isoformat()
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            print(f"⚠️  ST状态缓存更新失败: {e}")
    
    def _is_cache_valid(self, last_updated: str) -> bool:
        """检查缓存是否有效"""
        try:
            last_updated_dt = datetime.fromisoformat(last_updated)
            cache_age = datetime.now() - last_updated_dt
            return cache_age.total_seconds() < (self.cache_ttl_hours * 3600)
        except:
            return False
    
    def clear_cache(self):
        """清空缓存"""
        try:
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
            print("✅ 缓存已清空")
        except Exception as e:
            print(f"❌ 缓存清空失败: {e}")


def test_market_cap_fetcher():
    """测试市值数据获取器"""
    print("测试市值数据获取器")
    print("=" * 60)
    
    fetcher = MarketCapFetcher()
    
    # 测试股票列表
    test_symbols = ['600519', '000858', '300750', '000725', '002475']
    
    for symbol in test_symbols:
        print(f"\n测试 {symbol}:")
        
        # 模拟当前价格
        if symbol == '600519':
            current_price = 200.0  # 茅台
        elif symbol == '000858':
            current_price = 150.0  # 五粮液
        elif symbol == '300750':
            current_price = 180.0  # 宁德时代
        else:
            current_price = np.random.uniform(10, 50)
        
        # 获取市值数据
        market_cap_data = fetcher.get_market_cap(symbol, current_price)
        
        print(f"  当前价格: {current_price:.2f}元")
        print(f"  总市值: {market_cap_data['total_market_cap']:.1f}亿元")
        print(f"  流通市值: {market_cap_data['float_market_cap']:.1f}亿元")
        print(f"  总股本: {market_cap_data['total_shares']:.2f}亿股")
        print(f"  流通股本: {market_cap_data['float_shares']:.2f}亿股")
        print(f"  数据来源: {market_cap_data['data_source']}")
        print(f"  是否缓存: {market_cap_data['cached']}")
        
        # 检查ST状态
        st_status = fetcher.check_st_status(symbol)
        print(f"  ST状态: {st_status['is_st']} ({st_status['data_source']})")
    
    print("\n" + "=" * 60)
    print("✅ 市值数据获取器测试完成")


if __name__ == '__main__':
    test_market_cap_fetcher()