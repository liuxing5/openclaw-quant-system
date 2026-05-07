#!/bin/bash
# Step 0.1 - VM Resource Assessment
# Run this script to verify your VM has sufficient resources

echo "=== VM Resource Assessment ==="
echo ""

# CPU check
echo "CPU Cores (minimum 4 recommended):"
nproc
echo ""

# Memory check
echo "Memory (minimum 8GB recommended):"
free -h
echo ""

# Disk check
echo "Disk Space (minimum 50GB recommended):"
df -h
echo ""

# OS version
echo "System Version:"
lsb_release -a
echo ""

echo "=== Assessment Complete ==="
echo "If memory < 8GB, RSSHub chromium mode may OOM."
echo "Consider upgrading or using non-chromium image (reduced coverage)."
