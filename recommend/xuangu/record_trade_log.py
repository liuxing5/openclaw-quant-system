"""
实战日志记录脚本 v1.1
========================================
每天 16:00 自动从 zuiyou1 选股结果中拉取
稳健路径和高位路径得分最高的各一只股票
记录当日表现，2 周后可用 Excel 直接出胜率/盈亏统计图

修复 v1.0 问题:
  1. 使用交易日而非自然日计算"次日"
  2. 补充所有未填次日数据的记录（不局限于昨天）
  3. entry_price 明确标注为"理论收盘价"（非实际成交价）

用法:
    python record_trade_log.py
    或配置 cron 每天 16:00 运行
"""

import os
import csv
import requests
from datetime import datetime, timedelta
from typing import Optional

# ============================================================
# 配置
# ============================================================
LOG_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(LOG_DIR, "trade_log.csv")
ZUIYOU1_FILE = os.path.join(LOG_DIR, "zuiyou1.py")

# 腾讯行情接口字段索引
# 0: 市场+代码, 1: 名称, 3: 现价, 4: 昨收, 5: 开盘, 32: 涨跌幅%, 33: 最高, 34: 最低
# 37: 成交额(万元), 38: 换手率%, 44: 总市值(亿)


def get_latest_trading_day() -> str:
    """获取最近一个交易日（跳过周末）"""
    today = datetime.now()
    for delta in range(7):
        day = today - timedelta(days=delta)
        if day.weekday() < 5:  # 周一到周五
            return day.strftime("%Y-%m-%d")
    return today.strftime("%Y-%m-%d")


def get_previous_trading_day(date_str: str = None) -> str:
    """获取上一个交易日"""
    if date_str:
        base = datetime.strptime(date_str, "%Y-%m-%d")
    else:
        base = datetime.now()
    
    for delta in range(1, 8):  # 从昨天往前找
        day = base - timedelta(days=delta)
        if day.weekday() < 5:
            return day.strftime("%Y-%m-%d")
    return base.strftime("%Y-%m-%d")


def get_next_trading_day(date_str: str) -> str:
    """获取指定日期之后的下一个交易日"""
    base = datetime.strptime(date_str, "%Y-%m-%d")
    for delta in range(1, 8):
        day = base + timedelta(days=delta)
        if day.weekday() < 5:
            return day.strftime("%Y-%m-%d")
    return base.strftime("%Y-%m-%d")


