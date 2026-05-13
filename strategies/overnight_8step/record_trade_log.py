"""
实战日志记录脚本 v2.0
========================================
两段式架构:
  早上模式 (09:50): 读取昨天 CSV, 补 T+1 数据(开盘价/最高/最低/收盘)
  晚上模式 (16:00): 读取 zuiyou 当日推荐 + 实际买入记录, 记录新行

CSV 字段:
  - 入选日: 代码/名称/路径/入选价/涨幅/得分/量比/换手/连板/乖离
  - T+1 数据: 开盘价/最高/最低/收盘/涨跌幅/换手率
  - 盈亏指标:
      t1_open_pnl: T+1 开盘卖的盈亏(最接近实战卖点)
      t1_high_pnl: T+1 最高点理论盈亏
      t1_close_pnl: T+1 收盘盈亏
  - 实战标记:
      actually_bought: yes/no (是否实际买入)
      actual_buy_price: 实际成交价(手动录入或 sell_new.py 写入)
      actual_sell_price: 实际清仓价(由 sell_new.py 写入)
      actual_pnl: 真实盈亏

用法:
    python record_trade_log.py morning   # 早上 09:50 补充 T+1 数据
    python record_trade_log.py evening   # 晚上 16:00 记录当日推荐
    python record_trade_log.py           # 默认 evening 模式
"""

import os
import csv
import sys
import json
import requests
from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8))
from typing import Optional

# ============================================================
# 配置
# ============================================================
LOG_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(LOG_DIR, "trade_log.csv")
MY_TRADES_FILE = os.path.join(LOG_DIR, "my_trades.json")
ZUIYOU1_FILE = os.path.join(LOG_DIR, "zuiyou1.py")

# CSV 字段定义
FIELDNAMES = [
    "date",              # 入选日期
    "code",              # 股票代码
    "name",              # 股票名称
    "path",              # 路径(稳健/高位)
    "entry_price",       # 入选价(入选日收盘价)
    "entry_pct",         # 入选日涨幅%
    "entry_score",       # 入选得分
    "entry_vol_ratio",   # 入选量比
    "entry_turn",        # 入选换手率%
    "entry_streak",      # 入选连板数
    "entry_bias",        # 入选乖离%
    "actually_bought",   # 是否实际买入 yes/no
    "actual_buy_price",  # 实际成交价
    "actual_sell_price", # 实际清仓价
    "actual_pnl",        # 真实盈亏%
    "t1_open",           # T+1 开盘价
    "t1_high",           # T+1 最高价
    "t1_low",            # T+1 最低价
    "t1_close",          # T+1 收盘价
    "t1_pct",            # T+1 涨跌幅%
    "t1_turn",           # T+1 换手率%
    "t1_open_pnl",       # T+1 开盘卖的盈亏%
    "t1_high_pnl",       # T+1 最高点理论盈亏%
    "t1_low_pnl",        # T+1 最低点理论盈亏%
    "t1_close_pnl",      # T+1 收盘盈亏%
    "notes",             # 备注
]


def get_latest_trading_day() -> str:
    """获取最近一个交易日（跳过周末）"""
    today = datetime.now(BEIJING_TZ)
    for delta in range(7):
        day = today - timedelta(days=delta)
        if day.weekday() < 5:
            return day.strftime("%Y-%m-%d")
    return today.strftime("%Y-%m-%d")


def get_next_trading_day(date_str: str) -> str:
    """获取指定日期之后的下一个交易日"""
    base = datetime.strptime(date_str, "%Y-%m-%d")
    for delta in range(1, 8):
        day = base + timedelta(days=delta)
        if day.weekday() < 5:
            return day.strftime("%Y-%m-%d")
    return base.strftime("%Y-%m-%d")


def fetch_stock_info(code: str) -> Optional[dict]:
    """从腾讯接口获取股票实时行情（仅用于当日数据）"""
    api_code = code.replace(".", "").lower()
    url = f"http://qt.gtimg.cn/q={api_code}"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if resp.status_code != 200:
            return None

        for line in resp.text.split(";"):
            if len(line) < 50:
                continue
            p = line.split("~")
            if len(p) < 40:
                continue

            def _f(idx, default=0.0):
                try:
                    return float(p[idx]) if p[idx].strip() else default
                except (ValueError, IndexError):
                    return default

            return {
                "name": p[1] if len(p) > 1 else "",
                "pre_close": _f(4),
                "open": _f(5),
                "high": _f(33),
                "low": _f(34),
                "now": _f(3),
                "pct": _f(32),
                "turn": _f(38),
            }
    except Exception:
        return None
    return None


