"""
自动卖出系统 v2.2（sell_new.py）
========================================
基于 v2.1 新增「封板成交率评估(猎手公式)」

v2.2 新增特性：
  ✓ 封板成交率评估（基于猎手公式）
    - 公式: 封单成交率 = (收盘买一封单量 / 全天成交量) × 100%
    - 5档强度判断: 超强/中强/偏弱/极弱/无承接
    - 流通市值动态阈值(小盘从严,大盘放宽)
  ✓ POSITIONS 新字段:
    - limit_up_at_buy: 标记买入时是涨停状态
    - mktcap_yi: 流通市值(亿),用于动态阈值
  ✓ 高位路径决策升级:
    - T+1 09:25 评估封板强度
    - 强(level≥4): 强势持有,等盘中跟踪止盈
    - 弱(level≤2): 竞价直接清仓,不博次日
  ✓ Telegram 推送显示封板强度等级

v2.1 继承特性：
  ✓ 持久化最高盈利点（high_water）到 ./sell_state.json
  ✓ 跟踪止盈：从最高点回落 N% 自动卖出
    - 稳健路径：高点≥+3% 后回落 ≥2% 触发
    - 高位路径：高点≥+5% 后回落 ≥3% 触发
  ✓ 首批止盈状态保护：已止盈的股票不会重复触发"首批止盈"提示
  ✓ 涨停后炸板检测：从涨停回落 ≥2% 强制清仓信号
  ✓ 09:25 集合竞价挂单建议（开盘前预挂限价单）

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
    - 【v2.2 新】涨停板买入的票,T+1开盘前评估封板强度

封板强度判断标准(基于流通市值动态调整):
  中盘股(50-200亿)基准:
    >30%: 超强涨停 - 持有等连板
    15-30%: 中强涨停 - 冲高即走
    5-15%: 偏弱涨停 - 9:30冲高出
    1-5%: 极弱涨停 - 竞价立即出
    <1%: 无承接 - 平开即弱势

运行时间：
  09:20-09:25：查看集合竞价委比，建议挂单价
  09:26-09:30：竞价结束稳定后，主决策窗口（含封板强度评估）
  盘中定时：09:30 / 10:00 / 10:30 / 13:00 / 14:00
"""

import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

# 北京时间 (UTC+8)
BEIJING_TZ = timezone(timedelta(hours=8))

def get_beijing_time():
    """获取北京时间"""
    return datetime.now(BEIJING_TZ)

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
    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    if _SCRIPT_DIR not in sys.path:
        sys.path.insert(0, _SCRIPT_DIR)
    from position_manager import get_positions as load_positions_dynamic
    POSITIONS = load_positions_dynamic()
    if not POSITIONS:
        POSITIONS = []
except ImportError:
    POSITIONS = []

CONFIG = {
    "state_file": os.path.join(os.path.dirname(os.path.abspath(__file__)), "sell_state.json"),
    "check_times": ["09:30", "10:00", "10:30", "13:00", "14:00"],
    "stop_loss_global": -2.5,

    # ============== v2.2 新增:封板强度评估配置 ==============
    "limit_strength": {
        # 基准阈值(中盘股 50-200亿 标准)
        "threshold_super": 30.0,    # >30% 超强涨停
        "threshold_strong": 15.0,   # 15-30% 中强
        "threshold_weak": 5.0,      # 5-15% 偏弱
        "threshold_dead": 1.0,      # 1-5% 极弱, <1% 无承接

        # 流通市值动态系数
        # 小盘股(<50亿)阈值×1.5,要求更严
        # 大盘股(>500亿)阈值×0.5,门槛更低
        "mktcap_multipliers": [
            (50,   1.5),   # <50亿  → ×1.5
            (200,  1.0),   # 50-200亿 → ×1.0(基准)
            (500,  0.7),   # 200-500亿 → ×0.7
            (10000, 0.5),  # >500亿 → ×0.5
        ],
    },

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
    """原子写入状态文件，防止并发实例间数据丢失"""
    try:
        tmp = CONFIG["state_file"] + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG["state_file"])
    except (IOError, OSError) as e:
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
        # v2.2: 封板强度评估状态
        "limit_strength_evaluated": False,  # 是否已评估过封板强度(只评估一次)
        "limit_strength_level": 0,          # 评估出的强度等级 1-5
        "limit_strength_ratio": 0.0,        # 评估出的封单成交率
        "first_seen": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
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
def _normalize_code(code: str) -> str:
    """将股票代码标准化为腾讯接口格式 (如 sh600519, sz002439, bj830799)"""
    pure = code.replace("sh.", "").replace("sz.", "").replace("bj.", "")
    if pure.startswith(("6", "9")):
        return "sh" + pure
    elif pure.startswith(("8", "43")):
        return "bj" + pure
    else:
        return "sz" + pure


