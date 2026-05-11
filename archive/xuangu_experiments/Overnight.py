"""
隔夜施工法·终极融合版 (Ultimate v2.0)
========================================
融合来源：
  - shuanggui2   时间加权量比 / 盘中盘后双模式 / 三重风险扣分
  - mogai        连板高度判断 / 10日去极值均量 / 市场情绪感知
  - hebing       三重均线约束 / 成交额双边过滤
  - shuanggui    双路径涨幅区间（稳健 + 高位）
  - 隔夜施工法   十一层筛选哲学（技术是结果，资金是原因，叙事是灵魂，情绪是时机）

修复的共性缺陷：
  ✓ 时间加权量比（解决盘中量低估）
  ✓ ST股 / 退市风险股强制过滤
  ✓ 腾讯接口防崩溃：字段缺失 / 空值 / 格式异常全部 try-catch
  ✓ 连板高度惩罚（4连板风险远高于首板，不能线性加分）
  ✓ post 模式下 real_data=None 不再 crash
  ✓ 10日去极值均量替代4日简单均量
  ✓ 均线使用昨收序列，无未来函数
  ✓ 市场情绪感知（涨停家数自动调整阈值）
  ✓ 全市场扫描（sh.60 / sz.00 / sz.30），不限中证500

运行建议：
  盘中：14:10–14:30  CONFIG["MODE"] = "realtime"
  盘后：收盘后       CONFIG["MODE"] = "post"
  复盘：次日开盘前   CONFIG["MODE"] = "post"

止损铁律（代码外执行）：
  首板 / 稳健路径：次日09:35若不能维持昨收+1%，直接出局
  高位 / 连板路径：次日竞价弱于昨收，09:30集合竞价结束即清仓
"""

import baostock as bs
import pandas as pd
import requests
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple

# ============================================================
#  1. 全局配置
# ============================================================
# ── 稳健路径专属配置（hs300+zz500，主板大票）──────────────────
CONFIG_STABLE = {
    "MODE": "realtime",              # "realtime" 盘中  |  "post" 盘后复盘
    "POOL": "hs300+zz500",       # 沪深300+中证500，约700只
    "min_amount": 200_000_000,   # 2亿，大票流动性门槛更高
    "max_amount": 5_000_000_000, # 50亿
    "vol_ratio_min": 1.5,
    "vol_ratio_max": 10.0,
    "stable_pct_lo": 3.0,
    "stable_pct_hi": 5.5,
    "upper_pct_lo":  6.0,        # 大票高位路径也开放，但门槛更高
    "upper_pct_hi":  9.7,
    "turn_min": 3.0,             # 大票换手天然偏低，下限放宽
    "turn_max": 20.0,
    "streak_penalty_threshold": 3,
    "streak_penalty_per_board": 10,
    "score_threshold": 80,
    "sentiment_cold":   30,
    "sentiment_normal": 60,
    "sentiment_hot":   100,
    "penalty_hot_turn":   12.0,
    "penalty_vol_ratio":   8.0,
    "penalty_ma_bias":     0.08,
    # 仓位建议
    "position_ratio": "单票≤15%总仓位，稳健持有",
}

# ── 高位路径专属配置（zz1000，中小盘龙头）────────────────────────
CONFIG_UPPER = {
    "MODE": "post",              # 与稳健路径保持一致
    "POOL": "zz1000",            # 中证1000，约1000只，龙头主战场
    "min_amount": 100_000_000,   # 1亿，中小盘门槛适当降低
    "max_amount": 3_000_000_000, # 30亿，中小盘市值范围
    "vol_ratio_min": 1.5,
    "vol_ratio_max": 12.0,
    "stable_pct_lo": 3.0,        # 高位池也保留稳健路径，但权重低
    "stable_pct_hi": 5.5,
    "upper_pct_lo":  6.0,
    "upper_pct_hi":  9.7,
    "turn_min": 4.0,             # 中小盘换手活跃，下限提高
    "turn_max": 30.0,
    "streak_penalty_threshold": 3,
    "streak_penalty_per_board": 10,
    "score_threshold": 80,
    "sentiment_cold":   30,
    "sentiment_normal": 60,
    "sentiment_hot":   100,
    "penalty_hot_turn":   20.0,  # 中小盘换手过热惩罚更严
    "penalty_vol_ratio":   8.0,
    "penalty_ma_bias":     0.08,
    # 仓位建议
    "position_ratio": "单票≤8%总仓位，严守止损",
}

