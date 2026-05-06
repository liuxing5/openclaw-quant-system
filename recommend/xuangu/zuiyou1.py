"""
隔夜选股法·最优融合版 (zuiyou1 v1.2)
========================================
v1.2 修订（2026-04-30）：
  ✓ 市值过滤按池子分档 —— 稳健池100-2000亿，高位池30-300亿
  ✓ 涨停阈值按板块动态判断 —— 主板10%/创业板20%/科创板20%/北交所30%
  ✓ 压力检测按路径分档 —— 稳健8%，高位15%
  ✓ 盘后时间安全检查 —— 15:10前运行post模式会警告

v1.1 修订（2026-04-29）：
  ✓ 市值过滤不再受 MODE 限制 —— post 模式同样启用 50-200亿过滤
  ✓ MA 计算统一不含今日 —— 严格无未来函数（修复 realtime 模式的潜在偏差）
  ✓ 当日记录覆盖而非跳过 —— 支持盘中→盘后二次验证回写

v1.0 改进：
  ✓ post 模式也使用腾讯实时接口（修复 baostock 数据延迟导致的假信号）
  ✓ 流通市值50-200亿硬过滤（8步法第4步）
  ✓ 成交量递增验证（8步法第5步）
  ✓ K线上方压力检测（8步法第6步后半段）
  ✓ 换手率恢复5%-10%硬过滤（8步法第3步原始要求）
  ✓ 稳健路径涨幅3%-5%严格遵循8步法

继承最优特性：
  ✓ V8: 10日去极值均量 + 时间加权量比 + 双池策略 + 情绪感知 + 连板高度惩罚
  ✓ V5: 昨收序列均线(无未来函数) + 三重风险扣分
  ✓ V3: 量化评分系统 + 贴线加分
  ✓ V8: ST/退市过滤 + 腾讯接口全字段防空

运行建议：
  盘中：14:25-14:35  CONFIG["MODE"] = "realtime"  （初步候选，仅供参考）
  盘后：15:10+       CONFIG["MODE"] = "post"      （最终决策，二次验证）

止损铁律：
  稳健路径：次日09:35未维持昨收+1%，直接出局
  高位路径：次日竞价弱于昨收，集合竞价结束即清仓
  全局止损：亏损超2.5%无条件止损
"""

import baostock as bs
import pandas as pd
import requests
import time
import os
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict

# ============================================================
#  Telegram 推送（可选,未配置时不影响主流程）
# ============================================================
try:
    from notifyTelegram import send_stock_picks
    TELEGRAM_ENABLED = True
except ImportError:
    TELEGRAM_ENABLED = False
    print("ℹ️ notifyTelegram 模块未找到,不启用 Telegram 推送")

# ============================================================
#  1. 全局配置
# ============================================================
CONFIG_STABLE = {
    "MODE": "realtime",
    "POOL": "hs300+zz500",
    "min_amount": 200_000_000,
    "max_amount": 5_000_000_000,
    "min_mktcap": 10_000_000_000,
    "max_mktcap": 200_000_000_000,
    "vol_ratio_min": 1.5,
    "vol_ratio_max": 8.0,
    "stable_pct_lo": 3.0,
    "stable_pct_hi": 5.0,
    "upper_pct_lo": 6.0,
    "upper_pct_hi": 9.7,
    "turn_min": 5.0,
    "turn_max": 10.0,
    "streak_penalty_threshold": 3,
    "streak_penalty_per_board": 10,
    "score_threshold": 70,
    "sentiment_cold": 30,
    "sentiment_normal": 60,
    "sentiment_hot": 100,
    "penalty_hot_turn": 12.0,
    "penalty_vol_ratio": 7.0,
    "penalty_ma_bias": 0.08,
    "position_ratio": "单票≤15%总仓位",
}

CONFIG_UPPER = {
    "MODE": "post",
    "POOL": "zz1000",
    "min_amount": 100_000_000,
    "max_amount": 3_000_000_000,
    "min_mktcap": 3_000_000_000,
    "max_mktcap": 30_000_000_000,
    "vol_ratio_min": 1.5,
    "vol_ratio_max": 10.0,
    "stable_pct_lo": 3.0,
    "stable_pct_hi": 5.0,
    "upper_pct_lo": 6.0,
    "upper_pct_hi": 9.7,
    "turn_min": 5.0,
    "turn_max": 10.0,
    "streak_penalty_threshold": 3,
    "streak_penalty_per_board": 10,
    "score_threshold": 70,
    "sentiment_cold": 30,
    "sentiment_normal": 60,
    "sentiment_hot": 100,
    "penalty_hot_turn": 12.0,
    "penalty_vol_ratio": 7.0,
    "penalty_ma_bias": 0.08,
    "position_ratio": "单票≤8%总仓位，严守止损",
}

