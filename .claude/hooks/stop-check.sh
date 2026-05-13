#!/usr/bin/env bash
# Stop hook: 交付验收检查
# 如果本轮修改了代码/配置/文档但未完成验证，阻止结束会话。
set -euo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
MARKER_FILE="$PROJECT_DIR/.claude/.verification_done"

# 检测范围：代码、配置、文档
CODE_PATTERN='\.(py|js|ts|jsx|tsx|sql|sh|html|css|ps1|dockerfile)$'
CONFIG_PATTERN='\.(json|ya?ml|toml|cfg|ini|env)$'
DOC_PATTERN='\.(md|rst|txt)$'

# 排除：非交付文件
EXCLUDE_PATTERN='(^\.claude/|^logs/|^data/|\.parquet$|sell_state\.json|positions\.json)'

cd "$PROJECT_DIR"

# 收集所有未提交变更
STAGED=$(git diff --cached --name-only 2>/dev/null || true)
UNSTAGED=$(git diff --name-only 2>/dev/null || true)
UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null || true)
ALL=$(printf '%s\n%s\n%s\n' "$STAGED" "$UNSTAGED" "$UNTRACKED" | grep -v '^$' | sort -u || true)

# 筛选代码/配置/文档，排除非交付文件
RELEVANT=$(echo "$ALL" | grep -E "$CODE_PATTERN|$CONFIG_PATTERN|$DOC_PATTERN" | grep -vE "$EXCLUDE_PATTERN" || true)

# 同时检测今天内的 commit 中变更的文件
RECENT_COMMIT_FILES=$(git log --since="midnight" --name-only --pretty=format: 2>/dev/null | grep -E "$CODE_PATTERN|$CONFIG_PATTERN|$DOC_PATTERN" | grep -vE "$EXCLUDE_PATTERN" | sort -u || true)

ALL_RELEVANT=$(printf '%s\n%s\n' "$RELEVANT" "$RECENT_COMMIT_FILES" | grep -v '^$' | sort -u || true)

if [ -z "$ALL_RELEVANT" ]; then
  exit 0
fi

# 如果标记文件存在，说明已验证，允许退出
if [ -f "$MARKER_FILE" ]; then
  rm -f "$MARKER_FILE"
  exit 0
fi

# --- 阻断退出 ---
CHANGE_COUNT=$(echo "$ALL_RELEVANT" | wc -l | tr -d ' ')
CHANGE_LIST=$(echo "$ALL_RELEVANT" | head -15 | sed 's/^/  - /')

MESSAGE=$(cat <<EOF
🚨 交付验收未通过 — 检测到 ${CHANGE_COUNT} 个文件有变更。

变更文件：
${CHANGE_LIST}

请完成以下验证后才能结束会话：
1. 测试（pytest 或手动运行关键路径，确保 pass）
2. Lint / Type check（如 ruff、mypy，确保无新增告警）
3. 功能验证（实际运行核心流程，确认输出正确）
4. TODO 检查（代码中无遗留临时标记、未完成逻辑）

验证通过后，创建标记文件 '.claude/.verification_done' 即可正常退出。
EOF
)

# 输出 JSON 给 Claude Code
if command -v jq >/dev/null 2>&1; then
  jq -n --arg ctx "$MESSAGE" '{
    hookSpecificOutput: {
      hookEventName: "Stop",
      additionalContext: $ctx
    }
  }'
else
  ESCAPED=$(printf '%s' "$MESSAGE" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || python -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || printf '"%s"' "$MESSAGE")
  printf '{"hookSpecificOutput":{"hookEventName":"Stop","additionalContext":%s}}\n' "$ESCAPED"
fi

exit 1