def get_realtime_data(codes: list) -> dict:
    code_str = ",".join([_normalize_code(c) for c in codes])
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

        # 涨停判断：根据板块动态阈值（主板10%/创业板科创板20%/北交所30%）
        limit_threshold = get_limit_pct(code_raw) - 0.2
        is_limit_up = curr_pct >= limit_threshold
        was_limit_up = high_pct >= limit_threshold

        # 同时存储原始代码和标准化代码（sh.603319）作为 key，
        # 因为 positions.json 使用标准化格式，而腾讯接口返回原始格式
        data = {
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
        results[code_raw] = data
        # 添加标准化 key 映射
        if code_raw.startswith("sh"):
            results[f"sh.{code_raw[2:]}"] = data
        elif code_raw.startswith("sz"):
            results[f"sz.{code_raw[2:]}"] = data
        elif code_raw.startswith("bj"):
            results[f"bj.{code_raw[2:]}"] = data

    return results


# ============================================================
#  v2.2: 封板强度评估(基于猎手公式)
# ============================================================
def get_limit_pct(code: str) -> float:
    """根据股票代码返回涨停阈值(动态判断板块)"""
    pure_code = code.replace("sh.", "").replace("sz.", "").replace("bj.", "")
    # 创业板/科创板 20%
    if pure_code.startswith("30") or pure_code.startswith("68"):
        return 19.8
    # 北交所 30%
    if pure_code.startswith("8") or pure_code.startswith("43"):
        return 29.8
    # 主板/中小板 10%
    return 9.8


def _get_yesterday_vol_baostock(code: str) -> float:
    import baostock as bs
    try:
        lg = bs.login()
        if lg.error_code != '0':
            return 0
        pure = code.replace("sh.", "").replace("sz.", "").replace("bj.", "")
        if pure.startswith(("6", "9")):
            bs_code = f"sh.{pure}"
        elif pure.startswith(("8", "4")):
            bs_code = f"bj.{pure}"
        else:
            bs_code = f"sz.{pure}"
        today = get_beijing_time()
        start_date = (today - timedelta(days=5)).strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")
        rs = bs.query_history_k_data_plus(
            bs_code, "date,volume", start_date=start_date, end_date=end_date,
            frequency="d", adjustflag="3",
        )
        if rs.error_code == '0':
            rows = rs.get_data()
            if not rows.empty and len(rows) >= 1:
                result = float(rows.iloc[-1]["volume"]) / 100 if rows.iloc[-1]["volume"] else 0
                return result
        return 0
    except Exception:
        return 0
    finally:
        try:
            bs.logout()
        except Exception:
            pass


def evaluate_limit_strength(
    code: str,
    market_data: dict,
    mktcap_yi: float = 0,
) -> Optional[Dict]:
    """
    评估封板强度(基于猎手公式)

    封单成交率 = (收盘买一封单量 / 全天成交量) × 100%

    Args:
        code: 股票代码(腾讯接口格式如 sh600519)
        market_data: get_realtime_data 返回的数据字典
        mktcap_yi: 流通市值(亿),用于动态调整阈值

    Returns:
        None: 不是涨停板或数据不足
        dict: {
            "ratio": 封单成交率%,
            "level": 强度等级 1-5(5最强),
            "label": 中文标签,
            "action": 建议动作,
            "advice": 详细建议,
        }
    """
    info = market_data.get(code)
    if not info:
        return None

    curr_pct = info.get("curr_pct", 0)

    # 判断是否当日涨停(允许 0.2% 浮点误差)
    limit_pct = get_limit_pct(code)
    if curr_pct < limit_pct - 0.2:
        return None

    # 拿封单量和成交量(腾讯接口字段都有)
    bid1_vol = info.get("bid1_vol", 0)
    today_vol = info.get("vol", 0)

    # 盘前(09:26)腾讯成交量已重置为0，需用昨日baostock数据
    if today_vol < 100 and bid1_vol > 0:
        yesterday_vol = _get_yesterday_vol_baostock(code)
        if yesterday_vol > 0:
            today_vol = yesterday_vol

    if today_vol <= 0:
        return None

    # 封板成交率(用股数比,腾讯接口直接给手数)
    ratio = (bid1_vol / today_vol) * 100

    # 流通市值动态调整阈值
    multiplier = 1.0
    if mktcap_yi > 0:
        for mktcap_threshold, mult in CONFIG["limit_strength"]["mktcap_multipliers"]:
            if mktcap_yi < mktcap_threshold:
                multiplier = mult
                break

    cfg = CONFIG["limit_strength"]
    th_super = cfg["threshold_super"] * multiplier
    th_strong = cfg["threshold_strong"] * multiplier
    th_weak = cfg["threshold_weak"] * multiplier
    th_dead = cfg["threshold_dead"] * multiplier

    if ratio > th_super:
        return {
            "ratio": round(ratio, 2),
            "level": 5,
            "label": "🚀 超强涨停",
            "action": "强势持有",
            "advice": (
                f"封单率{ratio:.1f}% > {th_super:.1f}%(超强阈值),"
                f"次日易连板/高溢价"
            ),
        }
    elif ratio > th_strong:
        return {
            "ratio": round(ratio, 2),
            "level": 4,
            "label": "💪 中强涨停",
            "action": "冲高落袋",
            "advice": (
                f"封单率{ratio:.1f}%(中强),9:30冲高即走,保利润"
            ),
        }
    elif ratio > th_weak:
        return {
            "ratio": round(ratio, 2),
            "level": 3,
            "label": "⚠️ 偏弱涨停",
            "action": "9:30冲高即出",
            "advice": (
                f"封单率{ratio:.1f}%(偏弱),开盘冲高第一时间出货"
            ),
        }
    elif ratio > th_dead:
        return {
            "ratio": round(ratio, 2),
            "level": 2,
            "label": "🔴 极弱涨停",
            "action": "竞价直接挂卖",
            "advice": (
                f"封单率{ratio:.1f}%(极弱),集合竞价即挂卖单"
            ),
        }
    else:
        return {
            "ratio": round(ratio, 2),
            "level": 1,
            "label": "💀 无承接",
            "action": "立即离场",
            "advice": (
                f"封单率{ratio:.1f}%(无承接),平开即弱势,无幻想"
            ),
        }


# ============================================================
#  封板强度日志记录
# ============================================================
_LIMIT_STRENGTH_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "limit_strength_log.csv")

