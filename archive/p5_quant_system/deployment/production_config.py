#!/usr/bin/env python3
"""
生产环境配置 - 量化系统部署配置
"""

import os
import sys
from datetime import datetime
import json

class ProductionConfig:
    """生产环境配置类"""
    
    def __init__(self):
        self.config = self._load_default_config()
        self.env = self._detect_environment()
        
    def _detect_environment(self):
        """检测运行环境"""
        env_info = {
            'timestamp': datetime.now().isoformat(),
            'python_version': sys.version.split()[0],
            'platform': sys.platform,
            'cwd': os.getcwd(),
            'user': os.getenv('USER', 'unknown'),
            'hostname': os.getenv('HOSTNAME', 'unknown')
        }
        return env_info
    
    def _load_default_config(self):
        """加载默认配置"""
        return {
            # 系统配置
            'system': {
                'name': 'EnhancedQuantSystem',
                'version': '2.0.0',
                'deployment_time': datetime.now().isoformat(),
                'mode': 'production',
                'debug': False,
                'log_level': 'INFO'
            },
            
            # 数据源配置
            'data_sources': {
                'primary': 'local_database',
                'fallbacks': ['baostock', 'akshare', 'tushare', 'simulation'],
                'cache_enabled': True,
                'cache_ttl': 3600,  # 1小时
                'retry_times': 3,
                'retry_delay': 1.0
            },
            
            # 回测配置
            'backtest': {
                'initial_capital': 1000000.0,
                'commission_rate': 0.001,
                'slippage_rate': 0.002,
                'max_position_pct': 0.1,
                'stop_loss_pct': 0.10,
                'take_profit_pct': 0.20,
                'vectorized_enabled': True,
                'parallel_workers': 4,
                'max_memory_mb': 4096
            },
            
            # 因子配置
            'factors': {
                'ic_lookback_days': 20,
                'min_icir_for_weight': 0.1,
                'weight_smoothing': 0.3,
                'decay_monitoring_enabled': True,
                'effectiveness_threshold': 0.3,
                'default_half_life_technical': 4.0,
                'default_half_life_fundamental': 63.0
            },
            
            # 风险配置
            'risk': {
                'monte_carlo_simulations': 500,
                'confidence_level': 0.95,
                'timing_dependency_warning': 0.7,
                'max_drawdown_alarm': 0.25,
                'sharpe_ratio_min': 0.5
            },
            
            # 监控配置
            'monitoring': {
                'enabled': True,
                'metrics_interval': 60,  # 秒
                'log_retention_days': 7,
                'performance_alerts': True,
                'error_alerts': True,
                'health_check_interval': 300  # 5分钟
            },
            
            # 输出配置
            'output': {
                'reports_dir': './reports/production',
                'logs_dir': './logs',
                'cache_dir': './cache',
                'backup_dir': './backups',
                'daily_report_time': '17:00',
                'weekly_summary_day': 'Friday'
            },
            
            # 调度配置
            'scheduling': {
                'daily_report': '0 17 * * *',  # 每天17:00
                'weekly_rebalance': '0 9 * * 5',  # 每周五9:00
                'monthly_analysis': '0 10 1 * *',  # 每月1日10:00
                'health_check': '*/5 * * * *',  # 每5分钟
                'cache_cleanup': '0 2 * * *'  # 每天2:00
            }
        }
    
    def generate_deployment_script(self):
        """生成部署脚本"""
        script = f'''#!/bin/bash
# EnhancedQuantSystem 生产环境部署脚本
# 生成时间: {datetime.now().isoformat()}

echo "🚀 开始部署 EnhancedQuantSystem 生产环境"
echo "=========================================="

# 1. 创建目录结构
echo "📁 创建目录结构..."
mkdir -p {self.config['output']['reports_dir']}
mkdir -p {self.config['output']['logs_dir']}
mkdir -p {self.config['output']['cache_dir']}
mkdir -p {self.config['output']['backup_dir']}

# 2. 设置环境变量
echo "🔧 设置环境变量..."
export QUANT_SYSTEM_MODE=production
export QUANT_SYSTEM_VERSION={self.config['system']['version']}
export PYTHONPATH=$PYTHONPATH:{os.getcwd()}

# 3. 初始化系统
echo "🔍 初始化量化系统..."
python3 -c "
import sys
sys.path.append('{os.getcwd()}')
from deployment.production_config import ProductionConfig
config = ProductionConfig()

print('✅ 配置加载成功')
print(f'系统名称: {{config.config[\"system\"][\"name\"]}}')
print(f'部署模式: {{config.config[\"system\"][\"mode\"]}}')
print(f'Python版本: {{config.env[\"python_version\"]}}')
"

# 4. 运行系统检查
echo "🧪 运行系统检查..."
python3 -c "
import sys
sys.path.append('{os.getcwd()}')
sys.path.append('{os.getcwd()}/enhancements')

try:
    from enhanced_quant_system import EnhancedQuantSystem
    quant = EnhancedQuantSystem()
    
    if quant.use_enhanced_features:
        print('✅ EnhancedQuantSystem 初始化成功')
        print(f'   增强模块: {{len(quant.enhanced_modules)}}个')
        
        # 快速功能测试
        print('🔧 快速功能测试...')
        quant.run_quick_test()
        print('✅ 快速测试全部通过')
    else:
        print('⚠️ 增强功能不可用，使用基础功能')
        
except Exception as e:
    print(f'❌ 系统检查失败: {{e}}')
    exit 1
"

# 5. 启动监控服务
echo "📊 启动监控服务..."
echo "监控配置:"
echo "  - 指标间隔: {self.config['monitoring']['metrics_interval']}秒"
echo "  - 健康检查: {self.config['monitoring']['health_check_interval']}秒"
echo "  - 日志保留: {self.config['monitoring']['log_retention_days']}天"

# 6. 生成配置文件
echo "📝 生成配置文件..."
CONFIG_FILE="{os.getcwd()}/deployment/production_config.json"
python3 -c "
import json
from deployment.production_config import ProductionConfig
config = ProductionConfig()

with open('$CONFIG_FILE', 'w') as f:
    json.dump({{'system': config.env, 'config': config.config}}, f, indent=2, default=str)

print(f'✅ 配置文件已生成: $CONFIG_FILE')
"

echo ""
echo "🎉 部署完成!"
echo "=========================================="
echo "系统信息:"
echo "  名称: {self.config['system']['name']}"
echo "  版本: {self.config['system']['version']}"
echo "  模式: {self.config['system']['mode']}"
echo "  时间: {datetime.now().isoformat()}"
echo ""
echo "下一步操作:"
echo "  1. 检查日志: tail -f {self.config['output']['logs_dir']}/system.log"
echo "  2. 运行测试: python3 test_production.py"
echo "  3. 启动服务: python3 run_production.py"
echo "=========================================="
'''
        return script
    
    def save_config(self, path=None):
        """保存配置到文件"""
        if path is None:
            path = os.path.join(os.getcwd(), 'deployment', 'production_config.json')
        
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        config_data = {
            'metadata': {
                'generated_at': datetime.now().isoformat(),
                'system': self.env
            },
            'config': self.config
        }
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, default=str)
        
        return path
    
    def validate_config(self):
        """验证配置"""
        errors = []
        warnings = []
        
        # 检查必要目录
        required_dirs = [
            self.config['output']['reports_dir'],
            self.config['output']['logs_dir'],
            self.config['output']['cache_dir']
        ]
        
        for dir_path in required_dirs:
            if not os.path.exists(dir_path):
                warnings.append(f"目录不存在: {dir_path}")
        
        # 检查配置值范围
        if self.config['backtest']['max_position_pct'] > 0.5:
            warnings.append("单票最大仓位比例过高: >50%")
        
        if self.config['risk']['max_drawdown_alarm'] > 0.5:
            warnings.append("最大回撤警报阈值过高: >50%")
        
        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings
        }


