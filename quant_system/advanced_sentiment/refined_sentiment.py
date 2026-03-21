#!/usr/bin/env python3
"""
情绪因子精细化 - 动态阈值 + 状态机 + 衰减模型
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any, Optional, Callable
import warnings
warnings.filterwarnings('ignore')
import sys
import os

sys.path.append('/root/.openclaw/workspace/quant_system')

class RefinedSentimentFactor:
    """精细化情绪因子"""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or self._default_config()
        
        # 市场状态机
        self.market_states = {
            'bull': {'penalty_factor': 0.5, 'threshold_adjust': 1.2},
            'bear': {'penalty_factor': 2.0, 'threshold_adjust': 0.8},
            'consolidation': {'penalty_factor': 1.0, 'threshold_adjust': 1.0},
            'recovery': {'penalty_factor': 0.8, 'threshold_adjust': 1.1},
            'correction': {'penalty_factor': 1.5, 'threshold_adjust': 0.9}
        }
        
        # 衰减模型参数
        self.decay_half_life = self.config.get('decay_half_life', 10)  # 半衰期（日）
        self.decay_lambda = np.log(2) / self.decay_half_life
        
        # 历史状态记录
        self.state_history = []
        self.factor_history = {}
    
    def _default_config(self) -> Dict[str, Any]:
        """默认配置"""
        return {
            # 状态机参数
            'state_detection_window': 60,  # 状态检测窗口
            'state_transition_threshold': 0.6,  # 状态转换阈值
            
            # 动态阈值参数
            'quantile_levels': [0.1, 0.25, 0.5, 0.75, 0.9],  # 分位数水平
            'zscore_threshold': 1.5,  # Z-score阈值
            'adaptive_lookback': 252,  # 自适应阈值回顾期
            
            # 衰减模型参数
            'decay_half_life': 10,  # 半衰期（日）
            'decay_method': 'exponential',  # 指数衰减
            
            # 惩罚机制
            'base_penalty': 1.0,
            'penalty_sensitivity': 0.3,
            
            # 因子权重
            'factor_weights': {
                'volume_sentiment': 0.25,
                'price_sentiment': 0.30,
                'news_sentiment': 0.20,
                'social_sentiment': 0.15,
                'technical_sentiment': 0.10
            }
        }
    
    # ========== 市场状态机 ==========
    
    def detect_market_state(self,
                           market_data: pd.DataFrame,
                           current_date: str = None) -> Tuple[str, Dict[str, Any]]:
        """
        检测市场状态
        
        Args:
            market_data: 市场数据 (至少包含close, volume)
            current_date: 当前日期
        
        Returns:
            (状态名称, 状态参数)
        """
        if market_data.empty or 'close' not in market_data.columns:
            return 'consolidation', self.market_states['consolidation']
        
        # 确定分析窗口
        if current_date is not None:
            analysis_date = pd.to_datetime(current_date)
            window_end = analysis_date
            window_start = window_end - pd.Timedelta(days=self.config['state_detection_window'])
            
            # 提取窗口数据
            mask = (market_data.index >= window_start) & (market_data.index <= window_end)
            window_data = market_data.loc[mask]
        else:
            # 使用最后N天
            window_data = market_data.tail(self.config['state_detection_window'])
        
        if len(window_data) < 20:  # 最小数据要求
            return 'consolidation', self.market_states['consolidation']
        
        # 计算状态指标
        indicators = self._calculate_state_indicators(window_data)
        
        # 状态判断
        state_scores = {}
        
        # 1. 牛市判断
        bull_score = 0
        if indicators['trend_upward']:
            bull_score += 0.4
        if indicators['momentum_positive']:
            bull_score += 0.3
        if indicators['volume_increasing']:
            bull_score += 0.2
        if indicators['volatility_decreasing']:
            bull_score += 0.1
        state_scores['bull'] = bull_score
        
        # 2. 熊市判断
        bear_score = 0
        if indicators['trend_downward']:
            bear_score += 0.4
        if indicators['momentum_negative']:
            bear_score += 0.3
        if indicators['volume_decreasing']:
            bear_score += 0.2
        if indicators['volatility_increasing']:
            bear_score += 0.1
        state_scores['bear'] = bear_score
        
        # 3. 震荡市判断
        consolidation_score = 0
        if indicators['trend_flat']:
            consolidation_score += 0.5
        if indicators['volatility_stable']:
            consolidation_score += 0.3
        if indicators['volume_stable']:
            consolidation_score += 0.2
        state_scores['consolidation'] = consolidation_score
        
        # 4. 反弹市判断
        recovery_score = 0
        recent_data = window_data.tail(10)
        if len(recent_data) >= 5:
            recent_return = (recent_data['close'].iloc[-1] / recent_data['close'].iloc[0] - 1) * 100
            if recent_return > 5 and indicators['trend_downward']:  # 下跌后反弹
                recovery_score = 0.7
        state_scores['recovery'] = recovery_score
        
        # 5. 调整市判断
        correction_score = 0
        if len(recent_data) >= 5:
            recent_return = (recent_data['close'].iloc[-1] / recent_data['close'].iloc[0] - 1) * 100
            if recent_return < -5 and indicators['trend_upward']:  # 上涨后调整
                correction_score = 0.7
        state_scores['correction'] = correction_score
        
        # 选择最高分状态
        best_state = max(state_scores.items(), key=lambda x: x[1])
        
        # 状态确认阈值
        if best_state[1] < self.config['state_transition_threshold']:
            # 分数不足，保持前一个状态或默认状态
            if self.state_history:
                last_state = self.state_history[-1]['state']
                return last_state, self.market_states.get(last_state, self.market_states['consolidation'])
            else:
                return 'consolidation', self.market_states['consolidation']
        
        # 记录状态转换
        state_record = {
            'state': best_state[0],
            'score': best_state[1],
            'date': current_date or window_data.index[-1].strftime('%Y-%m-%d'),
            'indicators': indicators,
            'all_scores': state_scores
        }
        self.state_history.append(state_record)
        
        # 保持最近100条记录
        if len(self.state_history) > 100:
            self.state_history = self.state_history[-100:]
        
        return best_state[0], self.market_states.get(best_state[0], self.market_states['consolidation'])
    
    def _calculate_state_indicators(self, data: pd.DataFrame) -> Dict[str, Any]:
        """计算市场状态指标"""
        if data.empty or len(data) < 20:
            return {}
        
        prices = data['close']
        volumes = data['volume'] if 'volume' in data.columns else None
        
        indicators = {}
        
        # 1. 趋势方向
        # 短期均线 vs 长期均线
        ma_short = prices.rolling(10).mean()
        ma_long = prices.rolling(30).mean()
        
        current_ma_short = ma_short.iloc[-1] if not pd.isna(ma_short.iloc[-1]) else prices.iloc[-1]
        current_ma_long = ma_long.iloc[-1] if not pd.isna(ma_long.iloc[-1]) else prices.iloc[-1]
        
        indicators['trend_upward'] = current_ma_short > current_ma_long and (
            current_ma_short > ma_short.iloc[-10] if len(ma_short) >= 10 else True
        )
        indicators['trend_downward'] = current_ma_short < current_ma_long and (
            current_ma_short < ma_short.iloc[-10] if len(ma_short) >= 10 else True
        )
        indicators['trend_flat'] = not indicators['trend_upward'] and not indicators['trend_downward']
        
        # 2. 动量指标
        returns = prices.pct_change()
        momentum_20 = (prices.iloc[-1] / prices.iloc[-20] - 1) * 100 if len(prices) >= 20 else 0
        indicators['momentum_positive'] = momentum_20 > 2  # 20日涨幅>2%
        indicators['momentum_negative'] = momentum_20 < -2  # 20日跌幅>2%
        
        # 3. 波动率
        volatility_20 = returns.tail(20).std() * np.sqrt(252) if len(returns) >= 20 else 0
        volatility_60 = returns.tail(60).std() * np.sqrt(252) if len(returns) >= 60 else 0
        
        indicators['volatility_increasing'] = volatility_20 > volatility_60 * 1.2 if volatility_60 > 0 else False
        indicators['volatility_decreasing'] = volatility_20 < volatility_60 * 0.8 if volatility_60 > 0 else False
        indicators['volatility_stable'] = not indicators['volatility_increasing'] and not indicators['volatility_decreasing']
        
        # 4. 成交量
        if volumes is not None:
            volume_ma_20 = volumes.rolling(20).mean()
            current_volume = volumes.iloc[-1] if not pd.isna(volumes.iloc[-1]) else 0
            current_volume_ma = volume_ma_20.iloc[-1] if not pd.isna(volume_ma_20.iloc[-1]) else current_volume
            
            indicators['volume_increasing'] = current_volume > current_volume_ma * 1.3
            indicators['volume_decreasing'] = current_volume < current_volume_ma * 0.7
            indicators['volume_stable'] = not indicators['volume_increasing'] and not indicators['volume_decreasing']
        else:
            indicators['volume_increasing'] = False
            indicators['volume_decreasing'] = False
            indicators['volume_stable'] = True
        
        # 5. 技术指标
        rsi = self._calculate_rsi(prices)
        if rsi is not None:
            indicators['rsi_overbought'] = rsi.iloc[-1] > 70
            indicators['rsi_oversold'] = rsi.iloc[-1] < 30
        
        return indicators
    
    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> Optional[pd.Series]:
        """计算RSI"""
        if len(prices) < period + 1:
            return None
        
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    # ========== 动态阈值系统 ==========
    
    def calculate_dynamic_thresholds(self,
                                    factor_values: pd.Series,
                                    market_state: str = 'consolidation',
                                    lookback_days: int = None) -> Dict[str, float]:
        """
        计算动态阈值（分位数 + Z-score）
        
        Args:
            factor_values: 因子值序列
            market_state: 市场状态
            lookback_days: 回顾天数
        
        Returns:
            动态阈值字典
        """
        if factor_values.empty or len(factor_values) < 30:
            return self._get_default_thresholds(market_state)
        
        if lookback_days is None:
            lookback_days = self.config['adaptive_lookback']
        
        # 使用最近数据
        recent_values = factor_values.tail(min(lookback_days, len(factor_values)))
        
        # 1. 分位数阈值
        quantile_thresholds = {}
        for q in self.config['quantile_levels']:
            threshold = recent_values.quantile(q)
            quantile_thresholds[f'q_{int(q*100)}'] = threshold
        
        # 2. Z-score阈值
        mean_val = recent_values.mean()
        std_val = recent_values.std()
        
        if std_val > 0:
            z_thresholds = {
                'z_lower': mean_val - self.config['zscore_threshold'] * std_val,
                'z_upper': mean_val + self.config['zscore_threshold'] * std_val,
                'z_mean': mean_val,
                'z_std': std_val
            }
        else:
            z_thresholds = {
                'z_lower': mean_val - 1.0,
                'z_upper': mean_val + 1.0,
                'z_mean': mean_val,
                'z_std': 1.0
            }
        
        # 3. 市场状态调整
        state_params = self.market_states.get(market_state, self.market_states['consolidation'])
        adjust_factor = state_params['threshold_adjust']
        
        adjusted_thresholds = {}
        for key, value in quantile_thresholds.items():
            adjusted_thresholds[key] = value * adjust_factor
        
        for key, value in z_thresholds.items():
            if key not in ['z_mean', 'z_std']:
                adjusted_thresholds[key] = value * adjust_factor
            else:
                adjusted_thresholds[key] = value
        
        # 4. 历史极值
        historical_min = recent_values.min()
        historical_max = recent_values.max()
        historical_range = historical_max - historical_min
        
        adjusted_thresholds.update({
            'historical_min': historical_min,
            'historical_max': historical_max,
            'historical_range': historical_range,
            'current_value': factor_values.iloc[-1] if not factor_values.empty else 0,
            'market_state': market_state,
            'adjust_factor': adjust_factor
        })
        
        return adjusted_thresholds
    
    def _get_default_thresholds(self, market_state: str) -> Dict[str, float]:
        """获取默认阈值"""
        state_params = self.market_states.get(market_state, self.market_states['consolidation'])
        adjust_factor = state_params['threshold_adjust']
        
        # 基于市场状态的默认阈值
        defaults = {
            'bull': {
                'q_10': -0.8 * adjust_factor,
                'q_25': -0.4 * adjust_factor,
                'q_50': 0.0,
                'q_75': 0.4 * adjust_factor,
                'q_90': 0.8 * adjust_factor,
                'z_lower': -1.2 * adjust_factor,
                'z_upper': 1.2 * adjust_factor
            },
            'bear': {
                'q_10': -1.2 * adjust_factor,
                'q_25': -0.8 * adjust_factor,
                'q_50': -0.3,
                'q_75': 0.3 * adjust_factor,
                'q_90': 0.8 * adjust_factor,
                'z_lower': -1.5 * adjust_factor,
                'z_upper': 1.0 * adjust_factor
            },
            'consolidation': {
                'q_10': -1.0,
                'q_25': -0.5,
                'q_50': 0.0,
                'q_75': 0.5,
                'q_90': 1.0,
                'z_lower': -1.5,
                'z_upper': 1.5
            }
        }
        
        return defaults.get(market_state, defaults['consolidation'])
    
    # ========== 情绪衰减模型 ==========
    
    def apply_emotion_decay(self,
                           sentiment_scores: pd.Series,
                           decay_method: str = None,
                           half_life: int = None) -> pd.Series:
        """
        应用情绪衰减模型
        
        Args:
            sentiment_scores: 原始情绪分数
            decay_method: 衰减方法
            half_life: 半衰期
        
        Returns:
            衰减后的情绪分数
        """
        if sentiment_scores.empty:
            return sentiment_scores
        
        if decay_method is None:
            decay_method = self.config['decay_method']
        
        if half_life is None:
            half_life = self.config['decay_half_life']
        
        # 计算衰减权重
        if decay_method == 'exponential':
            decay_weights = self._calculate_exponential_decay_weights(
                len(sentiment_scores), half_life
            )
        elif decay_method == 'linear':
            decay_weights = self._calculate_linear_decay_weights(len(sentiment_scores))
        else:
            # 默认指数衰减
            decay_weights = self._calculate_exponential_decay_weights(
                len(sentiment_scores), half_life
            )
        
        # 应用衰减（加权平均）
        decayed_scores = sentiment_scores.copy()
        
        # 对每个时间点计算衰减影响
        for i in range(len(sentiment_scores)):
            if i == 0:
                continue
            
            # 考虑历史情绪的衰减影响
            historical_influence = 0
            total_weight = 0
            
            for j in range(max(0, i - len(decay_weights) + 1), i):
                weight_idx = i - j - 1
                if weight_idx < len(decay_weights):
                    weight = decay_weights[weight_idx]
                    historical_influence += sentiment_scores.iloc[j] * weight
                    total_weight += weight
            
            if total_weight > 0:
                # 当前情绪 = 新情绪 + 衰减后的历史情绪
                current_score = sentiment_scores.iloc[i]
                decayed_score = (current_score * 0.7) + (historical_influence / total_weight * 0.3)
                decayed_scores.iloc[i] = decayed_score
        
        return decayed_scores
    
    def _calculate_exponential_decay_weights(self, n: int, half_life: int) -> List[float]:
        """计算指数衰减权重"""
        lambda_val = np.log(2) / half_life
        weights = [np.exp(-lambda_val * i) for i in range(n)]
        
        # 归一化
        total = sum(weights)
        if total > 0:
            weights = [w / total for w in weights]
        
        return weights
    
    def _calculate_linear_decay_weights(self, n: int) -> List[float]:
        """计算线性衰减权重"""
        weights = [(n - i) / n for i in range(n)]
        
        # 归一化
        total = sum(weights)
        if total > 0:
            weights = [w / total for w in weights]
        
        return weights
    
    # ========== 综合情绪因子计算 ==========
    
    def calculate_refined_sentiment(self,
                                  stock_data: Dict[str, pd.DataFrame],
                                  market_data: pd.DataFrame,
                                  current_date: str = None) -> Dict[str, Dict[str, Any]]:
        """
        计算精细化情绪因子
        
        Args:
            stock_data: 股票数据 {symbol: DataFrame}
            market_data: 市场数据
            current_date: 当前日期
        
        Returns:
            精细化情绪因子结果
        """
        print("计算精细化情绪因子...")
        
        results = {}
        
        # 1. 检测市场状态
        market_state, state_params = self.detect_market_state(market_data, current_date)
        print(f"  市场状态: {market_state}")
        
        for symbol, data in stock_data.items():
            try:
                symbol_result = self._calculate_single_stock_sentiment(
                    symbol, data, market_state, state_params, current_date
                )
                
                results[symbol] = symbol_result
                
            except Exception as e:
                print(f"  {symbol}: 情绪计算失败 - {e}")
                results[symbol] = {
                    'error': str(e),
                    'sentiment_score': 0,
                    'market_state': market_state
                }
        
        # 汇总分析
        summary = self._summarize_sentiment_results(results, market_state)
        
        return {
            'individual_results': results,
            'summary': summary,
            'market_state': market_state,
            'state_params': state_params,
            'calculation_date': current_date or datetime.now().strftime('%Y-%m-%d')
        }
    
    def _calculate_single_stock_sentiment(self,
                                         symbol: str,
                                         data: pd.DataFrame,
                                         market_state: str,
                                         state_params: Dict[str, Any],
                                         current_date: str = None) -> Dict[str, Any]:
        """计算单股票精细化情绪"""
        
        if data.empty or len(data) < 30:
            return {
                'sentiment_score': 0,
                'market_state': market_state,
                'error': '数据不足'
            }
        
        # 确定分析日期
        if current_date is not None:
            analysis_date = pd.to_datetime(current_date)
            # 使用截至该日期的数据
            historical_data = data[data.index <= analysis_date]
        else:
            historical_data = data
        
        # 计算基础情绪因子
        base_factors = self._calculate_base_sentiment_factors(historical_data)
        
        # 应用衰减模型
        if 'sentiment_series' in base_factors:
            decayed_sentiment = self.apply_emotion_decay(
                base_factors['sentiment_series'],
                half_life=self.config['decay_half_life']
            )
            base_factors['decayed_sentiment'] = decayed_sentiment
            current_sentiment = decayed_sentiment.iloc[-1] if not decayed_sentiment.empty else 0
        else:
            current_sentiment = base_factors.get('current_sentiment', 0)
        
        # 计算动态阈值
        if 'sentiment_series' in base_factors:
            thresholds = self.calculate_dynamic_thresholds(
                base_factors['sentiment_series'],
                market_state
            )
        else:
            thresholds = self.calculate_dynamic_thresholds(
                pd.Series([current_sentiment]),
                market_state
            )
        
        # 应用市场状态惩罚
        penalty_factor = state_params['penalty_factor']
        adjusted_sentiment = current_sentiment * penalty_factor
        
        # 非线性惩罚（基于Z-score）
        if 'sentiment_series' in base_factors:
            mean_val = base_factors['sentiment_series'].mean()
            std_val = base_factors['sentiment_series'].std()
            
            if std_val > 0:
                z_score = (current_sentiment - mean_val) / std_val
                # 极端值额外惩罚
                if abs(z_score) > 2:
                    extreme_penalty = 1.0 + (abs(z_score) - 2) * 0.2
                    adjusted_sentiment /= extreme_penalty
            else:
                z_score = 0
        
        # 阈值信号
        threshold_signals = {}
        if thresholds:
            current_val = current_sentiment
            threshold_signals = {
                'below_q10': current_val < thresholds.get('q_10', -1),
                'below_q25': current_val < thresholds.get('q_25', -0.5),
                'above_q75': current_val > thresholds.get('q_75', 0.5),
                'above_q90': current_val > thresholds.get('q_90', 1),
                'below_z_lower': 'z_lower' in thresholds and current_val < thresholds['z_lower'],
                'above_z_upper': 'z_upper' in thresholds and current_val > thresholds['z_upper']
            }
        
        return {
            'symbol': symbol,
            'sentiment_score': current_sentiment,
            'adjusted_sentiment': adjusted_sentiment,
            'market_state': market_state,
            'penalty_factor': penalty_factor,
            'thresholds': thresholds,
            'threshold_signals': threshold_signals,
            'base_factors': {k: v for k, v in base_factors.items() if not isinstance(v, pd.Series)},
            'calculation_details': {
                'data_points': len(historical_data),
                'period_start': historical_data.index[0].strftime('%Y-%m-%d') if not historical_data.empty else None,
                'period_end': historical_data.index[-1].strftime('%Y-%m-%d') if not historical_data.empty else None
            }
        }
    
    def _calculate_base_sentiment_factors(self, data: pd.DataFrame) -> Dict[str, Any]:
        """计算基础情绪因子"""
        
        if data.empty or len(data) < 20:
            return {'current_sentiment': 0, 'error': '数据不足'}
        
        # 确保有必要的列
        required_cols = ['close', 'volume']
        missing_cols = [col for col in required_cols if col not in data.columns]
        if missing_cols:
            # 尝试使用默认列名
            if 'price' in data.columns and 'close' not in data.columns:
                data = data.rename(columns={'price': 'close'})
            if 'vol' in data.columns and 'volume' not in data.columns:
                data = data.rename(columns={'vol': 'volume'})
        
        if 'close' not in data.columns or len(data) < 20:
            return {'current_sentiment': 0, 'error': '缺少价格数据'}
        
        prices = data['close']
        volumes = data['volume'] if 'volume' in data.columns else pd.Series(1, index=prices.index)
        
        factors = {}
        
        # 1. 成交量情绪
        if 'volume' in data.columns:
            volume_factor = self._calculate_volume_sentiment(volumes)
            factors['volume_sentiment'] = volume_factor
        else:
            factors['volume_sentiment'] = 0
        
        # 2. 价格情绪
        price_factor = self._calculate_price_sentiment(prices)
        factors['price_sentiment'] = price_factor
        
        # 3. 技术指标情绪（简化）
        tech_factor = self._calculate_technical_sentiment(prices)
        factors['technical_sentiment'] = tech_factor
        
        # 4. 综合情绪分数（加权）
        weights = self.config['factor_weights']
        sentiment_score = (
            factors.get('volume_sentiment', 0) * weights['volume_sentiment'] +
            factors.get('price_sentiment', 0) * weights['price_sentiment'] +
            factors.get('technical_sentiment', 0) * weights['technical_sentiment']
        )
        
        # 新闻和社交媒体情绪（模拟）
        # 实际应用中应从API获取
        factors['news_sentiment'] = np.random.normal(0, 0.3)
        factors['social_sentiment'] = np.random.normal(0, 0.2)
        
        sentiment_score += (
            factors['news_sentiment'] * weights['news_sentiment'] +
            factors['social_sentiment'] * weights['social_sentiment']
        )
        
        # 生成情绪序列（用于衰减模型）
        sentiment_series = pd.Series(sentiment_score, index=[prices.index[-1]])
        
        # 如果有历史数据，可以计算更完整的序列
        # 这里简化处理
        if len(prices) >= 50:
            # 计算滑动窗口情绪
            window_size = 20
            sentiment_list = []
            
            for i in range(window_size, len(prices)):
                window_prices = prices.iloc[i-window_size:i]
                window_volumes = volumes.iloc[i-window_size:i] if len(volumes) >= i else None
                
                window_price_sentiment = self._calculate_price_sentiment(window_prices)
                
                if window_volumes is not None:
                    window_volume_sentiment = self._calculate_volume_sentiment(window_volumes)
                else:
                    window_volume_sentiment = 0
                
                window_sentiment = (
                    window_price_sentiment * weights['price_sentiment'] +
                    window_volume_sentiment * weights['volume_sentiment'] +
                    np.random.normal(0, 0.2)  # 模拟其他因子
                )
                
                sentiment_list.append(window_sentiment)
            
            if sentiment_list:
                sentiment_series = pd.Series(sentiment_list, index=prices.index[window_size:])
        
        factors['current_sentiment'] = sentiment_score
        factors['sentiment_series'] = sentiment_series
        
        return factors
    
    def _calculate_volume_sentiment(self, volumes: pd.Series) -> float:
        """计算成交量情绪"""
        if len(volumes) < 20:
            return 0
        
        # 成交量相对变化
        current_volume = volumes.iloc[-1]
        avg_volume_20 = volumes.tail(20).mean()
        
        if avg_volume_20 > 0:
            volume_ratio = current_volume / avg_volume_20
            # 标准化到[-1, 1]
            if volume_ratio > 2:
                return 1.0
            elif volume_ratio > 1.5:
                return 0.5
            elif volume_ratio > 1.2:
                return 0.2
            elif volume_ratio < 0.5:
                return -1.0
            elif volume_ratio < 0.8:
                return -0.5
            elif volume_ratio < 0.9:
                return -0.2
            else:
                return 0
        else:
            return 0
    
    def _calculate_price_sentiment(self, prices: pd.Series) -> float:
        """计算价格情绪"""
        if len(prices) < 20:
            return 0
        
        # 价格动量
        returns_5 = (prices.iloc[-1] / prices.iloc[-5] - 1) if len(prices) >= 5 else 0
        returns_10 = (prices.iloc[-1] / prices.iloc[-10] - 1) if len(prices) >= 10 else 0
        returns_20 = (prices.iloc[-1] / prices.iloc[-20] - 1) if len(prices) >= 20 else 0
        
        # 加权动量
        momentum = returns_5 * 0.4 + returns_10 * 0.3 + returns_20 * 0.3
        
        # 转换为情绪分数
        if momentum > 0.05:
            return 1.0
        elif momentum > 0.02:
            return 0.5
        elif momentum > 0.005:
            return 0.2
        elif momentum < -0.05:
            return -1.0
        elif momentum < -0.02:
            return -0.5
        elif momentum < -0.005:
            return -0.2
        else:
            return 0
    
    def _calculate_technical_sentiment(self, prices: pd.Series) -> float:
        """计算技术指标情绪"""
        if len(prices) < 30:
            return 0
        
        # RSI
        rsi = self._calculate_rsi(prices, period=14)
        if rsi is not None:
            current_rsi = rsi.iloc[-1]
            # RSI情绪：超卖为正，超买为负
            if current_rsi < 30:
                rsi_sentiment = 0.5  # 超卖，可能反弹
            elif current_rsi > 70:
                rsi_sentiment = -0.5  # 超买，可能回调
            else:
                rsi_sentiment = 0
        else:
            rsi_sentiment = 0
        
        # 均线排列
        ma_5 = prices.rolling(5).mean()
        ma_10 = prices.rolling(10).mean()
        ma_20 = prices.rolling(20).mean()
        
        if len(ma_5) >= 1 and len(ma_10) >= 1 and len(ma_20) >= 1:
            if ma_5.iloc[-1] > ma_10.iloc[-1] > ma_20.iloc[-1]:
                ma_sentiment = 0.5  # 多头排列
            elif ma_5.iloc[-1] < ma_10.iloc[-1] < ma_20.iloc[-1]:
                ma_sentiment = -0.5  # 空头排列
            else:
                ma_sentiment = 0
        else:
            ma_sentiment = 0
        
        # 综合技术情绪
        technical_sentiment = rsi_sentiment * 0.6 + ma_sentiment * 0.4
        
        return technical_sentiment
    
    def _summarize_sentiment_results(self,
                                    results: Dict[str, Dict[str, Any]],
                                    market_state: str) -> Dict[str, Any]:
        """汇总情绪因子结果"""
        
        valid_results = [r for r in results.values() if 'error' not in r]
        
        if not valid_results:
            return {'error': '无有效结果'}
        
        # 提取情绪分数
        sentiment_scores = [r.get('adjusted_sentiment', 0) for r in valid_results]
        original_scores = [r.get('sentiment_score', 0) for r in valid_results]
        
        # 统计指标
        summary = {
            'total_stocks': len(valid_results),
            'market_state': market_state,
            'sentiment_stats': {
                'mean': np.mean(sentiment_scores),
                'std': np.std(sentiment_scores),
                'min': np.min(sentiment_scores),
                'max': np.max(sentiment_scores),
                'median': np.median(sentiment_scores),
                'skewness': pd.Series(sentiment_scores).skew() if len(sentiment_scores) > 2 else 0
            },
            'original_stats': {
                'mean': np.mean(original_scores),
                'std': np.std(original_scores)
            },
            'penalty_impact': np.mean(sentiment_scores) - np.mean(original_scores) if original_scores else 0
        }
        
        # 情绪分布
        sentiment_ranges = {
            'very_bearish': sum(1 for s in sentiment_scores if s < -0.5),
            'bearish': sum(1 for s in sentiment_scores if -0.5 <= s < -0.2),
            'neutral': sum(1 for s in sentiment_scores if -0.2 <= s <= 0.2),
            'bullish': sum(1 for s in sentiment_scores if 0.2 < s <= 0.5),
            'very_bullish': sum(1 for s in sentiment_scores if s > 0.5)
        }
        
        summary['sentiment_distribution'] = sentiment_ranges
        
        # 市场情绪状态
        bullish_count = sentiment_ranges['bullish'] + sentiment_ranges['very_bullish']
        bearish_count = sentiment_ranges['bearish'] + sentiment_ranges['very_bearish']
        
        if bullish_count > bearish_count * 1.5:
            market_sentiment = 'bullish'
        elif bearish_count > bullish_count * 1.5:
            market_sentiment = 'bearish'
        else:
            market_sentiment = 'neutral'
        
        summary['market_sentiment'] = market_sentiment
        
        # 推荐股票
        sorted_stocks = sorted(
            [(r['symbol'], r['adjusted_sentiment']) for r in valid_results],
            key=lambda x: x[1],
            reverse=True
        )
        
        summary['top_bullish'] = sorted_stocks[:5]  # 最看好的5支
        summary['top_bearish'] = sorted_stocks[-5:]  # 最不看好的5支
        
        return summary


# ========== 测试函数 ==========

def test_refined_sentiment():
    """测试精细化情绪因子"""
    print("=" * 60)
    print("测试精细化情绪因子")
    print("=" * 60)
    
    # 创建情绪因子计算器
    sentiment_calculator = RefinedSentimentFactor()
    
    # 生成模拟数据
    np.random.seed(42)
    dates = pd.date_range(start='2024-01-01', end='2024-12-31', freq='B')
    
    # 模拟市场数据
    market_prices = 3000 * (1 + np.cumsum(np.random.randn(len(dates)) * 0.005))
    market_volumes = np.random.randint(1e9, 5e9, len(dates))
    
    market_data = pd.DataFrame({
        'close': market_prices,
        'volume': market_volumes
    }, index=dates)
    
    # 模拟股票数据
    stock_data = {}
    symbols = ['600519', '000001', '300750']
    
    for symbol in symbols:
        base_price = 100 + hash(symbol) % 50
        trend = np.cumsum(np.random.randn(len(dates)) * 0.01)
        noise = np.random.randn(len(dates)) * 0.02
        
        prices = base_price * (1 + trend + noise)
        volumes = np.random.randint(1e6, 1e7, len(dates))
        
        stock_data[symbol] = pd.DataFrame({
            'close': prices,
            'volume': volumes
        }, index=dates)
    
    # 1. 测试市场状态检测
    print("\n1. 测试市场状态检测...")
    try:
        market_state, state_params = sentiment_calculator.detect_market_state(
            market_data, '2024-06-30'
        )
        print(f"   市场状态: {market_state}")
        print(f"   状态参数: {state_params}")
    except Exception as e:
        print(f"   市场状态检测失败: {e}")
    
    # 2. 测试动态阈值计算
    print("\n2. 测试动态阈值计算...")
    try:
        # 创建模拟因子值
        factor_values = pd.Series(np.random.randn(100) * 0.5 + 0.2)
        
        thresholds = sentiment_calculator.calculate_dynamic_thresholds(
            factor_values, 'bull'
        )
        
        print(f"   计算动态阈值完成")
        print(f"   分位数阈值: Q10={thresholds.get('q_10', 0):.3f}, Q90={thresholds.get('q_90', 0):.3f}")
        
    except Exception as e:
        print(f"   动态阈值计算失败: {e}")
    
    # 3. 测试情绪衰减模型
    print("\n3. 测试情绪衰减模型...")
    try:
        sentiment_series = pd.Series(np.random.randn(50) * 0.3)
        
        decayed_series = sentiment_calculator.apply_emotion_decay(
            sentiment_series, half_life=5
        )
        
        print(f"   情绪衰减应用完成")
        print(f"   原始均值: {sentiment_series.mean():.3f}, 衰减后均值: {decayed_series.mean():.3f}")
        
    except Exception as e:
        print(f"   情绪衰减模型失败: {e}")
    
    # 4. 测试综合情绪计算
    print("\n4. 测试综合情绪计算...")
    try:
        results = sentiment_calculator.calculate_refined_sentiment(
            stock_data, market_data, '2024-06-30'
        )
        
        print(f"   综合情绪计算完成")
        print(f"   市场状态: {results['market_state']}")
        
        if 'summary' in results:
            summary = results['summary']
            print(f"   市场情绪: {summary.get('market_sentiment', '未知')}")
            print(f"   情绪均值: {summary.get('sentiment_stats', {}).get('mean', 0):.3f}")
        
    except Exception as e:
        print(f"   综合情绪计算失败: {e}")
    
    print("\n" + "=" * 60)
    print("精细化情绪因子测试完成")
    print("=" * 60)


if __name__ == "__main__":
    test_refined_sentiment()