def log_limit_strength(
    date_str: str,
    code: str,
    name: str,
    path: str,
    mktcap_yi: float,
    ratio: float,
    level: int,
    label: str,
    action: str,
    actual_next_result: str = "",
):
    """追加封板强度评估记录到 CSV，5天后可用 Excel 分析准确率"""
    header = "date,code,name,path,mktcap_yi,ratio,level,label,action,actual_next_result"
    row = f"{date_str},{code},{name},{path},{mktcap_yi:.1f},{ratio:.2f},{level},{label},{action},{actual_next_result}"

    file_exists = os.path.exists(_LIMIT_STRENGTH_LOG_FILE)
    try:
        with open(_LIMIT_STRENGTH_LOG_FILE, "a", encoding="utf-8") as f:
            if not file_exists:
                f.write(header + "\n")
            f.write(row + "\n")
    except Exception:
        pass  # 沙箱/只读环境跳过写入


# ============================================================
#  卖出决策引擎（v2.2）
# ============================================================
def decide_sell(
    code: str,
    cost: float,
    path: str,
    market_data: dict,
    state_rec: Dict,
    pos_meta: Optional[Dict] = None,  # v2.2: 持仓元数据(limit_up_at_buy, mktcap_yi)
) -> dict:
    cfg = CONFIG["paths"].get(path, CONFIG["paths"]["稳健"])
    if pos_meta is None:
        pos_meta = {}

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

    # ---------- 优先级 4.5: v2.2 封板强度评估(开盘前用) ----------
    # 仅适用: 高位路径 + 买入时是涨停状态 + T+1 开盘前(09:30 前)
    # 评估 T 日收盘的封板强度,决定 T+1 是持有还是卖出
    
    if (
        path == "高位" 
        and pos_meta.get("limit_up_at_buy")
        and not state_rec.get("limit_strength_evaluated")  # 只评估一次
    ):
        mktcap_yi = pos_meta.get("mktcap_yi", 0)
        strength = evaluate_limit_strength(code, market_data, mktcap_yi)
        
        if strength:
            # 把封板强度写入状态(只评估一次)
            state_rec["limit_strength_evaluated"] = True
            state_rec["limit_strength_level"] = strength["level"]
            state_rec["limit_strength_ratio"] = strength["ratio"]
            
            # 记录到 CSV 日志
            stock_name = market_data.get(code, {}).get("name", "")
            today_str = get_beijing_time().strftime("%Y-%m-%d")
            log_limit_strength(
                date_str=today_str,
                code=code,
                name=stock_name,
                path=path,
                mktcap_yi=mktcap_yi,
                ratio=strength["ratio"],
                level=strength["level"],
                label=strength["label"],
                action=strength["action"],
            )
            
            # 把强度信息写入 result
            result["limit_strength"] = strength
            
            # 根据强度等级触发不同动作
            if strength["level"] >= 4:
                # 超强(5) + 中强(4): 强势持有,等盘中跟踪止盈
                result["action"] = f"{strength['label']}持有"
                result["reason"] = strength["advice"]
                result["priority"] = 0
                result["take_info"] = "等盘中跟踪止盈/分批止盈触发"
                # 不 return,让后续判断继续(可能被全局止损/竞价低开覆盖)
            elif strength["level"] == 3:
                # 偏弱: 9:30 冲高即出
                result["action"] = f"{strength['label']}冲高出"
                result["reason"] = strength["advice"]
                result["priority"] = 2
                # 不 return,继续走开盘弱势判断
            elif strength["level"] <= 2:
                # 极弱(2) + 无承接(1): 竞价立即清仓
                result["action"] = f"{strength['label']}清仓"
                result["reason"] = strength["advice"]
                result["priority"] = 4
                return result  # 直接返回,优先级足够高

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
    now = get_beijing_time()
    print("=" * 45)
    print("  自动卖出系统 v2.2（含封板强度评估 + 跟踪止盈 + 状态持久化）")
    print(f"  运行时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 45)

    # 每次运行时重新加载持仓（支持盘中通过 Telegram 动态添加）
    positions = load_positions_dynamic()
    if not positions:
        print("\n⚠️ 持仓列表为空，请通过 Telegram /add 命令添加持仓")
        return

    # 加载状态
    state = load_state()
    print(f"\n📂 状态文件: {CONFIG['state_file']}（已记录 {len(state)} 只历史持仓）")

    codes = [p["code"] for p in positions]
    print(f"📊 当前持仓: {len(codes)} 只")

    market_data = get_realtime_data(codes)
    if not market_data:
        print("❌ 行情数据获取失败，请检查网络")
        return

    print(f"  成功获取 {len(market_data)} 只行情\n")

    # 09:20-09:25 集合竞价时段：给出挂单建议
    hhmm = now.hour * 100 + now.minute
    if 920 <= hhmm < 925:
        print("─" * 45)
        print("📢 集合竞价挂单建议（09:20-09:25）")
        print("─" * 45)
        for p in positions:
            advice = auction_advice(p["code"], p["cost"], p.get("path", "稳健"), market_data)
            if advice:
                print(f"  {p['code']}: {advice}")
        print()

    # 主决策
    results = []
    for p in positions:
        code = p["code"]
        cost = p["cost"]
        path = p.get("path", "稳健")

        # v2.2: 提取持仓元数据(用于封板强度评估)
        pos_meta = {
            "limit_up_at_buy": p.get("limit_up_at_buy", False),
            "mktcap_yi": p.get("mktcap_yi", 0),
            "entry_date": p.get("entry_date", ""),
        }

        state_rec = get_position_state(state, code, cost)
        res = decide_sell(code, cost, path, market_data, state_rec, pos_meta)
        res["path"] = path
        results.append(res)

        # 写回状态
        state[code] = state_rec

    # 保存状态
    save_state(state)

    # 排序：优先级降序，盈亏降序
    results_sorted = sorted(results, key=lambda x: (-x["priority"], -x["profit_pct"]))

    print("📊 卖出建议：")
    print("─" * 45)
    print(
        f"{'代码':<10}{'名称':<10}{'路径':<6}{'现价':>7}{'成本':>7}"
        f"{'盈亏%':>8}{'峰值%':>8}{'回撤%':>8}{'竞价%':>8}  {'动作':<18}{'优先':>4}  理由"
    )
    print("─" * 45)

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
        # v2.2: 显示封板强度评估结果
        if row.get("limit_strength"):
            ls = row["limit_strength"]
            print(f"{'':<10}{'':<10}{'':<6}{'':<55}    🎯 封板强度: {ls['label']} "
                  f"(率{ls['ratio']}% / 等级{ls['level']}/5)")

    # 紧急清单
    urgent = [r for r in results_sorted if r["priority"] >= 4]
    if urgent:
        print("\n🚨 【紧急操作】以下标的需要立即处理：")
        for row in urgent:
            print(f"  → {row['code']} {row['name']} : {row['action']} | {row['reason']}")
            if row.get("take_info"):
                print(f"      {row['take_info']}")

        # v2.1: Telegram 推送紧急信号（每天只推一次，通过状态文件记录）
        if TELEGRAM_ENABLED:
            today_str = now.strftime("%Y-%m-%d")
            notified_codes = state.get("_notified_today", {})
            
            for row in urgent:
                code = row["code"]
                action_key = f"{today_str}_{code}_{row['action']}"
                if action_key not in notified_codes:
                    # v2.2: 如果有封板强度信息,附加到 reason
                    reason_with_strength = row["reason"]
                    if row.get("limit_strength"):
                        ls = row["limit_strength"]
                        reason_with_strength = (
                            f"{row['reason']}\n"
                            f"封板强度: {ls['label']} "
                            f"(率{ls['ratio']}%/等级{ls['level']}/5)"
                        )
                    try:
                        send_sell_alert(
                            code=row["code"],
                            name=row["name"],
                            action=row["action"],
                            reason=reason_with_strength,
                            profit_pct=row["profit_pct"],
                            priority=row["priority"],
                        )
                        notified_codes[action_key] = now.strftime("%H:%M:%S")
                    except Exception as e:
                        print(f"  ⚠️ 推送失败 {row['code']}: {e}")
            
            state["_notified_today"] = notified_codes
            save_state(state)
            print("  📲 紧急信号已推送到 Telegram（每日一次）\n")

    # v2.1: 即使没有紧急信号,也推送一份完整的卖出建议(每天首次运行时)
    if TELEGRAM_ENABLED and results_sorted:
        # 只在尾盘时段(13:50-15:00)推送一次完整持仓状态
        hhmm = now.hour * 100 + now.minute
        if 1350 <= hhmm <= 1500:
            summary_lines = [f"📊 📊 {now.strftime('%H:%M')} 尾盘前跟踪\n⏰"]
            summary_lines.append("=" * 40)
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
    print("  v2.2 封板强度评估(仅 limit_up_at_buy=True 的高位票):")
    print("    超强(>30%)→持有 | 中强(15-30%)→冲高出 | 偏弱(5-15%)→9:30出")
    print("    极弱(1-5%)→竞价清 | 无承接(<1%)→平开即出")
    print("    (按流通市值动态调整: 小盘×1.5 / 中盘×1.0 / 大盘×0.5)")
    print(f"  状态文件：{CONFIG['state_file']}（每次运行自动更新最高盈利点）")
    print("=" * 45)


if __name__ == "__main__":
    run()