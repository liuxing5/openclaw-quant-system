#!/bin/bash
# Evolver离线模式运行脚本
# 解决系统负载过高问题，完全离线运行

cd /root/.openclaw/workspace/skills/evolver

# 配置离线模式环境变量
export A2A_NODE_ID=local_offline_$(date +%s)_$RANDOM
export EVOLVE_STRATEGY=repair-only
export EVOLVER_ROLLBACK_MODE=stash
export EVOLVER_LLM_REVIEW=0
export EVOLVER_AUTO_ISSUE=0
export A2A_HUB_URL=http://localhost:9999  # 虚假地址，禁用网络
export EVOLVER_DEBUG=1
export EVOLVE_ALLOW_SELF_MODIFY=false

# 提高负载阈值，避免退避
export EVOLVE_LOAD_MAX=5.0  # 提高负载上限

# Git配置 - 使用工作空间的.git目录
export EVOLVER_USE_PARENT_GIT=true
export EVOLVER_REPO_ROOT=/root/.openclaw/workspace

# 创建必要的目录结构
mkdir -p /root/.openclaw/workspace/memory/evolution
mkdir -p ~/.evomap

echo "========================================"
echo "🧬 Evolver离线模式启动"
echo "========================================"
echo "节点ID: $A2A_NODE_ID"
echo "策略: $EVOLVE_STRATEGY (修复优先)"
echo "负载阈值: $EVOLVE_LOAD_MAX"
echo "回滚模式: $EVOLVER_ROLLBACK_MODE"
echo "网络地址: $A2A_HUB_URL (离线)"
echo "========================================"
echo "当前系统负载: $(uptime | awk -F'load average:' '{print $2}')"
echo "开始分析工作空间并自我进化..."

# 运行Evolver，启用详细日志
timeout 120 node index.js run 2>&1