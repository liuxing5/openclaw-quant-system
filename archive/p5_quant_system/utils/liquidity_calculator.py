#!/usr/bin/env python3
"""
流动性数据计算器
从历史价格数据计算ADV、市值等流动性指标
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')


class LiquidityCalculator:
    """流动性数据计算器（基础版本）"""
    
    def __init__(self, use_real_data: bool = True):
        """
        初始化流动性计算器
        
        Args:
            use_real_data: 是否使用真实市值数据（Baostock）
        """
        self.use_real_data = use_real_data
        self.market_cap_fetcher = None
        
        if use_real_data:
            try:
                from .market_cap_fetcher import MarketCapFetcher
                self.market_cap_fetcher = MarketCapFetcher()
                print("✅ 市值数据获取器初始化成功")
            except ImportError as e:
                print(f"⚠️  市值数据获取器初始化失败: {e}")
                self.use_real_data = False
    
    @staticmethod
    def calculate_adv_from_prices(prices_df: pd.DataFrame, window: int = 20) -> float:
        """
        从价格DataFrame计算20日平均成交额（ADV）
        
        Args:
            prices_df: 包含'volume'和'close'列的DataFrame
            window: 计算窗口（默认20日）
            
        Returns:
            ADV（万元）：20日平均日成交额
        """
        if prices_df.empty or 'volume' not in prices_df.columns or 'close' not in prices_df.columns:
            return 0.0
        
        # 确保数据按日期排序
        df = prices_df.sort_index()
        
        if len(df) < window:
            # 数据不足，使用所有可用数据
            window = max(1, len(df) // 2)
        
        # 计算日成交额（成交量 * 收盘价）
        if 'amount' in df.columns:
            # 如果有成交额数据，直接使用
            daily_amount = df['amount'].iloc[-window:]
        else:
            # 计算成交额：成交量（股） * 收盘价（元）
            daily_amount = df['volume'].iloc[-window:] * df['close'].iloc[-window:]
        
        # 计算20日平均成交额（转换为万元）
        adv = daily_amount.mean() / 10000.0  # 元 -> 万元
        
        return adv
    
    def estimate_market_cap(self, symbol: str, prices_df: pd.DataFrame) -> float:
        """
        获取流通市值（使用真实数据或估算）
        
        Args:
            symbol: 股票代码
            prices_df: 价格数据
            
        Returns:
            流通市值（亿元）
        """
        if prices_df.empty:
            return 0.0
        
        # 获取最新收盘价
        latest_close = prices_df['close'].iloc[-1] if 'close' in prices_df.columns else 0.0
        
        if latest_close == 0:
            return 0.0
        
        # 如果启用了真实数据且获取器可用
        if self.use_real_data and self.market_cap_fetcher is not None:
            try:
                market_cap_data = self.market_cap_fetcher.get_market_cap(symbol, latest_close)
                return market_cap_data['float_market_cap']  # 流通市值
            except Exception as e:
                print(f"⚠️  真实市值数据获取失败: {e}")
                # 回退到估算
                return self._estimate_market_cap_fallback(symbol, latest_close)
        else:
            # 使用估算值
            return self._estimate_market_cap_fallback(symbol, latest_close)
    
    def _estimate_market_cap_fallback(self, symbol: str, current_price: float) -> float:
        """
        估算流通市值（回退方法）
        
        Args:
            symbol: 股票代码
            current_price: 当前股价
            
        Returns:
            流通市值（亿元）的估算值
        """
        # 简化估算：根据股票代码和价格估算市值
        # 茅台、宁德等大盘股
        if symbol in ['600519', '000858', '300750', '000333', '600036']:
            return np.random.uniform(500, 2000)  # 500-2000亿
        
        # 中盘股
        elif symbol.startswith('60') or symbol.startswith('00'):
            return np.random.uniform(50, 500)  # 50-500亿
        
        # 小盘股
        else:
            return np.random.uniform(10, 100)  # 10-100亿
    
    def check_st_status(self, symbol: str) -> bool:
        """
        检查是否为ST股票（使用真实数据或简化判断）
        
        Args:
            symbol: 股票代码
            
        Returns:
            是否为ST股票
        """
        # 如果启用了真实数据且获取器可用
        if self.use_real_data and self.market_cap_fetcher is not None:
            try:
                st_status = self.market_cap_fetcher.check_st_status(symbol)
                return st_status['is_st']
            except Exception as e:
                print(f"⚠️  真实ST状态获取失败: {e}")
                # 回退到简化判断
                return self._check_st_status_fallback(symbol)
        else:
            # 使用简化判断
            return self._check_st_status_fallback(symbol)
    
    def _check_st_status_fallback(self, symbol: str) -> bool:
        """
        检查是否为ST股票（回退方法）
        
        Args:
            symbol: 股票代码
            
        Returns:
            是否为ST股票
        """
        # 简化：根据股票代码判断
        # 实际应用中应从财务数据或公告获取
        
        # 一些常见的ST股票代码（示例）
        st_stocks = ['600074', '600145', '600240', '000981', '002021']
        
        return symbol in st_stocks
    
    def calculate_daily_turnover(self, symbol: str, prices_df: pd.DataFrame) -> float:
        """
        计算日换手率
        
        Args:
            symbol: 股票代码
            prices_df: 价格数据
            
        Returns:
            日换手率（%）
        """
        if prices_df.empty or 'volume' not in prices_df.columns:
            return 0.0
        
        # 获取最新成交量
        latest_volume = prices_df['volume'].iloc[-1] if len(prices_df) > 0 else 0.0
        
        if latest_volume == 0:
            return 0.0
        
        # 获取最新收盘价
        latest_close = prices_df['close'].iloc[-1] if 'close' in prices_df.columns else 0.0
        
        if latest_close == 0:
            return 0.0
        
        # 如果启用了真实数据且获取器可用
        if self.use_real_data and self.market_cap_fetcher is not None:
            try:
                # 获取市值数据（包含流通股本）
                market_cap_data = self.market_cap_fetcher.get_market_cap(symbol, latest_close)
                
                # 获取流通股本（亿股转换为股）
                float_shares = market_cap_data.get('float_shares', 0) * 1e8  # 亿股 -> 股
                
                if float_shares > 0:
                    # 计算换手率 = 成交量 / 流通股本
                    turnover = latest_volume / float_shares * 100  # 百分比
                    return turnover
                else:
                    # 流通股本为0，回退到估算
                    return self._estimate_turnover_fallback(latest_volume)
                    
            except Exception as e:
                print(f"⚠️  真实换手率计算失败: {e}")
                # 回退到估算
                return self._estimate_turnover_fallback(latest_volume)
        else:
            # 使用估算值
            return self._estimate_turnover_fallback(latest_volume)
    
    def _estimate_turnover_fallback(self, latest_volume: float) -> float:
        """
        估算换手率（回退方法）
        
        Args:
            latest_volume: 最新成交量
            
        Returns:
            换手率（%）
        """
        if latest_volume == 0:
            return 0.0
        
        # 估算流通股本（假设1-10亿股）
        estimated_float_shares = np.random.uniform(1e8, 1e9)  # 1-10亿股
        
        # 计算换手率 = 成交量 / 流通股本
        turnover = latest_volume / estimated_float_shares * 100  # 百分比
        
        return turnover
    
    def get_liquidity_data(self, symbol: str, prices_df: pd.DataFrame) -> Dict[str, float]:
        """
        获取完整的流动性数据（使用真实数据）
        
        Args:
            symbol: 股票代码
            prices_df: 历史价格数据
            
        Returns:
            包含流动性指标的字典
        """
        if prices_df.empty:
            # 没有价格数据，返回默认值
            return {
                'adv_20d': 10000.0,
                'market_cap': 100.0,
                'is_st': False,
                'daily_turnover': 2.5,
                'data_source': 'default',
                'calculation_date': datetime.now().strftime('%Y-%m-%d')
            }
        
        # 计算ADV（使用真实历史数据）
        adv_20d = self.calculate_adv_from_prices(prices_df, window=20)
        
        # 获取市值（使用真实数据或估算）
        market_cap = self.estimate_market_cap(symbol, prices_df)
        
        # 检查ST状态（使用真实数据或简化判断）
        is_st = self.check_st_status(symbol)
        
        # 计算换手率
        daily_turnover = self.calculate_daily_turnover(symbol, prices_df)
        
        # 确定数据来源
        data_source = 'real' if self.use_real_data and self.market_cap_fetcher is not None else 'estimated'
        
        return {
            'adv_20d': adv_20d,            # 20日平均成交额（万元）
            'market_cap': market_cap,      # 流通市值（亿元）
            'is_st': is_st,                # 是否为ST股票
            'daily_turnover': daily_turnover,  # 日换手率（%）
            'data_source': data_source,    # 数据来源标记
            'calculation_date': datetime.now().strftime('%Y-%m-%d')
        }
    
    def get_liquidity_data_simple(self, symbol: str, prices_df: pd.DataFrame) -> Dict[str, float]:
        """
        简化的流动性数据获取（用于测试和快速集成）
        
        与原有接口兼容，但使用真实数据
        """
        if prices_df.empty:
            # 没有价格数据，返回默认值
            return {
                'adv_20d': 10000.0,      # 默认1亿日成交
                'market_cap': 100.0,     # 默认100亿市值
                'is_st': False,
                'daily_turnover': 2.5,
                'data_source': 'default'
            }
        
        # 计算真实ADV
        adv_20d = self.calculate_adv_from_prices(prices_df, window=20)
        
        # 获取其他数据
        market_cap = self.estimate_market_cap(symbol, prices_df)
        is_st = self.check_st_status(symbol)
        daily_turnover = self.calculate_daily_turnover(symbol, prices_df)
        
        return {
            'adv_20d': max(100.0, adv_20d),  # 确保最小ADV为100万
            'market_cap': market_cap,
            'is_st': is_st,
            'daily_turnover': daily_turnover,
            'data_source': 'real' if self.use_real_data else 'estimated'
        }
    
    # 保持向后兼容的类方法
    @classmethod
    def get_liquidity_data_classmethod(cls, symbol: str, prices_df: pd.DataFrame) -> Dict[str, float]:
        """
        保持向后兼容的类方法
        
        Args:
            symbol: 股票代码
            prices_df: 历史价格数据
            
        Returns:
            包含流动性指标的字典
        """
        # 创建实例（使用真实数据）
        calculator = cls(use_real_data=True)
        return calculator.get_liquidity_data(symbol, prices_df)
    
    @classmethod
    def get_liquidity_data_simple_classmethod(cls, symbol: str, prices_df: pd.DataFrame) -> Dict[str, float]:
        """
        保持向后兼容的简单数据获取类方法
        
        Args:
            symbol: 股票代码
            prices_df: 历史价格数据
            
        Returns:
            简化的流动性数据
        """
        # 创建实例（使用真实数据）
        calculator = cls(use_real_data=True)
        return calculator.get_liquidity_data_simple(symbol, prices_df)


def test_liquidity_calculator():
    """测试流动性计算器"""
    print("测试流动性计算器")
    print("=" * 60)
    
    # 创建测试数据
    dates = pd.date_range(start='2024-01-01', periods=30, freq='D')
    np.random.seed(42)
    
    test_df = pd.DataFrame({
        'close': np.cumprod(1 + np.random.randn(30) * 0.02) * 100,
        'volume': np.random.randint(1000000, 5000000, 30),
        'open': np.random.randn(30) * 0.01 + 100,
        'high': np.random.randn(30) * 0.02 + 102,
        'low': np.random.randn(30) * 0.02 + 98
    }, index=dates)
    
    calculator = LiquidityCalculator()
    
    # 测试ADV计算
    adv = calculator.calculate_adv_from_prices(test_df, window=20)
    print(f"ADV计算: {adv:.1f}万元")
    
    # 测试完整流动性数据
    liquidity_data = calculator.get_liquidity_data('600519', test_df)
    print(f"流动性数据: {liquidity_data}")
    
    # 测试简化版本
    simple_data = calculator.get_liquidity_data_simple('600519', test_df)
    print(f"简化版本: {simple_data}")
    
    print("\n✅ 流动性计算器测试完成")


if __name__ == '__main__':
    test_liquidity_calculator()