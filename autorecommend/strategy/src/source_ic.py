"""每周日跑 - 计算每个源的 IC 值并调整权重"""
import os
from datetime import date, timedelta
import psycopg2
import numpy as np
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(BASE_DIR, '.env'))


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'), user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'), dbname=os.getenv('POSTGRES_DB'),
    )


def update_weights():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT e.source_name, AVG(p.t5_return) as avg_ret, COUNT(*) as n,
               CORR(e.strength::float, p.t5_return) as ic
        FROM extracted_recommendations e
        JOIN daily_candidates c ON c.ts_code=e.ts_code AND DATE(e.pub_time)=c.snapshot_date
        JOIN performance_tracking p ON p.candidate_id=c.id
        WHERE e.pub_time > NOW() - INTERVAL '60 days'
          AND p.t5_return IS NOT NULL
        GROUP BY e.source_name
        HAVING COUNT(*) >= 5;
    """)
    
    for source_name, avg_ret, n, ic in cur.fetchall():
        # IC 映射到权重
        new_weight = max(0.2, min(2.0, 1.0 + (ic or 0) * 5))
        # 记录
        cur.execute("""
            INSERT INTO source_performance (source_id, eval_date, rec_count, avg_t5_return, ic_value)
            VALUES (NULL, %s, %s, %s, %s) ON CONFLICT DO NOTHING;
        """, (date.today(), n, avg_ret, ic))
        # 持续负 IC 自动停用 - 记录日志
        if (ic or 0) < -0.05 and n >= 10:
            print(f"WARNING: {source_name} 负 IC={ic:.3f}, 建议降低权重")
    
    conn.commit()
    cur.close(); conn.close()


if __name__ == '__main__':
    update_weights()
