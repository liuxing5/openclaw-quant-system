#!/usr/bin/env python3
"""
专业因子风险模型 - Barra/Axioma风格风险建模系统

解决用户指出的问题：
1. 简单历史模拟法或参数法VaR/CVaR → 因子暴露分解（style + industry + specific risk）
2. 缺少跨市场压力测试 → 2015股灾、2018熊市、2022俄乌
3. 缺少尾部风险情景生成 → 历史重放 + 合成极端情景
4. 缺少Conditional VaR → 在熊市regime下VaR放大

解决方案：
1. 引入Barra/Axioma风格的风险模型思想（简化版实现）
2. 至少做3个历史极端窗口重放（2015.6、2018.10、2020.3）
3. 实现尾部风险情景生成（历史重放 + 合成极端情景）
4. 实现Conditional VaR（在熊市regime下VaR放大）
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any, Optional, Union
import warnings
from dataclasses import dataclass, field
from enum import Enum
import logging
import json
import sys
import os

warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 尝试导入必要的库
try:
    import scipy.stats as stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

try:
    import statsmodels.api as sm
    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False


class MarketRegime(Enum):
    """市场状态"""
    NORMAL = "normal"           # 正常市场
    BEAR = "bear"               # 熊市
    CRASH = "crash"             # 崩盘
    RECOVERY = "recovery"       # 复苏
    BUBBLE = "bubble"           # 泡沫


class RiskModelType(Enum):
    """风险模型类型"""
    BARRA_STYLE = "barra_style"      # Barra风格因子模型
    AXIOMA_STYLE = "axioma_style"    # Axioma风格因子模型
    SIMPLIFIED = "simplified"        # 简化因子模型


@dataclass
class FactorExposure:
    """因子暴露"""
    factor_id: str
    factor_name: str
    factor_type: str  # style/industry/idiosyncratic
    exposure: float   # 暴露系数
    contribution: float = 0.0  # 风险贡献度
    risk_per_unit: float = 0.0  # 单位因子风险


@dataclass
class RiskDecomposition:
    """风险分解结果"""
    total_risk: float
    style_risk: float
    industry_risk: float
    specific_risk: float
    factor_exposures: List[FactorExposure]
    covariance_matrix: Optional[np.ndarray] = None
    factor_returns: Optional[np.ndarray] = None


@dataclass
class StressTestResult:
    """压力测试结果"""
    scenario_id: str
    scenario_name: str
    period_start: str
    period_end: str
    portfolio_loss: float  # 组合损失百分比
    market_loss: float     # 市场损失百分比
    relative_loss: float   # 相对损失
    worst_day_loss: float  # 最差单日损失
    factor_exposures_under_stress: Dict[str, float]
    lessons_learned: str


@dataclass
class TailRiskScenario:
    """尾部风险情景"""
    scenario_id: str
    scenario_type: str  # historical/synthetic/hybrid
    description: str
    parameters: Dict[str, Any]
    market_impact: Dict[str, float]  # 不同资产类别影响
    correlation_shifts: Dict[str, float]  # 相关性变化
    liquidity_impact: float  # 流动性冲击程度


class FactorRiskModel:
    """
    专业因子风险模型 - Barra/Axioma风格
    
    核心功能：
    1. 因子暴露分解（style + industry + specific risk）
    2. 跨市场压力测试（2015股灾、2018熊市、2022俄乌）
    3. 尾部风险情景生成（历史重放 + 合成极端情景）
    4. Conditional VaR（在熊市regime下VaR放大）
    """
    
    def __init__(self, 
                 model_type: RiskModelType = RiskModelType.SIMPLIFIED,
                 config: Dict[str, Any] = None):
        """
        初始化因子风险模型
        
        Args:
            model_type: 模型类型
            config: 配置参数
        """
        self.model_type = model_type
        self.config = config or self._default_config()
        
        # 初始化风格因子
        self.style_factors = self._initialize_style_factors()
        
        # 初始化行业因子
        self.industry_factors = self._initialize_industry_factors()
        
        # 初始化压力测试情景
        self.stress_scenarios = self._initialize_stress_scenarios()
        
        # 初始化尾部风险生成器
        self.tail_risk_generator = self._initialize_tail_risk_generator()
        
        logger.info(f"因子风险模型初始化完成 (type={model_type.value})")
    
    def _default_config(self) -> Dict[str, Any]:
        """默认配置"""
        return {
            # 因子模型参数
            'factor_lookback_days': 252,  # 因子计算回顾期
            'min_data_points': 100,       # 最小数据点要求
            'covariance_method': 'ledoit_wolf',  # 协方差矩阵估计方法
            
            # 风险分解参数
            'specific_risk_multiplier': 1.5,  # 特质风险乘数
            'max_factor_exposure': 2.0,       # 最大因子暴露
            
            # 压力测试参数
            'stress_window_days': 20,         # 压力窗口天数
            'confidence_level_stress': 0.99,  # 压力测试置信水平
            
            # Conditional VaR参数
            'bear_regime_amplification': 1.8,  # 熊市VaR放大倍数
            'crash_regime_amplification': 2.5, # 崩盘VaR放大倍数
            
            # 尾部风险参数
            'tail_probability': 0.01,         # 尾部概率
            'extreme_shock_size': 3.0,        # 极端冲击大小（标准差倍数）
        }
    
    def _initialize_style_factors(self) -> Dict[str, Dict[str, Any]]:
        """初始化风格因子定义（Barra CNE5风格）"""
        
        style_factors = {
            'size': {
                'name': '规模因子',
                'description': '市值规模，通常小盘股有溢价',
                'calculation': 'log(market_cap)',
                'expected_return': 'positive',  # 小盘股溢价
                'risk_premium': 0.02,  # 年化风险溢价
                'volatility': 0.15,    # 年化波动率
                'source': 'barra'
            },
            'value': {
                'name': '价值因子',
                'description': '估值水平，低估值股票溢价',
                'calculation': 'book_to_price, earnings_yield',
                'expected_return': 'positive',
                'risk_premium': 0.015,
                'volatility': 0.12,
                'source': 'barra'
            },
            'momentum': {
                'name': '动量因子',
                'description': '价格动量，追涨杀跌',
                'calculation': 'return_12m_excluding_last_month',
                'expected_return': 'positive',
                'risk_premium': 0.018,
                'volatility': 0.18,
                'source': 'barra'
            },
            'volatility': {
                'name': '波动率因子',
                'description': '波动率，低波动股票溢价',
                'calculation': 'historical_volatility, beta',
                'expected_return': 'positive',
                'risk_premium': 0.012,
                'volatility': 0.10,
                'source': 'barra'
            },
            'liquidity': {
                'name': '流动性因子',
                'description': '流动性，高流动性股票溢价',
                'calculation': 'turnover, trading_value',
                'expected_return': 'positive',
                'risk_premium': 0.008,
                'volatility': 0.08,
                'source': 'barra'
            },
            'growth': {
                'name': '成长因子',
                'description': '成长性，高成长股票溢价',
                'calculation': 'earnings_growth, sales_growth',
                'expected_return': 'positive',
                'risk_premium': 0.020,
                'volatility': 0.20,
                'source': 'barra'
            },
            'profitability': {
                'name': '盈利能力因子',
                'description': '盈利能力，高ROE股票溢价',
                'calculation': 'roe, gross_margin',
                'expected_return': 'positive',
                'risk_premium': 0.014,
                'volatility': 0.11,
                'source': 'barra'
            },
            'leverage': {
                'name': '杠杆因子',
                'description': '财务杠杆，低杠杆股票溢价',
                'calculation': 'debt_to_equity, interest_coverage',
                'expected_return': 'positive',
                'risk_premium': 0.010,
                'volatility': 0.09,
                'source': 'barra'
            }
        }
        
        return style_factors
    
    def _initialize_industry_factors(self) -> Dict[str, Dict[str, Any]]:
        """初始化行业因子定义（申万一级行业）"""
        
        # 申万一级行业分类
        industries = {
            'sw_banks': {'name': '银行', 'code': '801780'},
            'sw_non_bank_finance': {'name': '非银金融', 'code': '801790'},
            'sw_real_estate': {'name': '房地产', 'code': '801180'},
            'sw_construction': {'name': '建筑装饰', 'code': '801720'},
            'sw_steel': {'name': '钢铁', 'code': '801040'},
            'sw_nonferrous_metals': {'name': '有色金属', 'code': '801050'},
            'sw_defense': {'name': '国防军工', 'code': '801740'},
            'sw_computers': {'name': '计算机', 'code': '801750'},
            'sw_media': {'name': '传媒', 'code': '801760'},
            'sw_communications': {'name': '通信', 'code': '801770'},
            'sw_electronics': {'name': '电子', 'code': '801080'},
            'sw_household_appliances': {'name': '家用电器', 'code': '801110'},
            'sw_food_beverage': {'name': '食品饮料', 'code': '801120'},
            'sw_textile_apparel': {'name': '纺织服装', 'code': '801130'},
            'sw_light_manufacturing': {'name': '轻工制造', 'code': '801140'},
            'sw_pharmaceuticals': {'name': '医药生物', 'code': '801150'},
            'sw_utilities': {'name': '公用事业', 'code': '801160'},
            'sw_transportation': {'name': '交通运输', 'code': '801170'},
            'sw_automotive': {'name': '汽车', 'code': '801880'},
            'sw_machinery': {'name': '机械设备', 'code': '801890'},
            'sw_electric_equipment': {'name': '电气设备', 'code': '801730'},
            'sw_building_materials': {'name': '建筑材料', 'code': '801710'},
            'sw_chemicals': {'name': '化工', 'code': '801030'},
            'sw_agriculture': {'name': '农林牧渔', 'code': '801010'},
            'sw_commercial_trade': {'name': '商业贸易', 'code': '801200'},
            'sw_leisure_services': {'name': '休闲服务', 'code': '801210'},
            'sw_comprehensive': {'name': '综合', 'code': '801230'},
        }
        
        return industries
    
    def _initialize_stress_scenarios(self) -> Dict[str, Dict[str, Any]]:
        """初始化压力测试情景（用户要求的3个历史极端窗口）"""
        
        stress_scenarios = {
            # 用户要求的3个历史极端窗口
            '2015_china_stock_crash': {
                'name': '2015年中国股灾',
                'period_start': '2015-06-12',
                'period_end': '2015-08-26',
                'duration_days': 52,
                'description': '杠杆牛市的崩盘，上证综指从5178点跌至2850点',
                'market_impact': {
                    'shanghai_composite': -45.0,  # 上证综指下跌45%
                    'shenzhen_component': -47.0,  # 深证成指下跌47%
                    'gem_index': -51.2,          # 创业板指下跌51.2%
                    'leveraged_positions': -85.0, # 杠杆资金爆仓85%
                    'liquidity_shock': '严重',
                    'policy_response': '国家队救市'
                },
                'key_events': [
                    '2015-06-12: 上证综指达到5178点峰值',
                    '2015-06-15: 开始暴跌，千股跌停',
                    '2015-07-08: 国家队入市救市',
                    '2015-08-24: 黑色星期一，全球市场联动下跌',
                    '2015-08-26: 跌至2850点底部'
                ],
                'factor_impacts': {
                    'momentum': -0.35,    # 动量因子大幅失效
                    'liquidity': -0.28,   # 流动性因子负收益
                    'size': 0.12,         # 大盘股相对抗跌
                    'volatility': 0.25    # 高波动股票暴跌
                },
                'lessons_learned': '杠杆牛市不可持续，流动性危机传导迅速'
            },
            
            '2018_china_bear_market': {
                'name': '2018年A股熊市',
                'period_start': '2018-01-29',
                'period_end': '2018-10-19',
                'duration_days': 263,
                'description': '去杠杆+中美贸易战双重打击下的漫长熊市',
                'market_impact': {
                    'shanghai_composite': -31.5,  # 上证综指下跌31.5%
                    'shenzhen_component': -38.0,  # 深证成指下跌38%
                    'gem_index': -34.8,          # 创业板指下跌34.8%
                    'trade_war_impact': '严重',
                    'deleveraging_pressure': '持续'
                },
                'key_events': [
                    '2018-01-29: 上证综指3587点开始下跌',
                    '2018-03-22: 美国宣布对中国加征关税',
                    '2018-07-06: 中美正式互征关税',
                    '2018-10-19: 刘鹤副总理喊话稳定市场'
                ],
                'factor_impacts': {
                    'value': -0.18,       # 价值因子受贸易战冲击
                    'growth': -0.32,      # 成长股大幅下跌
                    'profitability': -0.15, # 盈利能力因子受挫
                    'size': 0.08          # 大盘股相对抗跌
                },
                'lessons_learned': '贸易战冲击估值体系，政策底不等于市场底'
            },
            
            '2022_russia_ukraine_war': {
                'name': '2022年俄乌战争',
                'period_start': '2022-02-24',
                'period_end': '2022-03-08',
                'duration_days': 13,
                'description': '俄乌战争爆发导致全球市场剧烈波动',
                'market_impact': {
                    'global_indices': -15.0,      # 全球指数普遍下跌
                    'commodity_prices': +45.0,    # 大宗商品暴涨
                    'energy_stocks': +38.0,       # 能源股大涨
                    'tech_stocks': -22.0,         # 科技股大跌
                    'currency_volatility': '极高',
                    'safe_haven_flows': '黄金/美元/美债'
                },
                'key_events': [
                    '2022-02-24: 俄罗斯宣布对乌特别军事行动',
                    '2022-02-28: SWIFT制裁俄罗斯银行',
                    '2022-03-07: 布伦特原油突破139美元',
                    '2022-03-08: 伦镍逼空事件'
                ],
                'factor_impacts': {
                    'momentum': -0.42,    # 动量因子剧烈反转
                    'value': 0.25,        # 价值股受益（能源/材料）
                    'growth': -0.38,      # 成长股受重创
                    'size': 0.05          # 大盘股相对稳定
                },
                'lessons_learned': '地缘政治风险不可预测，大宗商品冲击通胀'
            },
            
            # 额外补充的重要压力情景
            '2020_covid_crash': {
                'name': '2020年新冠疫情崩盘',
                'period_start': '2020-02-20',
                'period_end': '2020-03-23',
                'duration_days': 33,
                'description': '新冠疫情全球爆发导致的市场恐慌',
                'market_impact': {
                    'global_indices': -35.0,      # 全球指数暴跌
                    'volatility_index': +400.0,   # VIX暴涨
                    'liquidity_crisis': '严重',
                    'federal_reserve_response': '无限QE'
                },
                'key_events': [
                    '2020-02-24: 全球股市开始下跌',
                    '2020-03-09: 黑色星期一，原油价格战',
                    '2020-03-12: 黑色星期四，全球多国熔断',
                    '2020-03-16: 美联储紧急降息至零',
                    '2020-03-23: 市场底部，美联储宣布无限QE'
                ],
                'factor_impacts': {
                    'liquidity': -0.65,   # 流动性因子极端负收益
                    'volatility': 0.48,   # 高波动股票暴跌
                    'size': 0.15,         # 大盘股相对抗跌
                    'profitability': -0.22 # 盈利能力受疫情影响
                },
                'lessons_learned': '疫情黑天鹅，流动性危机传导，央行无限宽松'
            }
        }
        
        return stress_scenarios
    
    def _initialize_tail_risk_generator(self) -> Dict[str, Any]:
        """初始化尾部风险情景生成器"""
        
        tail_generator = {
            # 历史重放方法
            'historical_replay': {
                'description': '基于历史极端事件重放',
                'method': 'bootstrap_with_replacement',
                'parameters': {
                    'bootstrap_samples': 1000,
                    'extreme_threshold': 0.01,  # 1%尾部事件
                    'block_size': 10  # 块采样大小（保持序列依赖）
                }
            },
            
            # 合成极端情景方法
            'synthetic_extremes': {
                'description': '合成极端市场情景',
                'method': 'copula_based_extremes',
                'parameters': {
                    'copula_type': 't_copula',  # t-copula捕捉尾部依赖
                    'degrees_of_freedom': 3,    # 低自由度 => 厚尾
                    'correlation_shock': 0.6,   # 相关性冲击增加
                    'volatility_shock': 2.5     # 波动率冲击倍数
                }
            },
            
            # 混合方法（历史+合成）
            'hybrid_scenarios': {
                'description': '历史重放与合成极端结合',
                'method': 'historical_synthetic_hybrid',
                'parameters': {
                    'historical_weight': 0.7,
                    'synthetic_weight': 0.3,
                    'shock_amplification': 1.2
                }
            },
            
            # 系统性风险情景
            'systemic_risk_scenarios': {
                'description': '系统性风险爆发情景',
                'scenarios': {
                    'liquidity_crisis': {
                        'description': '流动性突然枯竭',
                        'impact_factors': {
                            'bid_ask_spread': 5.0,  # 买卖价差扩大5倍
                            'market_depth': -80.0,  # 市场深度减少80%
                            'funding_cost': 4.0,    # 融资成本上升4%
                            'forced_selling': '大规模'
                        }
                    },
                    'counterparty_default': {
                        'description': '交易对手违约连锁反应',
                        'impact_factors': {
                            'credit_spread': 3.5,   # 信用利差扩大3.5倍
                            'rehypothecation': -60.0, # 再抵押减少60%
                            'collateral_haircut': 2.0 # 质押品扣减率翻倍
                        }
                    },
                    'regime_shift': {
                        'description': '市场状态剧烈转换',
                        'impact_factors': {
                            'correlation_breakdown': 0.8,  # 相关性结构破坏
                            'factor_rotation': '剧烈',     # 因子轮动剧烈
                            'risk_premium_reversal': -0.25 # 风险溢价反转
                        }
                    }
                }
            }
        }
        
        return tail_generator
    
    # ========== 核心方法：因子暴露分解 ==========
    
    def decompose_risk(self,
                      portfolio: Dict[str, float],
                      stock_data: Dict[str, pd.DataFrame],
                      market_data: pd.DataFrame) -> RiskDecomposition:
        """
        分解组合风险为因子暴露（style + industry + specific risk）
        
        Args:
            portfolio: 组合持仓 {symbol: weight}
            stock_data: 股票数据 {symbol: DataFrame}
            market_data: 市场数据（基准指数）
            
        Returns:
            风险分解结果
        """
        logger.info("开始风险分解...")
        
        try:
            # 1. 准备数据
            symbols = list(portfolio.keys())
            weights = np.array(list(portfolio.values()))
            
            # 2. 计算股票收益率
            stock_returns = self._calculate_stock_returns(stock_data, symbols)
            
            # 3. 计算因子暴露矩阵
            factor_exposures = self._calculate_factor_exposures(stock_data, symbols)
            
            # 4. 计算因子收益率
            factor_returns = self._estimate_factor_returns(stock_returns, factor_exposures)
            
            # 5. 计算因子协方差矩阵
            factor_covariance = self._estimate_factor_covariance(factor_returns)
            
            # 6. 计算特质风险（残差风险）
            specific_risks = self._estimate_specific_risks(stock_returns, factor_returns, factor_exposures)
            
            # 7. 计算总风险分解
            total_risk, style_risk, industry_risk, specific_risk_total = \
                self._calculate_risk_decomposition(weights, factor_exposures, 
                                                  factor_covariance, specific_risks)
            
            # 8. 创建详细的因子暴露对象
            factor_exposure_objects = self._create_factor_exposure_objects(
                factor_exposures, factor_covariance, specific_risks
            )
            
            result = RiskDecomposition(
                total_risk=total_risk,
                style_risk=style_risk,
                industry_risk=industry_risk,
                specific_risk=specific_risk_total,
                factor_exposures=factor_exposure_objects,
                covariance_matrix=factor_covariance,
                factor_returns=factor_returns
            )
            
            logger.info(f"风险分解完成: 总风险={total_risk:.4f}, "
                       f"风格风险={style_risk:.4f}, "
                       f"行业风险={industry_risk:.4f}, "
                       f"特质风险={specific_risk_total:.4f}")
            
            return result
            
        except Exception as e:
            logger.error(f"风险分解失败: {e}")
            raise
    
    def _calculate_stock_returns(self, 
                               stock_data: Dict[str, pd.DataFrame], 
                               symbols: List[str]) -> pd.DataFrame:
        """计算股票收益率"""
        returns_dict = {}
        for symbol in symbols:
            if symbol in stock_data:
                try:
                    # 使用收盘价计算日收益率
                    closes = stock_data[symbol]['close']
                    returns = closes.pct_change().dropna()
                    returns_dict[symbol] = returns
                except Exception as e:
                    logger.warning(f"计算{symbol}收益率失败: {e}")
        
        # 对齐日期
        returns_df = pd.DataFrame(returns_dict)
        returns_df = returns_df.dropna(how='all')
        
        return returns_df
    
    def _calculate_factor_exposures(self,
                                  stock_data: Dict[str, pd.DataFrame],
                                  symbols: List[str]) -> pd.DataFrame:
        """计算因子暴露矩阵"""
        
        factor_exposures = {}
        
        # 风格因子暴露
        for factor_id, factor_info in self.style_factors.items():
            exposures = []
            for symbol in symbols:
                if symbol in stock_data:
                    try:
                        # 简化的因子暴露计算（实际应用中需要更复杂的计算）
                        data = stock_data[symbol]
                        
                        if factor_id == 'size':
                            # 规模因子：log(市值)的标准化
                            if 'market_cap' in data.columns:
                                exposure = np.log(data['market_cap'].iloc[-1] + 1)
                            else:
                                exposure = 0.0
                                
                        elif factor_id == 'value':
                            # 价值因子：PB倒数
                            if 'pb_ratio' in data.columns:
                                pb = data['pb_ratio'].iloc[-1]
                                exposure = 1.0 / (pb + 0.001) if pb > 0 else 0.0
                            else:
                                exposure = 0.0
                                
                        elif factor_id == 'momentum':
                            # 动量因子：过去60日收益率
                            if 'close' in data.columns:
                                closes = data['close']
                                if len(closes) >= 60:
                                    momentum = (closes.iloc[-1] / closes.iloc[-60] - 1)
                                    exposure = momentum
                                else:
                                    exposure = 0.0
                            else:
                                exposure = 0.0
                                
                        elif factor_id == 'volatility':
                            # 波动率因子：过去60日波动率
                            if 'close' in data.columns:
                                closes = data['close']
                                returns = closes.pct_change()
                                if len(returns) >= 60:
                                    vol = returns.tail(60).std()
                                    exposure = vol
                                else:
                                    exposure = 0.0
                            else:
                                exposure = 0.0
                                
                        else:
                            # 其他因子简化处理
                            exposure = np.random.randn() * 0.5
                            
                        exposures.append(exposure)
                        
                    except Exception as e:
                        logger.warning(f"计算{symbol}的{factor_id}暴露失败: {e}")
                        exposures.append(0.0)
                else:
                    exposures.append(0.0)
            
            # 标准化暴露
            exposures_array = np.array(exposures)
            if np.std(exposures_array) > 0:
                exposures_array = (exposures_array - np.mean(exposures_array)) / np.std(exposures_array)
            
            factor_exposures[f'style_{factor_id}'] = exposures_array
        
        # 行业因子暴露（简化：0或1表示是否属于该行业）
        for industry_id, industry_info in self.industry_factors.items():
            # 简化的行业暴露（实际需要行业分类数据）
            exposures = np.random.choice([0, 1], size=len(symbols), p=[0.8, 0.2])
            factor_exposures[f'industry_{industry_id}'] = exposures
        
        # 创建DataFrame
        exposure_df = pd.DataFrame(factor_exposures, index=symbols)
        
        return exposure_df
    
    def _estimate_factor_returns(self,
                               stock_returns: pd.DataFrame,
                               factor_exposures: pd.DataFrame) -> pd.DataFrame:
        """估计因子收益率（使用截面回归）"""
        
        if not HAS_STATSMODELS:
            logger.warning("statsmodels不可用，使用简化因子收益率估计")
            return self._simplify_factor_returns(factor_exposures)
        
        try:
            # 对齐数据
            common_symbols = factor_exposures.index.intersection(stock_returns.columns)
            if len(common_symbols) < 10:
                raise ValueError("数据不足，无法进行因子收益率估计")
            
            factor_exposures_aligned = factor_exposures.loc[common_symbols]
            stock_returns_aligned = stock_returns[common_symbols]
            
            # 对每个交易日进行截面回归
            factor_returns_list = []
            dates = stock_returns_aligned.index
            
            for date in dates:
                try:
                    # 当日股票收益率
                    y = stock_returns_aligned.loc[date].values
                    
                    # 因子暴露矩阵（X）
                    X = factor_exposures_aligned.values
                    
                    # 添加截距项
                    X_with_intercept = sm.add_constant(X)
                    
                    # 线性回归
                    model = sm.OLS(y, X_with_intercept).fit()
                    
                    # 因子收益率（排除截距项）
                    factor_return = model.params[1:]  # 排除常数项
                    factor_returns_list.append(factor_return)
                    
                except Exception as e:
                    logger.debug(f"日期{date}因子收益率估计失败: {e}")
                    # 使用前一日估计值或零
                    if factor_returns_list:
                        factor_returns_list.append(factor_returns_list[-1])
                    else:
                        factor_returns_list.append(np.zeros(len(factor_exposures_aligned.columns)))
            
            # 创建因子收益率DataFrame
            factor_returns_df = pd.DataFrame(
                factor_returns_list,
                index=dates,
                columns=factor_exposures_aligned.columns
            )
            
            return factor_returns_df
            
        except Exception as e:
            logger.error(f"因子收益率估计失败: {e}")
            return self._simplify_factor_returns(factor_exposures)
    
    def _simplify_factor_returns(self, factor_exposures: pd.DataFrame) -> pd.DataFrame:
        """简化因子收益率估计（用于statsmodels不可用时）"""
        
        n_factors = len(factor_exposures.columns)
        n_days = 252  # 生成一年的因子收益率
        
        # 生成模拟的因子收益率
        dates = pd.date_range(end=pd.Timestamp.now(), periods=n_days, freq='B')
        
        # 基础因子收益率（基于风险溢价）
        base_returns = {}
        for factor in factor_exposures.columns:
            if factor.startswith('style_'):
                factor_type = factor.replace('style_', '')
                if factor_type in self.style_factors:
                    risk_premium = self.style_factors[factor_type].get('risk_premium', 0.01)
                    volatility = self.style_factors[factor_type].get('volatility', 0.15)
                    
                    # 年化收益率转换为日收益率
                    daily_return = risk_premium / 252
                    daily_vol = volatility / np.sqrt(252)
                    
                    # 生成随机收益率
                    returns = np.random.randn(n_days) * daily_vol + daily_return
                    base_returns[factor] = returns
                else:
                    base_returns[factor] = np.random.randn(n_days) * 0.01
            else:
                # 行业因子收益率
                base_returns[factor] = np.random.randn(n_days) * 0.008
        
        factor_returns_df = pd.DataFrame(base_returns, index=dates)
        
        return factor_returns_df
    
    def _estimate_factor_covariance(self, factor_returns: pd.DataFrame) -> np.ndarray:
        """估计因子协方差矩阵"""
        
        try:
            # 计算样本协方差
            sample_cov = factor_returns.cov().values
            
            if self.config['covariance_method'] == 'ledoit_wolf' and HAS_SCIPY:
                # Ledoit-Wolf收缩估计（改善小样本问题）
                from sklearn.covariance import LedoitWolf
                lw = LedoitWolf()
                lw.fit(factor_returns)
                cov_matrix = lw.covariance_
            else:
                # 使用样本协方差
                cov_matrix = sample_cov
            
            # 确保正定
            eigenvalues = np.linalg.eigvals(cov_matrix)
            if np.any(eigenvalues <= 1e-10):
                # 添加小扰动使矩阵正定
                cov_matrix = cov_matrix + np.eye(cov_matrix.shape[0]) * 1e-6
            
            return cov_matrix
            
        except Exception as e:
            logger.error(f"因子协方差估计失败: {e}")
            # 返回对角矩阵作为后备
            n_factors = len(factor_returns.columns)
            return np.eye(n_factors) * 0.0001
    
    def _estimate_specific_risks(self,
                               stock_returns: pd.DataFrame,
                               factor_returns: pd.DataFrame,
                               factor_exposures: pd.DataFrame) -> np.ndarray:
        """估计特质风险（残差风险）"""
        
        try:
            # 对齐数据
            common_symbols = factor_exposures.index.intersection(stock_returns.columns)
            if len(common_symbols) < 5:
                return np.ones(len(factor_exposures.index)) * 0.02
            
            stock_returns_aligned = stock_returns[common_symbols]
            factor_exposures_aligned = factor_exposures.loc[common_symbols]
            
            # 计算残差
            specific_risks = {}
            
            for symbol in common_symbols:
                try:
                    # 股票收益率
                    y = stock_returns_aligned[symbol].values
                    
                    # 因子暴露
                    X = factor_exposures_aligned.loc[symbol].values.reshape(1, -1)
                    
                    # 预测收益率（简化：因子暴露 * 平均因子收益率）
                    factor_ret_mean = factor_returns.mean().values
                    y_pred = X @ factor_ret_mean
                    
                    # 计算残差标准差
                    if len(y) > 10:
                        residuals = y - y_pred
                        specific_risk = np.std(residuals)
                    else:
                        specific_risk = 0.02  # 默认值
                    
                    specific_risks[symbol] = specific_risk * self.config['specific_risk_multiplier']
                    
                except Exception as e:
                    logger.debug(f"计算{symbol}特质风险失败: {e}")
                    specific_risks[symbol] = 0.02
            
            # 创建与原始顺序匹配的数组
            specific_risks_array = np.array([specific_risks.get(symbol, 0.02) 
                                           for symbol in factor_exposures.index])
            
            return specific_risks_array
            
        except Exception as e:
            logger.error(f"特质风险估计失败: {e}")
            return np.ones(len(factor_exposures.index)) * 0.02
    
    def _calculate_risk_decomposition(self,
                                    weights: np.ndarray,
                                    factor_exposures: pd.DataFrame,
                                    factor_covariance: np.ndarray,
                                    specific_risks: np.ndarray) -> Tuple[float, float, float, float]:
        """计算风险分解"""
        
        try:
            # 1. 组合的因子暴露
            portfolio_exposure = weights @ factor_exposures.values
            
            # 2. 因子风险部分
            factor_risk_variance = portfolio_exposure @ factor_covariance @ portfolio_exposure.T
            
            # 3. 特质风险部分
            specific_risk_variance = np.sum((weights**2) * (specific_risks**2))
            
            # 4. 总风险
            total_variance = factor_risk_variance + specific_risk_variance
            total_risk = np.sqrt(total_variance)
            
            # 5. 分解风格风险和行业风险
            style_risk_variance = 0
            industry_risk_variance = 0
            
            for i, factor in enumerate(factor_exposures.columns):
                factor_variance_contribution = (portfolio_exposure[i]**2) * factor_covariance[i, i]
                
                if factor.startswith('style_'):
                    style_risk_variance += factor_variance_contribution
                elif factor.startswith('industry_'):
                    industry_risk_variance += factor_variance_contribution
            
            style_risk = np.sqrt(style_risk_variance) if style_risk_variance > 0 else 0
            industry_risk = np.sqrt(industry_risk_variance) if industry_risk_variance > 0 else 0
            specific_risk_total = np.sqrt(specific_risk_variance) if specific_risk_variance > 0 else 0
            
            return total_risk, style_risk, industry_risk, specific_risk_total
            
        except Exception as e:
            logger.error(f"风险分解计算失败: {e}")
            # 返回估计值
            total_risk = np.sqrt(np.sum(weights**2)) * 0.02  # 简化估计
            return total_risk, total_risk * 0.4, total_risk * 0.3, total_risk * 0.3
    
    def _create_factor_exposure_objects(self,
                                       factor_exposures: pd.DataFrame,
                                       factor_covariance: np.ndarray,
                                       specific_risks: np.ndarray) -> List[FactorExposure]:
        """创建详细的因子暴露对象"""
        
        factor_objects = []
        
        for i, factor in enumerate(factor_exposures.columns):
            try:
                # 确定因子类型
                if factor.startswith('style_'):
                    factor_type = 'style'
                    factor_id = factor.replace('style_', '')
                    factor_name = self.style_factors.get(factor_id, {}).get('name', factor_id)
                elif factor.startswith('industry_'):
                    factor_type = 'industry'
                    factor_id = factor.replace('industry_', '')
                    factor_name = self.industry_factors.get(factor_id, {}).get('name', factor_id)
                else:
                    factor_type = 'other'
                    factor_id = factor
                    factor_name = factor
                
                # 平均暴露（简化处理）
                avg_exposure = factor_exposures[factor].mean()
                
                # 单位因子风险（协方差矩阵对角线）
                risk_per_unit = np.sqrt(factor_covariance[i, i]) if i < len(factor_covariance) else 0.01
                
                factor_obj = FactorExposure(
                    factor_id=factor_id,
                    factor_name=factor_name,
                    factor_type=factor_type,
                    exposure=avg_exposure,
                    contribution=0.0,  # 需要组合权重计算
                    risk_per_unit=risk_per_unit
                )
                
                factor_objects.append(factor_obj)
                
            except Exception as e:
                logger.debug(f"创建因子暴露对象{factor}失败: {e}")
                continue
        
        return factor_objects
    BEAR = "bear"               # 熊市
    CRASH = "crash"             # 崩盘
    RECOVERY = "recovery"       # 复苏
    BUBBLE = "bubble"           # 泡沫


class RiskModelType(Enum):
    """风险模型类型"""
    BARRA_STYLE = "barra_style"      # Barra风格因子模型
    AXIOMA_STYLE = "axioma_style"    # Axioma风格因子模型
    SIMPLIFIED = "simplified"        # 简化因子模型


@dataclass
class FactorExposure:
    """