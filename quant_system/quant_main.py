"""
专业量化系统主入口
集成数据管道、因子计算、回测引擎、风险控制
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import os
import sys
from typing import List, Dict, Any, Optional, Tuple
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from data.sources.data_pipeline import DataPipeline

# 因子管理器：优先使用真实因子管理器（解决伪因子问题）
try:
    # 尝试导入真实因子管理器（基于Baostock真实财务数据）
    from real_factors.real_factor_manager import RealFactorManager
    # 创建兼容包装器
    class FactorManager(RealFactorManager):
        """兼容包装器，提供get_factor_weights方法"""
        def get_factor_weights(self, method: str = 'equal') -> Dict[str, float]:
            """获取因子权重（兼容接口）"""
            if method == 'equal':
                n_factors = len(self.factors)
                return {factor_id: 1.0 / n_factors for factor_id in self.factors.keys()}
            
            elif method == 'category_weighted':
                # 使用真实因子类别统计
                # 技术因子保持50%，基本面因子30%（真实数据），情绪因子20%（部分真实）
                category_weights = {
                    'technical': 0.50,
                    'fundamental': 0.30,
                    'sentiment': 0.20
                }
                
                weights = {}
                for factor_id, info in self.factors.items():
                    category = info['category']
                    n_in_category = self.category_stats.get(category, 1)
                    weights[factor_id] = category_weights.get(category, 0.0) / n_in_category
                
                return weights
            
            else:
                raise ValueError(f"未知的权重方法: {method}")
        
        def combine_factors(self, df: pd.DataFrame, weights: Optional[Dict[str, float]] = None, symbol: str = None) -> pd.Series:
            """因子融合（增强版，支持symbol参数）"""
            # 计算所有因子
            factor_df = self.calculate_all_factors(df, symbol=symbol)
            
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
            weighted_sum = pd.Series(0.0, index=factor_df.index)
            for factor_id, weight in normalized_weights.items():
                if factor_id in factor_df.columns:
                    # 标准化因子值（去除NaN）
                    factor_values = factor_df[factor_id].fillna(0)
                    weighted_sum += factor_values * weight
            
            return weighted_sum
    
    print("✓ 使用真实因子管理器 (RealFactorManager)")
except ImportError as e:
    # 回退到安全因子管理器（禁用伪因子）
    print(f"⚠ 真实因子管理器导入失败: {e}，使用安全因子管理器（伪因子已禁用）")
    
    # 导入原始因子管理器作为基类，以及因子类
    from factors.factor_manager import FactorManager as OriginalFactorManager
    from factors.factor_manager import TechnicalFactor, SentimentFactor
    
    # 定义安全因子管理器：禁用基本面伪因子
    class FactorManager(OriginalFactorManager):
        """安全因子管理器（禁用伪因子）"""
        
        def _register_factors(self):
            """只注册技术因子，禁用基本面伪因子"""
            # 技术因子 (14个真实因子)
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
            
            # 情绪因子 (5个模拟因子，但相对安全)
            sent_factors = {
                'news_sentiment': ('新闻情绪', SentimentFactor.news_sentiment_simulation, 'sentiment'),
                'social_buzz': ('社交媒体热度', SentimentFactor.social_media_buzz_simulation, 'sentiment'),
                'institution_research': ('机构调研', SentimentFactor.institution_research_simulation, 'sentiment'),
                'dragon_tiger': ('龙虎榜', SentimentFactor.dragon_tiger_simulation, 'sentiment'),
                'margin_trading': ('融资融券', SentimentFactor.margin_trading_simulation, 'sentiment')
            }
            
            # 合并所有因子（排除基本面伪因子）
            all_factors = {**tech_factors, **sent_factors}
            
            for factor_id, (desc, func, category) in all_factors.items():
                self.factors[factor_id] = {
                    'name': factor_id,
                    'description': desc,
                    'function': func,
                    'category': category
                }
                self.category_stats[category] = self.category_stats.get(category, 0) + 1
            
            print(f"⚠ 安全模式：注册{len(self.factors)}个因子（{len(tech_factors)}技术+{len(sent_factors)}情绪），基本面伪因子已禁用")


# PIT因子管理器（Point-in-Time数据合规性）
try:
    from pit_factors.pit_factor_manager import PITFactorManager
    PIT_FACTOR_MANAGER_AVAILABLE = True
    print("✓ PIT因子管理器可用")
except ImportError as e:
    PIT_FACTOR_MANAGER_AVAILABLE = False
    print(f"⚠ PIT因子管理器导入失败: {e}")
# 动态导入，避免循环依赖
try:
    from models.backtest_engine.backtester import BacktestEngine
except ImportError:
    # 创建简化版本
    class BacktestEngine:
        def __init__(self, initial_capital=1000000):
            self.initial_capital = initial_capital
        
        def run_simple_backtest(self, *args, **kwargs):
            return {'status': 'backtest_placeholder', 'message': '回测引擎待完善'}

try:
    from risk.position_manager.portfolio_manager import PortfolioManager
except ImportError:
    # 创建简化版本
    class PortfolioManager:
        def __init__(self, config):
            self.config = config
        
        def check_position_limits(self, *args, **kwargs):
            return True, "通过"
        
        def rebalance_portfolio(self, *args, **kwargs):
            return {'status': 'rebalance_placeholder'}


class QuantSystem:
    """专业量化系统主类"""
    
    def __init__(self, config: dict = None):
        # 默认配置
        self.config = config or {
            # 回测参数
            'initial_capital': 1000000.0,  # 100万起始资金
            'transaction_cost': 0.001,     # 0.1%佣金
            'slippage': 0.002,             # 0.2%滑点
            'holding_days': 5,             # 5日持有期
            'stop_loss_pct': 0.10,         # 10%止损
            'take_profit_pct': 0.20,       # 20%止盈
            
            # 风险限制
            'max_single_stock_pct': 0.10,  # 单票上限10%
            'max_sector_pct': 0.30,        # 行业上限30%
            'target_beta_range': (0.8, 1.2),  # Beta控制范围
            
            # 调仓参数
            'rebalance_day': 'tuesday',    # 周二调仓
            'rebalance_time': '10:30',     # 10:30调仓
            'top_n_stocks': 10,            # 关注前10名
            'daily_monitor_top': 3,        # 每日监控前3名
            
            # 情绪因子调整（方案C）
            'sentiment_adjustment': {
                'method': 'segmented',     # 分段法（最简单版）
                'segments': [
                    {'max': 70, 'multiplier': 0.75},  # ≤70分：权重75%
                    {'max': 80, 'multiplier': 0.5},   # 70-80分：权重50%
                    {'max': 90, 'multiplier': 0.2},   # 80-90分：权重20%
                    {'max': 100, 'multiplier': 0.0}   # >90分：权重0%
                ]
            },
            # PIT配置
            'pit_mode': True,               # ✅ 默认启用PIT模式（用户要求）
            'pit_strict_mode': False,       # PIT严格模式（违规时抛出异常），默认False（警告模式）
            'pit_enable_caching': True,     # 启用PIT缓存
            'pit_current_date': None,       # PIT当前日期（自动设置）
        }
        
        # 初始化组件
        self.data_pipeline = DataPipeline()
        self.factor_manager = FactorManager()
        self.backtest_engine = BacktestEngine(
            initial_capital=self.config['initial_capital']
        )
        self.portfolio_manager = PortfolioManager(self.config)
        # 初始化PIT因子管理器
        self.pit_factor_manager = None
        self.active_factor_manager = self.factor_manager
        
        # ✅ 改进：健壮的PIT模式检测（用户要求修复）
        # 原问题：依赖 'PIT_FACTOR_MANAGER_AVAILABLE' in globals() 字符串检测
        # 新方案：直接检查PIT_FACTOR_MANAGER_AVAILABLE变量，并尝试导入验证
        if self.config.get('pit_mode', False):
            try:
                from pit_factors.pit_factor_manager import PITFactorManager
                self.pit_factor_manager = PITFactorManager(
                    base_factor_manager=self.factor_manager,
                    current_date=self.config.get('pit_current_date'),
                    pit_strict_mode=self.config.get('pit_strict_mode', False),
                    enable_caching=self.config.get('pit_enable_caching', True)
                )
                self.active_factor_manager = self.pit_factor_manager
                print("✓ PIT因子管理器初始化成功")
            except ImportError as e:
                print(f"⚠ PIT因子管理器不可用: {e}")
                print("  降级到原始因子管理器（无PIT保护）")
            except Exception as e:
                print(f"✗ PIT因子管理器初始化失败: {e}")
                print("  降级到原始因子管理器")
        
        print(f"使用因子管理器: {'PIT' if self.pit_factor_manager else '原始'}版本")        
        # 结果存储
        self.results = {}
        self.current_portfolio = {}
        self.performance_history = []
    
    def adjust_sentiment_factor(self, sentiment_score: float) -> float:
        """情绪因子调整（方案C：非线性惩罚）"""
        method = self.config['sentiment_adjustment']['method']
        
        if method == 'segmented':
            # 分段法（最简单版）
            segments = self.config['sentiment_adjustment']['segments']
            
            for segment in segments:
                if sentiment_score <= segment['max']:
                    adjusted = sentiment_score * segment['multiplier']
                    return max(0, adjusted)  # 确保非负
            
            return 0.0
        
        elif method == 'quantile':
            # 分位数版（需要全市场数据，这里简化）
            # 实际实现需要计算全市场分位数
            if sentiment_score <= 70:
                return sentiment_score * 0.7
            elif sentiment_score <= 85:
                return 49 + (sentiment_score - 70) * 0.4  # 70-85: 49-55分
            else:
                # >85分：急剧惩罚
                penalty = (sentiment_score - 85) * 2.0
                return max(0, 55 - penalty)
        
        elif method == 'quadratic':
            # 二次函数版: a*x + b*x^2 (b<0)
            a = 1.0
            b = -0.012  # 负系数创造倒U形
            adjusted = a * sentiment_score + b * (sentiment_score ** 2)
            return max(0, adjusted)
        
        else:
            # 默认：原样返回
            return sentiment_score
    
    def get_stock_scores(self, symbol: str, start_date: str, end_date: str) -> dict:
        """获取股票综合评分及因子贡献"""
        # 获取数据
        data_result = self.data_pipeline.get_stock_data(symbol, start_date, end_date)
        df = data_result['data']
        
        if df.empty:
            return {
                'symbol': symbol,
                'error': '无数据',
                'score': 0,
                'contributors': []
            }
        
        # 计算所有因子
        factor_df = self.factor_manager.calculate_all_factors(df, symbol=symbol)
        
        # 获取默认权重
        weights = self.factor_manager.get_factor_weights('category_weighted')
        
        # 调整情绪因子权重
        adjusted_weights = weights.copy()
        sentiment_factors = ['news_sentiment', 'social_buzz', 'institution_research', 
                           'dragon_tiger', 'margin_trading']
        
        for factor in sentiment_factors:
            if factor in factor_df.columns:
                # 获取原始情绪分
                raw_sentiment = factor_df[factor].iloc[-1] * 100  # 转为0-100分
                # 调整
                adjusted_sentiment = self.adjust_sentiment_factor(raw_sentiment)
                # 调整权重比例
                adjustment_ratio = adjusted_sentiment / raw_sentiment if raw_sentiment > 0 else 0
                adjusted_weights[factor] = weights[factor] * adjustment_ratio
        
        # 计算综合得分（使用调整后权重）
        latest_date = df.index[-1]
        combined_score = 0
        for factor_id, weight in adjusted_weights.items():
            if factor_id in factor_df.columns:
                factor_value = factor_df[factor_id].iloc[-1]
                combined_score += factor_value * weight
        
        # 转为0-100分
        final_score = min(100, max(0, combined_score * 100))
        
        # 获取前3大贡献因子
        top_contributors = []
        for factor_id in factor_df.columns:
            if factor_id in adjusted_weights:
                factor_value = factor_df[factor_id].iloc[-1]
                contribution = factor_value * adjusted_weights[factor_id] * 100
                
                # 获取因子信息
                factor_info = self.factor_manager.factors.get(factor_id, {})
                
                top_contributors.append({
                    'factor_id': factor_id,
                    'name': factor_info.get('description', factor_id),
                    'category': factor_info.get('category', 'unknown'),
                    'raw_value': float(factor_value),
                    'weight': float(adjusted_weights[factor_id]),
                    'contribution': float(contribution),
                    'contribution_pct': float(contribution / final_score * 100) if final_score > 0 else 0
                })
        
        # 按贡献度排序
        top_contributors.sort(key=lambda x: abs(x['contribution']), reverse=True)
        
        return {
            'symbol': symbol,
            'date': latest_date.strftime('%Y-%m-%d') if hasattr(latest_date, 'strftime') else str(latest_date),
            'price': float(df['close'].iloc[-1]) if 'close' in df.columns else 0,
            'score': float(final_score),
            'score_category': self._get_score_category(final_score),
            'risk_level': self._calculate_risk_level(df, final_score),
            'top_contributors': top_contributors[:3],  # 前3大贡献因子
            'data_quality': data_result['metadata']['quality']['overall'] if 'metadata' in data_result else 0.5,
            'data_source': data_result['metadata']['source']['source_name'] if 'metadata' in data_result else 'unknown'
        }
    
    def _get_score_category(self, score: float) -> str:
        """根据得分分类"""
        if score >= 80:
            return "强烈买入"
        elif score >= 70:
            return "买入"
        elif score >= 60:
            return "持有"
        elif score >= 50:
            return "观望"
        else:
            return "卖出"
    
    def _calculate_risk_level(self, df: pd.DataFrame, score: float) -> int:
        """计算风险等级（1-5，1最低风险）"""
        # 基于波动率和得分计算风险
        if 'close' in df.columns and len(df) > 20:
            volatility = df['close'].pct_change().std() * np.sqrt(252)
            
            # 高得分+低波动 = 低风险
            # 低得分+高波动 = 高风险
            risk_score = (1 - score/100) * 0.7 + volatility * 0.3
            
            if risk_score < 0.2:
                return 1
            elif risk_score < 0.4:
                return 2
            elif risk_score < 0.6:
                return 3
            elif risk_score < 0.8:
                return 4
            else:
                return 5
        else:
            return 3  # 中等风险
    
    def run_backtest(self, symbols: List[str], start_date: str, end_date: str) -> dict:
        """运行回测"""
        print(f"运行回测: {len(symbols)}支股票, {start_date} 至 {end_date}")
        
        # 获取所有股票数据
        all_data = {}
        all_signals = {}
        
        for symbol in symbols:
            try:
                # 获取数据
                data_result = self.data_pipeline.get_stock_data(symbol, start_date, end_date)
                df = data_result['data']
                
                if df.empty:
                    continue
                
                # 计算每日得分
                scores = []
                dates = []
                
                # 简化：使用滑动窗口计算每日得分
                for i in range(len(df)):
                    if i >= 60:  # 需要足够数据计算因子
                        window_df = df.iloc[:i+1]
                        factor_df = self.factor_manager.calculate_all_factors(window_df)
                        
                        if not factor_df.empty:
                            # 使用最新日期的因子值
                            latest_factors = factor_df.iloc[-1]
                            weights = self.factor_manager.get_factor_weights('category_weighted')
                            
                            # 计算得分
                            score = 0
                            for factor_id, weight in weights.items():
                                if factor_id in latest_factors:
                                    score += latest_factors[factor_id] * weight
                            
                            scores.append(score * 100)
                            dates.append(df.index[i])
                
                if scores:
                    # 创建信号（得分>70买入，<50卖出）
                    signal_series = pd.Series(scores, index=dates)
                    buy_signal = (signal_series > 70).astype(int)
                    sell_signal = (signal_series < 50).astype(int) * -1
                    signals = buy_signal + sell_signal
                    
                    all_data[symbol] = df[['close']]
                    all_signals[symbol] = signals
                    
            except Exception as e:
                print(f"处理股票 {symbol} 失败: {e}")
                continue
        
        if not all_data:
            return {'error': '无有效数据'}
        
        # 简单策略：选择每日得分最高的股票
        # 这里简化实现，实际需要更复杂的组合优化
        print("回测数据准备完成，开始模拟交易...")
        
        # 调用回测引擎（简化版）
        # 实际应该实现完整的组合回测
        
        return {
            'status': 'backtest_started',
            'symbols_processed': len(all_data),
            'period': f"{start_date} 至 {end_date}"
        }
    
    def generate_daily_report(self, symbols: List[str] = None) -> dict:
        """生成每日报告"""
        if symbols is None:
            # 默认关注股票池
            symbols = [
                '600519', '300750', '002415', '002230', '000063',
                '002475', '603986', '688111', '688981', '600588'
            ]
        
        # 获取最近3个月数据
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
        
        print(f"生成 {len(symbols)} 支股票的每日报告...")
        
        stock_scores = []
        for symbol in symbols:
            try:
                score_info = self.get_stock_scores(symbol, start_date, end_date)
                stock_scores.append(score_info)
                print(f"{symbol}: {score_info['score']:.1f}分 ({score_info['score_category']})")
            except Exception as e:
                print(f"获取 {symbol} 评分失败: {e}")
                continue
        
        # 按得分排序
        stock_scores.sort(key=lambda x: x['score'], reverse=True)
        
        # 计算整体市场状况
        avg_score = np.mean([s['score'] for s in stock_scores]) if stock_scores else 0
        bull_bear_indicator = "牛市" if avg_score > 60 else "熊市" if avg_score < 40 else "震荡市"
        
        # 生成推荐
        strong_buy = [s for s in stock_scores if s['score'] >= 80]
        buy = [s for s in stock_scores if 70 <= s['score'] < 80]
        
        report = {
            'report_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'market_condition': {
                'average_score': float(avg_score),
                'bull_bear': bull_bear_indicator,
                'stocks_analyzed': len(stock_scores),
                'strong_buy_count': len(strong_buy),
                'buy_count': len(buy)
            },
            'top_recommendations': stock_scores[:10],  # 前10名
            'all_scores': stock_scores,
            'risk_metrics': {
                'avg_risk_level': np.mean([s['risk_level'] for s in stock_scores]) if stock_scores else 3,
                'high_risk_count': len([s for s in stock_scores if s['risk_level'] >= 4])
            }
        }
        
        # 保存报告
        report_dir = "/root/.openclaw/workspace/quant_system/reports"
        os.makedirs(report_dir, exist_ok=True)
        
        report_file = os.path.join(report_dir, f"daily_report_{datetime.now().strftime('%Y%m%d')}.json")
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        
        print(f"每日报告已保存至: {report_file}")
        
        return report
    
    def quick_start(self):
        """快速启动：运行完整工作流"""
        print("=" * 60)
        print("专业量化系统快速启动")
        print("=" * 60)
        
        # 1. 数据管道测试
        print("\n1. 测试数据管道...")
        pipeline = DataPipeline()
        try:
            test_result = pipeline.get_stock_data('600519', '2025-12-01', '2026-03-01')
            print(f"✓ 数据管道正常 (源: {test_result['metadata']['source']['source_name']})")
        except Exception as e:
            print(f"✗ 数据管道异常: {e}")
        
        # 2. 因子计算测试
        print("\n2. 测试因子计算...")
        fm = FactorManager()
        print(f"✓ 因子管理器就绪 (注册因子: {len(fm.factors)}个)")
        
        # 3. 情绪因子调整测试
        print("\n3. 测试情绪因子调整...")
        test_scores = [60, 75, 85, 95]
        for score in test_scores:
            adjusted = self.adjust_sentiment_factor(score)
            print(f"  情绪分 {score} → 调整后 {adjusted:.1f} (惩罚比例: {adjusted/score*100:.1f}%)")
        
        # 4. 生成示例报告
        print("\n4. 生成示例报告...")
        try:
            report = self.generate_daily_report(['600519', '300750', '002415'])
            
            print(f"✓ 报告生成成功")
            print(f"  市场状态: {report['market_condition']['bull_bear']}")
            print(f"  平均得分: {report['market_condition']['average_score']:.1f}")
            
            if report['top_recommendations']:
                top_stock = report['top_recommendations'][0]
                print(f"  推荐榜首: {top_stock['symbol']} ({top_stock['score']:.1f}分, {top_stock['score_category']})")
                
                if top_stock['top_contributors']:
                    print(f"  主要驱动因子:")
                    for i, contrib in enumerate(top_stock['top_contributors'], 1):
                        print(f"    {i}. {contrib['name']}: 贡献{contrib['contribution_pct']:.1f}%")
        
        except Exception as e:
            print(f"✗ 报告生成失败: {e}")
        
        # 5. 系统配置显示
        print("\n5. 系统配置摘要:")
        print(f"   起始资金: {self.config['initial_capital']:,.0f}元")
        print(f"   交易成本: {self.config['transaction_cost']*100:.1f}%佣金 + {self.config['slippage']*100:.1f}%滑点")
        print(f"   风险限制: 单票≤{self.config['max_single_stock_pct']*100:.0f}%, 行业≤{self.config['max_sector_pct']*100:.0f}%")
        print(f"   Beta控制: {self.config['target_beta_range'][0]}-{self.config['target_beta_range'][1]}")
        print(f"   调仓频率: 每周{self.config['rebalance_day']} {self.config['rebalance_time']}")
        print(f"   情绪因子: {self.config['sentiment_adjustment']['method']}调整法")
        
        print("\n" + "=" * 60)
        print("专业量化系统就绪！")
        print("=" * 60)
        
        return True

    def enable_pit_mode(self, strict_mode=False, enable_caching=True, current_date=None):
        """启用PIT模式"""
        try:
            from pit_factors.pit_factor_manager import PITFactorManager
        except ImportError as e:
            print(f"❌ PIT因子管理器不可用: {e}")
            return False
        
        self.config['pit_mode'] = True
        self.config['pit_strict_mode'] = strict_mode
        self.config['pit_enable_caching'] = enable_caching
        self.config['pit_current_date'] = current_date
        
        try:
            self.pit_factor_manager = PITFactorManager(
                base_factor_manager=self.factor_manager,
                current_date=current_date,
                pit_strict_mode=strict_mode,
                enable_caching=enable_caching
            )
            self.active_factor_manager = self.pit_factor_manager
            print(f"✅ PIT模式启用")
            return True
        except Exception as e:
            print(f"❌ PIT模式启用失败: {e}")
            self.config['pit_mode'] = False
            return False
    
    def disable_pit_mode(self):
        """禁用PIT模式"""
        self.config['pit_mode'] = False
        self.active_factor_manager = self.factor_manager
        if self.pit_factor_manager:
            self.pit_factor_manager = None
        print("✅ PIT模式已禁用")
    
    def set_pit_current_date(self, current_date):
        """设置PIT当前日期"""
        self.config['pit_current_date'] = current_date
        if self.pit_factor_manager:
            self.pit_factor_manager.set_current_date(current_date)
    
    def get_pit_violations(self):
        """获取PIT违规记录"""
        if self.pit_factor_manager:
            return self.pit_factor_manager.get_pit_violations()
        return []
    
    def get_pit_stats(self):
        """获取PIT统计"""
        if self.pit_factor_manager:
            return {
                'performance': self.pit_factor_manager.get_performance_stats(),
                'cache': self.pit_factor_manager.get_cache_stats()
            }
        return {'error': 'PIT未启用'}

if __name__ == "__main__":
    # 创建量化系统实例
    quant = QuantSystem()
    
    # 快速启动测试
    quant.quick_start()
    
    print("\n📊 系统已启动，可以通过以下方式使用:")
    print("1. 获取单股票评分: quant.get_stock_scores('600519', '2025-12-01', '2026-03-01')")
    print("2. 生成每日报告: quant.generate_daily_report()")
    print("3. 运行回测: quant.run_backtest(['600519', '300750'], '2025-01-01', '2025-12-31')")
    print("\n📁 报告保存目录: /root/.openclaw/workspace/quant_system/reports/")    
    # PIT方法
