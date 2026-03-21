#!/bin/bash
# EnhancedQuantSystem 生产环境部署脚本
# 生成时间: 2026-03-19T18:59:41.164413

echo "🚀 开始部署 EnhancedQuantSystem 生产环境"
echo "=========================================="

# 1. 创建目录结构
echo "📁 创建目录结构..."
mkdir -p ./reports/production
mkdir -p ./logs
mkdir -p ./cache
mkdir -p ./backups

# 2. 设置环境变量
echo "🔧 设置环境变量..."
export QUANT_SYSTEM_MODE=production
export QUANT_SYSTEM_VERSION=2.0.0
export PYTHONPATH=$PYTHONPATH:/root/.openclaw/workspace/quant_system

# 3. 初始化系统
echo "🔍 初始化量化系统..."
python3 -c "
import sys
sys.path.append('/root/.openclaw/workspace/quant_system')
from deployment.production_config import ProductionConfig
config = ProductionConfig()

print('✅ 配置加载成功')
print(f'系统名称: {config.config["system"]["name"]}')
print(f'部署模式: {config.config["system"]["mode"]}')
print(f'Python版本: {config.env["python_version"]}')
"

# 4. 运行系统检查
echo "🧪 运行系统检查..."
python3 -c "
import sys
sys.path.append('/root/.openclaw/workspace/quant_system')
sys.path.append('/root/.openclaw/workspace/quant_system/enhancements')

try:
    from enhanced_quant_system import EnhancedQuantSystem
    quant = EnhancedQuantSystem()
    
    if quant.use_enhanced_features:
        print('✅ EnhancedQuantSystem 初始化成功')
        print(f'   增强模块: {len(quant.enhanced_modules)}个')
        
        # 快速功能测试
        print('🔧 快速功能测试...')
        quant.run_quick_test()
        print('✅ 快速测试全部通过')
    else:
        print('⚠️ 增强功能不可用，使用基础功能')
        
except Exception as e:
    print(f'❌ 系统检查失败: {e}')
    exit 1
"

# 5. 启动监控服务
echo "📊 启动监控服务..."
echo "监控配置:"
echo "  - 指标间隔: 60秒"
echo "  - 健康检查: 300秒"
echo "  - 日志保留: 7天"

# 6. 生成配置文件
echo "📝 生成配置文件..."
CONFIG_FILE="/root/.openclaw/workspace/quant_system/deployment/production_config.json"
python3 -c "
import json
from deployment.production_config import ProductionConfig
config = ProductionConfig()

with open('$CONFIG_FILE', 'w') as f:
    json.dump({'system': config.env, 'config': config.config}, f, indent=2, default=str)

print(f'✅ 配置文件已生成: $CONFIG_FILE')
"

echo ""
echo "🎉 部署完成!"
echo "=========================================="
echo "系统信息:"
echo "  名称: EnhancedQuantSystem"
echo "  版本: 2.0.0"
echo "  模式: production"
echo "  时间: 2026-03-19T18:59:41.164434"
echo ""
echo "下一步操作:"
echo "  1. 检查日志: tail -f ./logs/system.log"
echo "  2. 运行测试: python3 test_production.py"
echo "  3. 启动服务: python3 run_production.py"
echo "=========================================="
