#!/usr/bin/env python3
"""
系统性数据污染修正脚本
======================
背景: daily_quotes 中部分记录的最高价(high)被污染, 恰好精确等于 T 日推荐算出的
      target_1 / target_2 (精确到分), 且 high > max(open, close)。29 次"精确命中
      目标价到分"在时间上不可能是巧合 -> 系统性污染, 制造虚假止盈信号, 虚增收益。

修正策略(保守):
  对 score>=70 的高置信污染记录, 若 high > max(open, close) 且该 high 精确命中
  target -> 将 high 上限收敛到 max(open, close)。这符合真实 OHLC 不变式
  (high >= max(open, close) 恒成立, 收敛到其下界即给出最保守的 high 估计,
  消除虚假止盈, 不会制造新的虚假盈利)。

  high <= max(open, close) 的污染记录不产生虚假盈利(盈利已由收盘/开盘实现),
  仅记入审计日志, 不修改。

  low == stop_loss 到分的记录会产生虚假止损(低估收益, 偏保守), 仅记入审计, 不修改。

用法:
  python fix_contamination.py            # 默认 DRY_RUN, 只识别+打印+写日志, 不改库
  python fix_contamination.py --apply    # 真正执行 UPDATE 修正
"""
import os, sys, json, argparse
from datetime import date, datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres",
)
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIT_PATH = os.path.join(OUT_DIR, "contam_fix_log.json")
MATCH_TOL = 0.011  # 命中容忍(分)


def get_connection():
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return psycopg2.connect(db_url, connect_timeout=30, options="-c statement_timeout=180000")
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "aws-1-ap-northeast-1.pooler.supabase.com"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "postgres.qoakbxswwjqfsgbcgepr"),
        password=os.getenv("POSTGRES_PASSWORD", "wYFBB91zViSrk2vl"),
        dbname=os.getenv("POSTGRES_DB", "postgres"),
        sslmode=os.getenv("POSTGRES_SSLMODE", "require"),
        connect_timeout=30,
        options="-c statement_timeout=180000",
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行 UPDATE 修正")
    args = ap.parse_args()
    dry = not args.apply

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # === 识别: T 日推荐 targets == T+1 交易日 high (到分), 且 high>max(o,c), score>=70 ===
    sql = """
    SELECT
        dc.ts_code,
        dc.stock_name,
        dc.source,
        dc.snapshot_date,
        (dc.snapshot_date + interval '1 day')::date AS trade_date,
        dc.final_score,
        dc.target_1,
        dc.target_2,
        dc.stop_loss,
        q.open,
        q.high,
        q.low,
        q.close
    FROM daily_candidates dc
    JOIN daily_quotes q
      ON q.ts_code = dc.ts_code
     AND q.trade_date = (dc.snapshot_date + interval '1 day')::date
    WHERE dc.selected = TRUE
      AND dc.final_score >= 70
      AND dc.target_1 IS NOT NULL
      AND dc.target_2 IS NOT NULL
      AND (
            ABS(q.high - dc.target_1) < %s
         OR ABS(q.high - dc.target_2) < %s
      )
    ORDER BY dc.snapshot_date, dc.final_score DESC, dc.ts_code;
    """
    cur.execute(sql, (MATCH_TOL, MATCH_TOL))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()

    fixable = []   # high>max(o,c): 真正制造虚假盈利, 需收敛 high
    flagged = []   # 命中 target 但 high<=max(o,c): 不产生虚假盈利, 仅记录
    low_stop = []  # low==stop 到分: 虚假止损(低估, 偏保守), 仅记录

    for r in rows:
        o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
        moc = max(o, c)
        t1 = float(r["target_1"]) if r["target_1"] is not None else None
        t2 = float(r["target_2"]) if r["target_2"] is not None else None
        sl = float(r["stop_loss"]) if r["stop_loss"] is not None else None
        hit_t = "t1" if (t1 is not None and abs(h - t1) < MATCH_TOL) else "t2"

        rec = {
            "ts_code": r["ts_code"], "stock_name": r["stock_name"], "source": r["source"],
            "snapshot_date": str(r["snapshot_date"]), "trade_date": str(r["trade_date"]),
            "final_score": float(r["final_score"]),
            "open": o, "high": h, "low": l, "close": c, "max_oc": moc,
            "target_1": t1, "target_2": t2, "stop_loss": sl,
            "hit_target": hit_t, "capped_high": round(moc, 3),
        }
        if h > moc + 1e-9:
            fixable.append(rec)
        else:
            flagged.append(rec)

        # 虚假止损扫描
        if sl is not None and abs(l - sl) < MATCH_TOL and l < min(o, c) - 1e-9:
            low_stop.append({
                "ts_code": r["ts_code"], "trade_date": str(r["trade_date"]),
                "low": l, "stop_loss": sl, "open": o, "close": c,
            })

    # 去重 fixable (按 ts_code+trade_date)
    seen = set()
    fixable_uniq = []
    for r in fixable:
        k = (r["ts_code"], r["trade_date"])
        if k in seen:
            continue
        seen.add(k)
        fixable_uniq.append(r)

    audit = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "match_tolerance": MATCH_TOL,
        "score_threshold": 70,
        "counts": {
            "matched_total": len(rows),
            "fixable_high": len(fixable_uniq),
            "flagged_noaction": len(flagged),
            "phantom_stop": len(low_stop),
        },
        "fixable_high": fixable_uniq,
        "flagged_noaction": flagged,
        "phantom_stop": low_stop,
    }
    with open(AUDIT_PATH, "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=2)

    print(f"匹配总数(命中target且score>=70): {len(rows)}")
    print(f"  可修正(fix high>max(o,c)): {len(fixable_uniq)}")
    print(f"  仅标记无动作(high<=max(o,c)): {len(flagged)}")
    print(f"  虚假止损(low==stop, 仅记录): {len(low_stop)}")
    print(f"审计日志已写入: {AUDIT_PATH}")
    print()

    if dry:
        print("=== DRY RUN: 以下记录将被修正(high 收敛到 max(open,close)) ===")
        for r in fixable_uniq:
            print(f"  {r['ts_code']} {r['trade_date']} {r['source']} "
                  f"score={r['final_score']:.1f} O={r['open']} H={r['high']}({r['hit_target']}) "
                  f"C={r['close']} -> cap {r['capped_high']}")
        print("\n(未执行任何 UPDATE。使用 --apply 真正修正)")
        conn.close()
        return

    # === APPLY ===
    print("=== APPLY: 执行 UPDATE ===")
    upcur = conn.cursor()
    applied = 0
    for r in fixable_uniq:
        new_h = round(r["capped_high"], 3)
        upcur.execute(
            "UPDATE daily_quotes SET high = %s "
            "WHERE ts_code = %s AND trade_date = %s AND high > %s;",
            (new_h, r["ts_code"], r["trade_date"], new_h),
        )
        if upcur.rowcount > 0:
            applied += 1
            print(f"  UPD {r['ts_code']} {r['trade_date']}: high {r['high']} -> {new_h}")
    conn.commit()
    upcur.close()
    conn.close()
    print(f"\n已修正 {applied} 条记录。审计日志: {AUDIT_PATH}")


if __name__ == "__main__":
    main()
