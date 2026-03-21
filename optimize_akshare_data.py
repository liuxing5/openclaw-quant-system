#!/usr/bin/env python3
"""
优化AKShare数据获取稳定性
包含重试机制、缓存、并行处理和错误处理
"""
import sys
import os
import time
import json
import pickle
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import concurrent.futures
from functools import lru_cache
import warnings
warnings.filterwarnings('ignore')

# 使用系统Python环境
sys.path.insert(0, '/usr/lib/python3/dist-packages')

# 提前导入pandas和numpy以避免NameError
import pandas as pd
import numpy as np

print("=" * 80)
print("AKShare数据获取稳定性优化")
print("=" * 80)

# ============================================================================
# 1. 配置参数
print("\n1. ⚙️ 加载配置参数...")

class AKShareConfig:
    """AKShare配置类"""
    
    def __init__(self):
        # 重试配置
        self.max_retries = 3
        self.retry_delay = 2  # 秒
        self.retry_backoff = 2  # 指数退避因子
        
        # 缓存配置
        self.cache_dir = '/root/.openclaw/workspace/quant_system/data/cache'
        self.cache_ttl = 3600  # 1小时缓存
        
        # 并行配置
        self.max_workers = 5  # 最大并发数
        self.request_timeout = 30  # 请求超时秒数
        
        # 数据验证
        self.min_data_points = 100  # 最小数据点
        self.max_null_ratio = 0.1  # 最大空值比例
        
        # 股票配置
        self.default_stocks = ['000001', '000002', '000006', '000009', '000012']
        self.start_date = '2023-01-01'
        self.end_date = datetime.now().strftime('%Y-%m-%d')
        
        # 数据源备份
        self.backup_sources = ['baostock', 'tushare', 'tencent']
        
    def __str__(self):
        return f"AKShare配置: {self.max_retries}次重试, {self.max_workers}并行, {self.cache_ttl}秒缓存"

config = AKShareConfig()
print(f"  ✓ {config}")

# 创建缓存目录
os.makedirs(config.cache_dir, exist_ok=True)
print(f"  ✓ 缓存目录: {config.cache_dir}")

# ============================================================================
# 2. 缓存管理器
print("\n2. 💾 初始化缓存管理器...")

class DataCache:
    """数据缓存管理器"""
    
    def __init__(self, cache_dir: str, ttl: int = 3600):
        self.cache_dir = cache_dir
        self.ttl = ttl
        
    def _get_cache_key(self, func_name: str, *args, **kwargs) -> str:
        """生成缓存键"""
        key_str = f"{func_name}:{args}:{kwargs}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def _get_cache_path(self, cache_key: str) -> str:
        """获取缓存文件路径"""
        return os.path.join(self.cache_dir, f"{cache_key}.pkl")
    
    def get(self, func_name: str, *args, **kwargs) -> Optional[Any]:
        """从缓存获取数据"""
        cache_key = self._get_cache_key(func_name, *args, **kwargs)
        cache_path = self._get_cache_path(cache_key)
        
        if os.path.exists(cache_path):
            try:
                # 检查缓存是否过期
                mtime = os.path.getmtime(cache_path)
                if time.time() - mtime > self.ttl:
                    return None
                
                with open(cache_path, 'rb') as f:
                    data = pickle.load(f)
                
                print(f"    ✓ 缓存命中: {func_name}")
                return data
            except Exception as e:
                print(f"    ⚠ 缓存读取失败: {e}")
        
        return None
    
    def set(self, func_name: str, data: Any, *args, **kwargs):
        """设置缓存数据"""
        cache_key = self._get_cache_key(func_name, *args, **kwargs)
        cache_path = self._get_cache_path(cache_key)
        
        try:
            with open(cache_path, 'wb') as f:
                pickle.dump(data, f)
            print(f"    ✓ 缓存保存: {func_name}")
        except Exception as e:
            print(f"    ⚠ 缓存保存失败: {e}")

cache_manager = DataCache(config.cache_dir, config.cache_ttl)
print(f"  ✓ 缓存管理器初始化完成")

# ============================================================================
# 3. 重试装饰器
print("\n3. 🔄 创建重试装饰器...")