CONFIG = CONFIG_STABLE

# 北京时间工具函数（GitHub Actions VM 使用 UTC 时区，需要 +8）
def beijing_now():
    """返回北京时间"""
    utc_now = datetime.now()
    return utc_now + timedelta(hours=8)

# 自动判断 MODE：15:10 后为 post，其余为 realtime
_now = beijing_now()
if _now.hour > 15 or (_now.hour == 15 and _now.minute >= 10):
    CONFIG["MODE"] = "post"
else:
    CONFIG["MODE"] = "realtime"

FIELDS_HIST = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg"


# ============================================================
#  1.5 涨停阈值判断
# ============================================================
def get_limit_pct(code: str) -> float:
    """根据股票代码返回涨停阈值"""
    pure_code = code.replace("sh.", "").replace("sz.", "").replace("bj.", "")

    if pure_code.startswith("30") or pure_code.startswith("68"):
        return 19.8
    if pure_code.startswith("8") or pure_code.startswith("43"):
        return 29.8
    return 9.8


def is_safe_post_time() -> bool:
    """判断当前是否为盘后安全数据时间"""
    now = beijing_now()
    if now.weekday() >= 5:
        return True
    return (now.hour, now.minute) >= (15, 10)


# ============================================================
#  2. 时间权重
# ============================================================
def get_time_weight() -> float:
    if CONFIG["MODE"] == "post":
        return 1.0

    now = beijing_now()
    h, m = now.hour, now.minute

    if h < 9 or (h == 9 and m < 30):
        return 1.0
    elif h >= 15:
        return 1.0

    if h == 9:
        passed = m - 30
    elif h == 10:
        passed = 30 + m
    elif h == 11 and m <= 30:
        passed = 90 + m
    elif h == 11 or h == 12:
        passed = 120
    elif h == 13:
        passed = 120 + m
    elif h == 14:
        passed = 180 + m
    else:
        passed = 1

    return max(0.01, min(1.0, passed / 240.0))


# ============================================================
#  3. 市场情绪感知
# ============================================================
def fetch_market_sentiment() -> Tuple[int, str]:
    """
    获取真实涨停家数（东财数据中心接口）。
    返回 (涨停家数, 情绪描述)。
    """
    try:
        from datetime import datetime, timezone, timedelta
        beijing_now = datetime.now(timezone(timedelta(hours=8)))
        today_dash = beijing_now.strftime('%Y-%m-%d')
        
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
            "columns": "ALL",
            "pageNumber": 1,
            "pageSize": 100,
            "sortColumns": "BILLBOARD_NET_AMT",
            "sortTypes": -1,
            "filter": f"(TRADE_DATE='{today_dash}')"
        }
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
        
        r = requests.get(url, params=params, headers=headers, timeout=10)
        data = r.json()
        result = data.get('result', {})
        zt_count = result.get('count', 0)
        
        if zt_count == 0:
            return 50, "正常"
    except Exception:
        return 50, "正常"

    if zt_count < CONFIG["sentiment_cold"]:
        mood = "冷淡"
    elif zt_count < CONFIG["sentiment_normal"]:
        mood = "正常"
    elif zt_count < CONFIG["sentiment_hot"]:
        mood = "活跃"
    else:
        mood = "高潮"

    return zt_count, mood


