#!/bin/bash
# WSL2 快速部署脚本 - 在 WSL2 终端里直接运行

echo "=== AI Stock Recommender - WSL2 部署 ==="

# 1. 从 Windows 路径复制项目到 WSL2 家目录
echo "复制项目文件..."
cp -r /mnt/d/pythonProject/openclaw-quant-system/autorecommend ~/stock-recommender

# 2. 进入项目目录
cd ~/stock-recommender

# 3. 检查目录结构
echo "目录结构："
ls -la

echo ""
echo "=== 复制完成 ==="
echo "下一步："
echo "1. cd ~/stock-recommender"
echo "2. 编辑 .env 填入真实密钥"
echo "3. bash setup/run_all.sh"