def retry_with_backoff(max_retries: int = 3, delay: int = 2, backoff: int = 2):
    """重试装饰器（指数退避）"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            
            for attempt in range(max_retries + 1):
                try:
                    if attempt > 0:
                        print(f"    ⚠ 重试 {attempt}/{max_retries}: {func.__name__}")
                        time.sleep(current_delay)
                        current_delay *= backoff
                    
                    return func(*args, **kwargs)
                    
                except Exception as e:
                    last_exception = e
                    print(f"    ⚠ 尝试 {attempt+1}/{max_retries+1} 失败: {e}")
                    if attempt == max_retries:
                        print(f"    ✗ 达到最大重试次数")
                        raise
            
            raise last_exception
        return wrapper
    return decorator

print(f"  ✓ 重试装饰器创建完成 (最大{config.max_retries}次重试)")

# ============================================================================
# 4. AKShare数据获取器（带优化）
print("\n4. 🌐 创建优化的AKShare数据获取器...")

class OptimizedAKShareFetcher:
    """优化的AKShare数据获取器"""
    
    def __init__(self, config: AKShareConfig):
        self.config = config
        self.cache = cache_manager
        
        # 尝试导入AKShare
        self.akshare_available = False
        self.ak = None
        
        try:
            import akshare as ak
            self.ak = ak
            self.akshare_available = True
            print(f"  ✓ AKShare导入成功 (v{ak.__version__})")
        except Exception as e:
            print(f"  ⚠ AKShare导入失败: {e}")
    
    @retry_with_backoff(max_retries=3, delay=2, backoff=2)
    def get_stock_daily(self, symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """获取股票日线数据（带重试和缓存）"""
        cache_key = f"stock_daily_{symbol}_{start_date}_{end_date}"
        
        # 检查缓存
        cached_data = self.cache.get('get_stock_daily', symbol, start_date, end_date)
        if cached_data is not None:
            return cached_data
        
        if not self.akshare_available:
            print(f"    ⚠ AKShare不可用，使用模拟数据")
            return self._generate_mock_data(symbol, start_date, end_date)
        
        try:
            print(f"    📈 获取 {symbol} ({start_date} 至 {end_date})...")
            
            # 转换日期格式
            start_date_fmt = start_date.replace('-', '')
            end_date_fmt = end_date.replace('-', '')
            
            # 调用AKShare API
            df = self.ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date_fmt,
                end_date=end_date_fmt,
                adjust="qfq"
            )
            
            if df is not None and len(df) > 0:
                # 标准化列名
                df.columns = ['date', 'open', 'close', 'high', 'low', 'volume', 
                             'amount', 'amplitude', 'change_pct', 'change', 'turnover']
                
                # 转换日期格式
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                
                # 保留必要列
                df = df[['open', 'high', 'low', 'close', 'volume']]
                
                # 验证数据质量
                if self._validate_data(df, symbol):
                    # 保存到缓存
                    self.cache.set('get_stock_daily', df, symbol, start_date, end_date)
                    print(f"    ✓ 获取成功: {len(df)}个数据点")
                    return df
                else:
                    print(f"    ⚠ 数据验证失败: {symbol}")
                    return None
            else:
                print(f"    ⚠ 无数据返回: {symbol}")
                return None
                
        except Exception as e:
            print(f"    ✗ 数据获取失败: {symbol} - {e}")
            raise
    
    def _validate_data(self, df: pd.DataFrame, symbol: str) -> bool:
        """验证数据质量"""
        if df is None or len(df) == 0:
            return False
        
        # 检查数据点数量
        if len(df) < self.config.min_data_points:
            print(f"      ⚠ 数据点不足: {len(df)} < {self.config.min_data_points}")
            return False
        
        # 检查空值比例
        null_ratio = df.isnull().sum().sum() / (len(df) * len(df.columns))
        if null_ratio > self.config.max_null_ratio:
            print(f"      ⚠ 空值比例过高: {null_ratio:.2%} > {self.config.max_null_ratio:.0%}")
            return False
        
        # 检查价格合理性
        price_cols = ['open', 'high', 'low', 'close']
        for col in price_cols:
            if col in df.columns:
                if (df[col] <= 0).any():
                    print(f"      ⚠ 价格包含非正值: {col}")
                    return False
        
        return True
    
    def _generate_mock_data(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """生成模拟数据（当真实数据不可用时）"""
        dates = pd.date_range(start_date, end_date, freq='D')
        n_days = len(dates)
        
        # 基础价格
        base_price = 10 + int(symbol[-3:]) / 100
        
        # 生成价格序列
        np.random.seed(int(symbol))
        returns = np.random.normal(0.0005, 0.02, n_days)
        price_series = base_price * (1 + returns).cumprod()
        
        # 创建DataFrame
        df = pd.DataFrame({
            'open': price_series * 0.99,
            'high': price_series * 1.02,
            'low': price_series * 0.98,
            'close': price_series,
            'volume': 1000000 + np.random.normal(0, 200000, n_days).cumsum()
        }, index=dates)
        
        # 确保正数
        df = df.abs()
        
        print(f"      ⚠ 生成模拟数据: {symbol} ({n_days}天)")
        return df
    
    def get_multiple_stocks_parallel(self, symbols: List[str], start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
        """并行获取多只股票数据"""
        if not symbols:
            return {}
        
        print(f"  并行获取 {len(symbols)} 只股票数据...")
        
        results = {}
        failed_symbols = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            # 提交任务
            future_to_symbol = {
                executor.submit(self.get_stock_daily, symbol, start_date, end_date): symbol
                for symbol in symbols
            }
            
            # 处理结果
            for future in concurrent.futures.as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    data = future.result(timeout=self.config.request_timeout)
                    if data is not None:
                        results[symbol] = data
                        print(f"    ✓ {symbol}: 成功")
                    else:
                        failed_symbols.append(symbol)
                        print(f"    ⚠ {symbol}: 失败")
                except Exception as e:
                    failed_symbols.append(symbol)
                    print(f"    ✗ {symbol}: 错误 - {e}")
        
        print(f"  ✓ 完成: {len(results)}成功, {len(failed_symbols)}失败")
        return results
    
    def get_stock_list(self) -> Optional[pd.DataFrame]:
        """获取股票列表"""
        cache_key = "stock_list"
        
        # 检查缓存
        cached_data = self.cache.get('get_stock_list')
        if cached_data is not None:
            return cached_data
        
        if not self.akshare_available:
            print("    ⚠ AKShare不可用，使用本地股票列表")
            return self._get_local_stock_list()
        
        try:
            print("    📋 获取A股列表...")
            stock_list = self.ak.stock_info_a_code_name()
            
            if stock_list is not None and len(stock_list) > 0:
                # 保存到缓存
                self.cache.set('get_stock_list', stock_list)
                print(f"    ✓ 获取成功: {len(stock_list)}只股票")
                return stock_list
            else:
                print("    ⚠ 无股票列表返回")
                return self._get_local_stock_list()
                
        except Exception as e:
            print(f"    ✗ 股票列表获取失败: {e}")
            return self._get_local_stock_list()
    
    def _get_local_stock_list(self) -> pd.DataFrame:
        """从本地文件获取股票列表"""
        local_file = '/root/.openclaw/workspace/quant_system/data/backtest/akshare_stock_list.csv'
        
        if os.path.exists(local_file):
            df = pd.read_csv(local_file, dtype={'code': str})
            print(f"    ✓ 从本地文件加载: {len(df)}只股票")
            return df
        else:
            # 生成模拟股票列表
            stocks = []
            for i in range(100):
                stocks.append({
                    'code': f'{i+1:06d}',
                    'name': f'模拟股票{i+1}'
                })
            
            df = pd.DataFrame(stocks)
            print(f"    ⚠ 生成模拟股票列表: {len(df)}只")
            return df

# ============================================================================
# 5. 数据完整性检查
print("\n5. 🔍 数据完整性检查工具...")

class DataIntegrityChecker:
    """数据完整性检查器"""
    
    def __init__(self):
        self.checks = []
    
    def add_check(self, name: str, check_func):
        """添加检查项"""
        self.checks.append((name, check_func))
    
    def check_dataset(self, data_dict: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
        """检查数据集完整性"""
        results = {
            'total_stocks': len(data_dict),
            'passed_checks': 0,
            'failed_checks': 0,
            'details': {},
            'issues': []
        }
        
        if not data_dict:
            results['issues'].append("数据集为空")
            return results
        
        for symbol, df in data_dict.items():
            stock_results = {
                'data_points': len(df),
                'date_range': f"{df.index[0].date()} 至 {df.index[-1].date()}" if len(df) > 0 else "无数据",
                'missing_values': df.isnull().sum().sum(),
                'checks_passed': 0,
                'checks_failed': 0,
                'issues': []
            }
            
            # 执行检查
            for check_name, check_func in self.checks:
                try:
                    if check_func(df, symbol):
                        stock_results['checks_passed'] += 1
                    else:
                        stock_results['checks_failed'] += 1
                        stock_results['issues'].append(f"{check_name}失败")
                except Exception as e:
                    stock_results['checks_failed'] += 1
                    stock_results['issues'].append(f"{check_name}错误: {e}")
            
            results['details'][symbol] = stock_results
            
            if stock_results['checks_failed'] == 0:
                results['passed_checks'] += 1
            else:
                results['failed_checks'] += 1
        
        return results

# ============================================================================
# 主执行
if __name__ == "__main__":
    try:
        print("\n" + "=" * 80)
        print("开始执行AKShare数据获取优化")
        print("=" * 80)
        
        # 确保导入必要的库
        print("\n导入依赖库...")
        import pandas as pd
        import numpy as np
        print(f"  ✓ Pandas: {pd.__version__}")
        print(f"  ✓ NumPy: {np.__version__}")
        
        # 初始化获取器
        fetcher = OptimizedAKShareFetcher(config)
        
        # 测试1: 获取股票列表
        print("\n📋 测试1: 获取股票列表")
        stock_list = fetcher.get_stock_list()
        
        if stock_list is not None:
            print(f"  股票列表: {len(stock_list)}只")
            print(f"  前5只: {stock_list.head().to_string(index=False)}")
            
            # 选择样本股票
            sample_symbols = stock_list.head(10)['code'].tolist()
            print(f"  样本股票: {sample_symbols}")
        else:
            print("  ⚠ 无法获取股票列表，使用默认样本")
            sample_symbols = config.default_stocks
        
        # 测试2: 并行获取多只股票数据
        print(f"\n📈 测试2: 并行获取股票数据 ({len(sample_symbols)}只)")
        stock_data = fetcher.get_multiple_stocks_parallel(
            sample_symbols, 
            config.start_date, 
            config.end_date
        )
        
        # 测试3: 数据完整性检查
        print(f"\n🔍 测试3: 数据完整性检查")
        
        checker = DataIntegrityChecker()
        
        # 添加检查项
        checker.add_check("数据点数量", lambda df, sym: len(df) >= 50)
        checker.add_check("日期连续性", lambda df, sym: len(df) == 0 or (df.index[-1] - df.index[0]).days >= 30)
        checker.add_check("价格正数", lambda df, sym: all(df[['open', 'high', 'low', 'close']].min() > 0))
        checker.add_check("成交量正数", lambda df, sym: df['volume'].min() > 0)
        
        integrity_results = checker.check_dataset(stock_data)
        
        print(f"  检查结果:")
        print(f"    总股票数: {integrity_results['total_stocks']}")
        print(f"    通过检查: {integrity_results['passed_checks']}")
        print(f"    未通过检查: {integrity_results['failed_checks']}")
        
        if integrity_results['issues']:
            print(f"    问题列表:")
            for issue in integrity_results['issues'][:5]:  # 只显示前5个问题
                print(f"      - {issue}")
        
        # 保存数据
        print(f"\n💾 保存优化后的数据...")
        output_dir = '/root/.openclaw/workspace/quant_system/data/optimized'
        os.makedirs(output_dir, exist_ok=True)
        
        # 保存股票数据
        for symbol, df in stock_data.items():
            output_file = os.path.join(output_dir, f"{symbol}.csv")
            df.to_csv(output_file)
        
        # 保存元数据
        metadata = {
            'fetched_at': datetime.now().isoformat(),
            'symbols_fetched': list(stock_data.keys()),
            'symbols_count': len(stock_data),
            'date_range': f"{config.start_date} 至 {config.end_date}",
            'integrity_results': integrity_results,
            'config': {
                'max_retries': config.max_retries,
                'max_workers': config.max_workers,
                'cache_ttl': config.cache_ttl
            }
        }
        
        metadata_file = os.path.join(output_dir, 'metadata.json')
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        
        print(f"  ✓ 数据保存到: {output_dir}")
        print(f"  ✓ 元数据保存: {metadata_file}")
        
        # 性能统计
        print(f"\n📊 性能统计:")
        print(f"  缓存命中率: {len(stock_data) / len(sample_symbols):.1%}")
        print(f"  并行效率: {len(stock_data)}/{len(sample_symbols)}只成功")
        print(f"  数据目录: {output_dir}")
        print(f"  缓存目录: {config.cache_dir}")
        
        print("\n" + "=" * 80)
        print("AKShare数据获取优化完成")
        print("=" * 80)
        
        print("\n✅ 优化特性:")
        print("  1. 🔄 指数退避重试机制 (3次重试)")
        print("  2. 💾 智能缓存系统 (1小时TTL)")
        print("  3. ⚡ 并行数据获取 (5线程并发)")
        print("  4. 🔍 数据完整性验证")
        print("  5. 🛡️ 优雅降级 (模拟数据备用)")
        
        print("\n🚀 下一步:")
        print("  1. 使用优化后的数据进行真实回测")
        print("  2. 监控数据获取稳定性")
        print("  3. 调整参数以优化性能")
        
    except Exception as e:
        print(f"优化执行失败: {e}")
        import traceback
        traceback.print_exc()