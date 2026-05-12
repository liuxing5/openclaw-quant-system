"""
隔夜选股法·最优融合版 (zuiyou1 v1.6)
========================================
v1.6.2 修订（2026-05-12）：
  ✓ 修复腾讯API字段索引错误 —— p[45]-p[52]映射修正(量比/委比/大单净量/主力净流入/涨跌停价)
  ✓ 修复流通市值缺失 —— 新增circ_mcap字段，优先使用流通市值而非总市值
  ✓ 修复主力净流入单位 —— p[52]单位为万，统一乘以10000转为元
  ✓ 修复机构预期数据未填充 —— earnings_forecast结构化数据现在正确提取并存储

v1.6.1 修订（2026-05-12）：
  ✓ 修复 8:25 运行显示"盘中初筛" —— 增加 pre_market 模式，盘前显示"盘前预览"
  ✓ 修复 MODE 判断 —— 14:00-15:10 为 realtime，其余为 pre_market，15:10 后为 post

v1.6 修订（2026-05-12）：
  ✓ 新增 PE/PB估值评分 —— 低估值加分，高估值扣分
  ✓ 新增 财务质量评分 —— 利润率/毛利率/负债率/现金流质量
  ✓ 新增 次新股过滤 —— 上市<60天直接跳过
  ✓ 禁用 五档盘口采集 —— 数据衰减快，对隔夜策略价值低

v1.5.5 修订（2026-05-10）：
  ✓ P0 修复 情绪周期判断 elif 分支仍使用 zt_count —— 统一改为 sentiment_score，7个分支全部使用综合情绪分(0-100)

v1.5.4 修订（2026-05-10）：
  ✓ P0 修复 zt_count 变量名混乱 —— 统一重命名为 sentiment_score，消除语义歧义
  ✓ P0 修复 main() 中 zt_count 未定义 —— append_to_summary/Telegram 推送不再 NameError
  ✓ P0 修复 append_to_summary mode_label 参数被覆盖 —— 改为 if not 判断
  ✓ P1 修复 _persist_to_daily_candidates quant_score/final_score 重复 —— 分离量化评分和 LLM 加成
  ✓ P1 修复 debug_stock 缺少 LLM 加成 —— 增加 get_llm_boost_score 和 120 分上限
  ✓ P2 消除 CONFIG 全局冗余变量 —— 统一使用 CONFIG_STABLE/CONFIG_UPPER
  ✓ P2 修复 _format_funnel remaining 可能为负数 —— max(0, remaining) 防护
  ✓ P3 修复 debug_stock 行业评分重复计算 —— 复用已计算的 industry_bonus

v1.5.3 修订（2026-05-10）：
  ✓ P0 修复 get_db 未导入 —— LLM 候选池从 Supabase 读取不再 NameError
  ✓ P0 修复 Telegram 推送重复调用 API —— 复用 scan_pool 返回的池大小/行情数/时间权重
  ✓ P1 header_info 动态情绪描述 —— 不再硬编码"情绪偏热"
  ✓ P1 pool_summary 动态 mode/time_weight —— 不再硬编码 post/1.00
  ✓ P1 debug_stock 支持 cfg 参数 —— 可调试任意路径
  ✓ P3 行业查询失败冷却重试 —— 空字符串缓存 1 小时后可重试
  ✓ P3 _format_funnel 统一漏斗统计 —— 消除 _print_funnel/_print_reject_summary 重复代码

v1.5.2 修订（2026-05-10）：
  ✓ P0 修复 get_stock_pool 仍读全局 CONFIG —— 改为接收 pool_name 参数，双池扫描正确
  ✓ P0 修复 append_to_summary 仍读全局 CONFIG —— mode_label 改为传参
  ✓ P1 修复 CONFIG_UPPER MODE 不同步 —— 模块加载时同时更新双池 MODE
  ✓ P2 preload_industries 去重 —— 第二次 scan_pool 跳过重复预热
  ✓ P3 get_stock_industry 增加 DEBUG 日志 —— 连接异常时可追溯

v1.5.1 修订（2026-05-10）：
  ✓ P0 消除全局 CONFIG 依赖 —— analyze_ultimate/scan_pool 改为传参，双池配置隔离
  ✓ P0 修复 stock_name 传 None —— 从腾讯行情/LLM缓存获取名称写入 daily_candidates
  ✓ P1 评分膨胀修复 —— 设置评分上限120分，阈值提高到85分
  ✓ P1 mktcap 单位修正 —— 腾讯 f44 单位为亿元，乘以1e8转元
  ✓ P2 情绪接口降级 —— 增加备用默认值，基于历史统计更合理
  ✓ P2 行业缓存并发保护 —— 临时文件原子写入 + 文件锁
  ✓ P2 LLM 预过滤逻辑统一 —— 与 analyze_ultimate 内部判断一致
  ✓ P3 版本号统一 —— main() 打印与文件头一致
  ✓ P3 时间权重盘前逻辑 —— 返回 None 表示不可用
  ✓ P3 Telegram 推送复用数据 —— 避免重复调用 get_stock_pool/get_realtime_quotes
  ✓ P3 position_pct 从配置读取 —— 不再硬编码
  ✓ P3 _normalize_to_baostock 逻辑优化 —— 科创板/创业板明确分支
  ✓ P3 LLM SQL 时间窗口 —— 增加交易日感知，避免周末数据偏差

v1.5 修订（2026-05-09）：
  ✓ LLM候选池整合 —— 从Supabase读取昨日LLM多源策略候选池
  ✓ LLM优先级加成 —— 候选股票获得额外加分（最高+25分）
  ✓ 共鸣强化标记 —— 3+源推荐的标的额外+5分加成
  ✓ Telegram推送区分 —— LLM候选(🤖)和八步法自有(🔮)分开显示

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

# ============================================================
# 行业分类与评分体系
# ============================================================

# 行业分类体系（基于A股市场特性和隔夜溢价表现）
INDUSTRY_CATEGORIES = {
    # 热门行业（隔夜溢价较高，加分）
    "hot": {
        "keywords": [
            "半导体", "芯片", "集成电路", "人工智能", "AI", "机器学习", "大数据",
            "云计算", "软件服务", "互联网", "电商", "直播", "游戏", "元宇宙",
            "新能源", "光伏", "储能", "锂电池", "充电桩", "新能源汽车", "智能汽车",
            "军工", "航天", "国防", "半导体材料", "光刻", "算力", "数据中心",
            "生物医药", "创新药", "CXO", "医疗器械", "医美", "基因", "疫苗",
            "消费电子", "苹果概念", "华为概念", "5G", "物联网", "工业互联",
            "机器人", "智能制造", "工业自动化", "专精特新", "国企改革",
        ],
        "score_bonus": 15,
        "tags": ["热门赛道"]
    },
    # 中性行业（常规表现，不加分不扣分）
    "neutral": {
        "keywords": [
            "通用设备", "专用设备", "机械设备", "电气设备", "仪器仪表",
            "化工", "化学制品", "塑料", "橡胶", "新材料",
            "纺织", "服装", "家居", "造纸", "包装",
            "食品饮料", "白酒", "啤酒", "乳制品", "调味品",
            "零售", "百货", "超市", "家电", "家具",
            "建筑装饰", "建筑材料", "水泥", "玻璃",
            "交通运输", "物流", "快递", "仓储",
            "通信服务", "运营商", "光纤", "通信设备",
        ],
        "score_bonus": 0,
        "tags": ["中性行业"]
    },
    # 防御性行业（波动低，隔夜溢价一般，小幅扣分）
    "defensive": {
        "keywords": [
            "银行", "保险", "证券", "多元金融", "房地产", "物业", "园区开发",
            "公用事业", "电力", "水务", "燃气", "供热",
            "交通运输", "铁路", "公路", "港口", "机场", "航空",
            "农林牧渔", "种植", "养殖", "饲料", "化肥",
        ],
        "score_bonus": -10,
        "tags": ["防御板块"]
    },
    # 冷门/周期行业（隔夜溢价低，明显扣分）
    "cold": {
        "keywords": [
            "煤炭", "钢铁", "黑色金属", "有色金属", "黄金", "稀土",
            "石油", "石化", "化工原料", "化纤",
            "采掘", "矿业", "金属制品", "钢铁加工",
            "建材", "水泥制造", "玻璃制造",
        ],
        "score_bonus": -25,
        "tags": ["冷门行业"]
    },
}

def analyze_industry(industry: str) -> tuple:
    """
    分析行业分类并返回评分和标签
    
    返回: (score_bonus, category, tags)
    """
    if not industry:
        return 0, "未知", ["行业未知"]
    
    industry_lower = industry.lower()
    
    # 按优先级匹配（热门 > 冷门 > 防御 > 中性）
    for category, config in INDUSTRY_CATEGORIES.items():
        for keyword in config["keywords"]:
            if keyword.lower() in industry_lower:
                return config["score_bonus"], category, config["tags"]
    
    # 默认归类为中性
    return 0, "中性", ["中性行业"]

# 行业缓存（内存+文件双缓存，7天更新一次）
_industry_cache = {}
_industry_fail_times = {}
_INDUSTRY_RETRY_COOLDOWN = 3600
# 缓存文件放到 ~/.cache 而非仓库目录，让 GitHub Actions 的 actions/cache@v4
# 能跨 workflow run 持久化；本地开发时 ~/.cache 也始终存在。
# ZUIYOU_INDUSTRY_CACHE 环境变量可覆盖路径，便于测试。
_INDUSTRY_CACHE_FILE = os.environ.get(
    "ZUIYOU_INDUSTRY_CACHE",
    os.path.expanduser("~/.cache/zuiyou_industry.json"),
)
os.makedirs(os.path.dirname(_INDUSTRY_CACHE_FILE), exist_ok=True)

# 兼容：若仓库内还残留旧 industry_cache.json，首次运行时迁移到新位置
_LEGACY_CACHE_FILE = os.path.join(os.path.dirname(__file__), "industry_cache.json")
if os.path.exists(_LEGACY_CACHE_FILE) and not os.path.exists(_INDUSTRY_CACHE_FILE):
    try:
        import shutil as _sh
        _sh.move(_LEGACY_CACHE_FILE, _INDUSTRY_CACHE_FILE)
    except Exception:
        pass

def preload_industries(stock_pool: list):
    """启动时一次性查询所有股票行业，缓存到本地文件，7天更新一次
    v1.5.1: 增加临时文件原子写入，避免并发写入损坏文件。
    """
    if os.path.exists(_INDUSTRY_CACHE_FILE):
        mtime = os.path.getmtime(_INDUSTRY_CACHE_FILE)
        if time.time() - mtime < 7 * 86400:
            try:
                with open(_INDUSTRY_CACHE_FILE, "r", encoding="utf-8") as f:
                    _industry_cache.update(json.load(f))
                return
            except Exception:
                pass

    print("预热行业缓存（首次或过期）...")
    for code in stock_pool:
        get_stock_industry(code)

    tmp_file = _INDUSTRY_CACHE_FILE + ".tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(_industry_cache, f, ensure_ascii=False)
        os.replace(tmp_file, _INDUSTRY_CACHE_FILE)
    except Exception:
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except Exception:
                pass

def get_stock_industry(code: str) -> str:
    """获取股票所属行业，带内存+文件双缓存
    v1.5.2: 失败查询增加冷却时间，1小时后可重试，避免空字符串永久缓存。
    """
    if code in _industry_cache:
        return _industry_cache[code]

    now_ts = time.time()
    last_fail = _industry_fail_times.get(code, 0)
    if now_ts - last_fail < _INDUSTRY_RETRY_COOLDOWN:
        return ""

    try:
        rs = bs.query_stock_industry(code=code)
        if rs.error_code == '0':
            row = rs.get_row_data()
            if row and len(row) > 0:
                industry = row[0] if row[0] else ""
                _industry_cache[code] = industry
                return industry
        else:
            if DEBUG:
                print(f"  [DEBUG] 行业查询失败 {code}: {rs.error_msg}")
            _industry_fail_times[code] = now_ts
    except Exception as e:
        if DEBUG:
            print(f"  [DEBUG] 行业查询异常 {code}: {e}")
        _industry_fail_times[code] = now_ts
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
#  v1.4+: 持久化到 daily_candidates（与 llm_multisource 共享表）
# ============================================================
import sys as _sys
_sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
try:
    from core.db.connection import db_configured, get_db
    from core.db.candidates import write_candidates
    from core.utils.ts_code import baostock_to_standard
    from core.utils.env import load_project_env
    load_project_env()
    DB_ENABLED = db_configured()
    if not DB_ENABLED:
        print("ℹ️ POSTGRES_* 未配置，不启用 daily_candidates 写入")
except Exception as _e:
    DB_ENABLED = False
    print(f"ℹ️ core 包不可用，不启用 daily_candidates 写入: {_e}")


def _persist_to_daily_candidates(
    stable_picks,
    upper_picks,
    snapshot_date,
    name_map: dict = None,
    run_mode: str = 'afternoon',
) -> int:
    """Write zuiyou1 picks to shared daily_candidates table (source='overnight_8step').

    run_mode:
      - 'intraday'  : 14:30 盘中初筛快照（用于 15:10 计算 diff，selected=TRUE 但仅供内部对比）
      - 'afternoon' : 15:10 盘后定稿（最终推送的版本）
    """
    if not DB_ENABLED:
        return 0
    items = []
    for picks_df, pool_label, position in [(stable_picks, 'stable', CONFIG_STABLE['position_ratio']), (upper_picks, 'upper', CONFIG_UPPER['position_ratio'])]:
        for _, row in picks_df.iterrows():
            price = float(row['price'])
            tags_str = row.get('tags') or ''
            logic_tags = [t.strip() for t in tags_str.replace('/', '|').split('|') if t.strip()]
            logic_tags.append(f'pool:{pool_label}')
            code = row['code']
            stock_name = None
            if name_map:
                key = code.replace(".", "").lower()
                stock_name = name_map.get(key)
            items.append({
                'ts_code': baostock_to_standard(code),
                'stock_name': stock_name,
                'final_score': float(row['score']),
                'quant_score': float(row.get('quant_score', row['score'])),
                'llm_score': float(row.get('llm_final_score', 0)),
                'consensus_score': 1.0,
                'mention_count': 1,
                'source_diversity': 1,
                'logic_tags': logic_tags,
                'selected': True,
                'position_pct': position,
                'entry_low': round(price * 0.99, 2),
                'entry_high': round(price * 1.01, 2),
                'stop_loss': round(price * 0.975, 2),
                'target_1': round(price * 1.05, 2),
                'target_2': round(price * 1.10, 2),
                'sources': [{'source': 'zuiyou1', 'pool': pool_label, 'pct': float(row.get('pct', 0)),
                             'vol_ratio': float(row.get('vol_ratio', 0)), 'turn': float(row.get('turn', 0)),
                             'streak': int(row.get('streak', 0))}],
            })
    if not items:
        return 0
    try:
        return write_candidates(items, snapshot_date, source='overnight_8step', run_mode=run_mode)
    except Exception as e:
        print(f"⚠️ daily_candidates 写入失败（不影响推送）: {e}")
        return 0


def _persist_zero_result_audit(snapshot_date, reject_stats: dict, sentiment_score: int, mood: str) -> int:
    """零候选场景下写一行审计记录到 daily_candidates。

    selected=FALSE 不会被 pusher/report/tracker 拾取（它们都过滤 selected=TRUE），
    但在表里能直接看到"T 日 8步法跑了、扫了多少只、最大瓶颈是什么"，避免出现
    "查表为空 → 不知道是没跑还是没结果"的运维盲区。
    """
    if not DB_ENABLED:
        return 0
    total = sum(reject_stats.values())
    top_reject = max(reject_stats.items(), key=lambda kv: kv[1], default=("none", 0))
    # 判断本次是 intraday 还是 afternoon（与正常持久化保持一致的语义）
    now = beijing_now()
    run_mode = 'afternoon' if (now.hour > 15 or (now.hour == 15 and now.minute >= 10)) else 'intraday'
    audit_item = {
        'ts_code': '000000.AUDIT',  # 审计专用占位 ts_code
        'stock_name': '零候选审计',
        'final_score': 0,
        'quant_score': 0,
        'llm_score': 0,
        'consensus_score': 0,
        'mention_count': 0,
        'source_diversity': 0,
        'logic_tags': [
            f'audit:zero_result',
            f'scanned:{total}',
            f'top_reject:{top_reject[0]}({top_reject[1]})',
            f'sentiment:{sentiment_score}',
            f'mood:{mood}',
        ],
        'selected': False,
        'position_pct': 0,
        'sources': [{'source': 'zuiyou1_audit', 'reject_stats': reject_stats}],
    }
    try:
        return write_candidates(
            [audit_item], snapshot_date,
            source='overnight_8step', run_mode=run_mode,
        )
    except Exception as e:
        print(f"⚠️ 零候选审计行写入失败（不影响主流程）: {e}")
        return 0


def _read_intraday_picks(snapshot_date) -> set:
    """读取当日 14:30 盘中初筛入选的 ts_code 集合，用于 15:10 计算 diff。"""
    if not DB_ENABLED:
        return set()
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT ts_code FROM daily_candidates
            WHERE snapshot_date = %s
              AND source = 'overnight_8step'
              AND run_mode = 'intraday'
              AND selected = TRUE;
        """, (snapshot_date,))
        codes = {row[0] for row in cur.fetchall()}
        cur.close(); conn.close()
        return codes
    except Exception as e:
        print(f"⚠️ 读取 intraday 快照失败: {e}")
        return set()

