"""
隔夜选股策略 - 交易模拟回测
========================================
基于 recommend_选股记录汇总.txt 中的推荐记录，
模拟 T日14:50买入 → T+1日9:30-10:30卖出，
止盈+2%，止损-2%，未触发则10:30市价卖出。

初始资金: 1万元
分配规则: 推荐N只则资金均分N份
"""
import baostock as bs
import sys
import os
from datetime import datetime, timedelta

BEIJING_TZ_OFFSET = 8

RECOMMENDATIONS = [
    {
        "date": "2026-04-29",
        "stocks": [
            {"code": "sh.600520", "price": 29.80},
        ],
    },
    {
        "date": "2026-04-30",
        "stocks": [
            {"code": "sz.002266", "price": 5.28},
            {"code": "sz.002756", "price": 87.51},
            {"code": "sh.600520", "price": 29.80},
        ],
    },
    {
        "date": "2026-05-07",
        "stocks": [
            {"code": "sh.603319", "price": 40.06},
            {"code": "sh.603353", "price": 48.85},
            {"code": "sh.603488", "price": 12.37},
            {"code": "sh.603078", "price": 27.29},
            {"code": "sh.603390", "price": 14.16},
            {"code": "sh.605358", "price": 45.40},
        ],
    },
]

PROFIT_TARGET = 0.02
STOP_LOSS = 0.02

_trading_days_cache = None


def get_trading_days(start_date: str, end_date: str) -> list:
    global _trading_days_cache
    if _trading_days_cache is not None:
        return [d for d in _trading_days_cache if start_date <= d <= end_date]
    rs = bs.query_trade_dates(start_date=start_date, end_date=end_date)
    if rs.error_code != "0":
        return []
    days = []
    while rs.next():
        row = rs.get_row_data()
        if row:
            days.append(row[0])
    _trading_days_cache = days
    return [d for d in days if start_date <= d <= end_date]


def code_to_baostock(code: str) -> str:
    pure = code.replace("sh.", "").replace("sz.", "").replace("bj.", "")
    if pure.startswith(("6", "9")):
        return f"sh.{pure}"
    elif pure.startswith(("8", "4")):
        return f"bj.{pure}"
    else:
        return f"sz.{pure}"


def get_next_trading_day(date_str: str) -> str:
    base = datetime.strptime(date_str, "%Y-%m-%d")
    for delta in range(1, 15):
        day = base + timedelta(days=delta)
        day_str = day.strftime("%Y-%m-%d")
        rs = bs.query_history_k_data_plus(
            "sh.000001",
            "date,close",
            start_date=day_str,
            end_date=day_str,
            frequency="d",
            adjustflag="3",
        )
        while rs.next():
            row = rs.get_row_data()
            if row and row[0] == day_str and row[1]:
                return day_str
    return (base + timedelta(days=1)).strftime("%Y-%m-%d")


def fetch_5min_kline(bs_code: str, date_str: str):
    rs = bs.query_history_k_data_plus(
        bs_code,
        "date,time,open,high,low,close,volume,amount",
        start_date=date_str,
        end_date=date_str,
        frequency="5",
        adjustflag="3",
    )
    if rs.error_code != "0":
        return []

    rows = []
    while rs.next():
        row = rs.get_row_data()
        if not row or len(row) < 8:
            continue
        time_str = row[1]
        if len(time_str) >= 14:
            try:
                t = int(time_str[8:12])
            except (ValueError, IndexError):
                continue
            if 930 <= t <= 1030:
                rows.append({
                    "time": time_str,
                    "open": float(row[2]) if row[2] else 0,
                    "high": float(row[3]) if row[3] else 0,
                    "low": float(row[4]) if row[4] else 0,
                    "close": float(row[5]) if row[5] else 0,
                })
    return rows


def simulate_sell(buy_price: float, kline_bars: list):
    if not kline_bars:
        return None, "无数据", 0.0

    target_price = buy_price * (1 + PROFIT_TARGET)
    stop_price = buy_price * (1 - STOP_LOSS)

    for bar in kline_bars:
        if bar["high"] <= 0 or bar["low"] <= 0:
            continue

        hit_profit = bar["high"] >= target_price
        hit_stop = bar["low"] <= stop_price

        if hit_profit and hit_stop:
            sell_price = stop_price
            return sell_price, "同bar触发止盈止损(保守取止损)", (sell_price / buy_price - 1)
        elif hit_profit:
            sell_price = target_price
            return sell_price, "止盈+2%", (sell_price / buy_price - 1)
        elif hit_stop:
            sell_price = stop_price
            return sell_price, "止损-2%", (sell_price / buy_price - 1)

    last_bar = kline_bars[-1]
    sell_price = last_bar["close"]
    pnl_pct = (sell_price / buy_price - 1)
    reason = f"10:30市价卖出(收益{pnl_pct*100:+.2f}%)"
    return sell_price, reason, pnl_pct