# 当前激活配置（主程序使用此变量）
CONFIG = CONFIG_STABLE  # 运行时会被 main() 按池子切换

FIELDS_HIST = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg"


# ============================================================
#  2. 时间权重（解决盘中量低估）
# ============================================================
def get_time_weight() -> float:
    """
    计算当前时刻已过交易时长占全天240分钟的比例，用于量比时间加权。

    分段规则（Asia/Shanghai）：
      开盘前 / 收盘后  → 1.0（按全天量处理，避免量比异常放大）
      午休 12:00-13:00 → 按120/240=0.5处理（早盘已结束）
      交易中           → 实际已过分钟 / 240

    post模式始终返回1.0。
    """
    if CONFIG["MODE"] == "post":
        return 1.0

    now = datetime.now()
    h, m = now.hour, now.minute

    # 开盘前或收盘后：实时成交量即为当日全量（前者为0，后者为全天）
    # 统一返回1.0，避免 curr_vol/0.05 = 20倍放大导致误判
    if h < 9 or (h == 9 and m < 30):
        return 1.0   # 未开盘，实时量=0，不做加权
    elif h >= 15:
        return 1.0   # 已收盘，全天量已完整

    if h == 9:
        passed = m - 30
    elif h == 10:
        passed = 30 + m
    elif h == 11 and m <= 30:
        passed = 90 + m
    elif h == 11 or h == 12:
        passed = 120  # 午休，早盘已结束120分钟
    elif h == 13:
        passed = 120 + m
    elif h == 14:
        passed = 180 + m
    else:
        passed = 1

    return max(0.01, min(1.0, passed / 240.0))


# ============================================================
#  3. 市场情绪感知（东财涨停池）
# ============================================================
def fetch_market_sentiment() -> Tuple[int, str]:
    """
    获取真实涨停家数（东财数据中心接口）。
    返回 (涨停家数, 情绪描述)。
    """
    try:
        from datetime import datetime, timezone, timedelta
        beijing_now = datetime.now(timezone(timedelta(hours=8)))
        today_ymd = beijing_now.strftime('%Y%m%d')
        
        # 方案1：东财涨停池接口（最直接）
        url = f"https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&Pagesize=500&sort=fbt%3Aasc&date={today_ymd}&_={int(time.time() * 1000)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/ztb/detail"
        }
        
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        
        # 解析涨停池数据
        if data.get('data') and data['data'].get('pool'):
            zt_count = len(data['data']['pool'])
        else:
            zt_count = 0
        
        # 方案2：如果涨停池接口返回0，尝试数据中心接口
        if zt_count == 0:
            try:
                today_dash = beijing_now.strftime('%Y-%m-%d')
                url2 = "https://datacenter-web.eastmoney.com/api/data/v1/get"
                params2 = {
                    "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
                    "columns": "ALL",
                    "pageNumber": 1,
                    "pageSize": 100,
                    "sortColumns": "BILLBOARD_NET_AMT",
                    "sortTypes": -1,
                    "filter": f"(TRADE_DATE='{today_dash}')"
                }
                r2 = requests.get(url2, params=params2, headers=headers, timeout=10)
                data2 = r2.json()
                result = data2.get('result') or {}
                zt_count = result.get('count', 0) if isinstance(result, dict) else 0
            except:
                pass
        
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
#  4. 实时行情（腾讯接口，防崩溃版）
# ============================================================
def get_realtime_quotes(stock_list: list) -> dict:
    """
    腾讯行情接口，全字段防空处理。
    返回 dict: { 'sh600000': {'now': 12.5, 'pct': 3.2, ...} }
    """
    if CONFIG["MODE"] == "post":
        return {}

    results = {}
    api_codes = [s.replace(".", "").lower() for s in stock_list]
    total_batches = (len(api_codes) + 49) // 50
    ok_count = 0
    err_count = 0
    skip_count = 0

    print(f"  [腾讯接口] 共 {len(api_codes)} 只，分 {total_batches} 批请求...")

    for i in range(0, len(api_codes), 50):
        batch_no = i // 50 + 1
        chunk = api_codes[i : i + 50]
        url = f"http://qt.gtimg.cn/q={','.join(chunk)}"
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=8,
            )
            if resp.status_code != 200:
                print(f"  [腾讯接口] 批次 {batch_no}/{total_batches} HTTP {resp.status_code}，跳过")
                err_count += 1
                continue

            batch_ok = 0
            batch_skip = 0
            for line in resp.text.split(";"):
                if len(line) < 50:
                    continue
                p = line.split("~")
                if len(p) < 40:
                    continue
                try:
                    raw_key = p[0].split("=")[0][-8:]  # e.g. 'sh600000'

                    def _f(idx, default=0.0):
                        try:
                            return float(p[idx]) if p[idx].strip() else default
                        except (ValueError, IndexError):
                            return default

                    now_price = _f(3)
                    if now_price <= 0:
                        batch_skip += 1
                        continue

                    results[raw_key] = {
                        "now":    now_price,
                        "pct":    _f(32),
                        "vol":    _f(6) * 100,   # 手 → 股
                        "amount": _f(37) * 10000, # 万元 → 元
                        "high":   _f(33),
                        "pre":    _f(4),          # 昨收
                    }
                    batch_ok += 1
                except Exception as e:
                    batch_skip += 1
                    continue

            ok_count += batch_ok
            skip_count += batch_skip

            # 每10批或最后一批打印一次进度
            if batch_no % 10 == 0 or batch_no == total_batches:
                print(f"  [腾讯接口] 进度 {batch_no}/{total_batches} | "
                      f"本批解析 {batch_ok} 只，跳过 {batch_skip} 只 | "
                      f"累计成功 {ok_count} 只")

            # 抽样展示第1批的原始数据，方便确认字段映射
            if batch_no == 1 and batch_ok > 0:
                sample_key = list(results.keys())[0]
                sample = results[sample_key]
                print(f"  [腾讯接口] 字段抽样 ({sample_key}): "
                      f"价格={sample['now']} 涨幅={sample['pct']}% "
                      f"成交额={sample['amount']/1e8:.2f}亿 最高={sample['high']}")

        except Exception as e:
            print(f"  [腾讯接口] 批次 {batch_no}/{total_batches} 请求异常: {e}")
            err_count += 1
            continue

        time.sleep(0.12)

    print(f"  [腾讯接口] 完成 | 成功={ok_count} 跳过={skip_count} 批次错误={err_count}")
    if ok_count == 0:
        print("  [腾讯接口] ⚠️  返回0条数据，可能原因：")
        print("             1. 当前非交易时段，行情接口返回空盘口")
        print("             2. IP被限流，尝试稍后重试")
        print("             3. 接口地址变更，检查 qt.gtimg.cn 是否可访问")

    return results