# ============================================================
#  LLM候选池整合（v1.5+）
# ============================================================
# 缓存以 baostock 格式（'sz.000001'）为 key，避免 baostock <-> standard
# 之间反复转换。get_llm_candidates_from_supabase 在写入时统一归一化。
_llm_candidates_cache = {}

def _normalize_to_baostock(code: str) -> str:
    """把任意常见格式归一到 baostock 格式 'sz.000001' / 'sh.600519'"""
    if not code:
        return ""
    if "." in code:
        a, b = code.split(".", 1)
        if a.lower() in ("sz", "sh", "bj"):
            return f"{a.lower()}.{b}"
        if b.upper() in ("SZ", "SH", "BJ"):
            return f"{b.lower()}.{a}"
    if code.isdigit():
        if code.startswith("68"):
            return f"sh.{code}"
        if code.startswith("6"):
            return f"sh.{code}"
        if code.startswith("30"):
            return f"sz.{code}"
        if code.startswith("0"):
            return f"sz.{code}"
        if code.startswith(("8", "4")):
            return f"bj.{code}"
    return code


def get_llm_candidates_from_supabase(
    today: str,
    lookback_days: int = 3,
    min_score: float = 40.0,
) -> dict:
    """从Supabase读取最近 lookback_days 天的 LLM 候选池，用于八步法优先级加成

    时间窗口为 [today - lookback_days, today)，覆盖 T-1 盘后产出，
    且周一查询能找到上周五的数据。
    使用项目统一的 core.db.connection.get_db()，依赖 POSTGRES_HOST/PORT/USER/PASSWORD/DB。
    缓存 key 统一为 baostock 格式（'sz.000001'），与 stock_pool / analyze_ultimate 一致。
    v1.5.1: 增加交易日感知，周末/节假日自动扩展窗口到最近5个交易日。
    """
    global _llm_candidates_cache

    if _llm_candidates_cache:
        return _llm_candidates_cache

    if not DB_ENABLED:
        return {}

    try:
        conn = get_db()
        cur = conn.cursor()

        today_date = datetime.strptime(today, "%Y-%m-%d").date()
        effective_lookback = lookback_days
        if today_date.weekday() == 0:
            effective_lookback = max(lookback_days, 5)
        elif today_date.weekday() == 6:
            effective_lookback = max(lookback_days, 4)

        cur.execute("""
            SELECT DISTINCT ON (ts_code)
                   ts_code, final_score, llm_score, quant_score,
                   source_diversity, logic_tags, stock_name, snapshot_date
            FROM daily_candidates
            WHERE snapshot_date >= (%s::date - (%s || ' days')::interval)
              AND snapshot_date < %s::date
              AND source = 'llm_multisource'
              AND run_mode = 'afternoon'
              AND (selected = TRUE OR final_score >= %s)
            ORDER BY ts_code, snapshot_date DESC, final_score DESC
        """, (today, effective_lookback, today, min_score))

        rows = cur.fetchall()
        cur.close()
        conn.close()

        for row in rows:
            ts_code = row[0]
            bs_code = _normalize_to_baostock(ts_code)
            if not bs_code:
                continue
            _llm_candidates_cache[bs_code] = {
                'ts_code': ts_code,
                'final_score': float(row[1]) if row[1] else 0,
                'llm_score': float(row[2]) if row[2] else 0,
                'quant_score': float(row[3]) if row[3] else 0,
                'source_diversity': int(row[4]) if row[4] else 0,
                'logic_tags': row[5] or [],
                'stock_name': row[6] or '',
                'snapshot_date': row[7],
            }

        print(f"✓ 从Supabase加载 {len(_llm_candidates_cache)} 只LLM候选池 (近{lookback_days}日)")
        return _llm_candidates_cache

    except Exception as e:
        print(f"⚠️ LLM候选池读取失败: {e}")
        return {}


