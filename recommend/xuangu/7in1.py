import baostock as bs
import pandas as pd
import requests
import time
from datetime import datetime, timedelta

# ====================== 1. 策略配置中心 ======================
CONFIG = {

    "MODE": "realtime",  # realtime: 盘中 | post: 晚上或复盘
    "MIN_AMOUNT": 150000000,  # 成交额门槛：1.5亿
    "MAX_STREAK_PENALTY": 4,  # 连板高度惩罚：超过4板风险极大
    "SCORE_THRESHOLD": 70,  # 准入分数
}


def get_time_weight():
    """计算时间权重：解决盘中量比低估"""
    if CONFIG["MODE"] == "post": return 1.0
    now = datetime.now()
    if now.hour >= 15: return 1.0
    if now.hour < 9 or (now.hour == 9 and now.minute < 30): return 0.05

    h, m = now.hour, now.minute
    if h < 12:
        passed = max(0, (h - 9) * 60 + m - 30)
        passed = min(120, passed)
    else:
        passed = 120 + max(0, (h - 13) * 60 + m)
    return max(0.05, passed / 240.0)


def get_realtime_data(stock_list):
    """腾讯接口实时快照"""
    results = {}
    api_codes = [s.replace('.', '').lower() for s in stock_list]
    for i in range(0, len(api_codes), 50):
        chunk = api_codes[i:i + 50]
        url = f"http://qt.gtimg.cn/q={','.join(chunk)}"
        try:
            r = requests.get(url, timeout=5)
            for line in r.text.split(';'):
                p = line.split('~')
                if len(p) < 40: continue
                raw_code = p[0].split('=')[0][-8:]
                if 'ST' in p[1] or 'S' in p[1]: continue
                results[raw_code] = {
                    'now': float(p[3]), 'pct': float(p[32]), 'vol': float(p[6]) * 100,
                    'turn': float(p[38]) if p[38] else 0, 'amount': float(p[37]) * 10000,
                    'high': float(p[33]), 'low': float(p[34]), 'open': float(p[5])
                }
        except:
            continue
    return results


# ====================== 2. 十一层核心分析算法 ======================
def analyze_ultimate_logic(df, real, full_code):
    try:
        # 1. 数据预处理
        for col in ['close', 'volume', 'pctChg', 'high', 'low', 'amount', 'turn']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # 2. 硬性门槛：成交额、价格
        if real['amount'] < CONFIG["MIN_AMOUNT"]: return None
        if real['now'] <= 0: return None

        # 3. 价格区间：3% - 9.8% (稳健+突破双轨制)
        curr_pct = real['pct']
        if not (3.0 <= curr_pct <= 9.8): return None

        # 4. 时间加权量比 (10日去极值均量)
        # 如果是 post 模式，hist_df 已经包含了今天，计算均量时需剔除最后一行
        all_vols = df['volume'].tolist()
        if CONFIG["MODE"] == "post":
            ref_vols = all_vols[:-1][-10:]  # 过去10天
        else:
            ref_vols = all_vols[-10:]

        if len(ref_vols) < 5: return None
        ref_vols.sort()
        clean_avg_vol = sum(ref_vols[1:-1]) / (len(ref_vols) - 2)

        weight = get_time_weight()
        est_vol = real['vol'] / weight
        vol_ratio = est_vol / clean_avg_vol if clean_avg_vol > 0 else 0
        if not (1.2 <= vol_ratio <= 15): return None

        # 5. 趋势验证：MA5 > MA10
        ma5 = df['close'].rolling(5).mean().iloc[-1]
        ma10 = df['close'].rolling(10).mean().iloc[-1]
        if not (real['now'] > ma5 > ma10): return None

        # 6. 连板高度识别
        streak = 0
        pct_list = df['pctChg'].tolist()
        # 如果是 post 模式，最后一位是今天，逻辑一致
        search_list = pct_list if CONFIG["MODE"] == "post" else pct_list
        for p in reversed(search_list[:-1] if CONFIG["MODE"] == "realtime" else search_list[:-1]):
            if p >= 9.8:
                streak += 1
            else:
                break

        # 7. 评分系统 (含 MAX_STREAK_PENALTY)
        score = 60
        tags = []

        if streak == 0:
            score += 10;
            tags.append("首阳突破")
        elif 1 <= streak <= 2:
            score += 20;
            tags.append(f"{streak}连板强势")
        elif streak >= CONFIG["MAX_STREAK_PENALTY"]:
            score -= 30;
            tags.append("高标风险⚠️")  # 这里就是你说的惩罚

        # 8. K线形态：拒绝长上影
        if real['high'] > real['low']:
            body_pos = (real['now'] - real['low']) / (real['high'] - real['low'])
            if body_pos < 0.6: return None

            # 9. 换手率过滤
        if not (2.0 <= real['turn'] <= 16.0): return None

        # 10. 均线乖离 (安全垫)
        bias = (real['now'] - ma5) / ma5 * 100
        if bias < 2.5: score += 10; tags.append("贴线安全")

        # 11. 分数截断
        if score < CONFIG["SCORE_THRESHOLD"]: return None

        return {
            '代码': full_code, '得分': score, '涨幅%': curr_pct,
            '预估量比': round(vol_ratio, 2), '连板': streak, '特征': "|".join(tags)
        }
    except Exception as e:
        return None


# ====================== 3. 执行流程 ======================
def main():
    bs.login()
    print(f"🚀 终极版启动 | 模式: {CONFIG['MODE']} | 权重: {get_time_weight():.2f}")

    rs = bs.query_zz500_stocks()
    pool = []
    while rs.next(): pool.append(rs.get_row_data()[1])

    # 模式适配：如果是 realtime，一次性抓取腾讯数据
    real_data_map = get_realtime_data(pool) if CONFIG["MODE"] == "realtime" else {}

    final_list = []
    print(f"正在分析 {len(pool)} 只股票...")

    for i, code in enumerate(pool):
        # 1. 获取基础历史数据
        k_rs = bs.query_history_k_data_plus(
            code, "date,close,volume,pctChg,high,low,amount,turn",
            start_date=(datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d"),
            frequency="d", adjustflag="3"
        )

        data_list = []
        while k_rs.next(): data_list.append(k_rs.get_row_data())
        if len(data_list) < 20: continue
        df = pd.DataFrame(data_list, columns=k_rs.fields)

        # 2. 确定当日“快照”数据
        if CONFIG["MODE"] == "realtime":
            raw_code = code.replace('.', '').lower()
            if raw_code not in real_data_map: continue
            snapshot = real_data_map[raw_code]
        else:
            # POST 模式：直接取 Baostock 的最后一行作为今日数据
            last = df.iloc[-1]
            snapshot = {
                'now': float(last['close']), 'pct': float(last['pctChg']),
                'vol': float(last['volume']), 'amount': float(last['amount']),
                'high': float(last['high']), 'low': float(last['low']),
                'turn': float(last['turn'])
            }

        # 3. 运行算法
        res = analyze_ultimate_logic(df, snapshot, code)
        if res:
            final_list.append(res)

        if i % 100 == 0: print(f"进度: {i}/{len(pool)}")

    # 4. 稳健输出
    print("\n" + "🔥" * 5 + " 最终隔夜精选清单 " + "🔥" * 5)
    if not final_list:
        print("今日无符合条件标的")
    else:
        df_res = pd.DataFrame(final_list).sort_values(by='得分', ascending=False)
        print(df_res.to_string(index=False))

    bs.logout()


if __name__ == "__main__":
    main()