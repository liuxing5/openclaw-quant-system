"""Unified writer for daily_candidates.

Both strategies (zuiyou1 / llm_multisource / pre_surge) call write_candidates()
to deposit their picks into the same table. The `source` field distinguishes
them downstream (pusher, tracker, UI all filter by source).
"""
import json
from typing import Iterable, Mapping, Any

from .connection import get_db


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
        'consensus_score': item.get('consensus_score'),
        'llm_score': item.get('llm_score'),
        'quant_score': item.get('quant_score'),
        'final_score': item.get('final_score'),
        'logic_tags': item.get('logic_tags') or [],
        'selected': item.get('selected', True),
        'position_pct': item.get('position_pct', 0),
        'entry_low': item.get('entry_low'),
        'entry_high': item.get('entry_high'),
        'stop_loss': item.get('stop_loss'),
        'target_1': item.get('target_1'),
        'target_2': item.get('target_2'),
        'sources': sources,
        'run_mode': run_mode,
        'source': source,
    }


def write_candidates(
    items: Iterable[Mapping[str, Any]],
    snapshot_date,
    source: str,
    run_mode: str = 'afternoon',
    conn=None,
) -> int:
    """Upsert a batch of candidates. Returns count written.

    `items` keys (all optional except ts_code):
      ts_code, stock_name, final_score, quant_score, llm_score,
      consensus_score, mention_count, source_diversity,
      logic_tags (list[str]), selected (bool), position_pct,
      entry_low, entry_high, stop_loss, target_1, target_2,
      sources (list[dict] | str — JSONB)

    `source` distinguishes strategy: 'overnight_8step' | 'llm_multisource' | 'pre_surge'.
    `run_mode`: 'morning' | 'intraday' | 'afternoon'.
    """
    items = list(items)
    if not items:
        return 0

    own_conn = conn is None
    if own_conn:
        conn = get_db()
    try:
        ensure_source_column(conn)
        cur = conn.cursor()
        for it in items:
            cur.execute(_INSERT_SQL, _normalize(it, snapshot_date, source, run_mode))
        conn.commit()
        cur.close()
        return len(items)
    finally:
        if own_conn:
            conn.close()
