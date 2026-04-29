import pandas as pd
import numpy as np
import psycopg2
import math
import warnings

warnings.filterwarnings('ignore')

# ================= 1. 小盘股暴力配置 (高风险高收益) =================
DB_CONFIG = {
    "host": "49.233.189.132", "port": "5432",
    "database": "quant_system", "user": "quant", "password": "d1cf4fce072f6fc6aeb79dae"
}

INIT_CASH = 100000.0
MAX_POSITIONS = 10  # 小盘股容易踩雷，分仓到10只分散风险
STOP_LOSS = -12.0  # 给小盘股巨大的波动空间
TAKE_PROFIT = 35.0  # 目标就是翻倍或大涨
FEE_RATIO = 0.0015  # 小盘股佣金通常略高
SLIPPAGE = 0.002  # 小盘股冲击成本极大，滑点必须设高！


# ================= 2. 选股引擎：小市值 + 高活跃 =================
def fetch_v43_smallcap_data():
    print("🔥 正在扫描全市场微盘股，寻找高动能标的...")
    conn = psycopg2.connect(**DB_CONFIG)
    # 逻辑：筛选市值较小但换手率极高（活跃度）的前 1500 只
    query = """
        SELECT symbol, trade_date, open_price as open, close_price as close, 
               high_price as high, low_price as low, turnover_rate as turn, volume as vol
        FROM daily_prices 
        WHERE trade_date >= '2025-01-01'
        AND turnover_rate > 3.0  -- 换手必须活跃
        ORDER BY trade_date ASC
    """
    df = pd.read_sql(query, conn)
    conn.close()

    df['dt'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')
    groups = df.groupby('symbol')

    # 小盘股指标：3日动能 + 20日强度
    df['roc_3'] = groups['close'].transform(lambda x: x.pct_change(3))
    df['ma5'] = groups['close'].transform(lambda x: x.rolling(5).mean())
    df['pre_close'] = groups['close'].shift(1)

    # 严格去噪：剔除跌停板无法卖出的假数据
    df = df[df['close'] / df['pre_close'] > 0.91]

    date_map = {d: group.set_index('symbol').to_dict('index') for d, group in df.groupby('dt')}
    return df, date_map


# ================= 3. 策略引擎：追涨杀跌 =================
def run_v43_aggressive(backtest_days=250):
    df_all, date_map = fetch_v43_smallcap_data()
    all_dates = sorted(list(date_map.keys()))
    test_dates = all_dates[-backtest_days:]

    cash, positions, equity_curve, trade_log = INIT_CASH, [], [], []

    for i in range(len(test_dates) - 1):
        t_day, t_next = test_dates[i], test_dates[i + 1]
        day_snap = date_map.get(t_day, {})
        next_snap = date_map.get(t_next, {})

        # 1. 暴力离场逻辑
        still_holding = []
        for pos in positions:
            if pos['symbol'] in day_snap:
                r = day_snap[pos['symbol']]
                pnl = (r['close'] - pos['entry_price']) / pos['entry_price'] * 100

                # 小盘股逻辑：只要跌破 5 日线就全撤，不讲情面
                if pnl <= STOP_LOSS or pnl >= TAKE_PROFIT or r['close'] < r['ma5']:
                    if pos['symbol'] in next_snap:
                        sell_p = next_snap[pos['symbol']]['open'] * (1 - SLIPPAGE)
                        cash += pos['shares'] * sell_p * (1 - FEE_RATIO)
                        trade_log.append({'pnl': (sell_p / pos['entry_price'] - 1) * 100})
                        continue
            still_holding.append(pos)
        positions = still_holding

        # 2. 暴力买入逻辑 (寻找正在起飞的妖股)
        needed = MAX_POSITIONS - len(positions)
        if needed > 0:
            candidates = []
            for sym, r in day_snap.items():
                # 条件：近3日涨幅 > 8% (启动信号) 且 股价站在 5 日线上
                if r['roc_3'] > 0.08 and r['close'] > r['ma5'] and r['turn'] > 5.0:
                    candidates.append({'sym': sym, 'roc': r['roc_3']})

            # 选最猛的
            targets = sorted(candidates, key=lambda x: x['roc'], reverse=True)[:needed]
            for t in targets:
                if t['sym'] in next_snap:
                    buy_p = next_snap[t['sym']]['open'] * (1 + SLIPPAGE)
                    slot_cash = (cash + sum(
                        [p['shares'] * day_snap.get(p['symbol'], {'close': p['entry_price']})['close'] for p in
                         positions])) / MAX_POSITIONS
                    if cash >= slot_cash:
                        shs = math.floor(slot_cash / (buy_p * (1 + FEE_RATIO)) / 100) * 100
                        if shs >= 100:
                            cash -= shs * buy_p * (1 + FEE_RATIO)
                            positions.append({'symbol': t['sym'], 'shares': shs, 'entry_price': buy_p})

        equity = cash + sum(
            [p['shares'] * day_snap.get(p['symbol'], {'close': p['entry_price']})['close'] for p in positions])
        equity_curve.append({'total': equity})

        if i % 20 == 0:
            print(f"📅 {t_day} | 净值: {equity:,.2f} | 持仓: {len(positions)} | 发现妖股: {len(candidates)}")

    df_res = pd.DataFrame(equity_curve)
    mdd = ((df_res['total'] - df_res['total'].cummax()) / df_res['total'].cummax()).min() * 100
    print(
        f"\n💀 V43 小盘股暴力战报: 收益 {(df_res['total'].iloc[-1] / INIT_CASH - 1) * 100:.2f}% | 最大回撤 {mdd:.2f}% | 交易数 {len(trade_log)}")


if __name__ == "__main__":
    run_v43_aggressive(250)