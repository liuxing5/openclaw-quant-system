#!/usr/bin/env python3
"""
系统集成控制器 - 端到端量化工作流协调器
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any, Optional
import json
import sys
import os
import warnings
warnings.filterwarnings('ignore')

# 添加路径
sys.path.append('/root/.openclaw/workspace/quant_system')

class SystemIntegrationController:
    """系统集成控制器"""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or self._default_config()
        
        # 初始化各个模块
        self._init_modules()
        
        # 工作流状态
        self.workflow_state = {
            'current_step': None,
            'start_time': None,
            'end_time': None,
            'results': {},
            'errors': []
        }
    
    def _default_config(self) -> Dict[str, Any]:
        """默认配置"""
        return {
            # 数据配置
            'data': {
                'default_start_date': '2023-01-01',
                'default_end_date': '2024-12-31',
                'default_symbols': ['600519', '000001', '300750', '002415', '002230'],
                'cache_enabled': True,
                'cache_ttl': 3600  # 1小时
            },
            
            # 回测配置
            'backtest': {
                'initial_capital': 1000000.0,
                'commission': 0.001,
                'slippage': 0.002,
                'default_strategy': 'simple_moving_average'
            },
            
            # 风险配置
            'risk': {
                'var_confidence_level': 0.95,
                'stress_scenarios': ['2008_financial_crisis', '2022_small_cap_crash'],
                'style_factor_source': 'barra'
            },
            
            # 情绪因子配置
            'sentiment': {
                'decay_half_life': 10,
                'market_state_window': 60
            },
            
            # 工作流配置
            'workflow': {
                'enable_parallel': True,
                'max_workers': 4,
                'log_level': 'INFO'
            }
        }
    
    def _init_modules(self):
        """初始化各个模块"""
        print("初始化系统模块...")
        
        modules_status = {}
        
        try:
            # 数据管道
            from data.sources.data_pipeline import DataPipeline
            self.data_pipeline = DataPipeline()
            modules_status['data_pipeline'] = '✅ 已加载'
            print("  ✅ 数据管道模块已加载")
        except Exception as e:
            modules_status['data_pipeline'] = f'❌ 加载失败: {e}'
            print(f"  ❌ 数据管道模块加载失败: {e}")
            self.data_pipeline = None
        
        try:
            # 高级回测器
            from advanced_backtest.advanced_backtester import AdvancedBacktester
            self.backtester = AdvancedBacktester(
                initial_capital=self.config['backtest']['initial_capital'],
                commission=self.config['backtest']['commission'],
                slippage=self.config['backtest']['slippage']
            )
            modules_status['backtester'] = '✅ 已加载'
            print("  ✅ 高级回测器模块已加载")
        except Exception as e:
            modules_status['backtester'] = f'❌ 加载失败: {e}'
            print(f"  ❌ 高级回测器模块加载失败: {e}")
            self.backtester = None
        
        try:
            # 高级风险管理系统
            from advanced_risk.advanced_risk_manager import AdvancedRiskManager
            self.risk_manager = AdvancedRiskManager(self.config['risk'])
            modules_status['risk_manager'] = '✅ 已加载'
            print("  ✅ 高级风险管理系统模块已加载")
        except Exception as e:
            modules_status['risk_manager'] = f'❌ 加载失败: {e}'
            print(f"  ❌ 高级风险管理系统模块加载失败: {e}")
            self.risk_manager = None
        
        try:
            # 精细化情绪因子
            from advanced_sentiment.refined_sentiment import RefinedSentimentFactor
            self.sentiment_calculator = RefinedSentimentFactor(self.config['sentiment'])
            modules_status['sentiment_calculator'] = '✅ 已加载'
            print("  ✅ 精细化情绪因子模块已加载")
        except Exception as e:
            modules_status['sentiment_calculator'] = f'❌ 加载失败: {e}'
            print(f"  ❌ 精细化情绪因子模块加载失败: {e}")
            self.sentiment_calculator = None
        
        try:
            # 数据库管理器
            from data.database.database_manager import DatabaseManager
            self.database = DatabaseManager()
            modules_status['database'] = '✅ 已加载'
            print("  ✅ 数据库管理器模块已加载")
        except Exception as e:
            modules_status['database'] = f'❌ 加载失败: {e}'
            print(f"  ❌ 数据库管理器模块加载失败: {e}")
            self.database = None
        
        self.modules_status = modules_status
    
    def check_system_health(self) -> Dict[str, Any]:
        """检查系统健康状态"""
        print("\n" + "=" * 60)
        print("系统健康检查")
        print("=" * 60)
        
        health_status = {
            'overall': 'healthy',
            'modules': self.modules_status,
            'timestamp': datetime.now().isoformat()
        }
        
        # 检查各个模块
        for module_name, status in self.modules_status.items():
            if '❌' in status:
                health_status['overall'] = 'unhealthy'
                print(f"  ❌ {module_name}: {status}")
            else:
                print(f"  ✅ {module_name}: 正常")
        
        if health_status['overall'] == 'healthy':
            print("\n✅ 系统健康状态: 正常")
        else:
            print("\n❌ 系统健康状态: 异常")
        
        return health_status
    
    def run_end_to_end_workflow(self, 
                               symbols: List[str] = None,
                               start_date: str = None,
                               end_date: str = None,
                               workflow_steps: List[str] = None) -> Dict[str, Any]:
        """
        运行端到端工作流
        
        Args:
            symbols: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            workflow_steps: 工作流步骤列表
        
        Returns:
            工作流执行结果
        """
        print("=" * 80)
        print("运行端到端量化工作流")
        print("=" * 80)
        
        # 设置参数默认值
        symbols = symbols or self.config['data']['default_symbols']
        start_date = start_date or self.config['data']['default_start_date']
        end_date = end_date or self.config['data']['default_end_date']
        
        # 默认工作流步骤
        if workflow_steps is None:
            workflow_steps = [
                'data_acquisition',
                'market_state_analysis',
                'sentiment_calculation',
                'risk_assessment',
                'strategy_backtest',
                'performance_evaluation'
            ]
        
        # 初始化工作流状态
        self.workflow_state = {
            'current_step': None,
            'start_time': datetime.now(),
            'end_time': None,
            'results': {},
            'errors': [],
            'parameters': {
                'symbols': symbols,
                'start_date': start_date,
                'end_date': end_date,
                'workflow_steps': workflow_steps
            }
        }
        
        print(f"工作流参数:")
        print(f"  股票: {symbols}")
        print(f"  期间: {start_date} 至 {end_date}")
        print(f"  步骤: {workflow_steps}")
        print()
        
        # 执行各个步骤
        for step in workflow_steps:
            try:
                self._execute_workflow_step(step, symbols, start_date, end_date)
            except Exception as e:
                error_msg = f"步骤 {step} 执行失败: {e}"
                print(f"  ❌ {error_msg}")
                self.workflow_state['errors'].append(error_msg)
                
                # 根据错误严重程度决定是否继续
                if step in ['data_acquisition', 'risk_assessment']:
                    print(f"  ⚠️  关键步骤失败，停止工作流")
                    break
        
        # 完成工作流
        self.workflow_state['end_time'] = datetime.now()
        duration = (self.workflow_state['end_time'] - self.workflow_state['start_time']).total_seconds()
        
        print("\n" + "=" * 80)
        print("工作流执行完成")
        print("=" * 80)
        print(f"总耗时: {duration:.1f}秒")
        print(f"成功步骤: {len(self.workflow_state['results'])}/{len(workflow_steps)}")
        print(f"错误数: {len(self.workflow_state['errors'])}")
        
        # 生成总结报告
        summary = self._generate_workflow_summary()
        
        return {
            'workflow_state': self.workflow_state,
            'summary': summary,
            'success': len(self.workflow_state['errors']) == 0
        }
    
    def _execute_workflow_step(self, 
                              step: str, 
                              symbols: List[str], 
                              start_date: str, 
                              end_date: str):
        """执行单个工作流步骤"""
        
        print(f"执行步骤: {step}")
        self.workflow_state['current_step'] = step
        
        if step == 'data_acquisition':
            result = self._step_data_acquisition(symbols, start_date, end_date)
        
        elif step == 'market_state_analysis':
            result = self._step_market_state_analysis(start_date, end_date)
        
        elif step == 'sentiment_calculation':
            result = self._step_sentiment_calculation(symbols, start_date, end_date)
        
        elif step == 'risk_assessment':
            result = self._step_risk_assessment(symbols, start_date, end_date)
        
        elif step == 'strategy_backtest':
            result = self._step_strategy_backtest(symbols, start_date, end_date)
        
        elif step == 'performance_evaluation':
            result = self._step_performance_evaluation()
        
        else:
            raise ValueError(f"未知的工作流步骤: {step}")
        
        # 保存结果
        self.workflow_state['results'][step] = result
        print(f"  ✅ 步骤完成: {step}")
        
        return result
    
    def _step_data_acquisition(self, symbols: List[str], start_date: str, end_date: str) -> Dict[str, Any]:
        """步骤1: 数据采集"""
        print("  数据采集...")
        
        if self.data_pipeline is None:
            raise RuntimeError("数据管道模块未加载")
        
        # 采集数据
        stock_data = {}
        data_stats = {}
        
        for symbol in symbols:
            try:
                print(f"    获取 {symbol} 数据...")
                result = self.data_pipeline.get_stock_data(
                    symbol, start_date, end_date, with_metadata=True
                )
                
                if result['success']:
                    stock_data[symbol] = result['data']
                    data_stats[symbol] = {
                        'rows': len(result['data']),
                        'source': result['source'],
                        'columns': list(result['data'].columns) if result['data'] is not None else []
                    }
                    print(f"      ✓ 成功获取 {len(result['data'])} 条数据")
                else:
                    print(f"      ✗ 获取失败: {result.get('error', '未知错误')}")
                    data_stats[symbol] = {'error': result.get('error')}
            
            except Exception as e:
                print(f"      ✗ 异常: {e}")
                data_stats[symbol] = {'error': str(e)}
        
        # 获取市场指数数据
        market_data = self._get_market_index_data(start_date, end_date)
        
        return {
            'stock_data': stock_data,
            'market_data': market_data,
            'data_stats': data_stats,
            'symbols_count': len(symbols),
            'successful_symbols': sum(1 for s in data_stats.values() if 'error' not in s)
        }
    
    def _step_market_state_analysis(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """步骤2: 市场状态分析"""
        print("  市场状态分析...")
        
        if self.sentiment_calculator is None:
            raise RuntimeError("情绪因子模块未加载")
        
        # 获取市场数据
        market_data = self._get_market_index_data(start_date, end_date)
        
        if market_data is None or market_data.empty:
            print("    ⚠️  无市场数据，使用默认状态")
            return {
                'market_state': 'consolidation',
                'state_params': {'penalty_factor': 1.0, 'threshold_adjust': 1.0},
                'data_points': 0,
                'analysis_date': end_date
            }
        
        # 分析市场状态
        market_state, state_params = self.sentiment_calculator.detect_market_state(
            market_data, end_date
        )
        
        print(f"    市场状态: {market_state}")
        print(f"    状态参数: {state_params}")
        
        return {
            'market_state': market_state,
            'state_params': state_params,
            'data_points': len(market_data),
            'analysis_date': end_date,
            'market_index_info': {
                'start_date': market_data.index[0].strftime('%Y-%m-%d') if not market_data.empty else None,
                'end_date': market_data.index[-1].strftime('%Y-%m-%d') if not market_data.empty else None,
                'price_change': self._calculate_price_change(market_data)
            }
        }
    
    def _step_sentiment_calculation(self, symbols: List[str], start_date: str, end_date: str) -> Dict[str, Any]:
        """步骤3: 情绪因子计算"""
        print("  情绪因子计算...")
        
        if self.sentiment_calculator is None:
            raise RuntimeError("情绪因子模块未加载")
        
        # 获取股票数据
        stock_data = {}
        for symbol in symbols:
            if self.data_pipeline:
                result = self.data_pipeline.get_stock_data(symbol, start_date, end_date, with_metadata=False)
                if result['success'] and result['data'] is not None:
                    stock_data[symbol] = result['data']
        
        if not stock_data:
            print("    ⚠️  无股票数据可用")
            return {'error': '无股票数据可用', 'stock_data_count': 0}
        
        # 获取市场数据
        market_data = self._get_market_index_data(start_date, end_date)
        
        # 计算情绪因子
        sentiment_result = self.sentiment_calculator.calculate_refined_sentiment(
            stock_data=stock_data,
            market_data=market_data,
            current_date=end_date
        )
        
        print(f"    计算完成: {len(sentiment_result.get('individual_results', {}))} 支股票")
        
        if 'summary' in sentiment_result:
            summary = sentiment_result['summary']
            print(f"    市场情绪: {summary.get('market_sentiment', '未知')}")
            print(f"    情绪均值: {summary.get('sentiment_stats', {}).get('mean', 0):.3f}")
        
        return sentiment_result
    
    def _step_risk_assessment(self, symbols: List[str], start_date: str, end_date: str) -> Dict[str, Any]:
        """步骤4: 风险评估"""
        print("  风险评估...")
        
        if self.risk_manager is None:
            raise RuntimeError("风险管理系统模块未加载")
        
        # 创建模拟组合（等权重）
        portfolio = {symbol: 1.0/len(symbols) for symbol in symbols}
        
        # 获取股票数据
        stock_data = {}
        for symbol in symbols:
            if self.data_pipeline:
                result = self.data_pipeline.get_stock_data(symbol, start_date, end_date, with_metadata=False)
                if result['success'] and result['data'] is not None:
                    stock_data[symbol] = result['data']
        
        if not stock_data:
            print("    ⚠️  无股票数据可用")
            return {'error': '无股票数据可用', 'portfolio_size': len(portfolio)}
        
        results = {}
        
        # 1. 风格因子暴露计算
        print("    计算风格因子暴露...")
        try:
            exposure_result = self.risk_manager.calculate_style_exposures(
                portfolio=portfolio,
                stock_data=stock_data,
                factor_source=self.config['risk']['style_factor_source']
            )
            results['style_exposures'] = exposure_result
            
            if 'exposure_analysis' in exposure_result:
                analysis = exposure_result['exposure_analysis']
                print(f"      风险状态: {analysis.get('risk_status', '未知')}")
        
        except Exception as e:
            print(f"      风格因子暴露计算失败: {e}")
            results['style_exposures_error'] = str(e)
        
        # 2. 压力测试
        print("    运行压力测试...")
        try:
            stress_result = self.risk_manager.run_stress_tests(
                portfolio=portfolio,
                stock_data=stock_data,
                scenarios=self.config['risk']['stress_scenarios']
            )
            results['stress_tests'] = stress_result
            
            if 'summary' in stress_result:
                summary = stress_result['summary']
                print(f"      测试情景: {summary.get('successful_scenarios', 0)}/{summary.get('total_scenarios', 0)}")
        
        except Exception as e:
            print(f"      压力测试失败: {e}")
            results['stress_tests_error'] = str(e)
        
        return {
            'portfolio': portfolio,
            'risk_assessments': results,
            'portfolio_size': len(portfolio),
            'assessment_date': end_date
        }
    
    def _step_strategy_backtest(self, symbols: List[str], start_date: str, end_date: str) -> Dict[str, Any]:
        """步骤5: 策略回测"""
        print("  策略回测...")
        
        if self.backtester is None:
            raise RuntimeError("回测器模块未加载")
        
        # 使用简单策略进行测试
        from advanced_backtest.advanced_backtester import simple_moving_average_strategy
        
        # 运行标准回测
        print("    运行标准回测...")
        try:
            backtest_result = self.backtester.run_standard_backtest(
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                strategy_func=simple_moving_average_strategy,
                short_window=5,
                long_window=20
            )
            
            summary = backtest_result.get('summary', {})
            print(f"      回测完成")
            print(f"      总收益: {summary.get('total_return', 0):.1f}%")
            print(f"      胜率: {summary.get('win_rate', 0):.1f}%")
            
            return {
                'standard_backtest': backtest_result,
                'strategy_used': 'simple_moving_average',
                'parameters': {'short_window': 5, 'long_window': 20}
            }
        
        except Exception as e:
            print(f"      标准回测失败: {e}")
            return {'error': str(e), 'strategy': 'simple_moving_average'}
    
    def _step_performance_evaluation(self) -> Dict[str, Any]:
        """步骤6: 绩效评估"""
        print("  绩效评估...")
        
        # 综合各个步骤的结果进行评估
        evaluation = {
            'timestamp': datetime.now().isoformat(),
            'steps_completed': list(self.workflow_state['results'].keys()),
            'errors_count': len(self.workflow_state['errors']),
            'overall_status': 'success' if len(self.workflow_state['errors']) == 0 else 'partial_failure'
        }
        
        # 提取关键指标
        key_metrics = {}
        
        # 从回测结果提取
        if 'strategy_backtest' in self.workflow_state['results']:
            backtest_result = self.workflow_state['results']['strategy_backtest']
            if 'standard_backtest' in backtest_result:
                summary = backtest_result['standard_backtest'].get('summary', {})
                key_metrics['backtest'] = {
                    'total_return': summary.get('total_return', 0),
                    'win_rate': summary.get('win_rate', 0),
                    'sharpe_ratio': summary.get('sharpe_ratio', 0),
                    'max_drawdown': summary.get('max_drawdown', 0)
                }
        
        # 从情绪因子结果提取
        if 'sentiment_calculation' in self.workflow_state['results']:
            sentiment_result = self.workflow_state['results']['sentiment_calculation']
            if 'summary' in sentiment_result:
                summary = sentiment_result['summary']
                key_metrics['sentiment'] = {
                    'market_sentiment': summary.get('market_sentiment', 'unknown'),
                    'sentiment_mean': summary.get('sentiment_stats', {}).get('mean', 0),
                    'bullish_stocks': summary.get('sentiment_distribution', {}).get('bullish', 0) + 
                                    summary.get('sentiment_distribution', {}).get('very_bullish', 0)
                }
        
        # 从风险评估结果提取
        if 'risk_assessment' in self.workflow_state['results']:
            risk_result = self.workflow_state['results']['risk_assessment']
            if 'risk_assessments' in risk_result:
                risk_assessments = risk_result['risk_assessments']
                
                if 'style_exposures' in risk_assessments:
                    exposure = risk_assessments['style_exposures']
                    if 'exposure_analysis' in exposure:
                        analysis = exposure['exposure_analysis']
                        key_metrics['risk'] = {
                            'risk_status': analysis.get('risk_status', 'unknown'),
                            'max_exposure': analysis.get('metrics', {}).get('max_exposure', 0)
                        }
        
        evaluation['key_metrics'] = key_metrics
        
        # 生成建议
        evaluation['recommendations'] = self._generate_recommendations(key_metrics)
        
        print(f"    评估完成")
        print(f"    总体状态: {evaluation['overall_status']}")
        print(f"    关键指标: {len(key_metrics)} 项")
        
        return evaluation
    
    def _get_market_index_data(self, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """获取市场指数数据"""
        try:
            if self.data_pipeline:
                # 尝试获取上证指数数据
                result = self.data_pipeline.get_stock_data(
                    '000001', start_date, end_date, with_metadata=False
                )
                if result['success'] and result['data'] is not None:
                    return result['data']
        except Exception as e:
            print(f"    获取市场指数数据失败: {e}")
        
        # 备用：创建模拟数据
        dates = pd.date_range(start=start_date, end=end_date, freq='B')
        if len(dates) == 0:
            return None
        
        np.random.seed(42)
        prices = 3000 * (1 + np.cumsum(np.random.randn(len(dates)) * 0.005))
        
        return pd.DataFrame({
            'close': prices,
            'open': prices * 0.995,
            'high': prices * 1.01,
            'low': prices * 0.99,
            'volume': np.random.randint(1e9, 5e9, len(dates))
        }, index=dates)
    
    def _calculate_price_change(self, data: pd.DataFrame) -> float:
        """计算价格变化"""
        if data.empty or 'close' not in data.columns:
            return 0
        
        prices = data['close']
        if len(prices) < 2:
            return 0
        
        return (prices.iloc[-1] / prices.iloc[0] - 1) * 100
    
    def _generate_workflow_summary(self) -> Dict[str, Any]:
        """生成工作流总结"""
        summary = {
            'execution_time': {
                'start': self.workflow_state['start_time'].isoformat(),
                'end': self.workflow_state['end_time'].isoformat() if self.workflow_state['end_time'] else None,
                'duration_seconds': (self.workflow_state['end_time'] - self.workflow_state['start_time']).total_seconds() 
                    if self.workflow_state['end_time'] else None
            },
            'steps_summary': {
                'total_steps': len(self.workflow_state.get('parameters', {}).get('workflow_steps', [])),
                'completed_steps': len(self.workflow_state['results']),
                'failed_steps': len(self.workflow_state['errors'])
            },
            'errors': self.workflow_state['errors'],
            'overall_status': 'success' if len(self.workflow_state['errors']) == 0 else 'partial_failure'
        }
        
        return summary
    
    def _generate_recommendations(self, key_metrics: Dict[str, Any]) -> List[str]:
        """生成建议"""
        recommendations = []
        
        # 基于回测结果的建议
        if 'backtest' in key_metrics:
            backtest = key_metrics['backtest']
            
            if backtest.get('total_return', 0) < 0:
                recommendations.append("策略收益为负，建议优化策略参数或更换策略")
            
            if backtest.get('win_rate', 0) < 40:
                recommendations.append("胜率较低，建议调整入场时机或增加过滤条件")
            
            if backtest.get('max_drawdown', 0) < -20:
                recommendations.append("最大回撤较大，建议加强风险控制或降低仓位")
        
        # 基于情绪因子的建议
        if 'sentiment' in key_metrics:
            sentiment = key_metrics['sentiment']
            
            if sentiment.get('market_sentiment') == 'bearish':
                recommendations.append("市场情绪偏空，建议谨慎操作或降低仓位")
            elif sentiment.get('market_sentiment') == 'bullish':
                recommendations.append("市场情绪偏多，可适当增加仓位")
            
            if sentiment.get('bullish_stocks', 0) < len(self.workflow_state.get('parameters', {}).get('symbols', [])) * 0.3:
                recommendations.append("看多股票数量较少，市场可能缺乏明确方向")
        
        # 基于风险的建议
        if 'risk' in key_metrics:
            risk = key_metrics['risk']
            
            if risk.get('risk_status') == '超标':
                recommendations.append("风险暴露超标，建议立即调整持仓降低风险")
            elif risk.get('risk_status') == '警告':
                recommendations.append("风险暴露接近限额，建议关注并适当调整")
        
        # 默认建议
        if not recommendations:
            recommendations.append("系统运行正常，可继续执行当前策略")
        
        return recommendations


# 测试函数
def test_system_integration():
    """测试系统集成"""
    print("=" * 80)
    print("测试系统集成控制器")
    print("=" * 80)
    
    # 创建控制器
    controller = SystemIntegrationController()
    
    # 检查系统健康
    health = controller.check_system_health()
    
    if health['overall'] != 'healthy':
        print("系统健康状态异常，无法继续测试")
        return False
    
    # 运行端到端工作流（简化版本）
    print("\n运行简化工作流...")
    result = controller.run_end_to_end_workflow(
        symbols=['600519', '000001'],  # 减少股票数量以加快测试
        start_date='2024-01-01',
        end_date='2024-01-31',  # 缩短测试期间
        workflow_steps=['data_acquisition', 'market_state_analysis', 'sentiment_calculation']
    )
    
    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)
    
    success = result.get('success', False)
    if success:
        print("✅ 系统集成测试通过")
    else:
        print("❌ 系统集成测试失败")
        print("错误信息:")
        for error in result.get('workflow_state', {}).get('errors', []):
            print(f"  - {error}")
    
    return success


if __name__ == "__main__":
    test_system_integration()