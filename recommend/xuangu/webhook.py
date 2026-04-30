"""
Telegram Webhook 接收器（部署到 PythonAnywhere/Railway）
============================================================
接收 Telegram 消息，触发 GitHub Actions 执行命令。

部署步骤：
1. 在 PythonAnywhere 创建 Web App（Flask）
2. 上传此文件为 webhook.py
3. 设置环境变量：
   - TELEGRAM_BOT_TOKEN: 你的 Bot Token
   - GITHUB_TOKEN: GitHub Personal Access Token
   - GITHUB_REPO: liuxing5/openclaw-quant-system
   - GITHUB_BRANCH: master
4. 配置 Telegram Bot Webhook：
   https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://yourusername.pythonanywhere.com/webhook
"""

import os
import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# 配置
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "liuxing5/openclaw-quant-system")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "master")
AUTHORIZED_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def trigger_github_action(command: str, args: str = "") -> dict:
    """触发 GitHub Actions 执行命令"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/telegram-command.yml/dispatches"

    payload = {
        "ref": GITHUB_BRANCH,
        "inputs": {
            "command": command,
            "args": args,
            "chat_id": request.json.get("message", {}).get("chat", {}).get("id", ""),
        },
    }

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        return {"success": resp.status_code == 204, "status": resp.status_code}
    except Exception as e:
        return {"success": False, "error": str(e)}


def send_telegram_message(chat_id: str, text: str):
    """发送消息到 Telegram"""
    if not BOT_TOKEN:
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }

    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"发送消息失败: {e}")


@app.route("/webhook", methods=["POST"])
def webhook():
    """接收 Telegram Webhook"""
    data = request.json

    if not data:
        return jsonify({"status": "error", "message": "No data"}), 400

    # 验证聊天 ID
    chat_id = str(data.get("message", {}).get("chat", {}).get("id", ""))
    if AUTHORIZED_CHAT_ID and chat_id != AUTHORIZED_CHAT_ID:
        send_telegram_message(chat_id, "❌ 无权访问")
        return jsonify({"status": "ignored"}), 200

    # 获取消息文本
    text = data.get("message", {}).get("text", "")
    if not text or not text.startswith("/"):
        return jsonify({"status": "ignored"}), 200

    # 解析命令
    parts = text[1:].split()
    command = parts[0]
    args = " ".join(parts[1:]) if len(parts) > 1 else ""

    # 触发 GitHub Actions
    result = trigger_github_action(command, args)

    if result["success"]:
        send_telegram_message(chat_id, " 命令已提交，请稍候...")
    else:
        send_telegram_message(chat_id, f"❌ 命令提交失败: {result.get('error', 'Unknown')}")

    return jsonify({"status": "ok"}), 200


@app.route("/health", methods=["GET"])
def health():
    """健康检查"""
    return jsonify({"status": "ok", "service": "telegram-webhook"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
