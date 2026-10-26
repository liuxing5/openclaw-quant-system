# -*- coding: utf-8 -*-
"""一次性回填 ``app_sentiment_history``（近 N 个交易日）。

为什么需要单独脚本
------------------
``market.sentiment(day)`` 每次要对 200 万行日线做 60 日窗口/连板/成交额聚合，
实测单日约 4.5~5 秒；180 个交易日 ≈ 15 分钟。放在 HTTP 请求里做回填会让
/review 页面每次打开都卡住，因此改为离线回填 + 盘中/盘后增量。

用法::

    python webapp/scripts/backfill_sentiment.py            # 近 180 个交易日
    python webapp/scripts/backfill_sentiment.py --days 60  # 只补近 60 日
    python webapp/scripts/backfill_sentiment.py --force    # 重算并覆盖已有行
    python webapp/scripts/backfill_sentiment.py --sleep 0  # 不 sleep

脚本可重复执行：已有行默认跳过（--force 覆盖）。中断后重跑会从缺口继续。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.db import execute, scalar  # noqa: E402
from app.services import market  # noqa: E402


def _jsonable(v):
    """NaN/Infinity -> None：jsonb 不接受 NaN，缺失的量比要落成 null 而不是报错。"""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def _have(day) -> bool:
    return bool(scalar("SELECT 1 FROM app_sentiment_history WHERE trade_date = %s", (day,)))


def _save(day, sent: dict) -> None:
    metrics = {
        "dims": {d["key"]: d["value"] for d in (sent.get("dims") or [])},
        "amount_today": sent.get("amount_today"),
        "amount_ratio": sent.get("amount_ratio"),
        "source": "full_sentiment",
    }
    metrics = _jsonable(metrics)
    score = sent.get("score")
    if isinstance(score, float) and not math.isfinite(score):
        score = None
    execute(
        """
        INSERT INTO app_sentiment_history (trade_date, score, mood, metrics, updated_at)
        VALUES (%s, %s, %s, %s::jsonb, NOW())
        ON CONFLICT (trade_date)
        DO UPDATE SET score = EXCLUDED.score, mood = EXCLUDED.mood,
                      metrics = EXCLUDED.metrics, updated_at = NOW()
        """,
        (day, score, sent.get("level"), json.dumps(metrics, ensure_ascii=False)),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.2, help="每天之间的间隔秒数")
    args = ap.parse_args()

    dates = market.trade_dates(max(1, args.days))
    if not dates:
        print("no trade dates", flush=True)
        return 1
    total = len(dates)
    done = skipped = failed = 0
    t_all = time.time()
    for i, day in enumerate(dates, 1):
        if not args.force and _have(day):
            skipped += 1
            continue
        t0 = time.time()
        try:
            sent = market.sentiment(day)
            if not sent:
                failed += 1
                continue
            _save(day, sent)
            done += 1
            print(
                f"[{i}/{total}] {day} score={sent.get('score')} mood={sent.get('level')} "
                f"({time.time() - t0:.1f}s)",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 单日失败继续
            failed += 1
            print(f"[{i}/{total}] {day} FAILED {exc}", flush=True)
        if args.sleep:
            time.sleep(args.sleep)
    print(
        f"done: filled={done} skipped={skipped} failed={failed} "
        f"elapsed={time.time() - t_all:.0f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
