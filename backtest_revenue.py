#!/usr/bin/env python3
"""
每日荐股收益回测系统
=====================
从 Supabase 数据库读取 daily_candidates 推荐数据，
按照以下规则精确计算收益：

买入：次日开盘价（ENTRY_MODE=open）
卖出规则（按日顺序检查 T+1 ~ T+7）：
  1. 若当日最低价 ≤ 止损价(买入价-7%) → 以止损价卖出（保守优先）
  2. 若当日最高价 ≥ 止盈价(买入价+9%) → 以止盈价卖出
  3. T+7 收盘若仍未触发 → 以收盘价卖出
选股：每日选推荐分(final_score)最高的股票（全部4种策略参与）
仓位：单仓模式，95%资金买入，满仓进出
数据清洗：过滤日变动>30%的异常行情数据（A股涨跌停±10%/±20%）
初始资金：100,000元

参数经19000种组合精细扫描优化（含移动止损/分数过滤/分批止盈），
Calmar比率(年化收益/最大回撤)最优。
关键优化：权益计算修复（持仓无行情数据时使用最近收盘价估值，避免虚假回撤）。
"""

import os, sys, json, math
from datetime import date, datetime, timedelta
from collections import defaultdict
import psycopg2
from psycopg2.extras import RealDictCursor

# === Configuration ===
DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres",
)
INITIAL_CAPITAL = 100000.0
POSITION_PCT = 0.95
MAX_HOLD_DAYS = 9
PROFIT_PCT = 11.0  # 止盈百分比
STOP_PCT = 8.0     # 止损百分比
MAX_CONCURRENT = 1  # 单仓模式：每次只持有1只股票
SCORE_THRESHOLD = 30  # 最低推荐分过滤（与每日指导一致）
ENTRY_MODE = "open"  # "close" = 推荐日收盘买入, "open" = 次日开盘买入
EXCLUDE_SOURCES = []  # 不排除任何策略（全部策略表现更优）
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest")
OUTPUT_HTML = os.path.join(OUTPUT_DIR, "backtest_report.html")

COLOR_UP = "#dc3545"
COLOR_DOWN = "#28a745"
COLOR_FLAT = "#6c757d"


def get_connection():
    """Connect to Supabase. Supports both DATABASE_URL and individual POSTGRES_* env vars."""
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return psycopg2.connect(db_url, connect_timeout=30, options="-c statement_timeout=60000")
    # Fall back to individual env vars (used in GitHub Actions)
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "aws-1-ap-northeast-1.pooler.supabase.com"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "postgres.qoakbxswwjqfsgbcgepr"),
        password=os.getenv("POSTGRES_PASSWORD", "wYFBB91zViSrk2vl"),
        dbname=os.getenv("POSTGRES_DB", "postgres"),
        sslmode=os.getenv("POSTGRES_SSLMODE", "require"),
        connect_timeout=30,
        options="-c statement_timeout=60000",
    )


# ============================================================
# Data Loading
# ============================================================

