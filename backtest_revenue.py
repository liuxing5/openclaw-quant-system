#!/usr/bin/env python3
"""
每日荐股收益回测系统
=====================
从 Supabase 数据库读取 daily_candidates 推荐数据，
按照以下规则精确计算收益：

买入：推荐日（snapshot_date）收盘价
卖出规则（按日顺序检查 T+1, T+2, T+3）：
  1. 若当日最低价 ≤ 止损价(买入价-3%) → 以止损价卖出（保守优先）
  2. 若当日最高价 ≥ 止盈价(买入价+8%) → 以止盈价卖出
  3. T+3 收盘若仍未触发 → 以收盘价卖出
选股：每日选1只推荐分(final_score)最高的股票（排除八步法）
仓位：单仓模式，95%资金买入，满仓进出
初始资金：100,000元
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
MAX_HOLD_DAYS = 3
PROFIT_PCT = 8.0   # 止盈百分比
STOP_PCT = 3.0     # 止损百分比
MAX_CONCURRENT = 3  # 最多同时持仓数
EXCLUDE_SOURCES = ["overnight_8step"]  # 排除表现差的策略
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
          AND source IN ('llm_multisource', 'funnel_strategy')
        ORDER BY snapshot_date, source, final_score DESC;
    """)
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


# ============================================================
# Backtest Logic
# ============================================================

def get_buy_price(rec, quotes_by_stock):
    ts_code = rec["ts_code"]
    snap_date = rec["snapshot_date"]
    quotes = quotes_by_stock.get(ts_code, {})
    if snap_date in quotes:
        close = quotes[snap_date].get("close")
        if close is not None:
            return float(close)
    if rec.get("entry_low") and rec.get("entry_high"):
        return (float(rec["entry_low"]) + float(rec["entry_high"])) / 2
    return None


def determine_sell(rec, trading_dates, quotes_by_stock, buy_price):
    """Determine sell price using fixed percentage profit/stop-loss.
    Always returns within T+1~T+3, even if quote data is missing."""
    ts_code = rec["ts_code"]
    snap_date = rec["snapshot_date"]
    profit_price = buy_price * (1 + PROFIT_PCT / 100)
    stop_price = buy_price * (1 - STOP_PCT / 100)
    quotes = quotes_by_stock.get(ts_code, {})
    future_dates = [d for d in trading_dates if d > snap_date]

    for i, check_date in enumerate(future_dates[:MAX_HOLD_DAYS]):
        if check_date not in quotes:
            # No data for this day; if it's T+3, force return with buy_price
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

        # T+3 force sell
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
        buy_price = get_buy_price(rec, quotes_by_stock)
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


def simulate_portfolio(trades, trading_dates, quotes_by_stock):
    """Simulate portfolio: buy TOP 1 highest-scored stock each day, 10% equity per position."""
    sorted_trades = sorted(trades, key=lambda t: (t["rec_date"], -t["final_score"]))
    buys_by_date = defaultdict(list)
    sells_by_date = defaultdict(list)
    for t in sorted_trades:
        buys_by_date[t["rec_date"]].append(t)
        if t["sell_date"]:
            sells_by_date[t["sell_date"]].append(t)
    if not trades:
        return [], 0, []
    first_date = min(t["rec_date"] for t in trades)
    last_date = max(t.get("sell_date") or t["rec_date"] for t in trades)
    bt_dates = [d for d in trading_dates if first_date <= d <= last_date]
    cash = INITIAL_CAPITAL
    open_positions = []
    daily_equity = []
    executed_trades = []
    skipped_count = 0
    for d in bt_dates:
        # 1. Process sells first
        for t in sells_by_date.get(d, []):
            if t in open_positions:
                cash += t["proceeds"]
                open_positions.remove(t)
                executed_trades.append(t)
        # 2. Calculate current equity for dynamic position sizing
        open_value = 0
        for t in open_positions:
            q = quotes_by_stock.get(t["ts_code"], {}).get(d)
            if q and q.get("close"):
                open_value += t["shares"] * float(q["close"])
            else:
                open_value += t["cost"]
        current_equity = cash + open_value
        # 3. Process buys - single position mode, use 95% of cash, pick TOP 1 affordable
        daily_buys = buys_by_date.get(d, [])
        if open_positions:
            for t in daily_buys:
                t["executed"] = False
                skipped_count += 1
        else:
            if not daily_buys:
                pass
            position_amount = cash * POSITION_PCT
            bought = False
            for t in daily_buys:
                if bought:
                    t["executed"] = False
                    skipped_count += 1
                    continue
                buy_price = t["buy_price"]
                shares = int(position_amount / buy_price / 100) * 100
                if shares < 100:
                    shares = 100
                cost = shares * buy_price
                if cash < cost:
                    t["executed"] = False
                    continue
                cash -= cost
                t["shares"] = shares
                t["cost"] = round(cost, 2)
                t["proceeds"] = round(shares * t["sell_price"], 2)
                t["pnl"] = round(t["proceeds"] - cost, 2)
                t["executed"] = True
                open_positions.append(t)
                bought = True
        # 4. Recalculate end-of-day equity
        open_value = 0
        for t in open_positions:
            q = quotes_by_stock.get(t["ts_code"], {}).get(d)
            if q and q.get("close"):
                open_value += t["shares"] * float(q["close"])
            else:
                open_value += t["cost"]
        total_equity = cash + open_value
        daily_equity.append({
            "date": d, "cash": round(cash, 2),
            "open_value": round(open_value, 2),
            "total_equity": round(total_equity, 2),
            "open_count": len(open_positions),
        })
    return daily_equity, skipped_count, executed_trades


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