def fetch_historical_daily(code: str, target_date: str) -> Optional[dict]:
    """通过 baostock 获取指定日期的历史日线数据（OHLCV），用于 T+1 回填。

    腾讯实时接口只返回当前价格，不能用于历史 T+1 数据回填。
    baostock volume 为股，转为手以与腾讯接口单位一致。
    """
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != '0':
            return None

        pure = code.replace("sh.", "").replace("sz.", "").replace("bj.", "")
        if pure.startswith(("6", "9")):
            bs_code = f"sh.{pure}"
        elif pure.startswith("8") or pure.startswith("4"):
            bs_code = f"bj.{pure}"
        else:
            bs_code = f"sz.{pure}"

        # 拉取前后几天确保覆盖目标日期
        from datetime import datetime as _dt
        dt = _dt.strptime(target_date, "%Y-%m-%d")
        start = (dt - timedelta(days=3)).strftime("%Y-%m-%d")
        end = (dt + timedelta(days=1)).strftime("%Y-%m-%d")

        rs = bs.query_history_k_data_plus(
            bs_code, "date,open,high,low,close,volume,preclose,pctChg,turn",
            start_date=start, end_date=end,
            frequency="d", adjustflag="3",
        )
        if rs.error_code != '0':
            bs.logout()
            return None

        rows = rs.get_data()
        bs.logout()

        if rows.empty:
            return None

        # 找目标日期
        for _, r in rows.iterrows():
            if r["date"] == target_date:
                vol = float(r["volume"]) / 100 if r["volume"] else 0  # 股→手
                return {
                    "name": "",
                    "pre_close": float(r["preclose"]) if r["preclose"] else 0,
                    "open": float(r["open"]) if r["open"] else 0,
                    "high": float(r["high"]) if r["high"] else 0,
                    "low": float(r["low"]) if r["low"] else 0,
                    "now": float(r["close"]) if r["close"] else 0,
                    "pct": float(r["pctChg"]) if r["pctChg"] else 0,
                    "turn": float(r["turn"]) if r["turn"] else 0,
                }
        return None
    except Exception:
        return None


def parse_zuiyou1_results() -> tuple:
    """
    从 zuiyou1.py 的选股记录汇总文件中解析当日推荐结果
    返回: (stable_best, upper_best)
    """
    summary_file = os.path.join(LOG_DIR, "..", "选股记录汇总.txt")
    
    if not os.path.exists(summary_file):
        print("⚠️ 选股记录汇总文件不存在")
        return None, None
    
    today = get_latest_trading_day()
    
    try:
        with open(summary_file, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"⚠️ 读取汇总文件失败: {e}")
        return None, None
    
    if today not in content:
        print(f"⚠️ 今日({today})无选股记录")
        return None, None
    
    blocks = content.split("=" * 80)
    today_blocks = [b for b in blocks if today in b]
    if not today_blocks:
        print(f"⚠️ 今日({today})无选股记录块")
        return None, None
    
    block = today_blocks[-1]
    
    stable_best = None
    upper_best = None
    stable_max_score = -1
    upper_max_score = -1
    
    def parse_stock_line(line: str) -> Optional[dict]:
        """解析单行股票信息"""
        parts = line.split()
        if len(parts) < 8:
            return None
        
        try:
            code = parts[0]
            price = pct = vol_ratio = turn = bias = 0.0
            streak = 0
            score = -1
            
            for p in parts:
                if p.startswith("¥"):
                    price = float(p.replace("¥", ""))
                elif p.startswith("+") or p.startswith("-"):
                    pct = float(p.replace("%", ""))
                elif p.startswith("量比"):
                    vol_ratio = float(p.replace("量比", ""))
                elif p.startswith("换手"):
                    turn = float(p.replace("%", "").replace("换手", ""))
                elif p.startswith("连板"):
                    streak = int(p.replace("连板", ""))
                elif p.startswith("乖离"):
                    bias = float(p.replace("%", "").replace("乖离", ""))
                elif p.startswith("得分"):
                    score = int(p.replace("得分", ""))
            
            if score < 0 or price <= 0:
                return None
            
            return {
                "code": code,
                "price": price,
                "pct": pct,
                "vol_ratio": vol_ratio,
                "turn": turn,
                "streak": streak,
                "bias": bias,
                "score": score,
            }
        except (ValueError, IndexError):
            return None
    
    # 解析稳健路径
    if "稳健路径" in block:
        stable_section = block.split("稳健路径")[1].split("高位路径")[0] if "高位路径" in block else block.split("稳健路径")[1]
        for line in stable_section.split("\n"):
            line = line.strip()
            if not line or line.startswith("━") or line.startswith("单票"):
                continue
            stock = parse_stock_line(line)
            if stock and stock["score"] > stable_max_score:
                stable_max_score = stock["score"]
                stable_best = {**stock, "path": "稳健"}
    
    # 解析高位路径
    if "高位路径" in block:
        upper_section = block.split("高位路径")[1]
        for line in upper_section.split("\n"):
            line = line.strip()
            if not line or line.startswith("━") or line.startswith("单票"):
                continue
            stock = parse_stock_line(line)
            if stock and stock["score"] > upper_max_score:
                upper_max_score = stock["score"]
                upper_best = {**stock, "path": "高位"}
    
    return stable_best, upper_best