def main():
    print("=" * 80)
    print("  隔夜选股策略 - 交易模拟回测")
    print("  初始资金: 10,000 元 | 止盈: +2% | 止损: -2% | 卖出窗口: T+1 9:30-10:30")
    print("=" * 80)

    lg = bs.login()
    if lg.error_code != "0":
        print(f"baostock 登录失败: {lg.error_msg}")
        return

    capital = 10000.0
    initial_capital = capital
    trade_log = []

    for rec in RECOMMENDATIONS:
        date = rec["date"]
        stocks = rec["stocks"]
        t1_date = get_next_trading_day(date)
        n = len(stocks)
        alloc = capital / n

        buyable = []
        skipped = []
        for s in stocks:
            min_cost = s["price"] * 100
            if min_cost <= alloc:
                buyable.append(s)
            else:
                skipped.append(s)

        real_alloc = capital / len(buyable) if buyable else 0

        print(f"\n{'─' * 80}")
        print(f"📅 T日: {date} → T+1日: {t1_date}")
        print(f"   推荐股票: {n} 只 | 总资金: {capital:,.2f} 元 | 每只分配: {alloc:,.2f} 元")
        if skipped:
            print(f"   可买入: {len(buyable)} 只 | 实际每只分配: {real_alloc:,.2f} 元")
            for s in skipped:
                print(f"   ⏭️ {s['code']}: 1手需{s['price']*100:,.2f}元 > 分配{alloc:,.2f}元，跳过")
        print(f"{'─' * 80}")

        if not buyable:
            print(f"  ⚠️ 无可买入股票，当日空仓")
            trade_log.append({
                "date": date,
                "t1_date": t1_date,
                "details": [],
                "day_pnl": 0.0,
                "capital_after": capital,
            })
            continue

        day_pnl = 0.0
        day_details = []

        for s in buyable:
            code = s["code"]
            buy_price = s["price"]
            bs_code = code_to_baostock(code)

            shares = int(real_alloc / buy_price / 100) * 100
            if shares <= 0:
                shares = 100
            actual_cost = shares * buy_price

            kline_bars = fetch_5min_kline(bs_code, t1_date)

            if not kline_bars:
                print(f"  ⚠️ {code}: 无T+1分钟数据，尝试获取日线替代")
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,open,high,low,close",
                    start_date=t1_date,
                    end_date=t1_date,
                    frequency="d",
                    adjustflag="3",
                )
                daily_data = []
                while rs.next():
                    row = rs.get_row_data()
                    if row and len(row) >= 5:
                        daily_data.append({
                            "open": float(row[1]) if row[1] else 0,
                            "high": float(row[2]) if row[2] else 0,
                            "low": float(row[3]) if row[3] else 0,
                            "close": float(row[4]) if row[4] else 0,
                        })

                if daily_data:
                    d = daily_data[0]
                    sell_price = d["close"]
                    pnl_pct = (sell_price / buy_price - 1)
                    reason = f"日线收盘价替代(收益{pnl_pct*100:+.2f}%)"
                    pnl_amt = shares * sell_price - actual_cost
                else:
                    sell_price = buy_price
                    pnl_pct = 0.0
                    reason = "无任何数据，按买入价计算"
                    pnl_amt = 0.0
            else:
                sell_price, reason, pnl_pct = simulate_sell(buy_price, kline_bars)
                pnl_amt = shares * sell_price - actual_cost

            proceeds = shares * sell_price
            day_pnl += pnl_amt

            detail = {
                "code": code,
                "buy_price": buy_price,
                "sell_price": sell_price,
                "shares": shares,
                "cost": actual_cost,
                "proceeds": proceeds,
                "pnl_amt": pnl_amt,
                "pnl_pct": pnl_pct,
                "reason": reason,
                "kline_bars": len(kline_bars) if kline_bars else 0,
            }
            day_details.append(detail)

            bar_info = f"({len(kline_bars)}根5min K线)" if kline_bars else "(无分钟数据)"
            print(f"  {code}: 买入{buy_price:.2f} × {shares}股 = {actual_cost:,.2f}元")
            print(f"         卖出{sell_price:.2f} × {shares}股 = {proceeds:,.2f}元")
            print(f"         盈亏: {pnl_amt:+,.2f}元 ({pnl_pct*100:+.2f}%) {reason} {bar_info}")

        capital += day_pnl

        print(f"\n  📊 当日总盈亏: {day_pnl:+,.2f}元 | 当前资金: {capital:,.2f}元")

        trade_log.append({
            "date": date,
            "t1_date": t1_date,
            "details": day_details,
            "day_pnl": day_pnl,
            "capital_after": capital,
        })

    bs.logout()

    print(f"\n{'═' * 80}")
    print(f"  📈 模拟结果汇总")
    print(f"{'═' * 80}")
    print(f"  初始资金: {initial_capital:,.2f} 元")
    print(f"  最终资金: {capital:,.2f} 元")
    print(f"  总盈亏:   {capital - initial_capital:+,.2f} 元")
    print(f"  总收益率: {(capital / initial_capital - 1) * 100:+.2f}%")
    print(f"{'─' * 80}")

    print(f"\n  逐日明细:")
    print(f"  {'日期':<12} {'T+1日期':<12} {'股票数':<6} {'当日盈亏':>10} {'资金余额':>12} {'收益率':>8}")
    print(f"  {'─'*12} {'─'*12} {'─'*6} {'─'*10} {'─'*12} {'─'*8}")

    running_capital = initial_capital
    for t in trade_log:
        running_capital += t["day_pnl"]
        cum_ret = (running_capital / initial_capital - 1) * 100
        n_stocks = len(t["details"])
        print(f"  {t['date']:<12} {t['t1_date']:<12} {n_stocks:<6} {t['day_pnl']:>+10,.2f} {running_capital:>12,.2f} {cum_ret:>+7.2f}%")

    print(f"\n  逐笔明细:")
    for t in trade_log:
        print(f"\n  📅 {t['date']} (T+1: {t['t1_date']})")
        for d in t["details"]:
            print(f"    {d['code']}: 买{d['buy_price']:.2f}→卖{d['sell_price']:.2f} | "
                  f"{d['shares']}股 | 盈亏{d['pnl_amt']:+,.2f}元({d['pnl_pct']*100:+.2f}%) | {d['reason']}")

    print(f"\n{'═' * 80}")


if __name__ == "__main__":
    main()
