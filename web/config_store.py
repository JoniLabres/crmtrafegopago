"""
Abstraction layer for config persistence.

Priority:
  1. PostgreSQL (ixc_config table) — used on Vercel and whenever DATABASE_URL is set
  2. Local JSON file — fallback for local development without a database

All public functions are safe to call even when both storage layers are unavailable;
they return the supplied default rather than raising.
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Vercel sets this env var automatically in all deployments
IS_VERCEL = bool(os.getenv("VERCEL"))


# ── helpers ───────────────────────────────────────────────────────────────────

def _db_conn():
    import psycopg2
    return psycopg2.connect(os.getenv("DATABASE_URL", ""))


def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ixc_config (
                key        TEXT PRIMARY KEY,
                value      JSONB NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    conn.commit()


# ── public API ────────────────────────────────────────────────────────────────

def load(key: str, fallback_path: Path | None = None, default=None):
    """Load a config blob: tries PostgreSQL, falls back to JSON file."""
    # 1. PostgreSQL
    db_url = os.getenv("DATABASE_URL", "")
    if db_url:
        try:
            conn = _db_conn()
            _ensure_table(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM ixc_config WHERE key = %s", (key,))
                row = cur.fetchone()
            conn.close()
            if row is not None:
                return row[0]
        except Exception as exc:
            logger.debug("config_store.load DB [%s]: %s", key, exc)

    # 2. Local file
    if fallback_path and fallback_path.exists():
        try:
            with open(fallback_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("config_store.load file [%s]: %s", fallback_path, exc)

    return default


def save(key: str, value, fallback_path: Path | None = None) -> bool:
    """
    Save a config blob.
    - Always writes to PostgreSQL when DATABASE_URL is set.
    - Writes to local file only when NOT running on Vercel (local dev).
    Returns True if at least one backend succeeded.
    """
    ok = False

    # 1. PostgreSQL
    db_url = os.getenv("DATABASE_URL", "")
    if db_url:
        try:
            conn = _db_conn()
            _ensure_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ixc_config (key, value, updated_at)
                    VALUES (%s, %s::jsonb, NOW())
                    ON CONFLICT (key) DO UPDATE
                        SET value = EXCLUDED.value, updated_at = NOW()
                    """,
                    (key, json.dumps(value, ensure_ascii=False)),
                )
            conn.commit()
            conn.close()
            ok = True
        except Exception as exc:
            logger.warning("config_store.save DB [%s]: %s", key, exc)

    # 2. Local file (skip on Vercel — filesystem is read-only)
    if fallback_path and not IS_VERCEL:
        try:
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            with open(fallback_path, "w", encoding="utf-8") as f:
                json.dump(value, f, indent=2, ensure_ascii=False)
            ok = True
        except Exception as exc:
            logger.warning("config_store.save file [%s]: %s", fallback_path, exc)

    return ok