def load_recommendations(conn):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT id, snapshot_date, ts_code, stock_name, source,
               entry_low, entry_high, stop_loss, target_1, target_2,
               final_score, position_pct
        FROM daily_candidates
        WHERE selected = TRUE
          AND target_1 IS NOT NULL
          AND stop_loss IS NOT NULL
          AND final_score >= %s
        ORDER BY snapshot_date, final_score DESC, ts_code;
    """, (SCORE_THRESHOLD,))
    recs = [dict(r) for r in cur.fetchall()]
    cur.close()
    return recs


def load_trading_dates(conn):
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT trade_date FROM daily_quotes ORDER BY trade_date;")
    dates = [row[0] for row in cur.fetchall()]
    cur.close()
    return dates


def load_quotes_batch(conn, ts_codes, start_date, end_date):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    code_list = list(ts_codes)
    all_quotes = []
    batch_size = 500
    extended_end = end_date + timedelta(days=15)
    for i in range(0, len(code_list), batch_size):
        batch = code_list[i : i + batch_size]
        cur.execute("""
            SELECT ts_code, trade_date, open, high, low, close, volume, amount, pct_chg
            FROM daily_quotes
            WHERE ts_code = ANY(%s)
              AND trade_date >= %s
              AND trade_date <= %s
            ORDER BY ts_code, trade_date;
        """, (batch, start_date, extended_end))
        all_quotes.extend([dict(r) for r in cur.fetchall()])
    cur.close()
    return all_quotes


def organize_quotes(quotes):
    by_stock = defaultdict(dict)
    for q in quotes:
        by_stock[q["ts_code"]][q["trade_date"]] = q
    return by_stock


def validate_and_clean_quotes(quotes_by_stock):
    """Validate quote data and null out suspicious entries.
    A-share daily limit is ±10% (±20% for ChiNext/STAR), so >30% daily change is data error.
    Check ALL OHLC values against prev_close, not just close."""
    MAX_DAILY_CHANGE = 0.30
    cleaned_count = 0
    cleaned_details = []

    for ts_code, quotes in quotes_by_stock.items():
        sorted_dates = sorted(quotes.keys())
        prev_close = None

        for date in sorted_dates:
            q = quotes[date]
            close = q.get("close")
            if close is None:
                continue
            close = float(close)
            if close <= 0:
                q["open"] = q["high"] = q["low"] = q["close"] = None
                cleaned_count += 1
                continue

            if prev_close is not None and prev_close > 0:
                open_p = float(q["open"]) if q.get("open") else None
                high = float(q["high"]) if q.get("high") else None
                low = float(q["low"]) if q.get("low") else None

                is_suspicious = False
                max_dev = 0
                for val in [open_p, high, low, close]:
                    if val is not None and val > 0:
                        dev = abs(val - prev_close) / prev_close
                        if dev > MAX_DAILY_CHANGE:
                            is_suspicious = True
                            max_dev = max(max_dev, dev)

                if is_suspicious:
                    q["open"] = None
                    q["high"] = None
                    q["low"] = None
                    q["close"] = None
                    cleaned_count += 1
                    cleaned_details.append((ts_code, date, close, prev_close, round(max_dev * 100, 1)))
                    continue  # Don't update prev_close

            prev_close = close

    if cleaned_count > 0:
        print(f"  Cleaned {cleaned_count} suspicious quote records (>30% daily change in OHLC)")
        cleaned_details.sort(key=lambda x: -x[4])
        for ts, dt, close, prev, dev in cleaned_details[:10]:
            print(f"    {ts} {dt}: close={close} vs prev_close={prev} ({dev}%)")
    return quotes_by_stock


# ============================================================
# Backtest Logic
# ============================================================

def get_last_known_close(ts_code, current_date, quotes_by_stock, trading_dates, lookback=5):
    """Get last known close price before/at current_date, looking back up to `lookback` days."""
    if current_date not in trading_dates:
        return None
    idx = trading_dates.index(current_date)
    for i in range(idx, max(-1, idx - lookback - 1), -1):
        if i < 0:
            break
        d = trading_dates[i]
        q = quotes_by_stock.get(ts_code, {}).get(d)
        if q and q.get("close"):
            return float(q["close"])
    return None


def get_buy_price(rec, quotes_by_stock, trading_dates=None):
    """Get buy price based on ENTRY_MODE.
    'close' = snapshot date close price
    'open'  = next trading day open price"""
    ts_code = rec["ts_code"]
    snap_date = rec["snapshot_date"]
    quotes = quotes_by_stock.get(ts_code, {})
    if ENTRY_MODE == "open" and trading_dates:
        future = [d for d in trading_dates if d > snap_date]
        if future:
            q = quotes.get(future[0])
            if q and q.get("open"):
                return float(q["open"])
    # Fallback: close price on snapshot date
    if snap_date in quotes:
        close = quotes[snap_date].get("close")
        if close is not None:
            return float(close)
    if rec.get("entry_low") and rec.get("entry_high"):
        return (float(rec["entry_low"]) + float(rec["entry_high"])) / 2
    return None


def determine_sell(rec, trading_dates, quotes_by_stock, buy_price):
    """Determine sell price using fixed percentage profit/stop-loss.
    Always returns within T+1~T+MAX_HOLD_DAYS, even if quote data is missing."""
    ts_code = rec["ts_code"]
    snap_date = rec["snapshot_date"]
    profit_price = buy_price * (1 + PROFIT_PCT / 100)
    stop_price = buy_price * (1 - STOP_PCT / 100)
    quotes = quotes_by_stock.get(ts_code, {})
    future_dates = [d for d in trading_dates if d > snap_date]

    for i, check_date in enumerate(future_dates[:MAX_HOLD_DAYS]):
        if check_date not in quotes:
            # No data for this day; if it's last hold day, force return with buy_price
            if i == MAX_HOLD_DAYS - 1:
                return check_date, buy_price, "到期无数据", i + 1
            continue
        q = quotes[check_date]
        open_p = float(q["open"]) if q.get("open") else None
        high = float(q["high"]) if q.get("high") else None
        low = float(q["low"]) if q.get("low") else None
        close = float(q["close"]) if q.get("close") else None
        if high is None or low is None or close is None:
            if i == MAX_HOLD_DAYS - 1:
                return check_date, buy_price, "到期无数据", i + 1
            continue

        # Stop loss first (conservative)
        if low <= stop_price:
            if open_p is not None and open_p <= stop_price:
                return check_date, open_p, "跳空止损", i + 1
            return check_date, stop_price, "止损", i + 1

        # Profit taking
        if high >= profit_price:
            if open_p is not None and open_p >= profit_price:
                return check_date, open_p, "跳空止盈", i + 1
            return check_date, profit_price, "止盈", i + 1

        # Last day force sell
        if i == MAX_HOLD_DAYS - 1:
            return check_date, close, "到期平仓", i + 1

    # Fallback: not enough future dates, use what we have
    if future_dates:
        last_d = future_dates[-1]
        if last_d in quotes and quotes[last_d].get("close"):
            return last_d, float(quotes[last_d]["close"]), "持仓中", len(future_dates)
        return future_dates[0], buy_price, "无数据平仓", 1
    return None, None, "无数据", 0


def deduplicate_recommendations(recs):
    seen = {}
    for rec in recs:
        key = (rec["snapshot_date"], rec["ts_code"])
        if key not in seen:
            r = dict(rec)
            r["all_sources"] = [rec["source"]]
            seen[key] = r
        else:
            existing = seen[key]
            existing["all_sources"].append(rec["source"])
            if float(rec.get("final_score") or 0) > float(existing.get("final_score") or 0):
                new_r = dict(rec)
                new_r["all_sources"] = existing["all_sources"]
                seen[key] = new_r
    return sorted(seen.values(), key=lambda r: r["snapshot_date"])


def run_backtest_logic(recs, trading_dates, quotes_by_stock):
    """Calculate buy/sell prices and returns (percentage) for each recommendation.
    Position sizing is done later in simulate_portfolio with dynamic equity."""
    deduped = deduplicate_recommendations(recs)
    print(f"  Deduplicated: {len(deduped)} recommendations")
    trades = []
    skipped_no_price = 0
    for rec in deduped:
        buy_price = get_buy_price(rec, quotes_by_stock, trading_dates)
        if buy_price is None or buy_price <= 0:
            skipped_no_price += 1
            continue
        sell_date, sell_price, sell_reason, hold_days = determine_sell(
            rec, trading_dates, quotes_by_stock, buy_price)
        if sell_price is None:
            continue
        return_pct = (sell_price - buy_price) / buy_price * 100
        trades.append({
            "rec_date": rec["snapshot_date"],
            "ts_code": rec["ts_code"],
            "stock_name": rec["stock_name"],
            "source": rec["source"],
            "all_sources": rec.get("all_sources", [rec["source"]]),
            "buy_price": round(buy_price, 3),
            "sell_price": round(sell_price, 3),
            "target_1": round(buy_price * (1 + PROFIT_PCT / 100), 3),
            "stop_loss": round(buy_price * (1 - STOP_PCT / 100), 3),
            "return_pct": round(return_pct, 2),
            "sell_date": sell_date,
            "sell_reason": sell_reason,
            "hold_days": hold_days,
            "final_score": float(rec.get("final_score") or 0),
            # These will be set by simulate_portfolio
            "shares": 0, "cost": 0, "proceeds": 0, "pnl": 0, "executed": False,
        })
    print(f"  Valid trade signals: {len(trades)}")
    print(f"  Skipped (no price): {skipped_no_price}")
    return trades


def simulate_portfolio(recs, trading_dates, quotes_by_stock):
    """Dynamic day-by-day portfolio simulation (matches param_sweep logic).
    For each trading day: process sells (checking OHLC against targets/stops),
    then process buys (TOP-scored stock). Returns daily_equity, executed_trades, skipped_count.
    
    Key fix: Recommendations generate signals on snapshot_date, but buys are executed
    on the NEXT trading day using that day's open price. This matches real trading flow."""
    if not recs:
        return [], 0, []

    # Group recommendations by snapshot_date
    daily_recs = defaultdict(list)
    for r in recs:
        daily_recs[r["snapshot_date"]].append(r)

    # Build trading date range: from first recommendation to last trading date
    first_date = min(r["snapshot_date"] for r in recs)
    bt_dates = [d for d in trading_dates if d >= first_date]

    cash = INITIAL_CAPITAL
    open_positions = []  # list of dicts
    pending_buys = []    # Orders pending execution (next trading day)
    daily_equity = []
    executed_trades = []
    skipped_count = 0

    for d in bt_dates:
        # 0. Process pending buy orders (executed at today's open price)
        q_today = {}
        for ts_code in [p["ts_code"] for p in pending_buys]:
            q = quotes_by_stock.get(ts_code, {}).get(d)
            if q and q.get("open"):
                q_today[ts_code] = float(q["open"])
        
        pending_buys_sorted = sorted(pending_buys, key=lambda x: x["final_score"], reverse=True)
        slots = MAX_CONCURRENT - len(open_positions)
        
        for order in pending_buys_sorted:
            if slots <= 0:
                break
            
            ts_code = order["ts_code"]
            bp = q_today.get(ts_code)
            if not bp:
                continue
            
            avail = cash * POSITION_PCT
            allocated = avail / slots
            shares = int(allocated / bp / 100) * 100
            if shares < 100:
                pending_buys.remove(order)
                skipped_count += 1
                continue
            
            cost = shares * bp
            if cost > cash:
                pending_buys.remove(order)
                skipped_count += 1
                continue
            
            cash -= cost
            target = round(bp * (1 + PROFIT_PCT / 100), 2)
            stop_loss = round(bp * (1 - STOP_PCT / 100), 2)
            pos = {
                "ts_code": order["ts_code"], "stock_name": order["stock_name"],
                "source": order["source"], "all_sources": order.get("all_sources", [order["source"]]),
                "rec_date": order["rec_date"], "buy_date": d,
                "buy_price": bp, "shares": shares, "cost": round(cost, 2),
                "target": target, "stop_loss": stop_loss,
                "final_score": float(order.get("final_score") or 0),
                "target_1": target, "days_held": 0,
            }
            open_positions.append(pos)
            pending_buys.remove(order)
            slots -= 1

        # 1. Process sells first - dynamically check OHLC
        for pos in list(open_positions):
            pos["days_held"] = pos.get("days_held", 0) + 1
            q = quotes_by_stock.get(pos["ts_code"], {}).get(d)
            if not q or q.get("open") is None:
                if pos["days_held"] >= MAX_HOLD_DAYS:
                    last_p = _get_last_known_close(pos["ts_code"], d, quotes_by_stock, bt_dates) or pos["buy_price"]
                    cash += pos["shares"] * last_p
                    open_positions.remove(pos)
                    pnl = (last_p - pos["buy_price"]) * pos["shares"]
                    ret = (last_p - pos["buy_price"]) / pos["buy_price"] * 100
                    executed_trades.append(_make_trade_dict(pos, last_p, d, "到期无数据", pnl, ret))
                continue

            o = float(q["open"]) if q.get("open") else None
            h = float(q["high"]) if q.get("high") else None
            l = float(q["low"]) if q.get("low") else None
            c = float(q["close"]) if q.get("close") else None

            sell_price = None
            sell_reason = None

            # Gap-down stop
            if o is not None and o <= pos["stop_loss"] and o > 0:
                sell_price = o
                sell_reason = "跳空止损"
            # Gap-up profit
            elif o is not None and o >= pos["target"] and o > 0:
                sell_price = o
                sell_reason = "跳空止盈"
            # Intraday stop loss (conservative priority)
            elif l is not None and l <= pos["stop_loss"] and pos["stop_loss"] > 0:
                sell_price = pos["stop_loss"]
                sell_reason = "止损"
            # Intraday profit
            elif h is not None and h >= pos["target"] and pos["target"] > 0:
                sell_price = pos["target"]
                sell_reason = "止盈"
            # Time expiry
            elif pos.get("days_held", 0) >= MAX_HOLD_DAYS:
                sell_price = c
                sell_reason = "到期平仓"

            if sell_price is not None:
                cash += pos["shares"] * sell_price
                pnl = (sell_price - pos["buy_price"]) * pos["shares"]
                ret = (sell_price - pos["buy_price"]) / pos["buy_price"] * 100
                open_positions.remove(pos)
                executed_trades.append(_make_trade_dict(pos, sell_price, d, sell_reason, pnl, ret))

        # 2. Calculate equity for position sizing
        open_value = _calc_open_value(open_positions, d, quotes_by_stock, bt_dates)
        current_equity = cash + open_value

        # 3. Process recommendations - add to pending buys queue (executed tomorrow)
        recs_today = daily_recs.get(d, [])
        if recs_today:
            recs_sorted = sorted(recs_today, key=lambda x: float(x.get("final_score") or 0), reverse=True)
            for rec in recs_sorted:
                # Skip if already holding this stock
                if any(p["ts_code"] == rec["ts_code"] for p in open_positions):
                    continue
                # Skip if already in pending buys
                if any(p["ts_code"] == rec["ts_code"] for p in pending_buys):
                    continue
                
                pending_buys.append({
                    "ts_code": rec["ts_code"], "stock_name": rec["stock_name"],
                    "source": rec["source"], "all_sources": rec.get("all_sources", [rec["source"]]),
                    "rec_date": rec["snapshot_date"], "final_score": float(rec.get("final_score") or 0),
                })

        # 4. End-of-day equity
        open_value = _calc_open_value(open_positions, d, quotes_by_stock, bt_dates)
        total_equity = cash + open_value
        daily_equity.append({
            "date": d, "cash": round(cash, 2),
            "open_value": round(open_value, 2),
            "total_equity": round(total_equity, 2),
            "open_count": len(open_positions),
            "pending_count": len(pending_buys),
        })

    # Close remaining positions at last available prices
    for pos in open_positions:
        last_p = _get_last_known_close(pos["ts_code"], bt_dates[-1], quotes_by_stock, bt_dates) or pos["buy_price"]
        cash += pos["shares"] * last_p
        pnl = (last_p - pos["buy_price"]) * pos["shares"]
        ret = (last_p - pos["buy_price"]) / pos["buy_price"] * 100
        executed_trades.append(_make_trade_dict(pos, last_p, bt_dates[-1], "持仓中", pnl, ret))

    return daily_equity, skipped_count, executed_trades


