#!/bin/bash
# Step 0.2 - Install System Dependencies
# Installs all required system packages for the AI Stock Recommender

echo "=== Installing System Dependencies ==="

sudo apt update && sudo apt upgrade -y

sudo apt install -y \
    curl wget git vim htop \
    build-essential \
    python3.11 python3.11-venv python3-pip \
    postgresql postgresql-contrib \
    redis-server \
    nginx \
    supervisor \
    ca-certificates gnupg lsb-release

echo "=== System Dependencies Installed ==="
