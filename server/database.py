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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS native_mt5_events (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type     TEXT NOT NULL,
                source         TEXT,
                bot_id         TEXT,
                symbol         TEXT,
                magic_number   INTEGER,
                event_time     TEXT,
                payload        TEXT NOT NULL,
                created_at     TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS native_account_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source          TEXT,
                bot_id          TEXT,
                symbol          TEXT,
                magic_number    INTEGER,
                balance         REAL NOT NULL,
                equity          REAL NOT NULL,
                margin          REAL,
                free_margin     REAL,
                open_positions  INTEGER,
                snapshot_at     TEXT,
                payload         TEXT NOT NULL,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS native_mt5_accounts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source          TEXT,
                symbol          TEXT,
                magic_number    INTEGER,
                balance         REAL NOT NULL,
                equity          REAL NOT NULL,
                margin          REAL,
                free_margin     REAL,
                open_positions  INTEGER,
                snapshot_at     TEXT,
                payload         TEXT NOT NULL,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS native_mt5_active_trades (
                trade_key       TEXT PRIMARY KEY,
                bot_id          TEXT,
                symbol          TEXT,
                magic_number    INTEGER,
                side            TEXT,
                lot             REAL,
                entry           REAL,
                exit_price      REAL,
                current_price   REAL,
                sl              REAL,
                tp1             REAL,
                tp2             REAL,
                tp3             REAL,
                tp1_done        INTEGER NOT NULL DEFAULT 0,
                tp2_done        INTEGER NOT NULL DEFAULT 0,
                tp3_done        INTEGER NOT NULL DEFAULT 0,
                be_done         INTEGER NOT NULL DEFAULT 0,
                closed_percent  REAL,
                profit          REAL,
                balance         REAL,
                equity          REAL,
                status          TEXT,
                last_event_type TEXT,
                opened_at       TEXT,
                updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
                message         TEXT,
                payload         TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS native_mt5_closed_trades (
                trade_key       TEXT PRIMARY KEY,
                bot_id          TEXT,
                symbol          TEXT,
                magic_number    INTEGER,
                side            TEXT,
                lot             REAL,
                entry           REAL,
                exit_price      REAL,
                sl              REAL,
                tp1             REAL,
                tp2             REAL,
                tp3             REAL,
                tp1_done        INTEGER NOT NULL DEFAULT 0,
                tp2_done        INTEGER NOT NULL DEFAULT 0,
                tp3_done        INTEGER NOT NULL DEFAULT 0,
                be_done         INTEGER NOT NULL DEFAULT 0,
                closed_percent  REAL,
                profit          REAL,
                balance         REAL,
                equity          REAL,
                status          TEXT,
                opened_at       TEXT,
                closed_at       TEXT,
                message         TEXT,
                payload         TEXT NOT NULL,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS native_mt5_state (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS native_bot_controls (
                bot_id              TEXT PRIMARY KEY,
                symbol              TEXT,
                magic_number        INTEGER,
                enabled             INTEGER NOT NULL DEFAULT 1,
                paused_reason       TEXT,
                status              TEXT,
                has_position        INTEGER NOT NULL DEFAULT 0,
                last_event_type     TEXT,
                settings_summary    TEXT,
                last_heartbeat_at   TEXT,
                last_account_at     TEXT,
                last_event_at       TEXT,
                last_screenshot_at  TEXT,
                created_at          TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS native_trade_journal (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_uid            TEXT UNIQUE NOT NULL,
                bot_id               TEXT,
                symbol               TEXT,
                magic_number         INTEGER,
                side                 TEXT,
                lot                  REAL,
                entry                REAL,
                sl                   REAL,
                tp1                  REAL,
                tp2                  REAL,
                tp3                  REAL,
                opened_at            TEXT,
                closed_at            TEXT,
                status               TEXT,
                close_reason         TEXT,
                profit               REAL,
                balance_after        REAL,
                equity_after         REAL,
                tp1_done             INTEGER NOT NULL DEFAULT 0,
                tp2_done             INTEGER NOT NULL DEFAULT 0,
                tp3_done             INTEGER NOT NULL DEFAULT 0,
                be_done              INTEGER NOT NULL DEFAULT 0,
                open_screenshot_id   INTEGER,
                close_screenshot_id  INTEGER,
                created_at           TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS native_trade_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_uid     TEXT,
                event_type    TEXT NOT NULL,
                symbol        TEXT,
                bot_id        TEXT,
                side          TEXT,
                price         REAL,
                profit        REAL,
                message       TEXT,
                time          TEXT,
                dedupe_key    TEXT UNIQUE,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history_deals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_ticket     TEXT UNIQUE NOT NULL,
                order_ticket    TEXT,
                position_id     TEXT,
                symbol          TEXT,
                magic_number    INTEGER,
                bot_id          TEXT,
                side            TEXT,
                entry_type      TEXT,
                deal_type       TEXT,
                volume          REAL,
                price           REAL,
                profit          REAL,
                commission      REAL,
                swap            REAL,
                net             REAL,
                deal_time       TEXT,
                comment         TEXT,
                source          TEXT DEFAULT 'mt5_history',
                payload         TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_trades (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id         TEXT,
                symbol         TEXT,
                side           TEXT,
                lots           REAL,
                entry_price    REAL,
                sl_price       REAL,
                tp1_price      REAL,
                tp2_price      REAL,
                tp3_price      REAL,
                open_time      TEXT,
                close_time     TEXT,
                exit_price     REAL,
                profit_money   REAL,
                r_multiple     REAL,
                status         TEXT,
                tp1_hit        INTEGER NOT NULL DEFAULT 0,
                tp2_hit        INTEGER NOT NULL DEFAULT 0,
                source         TEXT DEFAULT 'backtest',
                created_at     TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_backtest_trades_unique
            ON backtest_trades (bot_id, symbol, open_time)
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_backtest_unique
            ON backtest_trades (bot_id, symbol, open_time)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS native_screenshots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_uid     TEXT,
                bot_id        TEXT,
                symbol        TEXT,
                event_type    TEXT,
                file_path     TEXT NOT NULL,
                caption       TEXT,
                time          TEXT,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS native_event_dedupe (
                dedupe_key    TEXT PRIMARY KEY,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        _ensure_columns(
            conn,
            "native_account_snapshots",
            {
                "bot_id": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "native_mt5_active_trades",
            {
                "tp1_done": "INTEGER NOT NULL DEFAULT 0",
                "tp2_done": "INTEGER NOT NULL DEFAULT 0",
                "tp3_done": "INTEGER NOT NULL DEFAULT 0",
                "be_done": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        _ensure_columns(
            conn,
            "native_mt5_closed_trades",
            {
                "tp1_done": "INTEGER NOT NULL DEFAULT 0",
                "tp2_done": "INTEGER NOT NULL DEFAULT 0",
                "tp3_done": "INTEGER NOT NULL DEFAULT 0",
                "be_done": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        _ensure_columns(
            conn,
            "native_mt5_active_trades",
            {
                "trade_uid": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "native_mt5_closed_trades",
            {
                "trade_uid": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "native_mt5_active_trades",
            {
                "tp1_profit": "REAL",
                "tp2_profit": "REAL",
            },
        )
        _ensure_columns(
            conn,
            "native_mt5_closed_trades",
            {
                "tp1_profit": "REAL",
                "tp2_profit": "REAL",
            },
        )
        _ensure_columns(
            conn,
            "native_trade_journal",
            {
                "ticket": "INTEGER",
                "source": "TEXT DEFAULT 'bot'",
                "is_backtest": "INTEGER NOT NULL DEFAULT 0",
                "exit_price": "REAL",
                "commission": "REAL",
                "swap": "REAL",
                "comment": "TEXT",
                "position_id": "TEXT",
                "order_ticket": "TEXT",
                "deal_ticket": "TEXT",
                "lot_initial": "REAL",
                "entry_price": "REAL",
                "sl_price": "REAL",
                "tp1_price": "REAL",
                "tp1_volume": "REAL",
                "tp1_percent": "REAL",
                "tp1_profit": "REAL",
                "tp1_commission": "REAL",
                "tp1_swap": "REAL",
                "tp1_net": "REAL",
                "tp2_price": "REAL",
                "tp2_volume": "REAL",
                "tp2_percent": "REAL",
                "tp2_profit": "REAL",
                "tp2_commission": "REAL",
                "tp2_swap": "REAL",
                "tp2_net": "REAL",
                "tp3_price": "REAL",
                "tp3_volume": "REAL",
                "tp3_percent": "REAL",
                "tp3_profit": "REAL",
                "tp3_commission": "REAL",
                "tp3_swap": "REAL",
                "tp3_net": "REAL",
                "total_profit": "REAL",
                "total_commission": "REAL",
                "total_swap": "REAL",
                "total_net": "REAL",
                "duration_seconds": "INTEGER",
                "screenshot_open": "TEXT",
                "screenshot_tp1": "TEXT",
                "screenshot_close": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "native_mt5_events",
            {
                "event_id": "TEXT",
                "trade_uid": "TEXT",
                "normalized_type": "TEXT",
                "should_notify": "INTEGER NOT NULL DEFAULT 0",
                "telegram_sent": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        _ensure_columns(
            conn,
            "native_trade_events",
            {
                "event_id": "TEXT",
                "normalized_type": "TEXT",
                "should_notify": "INTEGER NOT NULL DEFAULT 0",
                "telegram_sent": "INTEGER NOT NULL DEFAULT 0",
                "screenshot_id": "INTEGER",
                "screenshot_url": "TEXT",
                "payload": "TEXT",
                "magic": "INTEGER",
                "position_id": "TEXT",
                "order_ticket": "TEXT",
                "deal_ticket": "TEXT",
                "tp_index": "INTEGER",
            },
        )
        _ensure_columns(
            conn,
            "history_deals",
            {
                "order_ticket": "TEXT",
                "position_id": "TEXT",
                "symbol": "TEXT",
                "magic_number": "INTEGER",
                "bot_id": "TEXT",
                "side": "TEXT",
                "entry_type": "TEXT",
                "deal_type": "TEXT",
                "volume": "REAL",
                "price": "REAL",
                "profit": "REAL",
                "commission": "REAL",
                "swap": "REAL",
                "net": "REAL",
                "deal_time": "TEXT",
                "comment": "TEXT",
                "source": "TEXT DEFAULT 'mt5_history'",
                "payload": "TEXT",
                "updated_at": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "backtest_trades",
            {
                "tp3_price": "REAL",
                "r_multiple": "REAL",
                "tp1_hit": "INTEGER NOT NULL DEFAULT 0",
                "tp2_hit": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        _ensure_columns(
            conn,
            "native_bot_controls",
            {
                "status": "TEXT",
                "has_position": "INTEGER NOT NULL DEFAULT 0",
                "last_event_type": "TEXT",
                "settings_summary": "TEXT",
            },
        )
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_native_active_lookup
            ON native_mt5_active_trades (bot_id, symbol, magic_number)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_native_events_time
            ON native_mt5_events (event_type, created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_native_account_snapshots_time
            ON native_account_snapshots (created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_native_closed_trades_time
            ON native_mt5_closed_trades (closed_at, created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_native_journal_time
            ON native_trade_journal (opened_at, closed_at, created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_native_journal_bot
            ON native_trade_journal (bot_id, symbol, magic_number)
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_native_journal_ticket
            ON native_trade_journal (ticket)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_native_trade_events_time
            ON native_trade_events (event_type, time, created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_deals_position
            ON history_deals (bot_id, symbol, magic_number, position_id, deal_time)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_native_screenshots_lookup
            ON native_screenshots (bot_id, symbol, created_at)
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


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
