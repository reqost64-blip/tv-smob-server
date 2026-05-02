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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type  TEXT NOT NULL,
                signal_id   TEXT,
                payload     TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_approvals (
                approval_id    TEXT PRIMARY KEY,
                chat_id        TEXT NOT NULL,
                command_text   TEXT NOT NULL,
                parsed_action  TEXT NOT NULL,
                old_value      TEXT,
                new_value      TEXT NOT NULL,
                status         TEXT NOT NULL DEFAULT 'pending',
                created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at     TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type    TEXT NOT NULL,
                actor         TEXT NOT NULL,
                command_text  TEXT,
                before_value  TEXT,
                after_value   TEXT,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS account_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                balance         REAL NOT NULL,
                equity          REAL NOT NULL,
                margin          REAL,
                free_margin     REAL,
                margin_level    REAL,
                currency        TEXT,
                account_login   TEXT,
                account_server  TEXT,
                trade_mode      TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions_snapshots (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket         INTEGER NOT NULL,
                symbol         TEXT NOT NULL,
                side           TEXT NOT NULL,
                lot            REAL NOT NULL,
                entry_price    REAL,
                current_price  REAL,
                sl             REAL,
                tp             REAL,
                profit         REAL,
                swap           REAL,
                commission     REAL,
                magic          INTEGER,
                comment        TEXT,
                opened_at      TEXT,
                snapshot_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS deal_reports (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_ticket      INTEGER UNIQUE NOT NULL,
                position_ticket  INTEGER,
                symbol           TEXT NOT NULL,
                side             TEXT NOT NULL,
                lot              REAL NOT NULL,
                entry_price      REAL,
                exit_price       REAL,
                profit           REAL,
                commission       REAL,
                swap             REAL,
                net_profit       REAL,
                opened_at        TEXT,
                closed_at        TEXT,
                reason           TEXT,
                magic            INTEGER,
                comment          TEXT,
                created_at       TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        defaults = {
            "trading_enabled": str(config.TRADING_ENABLED).lower(),
            "dry_run": "true",
            "use_server_lot": "false",
            "global_lot_multiplier": "1.0",
            "max_lot": "0.10",
            "max_daily_loss": "0",
            "max_trades_per_day": "10",
            "allowed_symbols": "XAUUSD,NAS100,DJ30,US500,BTCUSD",
            "symbol_lot_multiplier_XAUUSD": "1.0",
            "symbol_lot_multiplier_NAS100": "1.0",
            "symbol_lot_multiplier_DJ30": "1.0",
            "symbol_lot_multiplier_US500": "1.0",
            "symbol_lot_multiplier_BTCUSD": "1.0",
            "symbol_paused_until_XAUUSD": "",
            "symbol_paused_until_NAS100": "",
            "symbol_paused_until_DJ30": "",
            "symbol_paused_until_US500": "",
            "symbol_paused_until_BTCUSD": "",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)",
                (key, value),
            )