def is_llm_candidate(code: str) -> tuple:
    """检查股票是否在LLM候选池中

    code 可以是 baostock ('sz.000001') 或 standard ('000001.SZ') 格式，
    内部统一归一化到 baostock 后查缓存。
    """
    global _llm_candidates_cache
    if not _llm_candidates_cache:
        return False, {}
    bs_code = _normalize_to_baostock(code)
    if bs_code in _llm_candidates_cache:
        return True, _llm_candidates_cache[bs_code]
    return False, {}


def get_llm_boost_score(code: str) -> float:
    """获取LLM候选池的加成分数，上限12分（总分120的10%）"""
    is_candidate, info = is_llm_candidate(code)
    if not is_candidate:
        return 0.0
    
    boost = 0.0
    boost += min(info.get('final_score', 0) * 0.05, 8.0)
    boost += min(info.get('llm_score', 0) * 0.03, 5.0)
    
    source_diversity = info.get('source_diversity', 0)
    if source_diversity >= 3:
        boost += 2.0
    elif source_diversity >= 2:
        boost += 1.0
    
    return min(boost, 12.0)


# ============================================================
#  新增指标缓存（强势股排名、机构预期、概念板块）
# ============================================================
_strong_rank_cache = {}
_earnings_cache = {}
_concept_cache = {}
_stock_concept_map = {}
_valuation_cache = {}
_fundamentals_cache = {}
_list_date_cache = {}
_indicators_loaded = False

def load_new_indicators(trade_date: str = None):
    """从数据库加载新增指标到缓存"""
    global _strong_rank_cache, _earnings_cache, _concept_cache, _stock_concept_map
    global _valuation_cache, _fundamentals_cache, _list_date_cache, _indicators_loaded
    
    if not DB_ENABLED:
        return
    
    if trade_date is None:
        trade_date = beijing_now().strftime("%Y-%m-%d")
    
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # 加载强势股排名
        cur.execute("""
            SELECT ts_code, rank_type, rank_position, consecutive_days,
                   stage_chg_pct, cumulative_turnover, industry
            FROM strong_stock_rank WHERE trade_date=%s;
        """, (trade_date,))
        for r in cur.fetchall():
            ts = r[0]
            if ts not in _strong_rank_cache:
                _strong_rank_cache[ts] = []
            _strong_rank_cache[ts].append({
                'rank_type': r[1],
                'rank_position': r[2],
                'consecutive_days': r[3],
                'stage_chg_pct': r[4],
                'cumulative_turnover': r[5],
                'industry': r[6],
            })
        
        # 加载机构预期
        cur.execute("""
            SELECT ts_code, forecast_year, institution_count, eps_mean,
                   eps_min, eps_max, industry_avg
            FROM earnings_forecast WHERE forecast_year = EXTRACT(YEAR FROM CURRENT_DATE);
        """)
        for r in cur.fetchall():
            _earnings_cache[r[0]] = {
                'forecast_year': r[1],
                'institution_count': r[2],
                'eps_mean': float(r[3]) if r[3] else None,
                'eps_min': float(r[4]) if r[4] else None,
                'eps_max': float(r[5]) if r[5] else None,
                'industry_avg': float(r[6]) if r[6] else None,
            }
        
        # 加载概念板块
        cur.execute("""
            SELECT concept_code, concept_name, pct_chg, turnover_rate,
                   lead_stock_code, lead_stock_name, stock_count
            FROM concept_board_quotes WHERE trade_date=%s;
        """, (trade_date,))
        for r in cur.fetchall():
            _concept_cache[r[0]] = {
                'concept_name': r[1],
                'pct_chg': r[2],
                'turnover_rate': r[3],
                'lead_stock_code': r[4],
                'lead_stock_name': r[5],
                'stock_count': r[6],
            }
        
        # 加载概念成分股映射
        cur.execute("""
            SELECT ts_code, concept_code, concept_name
            FROM concept_membership;
        """)
        for r in cur.fetchall():
            ts = r[0]
            if ts not in _stock_concept_map:
                _stock_concept_map[ts] = []
            _stock_concept_map[ts].append({
                'concept_code': r[1],
                'concept_name': r[2],
            })
        
        # 加载PE/PB估值数据
        cur.execute("""
            SELECT ts_code, pe_ratio, pb_ratio
            FROM daily_quotes WHERE trade_date=%s
              AND pe_ratio IS NOT NULL;
        """, (trade_date,))
        for r in cur.fetchall():
            _valuation_cache[r[0]] = {
                'pe_ratio': float(r[1]) if r[1] else None,
                'pb_ratio': float(r[2]) if r[2] else None,
            }
        
        # 加载财务质量数据（最新季报）
        cur.execute("""
            SELECT DISTINCT ON (ts_code)
                   ts_code, net_margin, gross_margin, debt_ratio,
                   revenue, net_profit, operating_cashflow
            FROM stock_fundamentals
            ORDER BY ts_code, report_date DESC;
        """)
        for r in cur.fetchall():
            _fundamentals_cache[r[0]] = {
                'net_margin': float(r[1]) if r[1] else None,
                'gross_margin': float(r[2]) if r[2] else None,
                'debt_ratio': float(r[3]) if r[3] else None,
                'revenue': float(r[4]) if r[4] else None,
                'net_profit': float(r[5]) if r[5] else None,
                'operating_cashflow': float(r[6]) if r[6] else None,
            }
        
        # 加载上市时间
        cur.execute("""
            SELECT ts_code, list_date
            FROM stock_basic_info
            WHERE list_date IS NOT NULL;
        """)
        for r in cur.fetchall():
            _list_date_cache[r[0]] = r[1]
        
        cur.close()
        conn.close()
        _indicators_loaded = True
        print(f"✓ 加载新增指标: 强势股{len(_strong_rank_cache)}只, 机构预期{len(_earnings_cache)}只, 概念{len(_concept_cache)}个, 估值{len(_valuation_cache)}只, 财务{len(_fundamentals_cache)}只, 上市时间{len(_list_date_cache)}只")
    except Exception as e:
        print(f"⚠️ 新增指标加载失败: {e}")


def get_strong_rank_bonus(ts_code: str) -> float:
    """获取强势股排名加分"""
    if ts_code not in _strong_rank_cache:
        return 0.0
    
    strong_ranks = _strong_rank_cache[ts_code]
    best_rank_bonus = 0.0
    
    for sr in strong_ranks:
        pos = sr['rank_position'] or 999
        if pos <= 10:
            bonus = 20
        elif pos <= 30:
            bonus = 10
        elif pos <= 50:
            bonus = 5
        else:
            bonus = 0
        best_rank_bonus = max(best_rank_bonus, bonus)
    
    consecutive_days = max((sr['consecutive_days'] or 0) for sr in strong_ranks)
    if consecutive_days >= 5:
        best_rank_bonus += 10
    elif consecutive_days >= 3:
        best_rank_bonus += 5
    
    return best_rank_bonus


def get_earnings_bonus(ts_code: str) -> float:
    """获取机构预期加分"""
    if ts_code not in _earnings_cache:
        return 0.0
    
    fc = _earnings_cache[ts_code]
    bonus = 0.0
    
    inst_count = fc['institution_count'] or 0
    if inst_count >= 10:
        bonus += 15
    elif inst_count >= 5:
        bonus += 8
    
    if fc['eps_mean'] and fc['industry_avg'] and fc['industry_avg'] > 0:
        eps_premium = (fc['eps_mean'] - fc['industry_avg']) / fc['industry_avg']
        if eps_premium > 0.1:
            bonus += 10
        elif eps_premium > 0.05:
            bonus += 5
    
    return bonus


def get_concept_bonus(ts_code: str) -> float:
    """获取概念板块加分"""
    if ts_code not in _stock_concept_map:
        return 0.0
    
    concepts = _stock_concept_map[ts_code]
    best_concept_bonus = 0.0
    
    for c in concepts:
        concept_data = _concept_cache.get(c['concept_code'])
        if concept_data:
            concept_pct = concept_data['pct_chg'] or 0
            if concept_pct > 3:
                bonus = 10
            elif concept_pct > 1:
                bonus = 5
            else:
                bonus = 0
            best_concept_bonus = max(best_concept_bonus, bonus)
    
    return best_concept_bonus


def get_valuation_bonus(ts_code: str) -> tuple:
    """获取PE/PB估值加分/扣分，返回(分数, 标签列表)"""
    if ts_code not in _valuation_cache:
        return 0.0, []
    
    val = _valuation_cache[ts_code]
    bonus = 0.0
    tags = []
    
    pe = val['pe_ratio']
    pb = val['pb_ratio']
    
    if pe is not None and pe > 0:
        if pe < 15:
            bonus += 15
            tags.append("低PE")
        elif pe < 30:
            bonus += 5
            tags.append("合理PE")
        elif pe < 60:
            pass
        elif pe < 100:
            bonus -= 10
            tags.append("高PE↓")
        else:
            bonus -= 20
            tags.append("极高PE↓")
    
    if pb is not None and pb > 0:
        if pb < 2:
            bonus += 10
            tags.append("低PB")
        elif pb < 5:
            bonus += 3
        elif pb < 10:
            pass
        else:
            bonus -= 10
            tags.append("高PB↓")
    
    return bonus, tags


def get_fundamentals_bonus(ts_code: str) -> tuple:
    """获取财务质量加分/扣分，返回(分数, 标签列表)"""
    if ts_code not in _fundamentals_cache:
        return 0.0, []
    
    fin = _fundamentals_cache[ts_code]
    bonus = 0.0
    tags = []
    
    net_margin = fin['net_margin']
    if net_margin is not None:
        if net_margin > 20:
            bonus += 10
            tags.append("高利润率")
        elif net_margin > 10:
            bonus += 5
        elif net_margin < 0:
            bonus -= 10
            tags.append("亏损↓")
    
    gross_margin = fin['gross_margin']
    if gross_margin is not None:
        if gross_margin > 40:
            bonus += 8
            tags.append("高毛利")
        elif gross_margin > 20:
            bonus += 3
        elif gross_margin < 10:
            bonus -= 5
            tags.append("低毛利↓")
    
    debt_ratio = fin['debt_ratio']
    if debt_ratio is not None:
        if debt_ratio < 40:
            bonus += 5
            tags.append("低负债")
        elif debt_ratio > 70:
            bonus -= 10
            tags.append("高负债↓")
    
    op_cashflow = fin['operating_cashflow']
    net_profit = fin['net_profit']
    if op_cashflow is not None and net_profit is not None and net_profit > 0:
        cashflow_ratio = op_cashflow / net_profit
        if cashflow_ratio > 1.2:
            bonus += 8
            tags.append("盈利质量高")
        elif cashflow_ratio < 0.5:
            bonus -= 5
            tags.append("盈利质量低↓")
    
    return bonus, tags