# ============================================================
#  4. 实时行情（腾讯接口，全字段防空）
#     修复：盘后(15:00+)也应使用腾讯接口获取收盘数据
#     因为baostock历史数据有延迟，盘后几小时可能还没更新
# ============================================================
def get_realtime_quotes(stock_list: list) -> dict:
    # 修复：不再在post模式下返回空字典
    # 盘后(15:00+)腾讯接口返回的就是最终收盘数据
    # if CONFIG["MODE"] == "post":
    #     return {}

    results = {}
    api_codes = [s.replace(".", "").lower() for s in stock_list]
    total_batches = (len(api_codes) + 49) // 50

    print(f"  [行情] 共 {len(api_codes)} 只，分 {total_batches} 批请求...")

    ok_count = 0
    for i in range(0, len(api_codes), 50):
        batch_no = i // 50 + 1
        chunk = api_codes[i: i + 50]
        url = f"http://qt.gtimg.cn/q={','.join(chunk)}"
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            if resp.status_code != 200:
                continue

            for line in resp.text.split(";"):
                if len(line) < 50:
                    continue
                p = line.split("~")
                if len(p) < 40:
                    continue
                try:
                    raw_key = p[0].split("=")[0][-8:]

                    def _f(idx, default=0.0):
                        try:
                            return float(p[idx]) if p[idx].strip() else default
                        except (ValueError, IndexError):
                            return default

                    now_price = _f(3)
                    if now_price <= 0:
                        continue

                    name = p[1] if len(p) > 1 else ""
                    if "ST" in name or "退" in name:
                        continue

                    results[raw_key] = {
                        "now": now_price,
                        "pct": _f(32),
                        "vol": _f(6) * 100,
                        "amount": _f(37) * 10000,
                        "high": _f(33),
                        "pre": _f(4),
                        "turn": _f(38),
                        "mktcap": _f(44) * 100_000_000 if _f(44) > 0 else 0,
                    }
                    ok_count += 1
                except Exception:
                    continue

            if batch_no % 10 == 0 or batch_no == total_batches:
                print(f"  [行情] 进度 {batch_no}/{total_batches} | 累计 {ok_count} 只")

        except Exception:
            continue

        time.sleep(0.12)

    print(f"  [行情] 完成 | 成功={ok_count}")
    return results


# ============================================================
#  5. 股票池获取
# ============================================================
def get_latest_trading_day() -> str:
    for delta in range(0, 14):
        day_str = (beijing_now() - timedelta(days=delta)).strftime("%Y-%m-%d")
        rs = bs.query_all_stock(day=day_str)
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if rows:
            return day_str
    return beijing_now().strftime("%Y-%m-%d")


def get_stock_pool() -> list:
    pool_name = CONFIG["POOL"]
    stocks_set = set()

    def _fetch_hs300():
        codes = []
        rs = bs.query_hs300_stocks()
        while rs.next():
            codes.append(rs.get_row_data()[1])
        return codes

    def _fetch_zz500():
        codes = []
        rs = bs.query_zz500_stocks()
        while rs.next():
            codes.append(rs.get_row_data()[1])
        return codes

    def _fetch_zz1000():
        hs300 = set(_fetch_hs300())
        zz500 = set(_fetch_zz500())
        exclude = hs300 | zz500
        trading_day = get_latest_trading_day()
        rs = bs.query_all_stock(day=trading_day)
        codes = []
        while rs.next():
            row = rs.get_row_data()
            code = row[0]
            if code.startswith(("sh.60", "sz.00", "sz.30")) and code not in exclude:
                codes.append(code)
        return sorted(codes)[:1000]

    def _fetch_market():
        trading_day = get_latest_trading_day()
        rs = bs.query_all_stock(day=trading_day)
        codes = []
        while rs.next():
            row = rs.get_row_data()
            code = row[0]
            if code.startswith(("sh.60", "sz.00", "sz.30")):
                codes.append(code)
        return codes

    if pool_name == "hs300":
        stocks_set.update(_fetch_hs300())
    elif pool_name == "zz500":
        stocks_set.update(_fetch_zz500())
    elif pool_name == "hs300+zz500":
        stocks_set.update(_fetch_hs300())
        stocks_set.update(_fetch_zz500())
    elif pool_name == "zz1000":
        stocks_set.update(_fetch_zz1000())
    else:
        stocks_set.update(_fetch_market())

    stocks = sorted(stocks_set)
    print(f"  股票池: [{pool_name}] 共 {len(stocks)} 只")
    return stocks


