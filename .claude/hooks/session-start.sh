#!/usr/bin/env bash
# SessionStart hook for openclaw-quant-system.
#
# 1. 静默 git fetch origin --prune，让会话启动时已经知道最新远程状态。
# 2. stdout 输出 JSON，会被 Claude Code 注入为 additionalContext，告知 Claude
#    项目约定的"修改前 fetch / 修改后 commit"规则——自动执行，无需每次询问。
#
# fetch 失败不阻塞会话；写到 stderr，仍出现在工作流日志里便于排查。
set -uo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"

if git -C "$PROJECT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  git -C "$PROJECT_DIR" fetch origin --prune >/dev/null 2>&1 \
    || echo "⚠️ git fetch origin --prune failed (offline or no remote)" >&2
fi

# 用 jq 拼 JSON，避免手写 escape
REMINDER='项目约定（openclaw-quant-system，自动执行，不要每次询问用户）：
1. 修改代码前：会话启动时已经 git fetch；如距启动较久，修改前再跑一次 git fetch origin master 确保拿到最新。
2. 修改完成后：提交并 git push 到当前 feature 分支。完整逻辑单元才提交，不要每个小 edit 都 commit。
3. 合并到 master 走 PR + squash merge，不直接 push master。'

if command -v jq >/dev/null 2>&1; then
  jq -n --arg ctx "$REMINDER" '{
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext: $ctx
    }
  }'
else
  # jq 不可用时退化：原样输出 + 简易 escape（仅处理换行）
  ESCAPED=$(printf '%s' "$REMINDER" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
  printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":%s}}\n' "$ESCAPED"
fi
