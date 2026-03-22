#!/usr/bin/env python3
"""
回测结果验证框架
自动验证回测结果的一致性和可靠性
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Any, Optional
from datetime import datetime, timedelta
import json
import os
import hashlib
import warnings
warnings.filterwarnings('ignore')


class BacktestValidator:
    """回测结果验证器"""
    
    def __init__(self, validation_dir: str = None):
        """
        初始化验证器
        
        Args:
            validation_dir: 验证结果存储目录
        """
        if validation_dir is None:
            validation_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'validation_results'
            )
        
        self.validation_dir = validation_dir
        os.makedirs(validation_dir, exist_ok=True)
        
        # 验证结果文件
        self.results_file = os.path.join(validation_dir, 'validation_results.json')
        self.consistency_file = os.path.join(validation_dir, 'consistency_checks.csv')
        
        # 验证规则配置
        self.validation_rules = self._load_validation_rules()
    
    def _load_validation_rules(self) -> Dict[str, Any]:
        """加载验证规则"""
        return {
            'consistency': {
                'total_return_threshold': 0.01,  # 1% 总收益差异阈值
                'sharpe_ratio_threshold': 0.05,  # 0.05 夏普比率差异阈值
                'max_drawdown_threshold': 0.02,  # 2% 最大回撤差异阈值
                'position_consistency_threshold': 0.9,  # 90% 持仓一致性
            },
            'statistical': {
                'min_trades_for_significance': 10,  # 显著性检验最小交易数
                'win_rate_confidence_level': 0.95,  # 胜率置信水平
                'sharpe_significance_level': 0.05,  # 夏普比率显著性水平
                'autocorrelation_lag': 5,  # 自相关检验滞后阶数
            },
            'data_quality': {
                'min_data_coverage': 0.95,  # 最小数据覆盖率
                'max_consecutive_missing': 5,  # 最大连续缺失数据
                'price_sanity_checks': True,  # 价格合理性检查
                'volume_sanity_checks': True,  # 成交量合理性检查
            },
            'performance': {
                'min_annual_return': -0.5,  # 最小年化收益 -50%
                'max_annual_volatility': 1.0,  # 最大年化波动率 100%
                'min_sharpe_ratio': -2.0,  # 最小夏普比率
                'max_drawdown_limit': 0.8,  # 最大回撤限制 80%
            }
        }
    
    def validate_single_backtest(self, result: Dict[str, Any], 
                                 reference_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        验证单个回测结果
        
        Args:
            result: 回测结果字典
            reference_result: 参考回测结果（用于一致性检查）
            
        Returns:
            验证结果字典
        """
        validation_results = {
            'timestamp': datetime.now().isoformat(),
            'symbol': result.get('symbol', 'unknown'),
            'validation_passed': True,
            'failed_checks': [],
            'warnings': [],
            'checks_performed': {}
        }
        
        try:
            # 1. 数据质量检查
            data_quality_checks = self._check_data_quality(result)
            validation_results['checks_performed']['data_quality'] = data_quality_checks
            
            if not data_quality_checks['passed']:
                validation_results['validation_passed'] = False
                validation_results['failed_checks'].append('data_quality')
            
            # 2. 统计合理性检查
            statistical_checks = self._check_statistical_reasonableness(result)
            validation_results['checks_performed']['statistical'] = statistical_checks
            
            for warning in statistical_checks.get('warnings', []):
                validation_results['warnings'].append(warning)
            
            # 3. 性能指标检查
            performance_checks = self._check_performance_metrics(result)
            validation_results['checks_performed']['performance'] = performance_checks
            
            if not performance_checks['passed']:
                validation_results['validation_passed'] = False
                validation_results['failed_checks'].append('performance')
            
            # 4. 与参考结果的一致性检查（如果有）
            if reference_result:
                consistency_checks = self._check_consistency(result, reference_result)
                validation_results['checks_performed']['consistency'] = consistency_checks
                
                if not consistency_checks['passed']:
                    validation_results['validation_passed'] = False
                    validation_results['failed_checks'].append('consistency')
            
            # 5. 交易记录检查
            if 'trade_records' in result:
                trade_checks = self._check_trade_records(result['trade_records'])
                validation_results['checks_performed']['trade_checks'] = trade_checks
                
                for warning in trade_checks.get('warnings', []):
                    validation_results['warnings'].append(warning)
            
            # 6. 计算验证分数
            validation_score = self._calculate_validation_score(validation_results)
            validation_results['validation_score'] = validation_score
            
            # 保存验证结果
            self._save_validation_result(validation_results)
            
        except Exception as e:
            validation_results['validation_passed'] = False
            validation_results['failed_checks'].append(f'exception: {str(e)}')
            validation_results['error'] = str(e)
        
        return validation_results
    
    def _check_data_quality(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """检查数据质量"""
        checks = {
            'passed': True,
            'details': {},
            'warnings': []
        }
        
        rules = self.validation_rules['data_quality']
        
        # 检查数据覆盖率（如果有portfolio_values）
        if 'portfolio_values' in result and hasattr(result['portfolio_values'], '__len__'):
            portfolio_values = result['portfolio_values']
            if len(portfolio_values) > 0:
                # 检查缺失值
                if hasattr(portfolio_values, 'isna'):
                    missing_count = portfolio_values.isna().sum()
                    missing_ratio = missing_count / len(portfolio_values)
                    
                    checks['details']['missing_ratio'] = float(missing_ratio)
                    
                    if missing_ratio > (1 - rules['min_data_coverage']):
                        checks['passed'] = False
                        checks['warnings'].append(f'数据缺失率过高: {missing_ratio:.1%}')
                
                # 检查连续缺失
                if hasattr(portfolio_values, 'isna'):
                    # 简化检查
                    pass
        
        # 检查价格合理性（如果有trade_records）
        if 'trade_records' in result and rules['price_sanity_checks']:
            trades = result['trade_records']
            if trades and len(trades) > 0:
                prices = [t.get('price', 0) for t in trades if t.get('price')]
                if prices:
                    min_price = min(prices)
                    max_price = max(prices)
                    
                    checks['details']['min_price'] = float(min_price)
                    checks['details']['max_price'] = float(max_price)
                    
                    # 检查价格是否在合理范围内
                    if min_price <= 0:
                        checks['passed'] = False
                        checks['warnings'].append(f'发现非正价格: {min_price}')
                    
                    if max_price > 10000:  # 假设股票价格不超过10000元
                        checks['warnings'].append(f'发现异常高价格: {max_price}')
        
        return checks
    
    def _check_statistical_reasonableness(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """检查统计合理性"""
        checks = {
            'passed': True,
            'details': {},
            'warnings': []
        }
        
        rules = self.validation_rules['statistical']
        
        # 检查交易次数是否足够
        total_trades = result.get('total_trades', 0)
        checks['details']['total_trades'] = total_trades
        
        if total_trades < rules['min_trades_for_significance']:
            checks['warnings'].append(f'交易次数较少 ({total_trades})，统计显著性可能不足')
        
        # 检查夏普比率（如果可用）
        sharpe_ratio = result.get('sharpe_ratio', 0)
        if sharpe_ratio != 0:
            checks['details']['sharpe_ratio'] = float(sharpe_ratio)
            
            # 检查夏普比率是否异常高（可能暗示未来函数）
            if sharpe_ratio > 5.0:
                checks['warnings'].append(f'夏普比率异常高 ({sharpe_ratio:.2f})，可能存在问题')
        
        # 检查胜率（如果可用）
        win_rate = result.get('win_rate', 0)
        if win_rate != 0:
            checks['details']['win_rate'] = float(win_rate)
            
            # 检查胜率是否异常高
            if win_rate > 0.9:
                checks['warnings'].append(f'胜率异常高 ({win_rate:.1%})，可能存在问题')
            elif win_rate < 0.1:
                checks['warnings'].append(f'胜率异常低 ({win_rate:.1%})，策略可能无效')
        
        # 检查最大回撤（如果可用）
        max_drawdown = result.get('max_drawdown', 0)
        if max_drawdown != 0:
            checks['details']['max_drawdown'] = float(max_drawdown)
            
            if max_drawdown > 0.5:  # 50%回撤
                checks['warnings'].append(f'最大回撤较大 ({max_drawdown:.1%})，风险较高')
        
        return checks
    
    def _check_performance_metrics(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """检查性能指标"""
        checks = {
            'passed': True,
            'details': {},
            'warnings': []
        }
        
        rules = self.validation_rules['performance']
        
        # 检查年化收益
        annual_return = result.get('annual_return', 0)
        checks['details']['annual_return'] = float(annual_return)
        
        if annual_return < rules['min_annual_return']:
            checks['passed'] = False
            checks['warnings'].append(f'年化收益过低 ({annual_return:.1%})，低于阈值 {rules["min_annual_return"]:.1%}')
        
        # 检查夏普比率
        sharpe_ratio = result.get('sharpe_ratio', 0)
        checks['details']['sharpe_ratio'] = float(sharpe_ratio)
        
        if sharpe_ratio < rules['min_sharpe_ratio']:
            checks['warnings'].append(f'夏普比率较低 ({sharpe_ratio:.2f})，低于阈值 {rules["min_sharpe_ratio"]:.2f}')
        
        # 检查最大回撤
        max_drawdown = result.get('max_drawdown', 0)
        checks['details']['max_drawdown'] = float(max_drawdown)
        
        if max_drawdown > rules['max_drawdown_limit']:
            checks['passed'] = False
            checks['warnings'].append(f'最大回撤过大 ({max_drawdown:.1%})，超过限制 {rules["max_drawdown_limit"]:.1%}')
        
        # 检查总交易次数
        total_trades = result.get('total_trades', 0)
        checks['details']['total_trades'] = total_trades
        
        if total_trades == 0:
            checks['warnings'].append('无交易记录，策略可能无效')
        
        return checks
    
    def _check_consistency(self, result: Dict[str, Any], 
                          reference_result: Dict[str, Any]) -> Dict[str, Any]:
        """检查结果一致性"""
        checks = {
            'passed': True,
            'details': {},
            'warnings': []
        }
        
        rules = self.validation_rules['consistency']
        
        # 比较关键指标
        metrics_to_compare = [
            ('total_return', '总收益'),
            ('annual_return', '年化收益'),
            ('sharpe_ratio', '夏普比率'),
            ('max_drawdown', '最大回撤'),
            ('win_rate', '胜率')
        ]
        
        for metric_key, metric_name in metrics_to_compare:
            if metric_key in result and metric_key in reference_result:
                value1 = result[metric_key]
                value2 = reference_result[metric_key]
                
                # 计算差异
                if value1 != 0 or value2 != 0:
                    if metric_key == 'max_drawdown':
                        # 最大回撤差异计算（都是负数）
                        diff = abs(value1 - value2)
                    else:
                        diff = abs((value1 - value2) / value2) if value2 != 0 else abs(value1 - value2)
                    
                    checks['details'][f'{metric_key}_diff'] = float(diff)
                    
                    # 检查是否超过阈值
                    threshold_key = f'{metric_key}_threshold'
                    threshold = rules.get(threshold_key, rules.get('total_return_threshold', 0.01))
                    
                    if diff > threshold:
                        checks['passed'] = False
                        checks['warnings'].append(
                            f'{metric_name}差异过大: {diff:.1%} (阈值: {threshold:.1%})'
                        )
        
        # 检查交易记录一致性（如果有）
        if 'trade_records' in result and 'trade_records' in reference_result:
            trades1 = result['trade_records']
            trades2 = reference_result['trade_records']
            
            if len(trades1) != len(trades2):
                checks['warnings'].append(f'交易记录数量不一致: {len(trades1)} vs {len(trades2)}')
        
        return checks
    
    def _check_trade_records(self, trade_records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """检查交易记录"""
        checks = {
            'passed': True,
            'details': {},
            'warnings': []
        }
        
        if not trade_records:
            checks['warnings'].append('无交易记录')
            return checks
        
        # 基本统计
        n_trades = len(trade_records)
        buy_trades = [t for t in trade_records if t.get('action') in ['BUY', 'buy']]
        sell_trades = [t for t in trade_records if t.get('action') in ['SELL', 'sell']]
        
        checks['details']['total_trades'] = n_trades
        checks['details']['buy_trades'] = len(buy_trades)
        checks['details']['sell_trades'] = len(sell_trades)
        
        # 检查买卖平衡
        if len(buy_trades) != len(sell_trades):
            checks['warnings'].append(f'买卖交易不平衡: 买入{len(buy_trades)}次, 卖出{len(sell_trades)}次')
        
        # 检查交易价格
        prices = []
        for trade in trade_records:
            price = trade.get('price', 0)
            if price > 0:
                prices.append(price)
        
        if prices:
            checks['details']['avg_price'] = float(np.mean(prices))
            checks['details']['min_price'] = float(min(prices))
            checks['details']['max_price'] = float(max(prices))
            
            # 检查价格范围
            price_range = max(prices) - min(prices)
            if price_range > 1000:  # 假设合理价格范围
                checks['warnings'].append(f'交易价格范围过大: {price_range:.1f}')
        
        # 检查交易时间顺序（如果有时戳）
        timestamps = []
        for trade in trade_records:
            if 'date' in trade:
                try:
                    if isinstance(trade['date'], str):
                        timestamps.append(pd.to_datetime(trade['date']))
                    else:
                        timestamps.append(trade['date'])
                except:
                    pass
        
        if len(timestamps) > 1:
            # 检查时间是否有序
            sorted_timestamps = sorted(timestamps)
            if timestamps != sorted_timestamps:
                checks['warnings'].append('交易时间顺序异常')
        
        return checks
    
    def _calculate_validation_score(self, validation_results: Dict[str, Any]) -> float:
        """计算验证分数（0-100分）"""
        score = 100.0
        
        # 扣分规则
        penalty_rules = {
            'failed_checks': 30,  # 每项失败检查扣30分
            'warnings': 5,        # 每个警告扣5分
            'data_quality_failed': 20,  # 数据质量失败额外扣分
            'performance_failed': 25,   # 性能检查失败额外扣分
            'consistency_failed': 20,   # 一致性检查失败额外扣分
        }
        
        # 检查失败扣分
        failed_checks = validation_results.get('failed_checks', [])
        score -= len(failed_checks) * penalty_rules['failed_checks']
        
        # 警告扣分
        warnings = validation_results.get('warnings', [])
        score -= len(warnings) * penalty_rules['warnings']
        
        # 特定检查失败额外扣分
        if 'data_quality' in failed_checks:
            score -= penalty_rules['data_quality_failed']
        if 'performance' in failed_checks:
            score -= penalty_rules['performance_failed']
        if 'consistency' in failed_checks:
            score -= penalty_rules['consistency_failed']
        
        # 确保分数在0-100之间
        score = max(0, min(100, score))
        
        return score
    
    def _save_validation_result(self, validation_result: Dict[str, Any]):
        """保存验证结果"""
        try:
            # 加载现有结果
            all_results = []
            if os.path.exists(self.results_file):
                with open(self.results_file, 'r') as f:
                    all_results = json.load(f)
            
            # 添加新结果
            all_results.append(validation_result)
            
            # 只保留最近100个结果
            if len(all_results) > 100:
                all_results = all_results[-100:]
            
            # 保存
            with open(self.results_file, 'w') as f:
                json.dump(all_results, f, indent=2, default=str)
                
        except Exception as e:
            print(f"验证结果保存失败: {e}")
    
    def validate_batch_backtests(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        验证批量回测结果
        
        Args:
            results: 回测结果列表
            
        Returns:
            批量验证结果
        """
        batch_validation = {
            'timestamp': datetime.now().isoformat(),
            'total_tests': len(results),
            'passed_tests': 0,
            'failed_tests': 0,
            'average_score': 0,
            'validation_results': [],
            'summary': {}
        }
        
        scores = []
        
        for i, result in enumerate(results):
            try:
                # 使用第一个结果作为参考（如果可用）
                reference = results[0] if i > 0 else None
                
                validation_result = self.validate_single_backtest(result, reference)
                batch_validation['validation_results'].append(validation_result)
                
                if validation_result.get('validation_passed', False):
                    batch_validation['passed_tests'] += 1
                else:
                    batch_validation['failed_tests'] += 1
                
                score = validation_result.get('validation_score', 0)
                scores.append(score)
                
            except Exception as e:
                print(f"回测 {i} 验证失败: {e}")
                batch_validation['failed_tests'] += 1
        
        # 计算统计
        if scores:
            batch_validation['average_score'] = float(np.mean(scores))
            batch_validation['min_score'] = float(min(scores))
            batch_validation['max_score'] = float(max(scores))
            batch_validation['score_std'] = float(np.std(scores))
        
        # 生成总结
        batch_validation['summary'] = {
            'pass_rate': batch_validation['passed_tests'] / batch_validation['total_tests'] 
                         if batch_validation['total_tests'] > 0 else 0,
            'quality_assessment': self._assess_batch_quality(batch_validation),
            'recommendations': self._generate_recommendations(batch_validation)
        }
        
        return batch_validation
    
    def _assess_batch_quality(self, batch_validation: Dict[str, Any]) -> str:
        """评估批量结果质量"""
        pass_rate = batch_validation['summary']['pass_rate']
        avg_score = batch_validation.get('average_score', 0)
        
        if pass_rate >= 0.9 and avg_score >= 80:
            return '优秀'
        elif pass_rate >= 0.7 and avg_score >= 60:
            return '良好'
        elif pass_rate >= 0.5 and avg_score >= 40:
            return '一般'
        else:
            return '需要改进'
    
    def _generate_recommendations(self, batch_validation: Dict[str, Any]) -> List[str]:
        """生成改进建议"""
        recommendations = []
        
        # 分析常见问题
        failed_checks = {}
        warnings = []
        
        for result in batch_validation['validation_results']:
            for check in result.get('failed_checks', []):
                failed_checks[check] = failed_checks.get(check, 0) + 1
            
            warnings.extend(result.get('warnings', []))
        
        # 基于失败检查生成建议
        if 'data_quality' in failed_checks:
            recommendations.append('提高数据质量：检查数据源和预处理流程')
        
        if 'performance' in failed_checks:
            recommendations.append('优化策略性能：调整参数或改进策略逻辑')
        
        if 'consistency' in failed_checks:
            recommendations.append('提高结果一致性：确保回测过程确定性')
        
        # 基于警告生成建议
        if any('夏普比率异常高' in w for w in warnings):
            recommendations.append('检查夏普比率：可能存在未来函数或过拟合')
        
        if any('胜率异常高' in w for w in warnings):
            recommendations.append('检查胜率：策略可能在特定市场环境下过拟合')
        
        if any('最大回撤过大' in w for w in warnings):
            recommendations.append('控制风险：考虑添加止损或降低仓位')
        
        # 通用建议
        if batch_validation['average_score'] < 60:
            recommendations.append('全面审查回测流程：建议从头检查数据、代码和参数设置')
        
        return recommendations
    
    def generate_validation_report(self, validation_results: Dict[str, Any], 
                                  output_file: Optional[str] = None) -> str:
        """生成验证报告"""
        if output_file is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = os.path.join(self.validation_dir, f'validation_report_{timestamp}.html')
        
        # 生成HTML报告
        html_template = f'''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>回测结果验证报告</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {{ font-family: Arial, sans-serif; }}
        .score-card {{ background: #f8f9fa; border-radius: 10px; padding: 20px; margin: 10px 0; }}
        .score-excellent {{ border-left: 5px solid #28a745; }}
        .score-good {{ border-left: 5px solid #17a2b8; }}
        .score-fair {{ border-left: 5px solid #ffc107; }}
        .score-poor {{ border-left: 5px solid #dc3545; }}
        .recommendation {{ background: #e7f3ff; border-left: 4px solid #007bff; padding: 15px; margin: 10px 0; }}
    </style>
</head>
<body>
    <div class="container mt-4">
        <h1>回测结果验证报告</h1>
        <p class="text-muted">生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        
        <div class="row">
            <div class="col-md-12">
                <div class="score-card {self._get_score_class(validation_results.get('validation_score', 0))}">
                    <h3>验证分数: {validation_results.get('validation_score', 0):.1f}/100</h3>
                    <p>验证状态: <strong>{'通过' if validation_results.get('validation_passed', False) else '失败'}</strong></p>
                </div>
            </div>
        </div>
        
        <div class="row mt-4">
            <div class="col-md-6">
                <h4>检查结果</h4>
                <ul>
                    {self._generate_check_list_html(validation_results)}
                </ul>
            </div>
            <div class="col-md-6">
                <h4>详细指标</h4>
                <table class="table">
                    {self._generate_metrics_table_html(validation_results)}
                </table>
            </div>
        </div>
        
        {self._generate_recommendations_html(validation_results)}
        
        <div class="mt-4">
            <h4>检查详情</h4>
            <pre>{json.dumps(validation_results.get('checks_performed', {}), indent=2, default=str)}</pre>
        </div>
    </div>
</body>
</html>
        '''
        
        try:
            with open(output_file, 'w') as f:
                f.write(html_template)
            return output_file
        except Exception as e:
            return f"报告生成失败: {e}"
    
    def _get_score_class(self, score: float) -> str:
        """获取分数对应的CSS类"""
        if score >= 80:
            return 'score-excellent'
        elif score >= 60:
            return 'score-good'
        elif score >= 40:
            return 'score-fair'
        else:
            return 'score-poor'
    
    def _generate_check_list_html(self, validation_results: Dict[str, Any]) -> str:
        """生成检查列表HTML"""
        html = ''
        
        # 失败检查
        failed_checks = validation_results.get('failed_checks', [])
        for check in failed_checks:
            html += f'<li><span class="text-danger">❌ {check}</span></li>'
        
        # 通过检查（假设其他都通过）
        all_checks = ['data_quality', 'statistical', 'performance', 'consistency', 'trade_checks']
        for check in all_checks:
            if check not in failed_checks:
                html += f'<li><span class="text-success">✅ {check}</span></li>'
        
        return html
    
    def _generate_metrics_table_html(self, validation_results: Dict[str, Any]) -> str:
        """生成指标表格HTML"""
        html = ''
        
        checks_performed = validation_results.get('checks_performed', {})
        
        for check_type, details in checks_performed.items():
            for key, value in details.get('details', {}).items():
                html += f'<tr><td>{key}</td><td>{value}</td></tr>'
        
        if not html:
            html = '<tr><td colspan="2">暂无详细指标</td></tr>'
        
        return html
    
    def _generate_recommendations_html(self, validation_results: Dict[str, Any]) -> str:
        """生成建议HTML"""
        warnings = validation_results.get('warnings', [])
        
        if not warnings:
            return ''
        
        html = '<div class="mt-4"><h4>改进建议</h4>'
        
        for warning in warnings:
            html += f'<div class="recommendation">{warning}</div>'
        
        html += '</div>'
        return html


def test_backtest_validator():
    """测试回测验证器"""
    print("测试回测结果验证框架")
    print("=" * 60)
    
    validator = BacktestValidator()
    
    # 创建测试回测结果
    test_result = {
        'symbol': '600519',
        'total_return': 0.15,
        'annual_return': 0.25,
        'sharpe_ratio': 1.8,
        'max_drawdown': -0.12,
        'win_rate': 0.65,
        'total_trades': 42,
        'profitable_trades': 27,
        'portfolio_values': pd.Series([1.0, 1.05, 1.02, 1.08, 1.12, 1.15]),
        'trade_records': [
            {'date': '2024-01-02', 'action': 'BUY', 'price': 200.5, 'shares': 100},
            {'date': '2024-01-15', 'action': 'SELL', 'price': 210.2, 'shares': 100},
            {'date': '2024-02-01', 'action': 'BUY', 'price': 205.8, 'shares': 150},
        ]
    }
    
    # 创建参考结果（稍有差异）
    reference_result = {
        'symbol': '600519',
        'total_return': 0.16,
        'annual_return': 0.26,
        'sharpe_ratio': 1.9,
        'max_drawdown': -0.11,
        'win_rate': 0.64,
        'total_trades': 41,
        'profitable_trades': 26,
    }
    
    # 验证单个回测
    print("验证单个回测结果...")
    validation_result = validator.validate_single_backtest(test_result, reference_result)
    
    print(f"验证分数: {validation_result.get('validation_score', 0):.1f}/100")
    print(f"验证状态: {'通过' if validation_result.get('validation_passed', False) else '失败'}")
    
    if validation_result.get('failed_checks'):
        print(f"失败检查: {', '.join(validation_result['failed_checks'])}")
    
    if validation_result.get('warnings'):
        print(f"警告: {len(validation_result['warnings'])}个")
        for warning in validation_result['warnings'][:3]:  # 显示前3个
            print(f"  - {warning}")
    
    # 测试批量验证
    print("\n测试批量验证...")
    batch_results = [test_result, reference_result]
    batch_validation = validator.validate_batch_backtests(batch_results)
    
    print(f"批量验证: {batch_validation['passed_tests']}/{batch_validation['total_tests']} 通过")
    print(f"平均分数: {batch_validation.get('average_score', 0):.1f}")
    print(f"质量评估: {batch_validation['summary']['quality_assessment']}")
    
    # 生成报告
    report_file = validator.generate_validation_report(validation_result)
    print(f"\n验证报告已生成: {report_file}")
    
    print("\n" + "=" * 60)
    print("✅ 回测验证框架测试完成")


if __name__ == '__main__':
    test_backtest_validator()