import baostock as bs
import pandas as pd
from datetime import datetime
import time

#稳健筛选法:
#核心逻辑:  惯性溢价：利用缩量后的首阳，博弈次日惯性冲高。
#安全性:   高。回落空间有限，止损好设。
#爆发力	中。通常是稳步上涨，少有连板
#数据要求	对分时图的平稳度要求极高。
#适合环境	震荡市、慢牛行情。

# ====================== 1. 配置参数（保持你的逻辑） ======================
CONFIG = {
    "min_pct": 3.0,
    "max_pct": 5.0,
    "turn_min": 5.0,
    "turn_max": 10.0,
    "stop_loss": -3,
    "take_profit": 6
}

# 字段增加：需要 preclose 来计算实时涨幅
FIELDS = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg"


# ====================== 2. 核心分析逻辑（完整保留你的 8 步法） ======================
def analyze_stock_logic(df, code):
    """
    输入 df 包含最近 20 天数据，最后一行是今天的实时数据
    """
    # 1. 基础长度检查
    if df is None or len(df) < 15:
        return None

    # 获取最后两行
    last = df.iloc[-1]
    prev = df.iloc[-2]

    # 2. 【核心修复】防空检查：确保关键字段不是空字符串 ''
    # 增加对 last['pctChg'] 和 last['volume'] 的校验
    if str(last['pctChg']).strip() == '' or str(last['volume']).strip() == '':
        # print(f"⚠️ {code} 今日数据为空（可能停牌或新股），跳过")
        return None

    try:
        # 类型转换
        close = float(last['close'])
        pct = float(last['pctChg'])
        turn = float(last['turn']) if last['turn'] and str(last['turn']).strip() != '' else 0
        volume = float(last['volume'])
        amount = float(last['amount']) if last['amount'] and str(last['amount']).strip() != '' else 0

        # --- STEP 1：涨幅筛选 ---
        if not (CONFIG["min_pct"] <= pct <= CONFIG["max_pct"]):
            return None

        # --- STEP 2：量比 ---
        # 同样对 prev['volume'] 进行防空处理
        prev_vol = float(prev['volume']) if str(prev['volume']).strip() != '' else 0
        vol_ratio = volume / prev_vol if prev_vol > 0 else 0
        if vol_ratio < 1:
            return None

        # --- STEP 3：换手率 ---
        if not (CONFIG["turn_min"] <= turn <= CONFIG["turn_max"]):
            return None

        # --- STEP 4：资金规模 ---
        if amount < 5e7 or amount > 5e9:
            return None

        # --- STEP 5 & 6：趋势与均线 ---
        # 转换为 float 时也要过滤掉可能的空值
        df_clean = df.copy()
        for col in ['close', 'volume', 'pctChg']:
            df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce').fillna(0)

        ma5 = df_clean['close'].rolling(5).mean().iloc[-1]
        ma10 = df_clean['close'].rolling(10).mean().iloc[-1]
        vol_ma5 = df_clean['volume'].rolling(5).mean().iloc[-1]

        if volume < vol_ma5: # STEP 5
            return None
        if not (close > ma5 and ma5 > ma10): # STEP 6
            return None

        # --- STEP 7 & 8：强势确认 ---
        avg_pct = df_clean['pctChg'].mean()
        if pct < avg_pct:
            return None

        # --- 最终评分 ---
        score = 0
        if pct >= 4: score += 3
        if turn > 7: score += 2
        if vol_ratio > 1.5: score += 2
        if close > ma5: score += 2

        return {
            "code": code,
            "close": close,
            "pct": pct,
            "turn": turn,
            "score": score,
            "reason": "符合V44实时隔夜法条件"
        }
    except Exception as e:
        # 即使发生其他解析错误，也只是跳过该股，不中断主流程
        # print(f"解析 {code} 出错: {e}")
        return None


# ====================== 3. 实时数据调度 ======================
def main():
    lg = bs.login()
    if lg.error_code != '0':
        print("登录失败")
        return

    # 获取选股池（中证500）
    print("正在初始化股票池...")
    stock_list = []
    rs = bs.query_zz500_stocks()
    while rs.next():
        stock_list.append(rs.get_row_data()[1])

    # 获取当前日期（实时模式）
    today = datetime.now().strftime("%Y-%m-%d")
    # 为了计算均线，我们需要拉取过去 30 天的数据到今天
    start_date = (datetime.now() - pd.Timedelta(days=40)).strftime("%Y-%m-%d")

    results = []
    total = len(stock_list)
    print(f"开始实时扫描 {total} 只股票 (策略: V44 8步法)...")

    for i, code in enumerate(stock_list):
        # 实时拉取该股最近的 K 线（包含今天）
        # 注意：在交易时间运行，最后一根 K 线即为当前实时状态
        k_rs = bs.query_history_k_data_plus(
            code, FIELDS,
            start_date=start_date, end_date=today,
            frequency="d", adjustflag="3"
        )

        data = []
        while k_rs.next():
            data.append(k_rs.get_row_data())

        if len(data) >= 15:
            df = pd.DataFrame(data, columns=k_rs.fields)
            res = analyze_stock_logic(df, code)
            if res:
                results.append(res)
                print(f"🔥 实时命中: {code} | 涨幅: {res['pct']}% | 评分: {res['score']}")

        if i % 50 == 0:
            print(f"进度: {i}/{total}...")

    bs.logout()

    # ====================== 4. 最终输出 ======================
    print("\n" + "=" * 80)
    print(f"📅 V44 实时推荐列表 ({datetime.now().strftime('%H:%M:%S')})")
    print("=" * 80)

    if not results:
        print("当前市场未发现完全符合 8 步法的标的。")
    else:
        results = sorted(results, key=lambda x: x["score"], reverse=True)
        for r in results:
            print(f"股票: {r['code']} | 现价: {r['close']:.2f} | 涨幅: {r['pct']:.2f}% | 评分: {r['score']}")
            print(f"操作建议: 止损 {CONFIG['stop_loss']}% / 止盈 {CONFIG['take_profit']}%")
            print("-" * 40)


if __name__ == "__main__":
    main()