def _get_buy_price_dynamic(rec, quotes_by_stock, bt_dates):
    """Get buy price: next trading day open price (matches param_sweep logic)."""
    ts_code = rec["ts_code"]
    snap_date = rec["snapshot_date"]
    quotes = quotes_by_stock.get(ts_code, {})
    future = [d for d in bt_dates if d > snap_date]
    if future:
        q = quotes.get(future[0])
        if q and q.get("open"):
            return float(q["open"])
    return None


def _get_last_known_close(ts_code, current_date, quotes_by_stock, bt_dates, lookback=5):
    """Get last known close price before/at current_date."""
    if current_date not in bt_dates:
        return None
    idx = bt_dates.index(current_date)
    for i in range(idx, max(-1, idx - lookback - 1), -1):
        if i < 0:
            break
        d = bt_dates[i]
        q = quotes_by_stock.get(ts_code, {}).get(d)
        if q and q.get("close"):
            return float(q["close"])
    return None


def _calc_open_value(positions, d, quotes_by_stock, bt_dates):
    """Calculate total market value of open positions."""
    val = 0
    for p in positions:
        q = quotes_by_stock.get(p["ts_code"], {}).get(d)
        if q and q.get("close"):
            val += p["shares"] * float(q["close"])
        else:
            last_p = _get_last_known_close(p["ts_code"], d, quotes_by_stock, bt_dates)
            val += p["shares"] * (last_p or p["buy_price"])
    return val


def _make_trade_dict(pos, sell_price, sell_date, sell_reason, pnl, ret):
    """Create a trade dict compatible with stats/HTML generation."""
    return {
        "rec_date": pos["rec_date"], "buy_date": pos.get("buy_date", pos["rec_date"]),
        "ts_code": pos["ts_code"], "stock_name": pos["stock_name"],
        "source": pos["source"], "all_sources": pos.get("all_sources", [pos["source"]]),
        "buy_price": round(pos["buy_price"], 3),
        "sell_price": round(sell_price, 3),
        "target_1": round(pos["target"], 3),
        "stop_loss": round(pos["stop_loss"], 3),
        "shares": pos["shares"], "cost": round(pos["shares"] * pos["buy_price"], 2),
        "proceeds": round(pos["shares"] * sell_price, 2),
        "pnl": round(pnl, 2), "return_pct": round(ret, 2),
        "sell_date": sell_date, "sell_reason": sell_reason,
        "hold_days": pos.get("days_held", 0),
        "final_score": pos.get("final_score", 0),
        "executed": True,
    }