def calculate_strategy_stats(trades):
    """Calculate per-strategy statistics based on all signals (return_pct) and executed trades (pnl)."""
    strategies = defaultdict(list)
    for t in trades:
        strategies[t["source"]].append(t)
    result = {}
    for source, str_trades in strategies.items():
        if not str_trades:
            continue
        # Use return_pct for win/loss (available for all signals)
        wins = [t for t in str_trades if t["return_pct"] > 0]
        losses = [t for t in str_trades if t["return_pct"] < 0]
        avg_ret = sum(t["return_pct"] for t in str_trades) / len(str_trades)
        # PnL only from executed trades
        executed = [t for t in str_trades if t.get("executed")]
        total_pnl = sum(t["pnl"] for t in executed) if executed else 0
        reason_dist = defaultdict(int)
        for t in str_trades:
            reason_dist[t["sell_reason"]] += 1
        result[source] = {
            "trades": len(str_trades), "executed": len(executed),
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

def generate_html(stats, strategy_stats, monthly_returns, daily_equity, trades, skipped_count):
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

    source_map = {"llm_multisource": "LLM多源", "overnight_8step": "八步法", "funnel_strategy": "漏斗策略"}

    trade_rows = []
    for t in sorted(trades, key=lambda x: x["rec_date"], reverse=True):
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
        if not s:
            strategy_table_html += '<tr><td>%s</td><td colspan="7" style="text-align:center;color:#999;">无数据</td></tr>' % display_name
            continue
        pnl_color = COLOR_UP if s["total_pnl"] > 0 else COLOR_DOWN
        reason_str = ", ".join("%s:%d" % (k, v) for k, v in sorted(s["reason_dist"].items(), key=lambda x: -x[1]))
        strategy_table_html += '<tr><td>%s</td><td>%d<br><small style="color:#999;">执行%d</small></td><td>%d / %d</td><td>%.1f%%</td><td style="color:%s;font-weight:600;">¥%s</td><td style="color:%s;">%+.2f%%</td><td>%s</td></tr>' % (
            display_name, s["trades"], s.get("executed", 0), s["wins"], s.get("losses", s["trades"] - s["wins"]), s["win_rate"],
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
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
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
        .search-box { padding: 8px 16px; border: 1px solid #ddd; border-radius: 6px; font-size: 13px; width: 200px; }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>每日荐股收益回测报告</h1>
        <div class="subtitle">基于 daily_candidates 推荐数据 · 推荐日收盘买入 · @@PROFIT_PCT@@止盈/@@STOP_PCT@@止损 · T+3强制平仓 · 单仓模式 · 排除八步法</div>
        <div class="params">
            <div class="param">初始资金: <strong>¥@@INITIAL_CAPITAL@@</strong></div>
            <div class="param">选股策略: <strong>每日TOP 1（推荐分最高）</strong></div>
            <div class="param">止盈/止损: <strong>@@PROFIT_PCT@@ / @@STOP_PCT@@</strong></div>
            <div class="param">仓位比例: <strong>@@POSITION_PCT@@</strong></div>
            <div class="param">持仓模式: <strong>单仓（满仓进出）</strong></div>
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
        <div class="section-title">策略对比</div>
        <table>
            <thead><tr><th>策略</th><th>交易数</th><th>盈/亏</th><th>胜率</th><th>总盈亏</th><th>平均收益</th><th>卖出原因</th></tr></thead>
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
        <p>数据来源: Supabase daily_candidates + daily_quotes | 回测规则: 推荐日收盘买入, @@PROFIT_PCT@@止盈/@@STOP_PCT@@止损, T+3强制平仓, 单仓模式, 排除八步法</p>
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
        plugins: { legend: { position: 'top' }, tooltip: { callbacks: { label: function(ctx) { return ctx.dataset.label + ': ¥' + ctx.parsed.y.toLocaleString('zh-CN', {maximumFractionDigits: 0}); } } } },
        scales: { y: { ticks: { callback: function(v) { return '¥' + (v/10000).toFixed(1) + '万'; } } } }
    }
});

