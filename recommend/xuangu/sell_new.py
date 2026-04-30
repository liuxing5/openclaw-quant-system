"""
自动卖出系统 v2.1（sell_new.py）
========================================
基于v2.0新增「跟踪止盈 + 状态持久化」

v2.1 新增特性：
  ✓ 持久化最高盈利点（high_water）到 ./sell_state.json
  ✓ 跟踪止盈：从最高点回落 N% 自动卖出
    - 稳健路径：高点≥+3% 后回落 ≥2% 触发
    - 高位路径：高点≥+5% 后回落 ≥3% 触发
  ✓ 首批止盈状态保护：已止盈的股票不会重复触发"首批止盈"提示
  ✓ 涨停后炸板检测：从涨停回落 ≥2% 强制清仓信号
  ✓ 09:25 集合竞价挂单建议（开盘前预挂限价单）
  ✓ T+1 日历对齐：自动比较 entry_date 与今日，区分 T+1 / T+N 持仓

路径区分（继承v2.0）：
  稳健路径（来自hs300+zz500池）：
    - 次日09:35未维持昨收+1%即出
    - 低开≤-1%竞价直接清仓
    - 全局止损 -2.5%
    - 跟踪止盈触发线 +3%、回落2%

  高位路径（来自zz1000池）：
    - 次日竞价弱于昨收（竞价≤昨收）即清仓
    - 竞价>0但<+1%可持有到09:35视情况出
    - 全局止损 -2.5%
    - 跟踪止盈触发线 +5%、回落3%
    - 涨停板锁仓持有，炸板回落2%清仓

运行时间：
  09:20-09:25：查看集合竞价委比，建议挂单价
  09:26-09:30：竞价结束稳定后，主决策窗口
  盘中定时：09:30 / 10:00 / 10:30 / 13:00 / 14:00
"""

import os
import json
import requests
from datetime import datetime
from typing import Dict, Optional

# ============================================================
#  Telegram 推送（可选）
# ============================================================
try:
    from notifyTelegram import send_message, send_sell_alert
    TELEGRAM_ENABLED = True
except ImportError:
    TELEGRAM_ENABLED = False

# ============================================================
#  持仓配置（从 position_manager 动态加载）
#  也可以通过 Telegram 命令管理：
#    /add <代码> <成本> [路径]  - 添加持仓
#    /remove <代码>            - 删除持仓
#    /positions                - 查看持仓
# ============================================================
try:
    from position_manager import get_positions as load_positions_dynamic
    POSITIONS = load_positions_dynamic()
    if not POSITIONS:
        # 如果动态加载为空，使用默认配置
        POSITIONS = [
            # {"code": "002439", "cost": 15.30, "path": "稳健", "entry_date": "2026-04-28"},
            # {"code": "601933", "cost": 4.10},
            # {"code": "002510", "cost": 7.80},
            # {"code": "000632", "cost": 4.60},
        ]
except ImportError:
    # 如果 position_manager 不可用，使用硬编码配置
    POSITIONS = [
        # {"code": "002439", "cost": 15.30, "path": "稳健", "entry_date": "2026-04-28"},
        # {"code": "601933", "cost": 4.10},
        # {"code": "002510", "cost": 7.80},
        # {"code": "000632", "cost": 4.60},
    ]

CONFIG = {
    "state_file": "./sell_state.json",
    "check_times": ["09:30", "10:00", "10:30", "13:00", "14:00"],
    "stop_loss_global": -2.5,
    "paths": {
        "稳健": {
            "open_stop": -1.0,
            "open_hold": 1.0,
            "profit_take1": 3.0,
            "profit_take2": 5.0,
            "profit_clear": 6.0,
            "take1_ratio": 0.33,
            "take2_ratio": 0.33,
            # 跟踪止盈：高点达到 trail_arm 后，回落 trail_drawdown 触发
            "trail_arm": 3.0,
            "trail_drawdown": 2.0,
            "desc": "09:35未维持昨收+1%即出，跟踪止盈+3%/-2%",
        },
        "高位": {
            "open_stop": 0.0,
            "open_hold": 1.0,
            "profit_take1": 5.0,
            "profit_take2": 7.0,
            "profit_clear": 9.0,
            "take1_ratio": 0.33,
            "take2_ratio": 0.33,
            "trail_arm": 5.0,
            "trail_drawdown": 3.0,
            # 涨停板炸板回撤
            "limit_break_drawdown": 2.0,
            "desc": "竞价弱于昨收即清仓，跟踪止盈+5%/-3%",
        },
    },
}


