import pandas as pd
import numpy as np
import psycopg2
import math
import warnings
from datetime import datetime

warnings.filterwarnings('ignore')

# ================= 1. 核心实战配置 =================
DB_CONFIG = {
    "host": "49.233.189.132", "port": "5432",
    "database": "quant_system", "user": "quant", "password": "d1cf4fce072f6fc6aeb79dae"
}

# 针对 cleaned 表精调的生产参数
VOL_RATIO_RANGE = (1.8, 5.0)  # 异动量比
MIN_TURNOVER = 3.5  # 换手率门槛
MIN_AMOUNT = 20000.0  # 成交额门槛 (单位:万元, 假设 amount 字段单位为元，此处需对应调整)
MAX_BIAS_20 = 0.12  # 拒绝高位接盘
LIMIT_UP_SENTIMENT = 30  # 全市场涨停红线
DRAGON_GENE_5D = 0.20  # 5日内最大波幅 (抓妖性)
MARKET_CAP_RANGE = (30.0, 200.0)  # 只做 30亿-200亿 的活跃弹性票


def run_oracle_v51_cleaned():
    print(f"🚀 {datetime.now().strftime('%Y-%m-%d %H:%M')} | 启动 V51 生产级猎龙引擎...")

    # --- STEP 1: 数据采集 (直接对接 daily_prices_cleaned) ---
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        # 直接使用 pct_change 和 market_cap，效率更高
        query = """
            SELECT symbol, trade_date, open_price as open, close_price as close, 
                   high_price as high, low_price as low, volume as vol, 
                   turnover_rate as turn, amount, pct_change, market_cap
            FROM daily_prices_cleaned 
            WHERE trade_date > (CURRENT_DATE - INTERVAL '90 days')
            ORDER BY symbol, trade_date ASC
        """
        df = pd.read_sql(query, conn)
        conn.close()
    except Exception as e:
        print(f"❌ 数据库接入失败 (请检查表名和字段): {e}");
        return

    # --- STEP 2: 严谨的情绪风控 (利用清洗后的字段) ---
    latest_date = df['trade_date'].max()
    today_df = df[df['trade_date'] == latest_date].copy()

    # 统计真实涨停 (pct_change 在 cleaned 表中通常是百分比，如 9.98)
    # 如果你的 pct_change 是小数(0.0998)，请将 9.5 改为 0.095
    limit_up_count = len(today_df[today_df['pct_change'] >= 9.5])

    # 计算广度 (收红盘比例)
    valid_count = len(today_df)
    up_ratio = (today_df['close'] > today_df['open']).sum() / valid_count if valid_count > 0 else 0

    print(f"📊 盘面复盘 | 涨停: {limit_up_count} 家 | 广度: {up_ratio:.1%} | 日期: {latest_date}")

    if limit_up_count < LIMIT_UP_SENTIMENT:
        print("🛑 [风控] 连板情绪枯竭，今日不触发猎杀逻辑。")
        return

    # --- STEP 3-6: 核心特征计算 ---
    df = df.sort_values(['symbol', 'trade_date'])
    groups = df.groupby('symbol')

    df['ma5'] = groups['close'].transform(lambda x: x.rolling(5).mean())
    df['ma20'] = groups['close'].transform(lambda x: x.rolling(20).mean())
    df['vol_ma5'] = groups['vol'].transform(lambda x: x.rolling(5).mean())

    # 猎龙基因：5日最大涨幅 (使用 pct_change 累计或直接算价格比)
    df['max_range_5d'] = groups['close'].transform(lambda x: x.rolling(5).max() / x.rolling(5).min() - 1)

    # 异动指标
    df['vol_ratio'] = df['vol'] / df['vol_ma5']
    df['bias_20'] = (df['close'] - df['ma20']) / df['ma20']

    # --- STEP 7-9: 生产级过滤 ---
    latest = df.groupby('symbol').tail(1).copy()
    latest = latest.dropna(subset=['ma20', 'vol_ratio', 'bias_20']).copy()

    # 组合过滤：有基因 + 刚启动 + 适中市值 + 足够成交额
    mask = (
            (latest['close'] > latest['ma5']) &
            (latest['close'] < latest['ma5'] * 1.04) &
            (latest['vol_ratio'].between(*VOL_RATIO_RANGE)) &
            (latest['turn'] >= MIN_TURNOVER) &
            (latest['market_cap'].between(*MARKET_CAP_RANGE)) &
            (latest['max_range_5d'] >= DRAGON_GENE_5D) &
            (latest['pct_change'] < 9.0)  # 还没封死涨停
    )

    picks = latest[mask]

    # --- STEP 10: 评分权重 ---
    if not picks.empty:
        # 基因权重 (40%) + 量比权重 (30%) + 空间安全性 (30%)
        g_score = picks['max_range_5d'].clip(0.2, 0.6) / 0.6
        v_score = picks['vol_ratio'].clip(1.8, 5.0) / 5.0
        s_score = (0.12 - picks['bias_20']).clip(0, 0.12) / 0.12

        picks['final_score'] = g_score * 40 + v_score * 30 + s_score * 30

    # --- STEP 11: 离场与竞价提醒 ---
    results = picks.sort_values('final_score', ascending=False).head(3)

    print("\n" + "🐉" * 15)
    print("📢 V51 生产版 - 猎龙出击")
    print("🐉" * 15)

    if results.empty:
        print("扫描完毕：今日无强基因的异动目标。")
    else:
        for _, row in results.iterrows():
            sms = (f"【猎龙票】代码:{row['symbol']} | 分数:{row['final_score']:.1f}\n"
                   f"📊 市值:{row['market_cap']:.1f}亿 | 5日波动:{row['max_range_5d']:.1%}\n"
                   f"🎯 战术: 竞价高开>0% 且不缩量介入\n"
                   f"🛡️ 风控: 竞价低开即放弃 | 止损 -3.5% | 冲高回落 2% 止盈")
            print(sms)
    print("🐉" * 15 + "\n")


if __name__ == "__main__":
    run_oracle_v51_cleaned()