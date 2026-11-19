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

# ---------------------------------------------------------------------------
# Default ITI Routes & Stops Cache
# ---------------------------------------------------------------------------

DEFAULT_ROUTES = [
    {
        "id": 21,
        "name": "خط مدينة نصر",
        "route_code": "NAS",
        "stops": [
            {"id": 31, "stop_name": "ميدان رابعة", "stop_order": 1},
        ],
    },
    {
        "id": 19,
        "name": "خط الطريق الدائري - العاشر",
        "route_code": "RNG",
        "stops": [
            {"id": 23, "stop_name": "موقف العاشر", "stop_order": 1},
            {"id": 24, "stop_name": "المرج", "stop_order": 2},
            {"id": 25, "stop_name": "مسطرد", "stop_order": 3},
            {"id": 26, "stop_name": "بهتيم", "stop_order": 4},
            {"id": 27, "stop_name": "قليوب", "stop_order": 5},
            {"id": 28, "stop_name": "القومية", "stop_order": 6},
        ],
    },
    {
        "id": 26,
        "name": "خط الطريق الدائري - مسطرد",
        "route_code": "RNG-C",
        "stops": [
            {"id": 48, "stop_name": "مسطرد", "stop_order": 3},
            {"id": 49, "stop_name": "بهتيم", "stop_order": 4},
            {"id": 50, "stop_name": "قليوب", "stop_order": 5},
            {"id": 51, "stop_name": "القومية", "stop_order": 6},
        ],
    },
    {
        "id": 16,
        "name": "خط رمسيس",
        "route_code": "RMS",
        "stops": [
            {"id": 12, "stop_name": "التوحيد والنور", "stop_order": 1},
            {"id": 13, "stop_name": "عبد المنعم رياض", "stop_order": 2},
            {"id": 14, "stop_name": "ميدان لبنان", "stop_order": 3},
        ],
    },
    {
        "id": 17,
        "name": "خط المعادي",
        "route_code": "MAD",
        "stops": [
            {"id": 15, "stop_name": "مترو المعادي", "stop_order": 1},
            {"id": 16, "stop_name": "النيل بدراوي", "stop_order": 2},
            {"id": 17, "stop_name": "أكاديمية السادات", "stop_order": 3},
            {"id": 18, "stop_name": "طلعة الدائري", "stop_order": 4},
        ],
    },
    {
        "id": 20,
        "name": "خط العباسية",
        "route_code": "ABS",
        "stops": [
            {"id": 29, "stop_name": "الوايلي", "stop_order": 1},
            {"id": 30, "stop_name": "غمرة", "stop_order": 2},
        ],
    },
    {
        "id": 22,
        "name": "خط التجمع",
        "route_code": "TAG",
        "stops": [
            {"id": 32, "stop_name": "المستشفى الجوي", "stop_order": 1},
            {"id": 33, "stop_name": "بنزينة وطنية", "stop_order": 2},
        ],
    },
    {
        "id": 28,
        "name": "خط الطريق الدائري - العاشر 2",
        "route_code": "RNG-f",
        "stops": [
            {"id": 52, "stop_name": "موقف العاشر", "stop_order": 1},
            {"id": 53, "stop_name": "المرج", "stop_order": 2},
            {"id": 54, "stop_name": "مسطرد", "stop_order": 3},
            {"id": 55, "stop_name": "بهتيم", "stop_order": 4},
            {"id": 56, "stop_name": "قليوب", "stop_order": 5},
            {"id": 57, "stop_name": "القومية", "stop_order": 6},
        ],
    },
    {
        "id": 29,
        "name": "خط الجيزة 2",
        "route_code": "GIZ-f",
        "stops": [
            {"id": 58, "stop_name": "التوحيد والنور", "stop_order": 1},
            {"id": 59, "stop_name": "الطالبية", "stop_order": 2},
            {"id": 60, "stop_name": "العريش", "stop_order": 3},
            {"id": 61, "stop_name": "المريوطية", "stop_order": 4},
            {"id": 62, "stop_name": "مشعل", "stop_order": 5},
            {"id": 63, "stop_name": "الرماية", "stop_order": 6},
        ],
    },
    {
        "id": 24,
        "name": "خط 6 أكتوبر - حصرى 1",
        "route_code": "OCT-D",
        "stops": [
            {"id": 39, "stop_name": "الحصري", "stop_order": 2},
            {"id": 40, "stop_name": "جهينة", "stop_order": 3},
            {"id": 41, "stop_name": "هايبر", "stop_order": 4},
        ],
    },
    {
        "id": 30,
        "name": "خط 6 أكتوبر ليلة القدر 2",
        "route_code": "OCT-h",
        "stops": [
            {"id": 64, "stop_name": "ليلة القدر", "stop_order": 1},
            {"id": 65, "stop_name": "الحصري", "stop_order": 2},
            {"id": 66, "stop_name": "جهينة", "stop_order": 3},
            {"id": 67, "stop_name": "هايبر", "stop_order": 4},
        ],
    },
    {
        "id": 23,
        "name": "خط 6 أكتوبر - حصرى 2",
        "route_code": "OCT-C",
        "stops": [
            {"id": 35, "stop_name": "الحصري", "stop_order": 2},
            {"id": 36, "stop_name": "جهينة", "stop_order": 3},
            {"id": 37, "stop_name": "هايبر", "stop_order": 4},
        ],
    },
    {
        "id": 18,
        "name": "خط 6 أكتوبر ليلة القدر 1",
        "route_code": "OCT",
        "stops": [
            {"id": 19, "stop_name": "ليلة القدر", "stop_order": 1},
            {"id": 20, "stop_name": "الحصري", "stop_order": 2},
            {"id": 21, "stop_name": "جهينة", "stop_order": 3},
            {"id": 22, "stop_name": "هايبر", "stop_order": 4},
        ],
    },
    {
        "id": 25,
        "name": "خط 6 أكتوبر - حصرى 3",
        "route_code": "OCT-E",
        "stops": [
            {"id": 43, "stop_name": "الحصري", "stop_order": 2},
            {"id": 44, "stop_name": "جهينة", "stop_order": 3},
            {"id": 45, "stop_name": "هايبر", "stop_order": 4},
        ],
    },
    {
        "id": 15,
        "name": "خط الجيزة 1",
        "route_code": "GIZ",
        "stops": [
            {"id": 6, "stop_name": "التوحيد والنور", "stop_order": 1},
            {"id": 7, "stop_name": "الطالبية", "stop_order": 2},
            {"id": 8, "stop_name": "العريش", "stop_order": 3},
            {"id": 9, "stop_name": "المريوطية", "stop_order": 4},
            {"id": 10, "stop_name": "مشعل", "stop_order": 5},
            {"id": 11, "stop_name": "الرماية", "stop_order": 6},
        ],
    },
    {
        "id": 31,
        "name": "خط 6 أكتوبر - حصرى 4",
        "route_code": "OCT-j",
        "stops": [
            {"id": 68, "stop_name": "الحصري", "stop_order": 2},
            {"id": 69, "stop_name": "جهينة", "stop_order": 3},
            {"id": 70, "stop_name": "هايبر", "stop_order": 4},
        ],
    },
    {
        "id": 32,
        "name": "خط 6 أكتوبر - حصرى 5",
        "route_code": "OCT-j-k",
        "stops": [
            {"id": 71, "stop_name": "الحصري", "stop_order": 2},
            {"id": 72, "stop_name": "جهينة", "stop_order": 3},
            {"id": 73, "stop_name": "هايبر", "stop_order": 4},
        ],
    },
]