def is_new_stock(ts_code: str) -> bool:
    """判断是否为次新股（上市<60天）
    当指标缓存未加载时，返回 False（不拦截）并记录警告。
    """
    if not _indicators_loaded:
        if DEBUG:
            print(f"  [DEBUG] 次新股过滤: 指标缓存未加载，无法判断 {ts_code}")
        return False
    
    if ts_code not in _list_date_cache:
        return False
    
    list_date = _list_date_cache[ts_code]
    if list_date is None:
        return False
    
    from datetime import date
    today = date.today()
    
    # 处理list_date可能是字符串或date对象
    if isinstance(list_date, str):
        try:
            list_date = date.fromisoformat(list_date)
        except ValueError:
            return False
    
    return (today - list_date).days < 60


# ============================================================
#  1. 全局配置
# ============================================================
CONFIG_STABLE = {
    "MODE": "post",
    "POOL": "hs300+zz500",
    "min_amount": 50_000_000,
    "max_amount": 5_000_000_000,
    "min_mktcap": 10_000_000_000,
    "max_mktcap": 200_000_000_000,
    "vol_ratio_min": 1.5,
    "vol_ratio_max": 8.0,
    "stable_pct_lo": 3.0,
    "stable_pct_hi": 6.0,
    "upper_pct_lo": 6.0,
    "upper_pct_hi": 9.7,
    "turn_min": 5.0,
    "turn_max": 10.0,
    "streak_penalty_threshold": 3,
    "streak_penalty_per_board": 10,
    "score_threshold": 78,
    "sentiment_cold": 40,      # 情绪评分阈值：冷淡
    "sentiment_normal": 55,    # 情绪评分阈值：正常
    "sentiment_hot": 70,       # 情绪评分阈值：活跃
    "sentiment_fever": 85,     # 情绪评分阈值：火热/高潮
    "penalty_hot_turn": 12.0,
    "penalty_vol_ratio": 7.0,
    "penalty_ma_bias": 0.08,
    "position_ratio": "单票≤15%总仓位",
}

CONFIG_UPPER = {
    "MODE": "post",
    "POOL": "zz1000",
    "min_amount": 30_000_000,
    "max_amount": 3_000_000_000,
    "min_mktcap": 3_000_000_000,
    "max_mktcap": 30_000_000_000,
    "vol_ratio_min": 1.5,
    "vol_ratio_max": 10.0,
    "stable_pct_lo": 3.0,
    "stable_pct_hi": 6.0,
    "upper_pct_lo": 6.0,
    "upper_pct_hi": 9.7,
    "turn_min": 5.0,
    "turn_max": 10.0,
    "streak_penalty_threshold": 3,
    "streak_penalty_per_board": 10,
    "score_threshold": 78,
    "sentiment_cold": 40,      # 情绪评分阈值：冷淡
    "sentiment_normal": 55,    # 情绪评分阈值：正常
    "sentiment_hot": 70,       # 情绪评分阈值：活跃
    "sentiment_fever": 85,     # 情绪评分阈值：火热/高潮
    "penalty_hot_turn": 12.0,
    "penalty_vol_ratio": 7.0,
    "penalty_ma_bias": 0.08,
    "position_ratio": "单票≤8%总仓位，严守止损",
}

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

# 自动判断 MODE：15:10 后为 post，14:00-15:10 为 realtime，其余为 pre_market
_now = beijing_now()
if DEBUG:
    print(f"  [DEBUG] 服务器时间: {datetime.now()}, 北京时间: {_now.strftime('%Y-%m-%d %H:%M:%S')}")
if _now.hour > 15 or (_now.hour == 15 and _now.minute >= 10):
    CONFIG_STABLE["MODE"] = "post"
    CONFIG_UPPER["MODE"] = "post"
elif _now.hour >= 14:
    # 14:00-15:10 为盘中尾盘
    CONFIG_STABLE["MODE"] = "realtime"
    CONFIG_UPPER["MODE"] = "realtime"
else:
    # 其他时间为盘前
    CONFIG_STABLE["MODE"] = "pre_market"
    CONFIG_UPPER["MODE"] = "pre_market"
if DEBUG:
    print(f"  [DEBUG] MODE: {CONFIG_STABLE['MODE']}")

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
def get_time_weight(mode: str = "post") -> float:
    if mode == "post":
        return 1.0

    now = beijing_now()
    h, m = now.hour, now.minute

    if h < 9 or (h == 9 and m < 30):
        return 0.0
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
    获取多维市场情绪指标（东财接口）。
    返回 (综合情绪分数, 情绪描述)。
    
    情绪维度：
    1. 涨停家数 - 市场热度核心指标
    2. 涨跌家数比 - 整体赚钱效应
    3. 封板率 - 打板成功率
    4. 连板高度 - 市场高度标杆
    5. 炸板率 - 情绪衰竭信号
    6. 跌停家数 - 风险释放程度
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://quote.eastmoney.com/ztb/detail"
    }
    
    # 三个 eastmoney 接口分别 try/except，任一失败不影响其他维度
    today_ymd = beijing_now().strftime('%Y%m%d')
    today_dash = beijing_now().strftime('%Y-%m-%d')

    zt_count = 0
    dt_count = 0
    streak_list = []
    explode_count = 0
    zt_ok = False
    market_ok = False
    dt_ok = False

    # 1. 涨停池
    try:
        url = f"https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&Pagesize=500&sort=fbt%3Aasc&date={today_ymd}&_={int(time.time() * 1000)}"
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        if data.get('data') and data['data'].get('pool'):
            pool = data['data']['pool']
            zt_count = len(pool)
            for stock in pool:
                streak = stock.get('zdf', 0)
                if isinstance(streak, int) and streak > 0:
                    streak_list.append(streak)
                if 'np' in stock and 'zdf' in stock:
                    try:
                        current_pct = float(stock.get('np', '0'))
                        limit_pct = float(stock.get('zdf', '10'))
                        if current_pct < limit_pct * 0.9:
                            explode_count += 1
                    except (ValueError, TypeError):
                        pass
            if zt_count > 0:
                sample = pool[:3]
                sample_names = [s.get('n', '') for s in sample]
                print(f"  [DEBUG] 涨停池: {zt_count}家, 示例: {', '.join(sample_names)}")
        zt_ok = True
    except Exception as e:
        print(f"  ⚠️ 涨停池接口失败: {e}（其他维度仍可用）")

    try:
        # 2. 涨跌家数
        up_count = 1500
        down_count = 1500
        try:
            market_url = "https://push2.eastmoney.com/api/qt/stock/get?secid=0.000001&fields=f57,f58,f116,f117,f118,f119,f120,f121"
            r = requests.get(market_url, headers=headers, timeout=10)
            market_data = r.json()
            if market_data.get('data'):
                raw_up = market_data['data'].get('f116', 1500)
                raw_down = market_data['data'].get('f117', 1500)
                # 数据验证：涨跌家数应该在合理范围内（100-6000）
                if isinstance(raw_up, (int, float)) and 100 <= raw_up <= 6000:
                    up_count = int(raw_up)
                else:
                    print(f"  ⚠️ 涨跌家数数据异常: f116={raw_up}，使用默认值1500")
                if isinstance(raw_down, (int, float)) and 100 <= raw_down <= 6000:
                    down_count = int(raw_down)
                else:
                    print(f"  ⚠️ 涨跌家数数据异常: f117={raw_down}，使用默认值1500")
                market_ok = True
        except Exception as e:
            print(f"  ⚠️ 涨跌家数接口失败: {e}（其他维度仍可用）")

        # 3. 跌停池
        try:
            dt_url = f"https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.dtzt&Pageindex=0&Pagesize=200&sort=fbt%3Aasc&date={today_ymd}&_={int(time.time() * 1000)}"
            r = requests.get(dt_url, headers=headers, timeout=10)
            dt_data = r.json()
            if dt_data.get('data') and dt_data['data'].get('pool'):
                dt_count = len(dt_data['data']['pool'])
            dt_ok = True
        except Exception as e:
            print(f"  ⚠️ 跌停池接口失败: {e}（其他维度仍可用）")

        # 涨停池失败时：返回保守 55 分，避免 zt_count=0 被误判为"极冷"
        if not zt_ok:
            print("=" * 60)
            print("  ⚠️ 涨停池接口失败，无法可靠计算情绪。返回保守 fallback (55, '正常·涨停池异常')")
            print("  ⚠️ 不要相信 8步法在此次扫描里的情绪驱动过滤/排序")
            print("=" * 60)
            return 55, "正常·涨停池异常"

        # 计算派生指标
        max_streak = max(streak_list) if streak_list else 2
        seal_rate = (zt_count - explode_count) / max(zt_count, 1)  # 封板率
        explode_rate = explode_count / max(zt_count, 1)  # 炸板率
        up_down_ratio = up_count / max(down_count, 1)  # 涨跌比
        avg_zt_pct = 9.5  # 平均涨停强度（简化处理）
        
        # 打印多维情绪指标
        print(f"\n  📊 多维情绪指标:")
        print(f"    涨停家数: {zt_count} | 跌停家数: {dt_count}")
        print(f"    上涨家数: {up_count} | 下跌家数: {down_count} | 涨跌比: {up_down_ratio:.2f}")
        print(f"    封板率: {seal_rate:.1%} | 炸板率: {explode_rate:.1%}")
        print(f"    最高连板: {max_streak}板")
        
        # 综合情绪评分（0-100分）
        # 各维度权重：涨停家数(25%) + 涨跌比(20%) + 封板率(20%) + 连板高度(15%) + 炸板率(15%) + 跌停(5%)
        # 使用 sqrt 对数压缩，避免极端值线性扭曲总分
        import math
        score = 50  # 基础分
        
        # 涨停家数评分（sqrt压缩：50→7.9, 100→11.2, 200→15.8, 500→25）
        zt_score = min(math.sqrt(zt_count) / math.sqrt(500) * 25, 25) if zt_count > 0 else 0
        score += zt_score
        
        # 涨跌比评分（sqrt压缩）
        ratio_score = min(math.sqrt(max(up_down_ratio, 0.1)) / math.sqrt(5) * 20, 20)
        score += ratio_score
        
        # 封板率评分（线性，封板率本身就是0-1归一化值）
        seal_score = seal_rate * 20
        score += seal_score
        
        # 连板高度评分（sqrt压缩：3板→10.4, 5板→13.4, 10板→19）
        streak_score = min(math.sqrt(max_streak) / math.sqrt(15) * 15, 15) if max_streak > 0 else 0
        score += streak_score
        
        # 炸板率扣分
        explode_penalty = explode_rate * 15
        score -= explode_penalty
        
        # 跌停家数扣分
        dt_penalty = min(dt_count * 0.5, 5)
        score -= dt_penalty
        
        score = max(0, min(100, score))
        
        # 情绪等级判定
        if score < 25:
            mood = "极冷"
        elif score < 40:
            mood = "冷淡"
        elif score < 55:
            mood = "正常"
        elif score < 70:
            mood = "活跃"
        elif score < 85:
            mood = "火热"
        else:
            mood = "高潮"
            
        print(f"    综合情绪分: {score:.1f} | 情绪状态: {mood}")
        return int(score), mood
        
    except Exception as e:
        # 情绪接口失败时，用一个明确"刚好通过情绪冷淡门槛"的中性分数（55）。
        # 旧版用 default_values 计算会得到 ~93 分（火热）误导后续逻辑，使
        # 实际冷淡的市场被当作火热放开高位路径。这里改成保守且可识别的分数，
        # 并在 mood 字符串里嵌入"·接口异常"使 Telegram 能直接看出数据可疑。
        print("=" * 60)
        print(f"  ⚠️⚠️⚠️ 市场情绪接口异常: {e}")
        print(f"  ⚠️ 使用保守 fallback (score=55, '正常·接口异常')")
        print(f"  ⚠️ 注意：此时不应信任情绪驱动的过滤/排序，建议手动检查")
        print("=" * 60)
        return 55, "正常·接口异常"


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
                        "name": name,
                        "mktcap": _f(44) * 1e8 if _f(44) > 0 else 0,
                        "circ_mcap": _f(45) * 1e8 if _f(45) > 0 else 0,
                        "amplitude": _f(43),
                        "volume_ratio": _f(49),
                        "commission_ratio": _f(50),
                        "large_order_net": _f(51),
                        "main_force_net": _f(52) * 10000 if _f(52) > 0 else 0,
                        "limit_up_price": _f(47),
                        "limit_down_price": _f(48),
                    }
                    ok_count += 1
                except Exception:
                    continue

        except Exception:
            continue

        time.sleep(0.12)

    print(f"行情获取: 成功={ok_count} 只")
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


