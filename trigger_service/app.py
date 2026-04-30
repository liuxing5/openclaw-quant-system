"""
GitHub Actions 触发服务
======================
部署到 Render.com / Railway 等免费平台
cron-job.org 定时调用此服务，服务内部调用 GitHub API 触发 workflow

环境变量:
    GITHUB_TOKEN: GitHub Personal Access Token (细粒度，只需 actions:write 权限)
    GITHUB_OWNER: liuxing5
    GITHUB_REPO: openclaw-quant-system
    GITHUB_WORKFLOW: daily-stock-pick.yml
"""

from flask import Flask, request, jsonify
import requests
import os
from datetime import datetime

app = Flask(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "liuxing5")
GITHUB_REPO = os.getenv("GITHUB_REPO", "openclaw-quant-system")
GITHUB_WORKFLOW = os.getenv("GITHUB_WORKFLOW", "daily-stock-pick.yml")

if not GITHUB_TOKEN:
    raise ValueError("必须设置 GITHUB_TOKEN 环境变量")


def trigger_workflow():
    """调用 GitHub API 触发 workflow"""
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/workflows/{GITHUB_WORKFLOW}/dispatches"
    
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
    }
    
    data = {
        "ref": "master",
        "inputs": {
            "triggered_by": "cron-job.org",
            "trigger_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    }
    
    response = requests.post(url, headers=headers, json=data, timeout=10)
    
    return {
        "status_code": response.status_code,
        "success": response.status_code == 204,
        "message": "Workflow triggered successfully" if response.status_code == 204 else response.text
    }


@app.route("/", methods=["GET"])
def health():
    """健康检查"""
    return jsonify({
        "status": "ok",
        "service": "GitHub Actions Trigger",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })


@app.route("/trigger", methods=["GET", "POST"])
def trigger():
    """触发 workflow"""
    result = trigger_workflow()
    
    return jsonify(result), 200 if result["success"] else 500


@app.route("/trigger/sell", methods=["GET", "POST"])
def trigger_sell():
    """触发卖出决策"""
    result = trigger_workflow()
    return jsonify({**result, "task": "sell_decision"}), 200 if result["success"] else 500


@app.route("/trigger/pick", methods=["GET", "POST"])
def trigger_pick():
    """触发选股"""
    result = trigger_workflow()
    return jsonify({**result, "task": "stock_pick"}), 200 if result["success"] else 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
