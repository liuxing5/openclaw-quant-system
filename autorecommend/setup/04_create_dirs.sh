#!/bin/bash
# Step 0.4 - Create Project Directory Structure
# Creates all necessary directories for the AI Stock Recommender system

echo "=== Creating Project Directory Structure ==="

mkdir -p ~/stock-recommender/{rsshub,collector,analyzer,strategy,bot,configs,logs,data}
cd ~/stock-recommender

# Subdirectories
mkdir -p collector/src
mkdir -p analyzer/{src,prompts}
mkdir -p strategy/src
mkdir -p bot/src
mkdir -p configs/feeds
mkdir -p data/{cookies,backups}

echo "=== Directory Structure ==="
tree -L 2

echo "=== Directory Structure Created ==="
