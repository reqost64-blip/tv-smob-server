import sqlite3
import json
from contextlib import contextmanager
from . import config


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS commands (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id   TEXT UNIQUE NOT NULL,
                payload     TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'queued',
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_reports (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id       TEXT NOT NULL,
                ticket          INTEGER,
                status          TEXT NOT NULL,
                message         TEXT,
                executed_price  REAL,
                executed_at     TEXT,
                received_at     TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