def load_my_trades() -> dict:
    """
    加载用户实际买入记录
    格式: { "sh.603267": { "buy_price": 56.85, "date": "2026-05-08", "bought": true } }
    """
    if not os.path.exists(MY_TRADES_FILE):
        return {}
    try:
        with open(MY_TRADES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_csv_rows() -> list:
    """读取 CSV 所有行"""
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


def save_csv_rows(rows: list):
    """保存 CSV 所有行"""
    with open(LOG_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def append_csv_row(row: dict):
    """追加单行到 CSV"""
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def morning_mode():
    """
    早上模式 (09:50): 补充 T+1 数据
    读取 CSV 中所有未填 T+1 数据的记录，拉取腾讯接口补充
    """
    print("=" * 50)
    print("  早上模式: 补充 T+1 数据")
    print("=" * 50)
    
    rows = load_csv_rows()
    if not rows:
        print("  CSV 为空，无记录需要补充")
        return
    
    today = datetime.now(BEIJING_TZ).date()
    updated = False
    
    for row in rows:
        # 跳过已填 T+1 数据的记录
        if row.get("t1_close", "") != "":
            continue
        
        code = row["code"]
        entry_date_str = row["date"]
        
        # 计算下一个交易日
        next_trading_day_str = get_next_trading_day(entry_date_str)
        next_trading_day = datetime.strptime(next_trading_day_str, "%Y-%m-%d").date()
        
        # 只有当"今天 >= 下一个交易日"时才能补充
        if today < next_trading_day:
            continue
        
        # T+1 当天用腾讯实时接口, 历史用 baostock 日线
        if next_trading_day == today:
            info = fetch_stock_info(code)
        else:
            info = fetch_historical_daily(code, next_trading_day_str)
        if info is None:
            print(f"  ⚠️ {code} 获取行情失败")
            continue
        
        entry_price = float(row.get("entry_price", 0))
        t1_open = info["open"]
        t1_high = info["high"]
        t1_low = info["low"]
        t1_close = info["now"]
        t1_pct = info["pct"]
        t1_turn = info["turn"]
        
        if entry_price <= 0 or t1_close <= 0:
            print(f"  ⚠️ {code} 价格异常")
            continue
        
        # 计算盈亏
        t1_open_pnl = (t1_open - entry_price) / entry_price * 100
        t1_high_pnl = (t1_high - entry_price) / entry_price * 100
        t1_low_pnl = (t1_low - entry_price) / entry_price * 100
        t1_close_pnl = (t1_close - entry_price) / entry_price * 100
        
        row["t1_open"] = f"{t1_open:.2f}"
        row["t1_high"] = f"{t1_high:.2f}"
        row["t1_low"] = f"{t1_low:.2f}"
        row["t1_close"] = f"{t1_close:.2f}"
        row["t1_pct"] = f"{t1_pct:.2f}"
        row["t1_turn"] = f"{t1_turn:.2f}"
        row["t1_open_pnl"] = f"{t1_open_pnl:.2f}"
        row["t1_high_pnl"] = f"{t1_high_pnl:.2f}"
        row["t1_low_pnl"] = f"{t1_low_pnl:.2f}"
        row["t1_close_pnl"] = f"{t1_close_pnl:.2f}"
        
        updated = True
        print(f"  ✓ {code} T+1 数据: 开盘{t1_open:.2f} 最高{t1_high:.2f} 最低{t1_low:.2f} 收盘{t1_close:.2f}")
        print(f"    开盘盈亏: {t1_open_pnl:+.2f}%  最高盈亏: {t1_high_pnl:+.2f}%  收盘盈亏: {t1_close_pnl:+.2f}%")
    
    if updated:
        save_csv_rows(rows)
        print(f"\n✅ 已更新 {sum(1 for r in rows if r.get('t1_close', ''))} 条记录")
    else:
        print("  无需要补充的记录")


def evening_mode():
    """
    晚上模式 (16:00): 记录当日推荐
    1. 先跑 morning_mode 补充旧记录
    2. 解析 zuiyou1 当日推荐
    3. 加载 my_trades.json 标记实际买入
    4. 写入新行到 CSV
    """
    print("=" * 50)
    print("  晚上模式: 记录当日推荐")
    print("=" * 50)
    
    # Step 1: 先补充旧记录
    print("\nStep 1: 补充 T+1 数据...")
    morning_mode()
    
    # Step 2: 解析 zuiyou1 选股结果
    print("\nStep 2: 解析 zuiyou1 选股结果...")
    stable_best, upper_best = parse_zuiyou1_results()
    
    stocks_to_record = []
    if stable_best:
        stocks_to_record.append(stable_best)
        print(f"  稳健最佳: {stable_best['code']} 得分{stable_best['score']}")
    else:
        print("  稳健路径: 无推荐")
    
    if upper_best:
        stocks_to_record.append(upper_best)
        print(f"  高位最佳: {upper_best['code']} 得分{upper_best['score']}")
    else:
        print("  高位路径: 无推荐")
    
    if not stocks_to_record:
        print("\n⚠️ 无股票需要记录")
        return
    
    # Step 3: 加载实际买入记录
    print("\nStep 3: 加载实际买入记录...")
    my_trades = load_my_trades()
    today = get_latest_trading_day()
    
    # Step 4: 写入新行
    print(f"\nStep 4: 记录到 CSV...")
    for stock in stocks_to_record:
        code = stock["code"]
        info = fetch_stock_info(code)
        if info is None:
            print(f"  ⚠️ {code} 获取信息失败")
            continue
        
        # 检查是否实际买入
        pure_code = code.replace('sh.', '').replace('sz.', '').replace('bj.', '').replace('.', '')
        actually_bought = "no"
        actual_buy_price = ""

        # 尝试多种key格式匹配my_trades
        for trade_key in [pure_code, f"sh.{pure_code}", f"sz.{pure_code}",
                          f"sh{pure_code}", f"sz{pure_code}"]:
            if trade_key in my_trades:
                trade = my_trades[trade_key]
                if trade.get("date") == today and trade.get("bought", False):
                    actually_bought = "yes"
                    actual_buy_price = trade.get("buy_price", "")
                break
        
        row = {
            "date": today,
            "code": code,
            "name": info["name"],
            "path": stock["path"],
            "entry_price": stock["price"],
            "entry_pct": stock["pct"],
            "entry_score": stock["score"],
            "entry_vol_ratio": stock.get("vol_ratio", 0),
            "entry_turn": stock.get("turn", 0),
            "entry_streak": stock.get("streak", 0),
            "entry_bias": stock.get("bias", 0),
            "actually_bought": actually_bought,
            "actual_buy_price": actual_buy_price,
            "actual_sell_price": "",
            "actual_pnl": "",
            "t1_open": "",
            "t1_high": "",
            "t1_low": "",
            "t1_close": "",
            "t1_pct": "",
            "t1_turn": "",
            "t1_open_pnl": "",
            "t1_high_pnl": "",
            "t1_low_pnl": "",
            "t1_close_pnl": "",
            "notes": "entry_price为理论收盘价",
        }
        
        append_csv_row(row)
        bought_str = f"实际买入¥{actual_buy_price}" if actually_bought == "yes" else "未记录买入"
        print(f"  ✓ {code} ({info['name']}) {stock['path']} 得分{stock['score']} {bought_str}")
    
    # Step 5: 打印统计
    print("\nStep 5: 统计摘要...")
    print_statistics()
    
    print(f"\n✅ 日志文件: {LOG_FILE}")


def print_statistics():
    """打印统计摘要"""
    rows = load_csv_rows()
    if not rows:
        print("  暂无交易记录")
        return
    
    # 只统计已填 T+1 数据的
    settled = [r for r in rows if r.get("t1_close", "") != ""]
    if not settled:
        print("  暂无已结算的交易记录")
        return
    
    total = len(settled)
    
    # 按 T+1 开盘盈亏统计（最接近实战卖点）
    open_wins = sum(1 for r in settled if float(r.get("t1_open_pnl", 0)) > 0)
    open_losses = total - open_wins
    open_win_rate = open_wins / total * 100 if total > 0 else 0
    open_avg_pnl = sum(float(r.get("t1_open_pnl", 0)) for r in settled) / total
    
    # 按 T+1 收盘盈亏统计
    close_wins = sum(1 for r in settled if float(r.get("t1_close_pnl", 0)) > 0)
    close_win_rate = close_wins / total * 100 if total > 0 else 0
    close_avg_pnl = sum(float(r.get("t1_close_pnl", 0)) for r in settled) / total
    
    # 最高/最低盈亏
    max_high = max(float(r.get("t1_high_pnl", 0)) for r in settled)
    max_low = min(float(r.get("t1_low_pnl", 0)) for r in settled)
    
    # 按路径统计
    stable_rows = [r for r in settled if r["path"] == "稳健"]
    upper_rows = [r for r in settled if r["path"] == "高位"]
    
    # 按实际买入统计
    bought_rows = [r for r in settled if r.get("actually_bought") == "yes"]
    
    print(f"\n{'=' * 50}")
    print(f"  实战统计摘要 (共{total}笔)")
    print(f"{'=' * 50}")
    print(f"  T+1 开盘卖点: 胜率{open_win_rate:.1f}% 平均{open_avg_pnl:+.2f}%")
    print(f"  T+1 收盘卖点: 胜率{close_win_rate:.1f}% 平均{close_avg_pnl:+.2f}%")
    print(f"  最高理论盈亏: {max_high:+.2f}%  最低理论盈亏: {max_low:+.2f}%")
    
    if stable_rows:
        s_total = len(stable_rows)
        s_wins = sum(1 for r in stable_rows if float(r.get("t1_open_pnl", 0)) > 0)
        s_rate = s_wins / s_total * 100
        s_avg = sum(float(r.get("t1_open_pnl", 0)) for r in stable_rows) / s_total
        print(f"\n  稳健路径: {s_total}笔 开盘胜率{s_rate:.1f}% 平均{s_avg:+.2f}%")
    
    if upper_rows:
        u_total = len(upper_rows)
        u_wins = sum(1 for r in upper_rows if float(r.get("t1_open_pnl", 0)) > 0)
        u_rate = u_wins / u_total * 100
        u_avg = sum(float(r.get("t1_open_pnl", 0)) for r in upper_rows) / u_total
        print(f"  高位路径: {u_total}笔 开盘胜率{u_rate:.1f}% 平均{u_avg:+.2f}%")
    
    if bought_rows:
        b_total = len(bought_rows)
        b_wins = sum(1 for r in bought_rows if float(r.get("t1_open_pnl", 0)) > 0)
        b_rate = b_wins / b_total * 100
        b_avg = sum(float(r.get("t1_open_pnl", 0)) for r in bought_rows) / b_total
        print(f"\n  实际买入: {b_total}笔 开盘胜率{b_rate:.1f}% 平均{b_avg:+.2f}%")
    
    print(f"{'=' * 50}")


def main():
    mode = "evening"
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
    
    if mode == "morning":
        morning_mode()
    else:
        evening_mode()


if __name__ == "__main__":
    main()