# ============================================================
# Statistics
# ============================================================

def calculate_stats(trades, daily_equity, executed_trades):
    portfolio_trades = [t for t in trades if t.get("executed", True)]
    total_trades = len(portfolio_trades)
    # Signal-level stats (all trades, based on return_pct)
    all_signals = trades
    sig_wins = [t for t in all_signals if t["return_pct"] > 0]
    sig_win_rate = len(sig_wins) / len(all_signals) * 100 if all_signals else 0
    sig_avg_ret = sum(t["return_pct"] for t in all_signals) / len(all_signals) if all_signals else 0
    if total_trades == 0:
        return {"signal_count": len(all_signals), "signal_win_rate": round(sig_win_rate, 1),
                "signal_avg_return": round(sig_avg_ret, 2)}
    wins = [t for t in portfolio_trades if t["pnl"] > 0]
    losses = [t for t in portfolio_trades if t["pnl"] < 0]
    breakeven = [t for t in portfolio_trades if t["pnl"] == 0]
    total_pnl = sum(t["pnl"] for t in portfolio_trades)
    total_cost = sum(t["cost"] for t in portfolio_trades)
    win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
    avg_return = sum(t["return_pct"] for t in portfolio_trades) / total_trades if total_trades > 0 else 0
    avg_win = sum(t["return_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["return_pct"] for t in losses) / len(losses) if losses else 0
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    max_profit = max(portfolio_trades, key=lambda t: t["pnl"])["pnl"] if portfolio_trades else 0
    max_loss = min(portfolio_trades, key=lambda t: t["pnl"])["pnl"] if portfolio_trades else 0
    if daily_equity:
        final_equity = daily_equity[-1]["total_equity"]
        total_return = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    else:
        final_equity = INITIAL_CAPITAL
        total_return = 0
    max_drawdown = 0
    peak = INITIAL_CAPITAL
    for de in daily_equity:
        if de["total_equity"] > peak:
            peak = de["total_equity"]
        dd = (peak - de["total_equity"]) / peak * 100
        if dd > max_drawdown:
            max_drawdown = dd
    hold_days_dist = defaultdict(int)
    for t in portfolio_trades:
        hold_days_dist[t["hold_days"]] += 1
    reason_dist = defaultdict(int)
    for t in portfolio_trades:
        reason_dist[t["sell_reason"]] += 1
    if daily_equity and len(daily_equity) > 1:
        days = (daily_equity[-1]["date"] - daily_equity[0]["date"]).days
        if days > 0:
            annualized = ((final_equity / INITIAL_CAPITAL) ** (365 / days) - 1) * 100
        else:
            annualized = 0
    else:
        annualized = 0
    if len(daily_equity) > 2:
        daily_returns = []
        for i in range(1, len(daily_equity)):
            prev = daily_equity[i - 1]["total_equity"]
            curr = daily_equity[i]["total_equity"]
            if prev > 0:
                daily_returns.append((curr - prev) / prev)
        if daily_returns:
            mean_r = sum(daily_returns) / len(daily_returns)
            var_r = sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)
            std_r = math.sqrt(var_r)
            sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0
        else:
            sharpe = 0
    else:
        sharpe = 0
    return {
        "total_trades": total_trades, "wins": len(wins), "losses": len(losses),
        "breakeven": len(breakeven), "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2), "total_return": round(total_return, 2),
        "annualized_return": round(annualized, 2), "avg_return": round(avg_return, 2),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor else None,
        "max_profit": round(max_profit, 2), "max_loss": round(max_loss, 2),
        "max_drawdown": round(max_drawdown, 2), "final_equity": round(final_equity, 2),
        "sharpe": round(sharpe, 2), "hold_days_dist": dict(hold_days_dist),
        "reason_dist": dict(reason_dist), "total_cost": round(total_cost, 2),
        "signal_count": len(all_signals), "signal_win_rate": round(sig_win_rate, 1),
        "signal_avg_return": round(sig_avg_ret, 2),
    }


def calculate_strategy_stats(trades, executed_trades=None):
    """Calculate per-strategy statistics. Signal stats from all trades, execution stats from executed trades."""
    strategies = defaultdict(list)
    for t in trades:
        strategies[t["source"]].append(t)
    # Execution stats from executed trades (separate list)
    exec_by_source = defaultdict(list)
    if executed_trades:
        for t in executed_trades:
            exec_by_source[t["source"]].append(t)
    result = {}
    for source, str_trades in strategies.items():
        if not str_trades:
            continue
        # Use return_pct for win/loss (available for all signals)
        wins = [t for t in str_trades if t["return_pct"] > 0]
        losses = [t for t in str_trades if t["return_pct"] < 0]
        avg_ret = sum(t["return_pct"] for t in str_trades) / len(str_trades)
        # PnL only from executed trades
        exec_list = exec_by_source.get(source, [])
        total_pnl = sum(t["pnl"] for t in exec_list) if exec_list else 0
        reason_dist = defaultdict(int)
        for t in str_trades:
            reason_dist[t["sell_reason"]] += 1
        result[source] = {
            "trades": len(str_trades), "executed": len(exec_list),
            "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(str_trades) * 100, 1) if str_trades else 0,
            "total_pnl": round(total_pnl, 2), "avg_return": round(avg_ret, 2),
            "reason_dist": dict(reason_dist),
        }
    return result


def calculate_monthly_returns(daily_equity):
    if not daily_equity:
        return []
    monthly = defaultdict(lambda: {"start": None, "end": None})
    for de in daily_equity:
        month_key = de["date"].strftime("%Y-%m")
        if monthly[month_key]["start"] is None:
            prev_equity = INITIAL_CAPITAL
            for mk in sorted(monthly.keys()):
                if mk < month_key and monthly[mk]["end"]:
                    prev_equity = monthly[mk]["end"]
            monthly[month_key]["start"] = prev_equity
        monthly[month_key]["end"] = de["total_equity"]
    result = []
    for month_key in sorted(monthly.keys()):
        d = monthly[month_key]
        if d["start"] and d["end"] and d["start"] > 0:
            ret = (d["end"] - d["start"]) / d["start"] * 100
            result.append({"month": month_key, "start_equity": round(d["start"], 2),
                           "end_equity": round(d["end"], 2), "return_pct": round(ret, 2)})
    return result


# ============================================================
# HTML Generation (template-based, no f-string for JS)
# ============================================================

def generate_html(stats, strategy_stats, monthly_returns, daily_equity, executed_trades, skipped_count):
    # Prepare chart data
    equity_dates = json.dumps([de["date"].strftime("%Y-%m-%d") for de in daily_equity])
    equity_values = json.dumps([de["total_equity"] for de in daily_equity])
    cash_values = json.dumps([de["cash"] for de in daily_equity])
    open_values = json.dumps([de["open_value"] for de in daily_equity])

    daily_pnl = []
    for i, de in enumerate(daily_equity):
        if i == 0:
            pnl = de["total_equity"] - INITIAL_CAPITAL
        else:
            pnl = de["total_equity"] - daily_equity[i - 1]["total_equity"]
        daily_pnl.append(round(pnl, 2))
    pnl_dates = json.dumps([de["date"].strftime("%Y-%m-%d") for de in daily_equity])
    pnl_values = json.dumps(daily_pnl)

    source_map = {"llm_multisource": "LLM多源", "overnight_8step": "八步法", "funnel_strategy": "漏斗策略", "main_uptrend": "主升浪"}

    trade_rows = []
    for t in sorted(executed_trades, key=lambda x: x["rec_date"], reverse=True):
        pnl_color = COLOR_UP if t["pnl"] > 0 else (COLOR_DOWN if t["pnl"] < 0 else COLOR_FLAT)
        trade_rows.append({
            "rec_date": t["rec_date"].strftime("%Y-%m-%d"), "ts_code": t["ts_code"],
            "stock_name": t["stock_name"], "source": source_map.get(t["source"], t["source"]),
            "buy_price": t["buy_price"], "sell_price": t["sell_price"],
            "target": t["target_1"], "stop_loss": t["stop_loss"], "shares": t["shares"],
            "cost": t["cost"], "pnl": t["pnl"], "return_pct": t["return_pct"],
            "sell_reason": t["sell_reason"], "hold_days": t["hold_days"],
            "sell_date": t["sell_date"].strftime("%Y-%m-%d") if t["sell_date"] else "-",
            "pnl_color": pnl_color,
        })
    trades_json = json.dumps(trade_rows, ensure_ascii=False)

    monthly_html = ""
    for mr in monthly_returns:
        color = COLOR_UP if mr["return_pct"] > 0 else COLOR_DOWN
        monthly_html += '<tr><td>%s</td><td>¥%s</td><td>¥%s</td><td style="color:%s;font-weight:600;">%+.2f%%</td></tr>' % (
            mr["month"], format(mr["start_equity"], ",.0f"), format(mr["end_equity"], ",.0f"), color, mr["return_pct"])

    reason_labels = json.dumps(list(stats.get("reason_dist", {}).keys()), ensure_ascii=False)
    reason_values = json.dumps(list(stats.get("reason_dist", {}).values()))
    hd_labels = json.dumps(["T+%d" % k for k in sorted(stats.get("hold_days_dist", {}).keys())], ensure_ascii=False)
    hd_values = json.dumps(list(stats.get("hold_days_dist", {}).values()))

    strategy_table_html = ""
    for source, display_name in source_map.items():
        s = strategy_stats.get(source, {})
        excluded = source in EXCLUDE_SOURCES
        status_tag = ' <span style="font-size:10px;color:#999;background:#f0f0f0;padding:1px 6px;border-radius:3px;">已排除</span>' if excluded else ''
        if not s:
            strategy_table_html += '<tr><td>%s%s</td><td colspan="7" style="text-align:center;color:#999;">无数据</td></tr>' % (display_name, status_tag)
            continue
        pnl_color = COLOR_UP if s["total_pnl"] > 0 else COLOR_DOWN
        reason_str = ", ".join("%s:%d" % (k, v) for k, v in sorted(s["reason_dist"].items(), key=lambda x: -x[1]))
        strategy_table_html += '<tr><td>%s%s</td><td>%d<br><small style="color:#999;">执行%d</small></td><td>%d / %d</td><td>%.1f%%</td><td style="color:%s;font-weight:600;">¥%s</td><td style="color:%s;">%+.2f%%</td><td>%s</td></tr>' % (
            display_name, status_tag, s["trades"], s.get("executed", 0), s["wins"], s.get("losses", s["trades"] - s["wins"]), s["win_rate"],
            pnl_color, format(s["total_pnl"], ",.0f"), pnl_color, s["avg_return"], reason_str)

    pf_display = "%.2f" % stats["profit_factor"] if stats.get("profit_factor") else "∞"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    first_date = daily_equity[0]["date"].strftime("%Y-%m-%d") if daily_equity else "-"
    last_date = daily_equity[-1]["date"].strftime("%Y-%m-%d") if daily_equity else "-"

    template = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>每日荐股收益回测报告</title>
    <script>
    // Chart.js loading: try bootcdn first (works in China), fallback to jsdelivr
    (function(){
        var s=document.createElement('script');
        s.src='https://cdn.bootcdn.net/ajax/libs/Chart.js/4.4.1/chart.umd.min.js';
        s.onerror=function(){
            var s2=document.createElement('script');
            s2.src='https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js';
            s2.onerror=function(){window._chartLoadFailed=true;};
            document.head.appendChild(s2);
        };
        document.head.appendChild(s);
    })();
    </script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background: #f5f7fa; color: #333; line-height: 1.6; }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        .header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: #fff; padding: 30px 40px; border-radius: 12px; margin-bottom: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.15); }
        .header h1 { font-size: 28px; margin-bottom: 8px; }
        .header .subtitle { font-size: 14px; opacity: 0.8; }
        .header .params { display: flex; gap: 24px; margin-top: 16px; flex-wrap: wrap; }
        .header .param { background: rgba(255,255,255,0.1); padding: 6px 16px; border-radius: 6px; font-size: 13px; }
        .header .param strong { color: #ffd700; }
        .metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .metric-card { background: #fff; padding: 20px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); text-align: center; transition: transform 0.2s; }
        .metric-card:hover { transform: translateY(-2px); box-shadow: 0 4px 16px rgba(0,0,0,0.1); }
        .metric-card .label { font-size: 13px; color: #888; margin-bottom: 8px; }
        .metric-card .value { font-size: 24px; font-weight: 700; }
        .metric-card .sub { font-size: 12px; color: #aaa; margin-top: 4px; }
        .section { background: #fff; border-radius: 10px; padding: 24px; margin-bottom: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
        .section-title { font-size: 18px; font-weight: 600; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 2px solid #e8e8e8; display: flex; justify-content: space-between; align-items: center; }
        .section-title .badge { font-size: 12px; background: #eef2ff; color: #6366f1; padding: 4px 12px; border-radius: 12px; font-weight: normal; }
        .chart-container { position: relative; height: 400px; }
        .chart-container-sm { position: relative; height: 280px; }
        .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
        @media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        thead th { background: #f8f9fa; padding: 10px 12px; text-align: left; font-weight: 600; color: #555; border-bottom: 2px solid #e0e0e0; position: sticky; top: 0; cursor: pointer; user-select: none; white-space: nowrap; }
        thead th:hover { background: #eef2ff; }
        tbody td { padding: 8px 12px; border-bottom: 1px solid #f0f0f0; white-space: nowrap; }
        tbody tr:hover { background: #f8f9ff; }
        .table-scroll { max-height: 600px; overflow-y: auto; overflow-x: auto; border-radius: 8px; }
        .footer { text-align: center; padding: 20px; color: #999; font-size: 13px; }
        .text-up { color: #dc3545; }
        .text-down { color: #28a745; }
        .tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }
        .tag-llm { background: #e0f2fe; color: #0284c7; }
        .tag-8step { background: #fef3c7; color: #d97706; }
        .tag-funnel { background: #ede9fe; color: #7c3aed; }
        .tag-uptrend { background: #d1fae5; color: #059669; }
        .search-box { padding: 8px 16px; border: 1px solid #ddd; border-radius: 6px; font-size: 13px; width: 200px; }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>每日荐股收益回测报告</h1>
        <div class="subtitle">基于 daily_candidates 推荐数据 · 次日开盘买入 · @@PROFIT_PCT@@止盈/@@STOP_PCT@@止损 · @@MAX_HOLD@@强制平仓 · 单仓模式(95%) · 全部4种策略 · 异常数据已清洗</div>
        <div class="params">
            <div class="param">初始资金: <strong>¥@@INITIAL_CAPITAL@@</strong></div>
            <div class="param">选股策略: <strong>每日TOP @@MAX_CONCURRENT@@（推荐分最高）</strong></div>
            <div class="param">止盈/止损: <strong>@@PROFIT_PCT@@ / @@STOP_PCT@@</strong></div>
            <div class="param">仓位比例: <strong>@@POSITION_PCT@@</strong></div>
            <div class="param">持仓模式: <strong>单仓模式（满仓进出）</strong></div>
            <div class="param">入场时机: <strong>次日开盘</strong></div>
            <div class="param">最大持仓天数: <strong>@@MAX_HOLD@@</strong></div>
            <div class="param">回测区间: <strong>@@FIRST_DATE@@ ~ @@LAST_DATE@@</strong></div>
            <div class="param">更新时间: <strong>@@NOW@@</strong></div>
        </div>
    </div>

    <div class="metrics-grid">
        <div class="metric-card">
            <div class="label">总收益率</div>
            <div class="value @@TOTAL_RETURN_CLS@@">@@TOTAL_RETURN@@</div>
            <div class="sub">最终资金: ¥@@FINAL_EQUITY@@</div>
        </div>
        <div class="metric-card">
            <div class="label">总盈亏</div>
            <div class="value @@PNL_CLS@@">¥@@TOTAL_PNL@@</div>
            <div class="sub">交易总额: ¥@@TOTAL_COST@@</div>
        </div>
        <div class="metric-card">
            <div class="label">胜率</div>
            <div class="value">@@WIN_RATE@@</div>
            <div class="sub">盈@@WINS@@ / 亏@@LOSSES@@ / 平@@BREAKEVEN@@</div>
        </div>
        <div class="metric-card">
            <div class="label">实盘交易</div>
            <div class="value">@@TOTAL_TRADES@@</div>
            <div class="sub">信号总数: @@SIGNAL_COUNT@@ | 跳过(非TOP1/资金不足): @@SKIPPED@@</div>
        </div>
        <div class="metric-card">
            <div class="label">信号胜率</div>
            <div class="value">@@SIGNAL_WIN_RATE@@</div>
            <div class="sub">信号平均收益: @@SIGNAL_AVG_RET@@</div>
        </div>
        <div class="metric-card">
            <div class="label">最大回撤</div>
            <div class="value text-down">-@@MAX_DRAWDOWN@@</div>
            <div class="sub">历史最大跌幅</div>
        </div>
        <div class="metric-card">
            <div class="label">盈亏比</div>
            <div class="value">@@PF@@</div>
            <div class="sub">总盈利/总亏损</div>
        </div>
        <div class="metric-card">
            <div class="label">平均每笔收益</div>
            <div class="value @@AVG_RET_CLS@@">@@AVG_RETURN@@</div>
            <div class="sub">盈: @@AVG_WIN@@ / 亏: @@AVG_LOSS@@</div>
        </div>
        <div class="metric-card">
            <div class="label">年化收益</div>
            <div class="value @@ANNUAL_CLS@@">@@ANNUAL@@</div>
            <div class="sub">夏普比率: @@SHARPE@@</div>
        </div>
    </div>

    <div class="section">
        <div class="section-title">资金曲线 <span class="badge">每日总权益 = 现金 + 持仓市值</span></div>
        <div class="chart-container"><canvas id="equityChart"></canvas></div>
    </div>

    <div class="two-col">
        <div class="section">
            <div class="section-title">每日盈亏</div>
            <div class="chart-container-sm"><canvas id="pnlChart"></canvas></div>
        </div>
        <div class="section">
            <div class="section-title">卖出原因分布</div>
            <div class="chart-container-sm"><canvas id="reasonChart"></canvas></div>
        </div>
    </div>

    <div class="two-col">
        <div class="section">
            <div class="section-title">持仓天数分布</div>
            <div class="chart-container-sm"><canvas id="holdDaysChart"></canvas></div>
        </div>
        <div class="section">
            <div class="section-title">月度收益</div>
            <div class="table-scroll" style="max-height:280px;">
                <table>
                    <thead><tr><th>月份</th><th>月初资金</th><th>月末资金</th><th>收益率</th></tr></thead>
                    <tbody>@@MONTHLY_HTML@@</tbody>
                </table>
            </div>
        </div>
    </div>

    <div class="section">
        <div class="section-title">策略对比 <span class="badge">全部4种策略参与组合模拟</span></div>
        <table>
            <thead><tr><th>策略</th><th>信号数</th><th>盈/亏</th><th>胜率</th><th>实盘盈亏</th><th>平均收益</th><th>卖出原因</th></tr></thead>
            <tbody>@@STRATEGY_HTML@@</tbody>
        </table>
    </div>

    <div class="section">
        <div class="section-title">
            交易明细
            <span style="display:flex;gap:12px;align-items:center;">
                <input type="text" class="search-box" id="tradeSearch" placeholder="搜索股票代码/名称..." oninput="filterTrades()">
                <span class="badge" id="tradeCount">共 @@TRADE_COUNT@@ 笔</span>
            </span>
        </div>
        <div class="table-scroll">
            <table id="tradeTable">
                <thead><tr>
                    <th onclick="sortTable(0)">推荐日</th><th onclick="sortTable(1)">代码</th>
                    <th onclick="sortTable(2)">名称</th><th onclick="sortTable(3)">策略</th>
                    <th onclick="sortTable(4)">买入价</th><th onclick="sortTable(5)">卖出价</th>
                    <th onclick="sortTable(6)">止盈价</th><th onclick="sortTable(7)">止损价</th>
                    <th onclick="sortTable(8)">股数</th><th onclick="sortTable(9)">成本</th>
                    <th onclick="sortTable(10)">盈亏</th><th onclick="sortTable(11)">收益率</th>
                    <th onclick="sortTable(12)">卖出原因</th><th onclick="sortTable(13)">持仓</th>
                    <th onclick="sortTable(14)">卖出日</th>
                </tr></thead>
                <tbody id="tradeBody"></tbody>
            </table>
        </div>
    </div>

    <div class="footer">
        <p>数据来源: Supabase daily_candidates + daily_quotes | 回测规则: 次日开盘买入, @@PROFIT_PCT@@止盈/@@STOP_PCT@@止损, @@MAX_HOLD@@强制平仓, 单仓模式(95%) | 全部4种策略参与 | 异常数据已清洗(日变动>30%过滤) | 19000种参数组合精细扫描最优 | 权益计算已修复(无行情日使用最近收盘价估值)</p>
        <p>注意: 本报告仅供学习研究, 不构成投资建议. A股交易规则: 100股整手, T+1交易制度</p>
        <p>生成时间: @@NOW@@ | © openclaw-quant-system</p>
    </div>
</div>

<script>
var equityDates = @@EQUITY_DATES@@;
var equityValues = @@EQUITY_VALUES@@;
var cashValues = @@CASH_VALUES@@;
var openValues = @@OPEN_VALUES@@;
var pnlDates = @@PNL_DATES@@;
var pnlValues = @@PNL_VALUES@@;
var reasonLabels = @@REASON_LABELS@@;
var reasonData = @@REASON_VALUES@@;
var hdLabels = @@HD_LABELS@@;
var hdData = @@HD_VALUES@@;
var allTrades = @@TRADES_JSON@@;

// ===== All rendering starts after DOM + Chart.js ready =====
var renderStarted = false;
function renderAll() {
    if (renderStarted) return;
    renderStarted = true;

    try {
        // --- Equity Curve ---
        new Chart(document.getElementById('equityChart'), {
            type: 'line',
            data: { labels: equityDates, datasets: [{
                label: '总权益', data: equityValues, borderColor: '#6366f1',
                backgroundColor: 'rgba(99,102,241,0.08)', fill: true, borderWidth: 2,
                pointRadius: 0, pointHoverRadius: 5, tension: 0.1
            }, {
                label: '现金', data: cashValues, borderColor: '#28a745',
                backgroundColor: 'transparent', borderWidth: 1.5, pointRadius: 0,
                pointHoverRadius: 4, borderDash: [4, 4]
            }, {
                label: '持仓市值', data: openValues, borderColor: '#dc3545',
                backgroundColor: 'transparent', borderWidth: 1.5, pointRadius: 0,
                pointHoverRadius: 4, borderDash: [4, 4]
            }] },
            options: {
                responsive: true, maintainAspectRatio: false,
                interaction: { intersect: false, mode: 'index' },
                plugins: { legend: { position: 'top' }, tooltip: { callbacks: { label: function(ctx) { return ctx.dataset.label + ': \u00a5' + ctx.parsed.y.toLocaleString('zh-CN', {maximumFractionDigits: 0}); } } } },
                scales: { y: { ticks: { callback: function(v) { return '\u00a5' + (v/10000).toFixed(1) + '\u4e07'; } } } }
            }
        });

        // --- Daily P&L ---
        (function() {
            var pnlColors = pnlValues.map(function(v) { return v >= 0 ? '#dc3545' : '#28a745'; });
            new Chart(document.getElementById('pnlChart'), {
                type: 'bar',
                data: { labels: pnlDates, datasets: [{ label: '\u6bcf\u65e5\u76c8\u4e8f', data: pnlValues, backgroundColor: pnlColors, borderWidth: 0 }] },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false }, tooltip: { callbacks: { label: function(ctx) { return '\u00a5' + ctx.parsed.y.toLocaleString('zh-CN', {maximumFractionDigits: 0}); } } } },
                    scales: { y: { ticks: { callback: function(v) { return '\u00a5' + (v/10000).toFixed(1) + '\u4e07'; } } } }
                }
            });
        })();

        // --- Sell Reason Doughnut ---
        new Chart(document.getElementById('reasonChart'), {
            type: 'doughnut',
            data: { labels: reasonLabels, datasets: [{ data: reasonData, backgroundColor: ['#dc3545','#28a745','#ffc107','#17a2b8','#6c757d','#e83e8c'] }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'right' } } }
        });

        // --- Holding Days Bar ---
        new Chart(document.getElementById('holdDaysChart'), {
            type: 'bar',
            data: { labels: hdLabels, datasets: [{ label: '\u4ea4\u6613\u7b14\u6570', data: hdData, backgroundColor: '#6366f1', borderWidth: 0 }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
        });
    } catch(e) {
        console.error('Chart render error:', e);
    }

    // --- Trade Table ---
    var tbody = document.getElementById('tradeBody');
    if (!tbody) return;
    var sourceTagClass = { 'LLM\u591a\u6e90': 'tag-llm', '\u516b\u6b65\u6cd5': 'tag-8step', '\u6f0f\u6597\u7b56\u7565': 'tag-funnel', '\u4e3b\u5347\u6d6a': 'tag-uptrend' };

    function renderTrades(trades) {
        tbody.innerHTML = trades.map(function(t) {
            var cls = sourceTagClass[t.source] || 'tag-llm';
            var pnlSign = t.pnl >= 0 ? '+' : '-';
            var retSign = t.return_pct > 0 ? '+' : '';
            return '<tr><td>' + t.rec_date + '</td><td>' + t.ts_code + '</td><td>' + t.stock_name +
                '</td><td><span class="tag ' + cls + '">' + t.source + '</span></td>' +
                '<td>' + t.buy_price.toFixed(2) + '</td><td>' + t.sell_price.toFixed(2) +
                '</td><td>' + t.target.toFixed(2) + '</td><td>' + t.stop_loss.toFixed(2) +
                '</td><td>' + t.shares + '</td><td>\u00a5' + t.cost.toLocaleString('zh-CN', {maximumFractionDigits: 0}) +
                '</td><td style="color:' + t.pnl_color + ';font-weight:600;">' + pnlSign + '\u00a5' + Math.abs(t.pnl).toLocaleString('zh-CN', {maximumFractionDigits: 0}) +
                '</td><td style="color:' + t.pnl_color + ';">' + retSign + t.return_pct.toFixed(2) + '%</td>' +
                '<td>' + t.sell_reason + '</td><td>' + t.hold_days + '\u5929</td><td>' + t.sell_date + '</td></tr>';
        }).join('');
        document.getElementById('tradeCount').textContent = '\u5171 ' + trades.length + ' \u7b14';
    }
    renderTrades(allTrades);

    window.filterTrades = function() {
        var q = document.getElementById('tradeSearch').value.toLowerCase();
        renderTrades(allTrades.filter(function(t) {
            return t.ts_code.toLowerCase().indexOf(q) >= 0 || t.stock_name.toLowerCase().indexOf(q) >= 0 || t.source.toLowerCase().indexOf(q) >= 0;
        }));
    };

    var sortAsc = {};
    window.sortTable = function(colIdx) {
        var keys = ['rec_date','ts_code','stock_name','source','buy_price','sell_price','target','stop_loss','shares','cost','pnl','return_pct','sell_reason','hold_days','sell_date'];
        var key = keys[colIdx];
        sortAsc[key] = !sortAsc[key];
        renderTrades(allTrades.slice().sort(function(a, b) {
            var va = a[key], vb = b[key];
            if (typeof va === 'string') return sortAsc[key] ? va.localeCompare(vb) : vb.localeCompare(va);
            return sortAsc[key] ? va - vb : vb - va;
        }));
    };
}

// Wait for Chart.js to load before rendering
function waitForChart(retries) {
    retries = retries || 0;
    if (window.Chart) {
        renderAll();
    } else if (window._chartLoadFailed) {
        // Show fallback message if Chart.js failed entirely
        var charts = document.querySelectorAll('.chart-container, .chart-container-sm');
        for (var i = 0; i < charts.length; i++) {
            charts[i].innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#999;">\u56fe\u8868\u52a0\u8f7d\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u7f51\u7edc\u8fde\u63a5</div>';
        }
        // Still render trade table
        if (document.getElementById('tradeBody')) renderAll();
    } else if (retries < 40) {
        setTimeout(function(){ waitForChart(retries + 1); }, 250);
    } else {
        // Timeout: charts failed but try to render anyway
        renderAll();
    }
}
// Also render immediately if Chart already loaded (cached)
if (window.Chart) { renderAll(); }
else { setTimeout(function(){ waitForChart(); }, 300); }
</script>
</body>
</html>'''

    repl = {
        "@@INITIAL_CAPITAL@@": format(INITIAL_CAPITAL, ",.0f"),
        "@@POSITION_PCT@@": "%d%%" % (POSITION_PCT * 100),
        "@@MAX_CONCURRENT@@": str(MAX_CONCURRENT),
        "@@PROFIT_PCT@@": "+%.0f%%" % PROFIT_PCT,
        "@@STOP_PCT@@": "-%.0f%%" % STOP_PCT,
        "@@MAX_HOLD@@": "T+%d" % MAX_HOLD_DAYS,
        "@@FIRST_DATE@@": first_date, "@@LAST_DATE@@": last_date,
        "@@NOW@@": now_str,
        "@@TOTAL_RETURN_CLS@@": "text-up" if stats["total_return"] > 0 else "text-down",
        "@@TOTAL_RETURN@@": "%+.2f%%" % stats["total_return"],
        "@@FINAL_EQUITY@@": format(stats["final_equity"], ",.0f"),
        "@@PNL_CLS@@": "text-up" if stats["total_pnl"] > 0 else "text-down",
        "@@TOTAL_PNL@@": format(stats["total_pnl"], "+,.0f"),
        "@@TOTAL_COST@@": format(stats.get("total_cost", 0), ",.0f"),
        "@@WIN_RATE@@": "%.1f%%" % stats["win_rate"],
        "@@WINS@@": str(stats["wins"]), "@@LOSSES@@": str(stats["losses"]),
        "@@BREAKEVEN@@": str(stats.get("breakeven", 0)),
        "@@TOTAL_TRADES@@": str(stats["total_trades"]),
        "@@SKIPPED@@": str(skipped_count),
        "@@SIGNAL_COUNT@@": str(stats.get("signal_count", 0)),
        "@@SIGNAL_WIN_RATE@@": "%.1f%%" % stats.get("signal_win_rate", 0),
        "@@SIGNAL_AVG_RET@@": "%+.2f%%" % stats.get("signal_avg_return", 0),
        "@@MAX_DRAWDOWN@@": "%.2f%%" % stats["max_drawdown"],
        "@@PF@@": pf_display,
        "@@AVG_RET_CLS@@": "text-up" if stats["avg_return"] > 0 else "text-down",
        "@@AVG_RETURN@@": "%+.2f%%" % stats["avg_return"],
        "@@AVG_WIN@@": "%+.2f%%" % stats["avg_win"], "@@AVG_LOSS@@": "%+.2f%%" % stats["avg_loss"],
        "@@ANNUAL_CLS@@": "text-up" if stats["annualized_return"] > 0 else "text-down",
        "@@ANNUAL@@": "%+.1f%%" % stats["annualized_return"],
        "@@SHARPE@@": "%.2f" % stats["sharpe"],
        "@@EQUITY_DATES@@": equity_dates, "@@EQUITY_VALUES@@": equity_values,
        "@@CASH_VALUES@@": cash_values, "@@OPEN_VALUES@@": open_values,
        "@@PNL_DATES@@": pnl_dates, "@@PNL_VALUES@@": pnl_values,
        "@@REASON_LABELS@@": reason_labels, "@@REASON_VALUES@@": reason_values,
        "@@HD_LABELS@@": hd_labels, "@@HD_VALUES@@": hd_values,
        "@@TRADES_JSON@@": trades_json,
        "@@MONTHLY_HTML@@": monthly_html, "@@STRATEGY_HTML@@": strategy_table_html,
        "@@TRADE_COUNT@@": str(len(trade_rows)),
    }
    html = template
    for k, v in repl.items():
        html = html.replace(k, v)
    return html


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 60)
    print("  每日荐股收益回测系统")
    print("=" * 60)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    conn = get_connection()

    print("\n[1/5] 加载推荐数据...")
    recs = load_recommendations(conn)
    print("  Found %d recommendations" % len(recs))

    print("\n[2/5] 加载交易日历...")
    trading_dates = load_trading_dates(conn)
    print("  Found %d trading dates" % len(trading_dates))
    ts_codes = set(r["ts_code"] for r in recs)
    min_date = min(r["snapshot_date"] for r in recs)
    max_date = max(r["snapshot_date"] for r in recs)
    print("  Unique stocks: %d" % len(ts_codes))
    print("  Date range: %s ~ %s" % (min_date, max_date))

    print("\n[3/5] 加载行情数据...")
    quotes = load_quotes_batch(conn, ts_codes, min_date, max_date)
    print("  Loaded %d quote records" % len(quotes))
    quotes_by_stock = organize_quotes(quotes)
    print("  Organized for %d stocks" % len(quotes_by_stock))
    validate_and_clean_quotes(quotes_by_stock)
    conn.close()

    print("\n[4/5] 执行回测...")
    # Signal analysis: ALL 4 strategies (for strategy comparison + signal stats)
    all_trades = run_backtest_logic(recs, trading_dates, quotes_by_stock)
    # Dynamic portfolio simulation (matches param_sweep logic)
    portfolio_recs = [r for r in recs if r["source"] not in EXCLUDE_SOURCES] if EXCLUDE_SOURCES else recs
    daily_equity, skipped_count, executed = simulate_portfolio(portfolio_recs, trading_dates, quotes_by_stock)

    print("\n[5/5] 计算统计 & 生成HTML...")
    stats = calculate_stats(executed, daily_equity, executed)
    # Override signal-level stats from all_trades (all signals, not just executed)
    if all_trades:
        sig_wins = [t for t in all_trades if t["return_pct"] > 0]
        stats["signal_count"] = len(all_trades)
        stats["signal_win_rate"] = round(len(sig_wins) / len(all_trades) * 100, 1)
        stats["signal_avg_return"] = round(sum(t["return_pct"] for t in all_trades) / len(all_trades), 2)
    strategy_stats = calculate_strategy_stats(all_trades, executed)
    monthly_returns = calculate_monthly_returns(daily_equity)
    html = generate_html(stats, strategy_stats, monthly_returns, daily_equity, executed, skipped_count)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print("\n" + "=" * 60)
    print("  回测完成!")
    print("  HTML报告: %s" % OUTPUT_HTML)
    print("  总交易数: %d" % stats.get("total_trades", 0))
    print("  总收益率: %+.2f%%" % stats.get("total_return", 0))
    print("  胜率: %.1f%%" % stats.get("win_rate", 0))
    print("  最终资金: ¥%s" % format(stats.get("final_equity", 0), ",.0f"))
    print("  最大回撤: -%.2f%%" % stats.get("max_drawdown", 0))
    print("=" * 60)


if __name__ == "__main__":
    main()
