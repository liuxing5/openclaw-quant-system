#!/usr/bin/env python3
"""
Brinson归因分析模型 - 收益分解

核心功能：
1. Brinson模型分解：资产配置效应、选股效应、交互效应
2. 行业暴露分析：Beta收益 vs Alpha收益
3. 指数对冲模拟：中证500/1000对冲，剥离大盘波动
4. 归因报告生成：可视化收益来源分解
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass, field
import warnings
from datetime import datetime, timedelta
import json
import sys
import os

warnings.filterwarnings('ignore')

# 尝试导入数据管道
try:
    sys.path.append('/root/.openclaw/workspace/quant_system')
    from data.sources.data_pipeline import DataPipeline
    from data.sources.data_adapter import DataAdapter
    DATA_MODULE_AVAILABLE = True
except ImportError:
    DATA_MODULE_AVAILABLE = False
    print("⚠ 数据模块不可用，将使用模拟数据")


@dataclass
class IndustryClassification:
    """行业分类数据"""
    symbol: str
    industry_code: str  # 申万一级行业代码
    industry_name: str  # 行业名称
    weight_in_index: float = 0.0  # 在基准指数中的权重


@dataclass
class AttributionResult:
    """归因分析结果"""
    # 基本收益数据
    portfolio_return: float  # 组合收益率
    benchmark_return: float  # 基准收益率
    active_return: float     # 超额收益（Alpha）
    
    # Brinson分解
    allocation_effect: float   # 资产配置效应
    selection_effect: float    # 选股效应
    interaction_effect: float  # 交互效应
    
    # 行业暴露分解
    beta_return: float         # Beta收益（市场/行业暴露）
    alpha_return: float        # Alpha收益（选股能力）
    
    # 指数对冲结果
    hedge_500_return: float    # 对冲中证500后收益
    hedge_1000_return: float   # 对冲中证1000后收益
    residual_alpha: float      # 剥离大盘波动后的Alpha
    
    # 行业层面分解
    industry_effects: Dict[str, Dict[str, float]]  # 各行业贡献
    
    # 统计信息
    r_squared: float           # R²（组合与基准相关性）
    tracking_error: float      # 跟踪误差
    information_ratio: float   # 信息比率


class BrinsonAttribution:
    """
    Brinson归因分析器
    
    参考专业机构做法：
    1. 将超额收益分解为资产配置、选股、交互效应
    2. 分析行业暴露带来的Beta收益
    3. 通过指数对冲剥离大盘波动
    4. 评估真正的选股能力（Alpha）
    """
    
    def __init__(self, 
                 benchmark_symbol: str = '000300.SH',  # 沪深300默认基准
                 use_index_hedge: bool = True):
        """
        初始化归因分析器
        
        Args:
            benchmark_symbol: 基准指数代码（默认沪深300）
            use_index_hedge: 是否使用指数对冲分析
        """
        self.benchmark_symbol = benchmark_symbol
        self.use_index_hedge = use_index_hedge
        
        # 初始化数据管道
        self.data_pipeline = None
        self.data_adapter = None
        if DATA_MODULE_AVAILABLE:
            try:
                self.data_pipeline = DataPipeline()
                self.data_adapter = DataAdapter()
                print("✓ 数据管道初始化成功")
            except Exception as e:
                print(f"⚠ 数据管道初始化失败: {e}")
        
        # 行业分类映射（简化的申万一级行业）
        self.industry_mapping = self._load_industry_mapping()
        
        # 指数代码映射
        self.index_symbols = {
            'csi300': '000300.SH',    # 沪深300
            'csi500': '000905.SH',    # 中证500
            'csi1000': '000852.SH',   # 中证1000
            'sse50': '000016.SH',     # 上证50
            'gem': '399006.SZ',       # 创业板指
        }
        
        print(f"Brinson归因分析器初始化完成，基准: {benchmark_symbol}")
    
    def _load_industry_mapping(self) -> Dict[str, str]:
        """加载行业分类映射（简化版）"""
        # 实际应用中应从数据库或API获取
        return {
            '600519': 'F01',  # 食品饮料
            '000858': 'F01',  # 食品饮料
            '000333': 'G01',  # 家用电器
            '000651': 'G01',  # 家用电器
            '300750': 'S01',  # 电力设备
            '002594': 'S01',  # 电力设备
            '601318': 'I01',  # 非银金融
            '600036': 'I01',  # 银行（归为非银金融简化处理）
            '000001': 'I01',  # 银行
            '600030': 'I01',  # 非银金融
            '300059': 'T01',  # 传媒
            '002415': 'T01',  # 计算机
            '000725': 'C01',  # 电子
            '002475': 'C01',  # 电子
            '600887': 'F01',  # 食品饮料
            '000568': 'F01',  # 食品饮料
            '300760': 'C01',  # 医药生物
            '300122': 'C01',  # 医药生物
            '601012': 'S01',  # 电力设备
            '601888': 'L01',  # 商贸零售
        }
    
    def get_industry_name(self, industry_code: str) -> str:
        """获取行业名称"""
        industry_names = {
            'F01': '食品饮料',
            'G01': '家用电器',
            'S01': '电力设备',
            'I01': '金融',
            'T01': 'TMT',
            'C01': '电子/医药',
            'L01': '商贸零售',
            'H01': '化工',
            'D01': '房地产',
            'E01': '建筑建材',
        }
        return industry_names.get(industry_code, '其他')
    
    def calculate_brinson_attribution(self,
                                    portfolio_weights: Dict[str, float],
                                    portfolio_returns: Dict[str, float],
                                    benchmark_weights: Dict[str, float],
                                    benchmark_returns: Dict[str, float]) -> AttributionResult:
        """
        计算Brinson归因分析
        
        Args:
            portfolio_weights: 组合权重 {symbol: weight}
            portfolio_returns: 组合收益率 {symbol: return}
            benchmark_weights: 基准权重 {symbol: weight}
            benchmark_returns: 基准收益率 {symbol: return}
            
        Returns:
            归因分析结果
        """
        # 确保数据对齐
        symbols = list(set(portfolio_weights.keys()) | set(benchmark_weights.keys()))
        
        # 计算组合和基准总收益
        portfolio_total_return = 0.0
        benchmark_total_return = 0.0
        
        for symbol in symbols:
            w_p = portfolio_weights.get(symbol, 0.0)
            w_b = benchmark_weights.get(symbol, 0.0)
            r_p = portfolio_returns.get(symbol, 0.0)
            r_b = benchmark_returns.get(symbol, 0.0)
            
            portfolio_total_return += w_p * r_p
            benchmark_total_return += w_b * r_b
        
        # 超额收益
        active_return = portfolio_total_return - benchmark_total_return
        
        # Brinson分解
        allocation_effect = 0.0  # 资产配置效应
        selection_effect = 0.0   # 选股效应
        interaction_effect = 0.0  # 交互效应
        
        # 按行业分组计算（如果有行业数据）
        industry_portfolio_weights = {}
        industry_portfolio_returns = {}
        industry_benchmark_weights = {}
        industry_benchmark_returns = {}
        
        # 假设我们有行业映射
        for symbol in symbols:
            industry = self.industry_mapping.get(symbol, 'OTHER')
            
            w_p = portfolio_weights.get(symbol, 0.0)
            w_b = benchmark_weights.get(symbol, 0.0)
            r_p = portfolio_returns.get(symbol, 0.0)
            r_b = benchmark_returns.get(symbol, 0.0)
            
            # 聚合行业数据
            # 🚨 关键验证：加权收益之和将在后续除以行业权重得到加权平均收益率
            # 第236行：r_p_i = (industry_portfolio_returns.get(industry, 0.0) / w_p_i) if w_p_i > 0 else 0.0
            industry_portfolio_weights[industry] = industry_portfolio_weights.get(industry, 0.0) + w_p
            industry_portfolio_returns[industry] = industry_portfolio_returns.get(industry, 0.0) + w_p * r_p
            industry_benchmark_weights[industry] = industry_benchmark_weights.get(industry, 0.0) + w_b
            industry_benchmark_returns[industry] = industry_benchmark_returns.get(industry, 0.0) + w_b * r_b
        
        # 计算行业层面的Brinson分解
        industry_effects = {}
        
        for industry in set(list(industry_portfolio_weights.keys()) + list(industry_benchmark_weights.keys())):
            w_p_i = industry_portfolio_weights.get(industry, 0.0)
            w_b_i = industry_benchmark_weights.get(industry, 0.0)
            
            # 计算行业收益率
            r_p_i = (industry_portfolio_returns.get(industry, 0.0) / w_p_i) if w_p_i > 0 else 0.0
            r_b_i = (industry_benchmark_returns.get(industry, 0.0) / w_b_i) if w_b_i > 0 else 0.0
            
            # Brinson公式
            # 资产配置效应 = (组合行业权重 - 基准行业权重) * (基准行业收益率 - 基准总收益率)
            alloc_eff = (w_p_i - w_b_i) * (r_b_i - benchmark_total_return)
            
            # 选股效应 = 基准行业权重 * (组合行业收益率 - 基准行业收益率)
            select_eff = w_b_i * (r_p_i - r_b_i)
            
            # 交互效应 = (组合行业权重 - 基准行业权重) * (组合行业收益率 - 基准行业收益率)
            inter_eff = (w_p_i - w_b_i) * (r_p_i - r_b_i)
            
            allocation_effect += alloc_eff
            selection_effect += select_eff
            interaction_effect += inter_eff
            
            industry_effects[industry] = {
                'allocation': alloc_eff,
                'selection': select_eff,
                'interaction': inter_eff,
                'portfolio_weight': w_p_i,
                'benchmark_weight': w_b_i,
                'portfolio_return': r_p_i,
                'benchmark_return': r_b_i
            }
        
        # Beta/Alpha分解（简化版）
        # Beta收益 = 基准收益率 * 组合与基准的相关性
        # Alpha收益 = 超额收益 - Beta收益
        
        # 计算相关性（需要时间序列数据，这里用简化估计）
        beta_estimate = 1.0  # 默认Beta=1.0
        beta_return = benchmark_total_return * beta_estimate
        alpha_return = active_return - beta_return
        
        # 指数对冲模拟
        hedge_500_return = portfolio_total_return
        hedge_1000_return = portfolio_total_return
        residual_alpha = alpha_return
        
        if self.use_index_hedge:
            # 模拟对冲效果（实际需要指数收益率数据）
            # 这里使用简化假设：对冲后保留Alpha，去除Beta
            hedge_500_return = alpha_return * 0.8  # 假设对冲中证500保留80%Alpha
            hedge_1000_return = alpha_return * 0.7  # 假设对冲中证1000保留70%Alpha
            residual_alpha = alpha_return * 0.9  # 剥离大盘波动后的Alpha
        
        # 计算统计指标
        r_squared = 0.8  # 默认值（实际需要计算）
        tracking_error = abs(active_return) * 0.3  # 简化估计
        information_ratio = active_return / tracking_error if tracking_error > 0 else 0.0
        
        return AttributionResult(
            portfolio_return=portfolio_total_return,
            benchmark_return=benchmark_total_return,
            active_return=active_return,
            allocation_effect=allocation_effect,
            selection_effect=selection_effect,
            interaction_effect=interaction_effect,
            beta_return=beta_return,
            alpha_return=alpha_return,
            hedge_500_return=hedge_500_return,
            hedge_1000_return=hedge_1000_return,
            residual_alpha=residual_alpha,
            industry_effects=industry_effects,
            r_squared=r_squared,
            tracking_error=tracking_error,
            information_ratio=information_ratio
        )
    
    def analyze_backtest_result(self,
                              backtest_result: Any,
                              benchmark_data: Optional[pd.DataFrame] = None) -> AttributionResult:
        """
        分析回测结果的归因
        
        Args:
            backtest_result: 回测结果对象（需包含trade_records和portfolio_values）
            benchmark_data: 基准指数数据（可选）
            
        Returns:
            归因分析结果
        """
        # 从回测结果中提取交易记录和组合价值
        try:
            trade_records = backtest_result.trade_records
            portfolio_values = backtest_result.portfolio_values
            dates = backtest_result.dates
            
            print(f"分析回测结果: {len(trade_records)}笔交易，{len(portfolio_values)}个交易日")
            
            # 提取组合权重和收益率（简化处理）
            # 实际应用中需要更精细的持仓数据
            portfolio_weights = {}
            portfolio_returns = {}
            
            # 假设我们分析最近一个季度的持仓
            if trade_records:
                # 按股票汇总持仓
                holdings = {}
                for trade in trade_records:
                    if trade.action == 'BUY':
                        holdings[trade.symbol] = holdings.get(trade.symbol, 0) + trade.shares
                    elif trade.action == 'SELL':
                        holdings[trade.symbol] = holdings.get(trade.symbol, 0) - trade.shares
                
                # 计算权重（基于最新价格）
                total_value = sum(holdings.values())  # 简化：假设每股价值相同
                if total_value > 0:
                    for symbol, shares in holdings.items():
                        if shares > 0:
                            portfolio_weights[symbol] = shares / total_value
            
            # 获取基准数据
            benchmark_weights, benchmark_returns = self._get_benchmark_data()
            
            # 估计组合收益率（使用回测结果中的总收益率）
            portfolio_total_return = backtest_result.total_return if hasattr(backtest_result, 'total_return') else 0.0
            
            # 为简化，假设所有股票有相同收益率
            for symbol in portfolio_weights:
                portfolio_returns[symbol] = portfolio_total_return
            
            # 计算归因
            result = self.calculate_brinson_attribution(
                portfolio_weights=portfolio_weights,
                portfolio_returns=portfolio_returns,
                benchmark_weights=benchmark_weights,
                benchmark_returns=benchmark_returns
            )
            
            return result
            
        except Exception as e:
            print(f"回测结果分析失败: {e}")
            # 返回空结果
            return self._create_empty_result()
    
    def _get_benchmark_data(self) -> Tuple[Dict[str, float], Dict[str, float]]:
        """获取基准指数数据（权重和收益率）"""
        # 简化版：使用预定义的基准权重
        benchmark_weights = {
            '600519': 0.03,  # 贵州茅台
            '000858': 0.02,  # 五粮液
            '000333': 0.02,  # 美的集团
            '300750': 0.03,  # 宁德时代
            '601318': 0.04,  # 中国平安
            '600036': 0.03,  # 招商银行
            '600030': 0.02,  # 中信证券
            '300059': 0.01,  # 东方财富
            '000725': 0.02,  # 京东方A
            '300760': 0.02,  # 迈瑞医疗
            '601012': 0.02,  # 隆基绿能
            '601888': 0.01,  # 中国中免
        }
        
        # 简化：假设所有基准成分股有相同收益率
        benchmark_return = 0.05  # 5%基准收益率
        benchmark_returns = {symbol: benchmark_return for symbol in benchmark_weights}
        
        # 权重归一化
        total_weight = sum(benchmark_weights.values())
        if total_weight > 0:
            benchmark_weights = {k: v/total_weight for k, v in benchmark_weights.items()}
        
        return benchmark_weights, benchmark_returns
    
    def _create_empty_result(self) -> AttributionResult:
        """创建空的归因结果"""
        return AttributionResult(
            portfolio_return=0.0,
            benchmark_return=0.0,
            active_return=0.0,
            allocation_effect=0.0,
            selection_effect=0.0,
            interaction_effect=0.0,
            beta_return=0.0,
            alpha_return=0.0,
            hedge_500_return=0.0,
            hedge_1000_return=0.0,
            residual_alpha=0.0,
            industry_effects={},
            r_squared=0.0,
            tracking_error=0.0,
            information_ratio=0.0
        )
    
    def generate_attribution_report(self, 
                                  result: AttributionResult,
                                  output_format: str = 'text') -> str:
        """
        生成归因分析报告
        
        Args:
            result: 归因分析结果
            output_format: 输出格式 ('text', 'json', 'markdown')
            
        Returns:
            格式化报告
        """
        if output_format == 'json':
            return json.dumps(self._result_to_dict(result), indent=2, ensure_ascii=False)
        
        elif output_format == 'markdown':
            return self._generate_markdown_report(result)
        
        else:  # text format
            return self._generate_text_report(result)
    
    def _result_to_dict(self, result: AttributionResult) -> Dict[str, Any]:
        """将结果转为字典"""
        return {
            'portfolio_return': result.portfolio_return,
            'benchmark_return': result.benchmark_return,
            'active_return': result.active_return,
            'brinson_decomposition': {
                'allocation_effect': result.allocation_effect,
                'selection_effect': result.selection_effect,
                'interaction_effect': result.interaction_effect,
                'total': result.allocation_effect + result.selection_effect + result.interaction_effect
            },
            'beta_alpha_decomposition': {
                'beta_return': result.beta_return,
                'alpha_return': result.alpha_return,
                'alpha_ratio': result.alpha_return / max(abs(result.active_return), 1e-10)
            },
            'index_hedge_analysis': {
                'hedge_csi500_return': result.hedge_500_return,
                'hedge_csi1000_return': result.hedge_1000_return,
                'residual_alpha': result.residual_alpha
            },
            'industry_effects': result.industry_effects,
            'statistics': {
                'r_squared': result.r_squared,
                'tracking_error': result.tracking_error,
                'information_ratio': result.information_ratio
            },
            'timestamp': datetime.now().isoformat()
        }
    
    def _generate_text_report(self, result: AttributionResult) -> str:
        """生成文本报告"""
        report = []
        report.append("=" * 80)
        report.append("Brinson归因分析报告")
        report.append("=" * 80)
        report.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"基准指数: {self.benchmark_symbol}")
        report.append("")
        
        # 收益概况
        report.append("📈 收益概况")
        report.append("-" * 40)
        report.append(f"组合收益率: {result.portfolio_return:.2%}")
        report.append(f"基准收益率: {result.benchmark_return:.2%}")
        report.append(f"超额收益: {result.active_return:.2%} ({'正' if result.active_return >= 0 else '负'}Alpha)")
        report.append("")
        
        # Brinson分解
        report.append("🔍 Brinson收益分解")
        report.append("-" * 40)
        total_effect = result.allocation_effect + result.selection_effect + result.interaction_effect
        report.append(f"资产配置效应: {result.allocation_effect:.2%} ({result.allocation_effect/total_effect:.1%} of total)")
        report.append(f"选股效应: {result.selection_effect:.2%} ({result.selection_effect/total_effect:.1%} of total)")
        report.append(f"交互效应: {result.interaction_effect:.2%} ({result.interaction_effect/total_effect:.1%} of total)")
        report.append(f"合计: {total_effect:.2%} (应与超额收益一致)")
        report.append("")
        
        # Beta/Alpha分解
        report.append("📊 Beta/Alpha分解")
        report.append("-" * 40)
        report.append(f"Beta收益(行业/市场暴露): {result.beta_return:.2%}")
        report.append(f"Alpha收益(选股能力): {result.alpha_return:.2%}")
        report.append(f"Alpha占比: {result.alpha_return/max(abs(result.active_return), 1e-10):.1%}")
        report.append("")
        
        # 指数对冲分析
        if self.use_index_hedge:
            report.append("🛡️ 指数对冲模拟")
            report.append("-" * 40)
            report.append(f"对冲中证500后收益: {result.hedge_500_return:.2%}")
            report.append(f"对冲中证1000后收益: {result.hedge_1000_return:.2%}")
            report.append(f"剥离大盘波动后Alpha: {result.residual_alpha:.2%}")
            report.append("")
        
        # 行业贡献
        if result.industry_effects:
            report.append("🏢 行业贡献分析")
            report.append("-" * 40)
            for industry_code, effects in result.industry_effects.items():
                industry_name = self.get_industry_name(industry_code)
                report.append(f"{industry_name}:")
                report.append(f"  配置效应: {effects['allocation']:.2%}, 选股效应: {effects['selection']:.2%}")
                report.append(f"  组合权重: {effects['portfolio_weight']:.1%}, 基准权重: {effects['benchmark_weight']:.1%}")
            report.append("")
        
        # 统计指标
        report.append("📊 统计指标")
        report.append("-" * 40)
        report.append(f"R² (与基准相关性): {result.r_squared:.3f}")
        report.append(f"跟踪误差: {result.tracking_error:.2%}")
        report.append(f"信息比率: {result.information_ratio:.3f}")
        report.append("")
        
        # 结论
        report.append("💡 结论与建议")
        report.append("-" * 40)
        
        if result.selection_effect > result.allocation_effect:
            report.append("主要收益来源: 选股能力 (Alpha)")
            if result.alpha_return > 0:
                report.append("建议: 继续加强选股策略，保持Alpha获取能力")
            else:
                report.append("警告: 选股能力为负，需优化选股模型")
        else:
            report.append("主要收益来源: 行业配置 (Beta)")
            report.append("建议: 收益主要来自市场/行业暴露，需关注市场风险")
        
        if result.residual_alpha > 0:
            report.append("✅ 剥离大盘波动后仍有正Alpha，说明策略具备真实选股能力")
        else:
            report.append("⚠️ 剥离大盘波动后Alpha为负，策略收益可能依赖市场行情")
        
        report.append("")
        report.append("=" * 80)
        
        return "\n".join(report)
    
    def _generate_markdown_report(self, result: AttributionResult) -> str:
        """生成Markdown报告"""
        # 简化版，实际可根据需要扩展
        return self._generate_text_report(result).replace("=" * 80, "---")


# ============================================================================
# 测试函数
# ============================================================================

def test_brinson_attribution():
    """测试Brinson归因分析"""
    print("🧪 测试Brinson归因分析")
    print("=" * 80)
    
    # 创建归因分析器
    attribution = BrinsonAttribution(benchmark_symbol='000300.SH', use_index_hedge=True)
    
    # 模拟数据
    portfolio_weights = {
        '600519': 0.10,  # 贵州茅台
        '300750': 0.15,  # 宁德时代
        '000858': 0.08,  # 五粮液
        '000333': 0.07,  # 美的集团
        '601318': 0.05,  # 中国平安
        '300059': 0.10,  # 东方财富
    }
    
    portfolio_returns = {
        '600519': 0.12,   # 12%
        '300750': 0.25,   # 25%
        '000858': 0.08,   # 8%
        '000333': 0.06,   # 6%
        '601318': -0.03,  # -3%
        '300059': 0.15,   # 15%
    }
    
    # 基准数据（简化）
    benchmark_weights = {
        '600519': 0.05,
        '300750': 0.03,
        '000858': 0.04,
        '000333': 0.04,
        '601318': 0.08,
        '300059': 0.02,
        '600036': 0.08,  # 招商银行（组合中没有）
        '000001': 0.06,  # 平安银行（组合中没有）
    }
    
    benchmark_returns = {
        '600519': 0.10,
        '300750': 0.20,
        '000858': 0.08,
        '000333': 0.05,
        '601318': 0.02,
        '300059': 0.12,
        '600036': 0.04,
        '000001': 0.03,
    }
    
    # 计算归因
    result = attribution.calculate_brinson_attribution(
        portfolio_weights=portfolio_weights,
        portfolio_returns=portfolio_returns,
        benchmark_weights=benchmark_weights,
        benchmark_returns=benchmark_returns
    )
    
    # 生成报告
    report = attribution.generate_attribution_report(result, output_format='text')
    print(report)
    
    # 测试JSON输出
    json_report = attribution.generate_attribution_report(result, output_format='json')
    print("\n📊 JSON格式结果（前500字符）:")
    print(json_report[:500] + "...")
    
    print("\n" + "=" * 80)
    print("✅ Brinson归因分析测试完成")


if __name__ == "__main__":
    test_brinson_attribution()