def init_routes_cache(path: Optional[str] = None) -> None:
    """Pre-cache ITI routes and stops so /primary and /backup work fully locally."""
    import json
    import os

    routes_to_load = DEFAULT_ROUTES
    for sp in (os.path.join(os.path.dirname(__file__), "routes_seed.json"), "routes_seed.json"):
        if os.path.exists(sp):
            try:
                with open(sp, "r", encoding="utf-8") as f:
                    seed_data = json.load(f)
                    if isinstance(seed_data, list) and len(seed_data) > 0:
                        normalized_routes = []
                        for sr in seed_data:
                            stops = []
                            for st in sr.get("stops", []):
                                stops.append({
                                    "id": st["id"],
                                    "stop_name": st.get("stopName") or st.get("stop_name", ""),
                                    "stop_order": st.get("stopOrder") or st.get("stop_order", 0),
                                })
                            normalized_routes.append({
                                "id": sr["id"],
                                "name": sr["name"],
                                "route_code": sr.get("routeCode") or sr.get("route_code", ""),
                                "stops": stops,
                            })
                        routes_to_load = normalized_routes
                        break
            except Exception:
                pass

    target_path = path or _DB_PATH
    with _connect(target_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cached_routes (
                id         INTEGER PRIMARY KEY,
                name       TEXT NOT NULL,
                route_code TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cached_stops (
                id         INTEGER PRIMARY KEY,
                route_id   INTEGER NOT NULL,
                stop_name  TEXT NOT NULL,
                stop_order INTEGER DEFAULT 0,
                FOREIGN KEY (route_id) REFERENCES cached_routes(id)
            )
        """)
        for r in routes_to_load:
            conn.execute(
                "INSERT OR REPLACE INTO cached_routes (id, name, route_code) VALUES (?, ?, ?)",
                (r["id"], r["name"], r.get("route_code", "")),
            )
            for s in r["stops"]:
                conn.execute(
                    "INSERT OR REPLACE INTO cached_stops (id, route_id, stop_name, stop_order) VALUES (?, ?, ?, ?)",
                    (s["id"], r["id"], s["stop_name"], s.get("stop_order", 0)),
                )
        conn.commit()


def get_cached_routes() -> list[dict]:
    """Return all pre-cached ITI routes."""
    with _connect(_DB_PATH) as conn:
        rows = conn.execute("SELECT * FROM cached_routes ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_cached_route(route_id: int) -> Optional[dict]:
    """Return a single cached route by its ID."""
    with _connect(_DB_PATH) as conn:
        row = conn.execute("SELECT * FROM cached_routes WHERE id = ?", (route_id,)).fetchone()
    return _row_to_dict(row)


def get_cached_stops(route_id: int) -> list[dict]:
    """Return all pre-cached stops for a given route."""
    with _connect(_DB_PATH) as conn:
        rows = conn.execute(
            "SELECT * FROM cached_stops WHERE route_id = ? ORDER BY stop_order, id",
            (route_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_cached_stop(stop_id: int) -> Optional[dict]:
    """Return a single cached stop by its ID."""
    with _connect(_DB_PATH) as conn:
        row = conn.execute("SELECT * FROM cached_stops WHERE id = ?", (stop_id,)).fetchone()
    return _row_to_dict(row)


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

    init_routes_cache(path)


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