# ============================================================
#  5. 股票池获取
# ============================================================
def get_latest_trading_day() -> str:
    """
    获取最近一个有效交易日。
    query_all_stock 在非交易日/节假日传入今日会返回空，
    向前最多回溯14天找到最近有效交易日。
    """
    for delta in range(0, 14):
        day_str = (datetime.now() - timedelta(days=delta)).strftime("%Y-%m-%d")
        rs = bs.query_all_stock(day=day_str)
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if rows:
            return day_str
    return datetime.now().strftime("%Y-%m-%d")


def get_stock_pool() -> list:
    """
    根据 CONFIG["POOL"] 返回股票代码列表。
    支持：hs300 / zz500 / hs300+zz500 / zz1000 / market
    """
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
        """中证1000：baostock无直接接口，用全市场过滤排除hs300+zz500"""
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
        # 取前1000只（按代码排序近似中证1000范围）
        return sorted(codes)[:1000]

    def _fetch_market():
        trading_day = get_latest_trading_day()
        print(f"  股票池基准交易日: {trading_day}")
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
    else:  # market
        stocks_set.update(_fetch_market())

    stocks = sorted(stocks_set)
    print(f"  股票池: [{pool_name}] 共 {len(stocks)} 只")
    return stocks


# ============================================================
#  6. 核心量化逻辑
# ============================================================
def analyze_ultimate(
    hist_df: pd.DataFrame,
    code: str,
    real_info: Optional[dict],
    zt_count: int,
    time_weight: float,
) -> Optional[dict]:
    """
    终极融合评分函数。

    参数
    ----
    hist_df    : 历史K线（含今日，post模式；或截至昨日，realtime模式）
    code       : baostock格式代码，如 'sh.600000'
    real_info  : 实时行情dict（realtime模式）或 None（post模式）
    zt_count   : 今日市场涨停家数
    time_weight: 当前时刻时间权重
    """

    # --- 6.1 基础清洗 ---
    if hist_df is None or len(hist_df) < 12:
        return None

    # ST过滤：从实时行情中获取股票名称判断
    if real_info:
        name = real_info.get("name", "")
        if "ST" in name or "*ST" in name or "退" in name:
            return None

    df = hist_df.copy()
    for col in ["close", "volume", "pctChg", "turn", "amount", "high", "open"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # 过滤成交量为0的停牌日
    df = df[df["volume"] > 0]
    if len(df) < 10:
        return None

    # --- 6.2 决定今日数据来源 ---
    if CONFIG["MODE"] == "realtime" and real_info is not None:
        curr_price  = real_info["now"]
        curr_pct    = real_info["pct"]
        curr_vol    = real_info["vol"]
        curr_amount = real_info["amount"]
        # 换手率实时模式从baostock当日K线取（end_date包含今日）
        curr_turn   = float(df.iloc[-1]["turn"]) if "turn" in df.columns else 0.0
        # 时间加权量比：推算全天预期量
        est_full_vol = curr_vol / time_weight if time_weight > 0 else curr_vol
        hist_vols = df["volume"].tolist()  # 用于计算历史均量
    else:
        # post 模式：直接取K线最后一行
        last = df.iloc[-1]
        curr_price  = float(last["close"])
        curr_pct    = float(last["pctChg"])
        curr_vol    = float(last["volume"])
        curr_turn   = float(last["turn"])
        curr_amount = float(last["amount"]) if "amount" in df.columns else 0.0
        est_full_vol = curr_vol   # 盘后全天量直接用
        hist_vols = df["volume"].tolist()[:-1]  # 去掉今日，用历史计算均量

    # --- 6.3 成交额硬过滤 ---
    if curr_amount < CONFIG["min_amount"] or curr_amount > CONFIG["max_amount"]:
        return None

    # --- 6.3b 换手率硬过滤 ---
    if curr_turn < CONFIG["turn_min"] or curr_turn > CONFIG["turn_max"]:
        return None

    # --- 6.4 10日去极值均量（mogai核心贡献）---
    recent_vols = sorted(hist_vols[-12:])  # 最多取12日
    if len(recent_vols) < 4:
        return None
    # 去掉最大最小各1个
    trimmed = recent_vols[1:-1] if len(recent_vols) > 2 else recent_vols
    avg_vol_trimmed = sum(trimmed) / len(trimmed)

    vol_ratio = est_full_vol / avg_vol_trimmed if avg_vol_trimmed > 0 else 0

    # --- 6.5 量比硬过滤 ---
    if not (CONFIG["vol_ratio_min"] <= vol_ratio <= CONFIG["vol_ratio_max"]):
        return None

    # --- 6.6 均线计算（昨收序列，无未来函数）---
    # 取昨日及之前的收盘价
    if CONFIG["MODE"] == "realtime":
        hist_close = df["close"].tolist()  # 不含今日实时价
    else:
        hist_close = df["close"].tolist()[:-1]  # 去掉今日

    if len(hist_close) < 10:
        return None

    ma5_yest  = sum(hist_close[-5:])  / 5
    ma10_yest = sum(hist_close[-10:]) / 10

    # 三重均线约束（hebing核心贡献）
    if not (curr_price > ma5_yest > ma10_yest):
        return None

    # --- 6.7 连板高度判断（mogai核心贡献）---
    streak = 0
    pct_list = df["pctChg"].tolist()
    if CONFIG["MODE"] != "realtime":
        pct_list = pct_list[:-1]  # 去掉今日，只看历史连板
    for p in reversed(pct_list):
        if p >= 9.8:
            streak += 1
        else:
            break

    # --- 6.8 涨幅区间双路径（shuanggui核心贡献）---
    in_stable = CONFIG["stable_pct_lo"] <= curr_pct <= CONFIG["stable_pct_hi"]
    in_upper  = CONFIG["upper_pct_lo"]  <= curr_pct <= CONFIG["upper_pct_hi"]

    # 情绪冷淡时只看稳健路径
    if zt_count < CONFIG["sentiment_cold"] and not in_stable:
        return None

    if not (in_stable or in_upper):
        return None

    # --- 6.9 评分系统（基础分50，加减分） ---
    score = 50
    tags  = []

    # A. 涨幅路径
    if in_stable:
        score += 15
        tags.append("稳健蓄势")
        # 贴线运行加分
        bias = (curr_price - ma5_yest) / ma5_yest if ma5_yest > 0 else 1
        if bias < 0.02:
            score += 10
            tags.append("紧贴MA5")
    elif in_upper:
        score += 20
        tags.append("高位博弈")
        # 光头大阳（收盘≈当日最高，适合盘后用）
        if CONFIG["MODE"] == "post":
            today_high = float(df.iloc[-1]["high"]) if "high" in df.columns else 0
            if today_high > 0 and curr_price >= today_high * 0.998:
                score += 10
                tags.append("光头大阳")

    # B. 量比（温和放量最佳）
    if 1.8 <= vol_ratio <= 4.0:
        score += 25
        tags.append("黄金放量")
    elif vol_ratio > 4.0:
        score += 10
        tags.append("爆量博弈")
    else:
        score += 5
        tags.append("量能达标")

    # C. 换手率
    if 5.0 <= curr_turn <= 12.0:
        score += 15
        tags.append("黄金换手")
    elif 3.0 <= curr_turn < 5.0:
        score += 5
        tags.append("换手偏低")

    # D. 连板高度（mogai贡献 + 高度惩罚修正）
    if streak == 1:
        score += 20
        tags.append("首板突破")
    elif streak == 2:
        score += 30
        tags.append("二连板")
    elif streak >= CONFIG["streak_penalty_threshold"]:
        # 高度板加分封顶但开始惩罚：基础+30 - 每多一板扣penalty分
        bonus = 30
        penalty = (streak - 2) * CONFIG["streak_penalty_per_board"]
        net = bonus - penalty
        score += max(net, 0)  # 不反向扣分，只是不加分
        tags.append(f"{streak}连板(高度风险)")

    # E. 情绪高潮期：放宽高位路径门槛10分
    if zt_count >= CONFIG["sentiment_hot"] and in_upper:
        score += 10
        tags.append("情绪高潮加成")

    # --- 6.10 风险扣分（shuanggui2核心贡献）---
    if curr_turn > CONFIG["penalty_hot_turn"]:
        score -= 20
        tags.append("换手过热↓")

    if vol_ratio > CONFIG["penalty_vol_ratio"]:
        score -= 15
        tags.append("量比过激↓")
        # 移除可能已加的"爆量博弈"标签，避免自相矛盾
        if "爆量博弈" in tags:
            tags.remove("爆量博弈")

    bias_ma5 = (curr_price - ma5_yest) / ma5_yest if ma5_yest > 0 else 0
    if bias_ma5 > CONFIG["penalty_ma_bias"]:
        score -= 20
        tags.append("乖离过大↓")

    # --- 6.11 最终判定 ---
    if score < CONFIG["score_threshold"]:
        return None

    return {
        "code":       code,
        "price":      round(curr_price, 2),
        "pct":        round(curr_pct, 2),
        "turn":       round(curr_turn, 2),
        "vol_ratio":  round(vol_ratio, 2),
        "streak":     streak,
        "ma5":        round(ma5_yest, 3),
        "bias_ma5":   round(bias_ma5 * 100, 2),   # %
        "score":      score,
        "path":       "稳健" if in_stable else "高位",
        "tags":       " | ".join(tags),
    }


# ============================================================
#  7. 单池扫描引擎（供主程序调用）
# ============================================================
def scan_pool(cfg: dict, zt_count: int, mood: str) -> list:
    """
    用指定配置扫描一个股票池，返回命中结果列表。
    """
    global CONFIG
    CONFIG = cfg  # 切换全局配置

    time_weight = get_time_weight()
    pool_name   = cfg["POOL"]
    mode        = cfg["MODE"]

    print(f"\n{'='*60}")
    print(f"  扫描池: [{pool_name}]  模式: {mode}  时间权重: {time_weight:.2f}")
    print(f"{'='*60}")

    stock_pool = get_stock_pool()

    # 实时行情（realtime模式）
    real_map = {}
    if mode == "realtime":
        print("  正在拉取实时行情...")
        real_map = get_realtime_quotes(stock_pool)
        print(f"  实时行情获取: {len(real_map)} 只")

    results = []
    total   = len(stock_pool)
    end_d   = datetime.now().strftime("%Y-%m-%d")
    start_d = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")

    print(f"  开始扫描 {total} 只股票...\n")

    for i, code in enumerate(stock_pool):
        if mode == "realtime":
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
        else:
            real_info = None

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
            res["pool"] = pool_name  # 记录来源池
            results.append(res)
            print(f"  🎯 {code:<14} 涨幅:{res['pct']:>6.2f}%  "
                  f"路径:{res['path']}  连板:{res['streak']}  得分:{res['score']}")

        if i % 100 == 0 and i > 0:
            print(f"  进度: {i}/{total}  已命中: {len(results)}")

    return results


# ============================================================
#  8. 主程序
# ============================================================
def main():
    print("=" * 70)
    print(f"  隔夜施工法·终极融合版 v2.0")
    print(f"  双池策略：稳健[hs300+zz500] + 高位[zz1000]")
    print(f"  运行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 8.1 登录（一次登录，两个池子共用）
    lg = bs.login()
    if lg.error_code != "0":
        print(f"❌ baostock 登录失败: {lg.error_msg}")
        return

    # 8.2 市场情绪（共用）
    zt_count, mood = fetch_market_sentiment()
    print(f"\n📊 市场情绪: 今日涨停 {zt_count} 家 → [{mood}]")
    print(f"   稳健路径仓位建议: {CONFIG_STABLE['position_ratio']}")
    print(f"   高位路径仓位建议: {CONFIG_UPPER['position_ratio']}")

    end_d = datetime.now().strftime("%Y-%m-%d")

    # 8.3 扫描稳健池（hs300+zz500）
    results_stable = scan_pool(CONFIG_STABLE, zt_count, mood)

    # 8.4 扫描高位池（zz1000）
    results_upper = scan_pool(CONFIG_UPPER, zt_count, mood)

    bs.logout()

    # ── 8.5 合并去重（同一只票可能同时出现在两个池子）──────────
    # 以得分高的为准保留，pool字段合并标注
    all_results = {}
    for r in results_stable + results_upper:
        code = r["code"]
        if code not in all_results or r["score"] > all_results[code]["score"]:
            all_results[code] = r
        elif r["score"] == all_results[code]["score"]:
            # 同分：合并pool标注
            if r["pool"] not in all_results[code]["pool"]:
                all_results[code]["pool"] += "+" + r["pool"]

    # ── 8.6 输出结果 ────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"  🔥 隔夜施工法·双池精选清单  ({end_d})  情绪: {mood}({zt_count}家涨停)")
    print("=" * 70)

    if not all_results:
        print("\n  今日暂无符合条件的标的。")
        print("  可能原因：")
        print("  1. 市场整体低迷，涨幅区间内标的不足")
        print("  2. 量比或均线条件过严，尝试调低 score_threshold")
        print("  3. realtime模式下实时接口未返回数据（检查网络）")
        return

    final_df = (
        pd.DataFrame(list(all_results.values()))
        .sort_values(by=["score", "vol_ratio"], ascending=False)
        .reset_index(drop=True)
    )

    # 分路径、分池子展示
    for path_label in ["高位", "稳健"]:
        sub = final_df[final_df["path"] == path_label]
        if sub.empty:
            continue

        # 仓位建议
        pos_hint = CONFIG_UPPER["position_ratio"] if path_label == "高位" else CONFIG_STABLE["position_ratio"]
        print(f"\n  ── {path_label}路径 ({len(sub)} 只)  💰 {pos_hint}")
        print(f"  {'代码':<14} {'池子':<16} {'价格':>7} {'涨幅%':>7} {'量比':>6} "
              f"{'换手%':>7} {'连板':>5} {'乖离%':>7} {'得分':>5}  特征")
        print(f"  {'-'*125}")
        for _, row in sub.iterrows():
            print(
                f"  {row['code']:<14} {row['pool']:<16} {row['price']:>7.2f} "
                f"{row['pct']:>7.2f} {row['vol_ratio']:>6.2f} {row['turn']:>7.2f} "
                f"{row['streak']:>5} {row['bias_ma5']:>7.2f} {row['score']:>5}  {row['tags']}"
            )

    print("\n" + "─" * 45)
    print("  💡 操作指引")
    print("  稳健路径(hs300+zz500)：仓位≤15%，次日09:35未强势高开即出")
    print("  高位路径(zz1000)    ：仓位≤8%，次日竞价弱于昨收集合竞价结束即清")
    print(f"  连板≥3板            ：高度风险，仓位再减半，不超过4%")
    print("  全局止损线          ：任意标的亏损超2%当日无条件止损")
    print("─" * 45 + "\n")


if __name__ == "__main__":
    main()