# ============================================================
#  6. 核心量化逻辑（8步法完整实现）
# ============================================================
def analyze_ultimate(
    hist_df: pd.DataFrame,
    code: str,
    real_info: Optional[dict],
    zt_count: int,
    time_weight: float,
) -> Optional[dict]:

    if hist_df is None or len(hist_df) < 15:
        return None

    name_lower = code.lower()
    if "st" in name_lower:
        return None

    df = hist_df.copy()
    for col in ["close", "volume", "pctChg", "turn", "amount", "high", "low", "open", "preclose"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df = df[df["volume"] > 0]
    if len(df) < 12:
        return None

    # --- 6.1 决定今日数据来源 ---
    # 修复：只要有real_info(腾讯数据)，无论realtime还是post模式都使用
    # 因为盘后腾讯接口返回的是最终收盘数据，而baostock有延迟
    if real_info is not None:
        curr_price = real_info["now"]
        curr_pct = real_info["pct"]
        curr_vol = real_info["vol"]
        curr_amount = real_info["amount"]
        curr_turn = real_info.get("turn", 0.0)
        if curr_turn <= 0:
            curr_turn = float(df.iloc[-1]["turn"]) if "turn" in df.columns else 0.0
        # 盘后模式下，time_weight=1.0，不需要时间加权
        est_full_vol = curr_vol / time_weight if time_weight > 0 else curr_vol
        hist_vols = df["volume"].tolist()
    else:
        last = df.iloc[-1]
        curr_price = float(last["close"])
        curr_pct = float(last["pctChg"])
        curr_vol = float(last["volume"])
        curr_turn = float(last["turn"])
        curr_amount = float(last["amount"]) if "amount" in df.columns else 0.0
        est_full_vol = curr_vol
        hist_vols = df["volume"].tolist()[:-1]

    # --- STEP 1: 涨幅筛选 ---
    in_stable = CONFIG["stable_pct_lo"] <= curr_pct <= CONFIG["stable_pct_hi"]
    in_upper = CONFIG["upper_pct_lo"] <= curr_pct <= CONFIG["upper_pct_hi"]

    if zt_count < CONFIG["sentiment_normal"] and not in_stable:
        return None

    if not (in_stable or in_upper):
        return None

    # --- STEP 2: 成交额硬过滤 ---
    if curr_amount < CONFIG["min_amount"] or curr_amount > CONFIG["max_amount"]:
        return None

    # --- STEP 3: 换手率硬过滤（8步法原始5%-10%）---
    if curr_turn < CONFIG["turn_min"] or curr_turn > CONFIG["turn_max"]:
        return None

    # --- STEP 4: 流通市值过滤（8步法50亿-200亿）---
    # v1.1: 去掉 MODE 限制，post 模式也启用市值过滤
    mktcap = real_info.get("mktcap", 0) if real_info else 0
    if mktcap > 0:
        if mktcap < CONFIG["min_mktcap"] or mktcap > CONFIG["max_mktcap"]:
            return None

    # --- STEP 5: 量比计算（10日去极值均量 + 时间加权）---
    recent_vols = sorted(hist_vols[-12:])
    if len(recent_vols) < 4:
        return None
    trimmed = recent_vols[1:-1] if len(recent_vols) > 2 else recent_vols
    avg_vol_trimmed = sum(trimmed) / len(trimmed)

    vol_ratio = est_full_vol / avg_vol_trimmed if avg_vol_trimmed > 0 else 0
    if not (CONFIG["vol_ratio_min"] <= vol_ratio <= CONFIG["vol_ratio_max"]):
        return None

    # --- STEP 6a: 均线验证（昨收序列，严格无未来函数）---
    # v1.1: 统一不含今日收盘 —— 无论 realtime 还是 post
    # 原因：realtime 模式下 baostock 当日"close"是延迟数据，会污染均线
    #       post 模式下今日已收盘，但 MA 应基于"昨日及之前"的历史序列
    hist_close = df["close"].tolist()[:-1]

    if len(hist_close) < 10:
        return None

    ma5_yest = sum(hist_close[-5:]) / 5
    ma10_yest = sum(hist_close[-10:]) / 10
    ma20_yest = sum(hist_close[-20:]) / 20 if len(hist_close) >= 20 else ma10_yest

    if not (curr_price > ma5_yest > ma10_yest):
        return None

    # --- STEP 6b: K线上方压力检测 ---
    recent_highs = df["high"].tail(20).tolist()
    recent_highs = [float(x) for x in recent_highs if float(x) > 0]
    if recent_highs:
        max_recent_high = max(recent_highs)
        resistance_ratio = (max_recent_high - curr_price) / curr_price
        resistance_threshold = 0.15 if in_upper else 0.08
        if resistance_ratio > resistance_threshold:
            return None

    # --- STEP 5b: 成交量递增验证 ---
    recent_5d_vols = df["volume"].tail(5).tolist()
    recent_5d_vols = [float(x) for x in recent_5d_vols if float(x) > 0]
    vol_increasing = False
    if len(recent_5d_vols) >= 3:
        increasing_count = 0
        for j in range(1, len(recent_5d_vols)):
            if recent_5d_vols[j] >= recent_5d_vols[j - 1] * 0.9:
                increasing_count += 1
        vol_increasing = increasing_count >= len(recent_5d_vols) - 2

    # --- 连板高度判断 ---
    streak = 0
    pct_list = df["pctChg"].tolist()
    if CONFIG["MODE"] != "realtime":
        pct_list = pct_list[:-1]
    limit_pct = get_limit_pct(code)
    for p in reversed(pct_list):
        if p >= limit_pct:
            streak += 1
        else:
            break

    # --- 评分系统（基础分50）---
    score = 50
    tags = []

    # A. 涨幅路径
    if in_stable:
        score += 15
        tags.append("稳健蓄势")
        bias = (curr_price - ma5_yest) / ma5_yest if ma5_yest > 0 else 1
        if bias < 0.02:
            score += 10
            tags.append("紧贴MA5")
    elif in_upper:
        score += 20
        tags.append("高位博弈")
        if CONFIG["MODE"] == "post":
            today_high = float(df.iloc[-1]["high"]) if "high" in df.columns else 0
            if today_high > 0 and curr_price >= today_high * 0.998:
                score += 10
                tags.append("光头大阳")

    # B. 量比评分
    if 1.8 <= vol_ratio <= 4.0:
        score += 25
        tags.append("黄金放量")
    elif vol_ratio > 4.0:
        score += 10
        tags.append("爆量博弈")
    else:
        score += 5
        tags.append("量能达标")

    # C. 换手率评分
    if 5.0 <= curr_turn <= 8.0:
        score += 15
        tags.append("黄金换手")
    elif 8.0 < curr_turn <= 10.0:
        score += 8
        tags.append("换手偏高")

    # D. 成交量递增加分
    if vol_increasing:
        score += 10
        tags.append("量能递增")
    else:
        score -= 5

    # E. 连板高度
    if streak == 0:
        score += 5
        tags.append("首阳突破")
    elif streak == 1:
        score += 20
        tags.append("首板突破")
    elif streak == 2:
        score += 30
        tags.append("二连板")
    elif streak >= CONFIG["streak_penalty_threshold"]:
        bonus = 30
        penalty = (streak - 2) * CONFIG["streak_penalty_per_board"]
        net = bonus - penalty
        score += max(net, 0)
        tags.append(f"{streak}连板(高度风险)")

    # F. 情绪高潮期加成
    if zt_count >= CONFIG["sentiment_hot"] and in_upper:
        score += 10
        tags.append("情绪高潮加成")

    # --- 风险扣分 ---
    if curr_turn > CONFIG["penalty_hot_turn"]:
        score -= 20
        tags.append("换手过热↓")

    if vol_ratio > CONFIG["penalty_vol_ratio"]:
        score -= 15
        tags.append("量比过激↓")

    bias_ma5 = (curr_price - ma5_yest) / ma5_yest if ma5_yest > 0 else 0
    if bias_ma5 > CONFIG["penalty_ma_bias"]:
        score -= 20
        tags.append("乖离过大↓")

    # --- 最终判定 ---
    if score < CONFIG["score_threshold"]:
        return None

    return {
        "code": code,
        "price": round(curr_price, 2),
        "pct": round(curr_pct, 2),
        "turn": round(curr_turn, 2),
        "vol_ratio": round(vol_ratio, 2),
        "streak": streak,
        "ma5": round(ma5_yest, 3),
        "bias_ma5": round(bias_ma5 * 100, 2),
        "score": score,
        "path": "稳健" if in_stable else "高位",
        "tags": " | ".join(tags),
    }


# ============================================================
#  7. 单池扫描引擎
# ============================================================
def scan_pool(cfg: dict, zt_count: int, mood: str) -> list:
    global CONFIG
    CONFIG = cfg

    time_weight = get_time_weight()
    pool_name = cfg["POOL"]
    mode = cfg["MODE"]

    print(f"\n{'=' * 60}")
    print(f"  扫描池: [{pool_name}]  模式: {mode}  时间权重: {time_weight:.2f}")
    print(f"{'=' * 60}")

    stock_pool = get_stock_pool()

    # post 模式也用腾讯接口获取收盘数据（baostock 历史数据有延迟）
    real_map = get_realtime_quotes(stock_pool)
    print(f"  实时行情获取: {len(real_map)} 只")

    results = []
    total = len(stock_pool)
    end_d = beijing_now().strftime("%Y-%m-%d")
    start_d = (beijing_now() - timedelta(days=45)).strftime("%Y-%m-%d")

    print(f"  开始扫描 {total} 只股票...\n")

    for i, code in enumerate(stock_pool):
        key = code.replace(".", "").lower()
        real_info = real_map.get(key)
        if real_info is None:
            continue
        pct = real_info.get("pct", 0)
        if not (
            cfg["stable_pct_lo"] <= pct <= cfg["stable_pct_hi"]
            or cfg["upper_pct_lo"] <= pct <= cfg["upper_pct_hi"]
        ):
            continue

        k_rs = bs.query_history_k_data_plus(
            code, FIELDS_HIST,
            start_date=start_d, end_date=end_d,
            frequency="d", adjustflag="3",
        )
        if k_rs.error_code != "0":
            continue

        data_list = []
        while k_rs.next():
            data_list.append(k_rs.get_row_data())

        if len(data_list) < 12:
            continue

        hist_df = pd.DataFrame(data_list, columns=k_rs.fields)
        res = analyze_ultimate(hist_df, code, real_info, zt_count, time_weight)
        if res:
            res["pool"] = pool_name
            results.append(res)
            print(f"  🎯 {code:<14} 涨幅:{res['pct']:>6.2f}%  "
                  f"路径:{res['path']}  连板:{res['streak']}  得分:{res['score']}  "
                  f"换手:{res['turn']}%  量比:{res['vol_ratio']}")

        if i % 100 == 0 and i > 0:
            print(f"  进度: {i}/{total}  已命中: {len(results)}")

    return results


# ============================================================
#  7.5 追加结果到汇总文件
# ============================================================
def append_to_summary(
    final_df: pd.DataFrame,
    end_d: str,
    zt_count: int,
    mood: str,
    total_candidates: int,
):
    """
    v1.1: 当日记录覆盖而非跳过 —— 支持盘中→盘后二次验证回写
    逻辑：
      - 如果是当日首次写入：直接 append
      - 如果当日已有记录：先删除当日旧块，再写入新块
      - 通过 MODE 标识区分"盘中初筛"vs"盘后定稿"
    """
    summary_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "选股记录汇总.txt",
    )

    mode_label = "盘后定稿" if CONFIG.get("MODE") == "post" else "盘中初筛"
    date_marker = f"📅 {end_d} "
    existing_content = ""

    try:
        with open(summary_file, "r", encoding="utf-8") as f:
            existing_content = f.read()
    except FileNotFoundError:
        existing_content = ""
    except Exception as e:
        print(f"\n  ⚠️ 读取汇总文件失败: {e}")

    # v1.1: 检测到当日已有记录 → 删除当日所有旧块，等下重新写入
    if date_marker in existing_content:
        # 按"=" * 80 分隔块，删除包含 date_marker 的所有块
        blocks = existing_content.split("=" * 80)
        new_blocks = []
        skip_next = False
        for blk in blocks:
            if skip_next:
                # 这是被跳过块的"内容部分"（紧跟标记块后面）
                skip_next = False
                continue
            if date_marker in blk:
                # 这是当日的标记块，连同其后的内容块一起跳过
                skip_next = True
                continue
            new_blocks.append(blk)
        existing_content = ("=" * 80).join(new_blocks)
        print(f"\n  🔄 检测到当日({end_d})已有记录，覆盖更新（{mode_label}）")

    # 构造新块
    lines = []
    lines.append("")
    lines.append("=" * 80)
    lines.append(f"📅 {end_d}  ({beijing_now().strftime('%H:%M:%S')})  [{mode_label}]")
    lines.append(f"情绪: {mood}({zt_count}家涨停)  扫描总量: {total_candidates}只")
    lines.append("=" * 80)

    for path_label in ["稳健", "高位"]:
        sub = final_df[final_df["path"] == path_label]
        if sub.empty:
            continue

        path_pool = "hs300+zz500" if path_label == "稳健" else "zz1000"
        pos_hint = CONFIG_STABLE["position_ratio"] if path_label == "稳健" else CONFIG_UPPER["position_ratio"]
        lines.append("")
        lines.append(f"── zuiyou1最优版·{path_label}路径 ({len(sub)} 只)  💰 {pos_hint}")
        lines.append(
            f"{'代码':<14} {'池子':<16} {'价格':>7} {'涨幅%':>7} {'量比':>6} "
            f"{'换手%':>7} {'连板':>5} {'乖离%':>7} {'得分':>5}  特征"
        )
        lines.append("-" * 120)

        for _, row in sub.iterrows():
            tags_clean = row["tags"].replace(" | ", "|")
            lines.append(
                f"{row['code']:<14} {row['pool']:<16} {row['price']:>7.2f} "
                f"{row['pct']:>7.2f} {row['vol_ratio']:>6.2f} {row['turn']:>7.2f} "
                f"{row['streak']:>5} {row['bias_ma5']:>7.2f} {row['score']:>5}  {tags_clean}"
            )

    lines.append("")
    lines.append("  💡 操作指引")
    lines.append("  稳健路径：仓位≤15%，次日09:35未维持昨收+1%即出")
    lines.append("  高位路径：仓位≤8%，次日竞价弱于昨收即清仓")
    lines.append("  全局止损：亏损超2.5%当日无条件止损")
    lines.append("")

    # v1.1: 写入策略
    # - 前面已经把当日旧块从 existing_content 中移除（如果有的话）
    # - 此处统一以 w 模式写入：existing_content（不含今日）+ 新块
    try:
        new_lines_str = "\n".join(lines)
        if existing_content.strip():
            new_full_content = existing_content.rstrip() + "\n" + new_lines_str + "\n"
        else:
            new_full_content = new_lines_str + "\n"

        with open(summary_file, "w", encoding="utf-8") as f:
            f.write(new_full_content)
        print(f"\n  ✅ 结果已写入: {summary_file}  [{mode_label}]")
    except Exception as e:
        print(f"\n  ⚠️ 写入汇总文件失败: {e}")