def parse_zuiyou1_results() -> tuple:
    """
    从 zuiyou1.py 的选股记录汇总文件中解析当日推荐结果
    返回: (stable_best, upper_best) 每只是 dict
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
    
    # 查找当日记录
    if today not in content:
        print(f"⚠️ 今日({today})无选股记录")
        return None, None
    
    # 按分隔符切块
    blocks = content.split("=" * 80)
    today_blocks = [b for b in blocks if today in b]
    if not today_blocks:
        print(f"⚠️ 今日({today})无选股记录块")
        return None, None
    
    # 取最后一块（最新）
    block = today_blocks[-1]
    
    stable_best = None
    upper_best = None
    stable_max_score = -1
    upper_max_score = -1
    
    # 解析稳健路径
    if "稳健路径" in block:
        stable_section = block.split("稳健路径")[1].split("高位路径")[0] if "高位路径" in block else block.split("稳健路径")[1]
        for line in stable_section.split("\n"):
            line = line.strip()
            if not line or line.startswith("━") or line.startswith("单票"):
                continue
            parts = line.split()
            if len(parts) >= 8:
                try:
                    code = parts[0]
                    score_idx = -2
                    for i, p in enumerate(parts):
                        if p.startswith("得分"):
                            score_idx = i
                            break
                    score = int(parts[score_idx].replace("得分", ""))
                    if score > stable_max_score:
                        stable_max_score = score
                        price = 0.0
                        pct = 0.0
                        vol_ratio = 0.0
                        turn = 0.0
                        streak = 0
                        bias = 0.0
                        for p in parts[1:]:
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
                        
                        stable_best = {
                            "code": code,
                            "price": price,
                            "pct": pct,
                            "vol_ratio": vol_ratio,
                            "turn": turn,
                            "streak": streak,
                            "bias": bias,
                            "score": score,
                            "path": "稳健",
                        }
                except (ValueError, IndexError):
                    continue
    
    # 解析高位路径
    if "高位路径" in block:
        upper_section = block.split("高位路径")[1]
        for line in upper_section.split("\n"):
            line = line.strip()
            if not line or line.startswith("━") or line.startswith("单票"):
                continue
            parts = line.split()
            if len(parts) >= 8:
                try:
                    code = parts[0]
                    score_idx = -2
                    for i, p in enumerate(parts):
                        if p.startswith("得分"):
                            score_idx = i
                            break
                    score = int(parts[score_idx].replace("得分", ""))
                    if score > upper_max_score:
                        upper_max_score = score
                        price = 0.0
                        pct = 0.0
                        vol_ratio = 0.0
                        turn = 0.0
                        streak = 0
                        bias = 0.0
                        for p in parts[1:]:
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
                        
                        upper_best = {
                            "code": code,
                            "price": price,
                            "pct": pct,
                            "vol_ratio": vol_ratio,
                            "turn": turn,
                            "streak": streak,
                            "bias": bias,
                            "score": score,
                            "path": "高位",
                        }
                except (ValueError, IndexError):
                    continue
    
    return stable_best, upper_best


def fetch_stock_info(code: str) -> Optional[dict]:
    """从腾讯接口获取股票信息（名称、昨收、开盘、最高、最低等）"""
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


def record_trade(stocks: list, date_str: str):
    """
    记录交易到 CSV
    
    Args:
        stocks: 股票列表，每项包含 {code, path, price, pct, score, ...}
        date_str: 日期字符串 YYYY-MM-DD
    """
    fieldnames = [
        "date",           # 记录日期
        "code",           # 股票代码
        "name",           # 股票名称
        "path",           # 路径(稳健/高位)
        "entry_price",    # 入选价(入选日收盘价，非实际成交价)
        "entry_pct",      # 入选日涨幅%
        "entry_score",    # 入选得分
        "entry_vol_ratio", # 入选量比
        "entry_turn",     # 入选换手率%
        "entry_streak",   # 入选连板数
        "entry_bias",     # 入选乖离%
        "next_open",      # 次日开盘价
        "next_high",      # 次日最高价
        "next_low",       # 次日最低价
        "next_close",     # 次日收盘价
        "next_pct",       # 次日涨跌幅%
        "next_turn",      # 次日换手率%
        "pnl_pct",        # 盈亏% = (次日收盘-入选价)/入选价
        "max_profit_pct", # 最大盈利% = (次日最高-入选价)/入选价
        "max_loss_pct",   # 最大亏损% = (次日最低-入选价)/入选价
        "win",            # 是否盈利(次日收盘>入选价)
        "notes",          # 备注
    ]
    
    file_exists = os.path.exists(LOG_FILE)
    
    with open(LOG_FILE, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        
        for stock in stocks:
            code = stock["code"]
            info = fetch_stock_info(code)
            if info is None:
                print(f"  ⚠️ {code} 获取信息失败")
                continue
            
            row = {
                "date": date_str,
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
                "next_open": "",
                "next_high": "",
                "next_low": "",
                "next_close": "",
                "next_pct": "",
                "next_turn": "",
                "pnl_pct": "",
                "max_profit_pct": "",
                "max_loss_pct": "",
                "win": "",
                "notes": "entry_price为理论收盘价，非实际成交价",
            }
            writer.writerow(row)
            print(f"  ✓ {code} ({info['name']}) {stock['path']} 得分{stock['score']} 已记录")


def fill_next_day_data():
    """
    为所有未填次日数据的记录补充数据
    修复 v1.0:
      1. 不用"yesterday"，而是找所有 next_close 为空的记录
      2. 校验"今天 >= 该记录的下一个交易日"才补充
      3. 处理周末/节假日边界情况
    """
    if not os.path.exists(LOG_FILE):
        return
    
    today = datetime.now().date()
    
    # 读取 CSV
    rows = []
    with open(LOG_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)
    
    updated = False
    for row in rows:
        # 只处理还没补充次日数据的记录
        if row.get("next_close", "") != "":
            continue
        
        code = row["code"]
        entry_date_str = row["date"]
        
        # 计算该记录的"下一个交易日"
        next_trading_day_str = get_next_trading_day(entry_date_str)
        next_trading_day = datetime.strptime(next_trading_day_str, "%Y-%m-%d").date()
        
        # 只有当"今天 >= 下一个交易日"时才能补充
        if today < next_trading_day:
            continue
        
        # 拉取腾讯接口获取行情
        api_code = code.replace(".", "").lower()
        url = f"http://qt.gtimg.cn/q={api_code}"
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            if resp.status_code != 200:
                print(f"  ⚠️ {code} 接口返回失败")
                continue
            
            parsed = False
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
                
                entry_price = float(row["entry_price"])
                next_open = _f(5)
                next_high = _f(33)
                next_low = _f(34)
                next_close = _f(3)
                next_pct = _f(32)
                next_turn = _f(38)
                
                if next_close <= 0 or entry_price <= 0:
                    break
                
                pnl = (next_close - entry_price) / entry_price * 100
                max_profit = (next_high - entry_price) / entry_price * 100
                max_loss = (next_low - entry_price) / entry_price * 100
                win = "是" if pnl > 0 else "否"
                
                row["next_open"] = f"{next_open:.2f}"
                row["next_high"] = f"{next_high:.2f}"
                row["next_low"] = f"{next_low:.2f}"
                row["next_close"] = f"{next_close:.2f}"
                row["next_pct"] = f"{next_pct:.2f}"
                row["next_turn"] = f"{next_turn:.2f}"
                row["pnl_pct"] = f"{pnl:.2f}"
                row["max_profit_pct"] = f"{max_profit:.2f}"
                row["max_loss_pct"] = f"{max_loss:.2f}"
                row["win"] = win
                
                updated = True
                print(f"  ✓ {code} 次日数据已补充: 收盘{next_close:.2f} 盈亏{pnl:.2f}%")
                parsed = True
                break
            
            if not parsed:
                print(f"  ⚠️ {code} 解析行情失败")
        except Exception as e:
            print(f"  ⚠️ {code} 补充数据失败: {e}")
    
    # 写回 CSV
    if updated:
        with open(LOG_FILE, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


def print_statistics():
    """打印统计摘要"""
    if not os.path.exists(LOG_FILE):
        print("暂无交易记录")
        return
    
    rows = []
    with open(LOG_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("pnl_pct") and row["pnl_pct"] != "":
                rows.append(row)
    
    if not rows:
        print("暂无已结算的交易记录")
        return
    
    total = len(rows)
    wins = sum(1 for r in rows if r["win"] == "是")
    losses = total - wins
    win_rate = wins / total * 100 if total > 0 else 0
    
    avg_pnl = sum(float(r["pnl_pct"]) for r in rows) / total
    max_profit = max(float(r["max_profit_pct"]) for r in rows)
    max_loss = min(float(r["max_loss_pct"]) for r in rows)
    
    # 按路径统计
    stable_rows = [r for r in rows if r["path"] == "稳健"]
    upper_rows = [r for r in rows if r["path"] == "高位"]
    
    print(f"\n{'=' * 50}")
    print(f"  实战统计摘要")
    print(f"{'=' * 50}")
    print(f"  总交易: {total} 笔")
    print(f"  盈利: {wins} 笔  亏损: {losses} 笔")
    print(f"  胜率: {win_rate:.1f}%")
    print(f"  平均盈亏: {avg_pnl:+.2f}%")
    print(f"  最大盈利: {max_profit:+.2f}%")
    print(f"  最大亏损: {max_loss:+.2f}%")
    
    if stable_rows:
        s_total = len(stable_rows)
        s_wins = sum(1 for r in stable_rows if r["win"] == "是")
        s_rate = s_wins / s_total * 100
        s_avg = sum(float(r["pnl_pct"]) for r in stable_rows) / s_total
        print(f"\n  稳健路径: {s_total}笔 胜率{s_rate:.1f}% 平均{s_avg:+.2f}%")
    
    if upper_rows:
        u_total = len(upper_rows)
        u_wins = sum(1 for r in upper_rows if r["win"] == "是")
        u_rate = u_wins / u_total * 100
        u_avg = sum(float(r["pnl_pct"]) for r in upper_rows) / u_total
        print(f"  高位路径: {u_total}笔 胜率{u_rate:.1f}% 平均{u_avg:+.2f}%")
    
    print(f"{'=' * 50}")


def main():
    print(f"实战日志记录脚本 v1.1")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Step 1: 先补充所有未填次日数据的记录
    print("Step 1: 补充未填次日数据...")
    fill_next_day_data()
    
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
    
    # Step 3: 记录到 CSV
    print(f"\nStep 3: 记录到 CSV...")
    today = get_latest_trading_day()
    record_trade(stocks_to_record, today)
    
    # Step 4: 打印统计
    print("\nStep 4: 统计摘要...")
    print_statistics()
    
    print(f"\n✅ 日志文件: {LOG_FILE}")
    print(f"   用 Excel 打开后可直接生成胜率/盈亏统计图")


if __name__ == "__main__":
    main()