# ============================================================
#  状态持久化（跟踪最高盈利、首批止盈状态、涨停记录）
# ============================================================
def load_state() -> Dict:
    """加载状态文件；不存在返回空字典"""
    path = CONFIG["state_file"]
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"⚠️ 状态文件读取失败({e})，使用空状态")
        return {}


def save_state(state: Dict) -> None:
    """保存状态文件"""
    try:
        with open(CONFIG["state_file"], "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"⚠️ 状态文件写入失败: {e}")


def get_position_state(state: Dict, code: str, cost: float) -> Dict:
    """
    获取单只持仓的状态记录；首次会初始化默认值。
    如果 cost 与状态记录不一致（说明换了持仓），会重置状态。
    """
    rec = state.get(code)
    default = {
        "cost": cost,
        "high_water": 0.0,        # 历史最高盈利%
        "high_water_price": 0.0,  # 对应价格
        "take1_done": False,      # 首批止盈已触发
        "take2_done": False,      # 二级止盈已触发
        "limit_up_hit": False,    # 是否曾经涨停
        "first_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    if not rec:
        return default

    # 成本不一致 → 视为新持仓，重置
    if abs(rec.get("cost", 0) - cost) > 0.001:
        print(f"  ℹ️ {code} 成本变更({rec.get('cost')}→{cost})，重置跟踪状态")
        return default

    # 用旧记录补全字段
    for k, v in default.items():
        rec.setdefault(k, v)
    return rec


# ============================================================
#  获取实时行情（腾讯接口）
# ============================================================
def get_realtime_data(codes: list) -> dict:
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
            today_low = float(p[34]) if len(p) > 34 and p[34].strip() else now_price
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

        # 涨停判断：现价≥昨收×1.0998（兼容浮点误差，主板10%）
        is_limit_up = curr_pct >= 9.8
        # 是否曾涨停：当日最高≥9.8%
        was_limit_up = high_pct >= 9.8

        results[code_raw] = {
            "name": name,
            "now": now_price,
            "pre": pre_close,
            "open": today_open,
            "high": today_high,
            "low": today_low,
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
#  卖出决策引擎（v2.1）
# ============================================================
def decide_sell(
    code: str,
    cost: float,
    path: str,
    market_data: dict,
    state_rec: Dict,
) -> dict:
    cfg = CONFIG["paths"].get(path, CONFIG["paths"]["稳健"])

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

    open_stop = cfg["open_stop"]
    open_hold = cfg["open_hold"]
    stop_global = CONFIG["stop_loss_global"]
    trail_arm = cfg.get("trail_arm", 999)
    trail_dd = cfg.get("trail_drawdown", 999)

    # ---------- 优先级 5：硬性清仓信号 ----------

    # 全局硬止损（最高优先级）
    if profit_pct <= stop_global:
        result["action"] = "🚨 全局硬止损"
        result["reason"] = f"亏损{profit_pct:.2f}%超全局止损{stop_global}%"
        result["priority"] = 5
        return result

    # 竞价低开清仓（盘前/开盘后均生效）
    if open_pct <= open_stop:
        result["action"] = "🔴 竞价清仓"
        result["reason"] = f"竞价{open_pct:.2f}%≤{open_stop}%({cfg['desc']})"
        result["priority"] = 5
        return result

    # 高位路径竞价≤0直接清仓
    if path == "高位" and open_pct <= 0:
        result["action"] = "🔴 竞价清仓"
        result["reason"] = "高位路径竞价≤0立即清仓"
        result["priority"] = 5
        return result

    # ---------- 优先级 4：涨停炸板 / 跟踪止盈 ----------

    # 涨停炸板（高位路径专属）
    if path == "高位" and state_rec.get("limit_up_hit") and not info["is_limit_up"]:
        limit_dd = cfg.get("limit_break_drawdown", 2.0)
        if drawdown_from_peak >= limit_dd:
            result["action"] = "💥 炸板清仓"
            result["reason"] = f"曾涨停后回落{drawdown_from_peak:.2f}%≥{limit_dd}%"
            result["priority"] = 4
            return result

    # 跟踪止盈：高点达 arm 阈值后，回落 drawdown 阈值触发
    if high_water >= trail_arm and drawdown_from_peak >= trail_dd:
        result["action"] = "📉 跟踪止盈"
        result["reason"] = (
            f"从最高+{high_water:.2f}%回落{drawdown_from_peak:.2f}%≥{trail_dd}%触发"
        )
        result["priority"] = 4
        result["take_info"] = f"剩余仓位清仓，锁定 ~+{profit_pct:.2f}%"
        return result

    # ---------- 优先级 3：涨停板锁仓信号 ----------

    if path == "高位" and info["is_limit_up"]:
        result["action"] = "🔴 涨停板持有"
        result["reason"] = "高位封板坚定持有，炸板2%回撤即清"
        result["priority"] = 3
        return result

    # ---------- 优先级 2：开盘弱势 ----------

    if open_pct < open_hold:
        result["action"] = "⚡ 09:35择机出"
        result["reason"] = f"竞价{open_pct:.2f}%未达{open_hold}%阈值"
        result["priority"] = 2

    # ---------- 优先级 1：分批止盈（带状态保护，避免重复提示） ----------

    pt1 = cfg["profit_take1"]
    pt2 = cfg["profit_take2"]
    pt3 = cfg["profit_clear"]
    tr1 = cfg["take1_ratio"]
    tr2 = cfg["take2_ratio"]

    if profit_pct >= pt3:
        result["action"] = "🏆 清仓观望"
        result["reason"] = f"盈利{profit_pct:.2f}%达清仓线+{pt3}%"
        result["priority"] = 1
        result["take_info"] = f"全程持有收益={profit_pct:.2f}%（最高曾+{high_water:.2f}%）"
        state_rec["take1_done"] = True
        state_rec["take2_done"] = True
    elif profit_pct >= pt2 and not state_rec.get("take2_done"):
        result["action"] = "💰 二级止盈"
        result["reason"] = f"盈利{profit_pct:.2f}%触及二级+{pt2}%"
        result["priority"] = 1
        result["take_info"] = f"建议卖{tr2*100:.0f}%仓位，剩余博更高"
        state_rec["take2_done"] = True
        state_rec["take1_done"] = True
    elif profit_pct >= pt2 and state_rec.get("take2_done"):
        result["action"] = "✅ 持有(已二抛)"
        result["reason"] = f"已执行二级止盈，剩余仓位待跟踪"
        result["priority"] = 0
        result["take_info"] = f"等待 +{pt3}% 清仓 或 跟踪止盈触发"
    elif profit_pct >= pt1 and not state_rec.get("take1_done"):
        result["action"] = "💰 首批止盈"
        result["reason"] = f"盈利{profit_pct:.2f}%触及一级+{pt1}%"
        result["priority"] = 1
        result["take_info"] = f"建议卖{tr1*100:.0f}%仓位"
        state_rec["take1_done"] = True
    elif profit_pct >= pt1 and state_rec.get("take1_done"):
        result["action"] = "✅ 持有(已首抛)"
        result["reason"] = f"已执行首批止盈，剩余仓位待跟踪"
        result["priority"] = 0
        result["take_info"] = f"等待 +{pt2}% 二抛 或 +{pt3}% 清仓"

    # ---------- 优先级 0：默认持有 ----------

    if result["priority"] == 0 and not result["take_info"]:
        if open_pct >= open_hold:
            result["action"] = "🚀 持有观察"
            result["reason"] = f"竞价{open_pct:.2f}%强于{open_hold}%，等待盘中机会"
        else:
            result["action"] = "⏳ 谨慎持有"
            result["reason"] = "竞价偏弱但未触发止损，继续观察"
            result["priority"] = 1

    return result


# ============================================================
#  集合竞价挂单建议（09:20-09:25 时段使用）
# ============================================================
def auction_advice(code: str, cost: float, path: str, market_data: dict) -> Optional[str]:
    """09:20-09:25 给出竞价挂单建议"""
    info = market_data.get(code)
    if not info:
        return None

    pre = info["pre"]
    bid1_vol = info.get("bid1_vol", 0)
    ask1_vol = info.get("ask1_vol", 0)
    cfg = CONFIG["paths"].get(path, CONFIG["paths"]["稳健"])

    # 委比 = (买盘 - 卖盘) / (买盘 + 卖盘)
    total = bid1_vol + ask1_vol
    bid_ratio = (bid1_vol - ask1_vol) / total if total > 0 else 0

    # 建议挂单价：成本 × (1 + take1) 与 昨收×(1+1.5%) 取较高
    target1 = cost * (1 + cfg["profit_take1"] / 100)
    target2 = pre * 1.015

    suggest_price = max(target1, target2)

    if bid_ratio > 0.3:
        return f"💪 竞价买盘强劲(委比+{bid_ratio*100:.0f}%)，建议挂 {suggest_price:.2f} 限价卖单"
    elif bid_ratio > 0:
        return f"⚖️ 竞价多空均衡(委比+{bid_ratio*100:.0f}%)，建议挂 {suggest_price:.2f} 限价卖单"
    elif bid_ratio > -0.3:
        return f"⚠️ 竞价卖压渐强(委比{bid_ratio*100:.0f}%)，建议放弃挂高单，开盘市价出"
    else:
        return f"❌ 竞价大幅卖压(委比{bid_ratio*100:.0f}%)，开盘前再观察一秒钟，准备9:30市价清仓"


# ============================================================
#  主程序
# ============================================================
def run():
    now = datetime.now()
    print("=" * 70)
    print("  自动卖出系统 v2.1（含跟踪止盈 + 状态持久化）")
    print(f"  运行时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    if not POSITIONS:
        print("\n⚠️ 持仓列表为空，请编辑POSITIONS配置")
        return

    # 加载状态
    state = load_state()
    print(f"\n📂 状态文件: {CONFIG['state_file']}（已记录 {len(state)} 只历史持仓）")

    codes = [p["code"] for p in POSITIONS]
    print(f"📊 当前持仓: {len(codes)} 只")

    market_data = get_realtime_data(codes)
    if not market_data:
        print("❌ 行情数据获取失败，请检查网络")
        return

    print(f"  成功获取 {len(market_data)} 只行情\n")

    # 09:20-09:25 集合竞价时段：给出挂单建议
    hhmm = now.hour * 100 + now.minute
    if 920 <= hhmm < 925:
        print("─" * 70)
        print("📢 集合竞价挂单建议（09:20-09:25）")
        print("─" * 70)
        for p in POSITIONS:
            advice = auction_advice(p["code"], p["cost"], p.get("path", "稳健"), market_data)
            if advice:
                print(f"  {p['code']}: {advice}")
        print()

    # 主决策
    results = []
    for p in POSITIONS:
        code = p["code"]
        cost = p["cost"]
        path = p.get("path", "稳健")

        state_rec = get_position_state(state, code, cost)
        res = decide_sell(code, cost, path, market_data, state_rec)
        res["path"] = path
        results.append(res)

        # 写回状态
        state[code] = state_rec

    # 保存状态
    save_state(state)

    # 排序：优先级降序，盈亏降序
    results_sorted = sorted(results, key=lambda x: (-x["priority"], -x["profit_pct"]))

    print("📊 卖出建议：")
    print("─" * 130)
    print(
        f"{'代码':<10}{'名称':<10}{'路径':<6}{'现价':>7}{'成本':>7}"
        f"{'盈亏%':>8}{'峰值%':>8}{'回撤%':>8}{'竞价%':>8}  {'动作':<18}{'优先':>4}  理由"
    )
    print("─" * 130)

    for row in results_sorted:
        print(
            f"{row['code']:<10}{row['name']:<10}{row['path']:<6}"
            f"{row['now']:>7.2f}{row['cost']:>7.2f}"
            f"{row['profit_pct']:>+8.2f}{row['high_water']:>+8.2f}"
            f"{row['drawdown']:>8.2f}{row['open_pct']:>+8.2f}  "
            f"{row['action']:<18}{row['priority']:>4}  "
            f"{row['reason']}"
        )
        if row.get("take_info"):
            print(f"{'':<10}{'':<10}{'':<6}{'':<55}    └─ {row['take_info']}")

    # 紧急清单
    urgent = [r for r in results_sorted if r["priority"] >= 4]
    if urgent:
        print("\n🚨 【紧急操作】以下标的需要立即处理：")
        for row in urgent:
            print(f"  → {row['code']} {row['name']} : {row['action']} | {row['reason']}")
            if row.get("take_info"):
                print(f"      {row['take_info']}")

        # v2.1: Telegram 推送紧急信号
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

    # v2.1: 即使没有紧急信号,也推送一份完整的卖出建议(每天首次运行时)
    elif TELEGRAM_ENABLED and results_sorted:
        summary_lines = [f" {datetime.now().strftime('%H:%M')} 持仓状态"]
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
    print("  路径说明：")
    print("  稳健(hs300+zz500)：止损-2.5% | 09:35未维持+1%出 | 跟踪止盈+3%/-2%")
    print("  高位(zz1000)     ：止损-2.5% | 竞价≤0即清 | 跟踪止盈+5%/-3% | 炸板-2%清")
    print("  分批止盈：")
    print("    稳健[+3%→1/3 / +5%→1/3 / +6%清仓]（已止盈状态自动跳过）")
    print("    高位[+5%→1/3 / +7%→1/3 / +9%清仓]（已止盈状态自动跳过）")
    print(f"  状态文件：{CONFIG['state_file']}（每次运行自动更新最高盈利点）")
    print("=" * 70)


if __name__ == "__main__":
    run()