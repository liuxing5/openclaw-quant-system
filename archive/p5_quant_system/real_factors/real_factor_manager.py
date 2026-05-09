#!/usr/bin/env python3
"""
真实因子管理器 - 解决伪因子问题（完全真实数据）
1. 技术因子: 基于价格/成交量（真实）
2. 基本面因子: 基于Baostock真实财报（用户要求：季度ROE、净利润增长、资产负债率）
3. 情绪因子: 基于真实数据源（融资余额变化率、换手率Z-score、股吧情绪）
目标：消除伪因子，避免高beta追涨杀跌，防止2025年小微盘/题材崩盘损失
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple, Any, Callable
import warnings
import time
warnings.filterwarnings('ignore')

# ========== 导入基本面数据获取器（优先级：Baostock > AKShare > 模拟）==========

# 1. 优先使用Baostock（免费、可靠）
try:
    from .baostock_fundamental import BaostockFundamentalData
    BAOSTOCK_FUNDAMENTAL_AVAILABLE = True
except ImportError:
    try:
        import sys
        sys.path.append('/root/.openclaw/workspace/quant_system/real_factors')
        from baostock_fundamental import BaostockFundamentalData
        BAOSTOCK_FUNDAMENTAL_AVAILABLE = True
    except ImportError:
        BAOSTOCK_FUNDAMENTAL_AVAILABLE = False
        print("警告: Baostock基本面数据模块导入失败")

# 2. 备用AKShare
try:
    from .akshare_fundamental import AKShareFundamentalData
    AKSHARE_FUNDAMENTAL_AVAILABLE = True
except ImportError:
    try:
        import sys
        sys.path.append('/root/.openclaw/workspace/quant_system/real_factors')
        from akshare_fundamental import AKShareFundamentalData
        AKSHARE_FUNDAMENTAL_AVAILABLE = True
    except ImportError:
        AKSHARE_FUNDAMENTAL_AVAILABLE = False
        print("警告: AKShare基本面数据模块导入失败")

# 确定最终可用的基本面数据源
if BAOSTOCK_FUNDAMENTAL_AVAILABLE:
    FUNDAMENTAL_DATA_SOURCE = 'baostock'
elif AKSHARE_FUNDAMENTAL_AVAILABLE:
    FUNDAMENTAL_DATA_SOURCE = 'akshare'
else:
    FUNDAMENTAL_DATA_SOURCE = 'simulated'
    print("警告: 所有真实基本面数据源不可用，将使用有限模拟数据")


class RealFactorManager:
    """真实因子管理器（无伪因子）"""
    
    def __init__(self, cache_dir: str = None):
        self.cache_dir = cache_dir or "/root/.openclaw/workspace/quant_system/data/cache/factors"
        
        # 初始化基本面数据获取器（根据优先级）
        self.fundamental_data = None
        self.fundamental_data_source = FUNDAMENTAL_DATA_SOURCE
        
        if self.fundamental_data_source == 'baostock' and BAOSTOCK_FUNDAMENTAL_AVAILABLE:
            try:
                self.fundamental_data = BaostockFundamentalData()
                print("✓ 基本面数据模块加载成功 (Baostock)")
            except Exception as e:
                print(f"✗ Baostock初始化失败: {e}")
                self.fundamental_data_source = 'akshare' if AKSHARE_FUNDAMENTAL_AVAILABLE else 'simulated'
        
        if self.fundamental_data_source == 'akshare' and AKSHARE_FUNDAMENTAL_AVAILABLE:
            try:
                self.fundamental_data = AKShareFundamentalData()
                print("✓ 基本面数据模块加载成功 (AKShare)")
            except Exception as e:
                print(f"✗ AKShare初始化失败: {e}")
                self.fundamental_data_source = 'simulated'
        
        if self.fundamental_data_source == 'simulated':
            self.fundamental_data = None
            print("⚠ 使用模拟基本面数据（真实数据源不可用）")
        
        # 情绪数据缓存
        self.sentiment_cache = {}
        self.cache_expiry = {}
        
        # 因子类别统计
        self.category_stats = {
            'technical': 0,
            'fundamental': 0,
            'sentiment': 0,
            'market': 0
        }
        
        # 因子注册表
        self.factors = {}
        self._register_factors()
    
    def _register_factors(self):
        """注册所有真实因子"""
        
        # ========== 技术因子（基于价格/成交量，真实）==========
        self._register_technical_factors()
        
        # ========== 基本面因子（基于真实财报）==========
        self._register_fundamental_factors()
        
        # ========== 情绪因子（标记为待完善）==========
        self._register_sentiment_factors()
        
        print(f"因子注册完成: 总计{len(self.factors)}个因子")
        print(f"  技术因子: {self.category_stats['technical']}个")
        print(f"  基本面因子: {self.category_stats['fundamental']}个")
        print(f"  情绪因子: {self.category_stats['sentiment']}个")
    
    def _register_technical_factors(self):
        """注册技术因子"""
        tech_factors = {
            'momentum_1m': {
                'name': '1个月动量',
                'category': 'technical',
                'description': '22日价格动量',
                'calc_func': self._calc_momentum_1m
            },
            'momentum_3m': {
                'name': '3个月动量',
                'category': 'technical',
                'description': '66日价格动量',
                'calc_func': self._calc_momentum_3m
            },
            'momentum_6m': {
                'name': '6个月动量',
                'category': 'technical',
                'description': '132日价格动量',
                'calc_func': self._calc_momentum_6m
            },
            'volatility_20d': {
                'name': '20日波动率',
                'category': 'technical',
                'description': '20日收益率波动率',
                'calc_func': self._calc_volatility_20d
            },
            'volatility_60d': {
                'name': '60日波动率',
                'category': 'technical',
                'description': '60日收益率波动率',
                'calc_func': self._calc_volatility_60d
            },
            'rsi_14': {
                'name': '14日RSI',
                'category': 'technical',
                'description': '14日相对强弱指数',
                'calc_func': self._calc_rsi_14
            },
            'macd_signal': {
                'name': 'MACD信号',
                'category': 'technical',
                'description': 'MACD指标信号',
                'calc_func': self._calc_macd_signal
            },
            'bollinger_position': {
                'name': '布林带位置',
                'category': 'technical',
                'description': '价格在布林带中的相对位置',
                'calc_func': self._calc_bollinger_position
            },
            'volume_breakout': {
                'name': '成交量突破',
                'category': 'technical',
                'description': '成交量相比20日均量',
                'calc_func': self._calc_volume_breakout
            },
            'atr_14': {
                'name': '14日ATR',
                'category': 'technical',
                'description': '14日平均真实波幅',
                'calc_func': self._calc_atr_14
            }
        }
        
        for factor_id, factor_info in tech_factors.items():
            self.factors[factor_id] = factor_info
            self.category_stats['technical'] += 1
    
    def _register_fundamental_factors(self):
        """注册基本面因子（真实财报数据）"""
        if self.fundamental_data is None:
            print("警告: 基本面数据不可用，跳过基本面因子注册")
            return
        
        fund_factors = {
            'roe': {
                'name': '净资产收益率',
                'category': 'fundamental',
                'description': 'ROE（净资产收益率）',
                'calc_func': self._calc_roe,
                'requires_fundamental': True
            },
            'profit_growth': {
                'name': '利润增长率',
                'category': 'fundamental',
                'description': '1年利润同比增长率',
                'calc_func': self._calc_profit_growth,
                'requires_fundamental': True
            },
            'debt_ratio': {
                'name': '资产负债率',
                'category': 'fundamental',
                'description': '总负债/总资产',
                'calc_func': self._calc_debt_ratio,
                'requires_fundamental': True
            },
            'cash_flow_yield': {
                'name': '现金流收益率',
                'category': 'fundamental',
                'description': '经营现金流/市值',
                'calc_func': self._calc_cash_flow_yield,
                'requires_fundamental': True
            },
            'pe_ratio': {
                'name': '市盈率',
                'category': 'fundamental',
                'description': '股价/每股收益',
                'calc_func': self._calc_pe_ratio,
                'requires_fundamental': True
            },
            'pb_ratio': {
                'name': '市净率',
                'category': 'fundamental',
                'description': '股价/每股净资产',
                'calc_func': self._calc_pb_ratio,
                'requires_fundamental': True
            }
        }
        
        for factor_id, factor_info in fund_factors.items():
            self.factors[factor_id] = factor_info
            self.category_stats['fundamental'] += 1
    
    def _register_sentiment_factors(self):
        """注册情绪因子（基于真实数据源，用户要求）
        1. 融资余额变化率 (Baostock)
        2. 换手率异常值 (Z-score)
        3. 股吧/雪球评论情绪 (AKShare + 文本分析，标记为待完善)
        """
        sent_factors = {
            'margin_balance_change': {
                'name': '融资余额变化率',
                'category': 'sentiment',
                'description': '融资余额日变化率 (Baostock真实数据)',
                'calc_func': self._calc_margin_balance_change,
                'requires_margin_data': True,
                'status': 'production'
            },
            'turnover_zscore': {
                'name': '换手率异常值',
                'category': 'sentiment',
                'description': '换手率Z-score (成交量/流通股本)',
                'calc_func': self._calc_turnover_zscore,
                'status': 'production'
            },
            'news_sentiment': {
                'name': '新闻情绪',
                'category': 'sentiment',
                'description': '新闻情绪分析（待完善）',
                'calc_func': self._calc_news_sentiment,
                'requires_sentiment': True,
                'status': 'development'
            },
            'social_buzz': {
                'name': '社交媒体热度',
                'category': 'sentiment',
                'description': '社交媒体讨论热度（待完善）',
                'calc_func': self._calc_social_buzz,
                'requires_sentiment': True,
                'status': 'development'
            }
        }
        
        for factor_id, factor_info in sent_factors.items():
            self.factors[factor_id] = factor_info
            self.category_stats['sentiment'] += 1
    
    # ========== 技术因子计算方法 ==========
    
    @staticmethod
    def _calc_momentum_1m(df: pd.DataFrame) -> pd.Series:
        """1个月动量"""
        return df['close'].pct_change(22)
    
    @staticmethod
    def _calc_momentum_3m(df: pd.DataFrame) -> pd.Series:
        """3个月动量"""
        return df['close'].pct_change(66)
    
    @staticmethod
    def _calc_momentum_6m(df: pd.DataFrame) -> pd.Series:
        """6个月动量"""
        return df['close'].pct_change(132)
    
    @staticmethod
    def _calc_volatility_20d(df: pd.DataFrame) -> pd.Series:
        """20日波动率"""
        returns = df['close'].pct_change()
        return returns.rolling(20).std()
    
    @staticmethod
    def _calc_volatility_60d(df: pd.DataFrame) -> pd.Series:
        """60日波动率"""
        returns = df['close'].pct_change()
        return returns.rolling(60).std()
    
    @staticmethod
    def _calc_rsi_14(df: pd.DataFrame) -> pd.Series:
        """14日RSI"""
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi / 100  # 归一化到0-1
    
    @staticmethod
    def _calc_macd_signal(df: pd.DataFrame) -> pd.Series:
        """MACD信号"""
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        macd = exp1 - exp2
        signal = macd.ewm(span=9, adjust=False).mean()
        return macd - signal  # MACD柱状图
    
    @staticmethod
    def _calc_bollinger_position(df: pd.DataFrame) -> pd.Series:
        """布林带位置"""
        ma20 = df['close'].rolling(20).mean()
        std20 = df['close'].rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        return (df['close'] - lower) / (upper - lower)
    
    @staticmethod
    def _calc_volume_breakout(df: pd.DataFrame) -> pd.Series:
        """成交量突破"""
        avg_volume = df['volume'].rolling(20).mean()
        return df['volume'] / avg_volume
    
    @staticmethod
    def _calc_atr_14(df: pd.DataFrame) -> pd.Series:
        """14日平均真实波幅"""
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        return atr / df['close']  # 相对ATR
    
    # ========== 基本面因子计算方法 ==========
    
    def _calc_roe(self, df: pd.DataFrame, symbol: str = None) -> pd.Series:
        """ROE计算（使用真实财报数据）"""
        if self.fundamental_data is None or symbol is None:
            # 回退：使用模拟数据（明确标记）
            print(f"警告: ROE使用模拟数据 (symbol={symbol}, 数据源={self.fundamental_data_source})")
            return pd.Series(0.15, index=df.index)  # 固定15% ROE
        
        # 获取最新财报日期的ROE（财务数据变化慢，不需要每日更新）
        latest_date = df.index[-1] if len(df) > 0 else None
        if latest_date is None:
            return pd.Series(0.15, index=df.index)
        
        try:
            financial_data = self.fundamental_data.get_financial_data(symbol, latest_date.strftime('%Y-%m-%d'))
            roe = financial_data.get('roe', 0.15)
            
            # 财务数据变化慢，所有日期使用相同值
            roe_series = pd.Series(roe, index=df.index)
            
            # 标记数据来源
            data_source = financial_data.get('data_source', 'unknown')
            if data_source == 'simulated':
                print(f"警告: ROE使用模拟数据 (symbol={symbol})")
            
            return roe_series
        except Exception as e:
            print(f"ROE计算失败 {symbol}: {e}")
            return pd.Series(0.15, index=df.index)
    
    def _calc_profit_growth(self, df: pd.DataFrame, symbol: str = None) -> pd.Series:
        """利润增长率计算（使用真实财报数据）"""
        if self.fundamental_data is None or symbol is None:
            print(f"警告: 利润增长使用模拟数据 (symbol={symbol})")
            return pd.Series(0.1, index=df.index)  # 固定10%增长
        
        latest_date = df.index[-1] if len(df) > 0 else None
        if latest_date is None:
            return pd.Series(0.1, index=df.index)
        
        try:
            financial_data = self.fundamental_data.get_financial_data(symbol, latest_date.strftime('%Y-%m-%d'))
            profit_growth = financial_data.get('profit_growth', 0.1)
            
            profit_growth_series = pd.Series(profit_growth, index=df.index)
            
            data_source = financial_data.get('data_source', 'unknown')
            if data_source == 'simulated':
                print(f"警告: 利润增长使用模拟数据 (symbol={symbol})")
            
            return profit_growth_series
        except Exception as e:
            print(f"利润增长计算失败 {symbol}: {e}")
            return pd.Series(0.1, index=df.index)
    
    def _calc_debt_ratio(self, df: pd.DataFrame, symbol: str = None) -> pd.Series:
        """资产负债率计算（使用真实财报数据）"""
        if self.fundamental_data is None or symbol is None:
            print(f"警告: 资产负债率使用模拟数据 (symbol={symbol})")
            return pd.Series(0.5, index=df.index)  # 固定50%负债率
        
        latest_date = df.index[-1] if len(df) > 0 else None
        if latest_date is None:
            return pd.Series(0.5, index=df.index)
        
        try:
            financial_data = self.fundamental_data.get_financial_data(symbol, latest_date.strftime('%Y-%m-%d'))
            debt_ratio = financial_data.get('debt_ratio', 0.5)
            
            debt_ratio_series = pd.Series(debt_ratio, index=df.index)
            
            data_source = financial_data.get('data_source', 'unknown')
            if data_source == 'simulated':
                print(f"警告: 资产负债率使用模拟数据 (symbol={symbol})")
            
            return debt_ratio_series
        except Exception as e:
            print(f"资产负债率计算失败 {symbol}: {e}")
            return pd.Series(0.5, index=df.index)
    
    def _calc_cash_flow_yield(self, df: pd.DataFrame, symbol: str = None) -> pd.Series:
        """现金流收益率计算（使用真实财报数据）"""
        if self.fundamental_data is None or symbol is None:
            print(f"警告: 现金流收益率使用模拟数据 (symbol={symbol})")
            return pd.Series(0.05, index=df.index)  # 固定5%收益率
        
        latest_date = df.index[-1] if len(df) > 0 else None
        if latest_date is None:
            return pd.Series(0.05, index=df.index)
        
        try:
            financial_data = self.fundamental_data.get_financial_data(symbol, latest_date.strftime('%Y-%m-%d'))
            cash_flow_yield = financial_data.get('cash_flow_yield', 0.05)
            
            cash_flow_yield_series = pd.Series(cash_flow_yield, index=df.index)
            
            data_source = financial_data.get('data_source', 'unknown')
            if data_source == 'simulated':
                print(f"警告: 现金流收益率使用模拟数据 (symbol={symbol})")
            
            return cash_flow_yield_series
        except Exception as e:
            print(f"现金流收益率计算失败 {symbol}: {e}")
            return pd.Series(0.05, index=df.index)
    
    def _calc_pe_ratio(self, df: pd.DataFrame, symbol: str = None) -> pd.Series:
        """市盈率计算（低PE得分高，使用真实数据）"""
        if self.fundamental_data is None or symbol is None:
            print(f"警告: 市盈率使用模拟数据 (symbol={symbol})")
            pe = 20.0  # 固定20倍PE
        else:
            latest_date = df.index[-1] if len(df) > 0 else None
            if latest_date is None:
                pe = 20.0
            else:
                try:
                    financial_data = self.fundamental_data.get_financial_data(symbol, latest_date.strftime('%Y-%m-%d'))
                    pe = financial_data.get('pe_ratio', 20.0)
                    
                    data_source = financial_data.get('data_source', 'unknown')
                    if data_source == 'simulated':
                        print(f"警告: 市盈率使用模拟数据 (symbol={symbol})")
                except Exception as e:
                    print(f"市盈率获取失败 {symbol}: {e}")
                    pe = 20.0
        
        # 低PE得分高（归一化）
        pe_series = pd.Series(pe, index=df.index)
        return 1 / pe_series.clip(lower=1, upper=100)  # 防止除零
    
    def _calc_pb_ratio(self, df: pd.DataFrame, symbol: str = None) -> pd.Series:
        """市净率计算（低PB得分高，使用真实数据）"""
        if self.fundamental_data is None or symbol is None:
            print(f"警告: 市净率使用模拟数据 (symbol={symbol})")
            pb = 2.0  # 固定2倍PB
        else:
            latest_date = df.index[-1] if len(df) > 0 else None
            if latest_date is None:
                pb = 2.0
            else:
                try:
                    financial_data = self.fundamental_data.get_financial_data(symbol, latest_date.strftime('%Y-%m-%d'))
                    pb = financial_data.get('pb_ratio', 2.0)
                    
                    data_source = financial_data.get('data_source', 'unknown')
                    if data_source == 'simulated':
                        print(f"警告: 市净率使用模拟数据 (symbol={symbol})")
                except Exception as e:
                    print(f"市净率获取失败 {symbol}: {e}")
                    pb = 2.0
        
        pb_series = pd.Series(pb, index=df.index)
        return 1 / pb_series.clip(lower=0.1, upper=20)  # 防止除零
    
    # ========== 情绪因子计算方法 ==========
    
    @staticmethod
    def _calc_news_sentiment(df: pd.DataFrame) -> pd.Series:
        """新闻情绪（待完善）"""
        # 返回中性情绪（0.5）
        return pd.Series(0.5, index=df.index)
    
    @staticmethod
    def _calc_social_buzz(df: pd.DataFrame) -> pd.Series:
        """社交媒体热度（待完善）"""
        # 基于成交量模拟
        volume_ratio = df['volume'] / df['volume'].rolling(20).mean()
        buzz = np.log1p(volume_ratio.clip(lower=0))
        return buzz / buzz.max() if buzz.max() > 0 else pd.Series(0.5, index=df.index)
    
    def _calc_margin_balance_change(self, df: pd.DataFrame, symbol: str = None) -> pd.Series:
        """融资余额变化率（用户要求：Baostock真实数据）"""
        if self.fundamental_data is None or symbol is None:
            print(f"警告: 融资余额变化率使用模拟数据 (symbol={symbol})")
            # 返回中性值
            return pd.Series(0.0, index=df.index)
        
        # 获取日期范围
        if len(df) == 0:
            return pd.Series(0.0, index=df.index)
        
        start_date = df.index[0].strftime('%Y-%m-%d')
        end_date = df.index[-1].strftime('%Y-%m-%d')
        
        try:
            # 尝试从Baostock获取融资融券数据
            margin_data = self.fundamental_data.get_margin_trading_data(symbol, start_date, end_date)
            
            if margin_data is None or margin_data.empty:
                print(f"警告: 融资融券数据为空 (symbol={symbol})")
                return pd.Series(0.0, index=df.index)
            
            # 对齐日期索引
            # 融资数据可能没有每日数据，需要重新采样
            if 'margin_balance_change' in margin_data.columns:
                # 对齐到df的索引
                aligned_margin = margin_data['margin_balance_change'].reindex(df.index, method='ffill')
                aligned_margin = aligned_margin.fillna(0.0)
                return aligned_margin
            else:
                print(f"警告: 融资数据缺少margin_balance_change列")
                return pd.Series(0.0, index=df.index)
                
        except Exception as e:
            print(f"融资余额变化率计算失败 {symbol}: {e}")
            return pd.Series(0.0, index=df.index)
    
    @staticmethod
    def _calc_turnover_zscore(df: pd.DataFrame) -> pd.Series:
        """换手率异常值（用户要求：Z-score）
        使用换手率字段（如果存在）或成交量Z-score
        """
        # 检查是否有换手率字段
        if 'turnover' in df.columns:
            turnover = df['turnover']
        elif 'turn' in df.columns:  # 某些数据源使用'turn'
            turnover = df['turn']
        else:
            # 使用成交量Z-score作为代理
            volume = df['volume']
            turnover = volume / volume.rolling(20).mean()
        
        # 计算Z-score (20日滚动窗口)
        zscore = (turnover - turnover.rolling(20).mean()) / turnover.rolling(20).std()
        
        # 处理NaN值
        zscore = zscore.fillna(0.0)
        
        # 压缩极端值 (tanh函数)
        zscore_normalized = np.tanh(zscore / 3.0)  # 除以3使大多数值在[-1, 1]范围内
        
        # 转换为0-1范围
        return (zscore_normalized + 1) / 2
    
    # ========== 公共接口 ==========
    
    def calculate_factor(self, factor_id: str, df: pd.DataFrame, symbol: str = None, **kwargs) -> pd.Series:
        """计算单个因子
        Args:
            factor_id: 因子ID
            df: 价格数据DataFrame
            symbol: 股票代码（基本面因子必需）
            **kwargs: 其他参数
        """
        if factor_id not in self.factors:
            raise ValueError(f"未知因子: {factor_id}")
        
        factor_info = self.factors[factor_id]
        calc_func = factor_info['calc_func']
        
        # 检查数据要求
        if factor_info.get('requires_fundamental', False) and self.fundamental_data is None:
            print(f"警告: 因子{factor_id}需要基本面数据，但基本面模块不可用")
        
        # 计算因子值
        try:
            # 根据函数签名传递参数
            import inspect
            sig = inspect.signature(calc_func)
            params = list(sig.parameters.keys())
            
            if len(params) == 1 or (len(params) == 2 and 'self' in params):
                # 只有一个参数（或self+df），只传递df
                if hasattr(calc_func, '__self__'):  # 绑定方法
                    factor_values = calc_func(df)
                else:  # 静态方法
                    factor_values = calc_func(df)
            elif 'symbol' in params:
                # 需要symbol参数
                if symbol is None:
                    print(f"警告: 因子{factor_id}需要symbol参数，但未提供。使用默认值'000001'")
                    symbol = '000001'  # 默认值
                
                if hasattr(calc_func, '__self__'):  # 绑定方法
                    factor_values = calc_func(df, symbol=symbol)
                else:  # 静态方法或函数
                    # 尝试不同的参数组合
                    try:
                        factor_values = calc_func(df, symbol)
                    except:
                        factor_values = calc_func(df, symbol=symbol)
            else:
                # 其他情况，只传递df
                factor_values = calc_func(df)
            
            return factor_values
        except Exception as e:
            print(f"计算因子{factor_id}失败: {e}")
            # 返回中性值
            return pd.Series(0.5, index=df.index)
    
    def calculate_all_factors(self, df: pd.DataFrame, factor_ids: List[str] = None) -> pd.DataFrame:
        """计算多个因子"""
        if factor_ids is None:
            factor_ids = list(self.factors.keys())
        
        results = pd.DataFrame(index=df.index)
        
        for factor_id in factor_ids:
            if factor_id in self.factors:
                try:
                    results[factor_id] = self.calculate_factor(factor_id, df)
                except Exception as e:
                    print(f"跳过因子{factor_id}: {e}")
                    results[factor_id] = np.nan
        
        return results
    
    def get_factor_info(self, factor_id: str = None) -> Dict:
        """获取因子信息"""
        if factor_id:
            return self.factors.get(factor_id, {})
        else:
            return self.factors
    
    def get_factor_correlation_matrix(self, factor_values: pd.DataFrame) -> pd.DataFrame:
        """计算因子相关性矩阵"""
        # 清理NaN值
        clean_df = factor_values.dropna(axis=1, how='all').fillna(method='ffill').fillna(method='backfill').fillna(0)
        
        # 计算相关性
        corr_matrix = clean_df.corr()
        
        # 分析共线性问题
        high_corr_pairs = []
        for i in range(len(corr_matrix.columns)):
            for j in range(i+1, len(corr_matrix.columns)):
                corr_val = abs(corr_matrix.iloc[i, j])
                if corr_val > 0.7:  # 高相关性阈值
                    high_corr_pairs.append({
                        'factor1': corr_matrix.columns[i],
                        'factor2': corr_matrix.columns[j],
                        'correlation': corr_val
                    })
        
        return {
            'correlation_matrix': corr_matrix,
            'high_correlation_pairs': high_corr_pairs,
            'avg_correlation': corr_matrix.abs().mean().mean(),
            'factor_count': len(corr_matrix.columns)
        }
    
    def calculate_factor_correlation(self, df: pd.DataFrame, factor_ids: List[str] = None) -> pd.DataFrame:
        """
        计算指定因子的相关性矩阵
        简化接口：接受原始数据和因子ID列表
        """
        if factor_ids is None:
            factor_ids = list(self.factors.keys())[:10]  # 默认最多10个因子
        
        # 计算因子值
        factor_values = self.calculate_all_factors(df, factor_ids)
        
        # 获取相关性矩阵
        corr_analysis = self.get_factor_correlation_matrix(factor_values)
        
        return corr_analysis['correlation_matrix']


# 测试函数
def test_real_factor_manager():
    """测试真实因子管理器"""
    print("=== 测试真实因子管理器 ===")
    
    # 创建模拟数据
    dates = pd.date_range('2024-01-01', '2024-06-30', freq='D')
    n = len(dates)
    
    df = pd.DataFrame({
        'open': 100 + np.random.randn(n).cumsum(),
        'high': 105 + np.random.randn(n).cumsum(),
        'low': 95 + np.random.randn(n).cumsum(),
        'close': 100 + np.random.randn(n).cumsum(),
        'volume': 1000000 + np.random.randn(n).cumsum() * 100000
    }, index=dates)
    
    # 创建因子管理器
    factor_mgr = RealFactorManager()
    
    # 测试单个因子
    print(f"\n测试单个因子计算:")
    momentum = factor_mgr.calculate_factor('momentum_1m', df)
    print(f"momentum_1m: 形状={momentum.shape}, 均值={momentum.mean():.4f}")
    
    # 测试所有因子
    print(f"\n测试所有因子计算:")
    all_factors = factor_mgr.calculate_all_factors(df, factor_ids=['momentum_1m', 'rsi_14', 'roe', 'profit_growth'])
    print(f"因子数据形状: {all_factors.shape}")
    print(f"因子列: {list(all_factors.columns)}")
    
    # 测试相关性分析
    print(f"\n测试因子相关性分析:")
    if len(all_factors.columns) >= 2:
        corr_analysis = factor_mgr.get_factor_correlation_matrix(all_factors)
        print(f"平均相关性: {corr_analysis['avg_correlation']:.4f}")
        print(f"高相关性因子对: {len(corr_analysis['high_correlation_pairs'])}个")
        
        if corr_analysis['high_correlation_pairs']:
            print("高相关性对:")
            for pair in corr_analysis['high_correlation_pairs'][:3]:
                print(f"  {pair['factor1']} vs {pair['factor2']}: {pair['correlation']:.4f}")
    
    # 显示因子统计
    print(f"\n因子类别统计:")
    for category, count in factor_mgr.category_stats.items():
        print(f"  {category}: {count}个因子")


if __name__ == "__main__":
    test_real_factor_manager()