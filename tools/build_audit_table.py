"""生成逐笔审计表 + 月度表（含买入/卖出价、时间、原因、后续走势）。"""
import json
import pickle

import numpy as np
import pandas as pd
import psycopg2

CACHE = "data/research/morning_exit_bars.pkl"
CORE = "docs/overnight_v2/v3_trades.csv"
OUT = "docs/overnight_v2"
DSN = ("postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl"
       "@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres")


def main():
    core = pd.read_csv(CORE).rename(columns={"signal_date": "date", "ts_code": "code"})
    core["date"] = pd.to_datetime(core["date"])
    cache = pickle.loads(open(CACHE, "rb").read())
    from strategy_lab import load as load_panel
    daily = load_panel().set_index(["ts_code", "trade_date"])
    panel = pd.read_parquet("data/research/panic_enriched.parquet")
    panel["signal_date"] = pd.to_datetime(panel.signal_date)
    panel = panel.set_index(["ts_code", "signal_date"])

    conn = psycopg2.connect(DSN, connect_timeout=30)
    try:
        cur = conn.cursor()
        cur.execute("select ts_code, stock_name from stock_basic_info")
        names = dict(cur.fetchall())
    finally:
        conn.close()

    rows = []
    for r in core.itertuples():
        sig = str(r.date.date())
        rec = panel.loc[(r.code, r.date)]
        nxt = rec["next_date"]
        bars = cache[(r.code, sig, nxt)]
        t = [b for b in bars if b["date"] == sig]
        n = [b for b in bars if b["date"] == nxt]
        ebar = next((b for b in t if b["time"] == "1450"), t[-1])
        entry = ebar["close"]
        by = {b["time"]: b for b in n}
        open_px = n[0]["open"]
        gap = open_px / entry - 1
        if gap >= 0.005:
            sell, st, why = open_px, "09:30", "高开落袋"
        elif gap <= -0.03:
            sell, st, why = open_px, "09:30", "深水砍"
        else:
            stop = entry * 0.98
            sell, st, why = None, None, None
            for b in n:
                if b["time"] > "1000":
                    break
                if b["open"] <= stop:
                    sell, st, why = b["open"], b["time"][:2] + ":" + b["time"][2:], "止损(跳空)"
                    break
                if b["low"] <= stop:
                    sell, st, why = stop, b["time"][:2] + ":" + b["time"][2:], "−2%止损"
                    break
            if sell is None:
                sell, st, why = by["1000"]["close"], "10:00", "持到10:00"
        net = sell / entry - 1 - 0.0025
        prow = daily.loc[(r.code, r.date)]
        c1, c2, c3 = float(prow["f_close1"]), float(prow["f_close2"]), float(prow["f_close3"])
        rows.append({
            "date": sig, "code": r.code, "name": names.get(r.code, ""),
            "buy_time": "14:50", "buy": round(float(entry), 2),
            "sell_time": st, "sell": round(float(sell), 2), "reason": why,
            "gross": round(float(sell / entry - 1), 4),
            "fee": 0.0025, "net": round(float(net), 4),
            "next_date": nxt,
            "gap": round(float(gap), 4),
            "t1_1000": round(float(by["1000"]["close"]), 2),
            "t1_close": round(float(n[-1]["close"]), 2),
            "t1_close_ret": round(float(n[-1]["close"] / entry - 1), 4),
            "t2_ret": round(float(c2 / c1 - 1), 4),
            "t3_ret": round(float(c3 / c2 - 1), 4),
        })
    d = pd.DataFrame(rows)
    d["date"] = pd.to_datetime(d["date"])

    # 月度：按活跃日等权组合
    d["ym"] = d.date.dt.strftime("%Y-%m")
    day = d.groupby("date").net.mean()
    day.index = pd.to_datetime(day.index)
    mon = []
    for ym, g in d.groupby("ym"):
        days = day[day.index.strftime("%Y-%m") == ym]
        mon.append({"ym": ym, "trades": len(g), "wins": int((g.net > 0).sum()),
                    "losses": int((g.net <= 0).sum()),
                    "trade_sum": round(float(g.net.sum()), 4),
                    "monthly": round(float((1 + days).prod() - 1), 4)})
    m = pd.DataFrame(mon)
    print(d.to_string(index=False))
    print("\n=== 月度 ===")
    print(m.to_string(index=False))
    print("\n=== 汇总 ===")
    print("trades", len(d), "win", round((d.net > 0).mean(), 4),
          "avg_net", round(d.net.mean(), 4),
          "avg_win", round(d[d.net > 0].net.mean(), 4),
          "avg_loss", round(d[d.net <= 0].net.mean(), 4),
          "profit_factor", round(d[d.net > 0].net.sum() / abs(d[d.net <= 0].net.sum()), 2),
          "sum_net", round(d.net.sum(), 4))
    print("T+1 close mean", round(d.t1_close_ret.mean(), 4),
          "T+2 mean", round(d.t2_ret.mean(), 4), "T+3 mean", round(d.t3_ret.mean(), 4))
    d.to_csv(f"{OUT}/v3_audit.csv", index=False, encoding="utf-8-sig")
    m.to_csv(f"{OUT}/v3_monthly.csv", index=False, encoding="utf-8-sig")
    with open(f"{OUT}/v3_audit.json", "w", encoding="utf-8") as f:
        json.dump({"trades": d.to_dict("records"), "monthly": m.to_dict("records")},
                  f, ensure_ascii=False, indent=2, default=str)


if __name__ == "__main__":
    main()
