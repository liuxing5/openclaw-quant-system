import os
import psycopg2
from core.db.connection import get_db_fresh

conn = None
try:
    conn = get_db_fresh()
    cur = conn.cursor()

    print("=== daily_candidates 最新数据 (按日期倒序) ===")
    cur.execute("""
        SELECT snapshot_date, ts_code, stock_name, final_score,
               consensus_score, llm_score, quant_score, logic_tags, selected
        FROM daily_candidates
        ORDER BY snapshot_date DESC, final_score DESC
        LIMIT 20
    """)
    rows = cur.fetchall()
    for r in rows:
        print(f"日期:{r[0]} 代码:{r[1]} 名称:{r[2]}")
        print(f"  综合分:{r[3]} 共识分:{r[4]} LLM分:{r[5]} 量化分:{r[6]}")
        print(f"  标签:{r[7]} 选中:{r[8]}")
        print()

    print("\n=== daily_candidates 表结构 ===")
    cur.execute("""
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_name = 'daily_candidates'
        ORDER BY ordinal_position
    """)
    cols = cur.fetchall()
    for c in cols:
        nullable = "NULL" if c[2] == 'YES' else "NOT NULL"
        default = f" DEFAULT {c[3]}" if c[3] else ""
        print(f"  {c[0]}: {c[1]} {nullable}{default}")

    print("\n=== 数据时间范围 ===")
    cur.execute("SELECT MIN(snapshot_date), MAX(snapshot_date) FROM daily_candidates")
    date_range = cur.fetchone()
    print(f"  最早: {date_range[0]}, 最晚: {date_range[1]}")

    print("\n=== 选中的标的 (selected=True) ===")
    cur.execute("""
        SELECT snapshot_date, ts_code, stock_name, final_score, position_pct, entry_low, entry_high, stop_loss
        FROM daily_candidates
        WHERE selected = TRUE
        ORDER BY snapshot_date DESC
    """)
    selected = cur.fetchall()
    for s in selected:
        print(f"  {s[0]}: {s[1]} {s[2]} 评分:{s[3]} 仓位:{s[4]}% 入场:[{s[5]}-{s[6]}] 止损:{s[7]}")

    cur.close()
finally:
    if conn and not conn.closed:
        conn.close()
