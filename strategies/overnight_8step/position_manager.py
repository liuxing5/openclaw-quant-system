"""
持仓管理系统
========================================
支持通过 Telegram 命令管理持仓，sell_new.py 动态加载持仓配置。

功能：
  ✓ 添加持仓（买入）
  ✓ 删除持仓（卖出）
  ✓ 查看当前持仓
  ✓ 从选股结果自动导入
  ✓ 持久化存储到 positions.json

Telegram 命令：
  /positions      - 查看当前持仓
  /add <code> <cost> [path]  - 添加持仓
  /remove <code>  - 删除持仓
  /import         - 从最新选股结果导入
  /help           - 显示帮助
"""

import os
import json
from datetime import datetime, timezone, timedelta

BEIJING_TZ = timezone(timedelta(hours=8))
from typing import List, Dict, Optional

# ============================================================
# 配置
# ============================================================
POSITIONS_FILE = os.path.join(os.path.dirname(__file__), "positions.json")

DEFAULT_PATH = "稳健"
VALID_PATHS = ["稳健", "高位"]


# ============================================================
# 持仓 CRUD 操作
# ============================================================
def load_positions() -> List[Dict]:
    """加载持仓列表"""
    if not os.path.exists(POSITIONS_FILE):
        return []
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"⚠️ 持仓文件读取失败: {e}")
        return []


def save_positions(positions: List[Dict]) -> None:
    """保存持仓列表"""
    try:
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"⚠️ 持仓文件写入失败: {e}")


def add_position(code: str, cost: float, path: str = None, entry_date: str = None) -> Dict:
    """
    添加持仓。

    Args:
        code: 股票代码
        cost: 买入成本
        path: 路径（稳健/高位），默认"稳健"
        entry_date: 入场日期，默认今天

    Returns:
        新持仓记录
    """
    if path is None:
        path = DEFAULT_PATH
    if path not in VALID_PATHS:
        path = DEFAULT_PATH

    if entry_date is None:
        entry_date = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    position = {
        "code": code,
        "cost": cost,
        "path": path,
        "entry_date": entry_date,
    }

    positions = load_positions()

    # 检查是否已存在
    for i, p in enumerate(positions):
        if p["code"] == code:
            positions[i].update(position)
            save_positions(positions)
            return {"action": "updated", "position": positions[i]}

    positions.append(position)
    save_positions(positions)
    return {"action": "added", "position": position}


def remove_position(code: str) -> Dict:
    """
    删除持仓。

    Returns:
        {"action": "removed", "position": {...}} 或 {"action": "not_found"}
    """
    positions = load_positions()
    for i, p in enumerate(positions):
        if p["code"] == code:
            removed = positions.pop(i)
            save_positions(positions)
            return {"action": "removed", "position": removed}
    return {"action": "not_found"}


def get_positions() -> List[Dict]:
    """获取所有持仓"""
    return load_positions()


def format_positions(positions: List[Dict] = None) -> str:
    """格式化持仓列表为文本"""
    if positions is None:
        positions = load_positions()

    if not positions:
        return "📭 当前无持仓\n\n使用 /add <代码> <成本> [路径] 添加持仓"

    lines = ["📊 当前持仓", "=" * 40]
    for p in positions:
        path_icon = "🛡️" if p.get("path") == "稳健" else "🚀"
        lines.append(
            f"{path_icon} {p['code']}  成本¥{p['cost']:.2f}  "
            f"路径:{p.get('path', '稳健')}  "
            f"入场:{p.get('entry_date', '?')}"
        )
    lines.append("=" * 40)
    lines.append(f"共 {len(positions)} 只持仓")
    return "\n".join(lines)


# ============================================================
# 从选股结果导入
# ============================================================
def import_from_picks(picks: List[Dict], path: str = None) -> Dict:
    """
    从选股结果批量导入持仓。

    Args:
        picks: 选股结果列表 [{code, price, ...}, ...]
        path: 路径，默认根据 picks 自动判断

    Returns:
        {"added": [...], "skipped": [...]}
    """
    if path is None:
        path = DEFAULT_PATH

    positions = load_positions()
    existing_codes = {p["code"] for p in positions}

    added = []
    skipped = []

    for pick in picks:
        code = pick.get("code", "")
        price = pick.get("price", 0)

        if code in existing_codes:
            skipped.append(code)
            continue

        position = {
            "code": code,
            "cost": price,
            "path": path,
            "entry_date": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d"),
        }
        positions.append(position)
        added.append(position)

    save_positions(positions)
    return {"added": added, "skipped": skipped}


# ============================================================
# Telegram Bot 命令处理
# ============================================================
def handle_command(command: str, args: str = "") -> str:
    """
    处理 Telegram 命令。

    Args:
        command: 命令名称（不含 /）
        args: 命令参数

    Returns:
        回复消息
    """
    command = command.lower().strip()

    if command == "positions" or command == "list":
        return format_positions()

    elif command == "add":
        parts = args.strip().split()
        if len(parts) < 2:
            return "❌ 用法: /add <代码> <成本> [路径]\n例: /add 601933 4.10 稳健"

        code = parts[0]
        try:
            cost = float(parts[1])
        except ValueError:
            return "❌ 成本必须是数字"

        path = parts[2] if len(parts) > 2 else None

        result = add_position(code, cost, path)
        if result["action"] == "added":
            return f"✅ 已添加持仓: {code} 成本¥{cost:.2f} 路径:{result['position']['path']}"
        else:
            return f" 已更新持仓: {code} 成本¥{cost:.2f} 路径:{result['position']['path']}"

    elif command == "remove" or command == "del" or command == "delete":
        code = args.strip()
        if not code:
            return "❌ 用法: /remove <代码>\n例: /remove 601933"

        result = remove_position(code)
        if result["action"] == "removed":
            return f"✅ 已删除持仓: {code}"
        else:
            return f" 未找到持仓: {code}"

    elif command == "import":
        # 这里需要从 zuiyou1 结果获取，暂时返回提示
        return " 从选股结果导入功能需要配合 zuiyou1.py 使用\n请在选股推送消息中点击「一键买入」按钮"

    elif command == "help":
        return """📖 持仓管理命令

/positions 或 /list    - 查看当前持仓
/add <代码> <成本> [路径]  - 添加持仓
/remove <代码>         - 删除持仓
/import               - 从选股结果导入
/help                 - 显示此帮助

路径选项: 稳健(默认) / 高位
例: /add 601933 4.10 稳健"""

    else:
        return f"❌ 未知命令: /{command}\n输入 /help 查看可用命令"


# ============================================================
# 自测
# ============================================================
if __name__ == "__main__":
    print("🧪 持仓管理系统自测")
    print("=" * 50)

    # 测试添加
    print("\n测试添加持仓:")
    result = add_position("601933", 4.10, "稳健")
    print(f"  {result}")

    result = add_position("002510", 7.80, "稳健")
    print(f"  {result}")

    # 测试查看
    print("\n测试查看持仓:")
    print(format_positions())

    # 测试删除
    print("\n测试删除持仓:")
    result = remove_position("601933")
    print(f"  {result}")

    print("\n最终持仓:")
    print(format_positions())

    # 清理测试数据
    if os.path.exists(POSITIONS_FILE):
        os.remove(POSITIONS_FILE)
        print("\n 已清理测试数据")
