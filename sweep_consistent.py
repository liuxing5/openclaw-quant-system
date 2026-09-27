#!/usr/bin/env python3
"""Consistent parameter sweep: reuses backtest_revenue.py's EXACT engine.

The previous param_sweep8.py had its own (profit-first, lighter-cleaning) logic
that disagreed with backtest_revenue.py (stop-first, stricter cleaning), causing
the optimization and the final report to contradict each other.

This sweep imports backtest_revenue as a module and only varies the 4 parameters,
so every combo is evaluated with the identical engine that produces the final
HTML report. Results are therefore guaranteed consistent with backtest_revenue.py.

Run:  .venv/Scripts/python.exe sweep_consistent.py
"""
import importlib
import itertools
import statistics
import backtest_revenue as bt

# === Micro-grid (same as param_sweep8) ===
PROFIT_PCTS = [10.0, 10.5, 11.0, 11.5, 12.0]
STOP_PCTS = [7.0, 7.5, 8.0, 8.5, 9.0]
MAX_HOLDS = [7, 8, 9]
SCORE_THRESHOLDS = [65, 68, 70, 72, 75]


def main():
    # Load ALL recommendations (disable SQL score filter), full data once.
    bt.SCORE_THRESHOLD = -1
    conn = bt.get_connection()
    recs = bt.load_recommendations(conn)
    print(f"Loaded {len(recs)} recommendations (all scores)")

    trading_dates = bt.load_trading_dates(conn)
    ts_codes = set(r["ts_code"] for r in recs)
    min_date = min(r["snapshot_date"] for r in recs)
    max_date = max(r["snapshot_date"] for r in recs)
    print(f"Trading dates: {len(trading_dates)}, stocks: {len(ts_codes)}, "
          f"range {min_date}~{max_date}")

    quotes = bt.load_quotes_batch(conn, ts_codes, min_date, max_date)
    quotes_by_stock = bt.organize_quotes(quotes)
    bt.validate_and_clean_quotes(quotes_by_stock)
    conn.close()
    print(f"Quotes organized for {len(quotes_by_stock)} stocks\n")

    INITIAL = bt.INITIAL_CAPITAL
    days = len(trading_dates)

    results = []
    total = len(PROFIT_PCTS) * len(STOP_PCTS) * len(MAX_HOLDS) * len(SCORE_THRESHOLDS)
    done = 0

    for pf, sl, mh, st in itertools.product(
            PROFIT_PCTS, STOP_PCTS, MAX_HOLDS, SCORE_THRESHOLDS):
        # Vary engine parameters
        bt.PROFIT_PCT = pf
        bt.STOP_PCT = sl
        bt.MAX_HOLD_DAYS = mh

        recs_f = [r for r in recs if (r.get("final_score") or 0) >= st]
        trades = bt.run_backtest_logic(recs_f, trading_dates, quotes_by_stock)
        daily_equity, skipped, executed = bt.simulate_portfolio(
            trades, trading_dates, quotes_by_stock)
        stats = bt.calculate_stats(trades, daily_equity, executed)

        final = stats.get("final_equity", INITIAL)
        total_return = (final - INITIAL) / INITIAL * 100
        max_dd = stats.get("max_drawdown", 0)
        ann = ((final / INITIAL) ** (252 / days) - 1) * 100 if days and final > 0 else 0
        calmar = ann / abs(max_dd) if max_dd != 0 else 0

        results.append({
            "profit_pct": pf, "stop_pct": sl, "max_hold": mh, "score_threshold": st,
            "return_pct": round(total_return, 2),
            "max_dd": round(max_dd, 2),
            "trades": stats.get("total_trades", 0),
            "win_rate": round(stats.get("win_rate", 0), 1),
            "calmar": round(calmar, 2),
            "final_equity": round(final, 0),
        })
        done += 1
        if done % 25 == 0 or done == total:
            print(f"  {done}/{total} done")

    results.sort(key=lambda x: x["calmar"], reverse=True)
    print("\nTOP 20 by Calmar (using backtest_revenue engine):")
    print(f"{'#':>3} {'PF%':>5} {'SL%':>4} {'Hold':>4} {'Score':>5} "
          f"{'Return':>8} {'MaxDD':>7} {'Trd':>4} {'Win%':>5} {'Calmar':>7}")
    for i, r in enumerate(results[:20]):
        print(f"{i+1:>3} {r['profit_pct']:>4}% {r['stop_pct']:>3}% {r['max_hold']:>3}d "
              f"{r['score_threshold']:>5} {r['return_pct']:>+7.2f}% {r['max_dd']:>+6.2f}% "
              f"{r['trades']:>4} {r['win_rate']:>5.1f}% {r['calmar']:>7.2f}")

    best = results[0]
    print(f"\nBEST by Calmar: {best['profit_pct']}%/{best['stop_pct']}%/{best['max_hold']}d/"
          f"score{best['score_threshold']} -> +{best['return_pct']}%/"
          f"{best['max_dd']}% Calmar={best['calmar']} (win {best['win_rate']}%, "
          f"{best['trades']} trades)")

    with open("sweep_consistent.json", "w") as f:
        import json
        json.dump(results, f, indent=2)
    print("\nSaved sweep_consistent.json")


if __name__ == "__main__":
    main()