def get_stock_pool(pool_name: str = None) -> list:
    if pool_name is None:
        pool_name = CONFIG_STABLE["POOL"]
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
    sentiment_score: int,
    time_weight: float,
    cfg: dict,
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

    # --- LLM 票放宽过滤标志（v1.5.1）---
    # LLM 多源策略推荐的标的，硬过滤分别放宽：
    #   涨幅 1.5-12% (vs 3-6%/6-9.7%)
    #   成交额 ≥5000万 (vs 200亿/100亿下限)
    #   换手率 3-15% (vs 5-10%)
    # 量比/均线/压力/乖离 等核心 8步法逻辑保持不变，确保二次确认仍然有效
    _is_llm_for_filter, _ = is_llm_candidate(code)

    # --- STEP 1: 涨幅筛选 ---
    if _is_llm_for_filter:
        in_stable = 1.5 <= curr_pct <= cfg["stable_pct_hi"]
        in_upper = cfg["stable_pct_hi"] < curr_pct <= 12.0
    else:
        in_stable = cfg["stable_pct_lo"] <= curr_pct <= cfg["stable_pct_hi"]
        in_upper = cfg["upper_pct_lo"] <= curr_pct <= cfg["upper_pct_hi"]

    if sentiment_score < cfg["sentiment_normal"] and not in_stable:
        if reject_stats is not None:
            reject_stats["情绪冷淡"] += 1
        return None

    if not (in_stable or in_upper):
        if reject_stats is not None:
            reject_stats["涨幅不符"] += 1
        return None

    # --- STEP 2: 成交额硬过滤 ---
    min_amount = 50_000_000 if _is_llm_for_filter else cfg["min_amount"]
    if curr_amount < min_amount or curr_amount > cfg["max_amount"]:
        if reject_stats is not None:
            reject_stats["成交额"] += 1
        return None

    # --- STEP 3: 换手率硬过滤（8步法原始5%-10%）---
    if _is_llm_for_filter:
        turn_min, turn_max = 3.0, 15.0
    else:
        turn_min, turn_max = cfg["turn_min"], cfg["turn_max"]
    if curr_turn < turn_min or curr_turn > turn_max:
        if reject_stats is not None:
            reject_stats["换手率"] += 1
        return None

    # --- STEP 4: 流通市值过滤（8步法50亿-200亿）---
    # v1.1: 去掉 MODE 限制，post 模式也启用市值过滤
    # v1.5.1: 修复市值估算逻辑，腾讯 f44/f45 单位为亿元
    # v1.6.2: 优先使用流通市值(circ_mcap)，其次总市值(mktcap)
    circ_mcap = real_info.get("circ_mcap", 0) if real_info else 0
    mktcap = real_info.get("mktcap", 0) if real_info else 0
    
    # 优先使用流通市值，如果缺失则用总市值，最后用估算
    if circ_mcap > 0:
        mktcap = circ_mcap
    elif mktcap <= 0 and curr_amount > 0 and curr_turn > 0:
        # curr_amount 单位为元, curr_turn 单位为百分比
        # 流通市值(元) = 成交额(元) / (换手率% / 100)
        mktcap = curr_amount / (curr_turn / 100.0)
    
    if mktcap > 0:
        if mktcap < cfg["min_mktcap"] or mktcap > cfg["max_mktcap"]:
            if reject_stats is not None:
                reject_stats["市值"] += 1
            return None
    else:
        # 市值数据完全无法获取，拒绝该股票
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
    if not (cfg["vol_ratio_min"] <= vol_ratio <= cfg["vol_ratio_max"]):
        if reject_stats is not None:
            reject_stats["量比"] += 1
        return None

    # --- STEP 6a: 均线验证（昨收序列，严格无未来函数）---
    # post 模式：今日收盘已确定，纳入MA计算更准确
    # realtime 模式：今日价格仍在变动，排除今日收盘
    if cfg["MODE"] == "post" and curr_price > 0:
        hist_close = df["close"].tolist()
        if hist_close:
            hist_close[-1] = curr_price
    else:
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
        # 条件1：连续增长检测（允许小幅回撤）
        increasing_count = 0
        for j in range(1, len(recent_5d_vols)):
            if recent_5d_vols[j] >= recent_5d_vols[j - 1] * 0.9:
                increasing_count += 1
        meets_continuous = increasing_count >= len(recent_5d_vols) - 2
        
        # 条件2：整体放量趋势（今日量能相对于前期均值有明显增长）
        if len(hist_vols) >= 10:
            avg_vol_recent = sum(hist_vols[-10:-5]) / 5  # 5-10日前的平均量能
            today_ratio = est_full_vol / avg_vol_recent if avg_vol_recent > 0 else 0
            meets_trend = today_ratio >= 1.3  # 今日量能至少是前期均值的1.3倍
        else:
            meets_trend = True  # 数据不足时跳过此条件
        
        # 条件3：今日量能必须大于前一日
        meets_today = est_full_vol >= hist_vols[-1] * 0.95 if hist_vols else True
        
        vol_increasing = meets_continuous and meets_trend and meets_today

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
        if cfg["MODE"] == "post":
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
    elif streak >= cfg["streak_penalty_threshold"]:
        bonus = 30
        penalty = (streak - 2) * cfg["streak_penalty_per_board"]
        net = bonus - penalty
        score += max(net, 0)
        tags.append(f"{streak}连板(高度风险)")

    # F. 情绪周期判断（逆向思维）- 基于多维情绪评分
    # sentiment_score 是综合情绪分数(0-100)
    if sentiment_score >= cfg["sentiment_fever"]:
        # 高潮期(≥85分)：风险最高，大幅扣分
        score -= 15
        tags.append("情绪高潮↓↓")
    elif sentiment_score >= cfg["sentiment_hot"]:
        # 活跃期(≥70分)：适当扣分
        score -= 5
        tags.append("情绪偏热↓")
    elif sentiment_score >= cfg["sentiment_normal"]:
        # 正常期(≥55分)：小幅加分
        score += 3
        tags.append("情绪健康+")
    elif sentiment_score >= cfg["sentiment_cold"]:
        # 冷淡期(≥40分)：观望态度，不加分也不扣分
        tags.append("情绪观望")
    else:
        # 极冷期(<40分)：市场风险高，扣分
        score -= 10
        tags.append("情绪极冷↓")

    # --- 风险扣分 ---
    if curr_turn > cfg["penalty_hot_turn"]:
        score -= 20
        tags.append("换手过热↓")

    if vol_ratio > cfg["penalty_vol_ratio"]:
        score -= 15
        tags.append("量比过激↓")
        # 移除可能已加的"爆量博弈"标签，避免自相矛盾
        if "爆量博弈" in tags:
            tags.remove("爆量博弈")

    # 乖离惩罚：阶梯加重扣分（防追高）
    bias_ma5 = (curr_price - ma5_yest) / ma5_yest if ma5_yest > 0 else 0
    bias_threshold = 0.08
    if in_upper:
        bias_threshold = 0.12
    # 根据情绪动态调整乖离阈值
    if mood in ("火热", "高潮"):
        bias_threshold += 0.03
    elif mood == "冷淡":
        bias_threshold -= 0.02
    bias_excess = bias_ma5 - bias_threshold
    if bias_excess > 0:
        if bias_excess <= 0.02:  # 超出阈值 0-2%
            score -= 15
            tags.append(f"乖离偏大({bias_ma5*100:.1f}%)↓")
        elif bias_excess <= 0.05:  # 超出 2-5%
            score -= 30
            tags.append(f"乖离过大({bias_ma5*100:.1f}%)↓↓")
        else:  # 超出 5%+，直接剔除
            if reject_stats is not None:
                reject_stats["乖离严重"] += 1
            return None

    # 行业评分：根据行业分类进行加分或扣分
    industry = get_stock_industry(code)
    industry_bonus, industry_category, industry_tags = analyze_industry(industry)
    if industry_bonus != 0:
        score += industry_bonus
        if industry_bonus > 0:
            tags.append(f"热门赛道(+{industry_bonus})")
        else:
            tags.append(f"{industry_tags[0]}({industry_bonus})")

    # 尾盘回落检测：post模式下检查今日是否从高点大幅回落
    if cfg["MODE"] == "post":
        today_high = float(df.iloc[-1]["high"]) if "high" in df.columns else 0
        if today_high > 0 and curr_price > 0:
            drawdown_from_high = (today_high - curr_price) / today_high
            if drawdown_from_high > 0.03:
                score -= 15
                tags.append("尾盘回落↓")

    # --- 细粒度排序因子（区分度增强）---
    fine_score = 0

    # F1. 成交额放大度（今日成交额 vs 5日均值）
    # 两者均为元: curr_amount=腾讯_f(37)*10000, baostock amount 原始为元
    if curr_amount > 0 and "amount" in df.columns and len(df) >= 5:
        hist_amounts = df["amount"].astype(float)
        avg_amount_5d = hist_amounts.iloc[-5:].mean()
        if avg_amount_5d > 0:
            amount_ratio = curr_amount / avg_amount_5d
            if amount_ratio > 3.0:
                fine_score += 5
                tags.append("成交额三倍量")
            elif amount_ratio < 0.8:
                fine_score -= 3
                tags.append("成交额萎缩↓")

    # F2. 距离涨停的远近（高位路径才用）
    if in_upper:
        distance_to_limit = limit_pct - curr_pct
        if distance_to_limit < 1.0:
            fine_score += 5
            tags.append("濒临涨停")
        elif distance_to_limit < 2.0:
            fine_score += 3

    # F3. 收盘价是否守住5日均线（post模式独有）—— 只扣分不加分
    if cfg["MODE"] == "post":
        if curr_price < ma5_yest:
            fine_score -= 5
            tags.append("破MA5↓")

    score += fine_score

    # --- LLM候选池加成（v1.5+）---
    is_llm_cand, llm_info = is_llm_candidate(code)
    llm_boost = 0.0
    quant_score_before_llm = score
    if is_llm_cand:
        llm_boost = get_llm_boost_score(code)
        score += llm_boost
        llm_tags = []
        llm_tags.append(f"LLM候选+{llm_boost:.0f}")
        if llm_info.get('source_diversity', 0) >= 3:
            llm_tags.append("共识强化")
        tags.extend(llm_tags)
        tags.append("🤖LLM")

    # --- 新增指标加成（v1.6+）---
    # 振幅评分：3%-8%为活跃区间，加分
    amplitude = real_info.get('amplitude', 0) if real_info else 0
    if 3 <= amplitude <= 8:
        score += 10 * (1 - abs(amplitude - 5.5) / 2.5)
        tags.append("振幅活跃")
    elif amplitude > 8:
        score += 5
        tags.append("振幅偏大")

    # 量比确认：腾讯瞬时量比作为辅助验证（B部分已做主力评分，此处仅微调±3）
    volume_ratio = real_info.get('volume_ratio', 0) if real_info else 0
    if volume_ratio > 1.5:
        score += min(3, (volume_ratio - 1.5) * 2)
        tags.append("放量确认")
    elif volume_ratio < 0.8:
        score -= 3
        tags.append("缩量↓")

    # 委比评分：委比>0表示买盘强，加分
    commission_ratio = real_info.get('commission_ratio', 0) if real_info else 0
    if commission_ratio > 0:
        score += min(10, commission_ratio / 10)
        tags.append("买盘强劲")
    elif commission_ratio < -20:
        score -= 5
        tags.append("卖压大↓")

    # 大单净量评分：大单净量>0表示主力买入
    large_order_net = real_info.get('large_order_net', 0) if real_info else 0
    if large_order_net > 0:
        score += min(15, large_order_net * 5)
        tags.append("大单净流入")
    elif large_order_net < -5:
        score -= 10
        tags.append("大单净流出↓")

    # 主力资金净流入评分
    main_force_net = real_info.get('main_force_net', 0) if real_info else 0
    if main_force_net > 0:
        score += min(10, main_force_net / 1e6 * 2)
        tags.append("主力流入")
    elif main_force_net < -1e7:
        score -= 5
        tags.append("主力流出↓")

    # 强势股加分
    ts_code = baostock_to_standard(code) if 'baostock_to_standard' in globals() else code
    strong_bonus = get_strong_rank_bonus(ts_code)
    if strong_bonus > 0:
        score += strong_bonus
        tags.append(f"强势股+{strong_bonus:.0f}")

    # 机构预期加分
    earnings_bonus = get_earnings_bonus(ts_code)
    if earnings_bonus > 0:
        score += earnings_bonus
        tags.append(f"机构预期+{earnings_bonus:.0f}")

    # 概念板块加分
    concept_bonus = get_concept_bonus(ts_code)
    if concept_bonus > 0:
        score += concept_bonus
        tags.append(f"热门概念+{concept_bonus:.0f}")

    # PE/PB估值加分
    val_bonus, val_tags = get_valuation_bonus(ts_code)
    if val_bonus != 0:
        score += val_bonus
        tags.extend(val_tags)

    # 财务质量加分
    fin_bonus, fin_tags = get_fundamentals_bonus(ts_code)
    if fin_bonus != 0:
        score += fin_bonus
        tags.extend(fin_tags)

    # --- 最终判定 ---
    # v1.5.1: 评分上限120分，防止过度叠加导致评分膨胀
    score = min(score, 120)
    if score < cfg["score_threshold"]:
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
        "quant_score": quant_score_before_llm,
        "path": "稳健" if in_stable else "高位",
        "tags": " | ".join(tags),
        "is_llm": is_llm_cand,
        "llm_final_score": llm_info.get('final_score', 0) if is_llm_cand else 0,
    }


