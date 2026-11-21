"""逐笔审计：43 笔 v3.0 交易的买入价/卖出价/时间 + 后续走势，独立复核 5m 线。

输出：
  docs/overnight_v2/v3_audit.csv / .json
  并在控制台打印逐笔表
"""
import json
import pickle

import numpy as np
import pandas as pd

CACHE = "data/research/morning_exit_bars.pkl"
CORE = "docs/overnight_v2/v3_trades.csv"
OUT = "docs/overnight_v2"


def main():
    core = pd.read_csv(CORE).rename(columns={"signal_date": "date", "ts_code": "code"})
    core["date"] = pd.to_datetime(core["date"])
    from strategy_lab import load as load_panel
    panel_daily = load_panel().set_index(["ts_code", "trade_date"])
    cache = pickle.loads(open(CACHE, "rb").read())
    panel = pd.read_parquet("data/research/panic_enriched.parquet")
    panel["signal_date"] = pd.to_datetime(panel.signal_date)
    panel = panel.set_index(["ts_code", "signal_date"])

    rows = []
    for r in core.itertuples():
        sig = str(r.date.date())
        rec = panel.loc[(r.code, r.date)] if (r.code, r.date) in panel.index else None
        nxt = rec["next_date"] if rec is not None else None
        bars = cache.get((r.code, sig, nxt), [])
        t = [b for b in bars if b["date"] == sig]
        n = [b for b in bars if b["date"] == nxt]
        if not t or not n:
            rows.append({"date": sig, "code": r.code, "error": "no bars"})
            continue
        ebar = next((b for b in t if b["time"] == "1450"), t[-1])
        entry = ebar["close"]
        by = {b["time"]: b for b in n}
        open_px = n[0]["open"]
        gap = open_px / entry - 1

        # 卖出
        if gap >= 0.005:
            sell, sell_time, why = open_px, "09:30", "高开落袋"
        elif gap <= -0.03:
            sell, sell_time, why = open_px, "09:30", "深水开盘砍"
        else:
            stop = entry * 0.98
            sell, sell_time, why = None, None, None
            for b in n:
                if b["time"] > "1000":
                    break
                if b["open"] <= stop:
                    sell, sell_time, why = b["open"], b["time"][:2] + ":" + b["time"][2:], "止损(跳空)"
                    break
                if b["low"] <= stop:
                    sell, sell_time, why = stop, b["time"][:2] + ":" + b["time"][2:], "止损"
                    break
            if sell is None:
                sell, sell_time, why = by["1000"]["close"], "10:00", "持到10:00"

        net = sell / entry - 1 - 0.0025
        t1_close = by.get("1500", n[-1])["close"]
        # 后续走势：T+1 收盘 -> T+2 收盘、T+2 -> T+3（用面板日线）
        t2 = t3 = None
        key = (r.code, r.date)
        if key in panel_daily.index:
            pr = panel_daily.loc[key]
            c1, c2, c3 = float(pr["f_close1"]), float(pr["f_close2"]), float(pr["f_close3"])
            if c1 and pd.notna(c2):
                t2 = c2 / c1 - 1
            if pd.notna(c2) and pd.notna(c3) and c2:
                t3 = c3 / c2 - 1

        rows.append({
            "date": sig, "code": r.code, "name": rec.get("stock_name") if rec is not None else "",
            "buy_time": "14:50", "buy": round(float(entry), 3),
            "next_date": nxt, "t1_open": round(float(open_px), 3),
            "gap": round(float(gap), 4),
            "sell_time": sell_time, "sell": round(float(sell), 3),
            "why": why, "gross": round(float(sell / entry - 1), 4),
            "net": round(float(net), 4),
            "t1_10": round(float(by["1000"]["close"]), 3) if "1000" in by else None,
            "t1_close": round(float(t1_close), 3),
            "t1_close_ret": round(float(t1_close / entry - 1), 4),
            "t2_ret": round(float(t2), 4) if t2 is not None else None,
            "t3_ret": round(float(t3), 4) if t3 is not None else None,
        })

    d = pd.DataFrame(rows)
    print(d.to_string(index=False))
    d.to_csv(f"{OUT}/v3_audit.csv", index=False, encoding="utf-8-sig")
    with open(f"{OUT}/v3_audit.json", "w", encoding="utf-8") as f:
        json.dump(d.to_dict("records"), f, ensure_ascii=False, indent=2)

    ok = d[d["error"].isna()] if "error" in d else d
    print("\n=== 汇总 ===")
    print("trades", len(ok), "win", round((ok.net > 0).mean(), 4),
          "avg_net", round(ok.net.mean(), 4), "sum_net", round(ok.net.sum(), 4))
    print("T+1 收盘相对买入价：", round(ok.t1_close_ret.mean(), 4),
          "  T+1 收盘后 T+2：", round(ok.t2_ret.mean(), 4),
          "  T+2 后 T+3：", round(ok.t3_ret.mean(), 4))
    print("卖出类型：", ok.why.value_counts().to_dict())


if __name__ == "__main__":
    main()