# ============================================================
#  8. 主程序
# ============================================================
def main():
    print("=" * 70)
    print(f"  隔夜选股法·最优融合版 v1.2")
    print(f"  双池策略：稳健[hs300+zz500] + 高位[zz1000]")
    print(f"  完整8步法：涨幅→量比→换手→市值→量能→均线→压力→评分")
    print(f"  运行时间：{beijing_now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    lg = bs.login()
    if lg.error_code != "0":
        print(f"❌ baostock 登录失败: {lg.error_msg}")
        return

    if CONFIG_STABLE["MODE"] == "post" and not is_safe_post_time():
        print("⚠️ 当前不是安全的盘后时间,数据可能不是最终收盘价")
        print("   建议 15:10 之后再运行 post 模式")

    zt_count, mood = fetch_market_sentiment()
    print(f"\n📊 市场情绪: 今日涨停 {zt_count} 家 → [{mood}]")

    if mood == "冷淡":
        print("  ⚠️ 情绪冷淡，仅启用稳健路径(3%-5%)，高位路径自动关闭")
    elif mood in ("活跃", "高潮"):
        print("  🔥 情绪偏热，高位路径(6%-9.7%)已开放，注意风控")
    else:
        print("  📌 情绪正常，双路径运行")

    print(f"  稳健路径仓位: {CONFIG_STABLE['position_ratio']}")
    print(f"  高位路径仓位: {CONFIG_UPPER['position_ratio']}")

    end_d = beijing_now().strftime("%Y-%m-%d")

    results_stable = scan_pool(CONFIG_STABLE, zt_count, mood)

    results_upper = scan_pool(CONFIG_UPPER, zt_count, mood)

    bs.logout()

    all_results = {}
    for r in results_stable + results_upper:
        c = r["code"]
        if c not in all_results or r["score"] > all_results[c]["score"]:
            all_results[c] = r
        elif r["score"] == all_results[c]["score"]:
            if r["pool"] not in all_results[c]["pool"]:
                all_results[c]["pool"] += "+" + r["pool"]

    print("\n" + "=" * 70)
    print(f"  🔥 隔夜选股法·最优精选清单  ({end_d})  情绪: {mood}({zt_count}家涨停)")
    print("=" * 70)

    if not all_results:
        print("\n  今日暂无符合条件的标的。")
        print("  可能原因：")
        print("  1. 市场整体低迷，涨幅3%-5%区间标的不足")
        print("  2. 换手率5%-10%过滤过严（可适当放宽至3%-12%）")
        print("  3. 流通市值50-200亿过滤排除了部分标的")
        print("  4. 量比或均线条件未满足")
        return

    final_df = (
        pd.DataFrame(list(all_results.values()))
        .sort_values(by=["score", "vol_ratio"], ascending=False)
        .reset_index(drop=True)
    )

    total_candidates = len(results_stable) + len(results_upper)

    append_to_summary(final_df, end_d, zt_count, mood, total_candidates)

    for path_label in ["稳健", "高位"]:
        sub = final_df[final_df["path"] == path_label]
        if sub.empty:
            continue

        pos_hint = CONFIG_UPPER["position_ratio"] if path_label == "高位" else CONFIG_STABLE["position_ratio"]
        print(f"\n  ── {path_label}路径 ({len(sub)} 只)  💰 {pos_hint}")
        print(f"  {'代码':<14} {'池子':<16} {'价格':>7} {'涨幅%':>7} {'量比':>6} "
              f"{'换手%':>7} {'连板':>5} {'乖离%':>7} {'得分':>5}  特征")
        print(f"  {'-' * 125}")
        for _, row in sub.iterrows():
            print(
                f"  {row['code']:<14} {row['pool']:<16} {row['price']:>7.2f} "
                f"{row['pct']:>7.2f} {row['vol_ratio']:>6.2f} {row['turn']:>7.2f} "
                f"{row['streak']:>5} {row['bias_ma5']:>7.2f} {row['score']:>5}  {row['tags']}"
            )

    print("\n" + "─" * 70)
    print("  💡 操作指引")
    print("  ─────────────────────────────────────────────────────")
    print("  稳健路径(hs300+zz500)：仓位≤15%，次日09:35未维持昨收+1%即出")
    print("  高位路径(zz1000)    ：仓位≤8%，次日竞价弱于昨收即清仓")
    print("  连板≥3板            ：高度风险，仓位再减半，不超过4%")
    print("  全局止损线          ：任意标的亏损超2.5%当日无条件止损")
    print("  ─────────────────────────────────────────────────────")
    print("  📋 8步法完整度检查：")
    print("  ✅ Step1 涨幅3%-5%   ✅ Step2 量比≥1.5   ✅ Step3 换手5%-10%")
    print("  ✅ Step4 市值50-200亿 ✅ Step5 量能递增   ✅ Step6 均线多头+压力检测")
    print("  ⚠️ Step7 分时均价线上方（需盘中人工确认）")
    print("  ⚠️ Step8 14:30创新高回踩入场（需盘中人工确认）")
    print("─" * 70 + "\n")

    # ============================================================
    #  v1.1: Telegram 推送(如已配置)
    # ============================================================
    if TELEGRAM_ENABLED:
        mode_label = "盘后定稿" if CONFIG.get("MODE") == "post" else "盘中初筛"
        title = f"zuiyou1 v1.2 {mode_label}"
        mood_info = f"情绪: {mood} ({zt_count}家涨停)"

        stable_picks = []
        upper_picks = []
        for _, row in final_df.iterrows():
            pick = {
                "code": row["code"],
                "price": row["price"],
                "pct": row["pct"],
                "score": row["score"],
                "tags": row["tags"],
            }
            if row["path"] == "稳健":
                stable_picks.append(pick)
            else:
                upper_picks.append(pick)

        operation_note = (
            "稳健: 次日09:35未维持昨收+1%即出\n"
            "高位: 次日竞价弱于昨收即清仓\n"
            "全局止损: 亏损超-2.5%无条件清仓"
        )

        try:
            ok = send_stock_picks(title, beijing_now().strftime("%Y-%m-%d %H:%M:%S"), mood_info, stable_picks, upper_picks, operation_note)
            if ok:
                print("  ✅ 已推送到 Telegram\n")
            else:
                print("  ⚠️ Telegram 推送失败,请检查 token/chat_id\n")
        except Exception as e:
            print(f"  ⚠️ Telegram 推送异常: {e}\n")


if __name__ == "__main__":
    main()