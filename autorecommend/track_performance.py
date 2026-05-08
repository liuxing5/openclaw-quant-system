"""复盘候选股表现"""
import os
from datetime import date, timedelta
import psycopg2
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=int(os.getenv('POSTGRES_PORT') or '5432'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        dbname=os.getenv('POSTGRES_DB'),
    )


def update_tracking():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.ts_code, c.snapshot_date,
               (c.entry_low + c.entry_high) / 2 AS avg_entry,
               c.target_1, c.stop_loss
        FROM daily_candidates c
        WHERE c.selected=TRUE AND c.snapshot_date >= %s;
    """, (date.today() - timedelta(days=30),))
    
    for cand_id, ts, snap_date, avg_entry, t1, sl in cur.fetchall():
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