def main():
    """主函数"""
    print("🏭 EnhancedQuantSystem 生产环境配置生成器")
    print("=" * 60)
    
    # 创建配置实例
    config = ProductionConfig()
    
    # 显示环境信息
    print("📊 环境信息:")
    for key, value in config.env.items():
        print(f"  {key}: {value}")
    
    # 验证配置
    validation = config.validate_config()
    print(f"\n🔍 配置验证: {'✅ 通过' if validation['valid'] else '❌ 失败'}")
    
    if validation['warnings']:
        print("⚠️ 警告:")
        for warning in validation['warnings']:
            print(f"  - {warning}")
    
    if validation['errors']:
        print("❌ 错误:")
        for error in validation['errors']:
            print(f"  - {error}")
        return 1
    
    # 保存配置
    config_path = config.save_config()
    print(f"\n💾 配置已保存: {config_path}")
    
    # 生成部署脚本
    script = config.generate_deployment_script()
    script_path = os.path.join(os.getcwd(), 'deployment', 'deploy_production.sh')
    
    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(script)
    
    os.chmod(script_path, 0o755)
    print(f"🚀 部署脚本已生成: {script_path}")
    
    print("\n" + "=" * 60)
    print("✅ 生产环境配置生成完成")
    print(f"\n下一步: 运行部署脚本")
    print(f"  bash {script_path}")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    exit(main())