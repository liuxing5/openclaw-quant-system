"""复盘候选股表现"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from datetime import date, timedelta
from loguru import logger

from core.db.connection import get_db
from core.utils.env import load_project_env

load_project_env()


def update_tracking():
    conn = get_db(); cur = conn.cursor()
    
    # 自动迁移：添加缺失的列
    for col in ['t1_hit_target', 't1_hit_stop', 't5_hit_target', 't5_hit_stop', 't20_hit_target', 't20_hit_stop']:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'performance_tracking' AND column_name = %s;
        """, (col,))
        if not cur.fetchone():
            cur.execute(f"ALTER TABLE performance_tracking ADD COLUMN {col} BOOLEAN;")
            conn.commit()
    
    # 自动迁移：添加唯一约束
    try:
        cur.execute("""
            SELECT conname FROM pg_constraint
            WHERE conname = 'performance_tracking_unique';
        """)
        if not cur.fetchone():
            # 先清理重复数据
            cur.execute("""
                DELETE FROM performance_tracking a
                USING performance_tracking b
                WHERE a.ctid < b.ctid
                  AND a.candidate_id = b.candidate_id
                  AND a.rec_date = b.rec_date;
            """)
            conn.commit()
            cur.execute("""
                ALTER TABLE performance_tracking ADD CONSTRAINT performance_tracking_unique
                UNIQUE (candidate_id, rec_date);
            """)
            conn.commit()
    except Exception as e:
        logger.warning(f"约束迁移失败: {e}")
    
    cur.execute("""
        SELECT c.id, c.ts_code, c.snapshot_date, c.source,
               (c.entry_low + c.entry_high) / 2 AS avg_entry,
               c.target_1, c.stop_loss
        FROM daily_candidates c
        WHERE c.selected=TRUE
          AND c.source IN ('llm_multisource', 'overnight_8step')
          AND c.snapshot_date >= %s;
    """, (date.today() - timedelta(days=30),))
    
    for cand_id, ts, snap_date, _source, avg_entry, t1, sl in cur.fetchall():
        if not avg_entry:
            continue
        for offset, col in [(1, 't1'), (5, 't5'), (20, 't20')]:
            check_date = snap_date + timedelta(days=offset)
            if check_date > date.today():
                continue
            cur.execute("""
                SELECT high, low, close FROM daily_quotes
                WHERE ts_code=%s AND trade_date<=%s
                ORDER BY trade_date DESC LIMIT 1;
            """, (ts, check_date))
            r = cur.fetchone()
            if not r:
                continue
            high, low, close = r[0], r[1], r[2]
            ret = (float(close) - float(avg_entry)) / float(avg_entry)
            
            hit_target = (high is not None and t1 is not None and float(high) >= float(t1))
            hit_stop = (low is not None and sl is not None and float(low) <= float(sl))
            
            cur.execute(f"""
                INSERT INTO performance_tracking
                (candidate_id, ts_code, rec_date, entry_price,
                 {col}_high, {col}_low, {col}_close, {col}_return,
                 {col}_hit_target, {col}_hit_stop)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (candidate_id, rec_date) DO UPDATE SET
                {col}_high=EXCLUDED.{col}_high,
                {col}_low=EXCLUDED.{col}_low,
                {col}_close=EXCLUDED.{col}_close,
                {col}_return=EXCLUDED.{col}_return,
                {col}_hit_target=EXCLUDED.{col}_hit_target,
                {col}_hit_stop=EXCLUDED.{col}_hit_stop;
            """, (cand_id, ts, snap_date, avg_entry,
                  high, low, close, ret, hit_target, hit_stop))
    
    conn.commit()
    cur.close(); conn.close()


if __name__ == '__main__':
    update_tracking()
