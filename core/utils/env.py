"""Locate and load the project-root .env regardless of caller location.

Replaces the 9-copy `BASE_DIR = dirname(__file__); load_dotenv(BASE_DIR/.env)` pattern.
"""
import os
from dotenv import load_dotenv, find_dotenv


def load_project_env() -> None:
    """Walk up from CWD looking for .env, then load it. No-op in CI (env vars already set)."""
    path = find_dotenv(usecwd=True)
    if path:
        load_dotenv(path)
