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
        SELECT c.id, c.ts_code, c.snapshot_date, c.entry_high, c.target_1, c.stop_loss
        FROM daily_candidates c
        WHERE c.selected=TRUE AND c.snapshot_date >= %s;
    """, (date.today() - timedelta(days=30),))
    
    for cand_id, ts, snap_date, entry, t1, sl in cur.fetchall():
        for offset, col in [(1, 't1'), (5, 't5'), (20, 't20')]:
            check_date = snap_date + timedelta(days=offset)
            if check_date > date.today(): continue
            cur.execute("""
                SELECT close FROM daily_quotes
                WHERE ts_code=%s AND trade_date<=%s
                ORDER BY trade_date DESC LIMIT 1;
            """, (ts, check_date))
            r = cur.fetchone()
            if not r or not entry: continue
            ret = (float(r[0]) - float(entry)) / float(entry)
            cur.execute(f"""
                INSERT INTO performance_tracking
                (candidate_id, ts_code, rec_date, entry_price, {col}_close, {col}_return)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING;
            """, (cand_id, ts, snap_date, entry, r[0], ret))
    
    conn.commit()
    cur.close(); conn.close()


if __name__ == '__main__':
    update_tracking()
