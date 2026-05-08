"""
隔夜选股法·最优融合版 (zuiyou1 v1.4)
========================================
v1.4 修订（2026-05-07）：
  ✓ 细粒度排序因子 —— 大单/距涨停/MA5区分同分标的
  ✓ 乖离动态阈值 —— 高位路径0.12，高潮期+0.03
  ✓ 情绪逆向思维 —— 高潮扣分10分，推荐数压缩到3只
  ✓ 推荐数限制 —— >=100涨停限3只，>=80涨停限4只

v1.3 修订（2026-05-07）：
  ✓ 尾盘回落检测 —— post模式从最高回撤>3%扣15分
  ✓ 冷门行业过滤 —— 银行/保险/煤炭/钢铁扣20分（证监会分类子串匹配）
  ✓ 行业缓存预热 —— 启动时批量查询，文件缓存7天
  ✓ 过滤统计 —— 每次扫描输出各环节淘汰分布，无标的时显示TOP3瓶颈
  ✓ tqdm进度条 —— 自动检测，兼容打印不破坏进度
  ✓ 成交量递增修复 —— 使用hist_vols+预估量替代baostock延迟数据
  ✓ 连板高度修复 —— realtime模式用curr_pct判断今日涨停
  ✓ 双池信号保留 —— 同一股票命中双池时标记"stable+upper"
  ✓ DEBUG日志开关 —— ZUIYOU_DEBUG环境变量控制

v1.2 修订（2026-04-30）：
  ✓ 市值过滤按池子分档 —— 稳健池100-2000亿，高位池30-300亿
  ✓ 涨停阈值按板块动态判断 —— 主板10%/创业板20%/科创板20%/北交所30%
  ✓ 压力检测按路径分档 —— 稳健8%，高位15%
  ✓ 盘后时间安全检查 —— 15:10前运行post模式会警告

v1.1 修订（2026-04-29）：
  ✓ 市值过滤不再受 MODE 限制 —— post 模式同样启用
  ✓ MA 计算统一不含今日 —— 严格无未来函数（修复 realtime 模式的潜在偏差）
  ✓ 当日记录覆盖而非跳过 —— 支持盘中→盘后二次验证回写

v1.0 改进：
  ✓ post 模式也使用腾讯实时接口（修复 baostock 数据延迟导致的假信号）
  ✓ 成交量递增验证
  ✓ K线上方压力检测
  ✓ 换手率硬过滤
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
import json
from datetime import datetime, timedelta
from typing import Tuple, Optional, List

# tqdm 自动检测
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    tqdm = None

# 调试模式开关（环境变量 ZUIYOU_DEBUG=true 时启用）
DEBUG = os.environ.get("ZUIYOU_DEBUG", "").lower() == "true"

# 冷门行业关键词（匹配证监会行业分类的子串）
COLD_INDUSTRIES_KEYWORDS = ["银行", "保险", "煤炭", "钢铁", "黑色金属"]

def is_cold_industry(industry: str) -> bool:
    """判断是否为冷门行业（隔夜溢价较低）"""
    if not industry:
        return False
    return any(kw in industry for kw in COLD_INDUSTRIES_KEYWORDS)

# 行业缓存（内存+文件双缓存，7天更新一次）
_industry_cache = {}
_INDUSTRY_CACHE_FILE = os.path.join(os.path.dirname(__file__), "industry_cache.json")

def preload_industries(stock_pool: list):
    """启动时一次性查询所有股票行业，缓存到本地文件，7天更新一次"""
    if os.path.exists(_INDUSTRY_CACHE_FILE):
        mtime = os.path.getmtime(_INDUSTRY_CACHE_FILE)
        if time.time() - mtime < 7 * 86400:
            try:
                with open(_INDUSTRY_CACHE_FILE, "r", encoding="utf-8") as f:
                    _industry_cache.update(json.load(f))
                return
            except Exception:
                pass

    print("  预热行业缓存（首次或过期）...")
    for code in stock_pool:
        get_stock_industry(code)

    try:
        with open(_INDUSTRY_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_industry_cache, f, ensure_ascii=False)
    except Exception:
        pass

def get_stock_industry(code: str) -> str:
    """获取股票所属行业，带内存+文件双缓存"""
    if code in _industry_cache:
        return _industry_cache[code]
    try:
        rs = bs.query_stock_industry(code=code)
        if rs.error_code == '0':
            row = rs.get_row_data()
            if row and len(row) > 0:
                industry = row[0] if row[0] else ""
                _industry_cache[code] = industry
                return industry
    except Exception:
        pass
    _industry_cache[code] = ""
    return ""

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
    "MODE": "post",
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

# 北京时间工具函数（自动检测服务器时区并转换为北京时间）
def beijing_now():
    """返回北京时间，自动处理不同服务器时区"""
    import time
    from datetime import datetime, timezone, timedelta
    
    # 获取当前UTC时间戳（这是全球统一的，不受服务器时区影响）
    utc_timestamp = time.time()
    
    # 将UTC时间戳转换为UTC datetime对象
    utc_dt = datetime.fromtimestamp(utc_timestamp, tz=timezone.utc)
    
    # 转换为北京时间 (UTC+8)
    beijing_tz = timezone(timedelta(hours=8))
    return utc_dt.astimezone(beijing_tz)

# 自动判断 MODE：15:10 后为 post，其余为 realtime
_now = beijing_now()
if DEBUG:
    print(f"  [DEBUG] 服务器时间: {datetime.now()}, 北京时间: {_now.strftime('%Y-%m-%d %H:%M:%S')}")
if _now.hour > 15 or (_now.hour == 15 and _now.minute >= 10):
    CONFIG["MODE"] = "post"
else:
    CONFIG["MODE"] = "realtime"
if DEBUG:
    print(f"  [DEBUG] MODE: {CONFIG['MODE']}")

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
    获取真实涨停家数（东财涨停池接口）。
    返回 (涨停家数, 情绪描述)。
    """
    try:
        # 使用统一的北京时间函数
        today_ymd = beijing_now().strftime('%Y%m%d')
        
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
            pool = data['data']['pool']
            zt_count = len(pool)
            # 调试：打印前3只涨停股票验证数据真实性
            if zt_count > 0:
                sample = pool[:3]
                sample_names = [s.get('n', '') for s in sample]
                print(f"  [DEBUG] 涨停池验证: 共{zt_count}家, 示例: {', '.join(sample_names)}")
        else:
            zt_count = 0
        
        # 方案2：如果涨停池接口返回0，尝试数据中心接口
        if zt_count == 0:
            try:
                today_dash = beijing_now().strftime('%Y-%m-%d')
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
        
        # 如果两个接口都返回0，可能是非交易时间
        if zt_count == 0:
            return 50, "正常"
            
    except Exception as e:
        print(f"  ⚠️ 情绪接口异常: {e}")
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
    reject_stats: Optional[dict] = None,
    mood: str = "",
) -> Optional[dict]:

    if hist_df is None or len(hist_df) < 15:
        if reject_stats is not None:
            reject_stats["数据不足"] += 1
        return None

    # ST过滤：从实时行情中获取股票名称判断
    if real_info:
        name = real_info.get("name", "")
        if "ST" in name or "*ST" in name or "退" in name:
            if reject_stats is not None:
                reject_stats["ST/退市"] += 1
            return None

    df = hist_df.copy()
    for col in ["close", "volume", "pctChg", "turn", "amount", "high", "low", "open", "preclose"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df = df[df["volume"] > 0]
    if len(df) < 12:
        if reject_stats is not None:
            reject_stats["数据不足"] += 1
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
        if reject_stats is not None:
            reject_stats["情绪冷淡"] += 1
        return None

    if not (in_stable or in_upper):
        if reject_stats is not None:
            reject_stats["涨幅不符"] += 1
        return None

    # --- STEP 2: 成交额硬过滤 ---
    if curr_amount < CONFIG["min_amount"] or curr_amount > CONFIG["max_amount"]:
        if reject_stats is not None:
            reject_stats["成交额"] += 1
        return None

    # --- STEP 3: 换手率硬过滤（8步法原始5%-10%）---
    if curr_turn < CONFIG["turn_min"] or curr_turn > CONFIG["turn_max"]:
        if reject_stats is not None:
            reject_stats["换手率"] += 1
        return None

    # --- STEP 4: 流通市值过滤（8步法50亿-200亿）---
    # v1.1: 去掉 MODE 限制，post 模式也启用市值过滤
    mktcap = real_info.get("mktcap", 0) if real_info else 0
    if mktcap > 0:
        if mktcap < CONFIG["min_mktcap"] or mktcap > CONFIG["max_mktcap"]:
            if reject_stats is not None:
                reject_stats["市值"] += 1
            return None

    # --- STEP 5: 量比计算（10日去极值均量 + 时间加权）---
    recent_vols = sorted(hist_vols[-12:])
    if len(recent_vols) < 4:
        if reject_stats is not None:
            reject_stats["量比"] += 1
        return None
    trimmed = recent_vols[1:-1] if len(recent_vols) > 2 else recent_vols
    avg_vol_trimmed = sum(trimmed) / len(trimmed)

    vol_ratio = est_full_vol / avg_vol_trimmed if avg_vol_trimmed > 0 else 0
    if not (CONFIG["vol_ratio_min"] <= vol_ratio <= CONFIG["vol_ratio_max"]):
        if reject_stats is not None:
            reject_stats["量比"] += 1
        return None

    # --- STEP 6a: 均线验证（昨收序列，严格无未来函数）---
    # v1.1: 统一不含今日收盘 —— 无论 realtime 还是 post
    # 原因：realtime 模式下 baostock 当日"close"是延迟数据，会污染均线
    #       post 模式下今日已收盘，但 MA 应基于"昨日及之前"的历史序列
    hist_close = df["close"].tolist()[:-1]

    if len(hist_close) < 10:
        if reject_stats is not None:
            reject_stats["均线"] += 1
        return None

    ma5_yest = sum(hist_close[-5:]) / 5
    ma10_yest = sum(hist_close[-10:]) / 10
    ma20_yest = sum(hist_close[-20:]) / 20 if len(hist_close) >= 20 else ma10_yest

    if not (curr_price > ma5_yest > ma10_yest):
        if reject_stats is not None:
            reject_stats["均线"] += 1
        return None

    # --- STEP 6b: K线上方压力检测 ---
    recent_highs = df["high"].tail(20).tolist()
    recent_highs = [float(x) for x in recent_highs if float(x) > 0]
    if recent_highs:
        max_recent_high = max(recent_highs)
        resistance_ratio = (max_recent_high - curr_price) / curr_price
        resistance_threshold = 0.15 if in_upper else 0.08
        if resistance_ratio > resistance_threshold:
            if reject_stats is not None:
                reject_stats["压力"] += 1
            return None

    # --- STEP 5b: 成交量递增验证 ---
    # 使用 hist_vols（已处理过的历史序列，不含今日baostock延迟数据）+ 今日预估量
    recent_5d_vols = hist_vols[-4:] + [est_full_vol] if len(hist_vols) >= 4 else hist_vols[-2:] + [est_full_vol]
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
    # 永远不含今日 baostock 数据（可能有延迟）
    pct_list = df["pctChg"].tolist()[:-1]
    limit_pct = get_limit_pct(code)

    # 先看今日是否涨停（用准确的 curr_pct，来自腾讯实时/收盘数据）
    if curr_pct >= limit_pct:
        streak = 1
        # 然后往前推历史涨停天数
        for p in reversed(pct_list):
            if p >= limit_pct:
                streak += 1
            else:
                break
    else:
        streak = 0

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

    # F. 情绪周期判断（逆向思维）
    if zt_count >= CONFIG["sentiment_hot"]:
        # 高潮期反而扣分，因为是顶部信号
        score -= 10
        tags.append("高潮警惕↓")
    elif zt_count >= 80:
        # 偏热期不加分
        pass
    elif zt_count >= 50:
        # 正常期加分
        score += 5
        tags.append("情绪正常+")

    # --- 风险扣分 ---
    if curr_turn > CONFIG["penalty_hot_turn"]:
        score -= 20
        tags.append("换手过热↓")

    if vol_ratio > CONFIG["penalty_vol_ratio"]:
        score -= 15
        tags.append("量比过激↓")
        # 移除可能已加的"爆量博弈"标签，避免自相矛盾
        if "爆量博弈" in tags:
            tags.remove("爆量博弈")

    # 乖离惩罚：动态阈值（分路径分情绪）
    bias_ma5 = (curr_price - ma5_yest) / ma5_yest if ma5_yest > 0 else 0
    bias_threshold = 0.08
    if in_upper:
        bias_threshold = 0.12
    if mood == "高潮":
        bias_threshold += 0.03
    if bias_ma5 > bias_threshold:
        score -= 20
        tags.append("乖离过大↓")

    # 冷门行业过滤：隔夜溢价较低的行业扣分
    industry = get_stock_industry(code)
    if is_cold_industry(industry):
        score -= 20
        tags.append(f"冷门行业({industry})↓")

    # 尾盘回落检测：post模式下检查今日是否从高点大幅回落
    if CONFIG["MODE"] == "post":
        today_high = float(df.iloc[-1]["high"]) if "high" in df.columns else 0
        if today_high > 0 and curr_price > 0:
            drawdown_from_high = (today_high - curr_price) / today_high
            if drawdown_from_high > 0.03:
                score -= 15
                tags.append("尾盘回落↓")

    # --- 细粒度排序因子（区分度增强）---
    fine_score = 0

    # F1. 量价配合（成交额/换手 比值，反映平均单笔大小）
    if curr_amount > 0 and curr_turn > 0:
        avg_trade_size = curr_amount / (curr_turn * 1e6)
        if avg_trade_size > 50000:
            fine_score += 5
            tags.append("大单为主")
        elif avg_trade_size < 10000:
            fine_score -= 3
            tags.append("散户为主")

    # F2. 距离涨停的远近（高位路径才用）
    if in_upper:
        distance_to_limit = limit_pct - curr_pct
        if distance_to_limit < 1.0:
            fine_score += 5
            tags.append("濒临涨停")
        elif distance_to_limit < 2.0:
            fine_score += 3

    # F3. 收盘价是否守住5日均线（post模式独有）
    if CONFIG["MODE"] == "post":
        if curr_price > ma5_yest * 1.02:
            fine_score += 3
            tags.append("稳守MA5+")
        elif curr_price < ma5_yest:
            fine_score -= 5
            tags.append("破MA5↓")

    score += fine_score

    # --- 最终判定 ---
    if score < CONFIG["score_threshold"]:
        if reject_stats is not None:
            reject_stats["得分不足"] += 1
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
def scan_pool(cfg: dict, zt_count: int, mood: str) -> List[dict]:
    global CONFIG
    CONFIG = cfg

    time_weight = get_time_weight()
    pool_name = cfg["POOL"]
    mode = cfg["MODE"]

    print(f"\n{'=' * 60}")
    print(f"  扫描池: [{pool_name}]  模式: {mode}  时间权重: {time_weight:.2f}")
    print(f"{'=' * 60}")

    stock_pool = get_stock_pool()

    # 预热行业缓存（首次或7天过期时批量查询）
    preload_industries(stock_pool)

    # post 模式也用腾讯接口获取收盘数据（baostock 历史数据有延迟）
    real_map = get_realtime_quotes(stock_pool)
    print(f"  实时行情获取: {len(real_map)} 只")

    results = []
    total = len(stock_pool)
    end_d = beijing_now().strftime("%Y-%m-%d")
    start_d = (beijing_now() - timedelta(days=45)).strftime("%Y-%m-%d")

    reject_stats = {
        "数据不足": 0, "ST/退市": 0, "情绪冷淡": 0, "涨幅不符": 0,
        "成交额": 0, "换手率": 0, "市值": 0, "量比": 0, "均线": 0,
        "压力": 0, "得分不足": 0,
    }

    print(f"  开始扫描 {total} 只股票...")

    for code in stock_pool:
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
        res = analyze_ultimate(hist_df, code, real_info, zt_count, time_weight, reject_stats, mood)
        if res:
            res["pool"] = pool_name
            results.append(res)
            msg = (f"  🎯 {code:<14} 涨幅:{res['pct']:>6.2f}%  "
                   f"路径:{res['path']}  连板:{res['streak']}  得分:{res['score']}  "
                   f"换手:{res['turn']}%  量比:{res['vol_ratio']}")
            if HAS_TQDM:
                tqdm.write(msg)
            else:
                print(msg)

    print(f"\n  📊 过滤统计 [{pool_name}]:")
    for reason, count in sorted(reject_stats.items(), key=lambda x: -x[1]):
        if count > 0:
            bar = "█" * min(count // 5, 20)
            print(f"    {reason:>8}: {count:>5} {bar}")

    return results, reject_stats


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
#  7b. 过滤统计工具
# ============================================================
_REJECT_TREND_FILE = os.path.join(os.path.dirname(__file__), "reject_trend.json")

def _print_reject_summary(rejects: dict, total: int = 0):
    """打印过滤统计，带百分比"""
    if total == 0:
        total = sum(rejects.values())
    print("  过滤统计:")
    for reason, count in sorted(rejects.items(), key=lambda x: -x[1]):
        if count > 0:
            pct = count / total * 100 if total > 0 else 0
            bar = "█" * min(int(pct // 3), 20)
            print(f"    ✗ {reason:>8}: {count:>5} ({pct:5.1f}%) {bar}")

def _save_reject_trend(date_str: str, rejects: dict):
    """保存当日过滤瓶颈到文件，展示5日趋势"""
    total = sum(rejects.values())
    if total == 0:
        return

    trend_entry = {
        "date": date_str,
        "total": total,
        "ratios": {k: round(v / total * 100, 1) for k, v in rejects.items() if v > 0},
    }

    trend_data = []
    if os.path.exists(_REJECT_TREND_FILE):
        try:
            with open(_REJECT_TREND_FILE, "r", encoding="utf-8") as f:
                trend_data = json.load(f)
        except Exception:
            trend_data = []

    # 去重（同一天只保留最新）
    trend_data = [d for d in trend_data if d.get("date") != date_str]
    trend_data.append(trend_entry)

    # 只保留最近30天
    trend_data = sorted(trend_data, key=lambda x: x["date"])[-30:]

    try:
        with open(_REJECT_TREND_FILE, "w", encoding="utf-8") as f:
            json.dump(trend_data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 沙箱/只读环境跳过写入

    # 打印5日趋势
    recent = trend_data[-5:]
    if len(recent) >= 2:
        print(f"\n  📈 过滤瓶颈5日趋势:")
        # 收集所有出现过的原因
        all_reasons = set()
        for d in recent:
            all_reasons.update(d["ratios"].keys())

        for reason in sorted(all_reasons):
            vals = []
            for d in recent:
                vals.append(d["ratios"].get(reason, "-"))
            vals_str = " / ".join(f"{v}%" if isinstance(v, (int, float)) else "-" for v in vals)
            print(f"    {reason:>8}: {vals_str}")


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

    results_stable, rejects_stable = scan_pool(CONFIG_STABLE, zt_count, mood)

    results_upper, rejects_upper = scan_pool(CONFIG_UPPER, zt_count, mood)

    # 聚合两次扫描的reject_stats
    total_rejects = {}
    for stats in (rejects_stable, rejects_upper):
        for reason, count in stats.items():
            total_rejects[reason] = total_rejects.get(reason, 0) + count

    bs.logout()

    all_results = {}
    for r in results_stable + results_upper:
        c = r["code"]
        if c not in all_results:
            all_results[c] = r
        else:
            # 取更高分的版本，但保留双池信号标记
            if r["score"] > all_results[c]["score"]:
                old_pool = all_results[c]["pool"]
                all_results[c] = r
                if r["pool"] != old_pool:
                    all_results[c]["pool"] = r["pool"] + "+" + old_pool
            else:
                if r["pool"] not in all_results[c]["pool"]:
                    all_results[c]["pool"] += "+" + r["pool"]

    print("\n" + "=" * 70)
    print(f"  🔥 隔夜选股法·最优精选清单  ({end_d})  情绪: {mood}({zt_count}家涨停)")
    print("=" * 70)

    if not all_results:
        print("\n  今日暂无符合条件的标的。")
        _print_reject_summary(total_rejects)
        _save_reject_trend(end_d, total_rejects)
        return

    final_df = (
        pd.DataFrame(list(all_results.values()))
        .sort_values(by=["score", "vol_ratio"], ascending=False)
        .reset_index(drop=True)
    )

    # 情绪高潮时推荐数自动减半（逆向思维）
    final_count_limit = 5
    if zt_count >= 100:
        final_count_limit = 3
        print(f"\n  ⚠️ 市场高潮(>=100涨停),推荐数压缩到 {final_count_limit} 只,提示防顶部")
    elif zt_count >= 80:
        final_count_limit = 4
        print(f"\n  ⚠️ 市场偏热(>=80涨停),推荐数压缩到 {final_count_limit} 只")

    stable_picks = final_df[final_df["path"] == "稳健"].head(final_count_limit)
    upper_picks = final_df[final_df["path"] == "高位"].head(final_count_limit)

    total_candidates = len(results_stable) + len(results_upper)
    total_scanned = sum(total_rejects.values()) + total_candidates

    # 打印过滤统计主报告
    print(f"\n  📊 今日扫描汇总: {total_scanned} 只")
    print(f"    ✓ 通过: {total_candidates} 只")
    _print_reject_summary(total_rejects, total_scanned)
    _save_reject_trend(end_d, total_rejects)

    # 只在15:10后写入选股记录，避免盘中数据覆盖盘后定稿
    current_beijing = beijing_now()
    is_post_time = current_beijing.hour > 15 or (current_beijing.hour == 15 and current_beijing.minute >= 10)
    if is_post_time:
        append_to_summary(final_df, end_d, zt_count, mood, total_candidates)
    else:
        print(f"\n  ℹ️ 当前北京时间 {current_beijing.strftime('%H:%M')}，跳过文件写入（等待15:10后盘后定稿）")

    for path_label, picks in [("稳健", stable_picks), ("高位", upper_picks)]:
        if picks.empty:
            continue

        pos_hint = CONFIG_UPPER["position_ratio"] if path_label == "高位" else CONFIG_STABLE["position_ratio"]
        print(f"\n  ── {path_label}路径 ({len(picks)} 只)  💰 {pos_hint}")
        print(f"  {'代码':<14} {'池子':<16} {'价格':>7} {'涨幅%':>7} {'量比':>6} "
              f"{'换手%':>7} {'连板':>5} {'乖离%':>7} {'得分':>5}  特征")
        print(f"  {'-' * 125}")
        for _, row in picks.iterrows():
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
    print("  ✅ Step1 涨幅筛选   ✅ Step2 量比   ✅ Step3 换手率")
    print("  ✅ Step4 市值过滤   ✅ Step5 量能递增   ✅ Step6 均线+压力检测")
    print("  ⚠️ Step7 分时均价线上方（需盘中人工确认）")
    print("  ⚠️ Step8 14:30创新高回踩入场（需盘中人工确认）")
    print("─" * 70 + "\n")

    # ============================================================
    #  v1.1: Telegram 推送(如已配置)
    # ============================================================
    if TELEGRAM_ENABLED:
        # 只在15:10后推送盘后定稿，避免盘中重复推送
        # 双重保险：检查MODE和实时时间
        current_beijing = beijing_now()
        is_post_time = current_beijing.hour > 15 or (current_beijing.hour == 15 and current_beijing.minute >= 10)
        
        if CONFIG.get("MODE") == "post" and is_post_time:
            mode_label = "盘后定稿"
            title = f"zuiyou1 v1.3 {mode_label}"
            mood_info = f"情绪: {mood} ({zt_count}家涨停)"

            # 使用已筛选的 stable_picks 和 upper_picks（已应用推荐数限制）
            stable_list = []
            upper_list = []
            for _, row in stable_picks.iterrows():
                stable_list.append({
                    "code": row["code"],
                    "price": row["price"],
                    "pct": row["pct"],
                    "vol_ratio": row.get("vol_ratio", 0),
                    "turn": row.get("turn", 0),
                    "score": row["score"],
                    "tags": row["tags"],
                })
            for _, row in upper_picks.iterrows():
                upper_list.append({
                    "code": row["code"],
                    "price": row["price"],
                    "pct": row["pct"],
                    "vol_ratio": row.get("vol_ratio", 0),
                    "turn": row.get("turn", 0),
                    "score": row["score"],
                    "tags": row["tags"],
                })

            operation_note = (
                "稳健: 次日09:35未维持昨收+1%即出\n"
                "高位: 次日竞价弱于昨收即清仓\n"
                "全局止损: 亏损超-2.5%无条件清仓"
            )

            # 构建过滤统计摘要
            reject_lines = []
            for reason, count in sorted(total_rejects.items(), key=lambda x: -x[1]):
                if count > 0:
                    pct = count / total_scanned * 100
                    reject_lines.append(f"✗ {reason}: {count}只({pct:.0f}%)")
            reject_summary = "\n".join(reject_lines[:5]) if reject_lines else "无"

            try:
                ok = send_stock_picks(title, current_beijing.strftime("%Y-%m-%d"), mood_info, stable_list, upper_list, operation_note, reject_summary)
                if ok:
                    print("  ✅ 已推送到 Telegram\n")
                else:
                    print("  ⚠️ Telegram 推送失败,请检查 token/chat_id\n")
            except Exception as e:
                print(f"  ⚠️ Telegram 推送异常: {e}\n")
        else:
            print(f"  ℹ️ 当前北京时间 {current_beijing.strftime('%H:%M')}，跳过推送（等待15:10后盘后定稿）\n")


# ============================================================
#  8b. 单股调试模式
# ============================================================
def debug_stock(code: str):
    """调试单只股票，查看每一步过滤结果"""
    print(f"\n{'=' * 60}")
    print(f"  调试模式: {code}")
    print(f"{'=' * 60}\n")

    bs.login()

    # 获取实时行情
    real_map = get_realtime_quotes([code])
    key = code.replace(".", "").lower()
    real_info = real_map.get(key)
    if real_info is None:
        print(f"  ✗ 无法获取 {code} 的实时行情")
        bs.logout()
        return

    name = real_info.get("name", "未知")
    print(f"  {code} {name}")
    print(f"  现价: {real_info['now']}  涨幅: {real_info['pct']:.2f}%  换手: {real_info.get('turn', 0):.2f}%")
    print(f"  量: {real_info['vol']}  额: {real_info['amount']:.0f}万  市值: {real_info.get('mktcap', 0):.0f}亿\n")

    # 获取历史数据
    end_d = beijing_now().strftime("%Y-%m-%d")
    start_d = (beijing_now() - timedelta(days=45)).strftime("%Y-%m-%d")
    k_rs = bs.query_history_k_data_plus(
        code, FIELDS_HIST,
        start_date=start_d, end_date=end_d,
        frequency="d", adjustflag="3",
    )
    if k_rs.error_code != "0":
        print(f"  ✗ 无法获取历史数据: {k_rs.error_msg}")
        bs.logout()
        return

    data_list = []
    while k_rs.next():
        data_list.append(k_rs.get_row_data())

    if len(data_list) < 15:
        print(f"  ✗ 数据不足: 仅 {len(data_list)} 条（需要≥15条）")
        bs.logout()
        return

    hist_df = pd.DataFrame(data_list, columns=k_rs.fields)

    # 逐步检查
    steps = []
    df = hist_df.copy()
    for col in ["close", "volume", "pctChg", "turn", "amount", "high", "low", "open", "preclose"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df = df[df["volume"] > 0]
    if len(df) < 12:
        print(f"  ✗ 有效数据不足: 仅 {len(df)} 条（需要≥12条）")
        bs.logout()
        return

    # 使用当前配置
    global CONFIG
    CONFIG = CONFIG_STABLE

    curr_price = real_info["now"]
    curr_pct = real_info["pct"]
    curr_vol = real_info["vol"]
    curr_amount = real_info["amount"]
    curr_turn = real_info.get("turn", 0.0)
    if curr_turn <= 0:
        curr_turn = float(df.iloc[-1]["turn"]) if "turn" in df.columns else 0.0

    time_weight = get_time_weight()
    est_full_vol = curr_vol / time_weight if time_weight > 0 else curr_vol
    hist_vols = df["volume"].tolist()

    # Step 1: 涨幅
    in_stable = CONFIG["stable_pct_lo"] <= curr_pct <= CONFIG["stable_pct_hi"]
    in_upper = CONFIG["upper_pct_lo"] <= curr_pct <= CONFIG["upper_pct_hi"]
    steps.append(("涨幅筛选", in_stable or in_upper,
                  f"{curr_pct:.2f}% [{'稳健' if in_stable else '高位' if in_upper else '无'}] "
                  f"稳健:{CONFIG['stable_pct_lo']}-{CONFIG['stable_pct_hi']}% 高位:{CONFIG['upper_pct_lo']}-{CONFIG['upper_pct_hi']}%"))
    if not (in_stable or in_upper):
        _print_debug_steps(steps)
        bs.logout()
        return

    # Step 2: 成交额
    ok = CONFIG["min_amount"] <= curr_amount <= CONFIG["max_amount"]
    steps.append(("成交额", ok, f"{curr_amount/1e4:.0f}万 要求:{CONFIG['min_amount']/1e4:.0f}万-{CONFIG['max_amount']/1e4:.0f}万"))
    if not ok:
        _print_debug_steps(steps)
        bs.logout()
        return

    # Step 3: 换手率
    ok = CONFIG["turn_min"] <= curr_turn <= CONFIG["turn_max"]
    steps.append(("换手率", ok, f"{curr_turn:.2f}% 要求:{CONFIG['turn_min']}-{CONFIG['turn_max']}%"))
    if not ok:
        _print_debug_steps(steps)
        bs.logout()
        return

    # Step 4: 市值
    mktcap = real_info.get("mktcap", 0)
    if mktcap > 0:
        ok = CONFIG["min_mktcap"] <= mktcap <= CONFIG["max_mktcap"]
        steps.append(("流通市值", ok, f"{mktcap:.0f}亿 要求:{CONFIG['min_mktcap']}-{CONFIG['max_mktcap']}亿"))
        if not ok:
            _print_debug_steps(steps)
            bs.logout()
            return
    else:
        steps.append(("流通市值", True, "数据缺失，跳过"))

    # Step 5: 量比
    recent_vols = sorted(hist_vols[-12:])
    if len(recent_vols) < 4:
        steps.append(("量比", False, f"有效数据仅{len(recent_vols)}条，不足计算"))
        _print_debug_steps(steps)
        bs.logout()
        return
    trimmed = recent_vols[1:-1] if len(recent_vols) > 2 else recent_vols
    avg_vol_trimmed = sum(trimmed) / len(trimmed)
    vol_ratio = est_full_vol / avg_vol_trimmed if avg_vol_trimmed > 0 else 0
    ok = CONFIG["vol_ratio_min"] <= vol_ratio <= CONFIG["vol_ratio_max"]
    steps.append(("量比", ok, f"{vol_ratio:.2f} 要求:{CONFIG['vol_ratio_min']}-{CONFIG['vol_ratio_max']}"))
    if not ok:
        _print_debug_steps(steps)
        bs.logout()
        return

    # Step 6a: 均线
    hist_close = df["close"].tolist()[:-1]
    if len(hist_close) < 10:
        steps.append(("均线", False, f"有效数据仅{len(hist_close)}条，不足计算"))
        _print_debug_steps(steps)
        bs.logout()
        return
    ma5_yest = sum(hist_close[-5:]) / 5
    ma10_yest = sum(hist_close[-10:]) / 10
    ok = curr_price > ma5_yest > ma10_yest
    steps.append(("均线多头", ok, f"现价:{curr_price:.2f} MA5:{ma5_yest:.2f} MA10:{ma10_yest:.2f}"))
    if not ok:
        _print_debug_steps(steps)
        bs.logout()
        return

    # Step 6b: 压力
    recent_highs = df["high"].tail(20).tolist()
    recent_highs = [float(x) for x in recent_highs if float(x) > 0]
    if recent_highs:
        max_high = max(recent_highs)
        resistance_ratio = (max_high - curr_price) / curr_price
        threshold = 0.15 if in_upper else 0.08
        ok = resistance_ratio <= threshold
        steps.append(("压力检测", ok, f"压力比:{resistance_ratio*100:.2f}% 阈值:{threshold*100:.0f}%"))
        if not ok:
            _print_debug_steps(steps)
            bs.logout()
            return

    # 冷门行业
    industry = get_stock_industry(code)
    cold = is_cold_industry(industry)
    steps.append(("行业过滤", not cold, f"{industry} {'冷门行业扣分' if cold else '正常'}"))

    # 尾盘回落（post模式）
    if CONFIG["MODE"] == "post":
        today_high = float(df.iloc[-1]["high"]) if "high" in df.columns else 0
        if today_high > 0 and curr_price > 0:
            drawdown = (today_high - curr_price) / today_high
            ok = drawdown <= 0.03
            steps.append(("尾盘回落", ok, f"回撤:{drawdown*100:.2f}% 阈值:3%"))

    # 评分
    score = 50
    tags = []
    if in_stable:
        score += 15; tags.append("稳健蓄势")
        bias = (curr_price - ma5_yest) / ma5_yest if ma5_yest > 0 else 1
        if bias < 0.02:
            score += 10; tags.append("紧贴MA5")
    elif in_upper:
        score += 20; tags.append("高位博弈")

    if 1.8 <= vol_ratio <= 4.0:
        score += 25; tags.append("黄金放量")
    elif vol_ratio > 4.0:
        score += 10; tags.append("爆量博弈")
    else:
        score += 5; tags.append("量能达标")

    if 5.0 <= curr_turn <= 8.0:
        score += 15; tags.append("黄金换手")
    elif 8.0 < curr_turn <= 10.0:
        score += 8; tags.append("换手偏高")

    if cold:
        score -= 20; tags.append("冷门行业↓")

    steps.append(("评分", score >= CONFIG["score_threshold"],
                  f"得分:{score} 阈值:{CONFIG['score_threshold']} 标签:{' | '.join(tags)}"))

    _print_debug_steps(steps)
    if score >= CONFIG["score_threshold"]:
        print(f"\n  ✅ {code} 通过所有过滤！")
    else:
        print(f"\n  ✗ {code} 得分不足，被过滤")

    bs.logout()

def _print_debug_steps(steps):
    """打印调试步骤结果"""
    for i, (name, passed, detail) in enumerate(steps, 1):
        status = "✅" if passed else "✗"
        print(f"  Step{i:2d} {name:<8}: {status} {detail}")
        if not passed:
            print(f"  → 跳过后续步骤")
            break


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--debug-stock":
        if len(sys.argv) < 3:
            print("用法: python zuiyou1.py --debug-stock <股票代码>")
            print("示例: python zuiyou1.py --debug-stock sh.600519")
            sys.exit(1)
        debug_stock(sys.argv[2])
    else:
        main()