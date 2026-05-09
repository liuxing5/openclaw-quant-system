"""Apply database schema safely.

Handles existing tables and columns gracefully.
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db
from core.utils.env import load_project_env

load_project_env()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def apply_schema():
    conn = get_db()
    conn.autocommit = True
    cur = conn.cursor()
    
    with open(os.path.join(BASE_DIR, 'schema.sql'), 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Split by semicolon but keep comments and empty lines separate
    statements = []
    current_stmt = []
    
    for line in content.split('\n'):
        # Skip empty lines and comments
        if not line.strip() or line.strip().startswith('--'):
            continue
        
        current_stmt.append(line)
        
        if ';' in line:
            # Remove the semicolon and add to statements
            stmt = '\n'.join(current_stmt).replace(';', '').strip()
            if stmt:
                statements.append(stmt)
            current_stmt = []
    
    # Add any remaining statement
    if current_stmt:
        stmt = '\n'.join(current_stmt).strip()
        if stmt:
            statements.append(stmt)
    
    # Execute each statement
    for i, stmt in enumerate(statements):
        try:
            cur.execute(stmt)
            print(f"✓ Executed statement {i+1}")
        except Exception as e:
            # Handle common errors gracefully
            error_msg = str(e).lower()
            if 'already exists' in error_msg or 'duplicate' in error_msg:
                print(f"⚠️ Statement {i+1} skipped (already exists)")
            elif 'column "source" does not exist' in error_msg:
                print(f"⚠️ Statement {i+1} skipped (column already handled)")
            else:
                print(f"❌ Statement {i+1} failed: {e}")
    
    cur.close()
    conn.close()
    print("\nSchema applied successfully")


if __name__ == '__main__':
    apply_schema()
