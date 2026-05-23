"""Apply database schema safely.

Handles existing tables and columns gracefully.
Uses schema_versions table to skip already-applied schema/migrations.
"""
import os
import sys
import hashlib
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db_fresh
from core.utils.env import load_project_env

load_project_env()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _ensure_version_table(cur):
    """Create schema_versions tracking table if not exists."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_versions (
            name VARCHAR(200) PRIMARY KEY,
            content_hash VARCHAR(64) NOT NULL,
            applied_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)


def _is_applied(cur, name, content_hash):
    """Check if a schema/migration with the same hash was already applied."""
    cur.execute(
        "SELECT content_hash FROM schema_versions WHERE name = %s",
        (name,)
    )
    row = cur.fetchone()
    if row is None:
        return False
    # If content changed (different hash), re-apply
    return row[0] == content_hash


def _mark_applied(cur, name, content_hash):
    """Mark a schema/migration as applied."""
    cur.execute("""
        INSERT INTO schema_versions (name, content_hash)
        VALUES (%s, %s)
        ON CONFLICT (name) DO UPDATE SET content_hash = EXCLUDED.content_hash, applied_at = NOW();
    """, (name, content_hash))


def _content_hash(content):
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def apply_schema():
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()

        # Ensure tracking table exists (must be in its own transaction before we use it)
        _ensure_version_table(cur)
        conn.commit()

        # ---- Apply main schema.sql ----
        schema_path = os.path.join(BASE_DIR, 'schema.sql')
        with open(schema_path, 'r', encoding='utf-8') as f:
            content = f.read()

        schema_hash = _content_hash(content)

        if _is_applied(cur, 'schema.sql', schema_hash):
            print("schema.sql already applied (hash matched), skipping")
        else:
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
                except Exception as e:
                    error_msg = str(e).lower()
                    if 'already exists' in error_msg or 'duplicate' in error_msg:
                        pass  # Expected for IF NOT EXISTS fallback
                    else:
                        print(f"Statement {i+1} failed: {e}")

            conn.commit()
            _mark_applied(cur, 'schema.sql', schema_hash)
            conn.commit()
            print(f"schema.sql applied ({len(statements)} statements)")

        # ---- Apply migrations ----
        migrations_dir = os.path.join(BASE_DIR, 'migrations')
        if os.path.isdir(migrations_dir):
            migration_files = sorted(f for f in os.listdir(migrations_dir) if f.endswith('.sql'))
            for mf in migration_files:
                mf_path = os.path.join(migrations_dir, mf)
                with open(mf_path, 'r', encoding='utf-8') as f:
                    mcontent = f.read().strip()
                if not mcontent:
                    continue

                mhash = _content_hash(mcontent)

                if _is_applied(cur, mf, mhash):
                    print(f"Migration {mf} already applied, skipping")
                    continue

                try:
                    cur.execute(mcontent)
                    conn.commit()
                    _mark_applied(cur, mf, mhash)
                    conn.commit()
                    print(f"Migration {mf} applied")
                except Exception as e:
                    error_msg = str(e).lower()
                    if 'already exists' in error_msg or 'does not exist' in error_msg or 'already the type' in error_msg:
                        # Mark as applied even if skipped, so we don't retry
                        _mark_applied(cur, mf, mhash)
                        conn.commit()
                        print(f"Migration {mf} skipped ({e})")
                    else:
                        print(f"Migration {mf} failed: {e}")
                        conn.rollback()

        cur.close()
        print("\nSchema check complete")
    finally:
        if conn and not conn.closed:
            conn.close()


if __name__ == '__main__':
    apply_schema()
