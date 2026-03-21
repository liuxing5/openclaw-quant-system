#!/usr/bin/env python3
"""
专业数据管道 V2 - 本地数据库优先
重构版本，解决网络依赖和数据可靠性问题
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import json
import os
from typing import Dict, List, Optional, Tuple, Any
import traceback
import warnings
warnings.filterwarnings('ignore')

# 导入本地数据库管理器
try:
    from database.database_manager import DatabaseManager
    DATABASE_AVAILABLE = True
except ImportError as e:
    print(f"数据库模块导入失败: {e}")
    DATABASE_AVAILABLE = False

# 尝试导入各种网络数据源（备用）
try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False
    ak = None

try:
    from web_interface.tencent_data import get_tencent_stock_data
    TENCENT_AVAILABLE = True
except ImportError:
    TENCENT_AVAILABLE = False


class DataPipelineV2:
    """专业数据管道 V2 - 本地数据库优先"""
    
    def __init__(self, cache_dir: str = None):
        self.cache_dir = cache_dir or "/root/.openclaw/workspace/quant_system/data/cache"
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # 初始化数据库管理器
        self.db = DatabaseManager() if DATABASE_AVAILABLE else None
        
        # 数据源优先级（本地优先）
        self.data_sources = []
        
        # 1. 本地数据库（最高优先级）
        if DATABASE_AVAILABLE and self.db:
            self.data_sources.append(('database', '本地数据库', self._fetch_from_database))
        
        # 2. 网络数据源（备用）
        if AKSHARE_AVAILABLE:
            self.data_sources.append(('akshare', 'AKShare', self._fetch_akshare))
        
        # 🚨 关键修复：移除腾讯财经数据源，因为其OHLC是按固定比例缩放的假数据
        # 用户指出问题：open = close * 0.99，high = close * 1.02，volume = 1000000（常数）
        # 这批数据被写入数据库后，所有依赖OHLC的因子（ATR、布林带、振幅、成交量突破）的计算结果全部是错的
        # if TENCENT_AVAILABLE:
        #     self.data_sources.append(('tencent', '腾讯财经', self._fetch_tencent))
        
        # 3. 模拟数据（最后防线）
        self.data_sources.append(('simulated', '模拟数据', self._fetch_simulated))
        
        print(f"数据管道V2初始化完成，可用数据源: {len(self.data_sources)}个")
        for source_id, source_name, _ in self.data_sources:
            print(f"  - {source_id}: {source_name}")
    
    def _fetch_from_database(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """从本地数据库获取数据（最高优先级）"""
        if not self.db:
            raise RuntimeError("数据库不可用")
        
        print(f"  本地数据库查询 {symbol} ({start_date} 至 {end_date})...")
        
        # 从数据库获取数据
        df = self.db.get_daily_prices(symbol, start_date, end_date)
        
        if df is not None and not df.empty:
            print(f"    数据库返回 {len(df)} 条记录")
            
            # 检查数据完整性
            expected_dates = pd.date_range(start=start_date, end=end_date, freq='B')
            missing_dates = set(expected_dates) - set(df.index)
            
            if missing_dates:
                print(f"    警告: 数据库缺少 {len(missing_dates)} 个交易日数据")
                # 可以在这里触发自动补充，但为了简单先返回现有数据
                
            return df
        else:
            print(f"    数据库无数据")
            raise RuntimeError("数据库无数据")
    
    def _fetch_akshare(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """从AKShare获取数据（网络备用）"""
        if not AKSHARE_AVAILABLE:
            raise RuntimeError("AKShare不可用")
        
        max_retries = 2
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                print(f"  AKShare获取 {symbol} (第{attempt+1}次尝试)...")
                
                # 清理代码
                clean_symbol = symbol.split('.')[0] if '.' in symbol else symbol
                
                # 获取数据
                df = ak.stock_zh_a_hist(
                    symbol=clean_symbol,
                    period="daily",
                    start_date=start_date.replace('-', ''),
                    end_date=end_date.replace('-', ''),
                    adjust="qfq",
                    timeout=10
                )
                
                if df is not None and not df.empty:
                    # 标准化列名
                    df.columns = ['date', 'open', 'close', 'high', 'low', 'volume', 
                                'amount', 'amplitude', 'pct_change', 'change', 'turnover']
                    df['date'] = pd.to_datetime(df['date'])
                    df.set_index('date', inplace=True)
                    
                    # 计算前收盘
                    df['pre_close'] = df['close'].shift(1)
                    
                    print(f"    AKShare成功获取 {len(df)} 条数据")
                    
                    # 自动保存到数据库
                    if self.db:
                        try:
                            new_count, update_count = self.db.insert_daily_prices(symbol, df)
                            print(f"    自动保存到数据库: 新增{new_count}条, 更新{update_count}条")
                        except Exception as e:
                            print(f"    保存到数据库失败: {e}")
                    
                    return df
                else:
                    print(f"    AKShare返回空数据")
                    raise RuntimeError("AKShare返回空数据")
                    
            except Exception as e:
                print(f"    AKShare第{attempt+1}次尝试失败: {str(e)[:100]}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                else:
                    raise RuntimeError(f"AKShare获取失败: {e}")
    
    def _fetch_tencent(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """从腾讯财经获取数据（网络备用）"""
        if not TENCENT_AVAILABLE:
            raise RuntimeError("腾讯财经不可用")
        
        try:
            print(f"  腾讯财经获取 {symbol}...")
            
            # 获取所有历史数据
            data = get_tencent_stock_data(symbol, 'all')
            
            if data and 'prices' in data and 'labels' in data:
                # 构建DataFrame
                dates = pd.to_datetime(data['labels'])
                closes = pd.Series(data['prices'], index=dates)
                
                df = pd.DataFrame({'close': closes})
                df = df.resample('D').last().dropna()
                
                # 模拟OHLC
                df['open'] = df['close'] * 0.99
                df['high'] = df['close'] * 1.02
                df['low'] = df['close'] * 0.98
                df['volume'] = 1000000
                df['amount'] = df['close'] * df['volume']
                
                # 筛选日期范围
                start_dt = pd.to_datetime(start_date)
                end_dt = pd.to_datetime(end_date)
                df = df[(df.index >= start_dt) & (df.index <= end_dt)]
                
                if not df.empty:
                    print(f"    腾讯财经获取 {len(df)} 条数据")
                    
                    # 自动保存到数据库
                    if self.db:
                        try:
                            new_count, update_count = self.db.insert_daily_prices(symbol, df, data_source='tencent')
                            print(f"    自动保存到数据库: 新增{new_count}条, 更新{update_count}条")
                        except Exception as e:
                            print(f"    保存到数据库失败: {e}")
                    
                    return df
                else:
                    raise RuntimeError("腾讯财经数据在指定日期范围内为空")
            else:
                raise RuntimeError("腾讯财经返回数据格式错误")
                
        except Exception as e:
            raise RuntimeError(f"腾讯财经获取失败: {e}")
    
    def _fetch_simulated(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """生成模拟数据（最后防线）"""
        print(f"  生成模拟数据 {symbol}...")
        
        dates = pd.date_range(start=start_date, end=end_date, freq='B')
        
        if len(dates) == 0:
            return pd.DataFrame()
        
        np.random.seed(hash(symbol) % 10000)
        base_price = 10 + hash(symbol) % 90
        
        # 创建随机但有一定趋势的价格序列
        n = len(dates)
        time_index = np.arange(n) / n
        trend = np.cumsum(np.random.randn(n) * 0.01)
        noise = np.random.randn(n) * 0.02
        
        prices = base_price * (1 + trend + noise)
        prices = np.maximum(prices, base_price * 0.3)
        
        df = pd.DataFrame({
            'open': prices * 0.99,
            'high': prices * 1.02,
            'low': prices * 0.98,
            'close': prices,
            'volume': np.random.randint(1000000, 10000000, n),
            'amount': prices * np.random.randint(1000000, 10000000, n)
        }, index=dates)
        
        df['pre_close'] = df['close'].shift(1)
        df['change'] = df['close'] - df['pre_close']
        df['change_pct'] = (df['change'] / df['pre_close']) * 100
        df['turnover'] = np.random.uniform(0.5, 5.0, n)
        df['amplitude'] = np.random.uniform(1.0, 10.0, n)
        
        print(f"    生成 {len(df)} 条模拟数据")
        
        # 🚨 关键修复：模拟数据绝对不写入数据库，防止信任链断裂
        # 用户指出问题：模拟数据写入数据库后，后续调用从"本地数据库"取出，
        # 元数据显示"来源：本地数据库"，完全看不出来是模拟的
        # 解决方案：模拟数据仅用于测试和开发，不污染生产数据库
        if self.db:
            print(f"    ⚠️  警告：模拟数据不写入数据库（防止信任链断裂）")
            # 注释掉原数据库写入代码，确保模拟数据不污染数据库
            # new_count, update_count = self.db.insert_daily_prices(symbol, df, data_source='simulated')
            # print(f"    保存模拟数据到数据库: 新增{new_count}条")
        
        return df
    
    def _calculate_data_quality(self, df: pd.DataFrame) -> Dict[str, float]:
        """计算数据质量评分"""
        if df.empty:
            return {'completeness': 0, 'consistency': 0, 'timeliness': 0, 'overall': 0}
        
        # 1. 数据完整性 (非空值比例)
        completeness = 1 - df.isnull().sum().sum() / (df.shape[0] * df.shape[1])
        
        # 2. 数据一致性 (价格逻辑检查)
        consistency_checks = []
        if 'high' in df.columns and 'low' in df.columns:
            high_low_valid = (df['high'] >= df['low']).mean()
            consistency_checks.append(high_low_valid)
        
        if 'high' in df.columns and 'close' in df.columns:
            high_close_valid = (df['high'] >= df['close']).mean()
            consistency_checks.append(high_close_valid)
        
        if 'low' in df.columns and 'close' in df.columns:
            low_close_valid = (df['low'] <= df['close']).mean()
            consistency_checks.append(low_close_valid)
        
        consistency = np.mean(consistency_checks) if consistency_checks else 0.8
        
        # 3. 数据及时性 (最近数据日期)
        if hasattr(df.index, 'max'):
            latest_date = df.index.max()
            days_diff = (datetime.now() - latest_date).days
            timeliness = max(0, 1 - days_diff / 30)  # 30天内为满分
        else:
            timeliness = 0.5
        
        # 综合评分
        overall = (completeness * 0.4 + consistency * 0.4 + timeliness * 0.2)
        
        return {
            'completeness': round(completeness, 3),
            'consistency': round(consistency, 3),
            'timeliness': round(timeliness, 3),
            'overall': round(overall, 3)
        }
    
    def get_stock_data(self, symbol: str, start_date: str, end_date: str, 
                      with_metadata: bool = True) -> Dict[str, Any]:
        """
        获取股票数据，带完整元数据
        本地数据库优先，自动回填缺失数据
        """
        formatted_symbol = symbol.split('.')[0] if '.' in symbol else symbol
        sources_tried = []
        data = None
        source_info = {}
        
        # 记录开始时间
        start_time = time.time()
        
        # 尝试各个数据源（按优先级）
        for source_id, source_name, fetch_func in self.data_sources:
            try:
                print(f"尝试从 {source_name} 获取 {formatted_symbol} 数据...")
                data = fetch_func(formatted_symbol, start_date, end_date)
                
                if data is not None and not data.empty:
                    # 成功获取数据
                    source_info = {
                        'source_id': source_id,
                        'source_name': source_name,
                        'success': True,
                        'data_points': len(data),
                        'date_range': {
                            'start': data.index.min().strftime('%Y-%m-%d') if hasattr(data.index.min(), 'strftime') else str(data.index.min()),
                            'end': data.index.max().strftime('%Y-%m-%d') if hasattr(data.index.max(), 'strftime') else str(data.index.max())
                        }
                    }
                    break
                    
            except Exception as e:
                sources_tried.append({
                    'source': source_id,
                    'error': str(e)[:100]
                })
                print(f"  {source_name} 失败: {str(e)[:100]}")
                continue
        
        if data is None or data.empty:
            # 所有数据源都失败（理论上不会发生，因为模拟数据是最后防线）
            print("所有数据源失败，使用紧急模拟数据")
            dates = pd.date_range(start=start_date, end=end_date, freq='B')
            data = pd.DataFrame({
                'open': np.random.normal(100, 10, len(dates)),
                'high': np.random.normal(105, 10, len(dates)),
                'low': np.random.normal(95, 10, len(dates)),
                'close': np.random.normal(100, 10, len(dates)),
                'volume': np.random.randint(1000000, 10000000, len(dates))
            }, index=dates)
            
            source_info = {
                'source_id': 'emergency_simulated',
                'source_name': '紧急模拟数据',
                'success': True,
                'data_points': len(data),
                'date_range': {'start': start_date, 'end': end_date},
                'warning': '所有数据源失败，使用紧急模拟数据'
            }
        
        # 计算数据质量
        quality_scores = self._calculate_data_quality(data)
        
        # 计算耗时
        elapsed_time = time.time() - start_time
        
        # 构建元数据
        metadata = {
            'symbol': formatted_symbol,
            'request': {
                'start_date': start_date,
                'end_date': end_date,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'response_time_seconds': round(elapsed_time, 3)
            },
            'source': source_info,
            'quality': quality_scores,
            'data_info': {
                'rows': len(data),
                'columns': list(data.columns),
                'period': f"{start_date} 至 {end_date}",
                'data_coverage': f"{len(data)}/{len(pd.date_range(start=start_date, end=end_date, freq='B'))}天"
            },
            'sources_tried': sources_tried,
            'cache_status': 'database_first' if source_info.get('source_id') == 'database' else 'network_fallback'
        }
        
        if with_metadata:
            return {
                'data': data,
                'metadata': metadata
            }
        else:
            return {'data': data}
    
    def get_stock_info(self, symbol: str) -> Optional[Dict]:
        """获取股票基本信息"""
        if not self.db:
            return None
        
        return self.db.get_stock_info(symbol)
    
    def upsert_stock_info(self, symbol: str, **kwargs) -> bool:
        """插入或更新股票基本信息"""
        if not self.db:
            return False
        
        return self.db.upsert_stock(symbol, **kwargs)
    
    def get_last_trading_date(self, symbol: str = None) -> Optional[str]:
        """获取最后交易日期"""
        if not self.db:
            return None
        
        return self.db.get_last_trading_date(symbol)
    
    def check_data_coverage(self, symbol: str, start_date: str, end_date: str) -> Dict[str, Any]:
        """检查数据覆盖情况"""
        if not self.db:
            return {'error': '数据库不可用'}
        
        # 获取数据库中的数据
        df = self.db.get_daily_prices(symbol, start_date, end_date)
        
        if df.empty:
            return {
                'symbol': symbol,
                'date_range': f"{start_date} 至 {end_date}",
                'total_days': 0,
                'data_days': 0,
                'coverage': 0.0,
                'status': 'no_data'
            }
        
        # 计算交易日历
        trading_days = pd.date_range(start=start_date, end=end_date, freq='B')
        
        result = {
            'symbol': symbol,
            'date_range': f"{start_date} 至 {end_date}",
            'total_days': len(trading_days),
            'data_days': len(df),
            'coverage': len(df) / max(1, len(trading_days)),
            'first_date': df.index.min().strftime('%Y-%m-%d') if not df.empty else None,
            'last_date': df.index.max().strftime('%Y-%m-%d') if not df.empty else None,
            'status': 'complete' if len(df) >= len(trading_days) * 0.9 else 'partial'
        }
        
        return result
    
    def trigger_data_update(self, symbol: str = None, force: bool = False) -> Dict[str, Any]:
        """触发数据更新（手动或自动）"""
        from database.daily_update import DailyDataUpdater
        
        print(f"触发数据更新: symbol={symbol or 'ALL'}, force={force}")
        
        try:
            updater = DailyDataUpdater(max_workers=4)
            
            if symbol:
                # 更新指定股票
                start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
                end_date = datetime.now().strftime('%Y-%m-%d')
                
                result = updater.update_single_stock(symbol, start_date, end_date)
                return {
                    'success': result['success'],
                    'symbol': symbol,
                    'new_data': result.get('new_count', 0),
                    'updated_data': result.get('update_count', 0)
                }
            else:
                # 更新所有股票
                report = updater.run_daily_update(force=force)
                return report
                
        except Exception as e:
            return {'error': str(e), 'success': False}


# 测试函数
def test_pipeline():
    """测试数据管道"""
    print("=" * 60)
    print("测试数据管道V2")
    print("=" * 60)
    
    pipeline = DataPipelineV2()
    
    # 测试1: 获取已知存在的数据
    print("\n1. 测试获取数据库已有数据...")
    try:
        result = pipeline.get_stock_data('000001', '2025-01-01', '2025-01-03')
        
        if 'data' in result and not result['data'].empty:
            print(f"   成功获取 {len(result['data'])} 条数据")
            print(f"   数据源: {result['metadata']['source']['source_name']}")
            print(f"   数据质量: {result['metadata']['quality']['overall']:.3f}")
            print(f"   响应时间: {result['metadata']['request']['response_time_seconds']}秒")
        else:
            print("   获取数据失败")
            
    except Exception as e:
        print(f"   测试1失败: {e}")
    
    # 测试2: 测试数据覆盖检查
    print("\n2. 测试数据覆盖检查...")
    try:
        coverage = pipeline.check_data_coverage('000001', '2024-12-01', '2025-01-31')
        print(f"   数据覆盖: {coverage.get('coverage', 0)*100:.1f}%")
        print(f"   状态: {coverage.get('status', 'unknown')}")
    except Exception as e:
        print(f"   测试2失败: {e}")
    
    # 测试3: 测试获取新数据（可能从网络）
    print("\n3. 测试获取新数据（可能触发网络获取）...")
    try:
        result = pipeline.get_stock_data('600519', '2025-12-01', '2025-12-05')
        
        if 'data' in result and not result['data'].empty:
            print(f"   成功获取 {len(result['data'])} 条数据")
            print(f"   数据源: {result['metadata']['source']['source_name']}")
            
            # 显示数据详情
            df = result['data']
            if not df.empty:
                print(f"   日期范围: {df.index[0].strftime('%Y-%m-%d')} 至 {df.index[-1].strftime('%Y-%m-%d')}")
                print(f"   价格范围: {df['low'].min():.2f} - {df['high'].max():.2f}")
        else:
            print("   获取数据失败")
            
    except Exception as e:
        print(f"   测试3失败: {e}")
    
    # 测试4: 检查数据库状态
    print("\n4. 检查数据库状态...")
    if pipeline.db:
        stats = pipeline.db.get_update_stats()
        print(f"   数据库大小: {stats['database_size_mb']:.2f} MB")
        print(f"   日线记录数: {stats['total_daily_records']:,}条")
        print(f"   活跃股票数: {stats['total_active_stocks']}支")
    else:
        print("   数据库不可用")
    
    print("\n✅ 数据管道V2测试完成!")


if __name__ == "__main__":
    test_pipeline()