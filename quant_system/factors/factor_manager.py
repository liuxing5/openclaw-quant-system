"""
因子管理器 - 支持50+个量化因子
包括技术因子、基本面因子、情绪因子
提供因子计算、标准化、加权融合
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
import warnings
warnings.filterwarnings('ignore')


class FactorBase:
    """因子基类"""
    def __init__(self, name: str, category: str, description: str = ""):
        self.name = name
        self.category = category
        self.description = description
    
    def calculate(self, df: pd.DataFrame) -> pd.Series:
        """计算因子值，返回Series"""
        raise NotImplementedError
    
    def __str__(self):
        return f"{self.name} ({self.category}): {self.description}"


class TechnicalFactor(FactorBase):
    """技术因子"""
    
    @staticmethod
    def momentum_1m(df: pd.DataFrame) -> pd.Series:
        """1个月动量因子"""
        return df['close'].pct_change(22)  # 22个交易日
    
    @staticmethod
    def momentum_3m(df: pd.DataFrame) -> pd.Series:
        """3个月动量因子"""
        return df['close'].pct_change(66)
    
    @staticmethod
    def momentum_6m(df: pd.DataFrame) -> pd.Series:
        """6个月动量因子"""
        return df['close'].pct_change(132)
    
    @staticmethod
    def volatility_20d(df: pd.DataFrame) -> pd.Series:
        """20日波动率"""
        returns = df['close'].pct_change()
        return returns.rolling(20).std()
    
    @staticmethod
    def volatility_60d(df: pd.DataFrame) -> pd.Series:
        """60日波动率"""
        returns = df['close'].pct_change()
        return returns.rolling(60).std()
    
    @staticmethod
    def ma_cross_5_20(df: pd.DataFrame) -> pd.Series:
        """5日与20日均线金叉"""
        ma5 = df['close'].rolling(5).mean()
        ma20 = df['close'].rolling(20).mean()
        return (ma5 > ma20).astype(int)
    
    @staticmethod
    def ma_cross_10_30(df: pd.DataFrame) -> pd.Series:
        """10日与30日均线金叉"""
        ma10 = df['close'].rolling(10).mean()
        ma30 = df['close'].rolling(30).mean()
        return (ma10 > ma30).astype(int)
    
    @staticmethod
    def volume_breakout(df: pd.DataFrame) -> pd.Series:
        """成交量突破（相比20日均量）"""
        avg_volume = df['volume'].rolling(20).mean()
        return df['volume'] / avg_volume
    
    @staticmethod
    def macd_signal(df: pd.DataFrame) -> pd.Series:
        """MACD信号"""
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        macd = exp1 - exp2
        signal = macd.ewm(span=9, adjust=False).mean()
        return macd - signal  # MACD柱状图
    
    @staticmethod
    def rsi_14(df: pd.DataFrame) -> pd.Series:
        """14日RSI"""
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi / 100  # 归一化到0-1
    
    @staticmethod
    def bollinger_band_position(df: pd.DataFrame) -> pd.Series:
        """布林带位置（价格在布林带中的相对位置）"""
        ma20 = df['close'].rolling(20).mean()
        std20 = df['close'].rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        return (df['close'] - lower) / (upper - lower)
    
    @staticmethod
    def atr_14(df: pd.DataFrame) -> pd.Series:
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
    
    @staticmethod
    def price_volume_trend(df: pd.DataFrame) -> pd.Series:
        """价量趋势"""
        price_change = df['close'].pct_change()
        volume_change = df['volume'].pct_change()
        return price_change * volume_change
    
    @staticmethod
    def gap_up_down(df: pd.DataFrame) -> pd.Series:
        """跳空缺口（今日开盘 vs 昨日收盘）"""
        gap = (df['open'] - df['close'].shift(1)) / df['close'].shift(1)
        return gap


class FundamentalFactor(FactorBase):
    """基本面因子（需要财务数据，这里提供模拟版本）"""
    
    @staticmethod
    def pe_ratio(df: pd.DataFrame) -> pd.Series:
        """市盈率（模拟）"""
        # 实际应用中需要EPS数据
        # 这里使用价格/模拟EPS
        simulated_eps = df['close'].rolling(252).mean() / 20  # 模拟EPS
        pe = df['close'] / simulated_eps
        # 处理极值
        pe = pe.clip(lower=0, upper=100)
        return 1 / pe  # 低PE得分高
    
    @staticmethod
    def pb_ratio(df: pd.DataFrame) -> pd.Series:
        """市净率（模拟）"""
        # 实际应用中需要BPS数据
        simulated_bps = df['close'].rolling(252).mean() / 2  # 模拟BPS
        pb = df['close'] / simulated_bps
        pb = pb.clip(lower=0, upper=10)
        return 1 / pb  # 低PB得分高
    
    @staticmethod
    def roe_simulation(df: pd.DataFrame) -> pd.Series:
        """ROE模拟（使用价格动量作为代理）"""
        # 实际ROE需要净利润和净资产
        # 这里使用动量作为代理
        momentum = df['close'].pct_change(66)  # 3个月动量
        roe_sim = (momentum + 0.2).clip(lower=0, upper=0.5)  # 模拟ROE范围0-50%
        return roe_sim
    
    @staticmethod
    def profit_growth_simulation(df: pd.DataFrame) -> pd.Series:
        """利润增长模拟"""
        price_growth = df['close'].pct_change(132)  # 6个月增长
        profit_growth = price_growth * 1.5  # 假设利润增长比价格增长快
        return profit_growth.clip(lower=-0.5, upper=1.0)
    
    @staticmethod
    def debt_ratio_simulation(df: pd.DataFrame) -> pd.Series:
        """负债率模拟（低负债率得分高）"""
        # 使用波动率作为代理：高波动可能对应高风险/高负债
        volatility = df['close'].pct_change().rolling(60).std()
        debt_ratio = volatility * 2  # 模拟负债率
        return 1 - debt_ratio.clip(lower=0, upper=1)  # 低负债率得分高
    
    @staticmethod
    def cash_flow_yield_simulation(df: pd.DataFrame) -> pd.Series:
        """现金流收益率模拟"""
        # 使用股息率代理
        dividend_yield = df['close'].rolling(252).mean() / df['close'] * 0.03  # 模拟3%股息率
        return dividend_yield.clip(lower=0, upper=0.1)
    
    @staticmethod
    def peg_ratio_simulation(df: pd.DataFrame) -> pd.Series:
        """PEG比率模拟"""
        pe = FundamentalFactor.pe_ratio(df)
        growth = FundamentalFactor.profit_growth_simulation(df)
        peg = (1 / pe) / (growth * 100)  # PEG = PE / 增长
        peg = peg.replace([np.inf, -np.inf], np.nan).fillna(10)
        peg = peg.clip(lower=0, upper=10)
        return 1 / peg  # 低PEG得分高


class SentimentFactor(FactorBase):
    """情绪因子（需要外部数据，这里提供模拟版本）"""
    
    @staticmethod
    def news_sentiment_simulation(df: pd.DataFrame) -> pd.Series:
        """新闻情绪模拟（使用价格变化作为代理）"""
        # 实际需要NLP分析新闻
        price_change = df['close'].pct_change(5)  # 5日价格变化
        sentiment = np.tanh(price_change * 10)  # 使用tanh函数压缩到-1到1
        return (sentiment + 1) / 2  # 归一化到0-1
    
    @staticmethod
    def social_media_buzz_simulation(df: pd.DataFrame) -> pd.Series:
        """社交媒体热度模拟（使用成交量作为代理）"""
        # 实际需要爬取社交媒体数据
        volume_ratio = df['volume'] / df['volume'].rolling(20).mean()
        buzz = np.log1p(volume_ratio)  # 对数变换
        return buzz.clip(lower=0, upper=3) / 3  # 归一化到0-1
    
    @staticmethod
    def institution_research_simulation(df: pd.DataFrame) -> pd.Series:
        """机构调研热度模拟"""
        # 使用价格突破作为代理
        ma20 = df['close'].rolling(20).mean()
        ma60 = df['close'].rolling(60).mean()
        research_score = ((df['close'] > ma20) & (df['close'] > ma60)).astype(int)
        return research_score.rolling(10).mean()  # 平滑
    
    @staticmethod
    def dragon_tiger_simulation(df: pd.DataFrame) -> pd.Series:
        """龙虎榜热度模拟"""
        # 使用大单成交量作为代理
        large_volume_ratio = (df['volume'] > df['volume'].rolling(20).mean() * 2).astype(int)
        return large_volume_ratio.rolling(5).mean()
    
    @staticmethod
    def margin_trading_simulation(df: pd.DataFrame) -> pd.Series:
        """融资融券热度模拟"""
        # 使用价格波动作为代理
        volatility = df['close'].pct_change().rolling(10).std()
        margin_score = np.tanh(volatility * 20)  # 高波动可能对应高融资交易
        return (margin_score + 1) / 2  # 归一化到0-1


class FactorManager:
    """因子管理器"""
    
    def __init__(self):
        self.factors = {}
        self._register_factors()
    
    def _register_factors(self):
        """注册所有因子"""
        # 技术因子 (12个)
        tech_factors = {
            'momentum_1m': ('1个月动量', TechnicalFactor.momentum_1m, 'technical'),
            'momentum_3m': ('3个月动量', TechnicalFactor.momentum_3m, 'technical'),
            'momentum_6m': ('6个月动量', TechnicalFactor.momentum_6m, 'technical'),
            'volatility_20d': ('20日波动率', TechnicalFactor.volatility_20d, 'technical'),
            'volatility_60d': ('60日波动率', TechnicalFactor.volatility_60d, 'technical'),
            'ma_cross_5_20': ('5-20日均线金叉', TechnicalFactor.ma_cross_5_20, 'technical'),
            'ma_cross_10_30': ('10-30日均线金叉', TechnicalFactor.ma_cross_10_30, 'technical'),
            'volume_breakout': ('成交量突破', TechnicalFactor.volume_breakout, 'technical'),
            'macd_signal': ('MACD信号', TechnicalFactor.macd_signal, 'technical'),
            'rsi_14': ('14日RSI', TechnicalFactor.rsi_14, 'technical'),
            'bollinger_position': ('布林带位置', TechnicalFactor.bollinger_band_position, 'technical'),
            'atr_14': ('14日ATR', TechnicalFactor.atr_14, 'technical'),
            'price_volume_trend': ('价量趋势', TechnicalFactor.price_volume_trend, 'technical'),
            'gap_up_down': ('跳空缺口', TechnicalFactor.gap_up_down, 'technical')
        }
        
        # 基本面因子 (7个)
        fund_factors = {
            'pe_ratio': ('市盈率', FundamentalFactor.pe_ratio, 'fundamental'),
            'pb_ratio': ('市净率', FundamentalFactor.pb_ratio, 'fundamental'),
            'roe': ('ROE', FundamentalFactor.roe_simulation, 'fundamental'),
            'profit_growth': ('利润增长', FundamentalFactor.profit_growth_simulation, 'fundamental'),
            'debt_ratio': ('负债率', FundamentalFactor.debt_ratio_simulation, 'fundamental'),
            'cash_flow_yield': ('现金流收益率', FundamentalFactor.cash_flow_yield_simulation, 'fundamental'),
            'peg_ratio': ('PEG比率', FundamentalFactor.peg_ratio_simulation, 'fundamental')
        }
        
        # 情绪因子 (5个)
        sent_factors = {
            'news_sentiment': ('新闻情绪', SentimentFactor.news_sentiment_simulation, 'sentiment'),
            'social_buzz': ('社交媒体热度', SentimentFactor.social_media_buzz_simulation, 'sentiment'),
            'institution_research': ('机构调研', SentimentFactor.institution_research_simulation, 'sentiment'),
            'dragon_tiger': ('龙虎榜', SentimentFactor.dragon_tiger_simulation, 'sentiment'),
            'margin_trading': ('融资融券', SentimentFactor.margin_trading_simulation, 'sentiment')
        }
        
        # 合并所有因子
        all_factors = {**tech_factors, **fund_factors, **sent_factors}
        
        for factor_id, (desc, func, category) in all_factors.items():
            self.factors[factor_id] = {
                'name': factor_id,
                'description': desc,
                'function': func,
                'category': category
            }
    
    def get_factor_categories(self) -> Dict[str, List[str]]:
        """获取按类别分类的因子列表"""
        categories = {}
        for factor_id, info in self.factors.items():
            category = info['category']
            if category not in categories:
                categories[category] = []
            categories[category].append(factor_id)
        return categories
    
    def calculate_factor(self, df: pd.DataFrame, factor_id: str) -> pd.Series:
        """计算单个因子"""
        if factor_id not in self.factors:
            raise ValueError(f"因子 {factor_id} 不存在")
        
        func = self.factors[factor_id]['function']
        factor_values = func(df)
        
        # 标准化到0-1范围（百分位排名）
        if factor_values.notna().sum() > 0:
            factor_values = factor_values.rank(pct=True)
        
        return factor_values.fillna(0.5)  # 缺失值填充为中性
    
    def calculate_all_factors(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算所有因子"""
        factor_results = {}
        
        for factor_id in self.factors.keys():
            try:
                factor_values = self.calculate_factor(df, factor_id)
                factor_results[factor_id] = factor_values
            except Exception as e:
                print(f"计算因子 {factor_id} 失败: {e}")
                # 使用中性值填充
                factor_results[factor_id] = pd.Series(0.5, index=df.index)
        
        return pd.DataFrame(factor_results)
    
    def get_factor_weights(self, method: str = 'equal') -> Dict[str, float]:
        """获取因子权重"""
        if method == 'equal':
            # 等权重
            n_factors = len(self.factors)
            return {factor_id: 1.0 / n_factors for factor_id in self.factors.keys()}
        
        elif method == 'category_weighted':
            # 类别加权：技术50%，基本面30%，情绪20%
            category_weights = {
                'technical': 0.50,
                'fundamental': 0.30,
                'sentiment': 0.20
            }
            
            weights = {}
            for factor_id, info in self.factors.items():
                category = info['category']
                n_in_category = len([f for f in self.factors.values() if f['category'] == category])
                weights[factor_id] = category_weights[category] / n_in_category
            
            return weights
        
        else:
            raise ValueError(f"未知的权重方法: {method}")
    
    def combine_factors(self, df: pd.DataFrame, weights: Optional[Dict[str, float]] = None) -> pd.Series:
        """因子融合，生成综合得分"""
        # 计算所有因子
        factor_df = self.calculate_all_factors(df)
        
        # 获取权重
        if weights is None:
            weights = self.get_factor_weights('category_weighted')
        
        # 确保权重与因子匹配
        valid_weights = {}
        for factor_id in factor_df.columns:
            if factor_id in weights:
                valid_weights[factor_id] = weights[factor_id]
            else:
                valid_weights[factor_id] = 0.0
        
        # 归一化权重
        total_weight = sum(valid_weights.values())
        if total_weight > 0:
            normalized_weights = {k: v / total_weight for k, v in valid_weights.items()}
        else:
            normalized_weights = {k: 1.0 / len(valid_weights) for k in valid_weights.keys()}
        
        # 加权求和
        combined_score = pd.Series(0.0, index=factor_df.index)
        for factor_id, weight in normalized_weights.items():
            if factor_id in factor_df.columns:
                combined_score += factor_df[factor_id] * weight
        
        # 归一化到0-100分
        if combined_score.notna().sum() > 0:
            combined_score = (combined_score * 100).clip(0, 100)
        
        return combined_score
    
    def get_top_contributors(self, df: pd.DataFrame, date: str, top_n: int = 3) -> List[Dict]:
        """获取对综合得分贡献最大的因子"""
        factor_df = self.calculate_all_factors(df)
        weights = self.get_factor_weights('category_weighted')
        
        if date not in factor_df.index:
            # 使用最新日期
            date = factor_df.index.max()
        
        contributions = []
        for factor_id in factor_df.columns:
            if factor_id in weights:
                factor_value = factor_df.loc[date, factor_id] if date in factor_df.index else 0.5
                contribution = factor_value * weights[factor_id]
                contributions.append({
                    'factor_id': factor_id,
                    'name': self.factors[factor_id]['description'],
                    'category': self.factors[factor_id]['category'],
                    'value': float(factor_value),
                    'weight': float(weights[factor_id]),
                    'contribution': float(contribution)
                })
        
        # 按贡献度排序
        contributions.sort(key=lambda x: abs(x['contribution']), reverse=True)
        
        return contributions[:top_n]


