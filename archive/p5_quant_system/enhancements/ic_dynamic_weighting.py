#!/usr/bin/env python3
"""
IC动态加权引擎 - 技术派与基本面派自动切换
核心算法：根据因子IC(信息系数)动态调整权重
ICIR越高的因子，在下一阶段分配越高权重
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass
from enum import Enum
import warnings
warnings.filterwarnings('ignore')

class FactorCategory(Enum):
    """因子分类"""
    TECHNICAL = "技术派"      # RSI, MACD, 均线等技术指标
    FUNDAMENTAL = "基本面派"  # PE, PB, ROE等财务指标
    SENTIMENT = "情绪派"      # 新闻情绪、资金流向
    MARKET = "市场派"        # 大盘环境、行业轮动
    
@dataclass
class FactorInfo:
    """因子信息"""
    factor_id: str
    name: str
    category: FactorCategory
    description: str
    calculation_window: int  # 计算窗口（天）
    ic_lookback: int = 20   # IC计算回顾期，默认20天
    
@dataclass
class ICResult:
    """IC计算结果"""
    factor_id: str
    date: pd.Timestamp
    ic_value: float          # 信息系数 (-1到1)
    ic_mean: float           # 过去N天IC均值
    ic_std: float            # 过去N天IC标准差
    icir: float              # 信息比率 = IC均值/IC标准差
    rank: int                # ICIR排名
    weight: float            # 动态权重 (0-1)
    
@dataclass
class WeightAllocation:
    """权重分配结果"""
    date: pd.Timestamp
    category_weights: Dict[FactorCategory, float]  # 分类权重
    factor_weights: Dict[str, float]               # 因子具体权重
    dominant_category: FactorCategory              # 主导分类
    switching_signal: bool                         # 是否发生切换
    
class ICDynamicWeightingEngine:
    """IC动态加权引擎"""
    
    def __init__(self, 
                 ic_lookback_days: int = 20,
                 min_icir_for_weight: float = 0.1,
                 weight_smoothing: float = 0.3):
        """
        初始化IC动态加权引擎
        
        Args:
            ic_lookback_days: IC计算回顾天数
            min_icir_for_weight: 分配权重的最小ICIR阈值
            weight_smoothing: 权重平滑系数 (0-1)，防止剧烈波动
        """
        self.ic_lookback_days = ic_lookback_days
        self.min_icir_for_weight = min_icir_for_weight
        self.weight_smoothing = weight_smoothing
        
        # 因子注册表
        self.factors: Dict[str, FactorInfo] = {}
        
        # 历史IC记录
        self.ic_history: Dict[str, List[ICResult]] = {}
        
        # 当前权重分配
        self.current_weights: Optional[WeightAllocation] = None
        
    def register_factor(self, factor_id: str, name: str, category: FactorCategory,
                       description: str = "", calculation_window: int = 20):
        """注册因子"""
        factor = FactorInfo(
            factor_id=factor_id,
            name=name,
            category=category,
            description=description,
            calculation_window=calculation_window
        )
        self.factors[factor_id] = factor
        self.ic_history[factor_id] = []
        
    def calculate_ic(self, factor_values: pd.Series, future_returns: pd.Series) -> float:
        """
        计算单个因子的IC值
        
        Args:
            factor_values: 因子值序列
            future_returns: 未来收益率序列
            
        Returns:
            IC值（相关系数）
        """
        # 对齐数据
        aligned_idx = factor_values.index.intersection(future_returns.index)
        if len(aligned_idx) < 5:  # 最少需要5个数据点
            return 0.0
            
        fv = factor_values.loc[aligned_idx]
        fr = future_returns.loc[aligned_idx]
        
        # 计算相关系数（IC）
        try:
            ic = fv.corr(fr)
            # 处理NaN和异常值
            if pd.isna(ic) or abs(ic) > 1.0:
                return 0.0
            return float(ic)
        except Exception:
            return 0.0
    
    def calculate_icir(self, ic_values: List[float]) -> Tuple[float, float, float]:
        """
        计算ICIR（信息比率）
        
        Args:
            ic_values: IC值列表
            
        Returns:
            (ic_mean, ic_std, icir)
        """
        if not ic_values or len(ic_values) < 2:
            return 0.0, 0.0, 0.0
            
        ic_array = np.array(ic_values)
        ic_mean = np.mean(ic_array)
        ic_std = np.std(ic_array)
        
        # 防止除零
        if ic_std < 1e-10:
            icir = 0.0
        else:
            icir = ic_mean / ic_std
            
        return float(ic_mean), float(ic_std), float(icir)
    
    def update_ic_for_factor(self, factor_id: str, current_date: pd.Timestamp,
                            factor_value: float, future_return: float) -> Optional[ICResult]:
        """
        更新单个因子的IC计算
        
        Args:
            factor_id: 因子ID
            current_date: 当前日期
            factor_value: 当前因子值
            future_return: 未来收益率
            
        Returns:
            IC计算结果
        """
        if factor_id not in self.factors:
            return None
            
        # 获取历史IC记录
        history = self.ic_history.get(factor_id, [])
        
        # 创建临时Series用于计算
        # 实际实现中应该有完整的历史数据，这里简化处理
        if len(history) >= 2:
            # 模拟计算：使用最近N天的数据
            recent_ics = [h.ic_value for h in history[-self.ic_lookback_days:]]
            recent_ics.append(0.0)  # 临时占位，实际应从数据计算
            
            ic_mean, ic_std, icir = self.calculate_icir(recent_ics)
        else:
            ic_mean, ic_std, icir = 0.0, 0.0, 0.0
            
        # 创建IC结果
        ic_result = ICResult(
            factor_id=factor_id,
            date=current_date,
            ic_value=0.0,  # 实际应从完整数据计算
            ic_mean=ic_mean,
            ic_std=ic_std,
            icir=icir,
            rank=0,
            weight=0.0
        )
        
        # 保存历史
        self.ic_history[factor_id].append(ic_result)
        
        return ic_result
    
    def calculate_dynamic_weights(self, current_date: pd.Timestamp) -> WeightAllocation:
        """
        计算动态权重分配
        
        Args:
            current_date: 当前日期
            
        Returns:
            权重分配结果
        """
        # 收集所有因子的最新ICIR
        factor_icirs = {}
        for factor_id in self.factors:
            history = self.ic_history.get(factor_id, [])
            if history:
                latest_ic = history[-1]
                factor_icirs[factor_id] = latest_ic.icir
            else:
                factor_icirs[factor_id] = 0.0
        
        # 按ICIR排序
        sorted_factors = sorted(factor_icirs.items(), key=lambda x: abs(x[1]), reverse=True)
        
        # 计算权重（基于ICIR绝对值）
        total_icir_abs = sum(abs(icir) for _, icir in sorted_factors if abs(icir) > self.min_icir_for_weight)
        
        factor_weights = {}
        for factor_id, icir in sorted_factors:
            if abs(icir) > self.min_icir_for_weight and total_icir_abs > 0:
                weight = abs(icir) / total_icir_abs
            else:
                weight = 0.0
            factor_weights[factor_id] = weight
        
        # 计算分类权重
        category_weights = {cat: 0.0 for cat in FactorCategory}
        for factor_id, weight in factor_weights.items():
            factor = self.factors[factor_id]
            category_weights[factor.category] += weight
        
        # 确定主导分类
        dominant_category = max(category_weights.items(), key=lambda x: x[1])[0]
        
        # 检查是否发生分类切换
        switching_signal = False
        if self.current_weights:
            prev_dominant = self.current_weights.dominant_category
            switching_signal = (prev_dominant != dominant_category)
            
            # 应用权重平滑
            if self.weight_smoothing > 0:
                for factor_id in factor_weights:
                    prev_weight = self.current_weights.factor_weights.get(factor_id, 0.0)
                    new_weight = factor_weights[factor_id]
                    smoothed_weight = (1 - self.weight_smoothing) * prev_weight + self.weight_smoothing * new_weight
                    factor_weights[factor_id] = smoothed_weight
                
                # 重新归一化
                total_weight = sum(factor_weights.values())
                if total_weight > 0:
                    factor_weights = {k: v/total_weight for k, v in factor_weights.items()}
                    
                # 重新计算分类权重
                category_weights = {cat: 0.0 for cat in FactorCategory}
                for factor_id, weight in factor_weights.items():
                    factor = self.factors[factor_id]
                    category_weights[factor.category] += weight
                
                dominant_category = max(category_weights.items(), key=lambda x: x[1])[0]
        
        # 更新IC结果的排名和权重
        for i, (factor_id, icir) in enumerate(sorted_factors):
            if factor_id in self.ic_history and self.ic_history[factor_id]:
                latest_ic = self.ic_history[factor_id][-1]
                latest_ic.rank = i + 1
                latest_ic.weight = factor_weights.get(factor_id, 0.0)
        
        # 创建权重分配结果
        weight_allocation = WeightAllocation(
            date=current_date,
            category_weights=category_weights,
            factor_weights=factor_weights,
            dominant_category=dominant_category,
            switching_signal=switching_signal
        )
        
        self.current_weights = weight_allocation
        
        return weight_allocation
    
    def get_factor_recommendations(self, top_n: int = 10) -> List[Dict[str, Any]]:
        """
        获取因子推荐（基于ICIR排名）
        
        Args:
            top_n: 返回前N个因子
            
        Returns:
            因子推荐列表
        """
        recommendations = []
        
        # 收集所有因子的ICIR
        factor_scores = []
        for factor_id in self.factors:
            history = self.ic_history.get(factor_id, [])
            if history:
                latest_ic = history[-1]
                factor_scores.append((factor_id, latest_ic.icir, latest_ic.weight))
        
        # 按ICIR排序
        factor_scores.sort(key=lambda x: abs(x[1]), reverse=True)
        
        for i, (factor_id, icir, weight) in enumerate(factor_scores[:top_n]):
            factor = self.factors[factor_id]
            recommendation = {
                'rank': i + 1,
                'factor_id': factor_id,
                'name': factor.name,
                'category': factor.category.value,
                'icir': icir,
                'weight': weight,
                'recommendation': self._get_recommendation_text(icir, weight)
            }
            recommendations.append(recommendation)
        
        return recommendations
    
    def _get_recommendation_text(self, icir: float, weight: float) -> str:
        """生成推荐文本"""
        if icir > 0.5:
            return "强烈推荐"
        elif icir > 0.2:
            return "推荐"
        elif icir > 0.0:
            return "中性"
        elif icir > -0.2:
            return "谨慎"
        else:
            return "回避"
    
    def generate_ic_report(self) -> Dict[str, Any]:
        """生成IC分析报告"""
        report = {
            'summary': {
                'total_factors': len(self.factors),
                'categories': {},
                'overall_icir': 0.0,
                'switching_count': 0
            },
            'category_performance': {},
            'top_factors': [],
            'weight_evolution': {}
        }
        
        # 计算分类表现
        for category in FactorCategory:
            category_factors = [f for f in self.factors.values() if f.category == category]
            category_icirs = []
            
            for factor in category_factors:
                history = self.ic_history.get(factor.factor_id, [])
                if history:
                    latest_ic = history[-1]
                    category_icirs.append(latest_ic.icir)
            
            if category_icirs:
                avg_icir = np.mean(category_icirs)
            else:
                avg_icir = 0.0
                
            report['category_performance'][category.value] = {
                'factor_count': len(category_factors),
                'avg_icir': avg_icir,
                'dominance': self.current_weights.category_weights.get(category, 0.0) if self.current_weights else 0.0
            }
        
        # 获取顶级因子
        report['top_factors'] = self.get_factor_recommendations(5)
        
        # 总体ICIR
        all_icirs = []
        for factor_id in self.factors:
            history = self.ic_history.get(factor_id, [])
            if history:
                all_icirs.append(history[-1].icir)
        
        if all_icirs:
            report['summary']['overall_icir'] = np.mean(all_icirs)
        
        return report

# ========== 示例使用 ==========

def example_usage():
    """示例使用方法"""
    
    # 创建IC动态加权引擎
    engine = ICDynamicWeightingEngine(
        ic_lookback_days=20,
        min_icir_for_weight=0.1,
        weight_smoothing=0.3
    )
    
    # 注册技术派因子
    engine.register_factor(
        factor_id="rsi_14",
        name="RSI相对强弱指标",
        category=FactorCategory.TECHNICAL,
        description="14日相对强弱指数",
        calculation_window=14
    )
    
    engine.register_factor(
        factor_id="macd",
        name="MACD指标",
        category=FactorCategory.TECHNICAL,
        description="指数平滑异同移动平均线",
        calculation_window=26
    )
    
    # 注册基本面因子
    engine.register_factor(
        factor_id="pe_ratio",
        name="市盈率",
        category=FactorCategory.FUNDAMENTAL,
        description="股价与每股收益比率",
        calculation_window=252  # 年频
    )
    
    engine.register_factor(
        factor_id="roe",
        name="净资产收益率",
        category=FactorCategory.FUNDAMENTAL,
        description="净利润与净资产比率",
        calculation_window=252  # 年频
    )
    
    # 模拟更新IC（实际应从数据计算）
    current_date = pd.Timestamp('2024-01-15')
    
    # 更新因子IC（简化示例）
    engine.update_ic_for_factor("rsi_14", current_date, 0.65, 0.02)   # RSI=65，未来收益2%
    engine.update_ic_for_factor("macd", current_date, 0.12, 0.015)    # MACD=0.12，未来收益1.5%
    engine.update_ic_for_factor("pe_ratio", current_date, 15.3, 0.01) # PE=15.3，未来收益1%
    engine.update_ic_for_factor("roe", current_date, 0.18, 0.025)     # ROE=18%，未来收益2.5%
    
    # 计算动态权重
    weights = engine.calculate_dynamic_weights(current_date)
    
    print("IC动态加权引擎示例")
    print("=" * 60)
    print(f"当前日期: {current_date}")
    print(f"主导分类: {weights.dominant_category.value}")
    print(f"是否切换: {'是' if weights.switching_signal else '否'}")
    print()
    
    print("分类权重:")
    for category, weight in weights.category_weights.items():
        print(f"  {category.value}: {weight:.3f}")
    print()
    
    print("因子权重:")
    for factor_id, weight in weights.factor_weights.items():
        factor = engine.factors[factor_id]
        print(f"  {factor.name} ({factor.category.value}): {weight:.3f}")
    print()
    
    # 获取因子推荐
    recommendations = engine.get_factor_recommendations(3)
    print("Top 3因子推荐:")
    for rec in recommendations:
        print(f"  {rec['rank']}. {rec['name']} - ICIR: {rec['icir']:.3f}, 权重: {rec['weight']:.3f}")
    
    # 生成报告
    report = engine.generate_ic_report()
    print(f"\n总体ICIR: {report['summary']['overall_icir']:.3f}")

if __name__ == "__main__":
    example_usage()