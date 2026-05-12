"""Unified writer for daily_candidates.

Both strategies (zuiyou1 / llm_multisource / pre_surge) call write_candidates()
to deposit their picks into the same table. The `source` field distinguishes
them downstream (pusher, tracker, UI all filter by source).

snapshot_date 语义说明：
  snapshot_date 始终是"产出当天"（T 日），不是"建议生效那天"。
  - llm_multisource / run_mode='morning'   : 当日盘前参考，标的应在 T 日盘中观察
  - llm_multisource / run_mode='intraday'  : 当日盘中速递，标的应在 T 日盘中观察
  - llm_multisource / run_mode='afternoon' : 盘后复盘，标的为 T+1 日盘前参考
  - overnight_8step / run_mode='intraday'  : T 日 14:30 盘中初筛，T+1 日盘前最终决策
  - overnight_8step / run_mode='afternoon' : T 日 15:10 盘后定稿，T+1 日开盘介入
  pusher/UI 在文案上分别用"今日参考""明日候选"等区分；查询不应假设 snapshot_date
  代表交易日，请用 (snapshot_date, run_mode) 组合判断。
"""
import json
import math
import time
import sys
from typing import Iterable, Mapping, Any

from .connection import get_db_fresh


def _clean_num(val):
    if val is None:
        return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    return val


def _clean_num(val):
    if val is None:
        return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    return val


_INSERT_SQL = """
INSERT INTO daily_candidates
  (snapshot_date, ts_code, stock_name, mention_count, source_diversity,
   consensus_score, llm_score, quant_score, final_score, logic_tags,
   selected, position_pct, entry_low, entry_high, stop_loss, target_1, target_2,
   sources, run_mode, source)
VALUES (%(snapshot_date)s, %(ts_code)s, %(stock_name)s, %(mention_count)s,
        %(source_diversity)s, %(consensus_score)s, %(llm_score)s, %(quant_score)s,
        %(final_score)s, %(logic_tags)s, %(selected)s, %(position_pct)s,
        %(entry_low)s, %(entry_high)s, %(stop_loss)s, %(target_1)s, %(target_2)s,
        %(sources)s, %(run_mode)s, %(source)s)
ON CONFLICT (snapshot_date, ts_code, run_mode, source) DO UPDATE SET
  final_score   = EXCLUDED.final_score,
  selected      = EXCLUDED.selected,
  entry_low     = EXCLUDED.entry_low,
  entry_high    = EXCLUDED.entry_high,
  stop_loss     = EXCLUDED.stop_loss,
  target_1      = EXCLUDED.target_1,
  target_2      = EXCLUDED.target_2,
  position_pct  = EXCLUDED.position_pct,
  logic_tags    = EXCLUDED.logic_tags,
  sources       = EXCLUDED.sources,
  quant_score   = EXCLUDED.quant_score,
  llm_score     = EXCLUDED.llm_score;
"""


def ensure_source_column(conn) -> None:
    """One-shot migration: add `source` column + new unique constraint.

    Idempotent. Safe to call from any caller before its INSERT.
    """
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'daily_candidates'
              AND column_name = 'source';
        """)
        if not cur.fetchone():
            cur.execute("""
                ALTER TABLE daily_candidates
                ADD COLUMN source VARCHAR(30) NOT NULL DEFAULT 'llm_multisource';
            """)
            conn.commit()

        cur.execute("""
            SELECT conname FROM pg_constraint
            WHERE conname = 'daily_candidates_unique_source';
        """)
        if not cur.fetchone():
            cur.execute("""
                ALTER TABLE daily_candidates
                DROP CONSTRAINT IF EXISTS daily_candidates_unique_mode;
            """)
            cur.execute("""
                ALTER TABLE daily_candidates
                ADD CONSTRAINT daily_candidates_unique_source
                UNIQUE (snapshot_date, ts_code, run_mode, source);
            """)
            conn.commit()
    finally:
        cur.close()


def _normalize(item: Mapping[str, Any], snapshot_date, source: str, run_mode: str) -> dict:
    sources = item.get('sources')
    if sources is not None and not isinstance(sources, str):
        sources = json.dumps(sources, default=str, ensure_ascii=False)
    return {
        'snapshot_date': snapshot_date,
        'ts_code': item['ts_code'],
        'stock_name': item.get('stock_name'),
        'mention_count': item.get('mention_count', 1),
        'source_diversity': item.get('source_diversity', 1),
        'consensus_score': _clean_num(item.get('consensus_score')),
        'llm_score': _clean_num(item.get('llm_score')),
        'quant_score': _clean_num(item.get('quant_score')),
        'final_score': _clean_num(item.get('final_score')),
        'logic_tags': item.get('logic_tags') or [],
        'selected': item.get('selected', True),
        'position_pct': _clean_num(item.get('position_pct', 0)),
        'entry_low': _clean_num(item.get('entry_low')),
        'entry_high': _clean_num(item.get('entry_high')),
        'stop_loss': _clean_num(item.get('stop_loss')),
        'target_1': _clean_num(item.get('target_1')),
        'target_2': _clean_num(item.get('target_2')),
        'sources': sources,
        'run_mode': run_mode,
        'source': source,
    }


def _write_candidates_once(
    items: list, snapshot_date, source: str, run_mode: str, conn=None,
):
    own_conn = conn is None
    if own_conn:
        conn = get_db_fresh()
    try:
        ensure_source_column(conn)
        cur = conn.cursor()
        for it in items:
            cur.execute(_INSERT_SQL, _normalize(it, snapshot_date, source, run_mode))
        conn.commit()
        cur.close()
        return len(items)
    except Exception:
        if own_conn:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
        raise


def write_candidates(
    items: Iterable[Mapping[str, Any]],
    snapshot_date,
    source: str,
    run_mode: str = 'afternoon',
    conn=None,
) -> int:
    """Upsert a batch of candidates. Returns count written.

    Uses a fresh DB connection per call (not session-cached) to avoid
    stale-connection failures in long-running CI jobs.

    Retries up to 2 times on transient errors (connection loss, SSL reset).
    """
    items = list(items)
    if not items:
        return 0

    if conn is not None:
        return _write_candidates_once(items, snapshot_date, source, run_mode, conn=conn)

    last_err = None
    for attempt in range(3):
        try:
            return _write_candidates_once(items, snapshot_date, source, run_mode)
        except Exception as e:
            last_err = e
            if attempt < 2:
                delay = 2 ** attempt
                msg = (
                    f"⚠️ daily_candidates 写入第{attempt + 1}次失败: {e}，"
                    f"{delay}s 后重试..."
                )
                print(msg)
                print(msg, file=sys.stderr)
                time.sleep(delay)
            else:
                msg = f"❌ daily_candidates 写入3次均失败: {e}"
                print(msg)
                print(msg, file=sys.stderr)
                raise
