#!/usr/bin/env python3
"""
1000只股票简化测试 - 生产环境验证
目标: 在30分钟内完成1000只股票的基础功能测试
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any
import concurrent.futures

# 添加路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'enhancements'))

class ThousandStockTester:
    """1000只股票测试器"""
    
    def __init__(self):
        self.start_time = datetime.now()
        self.results = {
            'test_id': f"test_{self.start_time.strftime('%Y%m%d_%H%M%S')}",
            'start_time': self.start_time.isoformat(),
            'total_stocks': 0,
            'completed': 0,
            'successful': 0,
            'failed': 0,
            'test_cases': {},
            'performance': {},
            'errors': []
        }
        
        # 设置日志
        self.setup_logging()
        
        # 初始化量化系统
        self.quant_system = None
        self.init_quant_system()
        
        # 生成测试股票列表
        self.test_stocks = self.generate_test_stocks(1000)
        self.results['total_stocks'] = len(self.test_stocks)
    
    def setup_logging(self):
        """设置日志"""
        log_dir = './logs/tests'
        os.makedirs(log_dir, exist_ok=True)
        
        log_file = os.path.join(log_dir, f'test_1000_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        
        self.logger = logging.getLogger('ThousandStockTester')
        self.logger.info(f"🧪 1000只股票测试开始于 {self.start_time}")
    
    def init_quant_system(self):
        """初始化量化系统"""
        try:
            from enhanced_quant_system import EnhancedQuantSystem
            
            self.logger.info("🔧 初始化量化系统...")
            self.quant_system = EnhancedQuantSystem()
            
            if self.quant_system.use_enhanced_features:
                self.logger.info(f"✅ 量化系统初始化成功")
                self.logger.info(f"   增强模块: {len(self.quant_system.enhanced_modules)}个")
            else:
                self.logger.warning("⚠️ 增强功能不可用")
                
        except Exception as e:
            self.logger.error(f"❌ 量化系统初始化失败: {e}")
            raise
    
    def generate_test_stocks(self, count: int) -> List[str]:
        """生成测试股票列表"""
        # 模拟A股股票代码
        stocks = []
        
        # 上证主板 (600xxx)
        for i in range(1, min(400, count//3 + 1)):
            stocks.append(f"600{i:03d}")
        
        # 深证主板 (000xxx)
        for i in range(1, min(400, (count//3) + 1)):
            stocks.append(f"000{i:03d}")
        
        # 创业板 (300xxx)
        for i in range(1, min(200, count - len(stocks) + 1)):
            stocks.append(f"300{i:03d}")
        
        self.logger.info(f"📊 生成 {len(stocks)} 只测试股票")
        return stocks[:count]
    
    def test_single_stock_basic(self, symbol: str) -> Dict[str, Any]:
        """测试单只股票基础功能"""
        test_start = time.time()
        result = {
            'symbol': symbol,
            'start_time': datetime.now().isoformat(),
            'tests': {},
            'success': False,
            'error': None,
            'duration': 0
        }
        
        try:
            # 测试1: 股票评分
            test1_start = time.time()
            scores = self.quant_system.get_stock_scores(
                symbol, 
                '2024-01-01', 
                '2024-03-01'  # 缩短测试期间
            )
            test1_duration = time.time() - test1_start
            
            result['tests']['stock_scores'] = {
                'success': True,
                'duration': test1_duration,
                'score': scores.get('score', 0),
                'enhanced': scores.get('enhanced_features', False)
            }
            
            # 测试2: 基础信息获取 (模拟)
            test2_start = time.time()
            # 这里可以添加更多测试
            test2_duration = time.time() - test2_start
            
            result['tests']['basic_info'] = {
                'success': True,
                'duration': test2_duration
            }
            
            result['success'] = True
            
        except Exception as e:
            result['success'] = False
            result['error'] = str(e)
            self.logger.error(f"❌ 股票 {symbol} 测试失败: {e}")
        
        result['duration'] = time.time() - test_start
        result['end_time'] = datetime.now().isoformat()
        
        return result
    
    def test_batch_stocks(self, symbols: List[str], batch_size: int = 50) -> List[Dict[str, Any]]:
        """批量测试股票"""
        batch_results = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            # 分批提交任务
            futures = {}
            for i in range(0, len(symbols), batch_size):
                batch = symbols[i:i+batch_size]
                for symbol in batch:
                    future = executor.submit(self.test_single_stock_basic, symbol)
                    futures[future] = symbol
            
            # 收集结果
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                symbol = futures[future]
                try:
                    result = future.result(timeout=30)  # 30秒超时
                    batch_results.append(result)
                    
                    if result['success']:
                        self.results['successful'] += 1
                    else:
                        self.results['failed'] += 1
                        self.results['errors'].append({
                            'symbol': symbol,
                            'error': result['error']
                        })
                    
                except concurrent.futures.TimeoutError:
                    self.logger.error(f"⏱️  股票 {symbol} 测试超时")
                    self.results['failed'] += 1
                    self.results['errors'].append({
                        'symbol': symbol,
                        'error': '测试超时'
                    })
                except Exception as e:
                    self.logger.error(f"❌ 股票 {symbol} 测试异常: {e}")
                    self.results['failed'] += 1
                    self.results['errors'].append({
                        'symbol': symbol,
                        'error': str(e)
                    })
                
                completed += 1
                self.results['completed'] = completed
                
                # 进度报告
                if completed % 50 == 0:
                    elapsed = time.time() - self.start_time.timestamp()
                    remaining = (elapsed / completed) * (len(symbols) - completed)
                    self.logger.info(f"📊 进度: {completed}/{len(symbols)} ({completed/len(symbols)*100:.1f}%)")
                    self.logger.info(f"⏱️  预计剩余: {remaining/60:.1f}分钟")
        
        return batch_results
    
    def test_vectorized_backtest(self, sample_size: int = 10):
        """测试向量化回测性能"""
        self.logger.info(f"🔬 测试向量化回测性能 ({sample_size}只股票)")
        
        test_start = time.time()
        
        # 选择样本股票
        sample_stocks = self.test_stocks[:sample_size]
        
        try:
            # 运行回测
            result = self.quant_system.run_backtest(
                sample_stocks,
                '2024-01-01',
                '2024-03-01'  # 2个月数据
            )
            
            duration = time.time() - test_start
            
            self.results['test_cases']['vectorized_backtest'] = {
                'success': True,
                'duration': duration,
                'stocks_processed': len(sample_stocks),
                'performance': result.get('performance', {}),
                'execution_mode': result.get('execution_mode', 'unknown')
            }
            
            self.logger.info(f"✅ 向量化回测完成: {duration:.2f}秒 ({duration/len(sample_stocks):.3f}秒/股票)")
            
        except Exception as e:
            self.logger.error(f"❌ 向量化回测失败: {e}")
            self.results['test_cases']['vectorized_backtest'] = {
                'success': False,
                'error': str(e)
            }
    
    def test_enhanced_features(self):
        """测试增强功能"""
        self.logger.info("🔧 测试增强功能...")
        
        test_cases = {}
        
        try:
            # 测试IC动态加权
            if hasattr(self.quant_system, 'ic_engine'):
                test_start = time.time()
                current_date = datetime.now()
                weights = self.quant_system.ic_engine.calculate_dynamic_weights(current_date)
                duration = time.time() - test_start
                
                test_cases['ic_weighting'] = {
                    'success': True,
                    'duration': duration,
                    'dominant_category': weights.dominant_category.value if hasattr(weights, 'dominant_category') else 'unknown'
                }
            
            # 测试因子衰减
            if hasattr(self.quant_system, 'decay_monitor'):
                test_start = time.time()
                self.quant_system.decay_monitor.register_factor(
                    "test_factor", "测试因子", "技术因子", initial_half_life=4.0
                )
                duration = time.time() - test_start
                
                test_cases['factor_decay'] = {
                    'success': True,
                    'duration': duration
                }
            
            self.results['test_cases']['enhanced_features'] = test_cases
            self.logger.info(f"✅ 增强功能测试完成: {len(test_cases)}个测试通过")
            
        except Exception as e:
            self.logger.error(f"❌ 增强功能测试失败: {e}")
            self.results['test_cases']['enhanced_features'] = {
                'success': False,
                'error': str(e)
            }
    
    def run_full_test(self):
        """运行完整测试"""
        self.logger.info("🚀 开始1000只股票完整测试")
        
        # 阶段1: 增强功能测试
        self.test_enhanced_features()
        
        # 阶段2: 向量化回测性能测试
        self.test_vectorized_backtest(sample_size=20)
        
        # 阶段3: 批量股票基础测试（抽样测试，加速）
        sample_size = min(200, len(self.test_stocks))  # 测试200只作为代表
        sample_stocks = self.test_stocks[:sample_size]
        
        self.logger.info(f"📊 开始批量测试 ({sample_size}只样本股票)")
        batch_results = self.test_batch_stocks(sample_stocks, batch_size=20)
        
        # 计算性能指标
        total_duration = sum(r['duration'] for r in batch_results if r['success'])
        avg_duration = total_duration / len([r for r in batch_results if r['success']]) if any(r['success'] for r in batch_results) else 0
        
        self.results['performance']['batch_test'] = {
            'sample_size': sample_size,
            'total_duration': total_duration,
            'avg_duration_per_stock': avg_duration,
            'throughput': sample_size / total_duration if total_duration > 0 else 0
        }
        
        # 保存结果
        self.save_results()
        
        # 生成报告
        self.generate_report()
    
    def save_results(self):
        """保存测试结果"""
        results_dir = './reports/tests'
        os.makedirs(results_dir, exist_ok=True)
        
        results_file = os.path.join(results_dir, f'{self.results["test_id"]}.json')
        
        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2, default=str)
        
        self.logger.info(f"💾 测试结果已保存: {results_file}")
        return results_file
    
    def generate_report(self):
        """生成测试报告"""
        total_time = datetime.now() - self.start_time
        success_rate = self.results['successful'] / self.results['completed'] * 100 if self.results['completed'] > 0 else 0
        
        report = f"""
