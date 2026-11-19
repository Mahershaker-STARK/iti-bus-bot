"""
database.py — SQLite layer for the ITI Bus Telegram Bot.
All functions are synchronous and use the stdlib sqlite3 module.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row          # rows accessible as dicts
    conn.execute("PRAGMA journal_mode=WAL") # safe for concurrent readers
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Module-level DB path (set once by init_db)
# ---------------------------------------------------------------------------

_DB_PATH: str = "busauto.db"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_db(path: str = "busauto.db") -> None:
    """Create the `users` table if it does not exist and persist the path."""
    global _DB_PATH
    _DB_PATH = path

    with _connect(path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id        INTEGER PRIMARY KEY,
                iti_email          TEXT,
                iti_password       TEXT,
                access_token       TEXT,
                refresh_token      TEXT,
                full_name          TEXT,
                primary_route_id   INTEGER,
                primary_route_name TEXT,
                primary_stop_id    INTEGER,
                primary_stop_name  TEXT,
                backup_route_id    INTEGER,
                backup_route_name  TEXT,
                backup_stop_id     INTEGER,
                backup_stop_name   TEXT,
                auto_book          INTEGER NOT NULL DEFAULT 1,
                booked_today       INTEGER NOT NULL DEFAULT 0,
                is_approved        INTEGER NOT NULL DEFAULT 0,
                created_at         TEXT
            )
        """)
        # Safe migration if table already exists without route_name columns
        for col in ("primary_route_name", "backup_route_name"):
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass
        conn.commit()


def get_user(telegram_id: int) -> Optional[dict]:
    """Return the user row as a dict, or None if not found."""
    with _connect(_DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    return _row_to_dict(row)


def upsert_user(telegram_id: int, **fields) -> None:
    """
    Insert a new user or update specific fields for an existing one.

    On first insert, `created_at` is automatically set to the current UTC
    ISO-8601 timestamp unless explicitly supplied.
    """
    allowed = {
        "iti_email", "iti_password", "access_token", "refresh_token",
        "full_name",
        "primary_route_id", "primary_route_name", "primary_stop_id", "primary_stop_name",
        "backup_route_id",  "backup_route_name",  "backup_stop_id",  "backup_stop_name",
        "auto_book", "booked_today", "is_approved", "created_at",
    }
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"Unknown column(s): {bad}")

    with _connect(_DB_PATH) as conn:
        existing = conn.execute(
            "SELECT telegram_id FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()

        if existing is None:
            # INSERT path — supply defaults for missing columns
            if "created_at" not in fields:
                fields["created_at"] = datetime.now(timezone.utc).isoformat()

            cols = ["telegram_id"] + list(fields.keys())
            placeholders = ", ".join(["?"] * len(cols))
            values = [telegram_id] + list(fields.values())
            conn.execute(
                f"INSERT INTO users ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )
        else:
            # UPDATE path — only touch the supplied fields
            if not fields:
                return  # nothing to do
            assignments = ", ".join(f"{col} = ?" for col in fields)
            values = list(fields.values()) + [telegram_id]
            conn.execute(
                f"UPDATE users SET {assignments} WHERE telegram_id = ?",
                values,
            )
        conn.commit()


def delete_user(telegram_id: int) -> None:
    """Permanently remove a user from the database."""
    with _connect(_DB_PATH) as conn:
        conn.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
        conn.commit()


def get_all_active_users() -> list[dict]:
    """Return all users where is_approved=1, auto_book=1, and booked_today=0."""
    with _connect(_DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT * FROM users
            WHERE is_approved = 1
              AND auto_book   = 1
              AND booked_today = 0
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_users() -> list[dict]:
    """Return every user row (for the admin panel)."""
    with _connect(_DB_PATH) as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def reset_booked_today() -> None:
    """Set booked_today=0 for every user (run at midnight/scheduler reset)."""
    with _connect(_DB_PATH) as conn:
        conn.execute("UPDATE users SET booked_today = 0")
        conn.commit()


def set_booked_today(telegram_id: int, value: bool) -> None:
    """Set booked_today for a single user."""
    with _connect(_DB_PATH) as conn:
        conn.execute(
            "UPDATE users SET booked_today = ? WHERE telegram_id = ?",
            (1 if value else 0, telegram_id),
        )
        conn.commit()


def set_tokens(telegram_id: int, access_token: str, refresh_token: str) -> None:
    """Persist a fresh token pair for a user."""
    upsert_user(telegram_id, access_token=access_token, refresh_token=refresh_token)


def set_route(
    telegram_id: int,
    kind: str,
    route_id: int,
    stop_id: int,
    stop_name: str,
    route_name: str = "",
) -> None:
    """
    Set the primary or backup route/stop for a user.

    Parameters
    ----------
    kind : "primary" | "backup"
    """
    if kind not in ("primary", "backup"):
        raise ValueError(f"kind must be 'primary' or 'backup', got {kind!r}")

    data = {
        f"{kind}_route_id":   route_id,
        f"{kind}_stop_id":    stop_id,
        f"{kind}_stop_name":  stop_name,
    }
    if route_name:
        data[f"{kind}_route_name"] = route_name

    upsert_user(telegram_id, **data)


def toggle_auto_book(telegram_id: int) -> bool:
    """
    Flip the auto_book flag for a user.

    Returns the *new* boolean value.
    Raises ValueError if the user does not exist.
    """
    with _connect(_DB_PATH) as conn:
        row = conn.execute(
            "SELECT auto_book FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"User {telegram_id} not found.")

        new_val = 0 if row["auto_book"] else 1
        conn.execute(
            "UPDATE users SET auto_book = ? WHERE telegram_id = ?",
            (new_val, telegram_id),
        )
        conn.commit()
    return bool(new_val)


def approve_user(telegram_id: int) -> None:
    """Mark a user as approved (is_approved=1)."""
    with _connect(_DB_PATH) as conn:
        conn.execute(
            "UPDATE users SET is_approved = 1 WHERE telegram_id = ?",
            (telegram_id,),
        )
        conn.commit()