# ============================================================
#  7. 单池扫描引擎
# ============================================================
def scan_pool(cfg: dict, sentiment_score: int, mood: str, preloaded: bool = False) -> List[dict]:
    time_weight = get_time_weight(cfg["MODE"])
    pool_name = cfg["POOL"]
    mode = cfg["MODE"]

    print(f"\n扫描池: [{pool_name}]  模式: {mode}  时间权重: {time_weight:.2f}")
    print()

    stock_pool = get_stock_pool(cfg["POOL"])
    print(f"股票池: [{cfg['POOL']}] 共 {len(stock_pool)} 只")

    # v1.5+: 加载LLM候选池（最近 3 天盘后产出，覆盖周末断档）
    today_str = beijing_now().strftime("%Y-%m-%d")
    llm_pool = get_llm_candidates_from_supabase(today_str, lookback_days=3, min_score=40.0)
    llm_count = len(llm_pool)
    if llm_count > 0:
        print(f"📊 LLM候选池: {llm_count} 只 (最近 3 日盘后 selected/分数≥40)")
        # 把 LLM 候选码合并进 stock_pool，避免基础池外的 LLM 票被漏扫
        existing = set(stock_pool)
        added = [c for c in llm_pool.keys() if c not in existing]
        if added:
            stock_pool = stock_pool + added
            print(f"  ➕ 合并 LLM 候选 {len(added)} 只到扫描池 (合并后: {len(stock_pool)} 只)")

    # v1.6+: 加载新增指标（强势股排名、机构预期、概念板块）
    load_new_indicators(today_str)

    # 预热行业缓存（首次或7天过期时批量查询），preloaded=True 时跳过
    if not preloaded:
        preload_industries(stock_pool)

    # post 模式也用腾讯接口获取收盘数据（baostock 历史数据有延迟）
    real_map = get_realtime_quotes(stock_pool)
    print(f"实时行情获取: {len(real_map)} 只")

    # 构建名称映射：key -> stock_name
    name_map = {}
    for key, info in real_map.items():
        if info and info.get("name"):
            name_map[key] = info["name"]

    results = []
    total = len(stock_pool)
    end_d = beijing_now().strftime("%Y-%m-%d")
    start_d = (beijing_now() - timedelta(days=45)).strftime("%Y-%m-%d")

    reject_stats = {
        "数据不足": 0, "ST/退市": 0, "情绪冷淡": 0, "涨幅不符": 0,
        "成交额": 0, "换手率": 0, "市值": 0, "量比": 0, "均线": 0,
        "压力": 0, "乖离严重": 0, "得分不足": 0,
    }

    print(f"开始扫描 {total} 只股票...")

    for code in stock_pool:
        # 次新股过滤：上市<60天直接跳过
        ts_code = baostock_to_standard(code) if 'baostock_to_standard' in globals() else code
        if is_new_stock(ts_code):
            continue
        
        key = code.replace(".", "").lower()
        real_info = real_map.get(key)
        if real_info is None:
            continue
        pct = real_info.get("pct", 0)
        # LLM 票预过滤用更宽的 1.5-12% 区间，与 analyze_ultimate 内部逻辑一致
        _is_llm_pre, _ = is_llm_candidate(code)
        if _is_llm_pre:
            if not (1.5 <= pct <= 12.0):
                continue
        else:
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
        res = analyze_ultimate(hist_df, code, real_info, sentiment_score, time_weight, cfg, reject_stats, mood)
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

    # 计算通过数量
    passed_count = len(results)
    total_scanned = sum(reject_stats.values()) + passed_count

    print(f"\n━━ 过滤统计，[{pool_name}]，共{total_scanned}只 ━━")
    _print_funnel(reject_stats, total_scanned, results, cfg)

    return results, reject_stats, name_map, len(stock_pool), len(real_map), time_weight