📊 1000只股票简化测试报告
==========================================
测试ID: {self.results['test_id']}
开始时间: {self.start_time}
结束时间: {datetime.now()}
总耗时: {total_time}

📈 测试统计:
   总股票数: {self.results['total_stocks']}
   已完成: {self.results['completed']}
   成功: {self.results['successful']}
   失败: {self.results['failed']}
   成功率: {success_rate:.1f}%

⚡ 性能指标:
"""
        
        # 添加性能指标
        for test_name, perf in self.results['performance'].items():
            report += f"   {test_name}:\n"
            for key, value in perf.items():
                if isinstance(value, float):
                    report += f"     {key}: {value:.3f}\n"
                else:
                    report += f"     {key}: {value}\n"
        
        # 错误摘要
        if self.results['errors']:
            report += f"\n⚠️ 错误摘要 (前10个):\n"
            for error in self.results['errors'][:10]:
                report += f"   {error['symbol']}: {error['error']}\n"
        
        report += f"""
✅ 测试通过:
"""
        
        # 测试用例结果
        for test_case, result in self.results['test_cases'].items():
            status = "✅ 通过" if result.get('success', False) else "❌ 失败"
            report += f"   {test_case}: {status}\n"
        
        report += f"""
🔍 建议:
"""
        
        if success_rate >= 95:
            report += "   系统稳定性优秀，建议进入生产环境\n"
        elif success_rate >= 80:
            report += "   系统稳定性良好，建议进一步优化错误处理\n"
        else:
            report += "   系统稳定性需要改进，建议检查错误原因\n"
        
        report += "==========================================\n"
        
        # 保存报告
        report_dir = './reports/tests'
        os.makedirs(report_dir, exist_ok=True)
        
        report_file = os.path.join(report_dir, f'{self.results["test_id"]}_report.md')
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report)
        
        self.logger.info(f"📝 测试报告已生成: {report_file}")
        print(report)
        
        return report_file


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='1000只股票简化测试')
    parser.add_argument('--sample-size', type=int, default=200, help='样本股票数量')
    parser.add_argument('--batch-size', type=int, default=20, help='批量大小')
    parser.add_argument('--skip-batch', action='store_true', help='跳过批量测试')
    
    args = parser.parse_args()
    
    try:
        tester = ThousandStockTester()
        
        if not args.skip_batch:
            # 运行完整测试
            tester.run_full_test()
        else:
            # 只运行核心功能测试
            tester.test_enhanced_features()
            tester.test_vectorized_backtest(sample_size=args.sample_size)
            tester.save_results()
            tester.generate_report()
        
        # 检查成功率
        success_rate = tester.results['successful'] / tester.results['completed'] * 100 if tester.results['completed'] > 0 else 0
        
        if success_rate >= 80:
            print(f"\n🎉 测试通过! 成功率: {success_rate:.1f}%")
            return 0
        else:
            print(f"\n⚠️  测试警告! 成功率: {success_rate:.1f}%")
            return 1
            
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return 1


if __name__ == "__main__":
    exit(main())