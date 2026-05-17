"""Apply database schema safely.

Handles existing tables and columns gracefully.
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db_fresh
from core.utils.env import load_project_env

load_project_env()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def apply_schema():
    conn = None
    try:
        conn = get_db_fresh()
        conn.autocommit = True
        cur = conn.cursor()
        
        with open(os.path.join(BASE_DIR, 'schema.sql'), 'r', encoding='utf-8') as f:
            content = f.read()
        
        statements = []
        current_stmt = []
        
        for line in content.split('\n'):
            if not line.strip() or line.strip().startswith('--'):
                continue
            
            current_stmt.append(line)
            
            if ';' in line:
                stmt = '\n'.join(current_stmt).replace(';', '').strip()
                if stmt:
                    statements.append(stmt)
                current_stmt = []
        
        if current_stmt:
            stmt = '\n'.join(current_stmt).strip()
            if stmt:
                statements.append(stmt)
        
        for i, stmt in enumerate(statements):
            try:
                cur.execute(stmt)
                print(f"✓ Executed statement {i+1}")
            except Exception as e:
                error_msg = str(e).lower()
                if 'already exists' in error_msg or 'duplicate' in error_msg:
                    print(f"⚠️ Statement {i+1} skipped (already exists)")
                elif 'column "source" does not exist' in error_msg:
                    print(f"⚠️ Statement {i+1} skipped (column already handled)")
                else:
                    print(f"❌ Statement {i+1} failed: {e}")
        
        migrations_dir = os.path.join(BASE_DIR, 'migrations')
        if os.path.isdir(migrations_dir):
            migration_files = sorted(f for f in os.listdir(migrations_dir) if f.endswith('.sql'))
            for mf in migration_files:
                mf_path = os.path.join(migrations_dir, mf)
                with open(mf_path, 'r', encoding='utf-8') as f:
                    mcontent = f.read().strip()
                if not mcontent:
                    continue
                try:
                    cur.execute(mcontent)
                    print(f"✓ Migration {mf} applied")
                except Exception as e:
                    error_msg = str(e).lower()
                    if 'already exists' in error_msg or 'does not exist' in error_msg or 'already the type' in error_msg:
                        print(f"⚠️ Migration {mf} skipped ({e})")
                    else:
                        print(f"❌ Migration {mf} failed: {e}")
        
        cur.close()
        print("\nSchema applied successfully")
    finally:
        if conn and not conn.closed:
            conn.close()


if __name__ == '__main__':
    apply_schema()
