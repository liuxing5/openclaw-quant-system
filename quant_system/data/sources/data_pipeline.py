#!/usr/bin/env python3
"""
专业数据管道 - 本地数据库优先（兼容版本）
替换原DataPipeline，保持接口兼容，但内部使用本地数据库优先架构
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

# 尝试导入各种数据源（保持兼容）
try:
    import baostock as bs
    BAOSTOCK_AVAILABLE = True
except ImportError:
    BAOSTOCK_AVAILABLE = False
    bs = None

try:
    import tushare as ts
    TUSHARE_AVAILABLE = True
except ImportError:
    TUSHARE_AVAILABLE = False
    ts = None

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False
    ak = None

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    yf = None

# 数据协调管道（新增）
try:
    from ..reconciliation import DataReconciliationPipeline, merge_dual_source_simple
    RECONCILIATION_AVAILABLE = True
except ImportError as e:
    # 尝试绝对路径
    try:
        import sys
        sys.path.append('/root/.openclaw/workspace/quant_system/data')
        from reconciliation import DataReconciliationPipeline, merge_dual_source_simple
        RECONCILIATION_AVAILABLE = True
    except ImportError as e2:
        print(f"数据协调模块导入失败: {e}")
        RECONCILIATION_AVAILABLE = False

# 腾讯财经模块（自定义）
try:
    from web_interface.tencent_data import get_tencent_stock_data
    TENCENT_AVAILABLE = True
except ImportError:
    TENCENT_AVAILABLE = False

# 导入本地数据库管理器（新功能）
try:
    from ..database.database_manager import DatabaseManager
    DATABASE_AVAILABLE = True
except ImportError as e:
    # 尝试绝对路径
    try:
        import sys
        sys.path.append('/root/.openclaw/workspace/quant_system/data')
        from database.database_manager import DatabaseManager
        DATABASE_AVAILABLE = True
    except ImportError as e2:
        print(f"数据库模块导入失败: {e}")
        DATABASE_AVAILABLE = False


class DataPipeline:
    """专业数据管道 - 本地数据库优先（兼容原接口）"""

    def __init__(self, cache_dir: str = None):
        self.cache_dir = cache_dir or "/root/.openclaw/workspace/quant_system/data/cache"
        os.makedirs(self.cache_dir, exist_ok=True)

        # 初始化数据库管理器（新功能）
        self.db = DatabaseManager() if DATABASE_AVAILABLE else None

        # 数据源优先级（新架构：本地数据库优先）
        self.data_sources = []

        # 1. 本地数据库（最高优先级 - 新增）
        if DATABASE_AVAILABLE and self.db:
            self.data_sources.append(('database', '本地数据库', self._fetch_from_database))

        # 2. Baostock（核心基本面+行情 - 用户首选）
        if BAOSTOCK_AVAILABLE:
            self.data_sources.append(('baostock', 'Baostock', self._fetch_baostock))

        # 3. AKShare（补充行情/概念/爬虫财务 - 用户指定）
        if AKSHARE_AVAILABLE:
            self.data_sources.append(('akshare', 'AKShare', self._fetch_akshare))

        # 4. 其他网络源（保持兼容）
        # 🚨 关键修复：移除腾讯财经数据源，因为其OHLC是按固定比例缩放的假数据
        # 用户指出问题：open = close * 0.99，high = close * 1.02，volume = 1000000（常数）
        # 这批数据被写入数据库后，所有依赖OHLC的因子（ATR、布林带、振幅、成交量突破）的计算结果全部是错的
        # if TENCENT_AVAILABLE:
        #     self.data_sources.append(('tencent', '腾讯财经', self._fetch_tencent))

        if TUSHARE_AVAILABLE:
            self.data_sources.append(('tushare', 'Tushare Pro', self._fetch_tushare))

        if YFINANCE_AVAILABLE:
            self.data_sources.append(('yfinance', 'Yahoo Finance', self._fetch_yfinance))

        # 5. 模拟数据（最后防线）
        self.data_sources.append(('simulated', '模拟数据', self._fetch_simulated))

        print(f"数据管道初始化完成（本地数据库优先）")
        print(f"可用数据源: {[name for _, name, _ in self.data_sources]}")
        
        # 初始化数据协调管道（新增）
        if RECONCILIATION_AVAILABLE:
            self.reconciliation_pipeline = DataReconciliationPipeline(
                primary_source_name='baostock',
                backup_source_name='akshare',
                strict_checks=False,  # 非严格模式，避免影响正常流程
                max_price_discrepancy_pct=2.0,  # 2%价格差异容忍
                max_volume_discrepancy_pct=20.0  # 20%成交量差异容忍
            )
            print(f"数据协调管道初始化完成: baostock + akshare")
        else:
            self.reconciliation_pipeline = None
            print(f"数据协调管道不可用")

    # ========== 新功能：本地数据库优先 ==========

    def _fetch_from_database(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """从本地数据库获取数据（最高优先级）"""
        if not self.db:
            raise RuntimeError("数据库不可用")

        print(f"  [数据库优先] 查询 {symbol} ({start_date} 至 {end_date})...")

        # 从数据库获取数据
        df = self.db.get_daily_prices(symbol, start_date, end_date)

        if df is not None and not df.empty:
            print(f"    数据库返回 {len(df)} 条记录")
            return df
        else:
            print(f"    数据库无数据，将尝试网络源")
            raise RuntimeError("数据库无数据")

    # ========== Baostock核心数据源方法 ==========

    def _fetch_baostock(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """从Baostock获取数据（核心基本面+行情）"""
        if not BAOSTOCK_AVAILABLE:
            raise RuntimeError("Baostock不可用")

        max_retries = 2
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                print(f"  Baostock获取 {symbol} (第{attempt+1}次)...")

                # 转换股票代码格式
                baostock_symbol = self._format_symbol_for_baostock(symbol)
                print(f"    Baostock格式: {baostock_symbol}")

                # 登录Baostock
                lg = bs.login()
                if lg.error_code != '0':
                    raise RuntimeError(f"Baostock登录失败: {lg.error_msg}")

                try:
                    # 查询K线数据
                    # 字段：date,code,open,high,low,close,volume,amount,pctChg,preclose,turn
                    fields = "date,code,open,high,low,close,volume,amount,pctChg,preclose,turn,peTTM,pbMRQ,psTTM,adjustfactor"

                    rs = bs.query_history_k_data_plus(
                        code=baostock_symbol,
                        fields=fields,
                        start_date=start_date,
                        end_date=end_date,
                        frequency="d",  # 日线
                        adjustflag="2"   # 前复权
                    )

                    if rs.error_code != '0':
                        raise RuntimeError(f"Baostock查询失败: {rs.error_msg}")

                    df = rs.get_data()

                    if df is not None and not df.empty:
                        # 转换数据类型
                        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount',
                                      'pctChg', 'preclose', 'turn', 'peTTM', 'pbMRQ', 'psTTM', 'adjustfactor']

                        for col in numeric_cols:
                            if col in df.columns:
                                df[col] = pd.to_numeric(df[col], errors='coerce')

                        # 重命名列以保持兼容
                        df = df.rename(columns={
                            'date': 'date',
                            'open': 'open',
                            'high': 'high',
                            'low': 'low',
                            'close': 'close',
                            'volume': 'volume',
                            'amount': 'amount',
                            'pctChg': 'change_pct',
                            'preclose': 'pre_close',
                            'turn': 'turnover',
                            'peTTM': 'pe_ttm',
                            'pbMRQ': 'pb_mrq',
                            'psTTM': 'ps_ttm',
                            'adjustfactor': 'adj_factor'
                        })

                        # 设置日期索引
                        df['date'] = pd.to_datetime(df['date'])
                        df.set_index('date', inplace=True)

                        # 计算涨跌额
                        if 'pre_close' in df.columns and 'close' in df.columns:
                            df['change'] = df['close'] - df['pre_close']

                        # 计算振幅（如果缺少）
                        if 'high' in df.columns and 'low' in df.columns and 'pre_close' in df.columns:
                            df['amplitude'] = (df['high'] - df['low']) / df['pre_close'] * 100

                        print(f"    Baostock成功获取 {len(df)} 条数据")
                        print(f"    数据范围: {df.index[0].strftime('%Y-%m-%d')} 至 {df.index[-1].strftime('%Y-%m-%d')}")

                        # 自动保存到数据库
                        if self.db:
                            try:
                                new_count, update_count = self.db.insert_daily_prices(symbol, df, data_source='baostock')
                                print(f"    自动保存到数据库: 新增{new_count}条, 更新{update_count}条")
                            except Exception as e:
                                print(f"    保存到数据库失败: {e}")

                        return df
                    else:
                        print(f"    Baostock返回空数据")
                        raise RuntimeError("Baostock返回空数据")

                finally:
                    # 确保登出
                    bs.logout()

            except Exception as e:
                print(f"    Baostock第{attempt+1}次尝试失败: {str(e)[:100]}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                else:
                    raise RuntimeError(f"Baostock获取失败: {e}")

    # ========== 兼容原有网络数据源方法 ==========

    def _fetch_tencent(self, symbol: str, start_date: str, end_date: str, period: str = '1d') -> pd.DataFrame:
        """从腾讯财经获取数据"""
        if not TENCENT_AVAILABLE:
            raise RuntimeError("腾讯财经不可用")

        try:
            print(f"  腾讯财经获取 {symbol}...")

            # 获取所有历史数据
            data = get_tencent_stock_data(symbol, 'all')

            if data and 'prices' in data and 'labels' in data:
                dates = pd.to_datetime(data['labels'])
                closes = pd.Series(data['prices'], index=dates)

                df = pd.DataFrame({'close': closes})
                df = df.resample('D').last().dropna()

                df['open'] = df['close'] * 0.99
                df['high'] = df['close'] * 1.02
                df['low'] = df['close'] * 0.98
                df['volume'] = 1000000
                df['amount'] = df['close'] * df['volume']

                start_dt = pd.to_datetime(start_date)
                end_dt = pd.to_datetime(end_date)
                df = df[(df.index >= start_dt) & (df.index <= end_dt)]

                if not df.empty:
                    print(f"    腾讯财经获取 {len(df)} 条数据")

                    # 自动保存到数据库（新功能）
                    if self.db:
                        try:
                            new_count, update_count = self.db.insert_daily_prices(symbol, df, data_source='tencent')
                            print(f"    自动保存到数据库: 新增{new_count}条")
                        except Exception as e:
                            print(f"    保存到数据库失败: {e}")

                    return df
                else:
                    raise RuntimeError("腾讯财经数据在指定日期范围内为空")
            else:
                raise RuntimeError("腾讯财经返回数据格式错误")

        except Exception as e:
            raise RuntimeError(f"腾讯财经获取失败: {e}")

    def _fetch_tushare(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """从Tushare获取数据"""
        if not TUSHARE_AVAILABLE:
            raise RuntimeError("Tushare不可用")

        try:
            token = os.getenv('TUSHARE_TOKEN')
            if not token:
                raise RuntimeError("需要设置TUSHARE_TOKEN环境变量")

            pro = ts.pro_api(token)

            df = pro.daily(
                ts_code=symbol if '.' in symbol else self._format_symbol(symbol),
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', '')
            )

            if df.empty:
                raise RuntimeError("Tushare返回空数据")

            df['trade_date'] = pd.to_datetime(df['trade_date'])
            df = df.rename(columns={
                'trade_date': 'date',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'vol': 'volume',
                'amount': 'amount'
            })

            df = df.set_index('date').sort_index()

            # 自动保存到数据库（新功能）
            if self.db:
                try:
                    new_count, update_count = self.db.insert_daily_prices(symbol, df, data_source='tushare')
                    print(f"    自动保存到数据库: 新增{new_count}条")
                except Exception as e:
                    print(f"    保存到数据库失败: {e}")

            return df

        except Exception as e:
            raise RuntimeError(f"Tushare获取失败: {e}")

    def _fetch_akshare(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """从AKShare获取数据"""
        if not AKSHARE_AVAILABLE:
            raise RuntimeError("AKShare不可用")

        max_retries = 2
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                print(f"  AKShare获取 {symbol} (第{attempt+1}次)...")

                clean_symbol = symbol.split('.')[0] if '.' in symbol else symbol

                df = ak.stock_zh_a_hist(
                    symbol=clean_symbol,
                    period="daily",
                    start_date=start_date.replace('-', ''),
                    end_date=end_date.replace('-', ''),
                    adjust="qfq",
                    timeout=10
                )

                if df is not None and not df.empty:
                    df.columns = ['date', 'open', 'close', 'high', 'low', 'volume',
                                'amount', 'amplitude', 'pct_change', 'change', 'turnover']
                    df['date'] = pd.to_datetime(df['date'])
                    df.set_index('date', inplace=True)

                    df['pre_close'] = df['close'].shift(1)

                    print(f"    AKShare成功获取 {len(df)} 条数据")

                    # 自动保存到数据库（新功能）
                    if self.db:
                        try:
                            new_count, update_count = self.db.insert_daily_prices(symbol, df)
                            print(f"    自动保存到数据库: 新增{new_count}条")
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

    def _fetch_yfinance(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """从Yahoo Finance获取数据"""
        if not YFINANCE_AVAILABLE:
            raise RuntimeError("Yahoo Finance不可用")

        try:
            if '.' not in symbol and symbol.isdigit():
                raise RuntimeError(f"代码{symbol}不适合Yahoo Finance")

            df = yf.download(symbol, start=start_date, end=end_date)

            if df.empty:
                raise RuntimeError("Yahoo Finance返回空数据")

            df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
            df.columns = ['open', 'high', 'low', 'close', 'volume']

            # 自动保存到数据库（新功能）
            if self.db:
                try:
                    new_count, update_count = self.db.insert_daily_prices(symbol, df, data_source='yfinance')
                    print(f"    自动保存到数据库: 新增{new_count}条")
                except Exception as e:
                    print(f"    保存到数据库失败: {e}")

            return df

        except Exception as e:
            raise RuntimeError(f"Yahoo Finance获取失败: {e}")

    def _fetch_simulated(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """生成模拟数据"""
        print(f"  生成模拟数据 {symbol}...")

        dates = pd.date_range(start=start_date, end=end_date, freq='B')

        if len(dates) == 0:
            return pd.DataFrame()

        np.random.seed(hash(symbol) % 10000)
        base_price = 10 + hash(symbol) % 90

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

    # ========== 兼容原有辅助方法 ==========

    def _format_symbol(self, symbol: str) -> str:
        """格式化股票代码"""
        if '.' in symbol:
            return symbol

        if symbol.isdigit() and len(symbol) == 6:
            if symbol.startswith('6'):
                return f"{symbol}.SH"
            elif symbol.startswith('0') or symbol.startswith('3'):
                return f"{symbol}.SZ"

        return symbol

    def _format_symbol_for_baostock(self, symbol: str) -> str:
        """将股票代码转换为Baostock格式 (sh.600519 或 sz.000001)"""
        if '.' in symbol:
            # 已经是格式化的，尝试转换
            parts = symbol.split('.')
            if len(parts) == 2:
                code = parts[0]
                market = parts[1].lower()
                if market == 'sh':
                    return f"sh.{code}"
                elif market == 'sz':
                    return f"sz.{code}"

        # 原始6位代码
        if symbol.isdigit() and len(symbol) == 6:
            if symbol.startswith('6'):
                return f"sh.{symbol}"
            elif symbol.startswith('0') or symbol.startswith('3'):
                return f"sz.{symbol}"

        # 无法识别的格式，直接返回
        return symbol

    def _calculate_data_quality(self, df: pd.DataFrame) -> Dict[str, float]:
        """计算数据质量评分"""
        if df.empty:
            return {'completeness': 0, 'consistency': 0, 'timeliness': 0, 'overall': 0}

        completeness = 1 - df.isnull().sum().sum() / (df.shape[0] * df.shape[1])

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

        if hasattr(df.index, 'max'):
            latest_date = df.index.max()
            days_diff = (datetime.now() - latest_date).days
            timeliness = max(0, 1 - days_diff / 30)
        else:
            timeliness = 0.5

        overall = (completeness * 0.4 + consistency * 0.4 + timeliness * 0.2)

        return {
            'completeness': round(completeness, 3),
            'consistency': round(consistency, 3),
            'timeliness': round(timeliness, 3),
            'overall': round(overall, 3)
        }

    def _apply_forward_adjustment(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        应用前复权处理（机构级底线）
        统一使用前复权价格（wind/choice默认前复权）
        在数据管道层完成复权，而不是让因子层自己处理
        
        参考用户提供的代码：
        def adjust_forward(df):
            df = df.sort_index()
            adj_factor = df['adj_factor'].fillna(1).cumprod() # 假设有adj_factor列
            for col in ['open','high','low','close']:
                df[col] *= adj_factor
            return df.drop(columns=['adj_factor'], errors='ignore')
        """
        if df.empty:
            return df
        
        # 复制以避免修改原始数据
        df = df.copy()
        
        # 如果有adj_factor列，使用它进行复权
        if 'adj_factor' in df.columns:
            # 确保adj_factor是数值类型
            adj_factor = pd.to_numeric(df['adj_factor'], errors='coerce').fillna(1.0)
            
            # 检查adj_factor是否已经接近1（可能数据已经是前复权）
            mean_adj = adj_factor.mean()
            if abs(mean_adj - 1.0) < 0.01 and adj_factor.std() < 0.05:
                print(f"    adj_factor接近1 (均值={mean_adj:.4f})，假设数据已经是前复权，跳过调整")
                # 移除adj_factor列，避免干扰后续处理
                df = df.drop(columns=['adj_factor'], errors='ignore')
                return df
            
            # 按日期排序
            df = df.sort_index()
            
            # 计算累积复权因子（前复权：因子累积乘积）
            # 注意：adj_factor是每日调整因子，累积乘积得到从最早日期到当前日期的总调整因子
            cum_adj_factor = adj_factor.cumprod()
            
            # 应用前复权到价格列
            for col in ['open', 'high', 'low', 'close']:
                if col in df.columns:
                    df[col] = df[col] * cum_adj_factor
            
            # 成交量也需要复权（按相同比例调整）
            if 'volume' in df.columns:
                df['volume'] = df['volume'] / cum_adj_factor
            
            print(f"    应用前复权：使用adj_factor累积调整，最新调整因子={cum_adj_factor.iloc[-1]:.6f}")
            
            # 移除adj_factor列，避免干扰后续处理
            df = df.drop(columns=['adj_factor'], errors='ignore')
        else:
            # 如果没有adj_factor列，假设数据已经是前复权
            # 记录警告
            print(f"    警告：缺少adj_factor列，假设数据已经是前复权")
        
        return df

    # ========== 主要公共接口（保持兼容） ==========

    def get_stock_data(self, symbol: str, start_date: str, end_date: str,
                      with_metadata: bool = True) -> Dict[str, Any]:
        """
        获取股票数据，带完整元数据
        本地数据库优先，自动回填网络数据
        
        增强：如果可用，使用双数据源协调（Baostock + AKShare）
        """
        formatted_symbol = self._format_symbol(symbol)
        sources_tried = []
        data = None
        source_info = {}

        start_time = time.time()
        
        # 检查是否应该使用双数据源协调
        use_reconciliation = (
            self.reconciliation_pipeline is not None and 
            BAOSTOCK_AVAILABLE and 
            AKSHARE_AVAILABLE
        )
        
        if use_reconciliation:
            print(f"使用双数据源协调模式: baostock + akshare")
            
            # 尝试获取主数据源（Baostock）
            primary_data = None
            primary_error = None
            
            try:
                print(f"尝试主数据源: baostock")
                primary_data = self._fetch_baostock(formatted_symbol, start_date, end_date)
                if primary_data is not None and not primary_data.empty:
                    print(f"  主数据源成功: {len(primary_data)}条记录")
                else:
                    primary_error = "返回空数据"
                    print(f"  主数据源失败: {primary_error}")
            except Exception as e:
                primary_error = str(e)[:100]
                print(f"  主数据源失败: {primary_error}")
                sources_tried.append({'source': 'baostock', 'error': primary_error})
            
            # 尝试获取备份数据源（AKShare）
            backup_data = None
            backup_error = None
            
            try:
                print(f"尝试备份数据源: akshare")
                backup_data = self._fetch_akshare(formatted_symbol, start_date, end_date)
                if backup_data is not None and not backup_data.empty:
                    print(f"  备份数据源成功: {len(backup_data)}条记录")
                else:
                    backup_error = "返回空数据"
                    print(f"  备份数据源失败: {backup_error}")
            except Exception as e:
                backup_error = str(e)[:100]
                print(f"  备份数据源失败: {backup_error}")
                sources_tried.append({'source': 'akshare', 'error': backup_error})
            
            # 数据协调
            reconciliation_metrics = None
            
            try:
                merged_data, reconciliation_metrics = self.reconciliation_pipeline.merge_dual_source(
                    primary_data,
                    backup_data,
                    symbol=formatted_symbol,
                    date_range=(start_date, end_date)
                )
                data = merged_data
                
                # 记录切换
                if primary_data is None or primary_data.empty:
                    source_info = {
                        'source_id': 'akshare',
                        'source_name': 'AKShare (backup)',
                        'success': True,
                        'data_points': len(data),
                        'date_range': {
                            'start': data.index.min().strftime('%Y-%m-%d') if hasattr(data.index.min(), 'strftime') else str(data.index.min()),
                            'end': data.index.max().strftime('%Y-%m-%d') if hasattr(data.index.max(), 'strftime') else str(data.index.max())
                        },
                        'reconciliation': reconciliation_metrics.to_dict(),
                        'switch_type': 'primary_to_backup'
                    }
                elif backup_data is not None and not backup_data.empty:
                    source_info = {
                        'source_id': 'baostock',
                        'source_name': 'Baostock (with akshare fill)',
                        'success': True,
                        'data_points': len(data),
                        'date_range': {
                            'start': data.index.min().strftime('%Y-%m-%d') if hasattr(data.index.min(), 'strftime') else str(data.index.min()),
                            'end': data.index.max().strftime('%Y-%m-%d') if hasattr(data.index.max(), 'strftime') else str(data.index.max())
                        },
                        'reconciliation': reconciliation_metrics.to_dict(),
                        'switch_type': 'data_filled'
                    }
                else:
                    source_info = {
                        'source_id': 'baostock',
                        'source_name': 'Baostock',
                        'success': True,
                        'data_points': len(data),
                        'date_range': {
                            'start': data.index.min().strftime('%Y-%m-%d') if hasattr(data.index.min(), 'strftime') else str(data.index.min()),
                            'end': data.index.max().strftime('%Y-%m-%d') if hasattr(data.index.max(), 'strftime') else str(data.index.max())
                        }
                    }
                    
            except Exception as e:
                print(f"数据协调失败: {e}")
                # 回退到原来的逻辑
                use_reconciliation = False
                print("回退到原始数据源优先级逻辑")
        
        # 如果不使用协调，或者协调失败，使用原来的逻辑
        if not use_reconciliation or data is None or data.empty:
            print("使用原始数据源优先级逻辑")
            
            # 按优先级尝试各个数据源
            for source_id, source_name, fetch_func in self.data_sources:
                try:
                    print(f"尝试从 {source_name} 获取 {formatted_symbol} 数据...")
                    data = fetch_func(formatted_symbol, start_date, end_date)

                    if data is not None and not data.empty:
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

        quality_scores = self._calculate_data_quality(data)
        elapsed_time = time.time() - start_time

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
                'period': f"{start_date} 至 {end_date}"
            },
            'sources_tried': sources_tried,
            'cache_status': 'database_first' if source_info.get('source_id') == 'database' else 'network_fallback'
        }

        # 应用前复权处理（机构级底线）
        data = self._apply_forward_adjustment(data)

        if with_metadata:
            return {
                'data': data,
                'metadata': metadata
            }
        else:
            return {'data': data}

    def get_stock_data_with_reconciliation(self, 
                                          symbol: str, 
                                          start_date: str, 
                                          end_date: str,
                                          with_metadata: bool = True) -> Dict[str, Any]:
        """
        增强版：使用双数据源协调获取股票数据
        
        解决用户指出的问题：
        1. Baostock不稳定，AKShare上游接口变动
        2. 数据一致性校验缺失
        3. 缺失值填充策略不一致
        4. 停牌/复权处理不同步
        
        返回：协调后的数据 + 详细的切换日志
        """
        formatted_symbol = self._format_symbol(symbol)
        start_time = time.time()
        
        print(f"\n=== 增强版数据获取（双数据源协调）===")
        print(f"股票: {formatted_symbol}, 期间: {start_date} 至 {end_date}")
        
        # 1. 获取主数据源（Baostock）
        primary_data = None
        primary_error = None
        primary_source_name = 'baostock'
        
        try:
            print(f"尝试主数据源: {primary_source_name}")
            primary_data = self._fetch_baostock(formatted_symbol, start_date, end_date)
            if primary_data is not None and not primary_data.empty:
                print(f"  主数据源成功: {len(primary_data)}条记录")
            else:
                primary_error = "返回空数据"
                print(f"  主数据源失败: {primary_error}")
        except Exception as e:
            primary_error = str(e)[:100]
            print(f"  主数据源失败: {primary_error}")
        
        # 2. 获取备份数据源（AKShare）
        backup_data = None
        backup_error = None
        backup_source_name = 'akshare'
        
        try:
            print(f"尝试备份数据源: {backup_source_name}")
            backup_data = self._fetch_akshare(formatted_symbol, start_date, end_date)
            if backup_data is not None and not backup_data.empty:
                print(f"  备份数据源成功: {len(backup_data)}条记录")
            else:
                backup_error = "返回空数据"
                print(f"  备份数据源失败: {backup_error}")
        except Exception as e:
            backup_error = str(e)[:100]
            print(f"  备份数据源失败: {backup_error}")
        
        # 3. 数据协调
        reconciliation_metrics = None
        reconciliation_log = None
        
        if self.reconciliation_pipeline is not None and (primary_data is not None or backup_data is not None):
            print("执行数据协调管道...")
            try:
                merged_data, reconciliation_metrics = self.reconciliation_pipeline.merge_dual_source(
                    primary_data,
                    backup_data,
                    symbol=formatted_symbol,
                    date_range=(start_date, end_date)
                )
                data = merged_data
                reconciliation_log = reconciliation_metrics.to_dict()
                print(f"数据协调完成: {reconciliation_metrics.merged_count}条记录")
                
                # 记录切换
                if primary_data is None or primary_data.empty:
                    self.reconciliation_pipeline._log_switch('primary_to_backup', formatted_symbol)
                elif backup_data is not None and not backup_data.empty and reconciliation_metrics.fill_count > 0:
                    self.reconciliation_pipeline._log_switch('data_filled', formatted_symbol, {
                        'fill_count': reconciliation_metrics.fill_count
                    })
                    
            except Exception as e:
                print(f"数据协调失败: {e}")
                # 回退到简单合并或单个数据源
                data = primary_data if primary_data is not None and not primary_data.empty else backup_data
        else:
            # 没有协调管道，使用简单逻辑
            print("使用简单数据源选择逻辑")
            if primary_data is not None and not primary_data.empty:
                data = primary_data
            elif backup_data is not None and not backup_data.empty:
                data = backup_data
            else:
                data = None
        
        # 4. 如果都失败，使用紧急数据
        if data is None or data.empty:
            print("双数据源均失败，使用紧急模拟数据")
            if self.reconciliation_pipeline:
                data = self.reconciliation_pipeline._create_emergency_data((start_date, end_date))
                self.reconciliation_pipeline._log_switch('emergency_simulated', formatted_symbol)
            else:
                dates = pd.date_range(start=start_date, end=end_date, freq='B')
                data = pd.DataFrame({
                    'open': np.random.normal(100, 10, len(dates)),
                    'high': np.random.normal(105, 10, len(dates)),
                    'low': np.random.normal(95, 10, len(dates)),
                    'close': np.random.normal(100, 10, len(dates)),
                    'volume': np.random.randint(1000000, 10000000, len(dates))
                }, index=dates)
        
        # 5. 数据质量处理
        data = self._apply_forward_adjustment(data)  # 前复权
        
        # 6. 构建元数据
        elapsed_time = time.time() - start_time
        
        metadata = {
            'symbol': formatted_symbol,
            'request': {
                'start_date': start_date,
                'end_date': end_date,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'response_time_seconds': round(elapsed_time, 3)
            },
            'sources': {
                'primary': {
                    'name': primary_source_name,
                    'success': primary_data is not None and not primary_data.empty,
                    'data_points': len(primary_data) if primary_data is not None else 0,
                    'error': primary_error
                },
                'backup': {
                    'name': backup_source_name,
                    'success': backup_data is not None and not backup_data.empty,
                    'data_points': len(backup_data) if backup_data is not None else 0,
                    'error': backup_error
                }
            },
            'reconciliation': reconciliation_log,
            'data_info': {
                'rows': len(data),
                'columns': list(data.columns),
                'period': f"{start_date} 至 {end_date}"
            },
            'quality': self._calculate_data_quality(data)
        }
        
        # 7. 返回结果
        if with_metadata:
            return {
                'data': data,
                'metadata': metadata
            }
        else:
            return {'data': data}

    # ========== 新功能方法 ==========

    def get_stock_info(self, symbol: str) -> Optional[Dict]:
        """获取股票基本信息（新功能）"""
        if not self.db:
            return None

        return self.db.get_stock_info(symbol)

    def get_last_trading_date(self, symbol: str = None) -> Optional[str]:
        """获取最后交易日期（新功能）"""
        if not self.db:
            return None

        return self.db.get_last_trading_date(symbol)

    # ========== 兼容原有私有方法（保持导入不报错） ==========

    def _check_adata_available(self) -> bool:
        """检查adata库是否可用"""
        try:
            import adata
            return True
        except ImportError:
            return False

    def _check_ashare_available(self) -> bool:
        """检查Ashare库是否可用"""
        try:
            import Ashare
            return True
        except ImportError:
            try:
                import ashare
                return True
            except ImportError:
                return False

    def _fetch_adata(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """从adata库获取数据"""
        if not self._check_adata_available():
            raise RuntimeError("Adata库不可用")

        try:
            import adata
            from adata.stock.market.stock_market import StockMarket

            market = StockMarket()
            clean_symbol = symbol.split('.')[0] if '.' in symbol else symbol

            df = market.kline(
                code=clean_symbol,
                period='day',
                start=start_date.replace('-', ''),
                end=end_date.replace('-', '')
            )

            if df is None or df.empty:
                raise RuntimeError("Adata返回空数据")

            df = df.rename(columns={
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'volume'
            })

            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'])
                df = df.set_index('date').sort_index()
            elif df.index.name == 'date':
                df.index = pd.to_datetime(df.index)
                df = df.sort_index()

            return df[['open', 'high', 'low', 'close', 'volume']]

        except Exception as e:
            raise RuntimeError(f"Adata获取失败: {e}")

    def _fetch_ashare(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """从Ashare库获取数据"""
        if not self._check_ashare_available():
            raise RuntimeError("Ashare库不可用")

        try:
            try:
                import Ashare as ashare_lib
            except ImportError:
                import ashare as ashare_lib

            clean_symbol = symbol.split('.')[0] if '.' in symbol else symbol

            df = ashare_lib.get_kline_data(
                code=clean_symbol,
                start=start_date,
                end=end_date
            )

            if df is None or df.empty:
                raise RuntimeError("Ashare返回空数据")

            df = df.rename(columns={
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'vol': 'volume'
            })

            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'])
                df = df.set_index('date').sort_index()
            elif df.index.name == 'date':
                df.index = pd.to_datetime(df.index)
                df = df.sort_index()

            return df[['open', 'high', 'low', 'close', 'volume']]

        except Exception as e:
            raise RuntimeError(f"Ashare获取失败: {e}")


# 测试函数（保持兼容）
def test_pipeline():
    """测试数据管道"""
    print("=" * 60)
    print("测试数据管道（本地数据库优先）")
    print("=" * 60)

    pipeline = DataPipeline()

    # 测试1: 获取数据库已有数据
    print("\n1. 测试获取数据库已有数据...")
    try:
        result = pipeline.get_stock_data('000001', '2025-01-01', '2025-01-03')

        if 'data' in result and not result['data'].empty:
            print(f"   成功获取 {len(result['data'])} 条数据")
            print(f"   数据源: {result['metadata']['source']['source_name']}")
            print(f"   数据质量: {result['metadata']['quality']['overall']:.3f}")
        else:
            print("   获取数据失败")

    except Exception as e:
        print(f"   测试1失败: {e}")

    # 测试2: 测试新功能
    print("\n2. 测试新功能...")
    try:
        last_date = pipeline.get_last_trading_date('000001')
        print(f"   最后交易日期: {last_date}")

        stock_info = pipeline.get_stock_info('000001')
        if stock_info:
            print(f"   股票信息: {stock_info.get('name', '未知')}")

    except Exception as e:
        print(f"   测试2失败: {e}")

    print("\n✅ 数据管道测试完成!")


if __name__ == "__main__":
    test_pipeline()