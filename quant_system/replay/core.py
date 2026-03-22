"""
策略回放引擎核心模块
支持历史策略重新执行和对比分析
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import json
import hashlib
import os


class ReplayDataManager:
    """回放数据管理器"""
    
    def __init__(self, data_dir: str = None):
        if data_dir is None:
            data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'replay_data')
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
    
    def save_replay_snapshot(self, snapshot_id: str, data: Dict[str, Any]) -> str:
        """保存回放快照"""
        file_path = os.path.join(self.data_dir, f'snapshot_{snapshot_id}.json')
        with open(file_path, 'w') as f:
            json.dump(data, f, default=str)
        return file_path
    
    def load_replay_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """加载回放快照"""
        file_path = os.path.join(self.data_dir, f'snapshot_{snapshot_id}.json')
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                return json.load(f)
        return None
    
    def save_market_data(self, date: str, symbol: str, data: pd.DataFrame) -> str:
        """保存市场数据"""
        date_dir = os.path.join(self.data_dir, 'market_data', date)
        os.makedirs(date_dir, exist_ok=True)
        
        file_path = os.path.join(date_dir, f'{symbol}.parquet')
        data.to_parquet(file_path)
        return file_path
    
    def load_market_data(self, date: str, symbol: str) -> Optional[pd.DataFrame]:
        """加载市场数据"""
        file_path = os.path.join(self.data_dir, 'market_data', date, f'{symbol}.parquet')
        if os.path.exists(file_path):
            return pd.read_parquet(file_path)
        return None
    
    def save_replay_result(self, replay_id: str, result: Dict[str, Any]) -> str:
        """保存回放结果"""
        file_path = os.path.join(self.data_dir, f'result_{replay_id}.json')
        with open(file_path, 'w') as f:
            json.dump(result, f, default=str)
        return file_path
    
    def load_replay_result(self, replay_id: str) -> Optional[Dict[str, Any]]:
        """加载回放结果"""
        file_path = os.path.join(self.data_dir, f'result_{replay_id}.json')
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                return json.load(f)
        return None


class StrategyReplayer:
    """策略回放器"""
    
    def __init__(self, data_manager: ReplayDataManager = None):
        self.data_manager = data_manager or ReplayDataManager()
        self.replay_cache = {}
    
    def create_snapshot(self, strategy_config: Dict[str, Any], 
                       portfolio_state: Dict[str, Any] = None,
                       market_conditions: Dict[str, Any] = None) -> str:
        """创建策略快照"""
        snapshot_data = {
            'timestamp': datetime.now().isoformat(),
            'strategy_config': strategy_config,
            'portfolio_state': portfolio_state or {},
            'market_conditions': market_conditions or {},
            'version': '1.0'
        }
        
        # 生成快照ID
        snapshot_hash = hashlib.md5(
            json.dumps(snapshot_data, sort_keys=True).encode()
        ).hexdigest()[:12]
        snapshot_id = f'snap_{snapshot_hash}'
        
        # 保存快照
        self.data_manager.save_replay_snapshot(snapshot_id, snapshot_data)
        return snapshot_id
    
    def replay_strategy(self, snapshot_id: str, replay_date: str,
                       market_data: Dict[str, pd.DataFrame] = None,
                       parameters: Dict[str, Any] = None) -> Dict[str, Any]:
        """回放策略"""
        # 加载快照
        snapshot = self.data_manager.load_replay_snapshot(snapshot_id)
        if not snapshot:
            raise ValueError(f"快照不存在: {snapshot_id}")
        
        # 准备回放参数
        strategy_config = snapshot['strategy_config']
        replay_params = parameters or {}
        
        # 执行回放
        replay_result = self._execute_replay(
            strategy_config, replay_date, market_data, replay_params
        )
        
        # 生成回放ID
        replay_hash = hashlib.md5(
            json.dumps({
                'snapshot_id': snapshot_id,
                'replay_date': replay_date,
                'parameters': replay_params
            }, sort_keys=True).encode()
        ).hexdigest()[:12]
        replay_id = f'replay_{replay_hash}'
        
        # 保存结果
        result_data = {
            'replay_id': replay_id,
            'snapshot_id': snapshot_id,
            'replay_date': replay_date,
            'parameters': replay_params,
            'result': replay_result,
            'timestamp': datetime.now().isoformat()
        }
        
        self.data_manager.save_replay_result(replay_id, result_data)
        return result_data
    
    def _execute_replay(self, strategy_config: Dict[str, Any], replay_date: str,
                       market_data: Dict[str, pd.DataFrame], parameters: Dict[str, Any]) -> Dict[str, Any]:
        """执行回放（核心逻辑）"""
        # 这里应该根据具体的策略类型执行回放
        # 简化实现，返回模拟结果
        
        result = {
            'performance_metrics': {
                'total_return': np.random.uniform(-0.1, 0.3),
                'annual_return': np.random.uniform(-0.2, 0.4),
                'sharpe_ratio': np.random.uniform(0, 2.5),
                'max_drawdown': np.random.uniform(-0.3, -0.05),
                'win_rate': np.random.uniform(0.3, 0.7),
            },
            'trades': [
                {
                    'date': replay_date,
                    'symbol': '600519',
                    'action': 'BUY',
                    'price': 200.5,
                    'shares': 100
                }
            ],
            'portfolio_changes': {
                'cash_change': -20050,
                'positions_change': {'600519': 100}
            },
            'market_conditions': {
                'date': replay_date,
                'market_state': 'normal',
                'volatility': 0.02
            }
        }
        
        return result
    
    def replay_with_different_parameters(self, snapshot_id: str, replay_date: str,
                                       parameter_sets: List[Dict[str, Any]]) -> Dict[str, Any]:
        """使用不同参数集回放策略"""
        results = {}
        
        for i, params in enumerate(parameter_sets):
            replay_id = f'param_set_{i}'
            result = self.replay_strategy(snapshot_id, replay_date, parameters=params)
            results[replay_id] = result
        
        # 参数敏感性分析
        sensitivity = self._analyze_parameter_sensitivity(results)
        
        return {
            'parameter_sets': parameter_sets,
            'results': results,
            'sensitivity_analysis': sensitivity
        }
    
    def _analyze_parameter_sensitivity(self, results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """分析参数敏感性"""
        if not results:
            return {}
        
        # 简化实现
        metrics = ['total_return', 'sharpe_ratio', 'max_drawdown']
        sensitivity = {metric: {'min': 0, 'max': 0, 'avg': 0} for metric in metrics}
        
        metric_values = {metric: [] for metric in metrics}
        
        for result in results.values():
            perf = result['result']['performance_metrics']
            for metric in metrics:
                if metric in perf:
                    metric_values[metric].append(perf[metric])
        
        for metric in metrics:
            if metric_values[metric]:
                sensitivity[metric] = {
                    'min': min(metric_values[metric]),
                    'max': max(metric_values[metric]),
                    'avg': np.mean(metric_values[metric]),
                    'std': np.std(metric_values[metric]),
                    'range': max(metric_values[metric]) - min(metric_values[metric])
                }
        
        return sensitivity


class ReplayComparator:
    """回放对比分析器"""
    
    def __init__(self, data_manager: ReplayDataManager = None):
        self.data_manager = data_manager or ReplayDataManager()
    
    def compare_replays(self, replay_ids: List[str]) -> Dict[str, Any]:
        """比较多个回放结果"""
        results = {}
        
        for replay_id in replay_ids:
            result = self.data_manager.load_replay_result(replay_id)
            if result:
                results[replay_id] = result
        
        if not results:
            return {}
        
        # 性能对比
        performance_comparison = self._compare_performance(results)
        
        # 交易对比
        trade_comparison = self._compare_trades(results)
        
        # 综合评分
        overall_scores = self._calculate_overall_scores(results)
        
        return {
            'replay_ids': replay_ids,
            'performance_comparison': performance_comparison,
            'trade_comparison': trade_comparison,
            'overall_scores': overall_scores,
            'best_replay': self._identify_best_replay(overall_scores),
            'worst_replay': self._identify_worst_replay(overall_scores)
        }
    
    def _compare_performance(self, results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """比较性能指标"""
        comparison = {
            'metrics': ['total_return', 'annual_return', 'sharpe_ratio', 'max_drawdown', 'win_rate'],
            'values': {},
            'rankings': {}
        }
        
        for metric in comparison['metrics']:
            values = {}
            for replay_id, result in results.items():
                if metric in result['result']['performance_metrics']:
                    values[replay_id] = result['result']['performance_metrics'][metric]
            
            comparison['values'][metric] = values
            
            # 排名（对于收益类指标越高越好，回撤类指标越小越好）
            if metric in ['max_drawdown']:  # 越小越好
                ranked = sorted(values.items(), key=lambda x: x[1])
            else:  # 越大越好
                ranked = sorted(values.items(), key=lambda x: x[1], reverse=True)
            
            comparison['rankings'][metric] = {
                item[0]: i + 1 for i, item in enumerate(ranked)
            }
        
        return comparison
    
    def _compare_trades(self, results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """比较交易记录"""
        trade_stats = {}
        
        for replay_id, result in results.items():
            trades = result['result'].get('trades', [])
            trade_stats[replay_id] = {
                'total_trades': len(trades),
                'buy_trades': len([t for t in trades if t.get('action') in ['BUY', 'buy']]),
                'sell_trades': len([t for t in trades if t.get('action') in ['SELL', 'sell']]),
                'avg_trade_size': np.mean([t.get('shares', 0) for t in trades]) if trades else 0,
                'total_volume': sum(t.get('shares', 0) for t in trades)
            }
        
        return trade_stats
    
    def _calculate_overall_scores(self, results: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
        """计算综合评分（0-100）"""
        scores = {}
        
        for replay_id, result in results.items():
            perf = result['result']['performance_metrics']
            
            # 评分规则
            score = 50  # 基础分
            
            # 总收益贡献（最高+30分）
            total_return = perf.get('total_return', 0)
            if total_return > 0:
                score += min(total_return * 100, 30)  # 每1%收益加1分，最高30分
            
            # 夏普比率贡献（最高+20分）
            sharpe = perf.get('sharpe_ratio', 0)
            if sharpe > 0:
                score += min(sharpe * 10, 20)  # 夏普每0.1加1分，最高20分
            
            # 最大回撤惩罚（最高-20分）
            max_dd = abs(perf.get('max_drawdown', 0))
            if max_dd > 0.1:  # 回撤超过10%开始惩罚
                penalty = min((max_dd - 0.1) * 100, 20)  # 每1%额外回撤减1分，最高20分
                score -= penalty
            
            # 胜率贡献（最高+10分）
            win_rate = perf.get('win_rate', 0)
            if win_rate > 0.5:
                score += min((win_rate - 0.5) * 20, 10)  # 胜率超过50%部分每2.5%加1分
            
            # 确保分数在0-100之间
            score = max(0, min(100, score))
            scores[replay_id] = round(score, 2)
        
        return scores
    
    def _identify_best_replay(self, scores: Dict[str, float]) -> str:
        """识别最佳回放"""
        if not scores:
            return ''
        return max(scores.items(), key=lambda x: x[1])[0]
    
    def _identify_worst_replay(self, scores: Dict[str, float]) -> str:
        """识别最差回放"""
        if not scores:
            return ''
        return min(scores.items(), key=lambda x: x[1])[0]


class ReplayReportGenerator:
    """回放报告生成器"""
    
    def __init__(self, output_dir: str = None):
        if output_dir is None:
            output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'replay_reports')
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
    
    def generate_comparison_report(self, comparison_result: Dict[str, Any], 
                                  format: str = 'html') -> str:
        """生成对比报告"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        if format == 'html':
            return self._generate_html_report(comparison_result, timestamp)
        elif format == 'markdown':
            return self._generate_markdown_report(comparison_result, timestamp)
        else:
            return self._generate_json_report(comparison_result, timestamp)
    
    def _generate_html_report(self, comparison: Dict[str, Any], timestamp: str) -> str:
        """生成HTML报告"""
        file_path = os.path.join(self.output_dir, f'comparison_report_{timestamp}.html')
        
        html_content = f'''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>策略回放对比报告</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .metric-card {{ background: #f5f5f5; padding: 15px; margin: 10px 0; border-radius: 5px; }}
        .best {{ border-left: 5px solid #4CAF50; }}
        .worst {{ border-left: 5px solid #f44336; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
    </style>
</head>
<body>
    <h1>策略回放对比报告</h1>
    <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    
    <h2>综合评分</h2>
    <table>
        <tr>
            <th>回放ID</th>
            <th>综合评分</th>
            <th>状态</th>
        </tr>
        {"".join([
            f'<tr>'
            f'<td>{replay_id}</td>'
            f'<td>{score}</td>'
            f'<td>{"🏆 最佳" if replay_id == comparison.get("best_replay") else "⚠️ 最差" if replay_id == comparison.get("worst_replay") else ""}</td>'
            f'</tr>'
            for replay_id, score in comparison.get('overall_scores', {}).items()
        ])}
    </table>
    
    <h2>性能指标对比</h2>
    {"".join([
        f'<div class="metric-card">'
        f'<h3>{metric}</h3>'
        f'<table>'
        f'<tr><th>回放ID</th><th>值</th><th>排名</th></tr>'
        {"".join([
            f'<tr>'
            f'<td>{replay_id}</td>'
            f'<td>{value:.4f}</td>'
            f'<td>{ranking}</td>'
            f'</tr>'
            for replay_id, value in values.items()
        ])}
        f'</table>'
        f'</div>'
        for metric, values in comparison.get('performance_comparison', {}).get('values', {}).items()
    ])}
    
    <h2>交易统计对比</h2>
    <table>
        <tr>
            <th>回放ID</th>
            <th>总交易数</th>
            <th>买入交易</th>
            <th>卖出交易</th>
            <th>平均交易量</th>
            <th>总交易量</th>
        </tr>
        {"".join([
            f'<tr>'
            f'<td>{replay_id}</td>'
            f'<td>{stats["total_trades"]}</td>'
            f'<td>{stats["buy_trades"]}</td>'
            f'<td>{stats["sell_trades"]}</td>'
            f'<td>{stats["avg_trade_size"]:.0f}</td>'
            f'<td>{stats["total_volume"]:.0f}</td>'
            f'</tr>'
            for replay_id, stats in comparison.get('trade_comparison', {}).items()
        ])}
    </table>
</body>
</html>
        '''
        
        with open(file_path, 'w') as f:
            f.write(html_content)
        
        return file_path
    
    def _generate_markdown_report(self, comparison: Dict[str, Any], timestamp: str) -> str:
        """生成Markdown报告"""
        file_path = os.path.join(self.output_dir, f'comparison_report_{timestamp}.md')
        
        with open(file_path, 'w') as f:
            f.write(f'# 策略回放对比报告\n\n')
            f.write(f'生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n\n')
            
            f.write(f'## 综合评分\n\n')
            f.write(f'| 回放ID | 综合评分 | 状态 |\n')
            f.write(f'|--------|----------|------|\n')
            for replay_id, score in comparison.get('overall_scores', {}).items():
                status = ''
                if replay_id == comparison.get('best_replay'):
                    status = '🏆 最佳'
                elif replay_id == comparison.get('worst_replay'):
                    status = '⚠️ 最差'
                f.write(f'| {replay_id} | {score} | {status} |\n')
            
            f.write(f'\n## 性能指标对比\n\n')
            for metric, values in comparison.get('performance_comparison', {}).get('values', {}).items():
                f.write(f'### {metric}\n\n')
                f.write(f'| 回放ID | 值 | 排名 |\n')
                f.write(f'|--------|----|------|\n')
                for replay_id, value in values.items():
                    ranking = comparison['performance_comparison']['rankings'][metric].get(replay_id, '-')
                    f.write(f'| {replay_id} | {value:.4f} | {ranking} |\n')
                f.write(f'\n')
        
        return file_path
    
    def _generate_json_report(self, comparison: Dict[str, Any], timestamp: str) -> str:
        """生成JSON报告"""
        file_path = os.path.join(self.output_dir, f'comparison_report_{timestamp}.json')
        
        with open(file_path, 'w') as f:
            json.dump(comparison, f, indent=2, default=str)
        
        return file_path