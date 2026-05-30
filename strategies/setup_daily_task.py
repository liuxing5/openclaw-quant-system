"""
注册/注销 Windows 任务计划程序 — 每日自动运行4策略
=============================================================
用法:
  # 注册定时任务（每日 15:15 自动运行）
  python -m strategies.setup_daily_task --register

  # 注销定时任务
  python -m strategies.setup_daily_task --unregister

  # 查看当前状态
  python -m strategies.setup_daily_task --status
"""
from __future__ import annotations

import argparse
import os
import sys
import subprocess
import json
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

PROJECT_ROOT = Path(__file__).parent.parent
TASK_NAME = "OpenClaw_Daily_4Strategies"
PYTHON_EXE = sys.executable
SCRIPT_PATH = str(PROJECT_ROOT / "strategies" / "run_daily_all.py")
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "daily_task.log"


def _ensure_log_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def register_task():
    _ensure_log_dir()

    ps_script = f'''
$ErrorActionPreference = "Stop"
$taskName = "{TASK_NAME}"
$action = New-ScheduledTaskAction `
    -Execute "{PYTHON_EXE}" `
    -Argument "-m strategies.run_daily_all" `
    -WorkingDirectory "{PROJECT_ROOT}"

$trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At "15:15"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "OpenClaw 每日4策略调度(漏斗+LLM+八步法+主升浪)+HTML报告+Telegram推送" `
    -Force | Out-Null
Write-Output "OK: 任务已注册 - $taskName"
Write-Output "   运行时间: 每日 15:15"
Write-Output "   日志文件: {LOG_FILE}"
'''

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + ps_script],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            print(result.stdout.strip())
            return True
        else:
            print(f"❌ 注册失败: {result.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        print("❌ 注册超时")
        return False
    except Exception as e:
        print(f"❌ 注册异常: {e}")
        return False


def unregister_task():
    ps_script = f'''
$taskName = "{TASK_NAME}"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {{
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Output "OK: 任务已注销 - $taskName"
}} else {{
    Write-Output "INFO: 任务不存在 - $taskName"
}}
'''
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, encoding='utf-8', timeout=15,
        )
        print(result.stdout.strip() or result.stderr.strip())
        return result.returncode == 0
    except Exception as e:
        print(f"❌ 注销异常: {e}")
        return False


def show_status():
    ps_script = f'''
$taskName = "{TASK_NAME}"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {{
    Write-Output "任务名称: $($task.TaskName)"
    Write-Output "状态: $($task.State)"
    Write-Output "描述: $($task.Description)"
    $nextRun = (($task.Triggers | Where-Object {{ $_ -is [Microsoft.Management.Infrastructure.CimInstance] }}).StartBoundary)
    if ($nextRun) {{ Write-Output "下次运行: $nextRun" }}
    $action = $task.Actions[0]
    Write-Output "执行: $($action.Execute) $($action.Arguments)"
    Write-Output "工作目录: $($action.WorkingDirectory)"
    
    $history = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
    if ($history) {{
        Write-Output "上次运行: $($history.LastRunTime)"
        Write-Output "上次结果: $($history.LastTaskResult)"
        Write-Output "运行次数: $($history.RunCount)"
    }}
}} else {{
    Write-Output "任务未注册"
}}
'''
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True, text=True, encoding='utf-8', timeout=15,
    )
    output = result.stdout.strip()
    if not output:
        output = result.stderr.strip()
    print(output or "(无输出)")


def run_now():
    """立即手动触发一次（用于测试）"""
    _ensure_log_dir()
    print("▶ 立即运行4策略调度...")
    print(f"  日志: {LOG_FILE}")
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"[{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 手动触发\n")
            f.write(f"{'='*60}\n")

        from strategies.run_daily_all import main
        ret = main()
        print(f"\n退出码: {ret}")
        return ret == 0
    except Exception as e:
        print(f"❌ 运行异常: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Windows 定时任务管理 — OpenClaw 每日4策略")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--register", "-r", action="store_true", help="注册每日15:15定时任务")
    group.add_argument("--unregister", "-u", action="store_true", help="注销定时任务")
    group.add_argument("--status", "-s", action="store_true", help="查看任务状态")
    group.add_argument("--run-now", "-n", action="store_true", help="立即运行一次（测试用）")
    args = parser.parse_args()

    if args.register:
        ok = register_task()
        sys.exit(0 if ok else 1)
    elif args.unregister:
        ok = unregister_task()
        sys.exit(0 if ok else 1)
    elif args.status:
        show_status()
    elif args.run_now:
        ok = run_now()
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
