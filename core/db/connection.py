"""Shared PostgreSQL connection helper.

Replaces the duplicated `get_db()` previously copy-pasted in 9 files.
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor


def get_db(use_dict_cursor: bool = False):
    """Connect using POSTGRES_* env vars. Returns a psycopg2 connection."""
    conn = psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=int(os.getenv('POSTGRES_PORT') or '5432'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        dbname=os.getenv('POSTGRES_DB'),
        sslmode=os.getenv('POSTGRES_SSLMODE', 'require'),
    )
    if use_dict_cursor:
        conn.cursor_factory = RealDictCursor
    return conn


def db_configured() -> bool:
    return bool(os.getenv('POSTGRES_HOST') and os.getenv('POSTGRES_DB'))
