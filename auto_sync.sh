#!/bin/bash
# Auto-sync script for GitHub repository
# Any changes will be automatically committed and pushed

set -e

WORKSPACE="/root/.openclaw/workspace"
SSH_KEY="/tmp/github_deploy_key"
REPO_URL="git@github.com:liuxing5/openclaw-quant-system.git"

cd "$WORKSPACE"

# Check if there are any changes
if git status --porcelain | grep -q .; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Changes detected, committing..."
    
    # Add all changes
    git add .
    
    # Create commit message with timestamp
    COMMIT_MSG="Auto-sync: $(date '+%Y-%m-%d %H:%M:%S')"
    
    # Check if there are any real changes (not just timestamps)
    if git diff --cached --quiet; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] No substantial changes, skipping commit."
        exit 0
    fi
    
    # Commit changes
    git commit -m "$COMMIT_MSG" || {
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Commit failed, possibly no changes."
        exit 0
    }
    
    # Push to GitHub using SSH key
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pushing to GitHub..."
    GIT_SSH_COMMAND="ssh -i $SSH_KEY -o StrictHostKeyChecking=no" git push origin master
    
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sync completed successfully."
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] No changes detected."
fi