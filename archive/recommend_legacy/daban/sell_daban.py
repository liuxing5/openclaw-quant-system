# -*- coding: utf-8 -*-
"""
短线打板卖出系统 V1.0
====================
定位: 与 daban.py 配套的持仓跟踪和卖出决策
复用: xuangu/sell_new.py 的核心逻辑，适配打板策略

风控参数 (打板激进版):
    - 单票止损: 3%
    - 跟踪止盈: 高点≥+5% 后回落 ≥3% 触发
    - 涨停炸板: 曾涨停后回落 ≥2% 清仓
    - 竞价弱势: 开盘价≤昨收 即清仓

运行时间:
    09:25: 集合竞价挂单建议
    09:30: 开盘决策
    14:50: 尾盘决策

输出: Telegram 推送卖出信号
"""

import os
import sys
import json
import requests
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# ============================================================
# 路径配置 (本地化到 recommend/daban/ 目录)
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "sell_state.json"
POSITIONS_FILE = BASE_DIR / "positions.json"

# 添加 xuangu 目录到路径以复用 notifyTelegram
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "xuangu"))

try:
    from notifyTelegram import send_message, send_sell_alert
    TELEGRAM_ENABLED = True
except ImportError:
    TELEGRAM_ENABLED = False

# ============================================================
# 配置参数
# ============================================================
CONFIG = {
    "stop_loss_global": -3.0,       # 全局硬止损
    "open_stop": 0.0,               # 竞价清仓线 (≤昨收即清)
    "trail_arm": 5.0,               # 跟踪止盈触发线
    "trail_drawdown": 3.0,          # 跟踪止盈回撤线
    "limit_break_drawdown": 2.0,    # 涨停炸板回撤线
    "profit_take1": 5.0,            # 首批止盈
    "profit_take2": 8.0,            # 二级止盈
    "profit_clear": 10.0,           # 清仓线
    "take1_ratio": 0.5,             # 首批卖出比例
    "take2_ratio": 0.5,             # 二级卖出比例
}


# ============================================================
# 持仓管理
# ============================================================
def load_positions() -> list:
    """加载持仓列表"""
    if POSITIONS_FILE.exists():
        try:
            with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def save_positions(positions: list):
    """保存持仓列表"""
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(positions, f, ensure_ascii=False, indent=2)