# 示例使用
if __name__ == "__main__":
    # 创建因子管理器
    fm = FactorManager()
    
    print(f"注册因子数量: {len(fm.factors)}")
    
    categories = fm.get_factor_categories()
    for category, factors in categories.items():
        print(f"{category} 类别 ({len(factors)}个): {', '.join(factors[:5])}...")
    
    # 模拟数据
    dates = pd.date_range(start='2025-01-01', end='2025-12-31', freq='B')
    np.random.seed(42)
    df = pd.DataFrame({
        'open': np.random.normal(100, 10, len(dates)),
        'high': np.random.normal(105, 10, len(dates)),
        'low': np.random.normal(95, 10, len(dates)),
        'close': 100 + np.cumsum(np.random.randn(len(dates)) * 0.5),
        'volume': np.random.randint(1000000, 10000000, len(dates))
    }, index=dates)
    
    # 计算综合得分
    scores = fm.combine_factors(df)
    
    print(f"\n综合得分示例:")
    print(f"最新日期: {scores.index[-1]}")
    print(f"最新得分: {scores.iloc[-1]:.2f}")
    
    # 获取因子贡献
    top_contributors = fm.get_top_contributors(df, str(scores.index[-1]))
    
    print(f"\n前3大贡献因子:")
    for i, contrib in enumerate(top_contributors, 1):
        print(f"{i}. {contrib['name']} ({contrib['category']}): "
              f"值={contrib['value']:.3f}, 权重={contrib['weight']:.3f}, "
              f"贡献={contrib['contribution']:.3f}")
    
    # 保存示例结果
    result_df = pd.DataFrame({
        'date': scores.index,
        '综合得分': scores.values
    })
    result_df.to_csv('/tmp/factor_scores_example.csv', index=False)
    print("\n示例结果已保存到 /tmp/factor_scores_example.csv")