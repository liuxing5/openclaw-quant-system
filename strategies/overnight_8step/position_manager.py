"""
持仓管理系统
========================================
支持通过 Telegram 命令管理持仓，sell_new.py 动态加载持仓配置。

功能：
  ✓ 添加持仓（买入）
  ✓ 删除持仓（卖出）
  ✓ 查看当前持仓
  ✓ 从选股结果自动导入
  ✓ 持久化存储到 PostgreSQL（主）+ positions.json（本地缓存）

Telegram 命令：
  /positions      - 查看当前持仓
  /add <code> <cost> [path]  - 添加持仓
  /remove <code>  - 删除持仓
  /import         - 从最新选股结果导入
  /help           - 显示帮助
"""

import os
import re
import json
import threading
from datetime import datetime, timezone, timedelta

BEIJING_TZ = timezone(timedelta(hours=8))
from typing import List, Dict, Optional

# ============================================================
# 配置
# ============================================================
POSITIONS_FILE = os.path.join(os.path.dirname(__file__), "positions.json")

DEFAULT_PATH = "稳健"
VALID_PATHS = ["稳健", "高位"]

# 数据库支持
try:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
    from core.db.connection import get_db_fresh
    DB_ENABLED = True
except Exception:
    DB_ENABLED = False


def _normalize_code(code: str) -> str:
    """统一代码格式为 sh.600519 / sz.000001 / bj.430047"""
    c = code.strip().lower()
    c = re.sub(r'^(sh|sz|bj)\.?', '', c)
    c = re.sub(r'\.(sh|sz|bj)$', '', c)
    c = c.replace('.', '')
    digits = re.sub(r'[^0-9]', '', c)
    if len(digits) < 6:
        return code.strip()
    code6 = digits[:6]
    if code6.startswith(('6', '68', '688', '9')):
        return f"sh.{code6}"
    elif code6.startswith(('8', '4')):
        return f"bj.{code6}"
    else:
        return f"sz.{code6}"


# ============================================================
# 持仓 CRUD 操作
# ============================================================
def load_positions() -> List[Dict]:
    """加载持仓列表（优先从数据库，回退到本地文件）"""
    if DB_ENABLED:
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor()
            cur.execute("""
                SELECT code, cost, path, entry_date, limit_up_at_buy, mktcap_yi
                FROM overnight_positions
                ORDER BY entry_date DESC;
            """)
            rows = cur.fetchall()
            positions = []
            for row in rows:
                positions.append({
                    "code": row[0],
                    "cost": float(row[1]),
                    "path": row[2],
                    "entry_date": row[3].strftime("%Y-%m-%d") if hasattr(row[3], 'strftime') else str(row[3]),
                    "limit_up_at_buy": row[4],
                    "mktcap_yi": float(row[5]) if row[5] else 0.0,
                })
            cur.close()
            if positions:
                return positions
        except Exception as e:
            print(f"⚠️ 数据库加载持仓失败: {e}")
        finally:
            if conn and not conn.closed:
                conn.close()

    # 回退到本地文件
    if not os.path.exists(POSITIONS_FILE):
        return []
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"⚠️ 持仓文件读取失败: {e}")
        return []


def save_positions(positions: List[Dict]) -> None:
    """保存持仓列表（同时写入数据库和本地文件）"""
    # 写入本地文件
    try:
        tmp = POSITIONS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)
        os.replace(tmp, POSITIONS_FILE)
    except (IOError, OSError) as e:
        print(f"⚠️ 持仓文件写入失败: {e}")

    # 写入数据库
    if DB_ENABLED:
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor()
            for p in positions:
                cur.execute("""
                    INSERT INTO overnight_positions (code, cost, path, entry_date, limit_up_at_buy, mktcap_yi)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (code, entry_date) DO UPDATE SET
                        cost = EXCLUDED.cost,
                        path = EXCLUDED.path,
                        limit_up_at_buy = EXCLUDED.limit_up_at_buy,
                        mktcap_yi = EXCLUDED.mktcap_yi,
                        updated_at = NOW();
                """, (
                    p["code"],
                    p["cost"],
                    p.get("path", "稳健"),
                    p.get("entry_date", datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")),
                    p.get("limit_up_at_buy", False),
                    p.get("mktcap_yi", 0.0),
                ))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"⚠️ 数据库保存持仓失败: {e}")
        finally:
            if conn and not conn.closed:
                conn.close()


