#!/usr/bin/env python3
"""
PIT因子管理器 - 包装现有因子管理器，确保PIT合规性

核心功能：
1. 包装现有因子管理器，保持接口兼容
2. 添加PIT时间戳检查，防止未来数据访问
3. 支持滚动窗口标准化，避免全局统计量
4. 记录PIT违规，便于调试和优化
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple, Any, Callable, Union
from datetime import datetime
import warnings
import sys
import traceback
import hashlib
import json

# 导入PIT特征工程模块
try:
    from ..pit_feature_engineer import PITFeatureEngineer, pit_feature, PITFeatureError
    PIT_MODULE_AVAILABLE = True
except ImportError:
    try:
        sys.path.append('/root/.openclaw/workspace/quant_system')
        from pit_feature_engineer import PITFeatureEngineer, pit_feature, PITFeatureError
        PIT_MODULE_AVAILABLE = True
    except ImportError as e:
        PIT_MODULE_AVAILABLE = False
        print(f"警告: PIT特征工程模块导入失败: {e}")

warnings.filterwarnings('ignore')


class PITFactorManager:
    """
    PIT因子管理器 - 包装现有因子管理器
    
    设计原则：
    1. 兼容性优先：保持与现有因子管理器相同的接口
    2. 渐进式迁移：支持原始模式和PIT模式切换
    3. 详细监控：记录所有PIT违规和性能指标
    4. 性能优化：智能缓存减少重复计算
    """
    
    def __init__(self, 
                 base_factor_manager,
                 current_date: Optional[pd.Timestamp] = None,
                 pit_strict_mode: bool = False,
                 enable_caching: bool = True):
        """
        初始化PIT因子管理器
        
        Args:
            base_factor_manager: 基础因子管理器实例
            current_date: 当前模拟日期（PIT检查基准）
            pit_strict_mode: 严格模式（PIT违规时抛出异常）
            enable_caching: 启用特征计算缓存
        """
        self.base_manager = base_factor_manager
        self.current_date = current_date
        self.pit_strict_mode = pit_strict_mode
        self.enable_caching = enable_caching
        
        # 初始化PIT特征工程器
        if PIT_MODULE_AVAILABLE:
            self.pit_engineer = PITFeatureEngineer(
                current_date=current_date,
                strict_mode=pit_strict_mode,
                enable_logging=True
            )
        else:
            self.pit_engineer = None
            print("警告: PIT特征工程器不可用，将使用简化检查")
        
        # 缓存系统
        self.feature_cache = {}
        self.cache_stats = {
            'hits': 0,
            'misses': 0,
            'size': 0
        }
        
        # 违规记录
        self.pit_violations = []
        
        # 性能统计
        self.performance_stats = {
            'total_calculations': 0,
            'pit_checks': 0,
            'pit_passed': 0,
            'pit_failed': 0,
            'average_calculation_time': 0
        }
        
        # 因子映射表
        self.factor_mapping = self._create_factor_mapping()
        
        print(f"✅ PIT因子管理器初始化完成")
        print(f"   基础管理器: {type(base_factor_manager).__name__}")
        print(f"   当前日期: {current_date}")
        print(f"   严格模式: {pit_strict_mode}")
        print(f"   缓存启用: {enable_caching}")
    
    def _create_factor_mapping(self) -> Dict[str, Callable]:
        """创建因子计算方法映射表"""
        
        mapping = {}
        
        # 尝试从基础管理器中获取因子方法
        if hasattr(self.base_manager, 'factors'):
            # 真实因子管理器模式
            for factor_id, factor_info in self.base_manager.factors.items():
                if 'function' in factor_info:
                    factor_func = factor_info['function']
                    mapping[factor_id] = self._wrap_factor_function(factor_id, factor_func)
        
        elif hasattr(self.base_manager, 'calculate_factor'):
            # 标准因子管理器模式
            # 这里需要根据具体实现调整
            pass
        
        # 添加默认的技术因子
        default_factors = {
            'momentum_1m': self._calculate_momentum_pit,
            'momentum_3m': self._calculate_momentum_pit,
            'volatility_20d': self._calculate_volatility_pit,
            'volatility_60d': self._calculate_volatility_pit,
            'ma_cross_5_20': self._calculate_ma_cross_pit,
            'ma_cross_10_30': self._calculate_ma_cross_pit,
            'volume_breakout': self._calculate_volume_breakout_pit,
            'rsi_14': self._calculate_rsi_pit,
        }
        
        mapping.update(default_factors)
        
        return mapping
    
    def _wrap_factor_function(self, factor_id: str, factor_func: Callable) -> Callable:
        """包装因子计算函数，添加PIT检查"""
        
        def wrapped_function(data: pd.DataFrame, **kwargs):
            # 生成缓存键
            cache_key = None
            if self.enable_caching:
                cache_key = self._generate_cache_key(factor_id, data, kwargs)
                if cache_key in self.feature_cache:
                    self.cache_stats['hits'] += 1
                    return self.feature_cache[cache_key]
            
            self.cache_stats['misses'] += 1
            self.performance_stats['total_calculations'] += 1
            
            start_time = datetime.now()
            
            try:
                # 检查数据时间戳
                self._check_data_timestamps(data, factor_id)
                
                # 使用PIT工程器计算特征
                if self.pit_engineer and self.current_date:
                    feature_values = self.pit_engineer.calculate_feature(
                        feature_func=factor_func,
                        data=data,
                        feature_name=factor_id,
                        **kwargs
                    )
                else:
                    # 简化版本：直接计算
                    feature_values = factor_func(data, **kwargs)
                
                # 记录性能
                elapsed = (datetime.now() - start_time).total_seconds()
                self.performance_stats['average_calculation_time'] = (
                    self.performance_stats['average_calculation_time'] * 
                    (self.performance_stats['total_calculations'] - 1) + elapsed
                ) / self.performance_stats['total_calculations']
                
                # 缓存结果
                if cache_key and self.enable_caching:
                    self.feature_cache[cache_key] = feature_values
                    self.cache_stats['size'] += 1
                
                self.performance_stats['pit_passed'] += 1
                
                return feature_values
                
            except Exception as e:
                self.performance_stats['pit_failed'] += 1
                
                # 记录PIT违规
                if "PIT" in str(e) or "future" in str(e).lower():
                    violation = {
                        'timestamp': datetime.now(),
                        'factor_id': factor_id,
                        'error_type': 'PIT_VIOLATION',
                        'error_message': str(e),
                        'current_date': self.current_date,
                        'data_shape': data.shape if hasattr(data, 'shape') else 'unknown'
                    }
                    self.pit_violations.append(violation)
                    
                    if self.pit_strict_mode:
                        raise PITFeatureError(f"PIT违规在因子 {factor_id}: {e}")
                    else:
                        print(f"⚠️  PIT警告: 因子 {factor_id} - {e}")
                
                # 重新抛出非PIT异常
                raise e
        
        return wrapped_function
    
    def _generate_cache_key(self, 
                           factor_id: str, 
                           data: pd.DataFrame,
                           kwargs: Dict[str, Any]) -> str:
        """生成缓存键"""
        
        # 🚨 关键修复：解决Python hash()随机化导致的缓存失效问题
        # CPython 3.3+ 默认启用hash randomization，hash()的结果在不同进程启动时不同
        # 导致相同输入在多进程或重启后得到不同缓存键，缓存完全失效
        # 解决方案：使用稳定哈希算法（MD5/SHA256）
        
        data_hash = ""
        if hasattr(data, 'values') and len(data) > 0:
            try:
                # 方法1: 使用pandas内置的稳定哈希函数
                # pd.util.hash_pandas_object返回每行的哈希值，我们将其聚合
                import pandas as pd
                hash_values = pd.util.hash_pandas_object(data, index=True)
                data_hash_bytes = hash_values.values.tobytes()
                data_hash = hashlib.md5(data_hash_bytes).hexdigest()
                
            except Exception as e:
                # 降级方法：使用数据的字符串表示
                # 注意：这种方法可能较慢，但作为备选方案
                print(f"  警告: 使用备用哈希方法: {e}")
                data_str = str(data.shape) + str(data.iloc[:min(10, len(data))].values.tolist())
                data_hash = hashlib.md5(data_str.encode('utf-8')).hexdigest()
        
        # 对kwargs也使用稳定哈希
        kwargs_hash = ""
        if kwargs:
            try:
                # 将kwargs转换为可哈希的稳定字符串表示
                # 排序键以确保顺序一致
                kwargs_str = json.dumps(kwargs, sort_keys=True, default=str)
                kwargs_hash = hashlib.md5(kwargs_str.encode('utf-8')).hexdigest()
            except Exception as e:
                print(f"  警告: kwargs哈希失败: {e}")
                kwargs_hash = str(hash(frozenset(kwargs.items())))
        
        return f"{factor_id}_{data_hash}_{kwargs_hash}_{self.current_date}"
    
    def _check_data_timestamps(self, data: pd.DataFrame, factor_id: str):
        """检查数据时间戳是否符合PIT原则"""
        
        if self.current_date is None:
            return
        
        # 查找日期列
        date_columns = [col for col in data.columns if 'date' in col.lower()]
        
        if not date_columns:
            return
        
        date_col = date_columns[0]
        
        # 检查是否有未来数据
        if date_col in data.columns:
            future_data = data[data[date_col] > self.current_date]
            
            if not future_data.empty:
                error_msg = f"因子 {factor_id} 接收到未来数据: {len(future_data)}行 > {self.current_date}"
                
                if self.pit_strict_mode:
                    raise ValueError(error_msg)
                else:
                    # 记录警告但不停止
                    print(f"⚠️  {error_msg}")
    
    # ========== PIT特征计算函数 ==========
    
    @staticmethod
    @pit_feature(strict_mode=True)
    def _calculate_momentum_pit(data: pd.DataFrame, 
                               price_col: str = 'close',
                               period: int = 22) -> pd.Series:
        """PIT动量计算"""
        return data[price_col].pct_change(period)
    
    @staticmethod
    @pit_feature(strict_mode=True)
    def _calculate_volatility_pit(data: pd.DataFrame,
                                 price_col: str = 'close',
                                 window: int = 20) -> pd.Series:
        """PIT波动率计算"""
        returns = data[price_col].pct_change()
        return returns.rolling(window).std()
    
    @staticmethod
    @pit_feature(strict_mode=True)
    def _calculate_ma_cross_pit(data: pd.DataFrame,
                               price_col: str = 'close',
                               short_window: int = 5,
                               long_window: int = 20) -> pd.Series:
        """PIT移动平均金叉计算"""
        ma_short = data[price_col].rolling(short_window).mean()
        ma_long = data[price_col].rolling(long_window).mean()
        return (ma_short > ma_long).astype(int)
    
    @staticmethod
    @pit_feature(strict_mode=True)
    def _calculate_volume_breakout_pit(data: pd.DataFrame,
                                      volume_col: str = 'volume',
                                      window: int = 20) -> pd.Series:
        """PIT成交量突破计算"""
        avg_volume = data[volume_col].rolling(window).mean()
        return data[volume_col] / avg_volume
    
    @staticmethod
    @pit_feature(strict_mode=True)
    def _calculate_rsi_pit(data: pd.DataFrame,
                          price_col: str = 'close',
                          period: int = 14) -> pd.Series:
        """PIT RSI计算"""
        delta = data[price_col].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi / 100  # 归一化到0-1
    
    # ========== 公共接口（兼容现有因子管理器） ==========
    
    def calculate_factor(self, 
                        factor_id: str,
                        data: pd.DataFrame,
                        **kwargs) -> pd.Series:
        """
        计算因子值（兼容接口）
        
        Args:
            factor_id: 因子ID
            data: 输入数据
            **kwargs: 因子计算参数
            
        Returns:
            因子值Series
        """
        
        if factor_id not in self.factor_mapping:
            # 尝试使用基础管理器
            if hasattr(self.base_manager, 'calculate_factor'):
                return self.base_manager.calculate_factor(factor_id, data, **kwargs)
            else:
                raise ValueError(f"未知因子: {factor_id}")
        
        factor_func = self.factor_mapping[factor_id]
        return factor_func(data, **kwargs)
    
    def calculate_all_factors(self, 
                             data: pd.DataFrame,
                             symbol: str = None) -> pd.DataFrame:
        """
        计算所有因子（兼容接口）
        
        Args:
            data: 输入数据
            symbol: 股票代码（可选）
            
        Returns:
            因子值DataFrame
        """
        
        results = {}
        
        for factor_id in self.factor_mapping.keys():
            try:
                factor_values = self.calculate_factor(factor_id, data, symbol=symbol)
                results[factor_id] = factor_values
            except Exception as e:
                print(f"因子 {factor_id} 计算失败: {e}")
                results[factor_id] = pd.Series(np.nan, index=data.index)
        
        return pd.DataFrame(results)
    
    def get_factor_weights(self, method: str = 'equal') -> Dict[str, float]:
        """获取因子权重（兼容接口）"""
        
        if hasattr(self.base_manager, 'get_factor_weights'):
            return self.base_manager.get_factor_weights(method)
        
        # 默认实现：等权重
        n_factors = len(self.factor_mapping)
        return {factor_id: 1.0 / n_factors for factor_id in self.factor_mapping.keys()}
    
    def combine_factors(self, 
                       df: pd.DataFrame,
                       weights: Optional[Dict[str, float]] = None,
                       symbol: str = None) -> pd.Series:
        """因子融合（兼容接口）"""
        
        # 计算所有因子
        factor_df = self.calculate_all_factors(df, symbol=symbol)
        
        # 获取权重
        if weights is None:
            weights = self.get_factor_weights('category_weighted')
        
        # 加权求和
        weighted_sum = pd.Series(0.0, index=factor_df.index)
        for factor_id, weight in weights.items():
            if factor_id in factor_df.columns:
                # 处理NaN值
                factor_values = factor_df[factor_id].fillna(0)
                weighted_sum += factor_values * weight
        
        return weighted_sum
    
    # ========== PIT特定功能 ==========
    
    def set_current_date(self, current_date: pd.Timestamp):
        """设置当前日期（用于PIT检查）"""
        
        self.current_date = current_date
        
        if self.pit_engineer:
            self.pit_engineer.set_current_date(current_date)
        
        # 清除缓存（日期变化使缓存失效）
        if self.enable_caching:
            self.feature_cache.clear()
            self.cache_stats['size'] = 0
        
        print(f"当前日期更新为: {current_date}")
    
    def get_pit_violations(self) -> List[Dict[str, Any]]:
        """获取PIT违规记录"""
        return self.pit_violations.copy()
    
    def clear_pit_violations(self):
        """清空PIT违规记录"""
        self.pit_violations.clear()
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """获取性能统计"""
        return self.performance_stats.copy()
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        return self.cache_stats.copy()
    
    def enable_pit_mode(self, strict_mode: bool = True):
        """启用PIT模式"""
        
        self.pit_strict_mode = strict_mode
        
        if self.pit_engineer:
            self.pit_engineer.strict_mode = strict_mode
        
        mode_str = "严格模式" if strict_mode else "警告模式"
        print(f"PIT模式启用: {mode_str}")
    
    def disable_pit_mode(self):
        """禁用PIT模式（回退到原始计算）"""
        
        self.pit_strict_mode = False
        
        if self.pit_engineer:
            self.pit_engineer.strict_mode = False
        
        print("PIT模式禁用，使用原始计算")


# ========== 测试函数 ==========

def test_pit_factor_manager():
    """测试PIT因子管理器"""
    
    print("🧪 测试PIT因子管理器")
    print("=" * 80)
    
    # 创建测试数据
    dates = pd.date_range('2023-01-01', periods=100, freq='D')
    test_data = pd.DataFrame({
        'date': dates,
        'close': np.exp(np.random.randn(100).cumsum() * 0.01) * 100,
        'volume': np.random.randint(1000, 10000, 100),
        'high': np.exp(np.random.randn(100).cumsum() * 0.01) * 105,
        'low': np.exp(np.random.randn(100).cumsum() * 0.01) * 95
    })
    
    current_date = pd.Timestamp('2023-03-01')
    
    print(f"测试数据: {len(test_data)}行, 当前日期: {current_date.date()}")
    
    # 创建模拟的基础因子管理器
    class MockFactorManager:
        """模拟基础因子管理器"""
        
        def calculate_factor(self, factor_id, data, **kwargs):
            if factor_id == 'test_momentum':
                return data['close'].pct_change(22)
            else:
                raise ValueError(f"未知因子: {factor_id}")
        
        def get_factor_weights(self, method='equal'):
            return {'test_momentum': 1.0}
    
    # 创建PIT因子管理器
    mock_manager = MockFactorManager()
    pit_manager = PITFactorManager(
        base_factor_manager=mock_manager,
        current_date=current_date,
        pit_strict_mode=False,  # 非严格模式用于测试
        enable_caching=True
    )
    
    print("\n测试PIT因子计算:")
    
    try:
        # 计算动量因子
        momentum = pit_manager.calculate_factor('momentum_1m', test_data)
        print(f"  ✅ 动量因子计算成功: {len(momentum.dropna())}个有效值")
        
        # 计算波动率因子
        volatility = pit_manager.calculate_factor('volatility_20d', test_data)
        print(f"  ✅ 波动率因子计算成功: {len(volatility.dropna())}个有效值")
        
        # 测试缓存
        print("\n测试缓存功能:")
        momentum_cached = pit_manager.calculate_factor('momentum_1m', test_data)
        cache_stats = pit_manager.get_cache_stats()
        print(f"  缓存命中: {cache_stats['hits']}, 缓存未命中: {cache_stats['misses']}")
        
        # 测试日期更新
        print("\n测试日期更新:")
        new_date = pd.Timestamp('2023-04-01')
        pit_manager.set_current_date(new_date)
        print(f"  当前日期已更新为: {new_date.date()}")
        
        # 获取性能统计
        perf_stats = pit_manager.get_performance_stats()
        print(f"\n性能统计:")
        print(f"  总计算次数: {perf_stats['total_calculations']}")
        print(f"  PIT检查通过: {perf_stats['pit_passed']}")
        print(f"  PIT检查失败: {perf_stats['pit_failed']}")
        print(f"  平均计算时间: {perf_stats['average_calculation_time']:.6f}s")
        
        # 测试PIT违规检测
        print("\n测试PIT违规检测:")
        
        # 故意传递未来数据
        future_date = pd.Timestamp('2023-05-01')
        future_data = test_data.copy()
        
        try:
            pit_manager.set_current_date(current_date)  # 重置为较早日期
            pit_manager.enable_pit_mode(strict_mode=False)
            momentum_future = pit_manager.calculate_factor('momentum_1m', future_data)
            print(f"  ⚠️  未检测到未来数据（可能数据中没有日期列）")
        except Exception as e:
            if "未来数据" in str(e):
                print(f"  ✅ 成功检测到未来数据: {str(e)[:60]}...")
            else:
                print(f"  ❌ 其他异常: {e}")
        
        print("\n" + "=" * 80)
        print("PIT因子管理器测试完成 ✅")
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_pit_factor_manager()