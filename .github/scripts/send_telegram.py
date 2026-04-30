"""
GitHub Actions Telegram 推送脚本
========================================
从 workflow 调用的入口脚本，读取输出文件并推送到 Telegram。

环境变量:
  BOT_TOKEN   : Telegram Bot Token
  CHAT_ID     : Telegram Chat ID
  TITLE       : 推送标题
  RUN_TIME    : 运行时间
  OUTPUT_FILE : 输出文件路径
  ACTION      : send / error
"""

import os
import sys
import time

# 添加项目路径到 sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'recommend', 'xuangu'))

try:
    from notifyTelegram import send_message, send_long_message
except ImportError:
    print("⚠️ 无法导入 notifyTelegram 模块")
    sys.exit(1)


def main():
    bot_token = os.environ.get('BOT_TOKEN', '')
    chat_id = os.environ.get('CHAT_ID', '')
    title = os.environ.get('TITLE', '未知任务')
    run_time = os.environ.get('RUN_TIME', '')
    output_file = os.environ.get('OUTPUT_FILE', '/tmp/output.log')
    action = os.environ.get('ACTION', 'send')

    # 设置环境变量供 notifyTelegram 使用
    if bot_token:
        os.environ['TELEGRAM_BOT_TOKEN'] = bot_token
    if chat_id:
        os.environ['TELEGRAM_CHAT_ID'] = chat_id

    if action == 'error':
        # 错误通知
        msg = f"❌ Workflow 执行失败\n\n"
        msg += f"📋 任务: {title}\n"
        msg += f"⏰ 时间: {run_time}\n\n"
        msg += f"请检查 GitHub Actions 日志获取详细错误信息。"
        
        print(f"发送错误通知: {title}")
        success = send_message(msg)
        
    elif action == 'send':
        # 正常输出推送
        if not os.path.exists(output_file):
            print(f"输出文件不存在: {output_file}")
            sys.exit(1)

        with open(output_file, 'r', encoding='utf-8') as f:
            content = f.read()

        if not content.strip():
            print("输出内容为空，跳过推送")
            sys.exit(0)

        # 构建推送消息
        msg = f"📊 {title}\n"
        msg += f"⏰ {run_time}\n"
        msg += f"{'='*40}\n\n"
        msg += content

        print(f"发送输出内容 ({len(content)} 字符)")
        
        # 使用 send_long_message 自动处理长消息
        success = send_long_message(msg)
        
    else:
        print(f"未知的 ACTION: {action}")
        sys.exit(1)

    if success:
        print("✅ 推送成功")
    else:
        print("❌ 推送失败")
        sys.exit(1)


if __name__ == '__main__':
    main()
