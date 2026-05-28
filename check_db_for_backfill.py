"""Quick check - write output to file."""
import os, sys
sys.path.insert(0, '.')
from core.utils.env import load_project_env
load_project_env()

from core.db.connection import get_db_fresh

output = []
try:
    conn = get_db_fresh()
    cur = conn.cursor()

    cur.execute('SELECT MIN(snapshot_date), MAX(snapshot_date), COUNT(*) FROM daily_candidates')
    r = cur.fetchone()
    output.append(f'daily_candidates: {r[0]} ~ {r[1]}, {r[2]} records')

    cur.execute('SELECT source, COUNT(*) FROM daily_candidates GROUP BY source ORDER BY source')
    for r in cur.fetchall():
        output.append(f'  source={r[0]}: {r[1]} records')

    cur.execute("SELECT DISTINCT trade_date FROM daily_quotes WHERE trade_date >= '2026-01-01' AND trade_date <= '2026-01-10' ORDER BY trade_date")
    dates = [r[0] for r in cur.fetchall()]
    output.append(f'daily_quotes Jan 2026 sample: {dates}')

    cur.execute("SELECT DISTINCT trade_date FROM daily_quotes WHERE trade_date >= '2026-05-20' ORDER BY trade_date")
    dates = [r[0] for r in cur.fetchall()]
    output.append(f'daily_quotes May 2026 sample: {dates}')

    cur.execute('SELECT COUNT(*) FROM stock_basic_info')
    r = cur.fetchone()
    output.append(f'stock_basic_info: {r[0]} stocks')

    cur.close()
    conn.close()
    output.append('Done!')
except Exception as e:
    output.append(f'ERROR: {e}')
    import traceback
    output.append(traceback.format_exc())

with open(os.path.join(os.path.dirname(__file__), 'check_db_result.txt'), 'w') as f:
    f.write('\n'.join(output))
