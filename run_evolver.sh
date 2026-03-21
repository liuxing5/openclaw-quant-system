#!/bin/bash
# 运行Evolver进行自我进化

cd /root/.openclaw/workspace/skills/evolver

# 设置环境变量
export A2A_NODE_ID=test_node_local_$(date +%s)
export EVOLVE_STRATEGY=balanced
export EVOLVER_ROLLBACK_MODE=stash
export EVOLVER_LLM_REVIEW=0
export EVOLVER_AUTO_ISSUE=0
export EVOLVE_ALLOW_SELF_MODIFY=false
export EVOLVE_LOAD_MAX=2.0

# 确保.evomap目录存在
mkdir -p ~/.evomap

echo "启动Evolver自我进化引擎..."
echo "节点ID: $A2A_NODE_ID"
echo "策略: $EVOLVE_STRATEGY"
echo "开始分析运行历史并自我改进..."

# 以review模式运行，允许人工审核
timeout 300 node index.js run 2>&1