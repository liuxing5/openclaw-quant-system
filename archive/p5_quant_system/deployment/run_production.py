#!/usr/bin/env python3
"""
生产环境运行脚本 - EnhancedQuantSystem 主服务
"""

import os
import sys
import time
import signal
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import json

# 添加路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'enhancements'))

class ProductionRunner:
    """生产环境运行器"""
    
    def __init__(self, config_path: Optional[str] = None):
        self.running = True
        self.start_time = datetime.now()
        
        # 设置信号处理
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        # 加载配置
        self.config = self.load_config(config_path)
        
        # 设置日志
        self.setup_logging()
        
        # 初始化量化系统
        self.quant_system = None
        self.init_quant_system()
        
        # 监控数据
        self.metrics = {
            'start_time': self.start_time.isoformat(),
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'last_error': None,
            'system_status': 'initializing'
        }
    
    def load_config(self, config_path: Optional[str] = None):
        """加载配置"""
        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), 'production_config.json')
        
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            # 默认配置
            from production_config import ProductionConfig
            config_obj = ProductionConfig()
            return {
                'metadata': {'generated_at': datetime.now().isoformat()},
                'config': config_obj.config
            }
    
    def setup_logging(self):
        """设置日志"""
        log_dir = self.config['config']['output']['logs_dir']
        os.makedirs(log_dir, exist_ok=True)
        
        log_file = os.path.join(log_dir, f'system_{datetime.now().strftime("%Y%m%d")}.log')
        
        logging.basicConfig(
            level=getattr(logging, self.config['config']['system']['log_level']),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        
        self.logger = logging.getLogger('EnhancedQuantSystem')
        self.logger.info(f"🚀 生产环境运行器启动于 {self.start_time}")
    
    def init_quant_system(self):
        """初始化量化系统"""
        try:
            from enhanced_quant_system import EnhancedQuantSystem
            
            self.logger.info("🔧 初始化EnhancedQuantSystem...")
            self.quant_system = EnhancedQuantSystem()
            
            if self.quant_system.use_enhanced_features:
                self.logger.info(f"✅ EnhancedQuantSystem 初始化成功")
                self.logger.info(f"   增强模块: {len(self.quant_system.enhanced_modules)}个")
                
                # 运行快速测试
                self.logger.info("🧪 运行快速功能测试...")
                self.quant_system.run_quick_test()
                self.logger.info("✅ 快速测试通过")
            else:
                self.logger.warning("⚠️ 增强功能不可用，使用基础功能")
            
            self.metrics['system_status'] = 'ready'
            
        except Exception as e:
            self.logger.error(f"❌ 量化系统初始化失败: {e}")
            self.metrics['system_status'] = 'error'
            self.metrics['last_error'] = str(e)
    
    def signal_handler(self, signum, frame):
        """信号处理"""
        self.logger.info(f"📡 收到信号 {signum}, 准备关闭...")
        self.running = False
    
    def run_health_check(self):
        """运行健康检查"""
        health = {
            'timestamp': datetime.now().isoformat(),
            'status': 'healthy',
            'checks': []
        }
        
        # 检查量化系统
        if self.quant_system is None:
            health['status'] = 'unhealthy'
            health['checks'].append({'name': 'quant_system', 'status': 'error', 'message': '系统未初始化'})
        elif self.quant_system.use_enhanced_features:
            health['checks'].append({'name': 'quant_system', 'status': 'healthy', 'message': f'{len(self.quant_system.enhanced_modules)}个模块运行正常'})
        else:
            health['checks'].append({'name': 'quant_system', 'status': 'warning', 'message': '使用基础功能'})
        
        # 检查内存使用（简化）
        try:
            import psutil
            memory = psutil.virtual_memory()
            memory_check = {
                'name': 'memory',
                'status': 'healthy' if memory.percent < 80 else 'warning',
                'message': f'{memory.percent}% 使用率'
            }
            health['checks'].append(memory_check)
        except ImportError:
            health['checks'].append({'name': 'memory', 'status': 'unknown', 'message': 'psutil未安装'})
        
        # 检查运行时间
        uptime = datetime.now() - self.start_time
        health['checks'].append({
            'name': 'uptime',
            'status': 'healthy',
            'message': str(uptime).split('.')[0]
        })
        
        # 更新状态
        if any(check['status'] in ['error', 'critical'] for check in health['checks']):
            health['status'] = 'unhealthy'
        elif any(check['status'] == 'warning' for check in health['checks']):
            health['status'] = 'warning'
        
        return health
    
    def run_daily_tasks(self):
        """运行每日任务"""
        current_hour = datetime.now().hour
        
        # 每天17:00生成报告
        if current_hour == 17 and datetime.now().minute < 5:
            self.logger.info("📊 生成每日报告...")
            try:
                report = self.quant_system.generate_daily_report()
                report_dir = self.config['config']['output']['reports_dir']
                os.makedirs(report_dir, exist_ok=True)
                
                report_file = os.path.join(report_dir, f'daily_report_{datetime.now().strftime("%Y%m%d_%H%M")}.json')
                with open(report_file, 'w', encoding='utf-8') as f:
                    json.dump(report, f, indent=2, default=str)
                
                self.logger.info(f"✅ 每日报告已保存: {report_file}")
            except Exception as e:
                self.logger.error(f"❌ 生成每日报告失败: {e}")
    
    def save_metrics(self):
        """保存监控指标"""
        metrics_file = os.path.join(self.config['config']['output']['logs_dir'], 'metrics.json')
        
        metrics_data = {
            'timestamp': datetime.now().isoformat(),
            'metrics': self.metrics,
            'health': self.run_health_check()
        }
        
        with open(metrics_file, 'w', encoding='utf-8') as f:
            json.dump(metrics_data, f, indent=2, default=str)
    
    def run(self):
        """主运行循环"""
        self.logger.info("🚀 进入主运行循环")
        
        check_interval = self.config['config']['monitoring']['health_check_interval']
        last_health_check = time.time()
        last_metrics_save = time.time()
        
        while self.running:
            current_time = time.time()
            
            # 健康检查
            if current_time - last_health_check >= check_interval:
                health = self.run_health_check()
                if health['status'] != 'healthy':
                    self.logger.warning(f"⚠️ 健康检查: {health['status']}")
                    for check in health['checks']:
                        if check['status'] in ['warning', 'error']:
                            self.logger.warning(f"  - {check['name']}: {check['message']}")
                
                last_health_check = current_time
            
            # 保存指标
            if current_time - last_metrics_save >= 300:  # 5分钟
                self.save_metrics()
                last_metrics_save = current_time
            
            # 运行每日任务
            self.run_daily_tasks()
            
            # 等待
            time.sleep(1)
        
        # 关闭处理
        self.shutdown()
    
    def shutdown(self):
        """关闭系统"""
        self.logger.info("🛑 正在关闭系统...")
        
        # 保存最终指标
        self.save_metrics()
        
        # 计算运行时间
        run_time = datetime.now() - self.start_time
        self.logger.info(f"📊 系统运行时间: {run_time}")
        self.logger.info(f"📊 总请求数: {self.metrics['total_requests']}")
        self.logger.info(f"📊 成功率: {self.metrics['successful_requests']/(self.metrics['total_requests'] or 1)*100:.1f}%")
        
        self.logger.info("👋 系统关闭完成")
    
    def run_test_scenario(self, test_name: str, **kwargs):
        """运行测试场景"""
        self.logger.info(f"🧪 运行测试场景: {test_name}")
        
        try:
            if test_name == 'quick_test':
                result = self.quant_system.run_quick_test()
                return {'status': 'success', 'result': '快速测试通过'}
            
            elif test_name == 'stock_scores':
                symbol = kwargs.get('symbol', '600519')
                start_date = kwargs.get('start_date', '2024-01-01')
                end_date = kwargs.get('end_date', '2024-12-31')
                
                scores = self.quant_system.get_stock_scores(symbol, start_date, end_date)
                return {'status': 'success', 'scores': scores}
            
            elif test_name == 'backtest':
                symbols = kwargs.get('symbols', ['TEST001', 'TEST002', 'TEST003'])
                start_date = kwargs.get('start_date', '2024-01-01')
                end_date = kwargs.get('end_date', '2024-06-30')
                
                result = self.quant_system.run_backtest(symbols, start_date, end_date)
                return {'status': 'success', 'result': result}
            
            elif test_name == 'full_market':
                # 全市场测试（简化）
                start_date = kwargs.get('start_date', '2024-01-01')
                end_date = kwargs.get('end_date', '2024-03-01')
                
                result = self.quant_system.run_backtest([], start_date, end_date)  # 空列表触发全市场
                return {'status': 'success', 'result': result}
            
            else:
                return {'status': 'error', 'message': f'未知测试场景: {test_name}'}
                
        except Exception as e:
            self.logger.error(f"❌ 测试场景失败: {test_name} - {e}")
            return {'status': 'error', 'message': str(e)}


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='EnhancedQuantSystem 生产环境运行器')
    parser.add_argument('--config', type=str, help='配置文件路径')
    parser.add_argument('--test', type=str, help='运行测试场景')
    parser.add_argument('--test-symbol', type=str, default='600519', help='测试股票代码')
    parser.add_argument('--duration', type=int, default=0, help='运行时长（秒），0表示持续运行')
    
    args = parser.parse_args()
    
    # 创建运行器
    runner = ProductionRunner(args.config)
    
    # 如果指定了测试，运行测试后退出
    if args.test:
        result = runner.run_test_scenario(
            args.test,
            symbol=args.test_symbol,
            start_date='2024-01-01',
            end_date='2024-12-31'
        )
        
        print(json.dumps(result, indent=2, default=str))
        return 0 if result['status'] == 'success' else 1
    
    # 否则进入主运行循环
    try:
        if args.duration > 0:
            # 运行指定时长
            print(f"⏱️  运行 {args.duration} 秒...")
            import threading
            
            def stop_after_duration():
                time.sleep(args.duration)
                runner.running = False
            
            timer_thread = threading.Thread(target=stop_after_duration)
            timer_thread.start()
        
        runner.run()
        
    except KeyboardInterrupt:
        print("\n👋 用户中断")
    except Exception as e:
        print(f"❌ 运行错误: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())