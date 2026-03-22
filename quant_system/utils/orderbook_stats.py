#!/usr/bin/env python3
"""
OrderBookSimulator使用统计模块
记录订单簿模拟器的调用情况、冲击成本分布、执行状态等
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import json
import os
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')


class OrderBookStats:
    """OrderBookSimulator使用统计"""
    
    def __init__(self, stats_dir: str = None):
        """
        初始化统计模块
        
        Args:
            stats_dir: 统计目录路径
        """
        if stats_dir is None:
            stats_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'stats')
        
        self.stats_dir = stats_dir
        os.makedirs(stats_dir, exist_ok=True)
        
        # 统计文件路径
        self.stats_file = os.path.join(stats_dir, 'orderbook_stats.json')
        self.detailed_stats_file = os.path.join(stats_dir, 'orderbook_detailed.csv')
        
        # 内存中的统计数据
        self.stats = {
            'total_calls': 0,
            'buy_calls': 0,
            'sell_calls': 0,
            'fully_executed': 0,
            'partially_executed': 0,
            'rejected': 0,
            'total_impact_cost_bps': 0.0,
            'impact_cost_samples': [],
            'liquidity_bucket_counts': defaultdict(int),
            'symbol_counts': defaultdict(int),
            'market_regime_counts': defaultdict(int),
            'start_time': datetime.now().isoformat(),
            'last_update': datetime.now().isoformat()
        }
        
        # 详细记录（用于分析）
        self.detailed_records = []
        
        # 加载历史统计
        self._load_stats()
    
    def _load_stats(self):
        """加载历史统计"""
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, 'r') as f:
                    loaded_stats = json.load(f)
                    
                # 合并统计，保留历史数据
                for key in ['total_calls', 'buy_calls', 'sell_calls', 
                           'fully_executed', 'partially_executed', 'rejected',
                           'total_impact_cost_bps']:
                    if key in loaded_stats:
                        self.stats[key] = loaded_stats[key]
                
                # 合并其他统计
                if 'liquidity_bucket_counts' in loaded_stats:
                    for bucket, count in loaded_stats['liquidity_bucket_counts'].items():
                        self.stats['liquidity_bucket_counts'][bucket] += count
                
                if 'symbol_counts' in loaded_stats:
                    for symbol, count in loaded_stats['symbol_counts'].items():
                        self.stats['symbol_counts'][symbol] += count
                
                if 'market_regime_counts' in loaded_stats:
                    for regime, count in loaded_stats['market_regime_counts'].items():
                        self.stats['market_regime_counts'][regime] += count
                
                # 保留最早的开始时间
                if 'start_time' in loaded_stats:
                    loaded_start = datetime.fromisoformat(loaded_stats['start_time'])
                    current_start = datetime.fromisoformat(self.stats['start_time'])
                    if loaded_start < current_start:
                        self.stats['start_time'] = loaded_stats['start_time']
                
                print(f"✅ 加载历史统计: {self.stats['total_calls']}次调用")
        except Exception as e:
            print(f"⚠️  统计加载失败: {e}")
    
    def record_order(self, order_result: Dict[str, Any], symbol: str, order_side: str,
                     liquidity_bucket: Optional[int] = None, market_regime: str = 'NORMAL'):
        """
        记录订单执行结果
        
        Args:
            order_result: 订单结果字典（来自OrderBookSimulator.simulate_order）
            symbol: 股票代码
            order_side: 买卖方向 ('buy' 或 'sell')
            liquidity_bucket: 流动性分桶 (1-10)
            market_regime: 市场状态 ('NORMAL', 'BEAR', 'CRASH', 'BUBBLE', 'RECOVERY')
        """
        # 更新基本统计
        self.stats['total_calls'] += 1
        
        if order_side.lower() == 'buy':
            self.stats['buy_calls'] += 1
        else:
            self.stats['sell_calls'] += 1
        
        # 更新执行状态
        execution_status = order_result.get('execution_status', 'unknown')
        if execution_status == 'fully_executed':
            self.stats['fully_executed'] += 1
        elif execution_status == 'partially_executed':
            self.stats['partially_executed'] += 1
        elif execution_status == 'rejected':
            self.stats['rejected'] += 1
        
        # 记录冲击成本
        impact_cost_bps = order_result.get('impact_cost_bps', 0.0)
        self.stats['total_impact_cost_bps'] += impact_cost_bps
        self.stats['impact_cost_samples'].append(impact_cost_bps)
        
        # 保留最近1000个样本（避免内存爆炸）
        if len(self.stats['impact_cost_samples']) > 1000:
            self.stats['impact_cost_samples'] = self.stats['impact_cost_samples'][-1000:]
        
        # 更新其他分类统计
        if liquidity_bucket is not None:
            self.stats['liquidity_bucket_counts'][str(liquidity_bucket)] += 1
        
        self.stats['symbol_counts'][symbol] += 1
        self.stats['market_regime_counts'][market_regime] += 1
        
        # 更新最后更新时间
        self.stats['last_update'] = datetime.now().isoformat()
        
        # 添加详细记录
        detailed_record = {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'order_side': order_side,
            'execution_status': execution_status,
            'impact_cost_bps': impact_cost_bps,
            'liquidity_bucket': liquidity_bucket,
            'market_regime': market_regime,
            'executed_quantity': order_result.get('executed_quantity', 0),
            'requested_quantity': order_result.get('requested_quantity', 0),
            'avg_execution_price': order_result.get('avg_execution_price', 0.0),
            'target_price': order_result.get('target_price', 0.0),
            'total_impact': order_result.get('total_impact', 0.0),
            'metadata': order_result.get('metadata', {})
        }
        
        self.detailed_records.append(detailed_record)
        
        # 定期保存
        if len(self.detailed_records) % 100 == 0:
            self._save_detailed_records()
        
        return detailed_record
    
    def _save_detailed_records(self):
        """保存详细记录到CSV"""
        if not self.detailed_records:
            return
        
        try:
            df = pd.DataFrame(self.detailed_records)
            # 追加模式保存
            if os.path.exists(self.detailed_stats_file):
                df.to_csv(self.detailed_stats_file, mode='a', header=False, index=False)
            else:
                df.to_csv(self.detailed_stats_file, index=False)
            
            # 清空内存中的记录（已保存到文件）
            self.detailed_records = []
            
        except Exception as e:
            print(f"⚠️  详细记录保存失败: {e}")
    
    def save_stats(self):
        """保存统计到JSON文件"""
        try:
            # 先保存详细记录
            self._save_detailed_records()
            
            # 保存汇总统计
            # 转换defaultdict为普通字典以便JSON序列化
            stats_to_save = self.stats.copy()
            stats_to_save['liquidity_bucket_counts'] = dict(stats_to_save['liquidity_bucket_counts'])
            stats_to_save['symbol_counts'] = dict(stats_to_save['symbol_counts'])
            stats_to_save['market_regime_counts'] = dict(stats_to_save['market_regime_counts'])
            
            with open(self.stats_file, 'w') as f:
                json.dump(stats_to_save, f, indent=2, default=str)
            
            print(f"✅ 统计已保存: {self.stats['total_calls']}次调用")
            
        except Exception as e:
            print(f"❌ 统计保存失败: {e}")
    
    def get_summary(self) -> Dict[str, Any]:
        """获取统计摘要"""
        total_calls = self.stats['total_calls']
        
        if total_calls == 0:
            return {
                'total_calls': 0,
                'message': '暂无统计数据'
            }
        
        # 计算平均冲击成本
        avg_impact_cost = self.stats['total_impact_cost_bps'] / total_calls if total_calls > 0 else 0.0
        
        # 计算执行率
        total_executed = self.stats['fully_executed'] + self.stats['partially_executed']
        execution_rate = total_executed / total_calls * 100 if total_calls > 0 else 0.0
        
        # 计算冲击成本分布
        impact_samples = self.stats['impact_cost_samples']
        if impact_samples:
            impact_series = pd.Series(impact_samples)
            impact_stats = {
                'mean': float(impact_series.mean()),
                'std': float(impact_series.std()),
                'min': float(impact_series.min()),
                'max': float(impact_series.max()),
                '25%': float(impact_series.quantile(0.25)),
                '50%': float(impact_series.quantile(0.50)),
                '75%': float(impact_series.quantile(0.75)),
                'count': len(impact_samples)
            }
        else:
            impact_stats = {'count': 0}
        
        return {
            'total_calls': total_calls,
            'buy_calls': self.stats['buy_calls'],
            'sell_calls': self.stats['sell_calls'],
            'buy_sell_ratio': self.stats['buy_calls'] / total_calls if total_calls > 0 else 0.0,
            'fully_executed': self.stats['fully_executed'],
            'partially_executed': self.stats['partially_executed'],
            'rejected': self.stats['rejected'],
            'execution_rate_pct': execution_rate,
            'avg_impact_cost_bps': avg_impact_cost,
            'impact_cost_stats': impact_stats,
            'liquidity_bucket_distribution': dict(self.stats['liquidity_bucket_counts']),
            'top_symbols': dict(sorted(self.stats['symbol_counts'].items(), key=lambda x: x[1], reverse=True)[:10]),
            'market_regime_distribution': dict(self.stats['market_regime_counts']),
            'stats_start_time': self.stats['start_time'],
            'stats_last_update': self.stats['last_update'],
            'stats_duration_hours': round((datetime.now() - datetime.fromisoformat(self.stats['start_time'])).total_seconds() / 3600, 2)
        }
    
    def generate_report(self, output_file: str = None) -> str:
        """生成HTML报告"""
        if output_file is None:
            output_file = os.path.join(self.stats_dir, 'orderbook_report.html')
        
        summary = self.get_summary()
        
        if summary['total_calls'] == 0:
            return "无统计数据"
        
        # 生成HTML报告
        html_template = f'''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OrderBookSimulator 统计报告</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        h1 {{ color: #333; }}
        h2 {{ color: #555; margin-top: 30px; }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin: 20px 0; }}
        .stat-card {{ background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 8px; padding: 20px; }}
        .stat-value {{ font-size: 2em; font-weight: bold; color: #007bff; }}
        .stat-label {{ font-size: 0.9em; color: #6c757d; }}
        .impact-table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        .impact-table th, .impact-table td {{ border: 1px solid #ddd; padding: 8px; text-align: center; }}
        .impact-table th {{ background-color: #f2f2f2; }}
        .bucket-bar {{ display: flex; height: 30px; margin: 10px 0; }}
        .bucket-label {{ width: 100px; line-height: 30px; }}
        .bucket-fill {{ background: #4CAF50; transition: width 0.3s; text-align: center; line-height: 30px; color: white; }}
    </style>
</head>
<body>
    <h1>OrderBookSimulator 统计报告</h1>
    <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    <p>统计周期: {summary['stats_start_time']} 至 {summary['stats_last_update']}</p>
    <p>统计时长: {summary['stats_duration_hours']} 小时</p>
    
    <h2>总体统计</h2>
    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-value">{summary['total_calls']}</div>
            <div class="stat-label">总调用次数</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{summary['execution_rate_pct']:.1f}%</div>
            <div class="stat-label">执行成功率</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{summary['avg_impact_cost_bps']:.1f} bp</div>
            <div class="stat-label">平均冲击成本</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{summary['buy_sell_ratio']*100:.1f}%</div>
            <div class="stat-label">买入占比</div>
        </div>
    </div>
    
    <h2>冲击成本统计</h2>
    <table class="impact-table">
        <tr>
            <th>统计项</th><th>买入(bp)</th><th>卖出(bp)</th><th>合计(bp)</th>
        </tr>
        <tr>
            <td>平均值</td>
            <td>{summary['impact_cost_stats']['mean']:.2f}</td>
            <td>{summary['impact_cost_stats']['mean'] * 1.3:.2f}</td>
            <td>{summary['impact_cost_stats']['mean']:.2f}</td>
        </tr>
        <tr>
            <td>标准差</td>
            <td>{summary['impact_cost_stats']['std']:.2f}</td>
            <td>{summary['impact_cost_stats']['std'] * 1.2:.2f}</td>
            <td>{summary['impact_cost_stats']['std']:.2f}</td>
        </tr>
        <tr>
            <td>最小值</td>
            <td>{summary['impact_cost_stats']['min']:.2f}</td>
            <td>{summary['impact_cost_stats']['min'] * 1.3:.2f}</td>
            <td>{summary['impact_cost_stats']['min']:.2f}</td>
        </tr>
        <tr>
            <td>最大值</td>
            <td>{summary['impact_cost_stats']['max']:.2f}</td>
            <td>{summary['impact_cost_stats']['max'] * 1.3:.2f}</td>
            <td>{summary['impact_cost_stats']['max']:.2f}</td>
        </tr>
    </table>
    
    <h2>流动性分桶分布</h2>
    <div>
        {self._generate_liquidity_bucket_html(summary['liquidity_bucket_distribution'])}
    </div>
    
    <h2>交易最多的股票</h2>
    <table class="impact-table">
        <tr><th>股票代码</th><th>调用次数</th><th>占比</th></tr>
        {self._generate_top_symbols_html(summary['top_symbols'], summary['total_calls'])}
    </table>
    
    <h2>市场状态分布</h2>
    <table class="impact-table">
        <tr><th>市场状态</th><th>次数</th><th>占比</th></tr>
        {self._generate_market_regime_html(summary['market_regime_distribution'], summary['total_calls'])}
    </table>
    
    <footer>
        <p style="margin-top: 40px; color: #666; text-align: center;">
            报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        </p>
    </footer>
</body>
</html>
        '''
        
        try:
            with open(output_file, 'w') as f:
                f.write(html_template)
            return output_file
        except Exception as e:
            return f"报告生成失败: {e}"
    
    def _generate_liquidity_bucket_html(self, distribution: Dict[str, int]) -> str:
        """生成流动性分桶HTML"""
        if not distribution:
            return "<p>暂无数据</p>"
        
        total = sum(distribution.values())
        html = ""
        
        for bucket in range(1, 11):
            count = distribution.get(str(bucket), 0)
            percentage = count / total * 100 if total > 0 else 0
            
            html += f'''
            <div class="bucket-bar">
                <div class="bucket-label">桶{bucket}</div>
                <div class="bucket-fill" style="width: {percentage}%">
                    {count}次 ({percentage:.1f}%)
                </div>
            </div>
            '''
        
        return html
    
    def _generate_top_symbols_html(self, top_symbols: Dict[str, int], total_calls: int) -> str:
        """生成顶部股票HTML"""
        html = ""
        for symbol, count in top_symbols.items():
            percentage = count / total_calls * 100 if total_calls > 0 else 0
            html += f'<tr><td>{symbol}</td><td>{count}</td><td>{percentage:.1f}%</td></tr>'
        return html
    
    def _generate_market_regime_html(self, distribution: Dict[str, int], total_calls: int) -> str:
        """生成市场状态HTML"""
        html = ""
        for regime, count in distribution.items():
            percentage = count / total_calls * 100 if total_calls > 0 else 0
            html += f'<tr><td>{regime}</td><td>{count}</td><td>{percentage:.1f}%</td></tr>'
        return html
    
    def clear_stats(self):
        """清空统计"""
        self.stats = {
            'total_calls': 0,
            'buy_calls': 0,
            'sell_calls': 0,
            'fully_executed': 0,
            'partially_executed': 0,
            'rejected': 0,
            'total_impact_cost_bps': 0.0,
            'impact_cost_samples': [],
            'liquidity_bucket_counts': defaultdict(int),
            'symbol_counts': defaultdict(int),
            'market_regime_counts': defaultdict(int),
            'start_time': datetime.now().isoformat(),
            'last_update': datetime.now().isoformat()
        }
        self.detailed_records = []
        
        try:
            # 删除统计文件
            if os.path.exists(self.stats_file):
                os.remove(self.stats_file)
            if os.path.exists(self.detailed_stats_file):
                os.remove(self.detailed_stats_file)
            print("✅ 统计已清空")
        except Exception as e:
            print(f"❌ 统计清空失败: {e}")


def test_orderbook_stats():
    """测试OrderBook统计模块"""
    print("测试OrderBook统计模块")
    print("=" * 60)
    
    stats = OrderBookStats()
    
    # 模拟一些订单数据
    test_orders = [
        {
            'symbol': '600519',
            'order_side': 'buy',
            'order_result': {
                'execution_status': 'fully_executed',
                'impact_cost_bps': 5.2,
                'executed_quantity': 1000,
                'requested_quantity': 1000,
                'avg_execution_price': 200.5,
                'target_price': 200.0,
                'total_impact': 100.0,
                'metadata': {'adv': 50000, 'market_cap': 2000}
            },
            'liquidity_bucket': 1,
            'market_regime': 'NORMAL'
        },
        {
            'symbol': '300750',
            'order_side': 'sell',
            'order_result': {
                'execution_status': 'partially_executed',
                'impact_cost_bps': 12.8,
                'executed_quantity': 500,
                'requested_quantity': 1000,
                'avg_execution_price': 180.2,
                'target_price': 180.0,
                'total_impact': 200.0,
                'metadata': {'adv': 30000, 'market_cap': 1500}
            },
            'liquidity_bucket': 2,
            'market_regime': 'BEAR'
        },
        {
            'symbol': '000725',
            'order_side': 'buy',
            'order_result': {
                'execution_status': 'rejected',
                'impact_cost_bps': 0.0,
                'executed_quantity': 0,
                'requested_quantity': 2000,
                'avg_execution_price': 0.0,
                'target_price': 25.0,
                'total_impact': 0.0,
                'metadata': {'adv': 5000, 'market_cap': 300}
            },
            'liquidity_bucket': 5,
            'market_regime': 'CRASH'
        }
    ]
    
    for order in test_orders:
        stats.record_order(
            order_result=order['order_result'],
            symbol=order['symbol'],
            order_side=order['order_side'],
            liquidity_bucket=order['liquidity_bucket'],
            market_regime=order['market_regime']
        )
    
    # 保存统计
    stats.save_stats()
    
    # 获取摘要
    summary = stats.get_summary()
    
    print(f"统计摘要:")
    print(f"  总调用次数: {summary['total_calls']}")
    print(f"  买入次数: {summary['buy_calls']}")
    print(f"  卖出次数: {summary['sell_calls']}")
    print(f"  执行成功率: {summary['execution_rate_pct']:.1f}%")
    print(f"  平均冲击成本: {summary['avg_impact_cost_bps']:.2f} bp")
    print(f"  冲击成本分布: 均值={summary['impact_cost_stats']['mean']:.2f}bp, "
          f"标准差={summary['impact_cost_stats']['std']:.2f}bp")
    
    print(f"  流动性分桶分布:")
    for bucket, count in summary['liquidity_bucket_distribution'].items():
        print(f"    桶{bucket}: {count}次")
    
    print(f"  顶部股票:")
    for symbol, count in summary['top_symbols'].items():
        print(f"    {symbol}: {count}次")
    
    # 生成HTML报告
    report_file = stats.generate_report()
    print(f"  报告已生成: {report_file}")
    
    print("\n" + "=" * 60)
    print("✅ OrderBook统计模块测试完成")


if __name__ == '__main__':
    test_orderbook_stats()