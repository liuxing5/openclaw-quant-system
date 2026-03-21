#!/usr/bin/env python3
"""
因子衰减监控系统 - 技术因子半衰期(Half-life)监控
A股技术因子(如RSI、MACD)的有效性通常只有3-5天
如果调仓周期是20天，这些因子在后15天可能提供"负贡献"
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Any, Optional, Callable
from dataclasses import dataclass
from enum import Enum
import warnings
warnings.filterwarnings('ignore')
from scipy import stats, optimize

# matplotlib是可选依赖，用于绘图
try:
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    plt = None

class FactorType(Enum):
    """因子类型"""
    TECHNICAL = "技术因子"      # RSI, MACD, 均线等，衰减快(3-5天)
    FUNDAMENTAL = "基本面因子"  # PE, PB, ROE等，衰减慢(季度/年度)
    MOMENTUM = "动量因子"       # 价格动量，中等衰减(10-20天)
    VOLATILITY = "波动率因子"   # 波动率指标，衰减快(5-7天)
    VOLUME = "成交量因子"       # 量价关系，衰减快(2-4天)
    
@dataclass
class DecayModel:
    """衰减模型"""
    factor_type: FactorType
    half_life_days: float        # 半衰期（天）
    decay_rate: float            # 衰减率 λ = ln(2)/half_life
    initial_effectiveness: float # 初始有效性 (0-1)
    r_squared: float             # 模型拟合优度
    last_update: pd.Timestamp
    
@dataclass
class FactorDecayInfo:
    """因子衰减信息"""
    factor_id: str
    factor_name: str
    factor_type: FactorType
    current_effectiveness: float  # 当前有效性 (0-1)
    days_since_signal: int        # 距离信号产生天数
    half_life_estimated: float    # 估计半衰期
    half_life_confidence: float   # 半衰期置信度 (0-1)
    decay_phase: str              # 衰减阶段: 有效/衰退/失效
    recommended_weight: float     # 推荐权重
    warning_level: str            # 警告级别: 正常/注意/警告/危险
    
@dataclass
class DecayWarning:
    """衰减警告"""
    factor_id: str
    factor_name: str
    warning_type: str            # half_life_change, effectiveness_drop, phase_change
    warning_level: str           # info, warning, critical
    message: str
    current_value: float
    threshold: float
    timestamp: pd.Timestamp

class FactorDecayMonitor:
    """因子衰减监控器"""
    
    def __init__(self, 
                 default_half_lives: Optional[Dict[FactorType, float]] = None,
                 effectiveness_threshold: float = 0.3,
                 min_data_points: int = 20):
        """
        初始化因子衰减监控器
        
        Args:
            default_half_lives: 默认半衰期设置（天）
            effectiveness_threshold: 有效性阈值，低于此值认为因子失效
            min_data_points: 计算半衰期所需的最小数据点数
        """
        self.effectiveness_threshold = effectiveness_threshold
        self.min_data_points = min_data_points
        
        # 默认半衰期设置（基于A股经验）
        self.default_half_lives = default_half_lives or {
            FactorType.TECHNICAL: 4.0,     # 技术因子: 3-5天
            FactorType.FUNDAMENTAL: 63.0,  # 基本面因子: 季度数据，约63个交易日
            FactorType.MOMENTUM: 15.0,     # 动量因子: 10-20天
            FactorType.VOLATILITY: 6.0,    # 波动率因子: 5-7天
            FactorType.VOLUME: 3.0         # 成交量因子: 2-4天
        }
        
        # 因子注册表
        self.factors: Dict[str, Dict] = {}
        
        # 衰减模型
        self.decay_models: Dict[str, DecayModel] = {}
        
        # 历史有效性记录
        self.effectiveness_history: Dict[str, List[Tuple[pd.Timestamp, float]]] = {}
        
        # 警告记录
        self.warnings: List[DecayWarning] = []
        
    def register_factor(self, 
                       factor_id: str, 
                       factor_name: str, 
                       factor_type: FactorType,
                       initial_half_life: Optional[float] = None):
        """注册因子"""
        half_life = initial_half_life or self.default_half_lives.get(factor_type, 10.0)
        
        self.factors[factor_id] = {
            'factor_name': factor_name,
            'factor_type': factor_type,
            'registered_date': pd.Timestamp.now()
        }
        
        # 初始化衰减模型
        decay_model = DecayModel(
            factor_type=factor_type,
            half_life_days=half_life,
            decay_rate=np.log(2) / half_life,
            initial_effectiveness=1.0,
            r_squared=0.0,
            last_update=pd.Timestamp.now()
        )
        
        self.decay_models[factor_id] = decay_model
        self.effectiveness_history[factor_id] = []
        
        print(f"注册因子: {factor_name} ({factor_type.value})，默认半衰期: {half_life:.1f}天")
    
    def record_effectiveness(self, 
                           factor_id: str, 
                           timestamp: pd.Timestamp,
                           effectiveness: float,
                           signal_strength: Optional[float] = None):
        """
        记录因子有效性
        
        Args:
            factor_id: 因子ID
            timestamp: 时间戳
            effectiveness: 有效性 (0-1)，通常为IC值或预测准确率
            signal_strength: 信号强度（可选）
        """
        if factor_id not in self.factors:
            print(f"警告: 未注册的因子 {factor_id}")
            return
        
        # 记录有效性
        self.effectiveness_history[factor_id].append((timestamp, effectiveness))
        
        # 保持历史记录长度
        if len(self.effectiveness_history[factor_id]) > 1000:
            self.effectiveness_history[factor_id] = self.effectiveness_history[factor_id][-1000:]
        
        # 检查是否需要更新衰减模型
        if len(self.effectiveness_history[factor_id]) >= self.min_data_points:
            self._update_decay_model(factor_id)
        
        # 检查警告条件
        self._check_warnings(factor_id, timestamp, effectiveness)
    
    def _update_decay_model(self, factor_id: str):
        """更新衰减模型"""
        history = self.effectiveness_history[factor_id]
        if len(history) < self.min_data_points:
            return
        
        # 提取时间和有效性数据
        timestamps, effectiveness = zip(*history)
        
        # 转换为数值（天为单位）
        base_time = timestamps[0]
        days = [(t - base_time).total_seconds() / (24 * 3600) for t in timestamps]
        
        # 转换为numpy数组
        x = np.array(days)
        y = np.array(effectiveness)
        
        # 指数衰减模型: y = a * exp(-λ * x)
        try:
            # 使用非线性最小二乘拟合
            def decay_func(x, a, lambda_param):
                return a * np.exp(-lambda_param * x)
            
            # 初始猜测
            p0 = [1.0, 0.1]  # a=1.0, λ=0.1
            
            # 拟合
            params, covariance = optimize.curve_fit(decay_func, x, y, p0=p0, maxfev=10000)
            
            a_fit, lambda_fit = params
            
            # 计算半衰期
            half_life = np.log(2) / lambda_fit if lambda_fit > 0 else 1000.0
            
            # 计算R²
            y_pred = decay_func(x, a_fit, lambda_fit)
            ss_res = np.sum((y - y_pred) ** 2)
            ss_tot = np.sum((y - np.mean(y)) ** 2)
            r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
            
            # 更新模型
            decay_model = self.decay_models[factor_id]
            decay_model.initial_effectiveness = float(a_fit)
            decay_model.decay_rate = float(lambda_fit)
            decay_model.half_life_days = float(half_life)
            decay_model.r_squared = float(r_squared)
            decay_model.last_update = pd.Timestamp.now()
            
            print(f"更新衰减模型 {factor_id}: 半衰期={half_life:.1f}天, R²={r_squared:.3f}")
            
        except Exception as e:
            print(f"衰减模型拟合失败 {factor_id}: {e}")
    
    def _check_warnings(self, factor_id: str, timestamp: pd.Timestamp, effectiveness: float):
        """检查警告条件"""
        factor_info = self.factors[factor_id]
        decay_model = self.decay_models[factor_id]
        
        warnings_to_add = []
        
        # 1. 有效性低于阈值警告
        if effectiveness < self.effectiveness_threshold:
            warning = DecayWarning(
                factor_id=factor_id,
                factor_name=factor_info['factor_name'],
                warning_type='effectiveness_drop',
                warning_level='critical' if effectiveness < 0.1 else 'warning',
                message=f"因子有效性降至{effectiveness:.3f}，低于阈值{self.effectiveness_threshold}",
                current_value=effectiveness,
                threshold=self.effectiveness_threshold,
                timestamp=timestamp
            )
            warnings_to_add.append(warning)
        
        # 2. 半衰期显著变化警告
        history = self.effectiveness_history[factor_id]
        if len(history) >= 30:  # 有足够历史数据
            recent_half = len(history) // 2
            recent_effectiveness = [e for _, e in history[-recent_half:]]
            older_effectiveness = [e for _, e in history[:recent_half]]
            
            if len(recent_effectiveness) > 5 and len(older_effectiveness) > 5:
                recent_mean = np.mean(recent_effectiveness)
                older_mean = np.mean(older_effectiveness)
                
                if older_mean > 0 and abs(recent_mean - older_mean) / older_mean > 0.5:
                    warning = DecayWarning(
                        factor_id=factor_id,
                        factor_name=factor_info['factor_name'],
                        warning_type='half_life_change',
                        warning_level='warning',
                        message=f"因子有效性均值变化超过50%，可能半衰期已改变",
                        current_value=recent_mean,
                        threshold=older_mean * 0.5,
                        timestamp=timestamp
                    )
                    warnings_to_add.append(warning)
        
        # 3. 与默认半衰期差异过大警告
        default_hl = self.default_half_lives.get(decay_model.factor_type, 10.0)
        if default_hl > 0:
            hl_ratio = decay_model.half_life_days / default_hl
            if hl_ratio < 0.5 or hl_ratio > 2.0:
                warning = DecayWarning(
                    factor_id=factor_id,
                    factor_name=factor_info['factor_name'],
                    warning_type='abnormal_half_life',
                    warning_level='warning' if 0.3 < hl_ratio < 3.0 else 'critical',
                    message=f"估计半衰期{decay_model.half_life_days:.1f}天与默认值{default_hl:.1f}天差异显著",
                    current_value=decay_model.half_life_days,
                    threshold=default_hl,
                    timestamp=timestamp
                )
                warnings_to_add.append(warning)
        
        # 添加警告
        self.warnings.extend(warnings_to_add)
        
        # 保持警告记录长度
        if len(self.warnings) > 1000:
            self.warnings = self.warnings[-1000:]
    
    def get_factor_decay_info(self, factor_id: str, days_since_signal: int = 0) -> FactorDecayInfo:
        """
        获取因子衰减信息
        
        Args:
            factor_id: 因子ID
            days_since_signal: 距离信号产生天数
            
        Returns:
            因子衰减信息
        """
        if factor_id not in self.factors:
            raise ValueError(f"未注册的因子: {factor_id}")
        
        factor_info = self.factors[factor_id]
        decay_model = self.decay_models[factor_id]
        
        # 计算当前有效性
        current_effectiveness = decay_model.initial_effectiveness * np.exp(
            -decay_model.decay_rate * days_since_signal
        )
        current_effectiveness = max(0.0, min(1.0, current_effectiveness))
        
        # 确定衰减阶段
        if current_effectiveness > 0.7:
            decay_phase = "有效"
        elif current_effectiveness > 0.4:
            decay_phase = "衰退"
        elif current_effectiveness > 0.1:
            decay_phase = "失效"
        else:
            decay_phase = "危险"
        
        # 计算推荐权重
        if decay_phase == "有效":
            recommended_weight = 1.0
        elif decay_phase == "衰退":
            recommended_weight = 0.5
        elif decay_phase == "失效":
            recommended_weight = 0.2
        else:
            recommended_weight = 0.0
        
        # 计算半衰期置信度
        confidence = min(1.0, decay_model.r_squared * 2)  # R²转置信度
        
        # 确定警告级别
        if decay_phase == "危险" or current_effectiveness < 0.1:
            warning_level = "危险"
        elif decay_phase == "失效" or current_effectiveness < 0.3:
            warning_level = "警告"
        elif decay_phase == "衰退":
            warning_level = "注意"
        else:
            warning_level = "正常"
        
        return FactorDecayInfo(
            factor_id=factor_id,
            factor_name=factor_info['factor_name'],
            factor_type=decay_model.factor_type,
            current_effectiveness=float(current_effectiveness),
            days_since_signal=days_since_signal,
            half_life_estimated=decay_model.half_life_days,
            half_life_confidence=float(confidence),
            decay_phase=decay_phase,
            recommended_weight=float(recommended_weight),
            warning_level=warning_level
        )
    
    def adjust_factor_weight(self, factor_id: str, original_weight: float, 
                           days_since_signal: int = 0) -> float:
        """
        根据衰减调整因子权重
        
        Args:
            factor_id: 因子ID
            original_weight: 原始权重
            days_since_signal: 距离信号产生天数
            
        Returns:
            调整后的权重
        """
        decay_info = self.get_factor_decay_info(factor_id, days_since_signal)
        
        # 根据衰减阶段调整权重
        adjusted_weight = original_weight * decay_info.recommended_weight
        
        return adjusted_weight
    
    def get_decay_aware_portfolio_weights(self, 
                                        factor_weights: Dict[str, float],
                                        days_since_signals: Dict[str, int]) -> Dict[str, float]:
        """
        获取考虑衰减的投资组合权重
        
        Args:
            factor_weights: 原始因子权重
            days_since_signals: 各因子信号产生天数
            
        Returns:
            调整后的因子权重
        """
        adjusted_weights = {}
        
        for factor_id, original_weight in factor_weights.items():
            if factor_id in self.factors:
                days = days_since_signals.get(factor_id, 0)
                adjusted = self.adjust_factor_weight(factor_id, original_weight, days)
                adjusted_weights[factor_id] = adjusted
            else:
                adjusted_weights[factor_id] = original_weight
        
        # 归一化
        total = sum(adjusted_weights.values())
        if total > 0:
            adjusted_weights = {k: v/total for k, v in adjusted_weights.items()}
        
        return adjusted_weights
    
    def generate_decay_report(self) -> Dict[str, Any]:
        """生成衰减监控报告"""
        report = {
            'summary': {
                'total_factors': len(self.factors),
                'factors_by_type': {},
                'average_half_life': 0.0,
                'warning_count': len(self.warnings)
            },
            'factor_details': [],
            'recent_warnings': [],
            'recommendations': []
        }
        
        # 按类型统计
        type_counts = {}
        type_half_lives = {}
        
        for factor_id, factor_info in self.factors.items():
            factor_type = factor_info['factor_type']
            type_counts[factor_type] = type_counts.get(factor_type, 0) + 1
            
            if factor_id in self.decay_models:
                decay_model = self.decay_models[factor_id]
                if factor_type not in type_half_lives:
                    type_half_lives[factor_type] = []
                type_half_lives[factor_type].append(decay_model.half_life_days)
        
        # 计算平均半衰期
        all_half_lives = []
        for factor_type, half_lives in type_half_lives.items():
            avg_hl = np.mean(half_lives) if half_lives else 0.0
            report['summary']['factors_by_type'][factor_type.value] = {
                'count': type_counts.get(factor_type, 0),
                'avg_half_life': avg_hl,
                'default_half_life': self.default_half_lives.get(factor_type, 0.0)
            }
            all_half_lives.extend(half_lives)
        
        report['summary']['average_half_life'] = np.mean(all_half_lives) if all_half_lives else 0.0
        
        # 因子详情
        for factor_id, factor_info in self.factors.items():
            decay_info = self.get_factor_decay_info(factor_id, days_since_signal=0)
            
            factor_detail = {
                'factor_id': factor_id,
                'factor_name': factor_info['factor_name'],
                'factor_type': factor_info['factor_type'].value,
                'half_life': decay_info.half_life_estimated,
                'half_life_confidence': decay_info.half_life_confidence,
                'current_effectiveness': decay_info.current_effectiveness,
                'warning_level': decay_info.warning_level,
                'last_update': self.decay_models[factor_id].last_update.strftime('%Y-%m-%d %H:%M')
            }
            report['factor_details'].append(factor_detail)
        
        # 最近警告
        recent_warnings = sorted(self.warnings, key=lambda w: w.timestamp, reverse=True)[:10]
        for warning in recent_warnings:
            warning_info = {
                'factor': warning.factor_name,
                'type': warning.warning_type,
                'level': warning.warning_level,
                'message': warning.message,
                'timestamp': warning.timestamp.strftime('%Y-%m-%d %H:%M')
            }
            report['recent_warnings'].append(warning_info)
        
        # 生成建议
        recommendations = []
        
        # 检查半衰期过短的因子
        for factor_id, decay_model in self.decay_models.items():
            if decay_model.half_life_days < 3.0 and decay_model.factor_type != FactorType.VOLUME:
                factor_name = self.factors[factor_id]['factor_name']
                recommendations.append({
                    'type': 'short_half_life',
                    'priority': 'high',
                    'message': f"因子'{factor_name}'半衰期仅{decay_model.half_life_days:.1f}天，建议缩短调仓周期或降低权重",
                    'factor_id': factor_id
                })
        
        # 检查有效性低的因子
        for factor_id, history in self.effectiveness_history.items():
            if history:
                recent_effectiveness = [e for _, e in history[-10:]]  # 最近10次
                if len(recent_effectiveness) >= 5:
                    avg_effectiveness = np.mean(recent_effectiveness)
                    if avg_effectiveness < 0.2:
                        factor_name = self.factors[factor_id]['factor_name']
                        recommendations.append({
                            'type': 'low_effectiveness',
                            'priority': 'medium',
                            'message': f"因子'{factor_name}'近期平均有效性仅{avg_effectiveness:.3f}，建议暂停使用",
                            'factor_id': factor_id
                        })
        
        report['recommendations'] = recommendations
        
        return report
    
    def plot_factor_decay(self, factor_id: str, save_path: Optional[str] = None):
        """绘制因子衰减曲线"""
        if not HAS_MATPLOTLIB:
            print("警告: matplotlib未安装，跳过绘图功能")
            return
            
        if factor_id not in self.factors:
            print(f"未找到因子: {factor_id}")
            return
        
        history = self.effectiveness_history.get(factor_id, [])
        if len(history) < 10:
            print(f"因子{factor_id}历史数据不足")
            return
        
        # 提取数据
        timestamps, effectiveness = zip(*history)
        days = [(t - timestamps[0]).total_seconds() / (24 * 3600) for t in timestamps]
        
        # 获取衰减模型
        decay_model = self.decay_models[factor_id]
        
        # 生成拟合曲线
        x_fit = np.linspace(0, max(days), 100)
        y_fit = decay_model.initial_effectiveness * np.exp(-decay_model.decay_rate * x_fit)
        
        # 绘图
        plt.figure(figsize=(12, 6))
        
        # 原始数据
        plt.scatter(days, effectiveness, alpha=0.6, label='实际有效性', color='blue')
        
        # 拟合曲线
        plt.plot(x_fit, y_fit, 'r-', linewidth=2, label=f'衰减模型 (半衰期={decay_model.half_life_days:.1f}天)')
        
        # 阈值线
        plt.axhline(y=self.effectiveness_threshold, color='orange', linestyle='--', 
                   alpha=0.7, label=f'阈值={self.effectiveness_threshold}')
        plt.axhline(y=0.1, color='red', linestyle='--', alpha=0.5, label='危险阈值')
        
        # 标注半衰期点
        half_life_point = decay_model.half_life_days
        plt.axvline(x=half_life_point, color='green', linestyle=':', alpha=0.7, label=f'半衰期点')
        
        plt.xlabel('时间 (天)')
        plt.ylabel('因子有效性')
        plt.title(f"因子衰减曲线 - {self.factors[factor_id]['factor_name']}")
        plt.legend()
        plt.grid(True, alpha=0.3)
        
        # 添加文本说明
        text_str = f"半衰期: {decay_model.half_life_days:.1f}天\n"
        text_str += f"衰减率: {decay_model.decay_rate:.4f}\n"
        text_str += f"初始有效性: {decay_model.initial_effectiveness:.3f}\n"
        text_str += f"模型R²: {decay_model.r_squared:.3f}"
        
        plt.text(0.02, 0.98, text_str, transform=plt.gca().transAxes,
                verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
        
        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"图表已保存至: {save_path}")
        
        plt.tight_layout()
        plt.show()

# ========== 示例使用 ==========

def example_usage():
    """示例使用方法"""
    print("因子衰减监控系统示例")
    print("=" * 60)
    
    # 创建监控器
    monitor = FactorDecayMonitor(
        effectiveness_threshold=0.3,
        min_data_points=10
    )
    
    # 注册因子
    monitor.register_factor("rsi_14", "RSI相对强弱指标", FactorType.TECHNICAL, initial_half_life=4.0)
    monitor.register_factor("macd", "MACD指标", FactorType.TECHNICAL, initial_half_life=5.0)
    monitor.register_factor("pe_ratio", "市盈率", FactorType.FUNDAMENTAL, initial_half_life=63.0)
    monitor.register_factor("volume_ratio", "量比指标", FactorType.VOLUME, initial_half_life=3.0)
    
    print("\n模拟记录因子有效性数据...")
    
    # 模拟记录数据（实际应从历史数据计算）
    base_date = pd.Timestamp('2024-01-01')
    np.random.seed(42)
    
    # RSI因子 - 快速衰减
    for i in range(50):
        date = base_date + pd.Timedelta(days=i)
        # 模拟指数衰减
        effectiveness = 0.9 * np.exp(-0.173 * i) + np.random.randn() * 0.05  # 半衰期4天
        effectiveness = max(0.0, min(1.0, effectiveness))
        monitor.record_effectiveness("rsi_14", date, effectiveness)
    
    # MACD因子 - 中等衰减
    for i in range(50):
        date = base_date + pd.Timedelta(days=i)
        effectiveness = 0.85 * np.exp(-0.139 * i) + np.random.randn() * 0.06  # 半衰期5天
        effectiveness = max(0.0, min(1.0, effectiveness))
        monitor.record_effectiveness("macd", date, effectiveness)
    
    # PE因子 - 慢速衰减
    for i in range(50):
        date = base_date + pd.Timedelta(days=i)
        effectiveness = 0.95 * np.exp(-0.011 * i) + np.random.randn() * 0.03  # 半衰期63天
        effectiveness = max(0.0, min(1.0, effectiveness))
        monitor.record_effectiveness("pe_ratio", date, effectiveness)
    
    print("\n获取因子衰减信息:")
    
    # 获取不同天数后的衰减信息
    for days in [0, 3, 7, 14, 30]:
        print(f"\n信号产生后 {days} 天:")
        for factor_id in ["rsi_14", "macd", "pe_ratio"]:
            try:
                info = monitor.get_factor_decay_info(factor_id, days_since_signal=days)
                print(f"  {info.factor_name}: 有效性={info.current_effectiveness:.3f}, "
                      f"阶段={info.decay_phase}, 推荐权重={info.recommended_weight:.2f}, "
                      f"警告={info.warning_level}")
            except Exception as e:
                print(f"  {factor_id}: 错误 - {e}")
    
    print("\n权重调整示例:")
    original_weights = {
        "rsi_14": 0.3,
        "macd": 0.3,
        "pe_ratio": 0.4
    }
    
    days_since_signals = {
        "rsi_14": 7,   # RSI信号已产生7天
        "macd": 3,     # MACD信号已产生3天
        "pe_ratio": 1  # PE信号刚产生1天
    }
    
    adjusted_weights = monitor.get_decay_aware_portfolio_weights(
        original_weights, days_since_signals
    )
    
    print("原始权重:")
    for factor_id, weight in original_weights.items():
        print(f"  {factor_id}: {weight:.3f}")
    
    print("\n考虑衰减后的权重:")
    for factor_id, weight in adjusted_weights.items():
        info = monitor.get_factor_decay_info(factor_id, days_since_signals.get(factor_id, 0))
        print(f"  {factor_id}: {weight:.3f} (有效性={info.current_effectiveness:.3f}, 阶段={info.decay_phase})")
    
    print("\n生成衰减报告...")
    report = monitor.generate_decay_report()
    
    print(f"\n报告摘要:")
    print(f"  总因子数: {report['summary']['total_factors']}")
    print(f"  平均半衰期: {report['summary']['average_half_life']:.1f}天")
    print(f"  警告数量: {report['summary']['warning_count']}")
    
    print("\n因子详情:")
    for detail in report['factor_details'][:3]:  # 显示前3个
        print(f"  {detail['factor_name']}: 半衰期={detail['half_life']:.1f}天, "
              f"置信度={detail['half_life_confidence']:.3f}, 警告级别={detail['warning_level']}")
    
    if report['recommendations']:
        print("\n建议:")
        for rec in report['recommendations'][:3]:  # 显示前3个建议
            print(f"  [{rec['priority'].upper()}] {rec['message']}")

if __name__ == "__main__":
    example_usage()