def add_position(code: str, cost: float, path: str = None, entry_date: str = None,
                 limit_up_at_buy: bool = False, mktcap_yi: float = 0.0) -> Dict:
    """
    添加持仓。

    Args:
        code: 股票代码
        cost: 买入成本
        path: 路径（稳健/高位），默认"稳健"
        entry_date: 入场日期，默认今天
        limit_up_at_buy: 买入时是否封板
        mktcap_yi: 市值（亿元）

    Returns:
        新持仓记录
    """
    code = _normalize_code(code)
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
        "limit_up_at_buy": limit_up_at_buy,
        "mktcap_yi": mktcap_yi,
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
    code = _normalize_code(code)
    for i, p in enumerate(positions):
        if p["code"] == code:
            removed = positions.pop(i)
            save_positions(positions)

            # 同时从数据库删除
            if DB_ENABLED:
                conn = None
                try:
                    conn = get_db_fresh()
                    cur = conn.cursor()
                    cur.execute("DELETE FROM overnight_positions WHERE code = %s;", (code,))
                    conn.commit()
                    cur.close()
                except Exception as e:
                    print(f"⚠️ 数据库删除持仓失败: {e}")
                finally:
                    if conn and not conn.closed:
                        conn.close()

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

        code = _normalize_code(parts[0])
        try:
            cost = float(parts[1])
        except ValueError:
            return "❌ 成本必须是数字"

        path = parts[2] if len(parts) > 2 else None

        result = add_position(code, cost, path)
        if result["action"] == "added":
            return f"✅ 已添加持仓: {code} 成本¥{cost:.2f} 路径:{result['position']['path']}"
        else:
            return f"✅ 已更新持仓: {code} 成本¥{cost:.2f} 路径:{result['position']['path']}"

    elif command == "remove" or command == "del" or command == "delete":
        code = _normalize_code(args.strip())
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
/add <代码> <成本> [路径]  - 添加持仓 + 记录买入
/remove <代码>         - 删除持仓
/sell <代码> <价格> [数量] - 记录卖出 + 移除持仓
/trades [代码]        - 查看最近交易记录
/import               - 从选股结果导入
/help                 - 显示此帮助

路径选项: 稳健(默认) / 高位
例: /add 601933 4.10 稳健
例: /sell 601933 4.80 1000"""

    else:
        return f"❌ 未知命令: /{command}\n输入 /help 查看可用命令"


# ============================================================
# 交易记录数据库写入
# ============================================================
def record_buy(code: str, price: float, quantity: Optional[float] = None,
               path: Optional[str] = None, source: Optional[str] = None,
               notes: Optional[str] = None, stock_name: Optional[str] = None) -> str:
    if not DB_ENABLED:
        return "⚠️ 数据库未启用，买入记录未保存"
    code = _normalize_code(code)
    trade_time = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    amount = price * quantity if quantity else None

    def _do():
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO trade_records
                (code, stock_name, trade_type, price, quantity, amount, path, trade_time, source, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (code, stock_name, 'buy', price, quantity, amount, path, trade_time, source, notes))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"⚠️ record_buy 写入失败: {e}")
        finally:
            if conn and not conn.closed:
                conn.close()

    threading.Thread(target=_do, daemon=True).start()
    return f"✅ 买入记录已保存: {code} ¥{price:.2f}"


def record_sell(code: str, price: float, quantity: Optional[float] = None,
                profit_pct: Optional[float] = None, path: Optional[str] = None,
                source: Optional[str] = None, notes: Optional[str] = None,
                stock_name: Optional[str] = None) -> str:
    if not DB_ENABLED:
        return "⚠️ 数据库未启用，卖出记录未保存"
    code = _normalize_code(code)
    trade_time = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    amount = price * quantity if quantity else None

    def _do():
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO trade_records
                (code, stock_name, trade_type, price, quantity, amount, path, profit_pct, trade_time, source, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (code, stock_name, 'sell', price, quantity, amount, path, profit_pct, trade_time, source, notes))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"⚠️ record_sell 写入失败: {e}")
        finally:
            if conn and not conn.closed:
                conn.close()

    threading.Thread(target=_do, daemon=True).start()
    return f"✅ 卖出记录已保存: {code} ¥{price:.2f}"


def get_trade_history(code: Optional[str] = None, limit: int = 20) -> List[Dict]:
    if not DB_ENABLED:
        return []
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        if code:
            code = _normalize_code(code)
            cur.execute("""
                SELECT id, code, stock_name, trade_type, price, quantity, amount,
                       path, profit_pct, trade_time, source, notes
                FROM trade_records
                WHERE code = %s
                ORDER BY trade_time DESC
                LIMIT %s
            """, (code, limit))
        else:
            cur.execute("""
                SELECT id, code, stock_name, trade_type, price, quantity, amount,
                       path, profit_pct, trade_time, source, notes
                FROM trade_records
                ORDER BY trade_time DESC
                LIMIT %s
            """, (limit,))
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "id": r[0], "code": r[1], "stock_name": r[2], "trade_type": r[3],
                "price": float(r[4]) if r[4] else 0,
                "quantity": float(r[5]) if r[5] else None,
                "amount": float(r[6]) if r[6] else None,
                "path": r[7], "profit_pct": r[8],
                "trade_time": str(r[9]), "source": r[10], "notes": r[11],
            }
            for r in rows
        ]
    except Exception as e:
        print(f"⚠️ 查询交易记录失败: {e}")
        return []
    finally:
        if conn and not conn.closed:
            conn.close()


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