# ============================================================
# 状态持久化
# ============================================================
def load_state() -> Dict:
    """加载卖出状态"""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_state(state: Dict):
    """保存卖出状态"""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_position_state(state: Dict, code: str, cost: float) -> Dict:
    """获取单只持仓的状态记录"""
    rec = state.get(code)
    default = {
        "cost": cost,
        "high_water": 0.0,
        "high_water_price": 0.0,
        "take1_done": False,
        "take2_done": False,
        "limit_up_hit": False,
        "first_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    if not rec:
        return default

    if abs(rec.get("cost", 0) - cost) > 0.001:
        return default

    for k, v in default.items():
        rec.setdefault(k, v)
    return rec


# ============================================================
# 实时行情 (腾讯接口)
# ============================================================
def get_realtime_data(codes: list) -> dict:
    """获取实时行情数据"""
    code_str = ",".join(["sh" + c if c.startswith("6") else "sz" + c for c in codes])
    url = f"http://qt.gtimg.cn/q={code_str}"

    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
    except Exception as e:
        print(f"  网络请求失败: {e}")
        return {}

    results = {}
    for line in resp.text.split(";"):
        if len(line) < 30:
            continue
        p = line.split("~")
        if len(p) < 40:
            continue
        try:
            code_raw = p[2]
            name = p[1]
            now_price = float(p[3]) if p[3].strip() else 0.0
            pre_close = float(p[4]) if p[4].strip() else 0.0
            today_open = float(p[5]) if p[5].strip() else 0.0
            today_high = float(p[33]) if len(p) > 33 and p[33].strip() else now_price
            vol = float(p[6]) if p[6].strip() else 0.0
            bid1_vol = float(p[27]) if len(p) > 27 and p[27].strip() else 0.0
            ask1_vol = float(p[28]) if len(p) > 28 and p[28].strip() else 0.0
        except (ValueError, IndexError):
            continue

        if now_price <= 0 or pre_close <= 0:
            continue

        open_pct = (today_open - pre_close) / pre_close * 100
        curr_pct = (now_price - pre_close) / pre_close * 100
        high_pct = (today_high - pre_close) / pre_close * 100

        is_limit_up = curr_pct >= 9.8
        was_limit_up = high_pct >= 9.8

        results[code_raw] = {
            "name": name,
            "now": now_price,
            "pre": pre_close,
            "open": today_open,
            "high": today_high,
            "open_pct": open_pct,
            "curr_pct": curr_pct,
            "high_pct": high_pct,
            "vol": vol,
            "bid1_vol": bid1_vol,
            "ask1_vol": ask1_vol,
            "is_limit_up": is_limit_up,
            "was_limit_up": was_limit_up,
        }

    return results


# ============================================================
# 卖出决策引擎
# ============================================================
def decide_sell(code: str, cost: float, market_data: dict, state_rec: Dict) -> dict:
    """打板策略卖出决策"""
    info = market_data.get(code)
    if not info:
        return {
            "code": code, "name": "?", "now": 0, "pre": 0, "cost": cost,
            "open_pct": 0, "curr_pct": 0, "profit_pct": 0,
            "high_water": state_rec.get("high_water", 0),
            "action": "⚠️ 无数据", "reason": "行情获取失败",
            "priority": 0, "take_info": "",
        }

    name = info["name"]
    now = info["now"]
    pre = info["pre"]
    open_pct = info["open_pct"]
    curr_pct = info["curr_pct"]
    profit_pct = (now - cost) / cost * 100

    # 更新最高盈利点
    high_water = state_rec.get("high_water", 0.0)
    if profit_pct > high_water:
        high_water = profit_pct
        state_rec["high_water"] = high_water
        state_rec["high_water_price"] = now

    # 更新涨停记录
    if info["is_limit_up"] or info["was_limit_up"]:
        state_rec["limit_up_hit"] = True

    drawdown_from_peak = high_water - profit_pct

    result = {
        "code": code,
        "name": name,
        "now": now,
        "pre": pre,
        "cost": cost,
        "open_pct": open_pct,
        "curr_pct": curr_pct,
        "profit_pct": profit_pct,
        "high_water": high_water,
        "drawdown": drawdown_from_peak,
        "action": "✅ 持有",
        "reason": "暂无触发条件",
        "priority": 0,
        "take_info": "",
    }

    # 优先级 5: 全局硬止损
    if profit_pct <= CONFIG["stop_loss_global"]:
        result["action"] = "🚨 全局硬止损"
        result["reason"] = f"亏损{profit_pct:.2f}%超止损{CONFIG['stop_loss_global']}%"
        result["priority"] = 5
        return result

    # 优先级 5: 竞价弱势清仓
    if open_pct <= CONFIG["open_stop"]:
        result["action"] = "🔴 竞价清仓"
        result["reason"] = f"竞价{open_pct:.2f}%≤{CONFIG['open_stop']}%(弱势)"
        result["priority"] = 5
        return result

    # 优先级 4: 涨停炸板
    if state_rec.get("limit_up_hit") and not info["is_limit_up"]:
        if drawdown_from_peak >= CONFIG["limit_break_drawdown"]:
            result["action"] = "💥 炸板清仓"
            result["reason"] = f"曾涨停后回落{drawdown_from_peak:.2f}%≥{CONFIG['limit_break_drawdown']}%"
            result["priority"] = 4
            return result

    # 优先级 4: 跟踪止盈
    if high_water >= CONFIG["trail_arm"] and drawdown_from_peak >= CONFIG["trail_drawdown"]:
        result["action"] = "📉 跟踪止盈"
        result["reason"] = f"从最高+{high_water:.2f}%回落{drawdown_from_peak:.2f}%≥{CONFIG['trail_drawdown']}%"
        result["priority"] = 4
        result["take_info"] = f"清仓锁定 ~+{profit_pct:.2f}%"
        return result

    # 优先级 3: 涨停板锁仓
    if info["is_limit_up"]:
        result["action"] = "🔴 涨停持有"
        result["reason"] = "封板坚定持有，炸板2%回撤即清"
        result["priority"] = 3
        return result

    # 优先级 1: 分批止盈
    pt1 = CONFIG["profit_take1"]
    pt2 = CONFIG["profit_take2"]
    pt3 = CONFIG["profit_clear"]

    if profit_pct >= pt3:
        result["action"] = "🏆 清仓"
        result["reason"] = f"盈利{profit_pct:.2f}%达清仓线+{pt3}%"
        result["priority"] = 1
        result["take_info"] = f"全程持有收益={profit_pct:.2f}%(最高+{high_water:.2f}%)"
        state_rec["take1_done"] = True
        state_rec["take2_done"] = True
    elif profit_pct >= pt2 and not state_rec.get("take2_done"):
        result["action"] = "💰 二级止盈"
        result["reason"] = f"盈利{profit_pct:.2f}%达二级+{pt2}%"
        result["priority"] = 1
        result["take_info"] = f"卖{CONFIG['take2_ratio']*100:.0f}%仓位，剩余博更高"
        state_rec["take2_done"] = True
        state_rec["take1_done"] = True
    elif profit_pct >= pt1 and not state_rec.get("take1_done"):
        result["action"] = "💰 首批止盈"
        result["reason"] = f"盈利{profit_pct:.2f}%达一级+{pt1}%"
        result["priority"] = 1
        result["take_info"] = f"卖{CONFIG['take1_ratio']*100:.0f}%仓位"
        state_rec["take1_done"] = True
    elif profit_pct >= pt1 and state_rec.get("take1_done"):
        result["action"] = "✅ 持有(已首抛)"
        result["reason"] = f"已执行首批止盈，待二级+{pt2}%"
        result["priority"] = 0

    # 优先级 0: 默认持有
    if result["priority"] == 0 and not result["take_info"]:
        if open_pct > 0:
            result["action"] = "🚀 持有观察"
            result["reason"] = f"竞价{open_pct:.2f}%强势"
        else:
            result["action"] = "⏳ 谨慎持有"
            result["reason"] = "竞价偏弱但未触发止损"
            result["priority"] = 1

    return result


# ============================================================
# 集合竞价挂单建议
# ============================================================
def auction_advice(code: str, cost: float, market_data: dict) -> Optional[str]:
    """09:20-09:25 给出竞价挂单建议"""
    info = market_data.get(code)
    if not info:
        return None

    pre = info["pre"]
    bid1_vol = info.get("bid1_vol", 0)
    ask1_vol = info.get("ask1_vol", 0)

    total = bid1_vol + ask1_vol
    bid_ratio = (bid1_vol - ask1_vol) / total if total > 0 else 0

    target1 = cost * (1 + CONFIG["profit_take1"] / 100)
    target2 = pre * 1.015
    suggest_price = max(target1, target2)

    if bid_ratio > 0.3:
        return f"💪 竞价买盘强劲(委比+{bid_ratio*100:.0f}%)，建议挂 {suggest_price:.2f} 限价卖单"
    elif bid_ratio > 0:
        return f"⚖️ 竞价多空均衡(委比+{bid_ratio*100:.0f}%)，建议挂 {suggest_price:.2f} 限价卖单"
    elif bid_ratio > -0.3:
        return f"⚠️ 竞价卖压渐强(委比{bid_ratio*100:.0f}%)，建议开盘市价出"
    else:
        return f"❌ 竞价大幅卖压(委比{bid_ratio*100:.0f}%)，准备9:30市价清仓"


# ============================================================
# 主程序
# ============================================================
def run():
    now = datetime.now()
    print("=" * 70)
    print("  短线打板卖出系统 V1.0")
    print(f"  运行时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    positions = load_positions()
    if not positions:
        print("\n⚠️ 持仓列表为空")
        return

    state = load_state()
    codes = [p["code"] for p in positions]
    print(f"\n📊 当前持仓: {len(codes)} 只")

    market_data = get_realtime_data(codes)
    if not market_data:
        print("❌ 行情数据获取失败")
        return

    print(f"  成功获取 {len(market_data)} 只行情\n")

    # 集合竞价时段
    hhmm = now.hour * 100 + now.minute
    if 920 <= hhmm < 925:
        print("─" * 45)
        print("📢 集合竞价挂单建议（09:20-09:25）")
        print("─" * 45)
        for p in positions:
            advice = auction_advice(p["code"], p["cost"], market_data)
            if advice:
                print(f"  {p['code']}: {advice}")
        print()

    # 主决策
    results = []
    for p in positions:
        code = p["code"]
        cost = p["cost"]
        state_rec = get_position_state(state, code, cost)
        res = decide_sell(code, cost, market_data, state_rec)
        results.append(res)
        state[code] = state_rec

    save_state(state)

    # 排序
    results_sorted = sorted(results, key=lambda x: (-x["priority"], -x["profit_pct"]))

    print("📊 卖出建议：")
    print("─" * 45)
    print(
        f"{'代码':<10}{'名称':<10}{'现价':>7}{'成本':>7}"
        f"{'盈亏%':>8}{'峰值%':>8}{'回撤%':>8}{'竞价%':>8}  {'动作':<16}{'优先':>4}  理由"
    )
    print("─" * 45)

    for row in results_sorted:
        print(
            f"{row['code']:<10}{row['name']:<10}"
            f"{row['now']:>7.2f}{row['cost']:>7.2f}"
            f"{row['profit_pct']:>+8.2f}{row['high_water']:>+8.2f}"
            f"{row['drawdown']:>8.2f}{row['open_pct']:>+8.2f}  "
            f"{row['action']:<16}{row['priority']:>4}  "
            f"{row['reason']}"
        )
        if row.get("take_info"):
            print(f"{'':<60}    └─ {row['take_info']}")

    # 紧急清单
    urgent = [r for r in results_sorted if r["priority"] >= 4]
    if urgent:
        print("\n🚨 【紧急操作】以下标的需要立即处理：")
        for row in urgent:
            print(f"  → {row['code']} {row['name']} : {row['action']} | {row['reason']}")
            if row.get("take_info"):
                print(f"      {row['take_info']}")

        # Telegram 推送
        if TELEGRAM_ENABLED:
            for row in urgent:
                try:
                    send_sell_alert(
                        code=row["code"],
                        name=row["name"],
                        action=row["action"],
                        reason=row["reason"],
                        profit_pct=row["profit_pct"],
                        priority=row["priority"],
                    )
                except Exception as e:
                    print(f"  ⚠️ 推送失败 {row['code']}: {e}")
            print("  📲 紧急信号已推送到 Telegram\n")

    # 完整持仓状态推送
    elif TELEGRAM_ENABLED and results_sorted:
        summary_lines = [f"📊 打板持仓状态 {now.strftime('%H:%M')}"]
        for row in results_sorted:
            summary_lines.append(
                f"• {row['code']} {row['name']}: {row['profit_pct']:+.2f}% "
                f"({row['action']})"
            )
        try:
            send_message("\n".join(summary_lines))
        except Exception as e:
            print(f"  ⚠️ 推送失败: {e}")

    print("\n" + "=" * 70)
    print("  风控参数：")
    print(f"    止损: {CONFIG['stop_loss_global']}% | 竞价弱势: ≤{CONFIG['open_stop']}%")
    print(f"    跟踪止盈: +{CONFIG['trail_arm']}% 回落 {CONFIG['trail_drawdown']}%")
    print(f"    涨停炸板: 回落 {CONFIG['limit_break_drawdown']}%")
    print(f"    止盈: +{CONFIG['profit_take1']}%→{CONFIG['take1_ratio']*100:.0f}% / "
          f"+{CONFIG['profit_take2']}%→{CONFIG['take2_ratio']*100:.0f}% / "
          f"+{CONFIG['profit_clear']}%清仓")
    print(f"  状态文件: {STATE_FILE}")
    print("=" * 70)


if __name__ == "__main__":
    run()