(function() {
    var pnlColors = pnlValues.map(function(v) { return v >= 0 ? '#dc3545' : '#28a745'; });
    new Chart(document.getElementById('pnlChart'), {
        type: 'bar',
        data: { labels: pnlDates, datasets: [{ label: '每日盈亏', data: pnlValues, backgroundColor: pnlColors, borderWidth: 0 }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { callbacks: { label: function(ctx) { return '¥' + ctx.parsed.y.toLocaleString('zh-CN', {maximumFractionDigits: 0}); } } } },
            scales: { y: { ticks: { callback: function(v) { return '¥' + (v/10000).toFixed(1) + '万'; } } } }
        }
    });
})();

new Chart(document.getElementById('reasonChart'), {
    type: 'doughnut',
    data: { labels: reasonLabels, datasets: [{ data: reasonData, backgroundColor: ['#dc3545','#28a745','#ffc107','#17a2b8','#6c757d','#e83e8c'] }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'right' } } }
});

new Chart(document.getElementById('holdDaysChart'), {
    type: 'bar',
    data: { labels: hdLabels, datasets: [{ label: '交易笔数', data: hdData, backgroundColor: '#6366f1', borderWidth: 0 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
});

var tbody = document.getElementById('tradeBody');
var sourceTagClass = { 'LLM多源': 'tag-llm', '八步法': 'tag-8step', '漏斗策略': 'tag-funnel' };

function renderTrades(trades) {
    tbody.innerHTML = trades.map(function(t) {
        var cls = sourceTagClass[t.source] || 'tag-llm';
        var pnlSign = t.pnl >= 0 ? '+' : '-';
        var retSign = t.return_pct > 0 ? '+' : '';
        return '<tr><td>' + t.rec_date + '</td><td>' + t.ts_code + '</td><td>' + t.stock_name +
            '</td><td><span class="tag ' + cls + '">' + t.source + '</span></td>' +
            '<td>' + t.buy_price.toFixed(2) + '</td><td>' + t.sell_price.toFixed(2) +
            '</td><td>' + t.target.toFixed(2) + '</td><td>' + t.stop_loss.toFixed(2) +
            '</td><td>' + t.shares + '</td><td>¥' + t.cost.toLocaleString('zh-CN', {maximumFractionDigits: 0}) +
            '</td><td style="color:' + t.pnl_color + ';font-weight:600;">' + pnlSign + '¥' + Math.abs(t.pnl).toLocaleString('zh-CN', {maximumFractionDigits: 0}) +
            '</td><td style="color:' + t.pnl_color + ';">' + retSign + t.return_pct.toFixed(2) + '%</td>' +
            '<td>' + t.sell_reason + '</td><td>' + t.hold_days + '天</td><td>' + t.sell_date + '</td></tr>';
    }).join('');
    document.getElementById('tradeCount').textContent = '共 ' + trades.length + ' 笔';
}
renderTrades(allTrades);

function filterTrades() {
    var q = document.getElementById('tradeSearch').value.toLowerCase();
    renderTrades(allTrades.filter(function(t) {
        return t.ts_code.toLowerCase().indexOf(q) >= 0 || t.stock_name.toLowerCase().indexOf(q) >= 0 || t.source.toLowerCase().indexOf(q) >= 0;
    }));
}

var sortAsc = {};
function sortTable(colIdx) {
    var keys = ['rec_date','ts_code','stock_name','source','buy_price','sell_price','target','stop_loss','shares','cost','pnl','return_pct','sell_reason','hold_days','sell_date'];
    var key = keys[colIdx];
    sortAsc[key] = !sortAsc[key];
    renderTrades(allTrades.slice().sort(function(a, b) {
        var va = a[key], vb = b[key];
        if (typeof va === 'string') return sortAsc[key] ? va.localeCompare(vb) : vb.localeCompare(va);
        return sortAsc[key] ? va - vb : vb - va;
    }));
}
</script>
</body>
</html>'''

    repl = {
        "@@INITIAL_CAPITAL@@": format(INITIAL_CAPITAL, ",.0f"),
        "@@POSITION_PCT@@": "%d%%" % (POSITION_PCT * 100),
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
    conn.close()

    print("\n[4/5] 执行回测...")
    trades = run_backtest_logic(recs, trading_dates, quotes_by_stock)
    daily_equity, skipped_count, executed = simulate_portfolio(trades, trading_dates, quotes_by_stock)

    print("\n[5/5] 计算统计 & 生成HTML...")
    stats = calculate_stats(trades, daily_equity, executed)
    strategy_stats = calculate_strategy_stats(trades)
    monthly_returns = calculate_monthly_returns(daily_equity)
    html = generate_html(stats, strategy_stats, monthly_returns, daily_equity, trades, skipped_count)
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