# ============================================================
#  7.5 追加结果到汇总文件
# ============================================================
def append_to_summary(
    final_df: pd.DataFrame,
    end_d: str,
    sentiment_score: int,
    mood: str,
    total_candidates: int,
    mode_label: str = "盘后定稿",
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

    if not mode_label:
        mode_label = "盘后定稿"
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
    lines.append(f"情绪: {mood}({sentiment_score}分)  扫描总量: {total_candidates}只")
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

def _format_funnel(rejects: dict, total: int, results: list = None, cfg: dict = None) -> str:
    """按8步法顺序格式化漏斗式过滤统计，返回字符串"""
    if total == 0:
        total = sum(rejects.values())
    
    # 从配置中读取阈值（如果没有传入 cfg，使用默认值）
    if cfg:
        min_amount_stable = cfg.get("min_amount", 50_000_000) / 100_000_000
        max_amount_stable = cfg.get("max_amount", 5_000_000_000) / 100_000_000
        min_amount_upper = CONFIG_UPPER.get("min_amount", 30_000_000) / 100_000_000
        max_amount_upper = CONFIG_UPPER.get("max_amount", 3_000_000_000) / 100_000_000
    else:
        min_amount_stable = 0.5
        max_amount_stable = 50
        min_amount_upper = 0.3
        max_amount_upper = 30
    
    steps = [
        ("涨幅筛选", "3%-5%/6%-9.7%", ["涨幅不符"]),
        ("成交额", f"{min_amount_stable}-{max_amount_stable}亿/{min_amount_upper}-{max_amount_upper}亿", ["成交额"]),
        ("换手率", ">=5%,<=10%", ["换手率"]),
        ("市值过滤", "50-500亿/30-200亿", ["市值"]),
        ("量比", ">=1", ["量比"]),
        ("均线+压力检测", "", ["均线", "压力"]),
        ("乖离严重", "超阈值5%+", ["乖离严重"]),
        ("得分不足", "低于阈值", ["得分不足"]),
    ]

    lines = []
    remaining = total
    for step_name, condition, keys in steps:
        count = sum(rejects.get(k, 0) for k in keys)
        if count > 0:
            remaining -= count
            cond_str = f"（{condition}）" if condition else ""
            lines.append(f"  ✗ {step_name}{cond_str} 筛选剩 {max(0, remaining)} 只")

    known_keys = set()
    for _, _, keys in steps:
        known_keys.update(keys)
    other_count = sum(v for k, v in rejects.items() if k not in known_keys and v > 0)
    if other_count > 0:
        # 列出"其他"中的具体原因
        other_reasons = [f"{k}: {v}只" for k, v in rejects.items() if k not in known_keys and v > 0]
        remaining -= other_count
        lines.append(f"  ✗ 其他 [{', '.join(other_reasons)}] 筛选剩 {max(0, remaining)} 只")

    lines.append(f"  ✓ 通过: {max(0, remaining)} 只")

    if results:
        for r in results:
            lines.append(
                f"  {r['code']:<14} 涨幅:{r['pct']:>6.2f}%  "
                f"路径:{r['path']}  连板:{r['streak']}  得分:{r['score']}  "
                f"换手:{r['turn']}%  量比:{r['vol_ratio']}"
            )

    return "\n".join(lines)


def _print_funnel(rejects: dict, total: int, results: list = None, cfg: dict = None):
    """按8步法顺序打印漏斗式过滤统计"""
    print(_format_funnel(rejects, total, results, cfg))


def _print_reject_summary(rejects: dict, total: int = 0, cfg: dict = None):
    """按8步法顺序打印漏斗式过滤统计（汇总用）"""
    print(_format_funnel(rejects, total, cfg=cfg))

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
    print(f"隔夜选股法·最优融合版 v1.5.5")
    print(f"双池策略：稳健[hs300+zz500] + 高位[zz1000]")
    print(f"完整8步法：涨幅→量比→换手→市值→量能→均线→压力→评分")
    print(f"运行时间：{beijing_now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    lg = bs.login()
    if lg.error_code != "0":
        print(f"❌ baostock 登录失败: {lg.error_msg}")
        return

    if CONFIG_STABLE["MODE"] == "post" and not is_safe_post_time():
        print("⚠️ 当前不是安全的盘后时间,数据可能不是最终收盘价")
        print("   建议 15:10 之后再运行 post 模式")

    sentiment_score, mood = fetch_market_sentiment()
    print(f"\n📊 市场情绪: 综合评分 {sentiment_score} 分 → [{mood}]")

    if mood in ("极冷", "冷淡"):
        print("情绪冷淡，仅启用稳健路径(3%-6%)，高位路径自动关闭")
    elif mood in ("活跃", "火热", "高潮"):
        print("情绪偏热，高位路径(6%-9.7%)已开放，注意风控")
    else:
        print("情绪正常，双路径运行")

    print(f"稳健路径仓位: {CONFIG_STABLE['position_ratio']}")
    print(f"高位路径仓位: {CONFIG_UPPER['position_ratio']}")

    end_d = beijing_now().strftime("%Y-%m-%d")

    results_stable, rejects_stable, name_map_stable, stable_pool_size, stable_real_count, stable_tw = scan_pool(CONFIG_STABLE, sentiment_score, mood, preloaded=False)

    results_upper, rejects_upper, name_map_upper, upper_pool_size, upper_real_count, upper_tw = scan_pool(CONFIG_UPPER, sentiment_score, mood, preloaded=True)

    # 合并名称映射
    all_name_map = {**name_map_stable, **name_map_upper}

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

    print()
    print(f"隔夜选股法·最优精选清单  ({end_d})  情绪: {mood}({sentiment_score}分)")
    print()

    if not all_results:
        print("\n  今日暂无符合条件的标的。")
        _print_reject_summary(total_rejects, cfg=CONFIG_STABLE)
        _save_reject_trend(end_d, total_rejects)
        # 零候选场景下也写一行审计，selected=FALSE 不影响下游查询，但留下"今天扫过、什么都没看上"的痕迹
        _persist_zero_result_audit(end_d, total_rejects, sentiment_score, mood)
        return

    final_df = (
        pd.DataFrame(list(all_results.values()))
        .sort_values(by=["score", "vol_ratio"], ascending=False)
        .reset_index(drop=True)
    )

    # 情绪高潮时推荐数自动压缩（逆向思维）
    final_count_limit = 5
    if sentiment_score >= CONFIG_STABLE["sentiment_fever"]:  # >=85分 高潮
        final_count_limit = 3
        print(f"\n市场高潮(情绪分≥85),推荐数压缩到 {final_count_limit} 只,提示防顶部")
    elif sentiment_score >= CONFIG_STABLE["sentiment_hot"]:  # >=70分 活跃
        final_count_limit = 4
        print(f"\n市场偏热(情绪分≥70),推荐数压缩到 {final_count_limit} 只")

    stable_picks = final_df[final_df["path"] == "稳健"].head(final_count_limit)
    upper_picks = final_df[final_df["path"] == "高位"].head(final_count_limit)

    total_candidates = len(results_stable) + len(results_upper)
    total_scanned = sum(total_rejects.values()) + total_candidates

    # 打印汇总过滤统计
    print(f"\n今日扫描汇总: {total_scanned} 只")
    print(f"━━ 过滤统计，共{total_scanned}只 ━━")
    _print_reject_summary(total_rejects, total_scanned, cfg=CONFIG_STABLE)
    _save_reject_trend(end_d, total_rejects)

    # 14:30 盘中初筛 与 15:10 盘后定稿都允许推送和写汇总文件，
    # DB 写入仍只在盘后定稿，避免盘中不稳定数据污染 daily_candidates
    current_beijing = beijing_now()
    is_post_time = current_beijing.hour > 15 or (current_beijing.hour == 15 and current_beijing.minute >= 10)
    is_pre_market = current_beijing.hour < 14 and not (current_beijing.hour == 9 and current_beijing.minute >= 30)
    
    if is_post_time:
        mode_label = "盘后定稿"
    elif is_pre_market:
        mode_label = "盘前预览"
    else:
        mode_label = "盘中初筛"

    append_to_summary(final_df, end_d, sentiment_score, mood, total_candidates, mode_label)

    # 14:30 写 run_mode='intraday'（用于 15:10 计算 diff），15:10 写 run_mode='afternoon'（最终版本）
    persist_run_mode = 'afternoon' if is_post_time else 'intraday'
    n_db = _persist_to_daily_candidates(
        stable_picks, upper_picks, end_d, all_name_map, run_mode=persist_run_mode,
    )
    if n_db:
        print(f"✓ 写入 {n_db} 条到 daily_candidates (source=overnight_8step, run_mode={persist_run_mode}, snapshot_date={end_d})")

    # 15:10 盘后定稿时计算与 14:30 的 diff
    diff_summary = ""
    if is_post_time:
        intraday_codes = _read_intraday_picks(end_d)
        if intraday_codes:
            current_codes = {baostock_to_standard(row['code']) for _, row in pd.concat([stable_picks, upper_picks]).iterrows()}
            added = current_codes - intraday_codes
            removed = intraday_codes - current_codes
            kept = current_codes & intraday_codes
            if added or removed:
                parts = [f"📊 相比 14:30 盘中初筛 ({len(intraday_codes)} 只)"]
                if added:
                    parts.append(f"  ➕ 新增: {len(added)} 只 ({', '.join(sorted(added))})")
                if removed:
                    parts.append(f"  ➖ 剔除: {len(removed)} 只 ({', '.join(sorted(removed))})")
                if kept:
                    parts.append(f"  ✓ 维持: {len(kept)} 只")
                diff_summary = "\n".join(parts)
                print(f"\n{diff_summary}")
            else:
                diff_summary = f"📊 与 14:30 盘中初筛完全一致 ({len(current_codes)} 只)"
                print(f"\n{diff_summary}")

    # 打印推荐单（盘中和盘后都显示）
    for path_label, picks in [("稳健", stable_picks), ("高位", upper_picks)]:
        if picks.empty:
            continue

        pos_hint = CONFIG_UPPER["position_ratio"] if path_label == "高位" else CONFIG_STABLE["position_ratio"]
        print(f"\n{path_label}路径 ({len(picks)} 只)  {pos_hint}")
        print(f"{'代码':<14} {'价格':>7} {'涨幅%':>7} {'量比':>6} "
              f"{'换手%':>7} {'连板':>5} {'乖离%':>7} {'得分':>5}  特征")
        for _, row in picks.iterrows():
            print(
                f"{row['code']:<14} {row['price']:>7.2f} "
                f"{row['pct']:>7.2f} {row['vol_ratio']:>6.2f} {row['turn']:>7.2f} "
                f"{row['streak']:>5} {row['bias_ma5']:>7.2f} {row['score']:>5}  {row['tags']}"
            )

    print(f"\n操作指引")
    print(f"稳健路径(hs300+zz500)：仓位≤15%，次日09:35未维持昨收+1%即出")
    print(f"高位路径(zz1000)    ：仓位≤8%，次日竞价弱于昨收即清仓")
    print(f"连板≥3板            ：高度风险，仓位再减半，不超过4%")
    print(f"全局止损线          ：任意标的亏损超2.5%当日无条件止损")
    print(f"8步法完整度检查：")
    print("Step1 涨幅筛选   Step2 量比   Step3 换手率")
    print("Step4 市值过滤   Step5 量能递增   Step6 均线+压力检测")
    print("Step7 分时均价线上方（需盘中人工确认）")
    print("Step8 14:30创新高回踩入场（需盘中人工确认）")
    print()

    # ============================================================
    #  Telegram 推送
    #  v1.5+: 盘中初筛 / 盘后定稿 都推送，标题用 mode_label 区分
    # ============================================================
    if TELEGRAM_ENABLED:
        title = f"zuiyou1 v1.5 [{mode_label}]"
        mood_info = f"情绪: {mood} ({sentiment_score}分)"

        # 使用已筛选的 stable_picks 和 upper_picks（已应用推荐数限制）
        stable_list = []
        upper_list = []
        for _, row in stable_picks.iterrows():
            stable_list.append({
                "code": row["code"],
                "pool": row["pool"],
                "price": row["price"],
                "pct": row["pct"],
                "vol_ratio": row.get("vol_ratio", 0),
                "turn": row.get("turn", 0),
                "streak": row.get("streak", 0),
                "bias_ma5": row.get("bias_ma5", 0),
                "score": row["score"],
                "tags": row["tags"],
                "is_llm": row.get("is_llm", False),
            })
        for _, row in upper_picks.iterrows():
            upper_list.append({
                "code": row["code"],
                "pool": row["pool"],
                "price": row["price"],
                "pct": row["pct"],
                "vol_ratio": row.get("vol_ratio", 0),
                "turn": row.get("turn", 0),
                "streak": row.get("streak", 0),
                "bias_ma5": row.get("bias_ma5", 0),
                "score": row["score"],
                "tags": row["tags"],
                "is_llm": row.get("is_llm", False),
            })

        mood_desc_map = {
            "极冷": "情绪极冷，仅稳健路径运行，严控仓位",
            "冷淡": "情绪偏冷，仅启用稳健路径，注意风控",
            "正常": "情绪正常，双路径运行，稳健为主",
            "活跃": "情绪偏热，高位路径已开放，注意风控",
            "火热": "情绪火热，高位路径开放，严控止损",
            "高潮": "情绪高潮，推荐数压缩，警惕顶部风险",
        }
        mood_desc = mood_desc_map.get(mood, "情绪正常，双路径运行")

        header_info = (
            f"模式: [{mode_label}]\n"
            "双池策略：稳健[hs300+zz500] + 高位[zz1000]\n"
            "完整八步：涨幅→量比→换手→市值→量能→均线→压力→评分\n"
            f"运行时间：{current_beijing.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"{mood_desc}\n"
            f"稳健路径仓位: 单票≤15%总仓位\n"
            f"高位路径仓位: 单票≤8%总仓位，严守止损"
        )
        # 15:10 把"相比 14:30"的 diff 嵌入 header，避免完整列表重复推送的视觉冗余
        if diff_summary:
            header_info += "\n\n" + diff_summary

        operation_note = (
            "稳健: 次日09:35未维持昨收+1%即出\n"
            "高位: 次日竞价弱于昨收即清仓\n"
            "连板≥3: 高度风险，仓位再减半，不超过4%\n"
            "全局止损: 亏损超-2.5%无条件清仓\n"
            "\n"
            "8步法完整度检查:\n"
            "Step1 涨幅筛选  Step2 量比  Step3 换手率\n"
            "Step4 市值过滤  Step5 量能递增  Step6 均线+压力检测\n"
            "Step7 分时均价线上方（需盘中人工确认）\n"
            "Step8 14:30创新高回踩入场（需盘中人工确认）"
        )

        stable_total = sum(rejects_stable.values()) + len(results_stable)
        upper_total = sum(rejects_upper.values()) + len(results_upper)

        stable_reject_summary = _format_funnel(rejects_stable, stable_total, results_stable, CONFIG_STABLE)
        upper_reject_summary = _format_funnel(rejects_upper, upper_total, results_upper, CONFIG_UPPER)

        reject_summary = _format_funnel(total_rejects, total_scanned, cfg=CONFIG_STABLE)

        # 构建各池扫描信息（复用 scan_pool 已获取数据，避免重复调用 API）
        pool_summary = (
            f"扫描池: [{CONFIG_STABLE['POOL']}]  模式: {CONFIG_STABLE['MODE']}  时间权重: {stable_tw:.2f}\n"
            f"股票池: [{CONFIG_STABLE['POOL']}] 共 {stable_pool_size} 只\n"
            f"行情获取: 成功={stable_real_count} 只\n"
            f"━━ 过滤统计，[{CONFIG_STABLE['POOL']}]，共{stable_total}只 ━━\n"
            f"{stable_reject_summary}\n"
            f"\n"
            f"扫描池: [{CONFIG_UPPER['POOL']}]  模式: {CONFIG_UPPER['MODE']}  时间权重: {upper_tw:.2f}\n"
            f"股票池: [{CONFIG_UPPER['POOL']}] 共 {upper_pool_size} 只\n"
            f"行情获取: 成功={upper_real_count} 只\n"
            f"━━ 过滤统计，[{CONFIG_UPPER['POOL']}]，共{upper_total}只 ━━\n"
            f"{upper_reject_summary}"
        )

        try:
            ok = send_stock_picks(
                title, current_beijing.strftime("%Y-%m-%d"), mood_info,
                stable_list, upper_list, operation_note,
                reject_summary, header_info,
                stable_reject_summary, upper_reject_summary, pool_summary
            )
            if ok:
                print("✅ 已推送到 Telegram\n")
            else:
                print("⚠️ Telegram 推送失败,请检查 token/chat_id\n")
        except Exception as e:
            print(f"  ️ Telegram 推送异常: {e}\n")


# ============================================================
#  8b. 单股调试模式
# ============================================================
def debug_stock(code: str, cfg: dict = None):
    """调试单只股票，查看每一步过滤结果"""
    if cfg is None:
        cfg = CONFIG_STABLE
    print(f"\n{'=' * 60}")
    print(f"  调试模式: {code}  [路径: {cfg['POOL']}]")
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

    curr_price = real_info["now"]
    curr_pct = real_info["pct"]
    curr_vol = real_info["vol"]
    curr_amount = real_info["amount"]
    curr_turn = real_info.get("turn", 0.0)
    if curr_turn <= 0:
        curr_turn = float(df.iloc[-1]["turn"]) if "turn" in df.columns else 0.0

    time_weight = get_time_weight(cfg["MODE"])
    est_full_vol = curr_vol / time_weight if time_weight > 0 else curr_vol
    hist_vols = df["volume"].tolist()

    # Step 1: 涨幅
    in_stable = cfg["stable_pct_lo"] <= curr_pct <= cfg["stable_pct_hi"]
    in_upper = cfg["upper_pct_lo"] <= curr_pct <= cfg["upper_pct_hi"]
    steps.append(("涨幅筛选", in_stable or in_upper,
                  f"{curr_pct:.2f}% [{'稳健' if in_stable else '高位' if in_upper else '无'}] "
                  f"稳健:{cfg['stable_pct_lo']}-{cfg['stable_pct_hi']}% 高位:{cfg['upper_pct_lo']}-{cfg['upper_pct_hi']}%"))
    if not (in_stable or in_upper):
        _print_debug_steps(steps)
        bs.logout()
        return

    # Step 2: 成交额
    ok = cfg["min_amount"] <= curr_amount <= cfg["max_amount"]
    steps.append(("成交额", ok, f"{curr_amount/1e4:.0f}万 要求:{cfg['min_amount']/1e4:.0f}万-{cfg['max_amount']/1e4:.0f}万"))
    if not ok:
        _print_debug_steps(steps)
        bs.logout()
        return

    # Step 3: 换手率
    ok = cfg["turn_min"] <= curr_turn <= cfg["turn_max"]
    steps.append(("换手率", ok, f"{curr_turn:.2f}% 要求:{cfg['turn_min']}-{cfg['turn_max']}%"))
    if not ok:
        _print_debug_steps(steps)
        bs.logout()
        return

    # Step 4: 市值
    mktcap = real_info.get("mktcap", 0)
    if mktcap > 0:
        ok = cfg["min_mktcap"] <= mktcap <= cfg["max_mktcap"]
        steps.append(("流通市值", ok, f"{mktcap:.0f}亿 要求:{cfg['min_mktcap']}-{cfg['max_mktcap']}亿"))
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
    ok = cfg["vol_ratio_min"] <= vol_ratio <= cfg["vol_ratio_max"]
    steps.append(("量比", ok, f"{vol_ratio:.2f} 要求:{cfg['vol_ratio_min']}-{cfg['vol_ratio_max']}"))
    if not ok:
        _print_debug_steps(steps)
        bs.logout()
        return

    # Step 6a: 均线
    if cfg["MODE"] == "post" and curr_price > 0:
        hist_close = df["close"].tolist()
        if hist_close:
            hist_close[-1] = curr_price
    else:
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

    # 行业评分
    industry = get_stock_industry(code)
    industry_bonus, industry_category, industry_tags = analyze_industry(industry)
    industry_note = f"{industry} [{industry_category}]"
    if industry_bonus != 0:
        industry_note += f" 评分{industry_bonus:+d}"
    steps.append(("行业评分", True, industry_note))

    # 尾盘回落（post模式）
    if cfg["MODE"] == "post":
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

    # 行业评分（复用上面已计算的 industry_bonus，不重复调用）
    if industry_bonus != 0:
        score += industry_bonus
        if industry_bonus > 0:
            tags.append(f"热门赛道(+{industry_bonus})")
        else:
            tags.append(f"{industry_tags[0]}({industry_bonus})")

    # LLM候选加成（与主流程一致）
    is_llm_cand, llm_info = is_llm_candidate(code)
    llm_boost = 0.0
    if is_llm_cand:
        llm_boost = get_llm_boost_score(code)
        score += llm_boost
        tags.append(f"LLM候选+{llm_boost:.0f}")
        if llm_info.get('source_diversity', 0) >= 3:
            tags.append("共识强化")
        tags.append("🤖LLM")

    score = min(score, 120)

    steps.append(("评分", score >= cfg["score_threshold"],
                  f"得分:{score} 阈值:{cfg['score_threshold']} 标签:{' | '.join(tags)}"))

    _print_debug_steps(steps)
    if score >= cfg["score_threshold"]:
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