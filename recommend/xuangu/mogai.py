from datetime import datetime, timedelta

import baostock as bs
import pandas as pd
import requests

# ====================== 1. 核心实战配置 ======================
CONFIG = {
    "score_threshold": 80,
    "max_buy_pct": 9.9,  # 晚上复盘可放宽至涨停，寻找连板种子
    "min_amount": 200000000,  # 成交额门槛：2亿（过滤边缘股）
    "stop_loss": -2.5,  # 止损线
    "drawdown_sell": 2.0,  # 回撤止盈线
    "vol_avg_days": 10  # 10日均量去极值
}


# ====================== 2. 状态机：持仓管理器 ======================
class TradeStation:
    def __init__(self):
        self.positions = {}  # 模拟持仓: {code: {'buy_price': 0, 'max_high': 0, 'date': ''}}

    def update_and_check(self, code, current_price):
        if code not in self.positions: return "IGNORE"
        pos = self.positions[code]
        pos['max_high'] = max(pos['max_high'], current_price)

        profit = (current_price - pos['buy_price']) / pos['buy_price'] * 100
        drawdown = (pos['max_high'] - current_price) / pos['max_high'] * 100

        if profit < CONFIG["stop_loss"]: return "STOP_LOSS"
        if profit > 3.0 and drawdown > CONFIG["drawdown_sell"]: return "TAKE_PROFIT"
        return "HOLD"


# ====================== 3. 真实行情与情绪 (东财/腾讯) ======================
def get_realtime_data(codes):
    """腾讯接口：支持全市场秒级抓取"""
    if not codes: return {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    results = {}
    api_codes = [c.replace('.', '').lower() for c in codes]

    for i in range(0, len(api_codes), 50):
        chunk = api_codes[i:i + 50]
        url = f"http://qt.gtimg.cn/q={','.join(chunk)}"
        try:
            r = requests.get(url, headers=headers, timeout=5)
            for line in r.text.split(';'):
                p = line.split('~')
                if len(p) < 40: continue
                try:
                    c = p[0].split('=')[0][-8:]
                    results[c] = {
                        "now": float(p[3]), "pct": float(p[32]), "high": float(p[33]),
                        "amount": float(p[37]) * 10000, "vol": float(p[6]) * 100
                    }
                except:
                    continue
        except:
            continue
    return results


def fetch_sentiment():
    """东财接口：判断当日赚钱效应"""
    try:
        r = requests.get("http://push2ex.eastmoney.com/getTopicZTPool", timeout=5).json()
        zt_list = r.get("data", {}).get("pool", [])
        return True, len(zt_list)
    except:
        return True, 0


# ====================== 4. 核心算法：龙头逻辑 + 连板高度 ======================
def analyze_dragon(df, real_info, code):
    # 过滤成交额过小的僵尸股
    if real_info['amount'] < CONFIG["min_amount"]: return None

    # 准备数据 (转换为数值型并去极值)
    df['volume'] = pd.to_numeric(df['volume'])
    df['pctChg'] = pd.to_numeric(df['pctChg'])

    # 10日去极值均量
    vols = df['volume'].tail(10).tolist()
    vols.sort()
    clean_avg_vol = sum(vols[1:-1]) / len(vols[1:-1]) if len(vols) > 2 else 1

    # 计算量比 (晚上复盘时 weight=1)
    vol_ratio = real_info['vol'] / clean_avg_vol

    score = 40
    tags = []

    # A. 龙头连板判定 (核心)
    streak = 0
    for p in reversed(df['pctChg'].tolist()):
        if p >= 9.8:
            streak += 1
        else:
            break

    if streak >= 2:
        score += 40;
        tags.append(f"{streak}连板核心")
    elif streak == 1:
        score += 20;
        tags.append("首板突破")

    # B. 量能健康度
    if 1.5 <= vol_ratio <= 4.0:
        score += 20;
        tags.append("缩量蓄势" if vol_ratio < 2 else "爆量主升")

    if score >= CONFIG["score_threshold"]:
        return {"代码": code, "得分": score, "连板": streak, "量比": round(vol_ratio, 2), "描述": "|".join(tags)}
    return None


# ====================== 5. 自动化主流程 ======================
# ====================== 5. 自动化主流程 (已修复报错) ======================
# ====================== 5. 自动化主流程 (修复 AttributeError 与 盘后逻辑) ======================
# ====================== 5. 自动化主流程 (全量加固版) ======================
def main():
    bs.login()

    # 【初始化】确保所有关键变量在任何分支下都有定义
    final_results = []

    try:
        # 1. 获取全市场代码清单
        print("正在拉取全市场代码清单...")
        today_str = datetime.now().strftime("%Y-%m-%d")
        rs = bs.query_all_stock(day=today_str)

        full_pool = []
        while rs.next():
            row = rs.get_row_data()
            if row[0].startswith(('sh.60', 'sz.00', 'sz.30')):
                full_pool.append(row[0])

        if not full_pool:
            print("未获取到股票清单。")
            bs.logout()
            return

        # 2. 批量实时抓取
        print(f"全市场初筛开始，目标: {len(full_pool)} 只标的...")
        real_data = get_realtime_data(full_pool)

        # 3. 动态计算市场情绪
        # 清洗 key，确保索引一致性
        clean_real_data = {k.replace('sh', '').replace('sz', '').replace('.', '').lower(): v
                           for k, v in real_data.items()}

        limit_up_list = [c for c, info in clean_real_data.items() if info['pct'] >= 9.8]
        zt_count = len(limit_up_list)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 市场实测情绪: 今日涨停 {zt_count} 家")

        # 4. 离线复盘深度分析
        # 仅分析今天涨幅 > 4% 的强势股
        candidates = [code for code, info in clean_real_data.items() if info['pct'] > 4.0]
        print(f"锁定今日强势候选 {len(candidates)} 只，正在进行 K 线逻辑校验...")

        for pure_code in candidates:
            # 格式清洗与转换
            if pure_code.startswith('6'):
                bs_code = f"sh.{pure_code}"
            elif pure_code.startswith(('0', '3')):
                bs_code = f"sz.{pure_code}"
            else:
                continue

            # 获取 K 线数据
            k_rs = bs.query_history_k_data_plus(
                bs_code, "date,close,volume,pctChg",
                start_date=(datetime.now() - timedelta(days=25)).strftime("%Y-%m-%d"),
                frequency="d", adjustflag="3"
            )

            d_list = []
            while k_rs.next():
                d_list.append(k_rs.get_row_data())

            if len(d_list) < 10:
                continue

            # 执行核心量化算法
            res = analyze_dragon(pd.DataFrame(d_list, columns=k_rs.fields), clean_real_data[pure_code], bs_code)
            if res:
                res['当前价'] = clean_real_data[pure_code]['now']
                final_results.append(res)

    except Exception as e:
        print(f"❌ 运行过程中出现错误: {e}")

    # 5. 输出结果 (现在 final_results 保证已被初始化)
    print("\n" + "🔥" * 10 + " 选股结果 (TOP 10) " + "🔥" * 10)
    if final_results:
        out_df = pd.DataFrame(final_results).sort_values(by="得分", ascending=False)
        cols = ['代码', '得分', '当前价', '连板', '量比', '描述']
        # 确保列存在
        available_cols = [c for c in cols if c in out_df.columns]
        print(out_df[available_cols].head(10).to_string(index=False))
        print("\n💡 复盘建议：重点关注连板数 >= 1 且得分靠前的标的。")
    else:
        print("今日未发现符合龙头筛选逻辑的标的。")

    bs.logout()


if __name__ == "__main__":
    main()
