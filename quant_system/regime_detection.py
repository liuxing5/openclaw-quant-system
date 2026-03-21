#!/usr/bin/env python3
"""
市场状态识别模型 (Regime Detection)
识别牛市、熊市、震荡市，实现自适应策略切换
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
import warnings
warnings.filterwarnings('ignore')

try:
    from sklearn.mixture import GaussianMixture
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

try:
    import statsmodels.api as sm
    STATSMODELS_AVAILABLE = True
except ImportError:
    STATSMODELS_AVAILABLE = False


class MarketRegimeDetector:
    """市场状态识别器"""
    
    def __init__(self, 
                 n_regimes: int = 3,  # 市场状态数量：牛市、熊市、震荡市
                 lookback_window: int = 252,  # 回溯窗口（1年）
                 volatility_threshold: float = 0.2,  # 波动率阈值
                 momentum_threshold: float = 0.1,   # 动量阈值
                 min_regime_days: int = 21):        # 最小状态持续时间
        self.n_regimes = n_regimes
        self.lookback_window = lookback_window
        self.volatility_threshold = volatility_threshold
        self.momentum_threshold = momentum_threshold
        self.min_regime_days = min_regime_days
        
        # 状态标签
        self.regime_labels = {
            0: '震荡市 (Sideways)',
            1: '牛市 (Bull)',
            2: '熊市 (Bear)'
        }
        
        # 状态特征
        self.regime_features = []
        self.regime_centers = {}
        self.regime_stats = {}
        
        # 模型
        self.model = None
        self.scaler = StandardScaler() if SKLEARN_AVAILABLE else None
        
        print(f"市场状态识别器初始化: {n_regimes}个状态")
    
    def extract_market_features(self, 
                               market_returns: pd.Series,
                               market_volumes: pd.Series = None) -> pd.DataFrame:
        """
        提取市场特征
        """
        features = pd.DataFrame(index=market_returns.index)
        
        # 1. 收益率特征
        features['return_1m'] = market_returns.rolling(21).mean()
        features['return_3m'] = market_returns.rolling(63).mean()
        features['return_12m'] = market_returns.rolling(252).mean()
        
        # 2. 波动率特征
        features['volatility_1m'] = market_returns.rolling(21).std()
        features['volatility_3m'] = market_returns.rolling(63).std()
        features['volatility_12m'] = market_returns.rolling(252).std()
        
        # 3. 动量特征
        features['momentum_3m'] = market_returns.rolling(63).sum()
        features['momentum_6m'] = market_returns.rolling(126).sum()
        
        # 4. 技术指标
        # RSI
        returns_pos = market_returns.where(market_returns > 0, 0)
        returns_neg = (-market_returns).where(market_returns < 0, 0)
        avg_gain = returns_pos.rolling(14).mean()
        avg_loss = returns_neg.rolling(14).mean()
        rs = avg_gain / avg_loss
        features['rsi'] = 100 - (100 / (1 + rs))
        
        # MACD (简化)
        ema12 = market_returns.ewm(span=12).mean()
        ema26 = market_returns.ewm(span=26).mean()
        features['macd'] = ema12 - ema26
        
        # 5. 成交量特征 (如果提供)
        if market_volumes is not None:
            volume_change = market_volumes.pct_change()
            features['volume_trend'] = volume_change.rolling(20).mean()
            features['volume_volatility'] = volume_change.rolling(20).std()
        
        # 6. 市场宽度特征 (简化)
        # 这里需要多股票数据，暂不实现
        
        # 7. 情绪指标
        # VIX/恐慌指数 (简化)
        features['volatility_ratio'] = features['volatility_1m'] / features['volatility_12m']
        
        # 8. 经济周期指标
        # 利率曲线、PMI等 (简化)
        
        # 清理NaN
        features = features.fillna(method='ffill').fillna(method='bfill').fillna(0)
        
        return features
    
    def detect_regimes_gmm(self, 
                          market_returns: pd.Series,
                          market_volumes: pd.Series = None) -> pd.Series:
        """
        使用高斯混合模型(GMM)识别市场状态
        """
        if not SKLEARN_AVAILABLE:
            raise ImportError("scikit-learn不可用")
        
        # 提取特征
        features = self.extract_market_features(market_returns, market_volumes)
        
        # 标准化
        features_scaled = self.scaler.fit_transform(features)
        
        # 训练GMM模型
        self.model = GaussianMixture(
            n_components=self.n_regimes,
            covariance_type='full',
            random_state=42,
            n_init=3
        )
        
        # 拟合数据
        self.model.fit(features_scaled)
        
        # 预测状态
        regime_labels = self.model.predict(features_scaled)
        
        # 转换为Series
        regime_series = pd.Series(regime_labels, index=features.index)
        
        # 状态平滑 (去除短暂状态)
        regime_series = self._smooth_regimes(regime_series)
        
        # 分析状态统计
        self._analyze_regimes(regime_series, market_returns)
        
        return regime_series
    
    def detect_regimes_rule_based(self,
                                 market_returns: pd.Series) -> pd.Series:
        """
        基于规则的简单状态识别
        """
        regimes = pd.Series(index=market_returns.index, dtype=int)
        
        # 计算滚动指标
        returns_3m = market_returns.rolling(63).mean()
        volatility_3m = market_returns.rolling(63).std()
        momentum_3m = market_returns.rolling(63).sum()
        
        # 基于规则分类
        for i in range(len(market_returns)):
            if i < 63:
                regimes.iloc[i] = 0  # 震荡市 (数据不足)
                continue
            
            ret = returns_3m.iloc[i]
            vol = volatility_3m.iloc[i]
            mom = momentum_3m.iloc[i]
            
            # 规则1: 高收益 + 低波动 = 牛市
            if ret > self.momentum_threshold and vol < self.volatility_threshold:
                regimes.iloc[i] = 1  # 牛市
            # 规则2: 负收益 + 高波动 = 熊市
            elif ret < -self.momentum_threshold and vol > self.volatility_threshold:
                regimes.iloc[i] = 2  # 熊市
            # 规则3: 其他情况 = 震荡市
            else:
                regimes.iloc[i] = 0  # 震荡市
        
        # 状态平滑
        regimes = self._smooth_regimes(regimes)
        
        # 分析状态统计
        self._analyze_regimes(regimes, market_returns)
        
        return regimes
    
    def _smooth_regimes(self, regimes: pd.Series) -> pd.Series:
        """
        平滑状态序列，去除短暂状态
        """
        smoothed = regimes.copy()
        
        # 滚动窗口平滑
        for i in range(len(regimes)):
            if i < self.min_regime_days:
                continue
            
            window = regimes.iloc[i-self.min_regime_days+1:i+1]
            if len(window) == self.min_regime_days:
                # 如果窗口内状态不一致，使用众数
                mode = window.mode()
                if len(mode) > 0:
                    smoothed.iloc[i] = mode.iloc[0]
        
        return smoothed
    
    def _analyze_regimes(self, 
                        regimes: pd.Series,
                        market_returns: pd.Series) -> Dict[str, Any]:
        """
        分析市场状态统计
        """
        # 对齐数据
        common_idx = regimes.index.intersection(market_returns.index)
        regimes_aligned = regimes.loc[common_idx]
        returns_aligned = market_returns.loc[common_idx]
        
        # 统计每个状态
        stats = {}
        for regime_id in range(self.n_regimes):
            mask = regimes_aligned == regime_id
            if mask.sum() > 0:
                regime_returns = returns_aligned[mask]
                
                stats[regime_id] = {
                    'label': self.regime_labels.get(regime_id, f'状态{regime_id}'),
                    'count': mask.sum(),
                    'percentage': mask.mean() * 100,
                    'mean_return': regime_returns.mean(),
                    'std_return': regime_returns.std(),
                    'sharpe_ratio': regime_returns.mean() / regime_returns.std() if regime_returns.std() > 0 else 0,
                    'positive_ratio': (regime_returns > 0).mean(),
                    'max_drawdown': self._calculate_max_drawdown(regime_returns)
                }
        
        self.regime_stats = stats
        return stats
    
    @staticmethod
    def _calculate_max_drawdown(returns: pd.Series) -> float:
        """计算最大回撤"""
        if len(returns) == 0:
            return 0
        
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        
        return drawdown.min()
    
    def get_regime_transition_matrix(self, regimes: pd.Series) -> pd.DataFrame:
        """
        计算状态转移矩阵
        """
        transitions = np.zeros((self.n_regimes, self.n_regimes))
        
        for i in range(1, len(regimes)):
            prev = regimes.iloc[i-1]
            curr = regimes.iloc[i]
            
            if not np.isnan(prev) and not np.isnan(curr):
                transitions[int(prev), int(curr)] += 1
        
        # 转换为概率
        row_sums = transitions.sum(axis=1, keepdims=True)
        transition_matrix = transitions / np.where(row_sums > 0, row_sums, 1)
        
        # 创建DataFrame
        labels = [self.regime_labels.get(i, f'状态{i}') for i in range(self.n_regimes)]
        df = pd.DataFrame(transition_matrix, index=labels, columns=labels)
        
        return df
    
    def predict_next_regime(self, 
                          current_features: pd.DataFrame,
                          current_regime: int = None) -> Dict[str, Any]:
        """
        预测下一个市场状态
        """
        if self.model is None:
            return {'error': '模型未训练'}
        
        # 标准化特征
        features_scaled = self.scaler.transform(current_features)
        
        # 预测状态概率
        if hasattr(self.model, 'predict_proba'):
            probabilities = self.model.predict_proba(features_scaled)[0]
        else:
            probabilities = np.zeros(self.n_regimes)
            if current_regime is not None:
                probabilities[current_regime] = 1.0
        
        # 状态转移预测
        if current_regime is not None and hasattr(self, 'transition_matrix'):
            transition_probs = self.transition_matrix[current_regime, :]
            # 结合当前状态和模型预测
            combined_probs = 0.7 * probabilities + 0.3 * transition_probs
        else:
            combined_probs = probabilities
        
        # 获取最可能状态
        next_regime = np.argmax(combined_probs)
        confidence = combined_probs[next_regime]
        
        return {
            'next_regime': next_regime,
            'regime_label': self.regime_labels.get(next_regime, f'状态{next_regime}'),
            'confidence': confidence,
            'probabilities': {
                self.regime_labels.get(i, f'状态{i}'): prob 
                for i, prob in enumerate(combined_probs)
            }
        }
    
    def generate_regime_strategy(self,
                               regimes: pd.Series,
                               market_returns: pd.Series) -> Dict[str, Any]:
        """
        生成基于市场状态的策略建议
        """
        # 分析每个状态下的最优策略
        strategy_rules = {}
        
        for regime_id, stats in self.regime_stats.items():
            label = stats['label']
            
            # 基于状态特征推荐策略
            if '牛' in label:
                # 牛市策略: 高仓位，进攻性配置
                strategy_rules[regime_id] = {
                    'name': '进攻策略',
                    'target_position': 0.9,  # 目标仓位
                    'risk_level': '中高',
                    'style': '成长型',
                    'sectors': ['科技', '消费', '金融'],
                    'stop_loss': -0.15  # 止损线
                }
            elif '熊' in label:
                # 熊市策略: 低仓位，防御性配置
                strategy_rules[regime_id] = {
                    'name': '防御策略',
                    'target_position': 0.3,
                    'risk_level': '低',
                    'style': '价值型',
                    'sectors': ['公用事业', '医疗', '必需消费品'],
                    'stop_loss': -0.08
                }
            else:
                # 震荡市策略: 中性仓位，波段操作
                strategy_rules[regime_id] = {
                    'name': '波段策略',
                    'target_position': 0.6,
                    'risk_level': '中',
                    'style': '均衡型',
                    'sectors': ['所有行业轮动'],
                    'stop_loss': -0.1
                }
        
        return strategy_rules


# 测试函数
def test_regime_detection():
    """测试市场状态识别"""
    print("=== 测试市场状态识别 ===")
    
    # 创建模拟市场数据
    np.random.seed(42)
    n_days = 1000
    
    # 模拟不同市场状态
    dates = pd.date_range('2020-01-01', periods=n_days, freq='D')
    
    # 分段模拟不同状态
    returns = np.zeros(n_days)
    
    # 第1段: 牛市 (高收益，低波动)
    returns[0:200] = np.random.normal(0.001, 0.01, 200)
    
    # 第2段: 熊市 (负收益，高波动)
    returns[200:400] = np.random.normal(-0.0005, 0.02, 200)
    
    # 第3段: 震荡市 (低收益，中等波动)
    returns[400:600] = np.random.normal(0.0002, 0.015, 200)
    
    # 第4段: 牛市
    returns[600:800] = np.random.normal(0.0012, 0.012, 200)
    
    # 第5段: 熊市
    returns[800:1000] = np.random.normal(-0.0008, 0.022, 200)
    
    market_returns = pd.Series(returns, index=dates)
    
    # 创建检测器
    detector = MarketRegimeDetector(n_regimes=3)
    
    print("1. 使用GMM识别市场状态...")
    try:
        regimes_gmm = detector.detect_regimes_gmm(market_returns)
        print(f"  识别完成: {len(regimes_gmm)}个状态点")
        
        # 统计状态分布
        for regime_id, stats in detector.regime_stats.items():
            print(f"  状态{regime_id} ({stats['label']}): {stats['count']}天 ({stats['percentage']:.1f}%)")
        
        # 计算转移矩阵
        transition_matrix = detector.get_regime_transition_matrix(regimes_gmm)
        print(f"\n2. 状态转移矩阵:")
        print(transition_matrix.round(3))
        
        # 生成策略建议
        strategies = detector.generate_regime_strategy(regimes_gmm, market_returns)
        print(f"\n3. 策略建议:")
        for regime_id, strategy in strategies.items():
            print(f"  状态{regime_id}: {strategy['name']} (仓位: {strategy['target_position']:.0%})")
        
        print("\n✅ 市场状态识别测试完成")
        
    except Exception as e:
        print(f"GMM识别失败: {e}")
        print("\n4. 使用基于规则的方法...")
        
        regimes_rule = detector.detect_regimes_rule_based(market_returns)
        print(f"  识别完成: {len(regimes_rule)}个状态点")
        
        for regime_id, stats in detector.regime_stats.items():
            print(f"  状态{regime_id} ({stats['label']}): {stats['count']}天 ({stats['percentage']:.1f}%)")
        
        print("\n✅ 基于规则的市场状态识别完成")


if __name__ == "__main__":
    test_regime_detection()