#!/usr/bin/env python3
"""Patch the 18 corrected high values into quotes_cache.pkl from contam_fix_log.json.
Avoids a full re-fetch from the flaky Supabase. Reversible: we back up the cache first.
"""
import os, json, pickle, shutil
from datetime import date

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(OUT_DIR, "backtest", "quotes_cache.pkl")
AUDIT = os.path.join(OUT_DIR, "contam_fix_log.json")
BACKUP = CACHE + ".bak_contam"


def main():
    with open(AUDIT, encoding="utf-8") as f:
        audit = json.load(f)
    fixable = audit["fixable_high"]

    # Backup cache
    if not os.path.exists(BACKUP):
        shutil.copy2(CACHE, BACKUP)
        print(f"备份缓存 -> {BACKUP}")

    with open(CACHE, "rb") as f:
        d = pickle.load(f)

    patched = 0
    for r in fixable:
        code = r["ts_code"]
        td = date.fromisoformat(r["trade_date"])
        new_h = r["capped_high"]
        if code in d and td in d[code]:
            old_h = d[code][td].get("high")
            d[code][td]["high"] = new_h
            print(f"  cache {code} {td}: high {old_h} -> {new_h}")
            patched += 1
        else:
            print(f"  [skip] cache 未命中 {code} {td} (将从DB重新加载)")

    with open(CACHE, "wb") as f:
        pickle.dump(d, f)
    print(f"\n已修补缓存 {patched}/{len(fixable)} 条。其余将从 DB 增量加载。")


if __name__ == "__main__":
    main()
