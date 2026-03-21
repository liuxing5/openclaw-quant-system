#!/usr/bin/env python3
"""
高级风险管理系统 - 因子+风格+情景多层风控
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any, Optional
import warnings
warnings.filterwarnings('ignore')
import json
import sys
import os

sys.path.append('/root/.openclaw/workspace/quant_system')

class AdvancedRiskManager:
    """高级风险管理器 - 多层次、动态、前瞻性风控"""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or self._default_config()
        
        # 风格因子定义
        self.style_factors = self._initialize_style_factors()
        
        # 风险限额
        self.risk_limits = self._initialize_risk_limits()
        
        # 历史压力情景
        self.stress_scenarios = self._initialize_stress_scenarios()
        
        # 实时监控数据
        self.monitoring_data = {
            'portfolio_exposures': {},
            'var_calculations': {},
            'stress_test_results': {},
            'last_update': None
        }
    
    def _default_config(self) -> Dict[str, Any]:
        """默认配置"""
        return {
            # 风格暴露限制
            'max_style_exposure': 0.3,  # 单风格因子最大暴露
            'max_total_style_exposure': 1.0,  # 总风格暴露限制
            
            # VaR/CVaR设置
            'var_confidence_level': 0.95,  # VaR置信水平
            'cvar_confidence_level': 0.99,  # CVaR置信水平
            'var_lookback_days': 252,  # 回顾期（1年）
            'var_method': 'historical',  # historical/montecarlo/parametric
            
            # 压力测试
            'stress_test_days': [1, 5, 10, 20],  # 压力测试天数
            'stress_scenarios': ['2008_crisis', '2015_crash', '2022_small_cap'],
            
            # 实时监控
            'monitoring_frequency': 'daily',  # daily/hourly/realtime
            'alert_thresholds': {
                'var_breach': 0.9,  # VaR突破阈值
                'exposure_warning': 0.8,  # 暴露警告阈值
                'stress_loss_warning': 0.7  # 压力测试损失警告
            }
        }
    
    def _initialize_style_factors(self) -> Dict[str, Dict[str, Any]]:
        """初始化风格因子定义"""
        
        # Barra CNE5风格因子
        barra_factors = {
            'size': {
                'name': '规模因子',
                'description': '市值规模，通常小盘股有溢价',
                'calculation': 'log(market_cap)',
                'source': 'barra',
                'expected_return': 'positive',  # 小盘股溢价
                'risk': 'high'
            },
            'value': {
                'name': '价值因子',
                'description': '估值水平，低估值股票溢价',
                'calculation': 'book_to_price, earnings_yield',
                'source': 'barra',
                'expected_return': 'positive',
                'risk': 'medium'
            },
            'momentum': {
                'name': '动量因子',
                'description': '价格动量，追涨杀跌',
                'calculation': 'return_12m_excluding_last_month',
                'source': 'barra',
                'expected_return': 'positive',
                'risk': 'high'
            },
            'volatility': {
                'name': '波动率因子',
                'description': '波动率，低波动股票溢价',
                'calculation': 'historical_volatility, beta',
                'source': 'barra',
                'expected_return': 'negative',  # 低波动溢价
                'risk': 'medium'
            },
            'liquidity': {
                'name': '流动性因子',
                'description': '流动性，高流动性股票溢价',
                'calculation': 'turnover, volume',
                'source': 'barra',
                'expected_return': 'negative',  # 低流动性溢价
                'risk': 'high'
            },
            'growth': {
                'name': '成长因子',
                'description': '成长性，高成长股票溢价',
                'calculation': 'sales_growth, earnings_growth',
                'source': 'barra',
                'expected_return': 'positive',
                'risk': 'high'
            },
            'leverage': {
                'name': '杠杆因子',
                'description': '财务杠杆，低杠杆股票溢价',
                'calculation': 'debt_to_equity, debt_to_assets',
                'source': 'barra',
                'expected_return': 'negative',  # 低杠杆溢价
                'risk': 'high'
            }
        }
        
        # 中信风格因子
        citic_factors = {
            'valuation': {
                'name': '估值因子',
                'description': 'PE/PB/PS等估值指标',
                'calculation': 'pe_ttm, pb_mrq, ps_ttm',
                'source': 'citic',
                'expected_return': 'positive',
                'risk': 'medium'
            },
            'quality': {
                'name': '质量因子',
                'description': '盈利能力、运营效率',
                'calculation': 'roe, roa, gross_margin',
                'source': 'citic',
                'expected_return': 'positive',
                'risk': 'low'
            },
            'growth': {
                'name': '成长因子',
                'description': '营收增长、利润增长',
                'calculation': 'revenue_growth, profit_growth',
                'source': 'citic',
                'expected_return': 'positive',
                'risk': 'high'
            },
            'leverage': {
                'name': '杠杆因子',
                'description': '财务杠杆、偿债能力',
                'calculation': 'debt_ratio, current_ratio',
                'source': 'citic',
                'expected_return': 'negative',
                'risk': 'high'
            },
            'volatility': {
                'name': '波动因子',
                'description': '价格波动、Beta',
                'calculation': 'beta, historical_vol',
                'source': 'citic',
                'expected_return': 'negative',
                'risk': 'medium'
            },
            'liquidity': {
                'name': '流动性因子',
                'description': '换手率、成交量',
                'calculation': 'turnover, volume_ratio',
                'source': 'citic',
                'expected_return': 'negative',
                'risk': 'high'
            }
        }
        
        # 华泰风格因子
        huatai_factors = {
            'size': {
                'name': '规模因子',
                'description': '市值规模',
                'calculation': 'market_cap',
                'source': 'huatai',
                'expected_return': 'positive',
                'risk': 'high'
            },
            'valuation': {
                'name': '估值因子',
                'description': '估值水平',
                'calculation': 'pe, pb, dividend_yield',
                'source': 'huatai',
                'expected_return': 'positive',
                'risk': 'medium'
            },
            'profitability': {
                'name': '盈利因子',
                'description': '盈利能力',
                'calculation': 'roe, roic, gross_margin',
                'source': 'huatai',
                'expected_return': 'positive',
                'risk': 'low'
            },
            'growth': {
                'name': '成长因子',
                'description': '成长能力',
                'calculation': 'growth_rate',
                'source': 'huatai',
                'expected_return': 'positive',
                'risk': 'high'
            },
            'leverage': {
                'name': '杠杆因子',
                'description': '财务杠杆',
                'calculation': 'debt_ratio',
                'source': 'huatai',
                'expected_return': 'negative',
                'risk': 'high'
            },
            'reversal': {
                'name': '反转因子',
                'description': '价格反转效应',
                'calculation': 'past_return',
                'source': 'huatai',
                'expected_return': 'negative',  # 反转效应
                'risk': 'medium'
            }
        }
        
        return {
            'barra': barra_factors,
            'citic': citic_factors,
            'huatai': huatai_factors
        }
    
    def _initialize_risk_limits(self) -> Dict[str, Any]:
        """初始化风险限额"""
        return {
            # 风格暴露限制
            'style_exposure_limits': {
                'max_single_exposure': 0.3,  # 单因子最大暴露
                'max_total_exposure': 1.0,   # 总暴露限制
                'warning_threshold': 0.8,    # 警告阈值
            },
            
            # VaR/CVaR限额
            'var_limits': {
                'daily_var_limit': 0.02,     # 日VaR限额 2%
                'weekly_var_limit': 0.05,    # 周VaR限额 5%
                'monthly_var_limit': 0.10,   # 月VaR限额 10%
                'cvar_multiplier': 1.5       # CVaR = VaR * multiplier
            },
            
            # 压力测试限额
            'stress_test_limits': {
                'max_daily_loss_1d': 0.03,   # 1日最大损失 3%
                'max_daily_loss_5d': 0.08,   # 5日最大损失 8%
                'max_daily_loss_10d': 0.15,  # 10日最大损失 15%
                'black_swan_loss': 0.25      # 黑天鹅损失 25%
            },
            
            # 情景分析限额
            'scenario_limits': {
                'inflation_shock': 0.10,     # 通胀冲击损失限额
                'interest_rate_shock': 0.08, # 利率冲击损失限额
                'policy_shock': 0.12,        # 政策冲击损失限额
                'liquidity_crisis': 0.20     # 流动性危机损失限额
            }
        }
    
    def _initialize_stress_scenarios(self) -> Dict[str, Dict[str, Any]]:
        """初始化压力情景"""
        return {
            # 历史极端事件
            '2008_financial_crisis': {
                'name': '2008年全球金融危机',
                'period': '2007-10-01 至 2008-12-31',
                'description': '次贷危机引发的全球金融海啸',
                'market_impact': {
                    'shanghai_index': -65.4,  # 上证指数跌幅
                    'shenzhen_index': -63.5,  # 深证成指跌幅
                    'small_cap_index': -72.3, # 小盘股指数跌幅
                    'liquidity_shock': '极端'
                },
                'duration_days': 300,
                'recovery_days': 800
            },
            
            '2015_stock_market_crash': {
                'name': '2015年A股股灾',
                'period': '2015-06-12 至 2015-09-15',
                'description': '杠杆牛市破裂引发的快速下跌',
                'market_impact': {
                    'shanghai_index': -43.3,
                    'shenzhen_index': -45.8,
                    'gem_index': -51.2,
                    'liquidity_shock': '严重'
                },
                'duration_days': 95,
                'recovery_days': 180
            },
            
            '2022_small_cap_crash': {
                'name': '2022年小微盘股崩盘',
                'period': '2022-01-01 至 2022-04-26',
                'description': '量化策略拥挤导致的微盘股流动性危机',
                'market_impact': {
                    'small_cap_index': -52.7,
                    'micro_cap_index': -61.3,
                    'quant_fund_loss': -35.8,
                    'liquidity_shock': '严重'
                },
                'duration_days': 116,
                'recovery_days': 150
            },
            
            '2025_tech_bubble_burst': {
                'name': '2025年科技股泡沫破裂（假设）',
                'period': '2025-03-01 至 2025-06-30',
                'description': 'AI/科技股过度炒作后的价值回归',
                'market_impact': {
                    'tech_index': -48.5,
                    'ai_index': -55.2,
                    'growth_stocks': -42.3,
                    'liquidity_shock': '中等'
                },
                'duration_days': 122,
                'recovery_days': 240
            },
            
            # 宏观冲击情景
            'inflation_shock': {
                'name': '通胀急剧上升',
                'description': 'CPI同比上升至8%以上，央行快速加息',
                'impact_factors': {
                    'interest_rate': '+3.0%',
                    'bond_yield': '+2.5%',
                    'equity_valuation': '-25%',
                    'currency': '-15%'
                }
            },
            
            'liquidity_crisis': {
                'name': '流动性突然枯竭',
                'description': '资金面紧张，回购利率飙升，融资困难',
                'impact_factors': {
                    'repo_rate': '+5.0%',
                    'funding_cost': '+4.0%',
                    'margin_call': '大规模',
                    'forced_selling': '严重'
                }
            },
            
            'policy_surprise': {
                'name': '政策意外变动',
                'description': '监管政策突然收紧，行业整顿',
                'impact_factors': {
                    'affected_sectors': '互联网/教育/房地产',
                    'regulatory_risk': '极高',
                    'valuation_multiple': '-40%',
                    'growth_outlook': '-30%'
                }
            }
        }
    
    # ========== 风格因子暴露计算 ==========
    
    def calculate_style_exposures(self,
                                 portfolio: Dict[str, float],
                                 stock_data: Dict[str, pd.DataFrame],
                                 factor_source: str = 'barra') -> Dict[str, Any]:
        """
        计算组合的风格因子暴露
        
        Args:
            portfolio: 组合持仓 {symbol: weight}
            stock_data: 股票数据 {symbol: DataFrame}
            factor_source: 因子来源 (barra/citic/huatai)
        
        Returns:
            风格因子暴露分析
        """
        print(f"计算风格因子暴露 ({factor_source})...")
        
        if factor_source not in self.style_factors:
            raise ValueError(f"不支持的因子来源: {factor_source}")
        
        factors = self.style_factors[factor_source]
        exposures = {}
        factor_values = {}
        
        # 对每个因子计算暴露
        for factor_id, factor_info in factors.items():
            try:
                # 计算组合在该因子上的暴露
                exposure = self._calculate_single_factor_exposure(
                    portfolio, stock_data, factor_id, factor_info
                )
                
                exposures[factor_id] = {
                    'exposure': exposure,
                    'name': factor_info['name'],
                    'description': factor_info['description'],
                    'expected_return': factor_info['expected_return'],
                    'risk': factor_info['risk']
                }
                
                factor_values[factor_id] = exposure
                
            except Exception as e:
                print(f"  因子 {factor_id} 计算失败: {e}")
                exposures[factor_id] = {
                    'exposure': 0,
                    'error': str(e),
                    'name': factor_info['name']
                }
        
        # 分析暴露风险
        exposure_analysis = self._analyze_exposure_risk(exposures, factor_values)
        
        return {
            'factor_exposures': exposures,
            'exposure_analysis': exposure_analysis,
            'factor_source': factor_source,
            'portfolio_size': len(portfolio),
            'calculation_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    
    def _calculate_single_factor_exposure(self,
                                         portfolio: Dict[str, float],
                                         stock_data: Dict[str, pd.DataFrame],
                                         factor_id: str,
                                         factor_info: Dict[str, Any]) -> float:
        """计算单个因子暴露"""
        
        # 这里简化实现，实际需要完整的因子数据
        # 实际应用中需要从数据库或API获取因子值
        
        total_exposure = 0.0
        total_weight = 0.0
        
        for symbol, weight in portfolio.items():
            if symbol not in stock_data:
                continue
            
            # 根据因子类型计算因子值（简化）
            factor_value = self._estimate_factor_value(symbol, factor_id, factor_info)
            
            # 加权暴露
            total_exposure += weight * factor_value
            total_weight += weight
        
        # 标准化暴露（均值为0，标准差为1）
        if total_weight > 0:
            exposure = total_exposure / total_weight
        else:
            exposure = 0.0
        
        return exposure
    
    def _estimate_factor_value(self,
                              symbol: str,
                              factor_id: str,
                              factor_info: Dict[str, Any]) -> float:
        """估计因子值（简化实现）"""
        
        # 这里简化处理，实际需要根据因子定义计算
        # 使用随机但有一定逻辑的值
        
        np.random.seed(hash(symbol + factor_id) % 10000)
        
        # 根据不同因子类型生成不同的值
        if factor_id in ['size', 'market_cap']:
            # 规模因子：与市值相关
            base = np.random.normal(0, 1)
            # 小盘股通常有正暴露
            if symbol.startswith('300') or symbol.startswith('688'):
                return base + 0.5  # 创业板/科创板偏小盘
            elif symbol.startswith('002'):
                return base + 0.3  # 中小板偏小盘
            else:
                return base
        
        elif factor_id in ['value', 'valuation']:
            # 价值因子：与估值相关
            base = np.random.normal(0, 1)
            # 银行/传统行业偏价值
            if symbol.startswith('000001') or symbol.startswith('600036'):
                return base + 0.8  # 银行股偏价值
            elif symbol.startswith('600519') or symbol.startswith('000858'):
                return base - 0.5  # 消费股偏成长
            else:
                return base
        
        elif factor_id == 'momentum':
            # 动量因子
            return np.random.normal(0, 1)
        
        elif factor_id in ['volatility', 'beta']:
            # 波动率因子
            base = np.random.normal(0, 1)
            # 科技股波动率高
            if symbol.startswith('300') or symbol.startswith('688'):
                return base + 0.6
            else:
                return base
        
        elif factor_id == 'liquidity':
            # 流动性因子
            base = np.random.normal(0, 1)
            # 大盘股流动性好
            if symbol in ['600519', '000001', '300750']:
                return base - 0.7  # 流动性好为负值
            else:
                return base
        
        elif factor_id in ['growth', 'profitability']:
            # 成长/盈利因子
            base = np.random.normal(0, 1)
            # 科技/消费股成长性高
            if symbol.startswith('300') or symbol.startswith('688'):
                return base + 0.5
            elif symbol.startswith('600519') or symbol.startswith('000858'):
                return base + 0.3
            else:
                return base
        
        elif factor_id == 'leverage':
            # 杠杆因子
            base = np.random.normal(0, 1)
            # 金融/地产股杠杆高
            if symbol.startswith('000001') or symbol.startswith('600036'):
                return base + 0.7
            else:
                return base
        
        elif factor_id == 'reversal':
            # 反转因子
            return np.random.normal(0, 1)
        
        else:
            # 默认
            return np.random.normal(0, 1)
    
    def _analyze_exposure_risk(self,
                              exposures: Dict[str, Any],
                              factor_values: Dict[str, float]) -> Dict[str, Any]:
        """分析暴露风险"""
        
        # 提取暴露值
        exp_values = [exp['exposure'] for exp in exposures.values() if 'exposure' in exp]
        
        if not exp_values:
            return {'error': '无有效暴露数据'}
        
        # 计算风险指标
        max_exposure = max(abs(v) for v in exp_values)
        total_exposure = sum(abs(v) for v in exp_values)
        avg_exposure = np.mean([abs(v) for v in exp_values])
        
        # 检查限额
        max_limit = self.risk_limits['style_exposure_limits']['max_single_exposure']
        total_limit = self.risk_limits['style_exposure_limits']['max_total_exposure']
        warning_threshold = self.risk_limits['style_exposure_limits']['warning_threshold']
        
        # 风险状态
        risk_status = '正常'
        warnings = []
        
        if max_exposure > max_limit:
            risk_status = '超标'
            warnings.append(f"单因子暴露 {max_exposure:.3f} 超过限额 {max_limit:.3f}")
        elif max_exposure > max_limit * warning_threshold:
            risk_status = '警告'
            warnings.append(f"单因子暴露 {max_exposure:.3f} 接近限额 {max_limit:.3f}")
        
        if total_exposure > total_limit:
            risk_status = '超标'
            warnings.append(f"总暴露 {total_exposure:.3f} 超过限额 {total_limit:.3f}")
        elif total_exposure > total_limit * warning_threshold:
            risk_status = '警告'
            warnings.append(f"总暴露 {total_exposure:.3f} 接近限额 {total_limit:.3f}")
        
        # 识别高风险因子
        high_risk_factors = []
        for factor_id, exp_data in exposures.items():
            if 'exposure' in exp_data:
                exposure = abs(exp_data['exposure'])
                if exposure > max_limit * 0.7:  # 超过70%限额
                    high_risk_factors.append({
                        'factor': factor_id,
                        'exposure': exp_data['exposure'],
                        'name': exp_data['name'],
                        'risk_level': '高' if exposure > max_limit * 0.9 else '中'
                    })
        
        return {
            'risk_status': risk_status,
            'warnings': warnings,
            'metrics': {
                'max_exposure': max_exposure,
                'total_exposure': total_exposure,
                'avg_exposure': avg_exposure,
                'max_exposure_limit': max_limit,
                'total_exposure_limit': total_limit
            },
            'high_risk_factors': high_risk_factors,
            'recommendations': self._generate_exposure_recommendations(high_risk_factors, max_limit)
        }
    
    def _generate_exposure_recommendations(self,
                                          high_risk_factors: List[Dict],
                                          max_limit: float) -> List[str]:
        """生成暴露调整建议"""
        recommendations = []
        
        for factor in high_risk_factors:
            factor_id = factor['factor']
            exposure = factor['exposure']
            risk_level = factor['risk_level']
            
            if risk_level == '高':
                action = '立即降低'
            else:
                action = '逐步调整'
            
            rec = f"{action} {factor['name']}({factor_id})暴露: 当前{exposure:.3f}, 目标{max_limit*0.5:.3f}"
            recommendations.append(rec)
        
        if not high_risk_factors:
            recommendations.append("风格因子暴露在安全范围内，可维持当前配置")
        
        return recommendations
    
    # ========== VaR/CVaR计算 ==========
    
    def calculate_var_cvar(self,
                          portfolio_returns: pd.Series,
                          confidence_level: float = None,
                          method: str = None,
                          lookback_days: int = None) -> Dict[str, Any]:
        """
        计算VaR和CVaR
        
        Args:
            portfolio_returns: 组合收益率序列
            confidence_level: 置信水平
            method: 计算方法 (historical/montecarlo/parametric)
            lookback_days: 回顾天数
        
        Returns:
            VaR/CVaR计算结果
        """
        print(f"计算VaR/CVaR (method={method})...")
        
        # 使用配置默认值
        if confidence_level is None:
            confidence_level = self.config['var_confidence_level']
        if method is None:
            method = self.config['var_method']
        if lookback_days is None:
            lookback_days = self.config['var_lookback_days']
        
        # 确保有足够数据
        if len(portfolio_returns) < lookback_days:
            lookback_days = len(portfolio_returns)
        
        if lookback_days < 30:
            return {'error': '数据不足，至少需要30个交易日'}
        
        # 使用最近的数据
        recent_returns = portfolio_returns.tail(lookback_days)
        
        # 根据方法计算
        if method == 'historical':
            result = self._calculate_historical_var_cvar(recent_returns, confidence_level)
        elif method == 'montecarlo':
            result = self._calculate_montecarlo_var_cvar(recent_returns, confidence_level)
        elif method == 'parametric':
            result = self._calculate_parametric_var_cvar(recent_returns, confidence_level)
        else:
            raise ValueError(f"不支持的VaR计算方法: {method}")
        
        # 检查限额
        limit_check = self._check_var_limits(result)
        
        return {
            'var_cvar_results': result,
            'limit_check': limit_check,
            'calculation_details': {
                'method': method,
                'confidence_level': confidence_level,
                'lookback_days': lookback_days,
                'data_points': len(recent_returns),
                'period_start': recent_returns.index[0].strftime('%Y-%m-%d'),
                'period_end': recent_returns.index[-1].strftime('%Y-%m-%d')
            }
        }
    
    def _calculate_historical_var_cvar(self,
                                      returns: pd.Series,
                                      confidence_level: float) -> Dict[str, float]:
        """历史模拟法计算VaR/CVaR"""
        
        # 按升序排序
        sorted_returns = returns.sort_values()
        
        # 计算分位数位置
        var_index = int(len(sorted_returns) * (1 - confidence_level))
        if var_index >= len(sorted_returns):
            var_index = len(sorted_returns) - 1
        
        # VaR (负号表示损失)
        var = -sorted_returns.iloc[var_index]
        
        # CVaR (超出VaR的平均损失)
        tail_returns = sorted_returns.iloc[:var_index+1]
        cvar = -tail_returns.mean()
        
        return {
            'var': var,
            'cvar': cvar,
            'confidence_level': confidence_level,
            'method': 'historical'
        }
    
    def _calculate_montecarlo_var_cvar(self,
                                      returns: pd.Series,
                                      confidence_level: float,
                                      simulations: int = 10000) -> Dict[str, float]:
        """蒙特卡洛模拟法计算VaR/CVaR"""
        
        # 计算收益率的统计特性
        mu = returns.mean()
        sigma = returns.std()
        
        # 生成模拟收益率
        np.random.seed(42)  # 可重复性
        simulated_returns = np.random.normal(mu, sigma, simulations)
        
        # 计算VaR/CVaR
        var = -np.percentile(simulated_returns, (1 - confidence_level) * 100)
        cvar = -simulated_returns[simulated_returns <= -var].mean()
        
        return {
            'var': var,
            'cvar': cvar,
            'confidence_level': confidence_level,
            'method': 'montecarlo',
            'simulations': simulations,
            'assumed_distribution': 'normal'
        }
    
    def _calculate_parametric_var_cvar(self,
                                      returns: pd.Series,
                                      confidence_level: float) -> Dict[str, float]:
        """参数法计算VaR/CVaR（假设正态分布）"""
        
        mu = returns.mean()
        sigma = returns.std()
        
        # 正态分布分位数
        from scipy import stats
        z_score = stats.norm.ppf(confidence_level)
        
        # 参数法VaR
        var = -(mu + z_score * sigma)
        
        # 参数法CVaR（正态分布下）
        phi_z = stats.norm.pdf(z_score)
        cvar = -(mu + sigma * phi_z / (1 - confidence_level))
        
        return {
            'var': var,
            'cvar': cvar,
            'confidence_level': confidence_level,
            'method': 'parametric',
            'assumed_distribution': 'normal',
            'mean': mu,
            'std': sigma,
            'z_score': z_score
        }
    
    def _check_var_limits(self, var_result: Dict[str, float]) -> Dict[str, Any]:
        """检查VaR限额"""
        
        daily_var = var_result.get('var', 0)
        daily_cvar = var_result.get('cvar', 0)
        
        limits = self.risk_limits['var_limits']
        
        # 计算其他期限的VaR（简化：sqrt(T)规则）
        weekly_var = daily_var * np.sqrt(5)
        monthly_var = daily_var * np.sqrt(22)
        
        # 检查限额
        breaches = []
        warnings = []
        
        if daily_var > limits['daily_var_limit']:
            breaches.append(f"日VaR {daily_var:.3%} 超过限额 {limits['daily_var_limit']:.2%}")
        elif daily_var > limits['daily_var_limit'] * 0.9:
            warnings.append(f"日VaR {daily_var:.3%} 接近限额 {limits['daily_var_limit']:.2%}")
        
        if weekly_var > limits['weekly_var_limit']:
            breaches.append(f"周VaR {weekly_var:.3%} 超过限额 {limits['weekly_var_limit']:.2%}")
        
        if monthly_var > limits['monthly_var_limit']:
            breaches.append(f"月VaR {monthly_var:.3%} 超过限额 {limits['monthly_var_limit']:.2%}")
        
        # CVaR检查
        cvar_multiplier = limits['cvar_multiplier']
        expected_cvar = daily_var * cvar_multiplier
        if daily_cvar > expected_cvar * 1.2:
            warnings.append(f"CVaR {daily_cvar:.3%} 显著高于预期 {expected_cvar:.3%}")
        
        status = '正常'
        if breaches:
            status = '超标'
        elif warnings:
            status = '警告'
        
        return {
            'status': status,
            'breaches': breaches,
            'warnings': warnings,
            'calculated_values': {
                'daily_var': daily_var,
                'daily_cvar': daily_cvar,
                'weekly_var': weekly_var,
                'monthly_var': monthly_var,
                'cvar_multiplier': daily_cvar / max(daily_var, 0.0001)
            },
            'limits': limits
        }
    
    # ========== 压力测试 ==========
    
    def run_stress_tests(self,
                        portfolio: Dict[str, float],
                        stock_data: Dict[str, pd.DataFrame],
                        scenarios: List[str] = None) -> Dict[str, Any]:
        """
        运行压力测试
        
        Args:
            portfolio: 组合持仓
            stock_data: 股票数据
            scenarios: 压力情景列表
        
        Returns:
            压力测试结果
        """
        print("运行压力测试...")
        
        if scenarios is None:
            scenarios = self.config['stress_scenarios']
        
        results = {}
        
        for scenario_id in scenarios:
            if scenario_id not in self.stress_scenarios:
                print(f"  跳过未知情景: {scenario_id}")
                continue
            
            print(f"  测试情景: {scenario_id}")
            
            try:
                scenario_result = self._run_single_stress_test(
                    portfolio, stock_data, scenario_id
                )
                
                results[scenario_id] = scenario_result
                
            except Exception as e:
                print(f"  情景 {scenario_id} 测试失败: {e}")
                results[scenario_id] = {
                    'error': str(e),
                    'scenario_name': self.stress_scenarios[scenario_id].get('name', '未知')
                }
        
        # 汇总分析
        summary = self._summarize_stress_test_results(results)
        
        return {
            'scenario_results': results,
            'summary': summary,
            'total_scenarios': len(results),
            'successful_scenarios': sum(1 for r in results.values() if 'error' not in r)
        }
    
    def _run_single_stress_test(self,
                               portfolio: Dict[str, float],
                               stock_data: Dict[str, pd.DataFrame],
                               scenario_id: str) -> Dict[str, Any]:
        """运行单个压力测试"""
        
        scenario = self.stress_scenarios[scenario_id]
        
        # 这里简化实现，实际需要历史数据或模型
        # 根据情景类型应用不同的冲击
        
        if scenario_id in ['2008_financial_crisis', '2015_stock_market_crash', '2022_small_cap_crash']:
            # 历史危机情景
            loss = self._simulate_historical_crisis(portfolio, stock_data, scenario)
        elif scenario_id == 'inflation_shock':
            # 通胀冲击
            loss = self._simulate_inflation_shock(portfolio, stock_data, scenario)
        elif scenario_id == 'liquidity_crisis':
            # 流动性危机
            loss = self._simulate_liquidity_crisis(portfolio, stock_data, scenario)
        elif scenario_id == 'policy_surprise':
            # 政策冲击
            loss = self._simulate_policy_shock(portfolio, stock_data, scenario)
        else:
            # 默认：随机冲击
            loss = self._simulate_random_shock(portfolio, stock_data, scenario)
        
        # 检查限额
        limit_check = self._check_stress_test_limits(loss, scenario_id)
        
        return {
            'scenario_info': scenario,
            'estimated_loss': loss,
            'limit_check': limit_check,
            'interpretation': self._interpret_stress_test_result(loss, scenario)
        }
    
    def _simulate_historical_crisis(self,
                                   portfolio: Dict[str, float],
                                   stock_data: Dict[str, pd.DataFrame],
                                   scenario: Dict[str, Any]) -> float:
        """模拟历史危机"""
        
        # 获取市场冲击数据
        market_impact = scenario.get('market_impact', {})
        
        # 简化的损失计算
        base_loss = 0.0
        
        # 根据股票类型应用不同冲击
        for symbol, weight in portfolio.items():
            # 判断股票类型
            if symbol.startswith('300') or symbol.startswith('688'):
                # 创业板/科创板 - 小盘科技股，冲击大
                stock_loss = market_impact.get('small_cap_index', -40) / 100
            elif symbol.startswith('002'):
                # 中小板 - 中小盘股
                stock_loss = market_impact.get('shenzhen_index', -35) / 100
            else:
                # 主板
                stock_loss = market_impact.get('shanghai_index', -30) / 100
            
            base_loss += weight * abs(stock_loss)
        
        # 添加随机成分
        np.random.seed(hash(str(portfolio)) % 10000)
        random_component = np.random.uniform(-0.05, 0.05)
        
        total_loss = base_loss + random_component
        
        # 确保在合理范围内
        return max(0.0, min(0.8, total_loss))
    
    def _simulate_inflation_shock(self,
                                 portfolio: Dict[str, float],
                                 stock_data: Dict[str, pd.DataFrame],
                                 scenario: Dict[str, Any]) -> float:
        """模拟通胀冲击"""
        
        # 通胀对不同行业影响不同
        sector_impacts = {
            '金融': -0.15,  # 加息对银行有利，但对保险不利
            '消费': -0.25,  # 成本上升，需求下降
            '科技': -0.20,  # 估值压缩
            '医药': -0.10,  # 防御性较强
            '周期': -0.30,  # 成本敏感
            '公用事业': -0.05  # 防御性
        }
        
        # 简化的行业分类
        def get_sector(symbol):
            if symbol.startswith('000001') or symbol.startswith('600036'):
                return '金融'
            elif symbol.startswith('600519') or symbol.startswith('000858'):
                return '消费'
            elif symbol.startswith('300') or symbol.startswith('688'):
                return '科技'
            elif symbol.startswith('002') and symbol.endswith('医药'):
                return '医药'
            else:
                return '周期'
        
        # 计算损失
        total_loss = 0.0
        for symbol, weight in portfolio.items():
            sector = get_sector(symbol)
            sector_loss = sector_impacts.get(sector, -0.20)
            total_loss += weight * abs(sector_loss)
        
        return total_loss
    
    def _simulate_liquidity_crisis(self,
                                  portfolio: Dict[str, float],
                                  stock_data: Dict[str, pd.DataFrame],
                                  scenario: Dict[str, Any]) -> float:
        """模拟流动性危机"""
        
        # 流动性危机对小盘股冲击更大
        total_loss = 0.0
        
        for symbol, weight in portfolio.items():
            # 判断流动性（简化）
            if symbol.startswith('300') or symbol.startswith('688'):
                # 创业板/科创板 - 流动性风险高
                liquidity_risk = 0.35
            elif symbol.startswith('002'):
                # 中小板
                liquidity_risk = 0.25
            else:
                # 主板大盘股
                liquidity_risk = 0.15
            
            total_loss += weight * liquidity_risk
        
        return total_loss
    
    def _simulate_policy_shock(self,
                              portfolio: Dict[str, float],
                              stock_data: Dict[str, pd.DataFrame],
                              scenario: Dict[str, Any]) -> float:
        """模拟政策冲击"""
        
        # 政策冲击对特定行业影响大
        policy_sensitive_sectors = ['互联网', '教育', '房地产', '平台经济']
        
        total_loss = 0.0
        
        for symbol, weight in portfolio.items():
            # 简化的政策敏感性判断
            if symbol.startswith('300') and '科技' in str(symbol):
                # 科技股可能受监管
                sensitivity = 0.28
            elif symbol.startswith('600') and '地产' in str(symbol):
                # 地产股政策敏感
                sensitivity = 0.32
            else:
                sensitivity = 0.12
            
            total_loss += weight * sensitivity
        
        return total_loss
    
    def _simulate_random_shock(self,
                              portfolio: Dict[str, float],
                              stock_data: Dict[str, pd.DataFrame],
                              scenario: Dict[str, Any]) -> float:
        """模拟随机冲击"""
        
        np.random.seed(hash(str(portfolio) + scenario.get('name', '')) % 10000)
        
        # 随机损失，但基于情景严重程度
        severity = scenario.get('severity', 'medium')
        
        if severity == 'extreme':
            base_loss = np.random.uniform(0.25, 0.45)
        elif severity == 'high':
            base_loss = np.random.uniform(0.15, 0.30)
        elif severity == 'medium':
            base_loss = np.random.uniform(0.08, 0.20)
        else:
            base_loss = np.random.uniform(0.03, 0.12)
        
        return base_loss
    
    def _check_stress_test_limits(self, loss: float, scenario_id: str) -> Dict[str, Any]:
        """检查压力测试限额"""
        
        limits = self.risk_limits['stress_test_limits']
        
        # 根据情景类型确定限额
        if 'black_swan' in scenario_id or 'crisis' in scenario_id:
            limit = limits['black_swan_loss']
        elif 'crash' in scenario_id:
            limit = limits['max_daily_loss_10d']
        elif 'shock' in scenario_id:
            limit = limits['max_daily_loss_5d']
        else:
            limit = limits['max_daily_loss_1d']
        
        # 检查限额
        breaches = []
        warnings = []
        
        if loss > limit:
            breaches.append(f"损失 {loss:.3%} 超过限额 {limit:.2%}")
        elif loss > limit * 0.8:
            warnings.append(f"损失 {loss:.3%} 接近限额 {limit:.2%}")
        
        status = '正常'
        if breaches:
            status = '超标'
        elif warnings:
            status = '警告'
        
        return {
            'status': status,
            'breaches': breaches,
            'warnings': warnings,
            'loss': loss,
            'limit': limit,
            'scenario_id': scenario_id
        }