#!/bin/bash
# Master Setup Script - Phase 0 to Phase 2
# Run this on your Ubuntu VM to set up the entire infrastructure

set -e

echo "========================================="
echo "AI Stock Recommender - Setup Script"
echo "========================================="

# Step 0.1: VM Resource Check
echo ""
echo "Step 0.1: Checking VM Resources..."
bash "$(dirname "$0")/01_vm_check.sh"

# Step 0.2: Install Dependencies
echo ""
echo "Step 0.2: Installing System Dependencies..."
bash "$(dirname "$0")/02_install_deps.sh"

# Step 0.3: Install Docker
echo ""
echo "Step 0.3: Installing Docker..."
bash "$(dirname "$0")/03_install_docker.sh"

# Step 0.4: Create Directory Structure
echo ""
echo "Step 0.4: Creating Directory Structure..."
bash "$(dirname "$0")/04_create_dirs.sh"

# Step 0.5: Setup PostgreSQL
echo ""
echo "Step 0.5: Setting up PostgreSQL..."
bash "$(dirname "$0")/05_setup_postgres.sh"

echo ""
echo "========================================="
echo "Phase 0-2 Setup Complete!"
echo "========================================="
echo ""
echo "Next Steps:"
echo "1. Edit .env file with your actual credentials"
echo "2. Get Xueqiu cookies and fill in .env"
echo "3. Run RSSHub: cd rsshub && docker compose --env-file ../.env up -d"
echo "4. Test RSSHub: curl http://localhost:1200/cls/telegraph"
echo "5. Apply schema: psql -h localhost -U stockrec -d stockrec_db -f ../configs/schema.sql"
