"""
database.py
-----------
SQLite setup using the standard library only (no ORM).
Creates two tables on first run:
  - users       : login credentials (hashed password)
  - dashboards  : per-user saved BI dashboard configs (PBI or Tableau)

All config values for each dashboard type are stored as a JSON blob in the
`config` column so the schema never needs to change when fields are added.
"""

import sqlite3
import hashlib
import os
import json
from contextlib import contextmanager

# DB file sits inside backend/data/ (alongside users.db and pharma_data.db)
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "dashboards.db")


# ── Connection helper ──────────────────────────────────────────────────────────

@contextmanager
def get_db():
    """Yield a SQLite connection and auto-commit / close it."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row          # rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL") # safe for concurrent reads
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema ─────────────────────────────────────────────────────────────────────

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL UNIQUE,
    password    TEXT    NOT NULL,
    created_at  TEXT    DEFAULT (datetime('now'))
);
"""

CREATE_DASHBOARDS_TABLE = """
CREATE TABLE IF NOT EXISTS dashboards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    type        TEXT    NOT NULL CHECK(type IN ('powerbi', 'tableau')),
    config      TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    DEFAULT (datetime('now'))
);
"""

CREATE_NOTEBOOK_DASHBOARDS_TABLE = """
CREATE TABLE IF NOT EXISTS notebook_dashboards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    notebook_id     TEXT    NOT NULL,
    dashboard_id    INTEGER NOT NULL,
    user_id         TEXT    NOT NULL,
    connected_at    TEXT    DEFAULT (datetime('now')),
    FOREIGN KEY(dashboard_id) REFERENCES dashboards(id) ON DELETE CASCADE,
    UNIQUE(notebook_id, dashboard_id)
);
"""

SEED_ADMIN = """
INSERT OR IGNORE INTO users (username, password)
VALUES ('admin', :pw);
"""


def init_db():
    """Create tables and seed a default admin account."""
    with get_db() as conn:
        conn.execute(CREATE_USERS_TABLE)
        conn.execute(CREATE_DASHBOARDS_TABLE)
        conn.execute(CREATE_NOTEBOOK_DASHBOARDS_TABLE)
        conn.execute(SEED_ADMIN, {"pw": hash_password("admin123")})
    print(f"[DB] Initialised at {os.path.abspath(DB_PATH)}")


# ── Password helpers ───────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def verify_password(plain: str, hashed: str) -> bool:
    return hash_password(plain) == hashed


# ── User queries ───────────────────────────────────────────────────────────────

def get_user_by_username(username: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


# ── Dashboard queries ──────────────────────────────────────────────────────────

def create_dashboard(user_id: str, name: str, type_: str, config: dict) -> dict:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO dashboards (user_id, name, type, config) VALUES (?, ?, ?, ?)",
            (user_id, name, type_, json.dumps(config)),
        )
        row = conn.execute(
            "SELECT * FROM dashboards WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return _parse_dashboard(row)


def get_dashboards_for_user(user_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM dashboards WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [_parse_dashboard(r) for r in rows]


def get_dashboard_by_id(dashboard_id: int, user_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM dashboards WHERE id = ? AND user_id = ?",
            (dashboard_id, user_id),
        ).fetchone()
    return _parse_dashboard(row) if row else None


def delete_dashboard(dashboard_id: int, user_id: str) -> bool:
    with get_db() as conn:
        # Delete from notebook junction table first
        conn.execute(
            "DELETE FROM notebook_dashboards WHERE dashboard_id = ?",
            (dashboard_id,),
        )
        # Then delete the dashboard itself
        cur = conn.execute(
            "DELETE FROM dashboards WHERE id = ? AND user_id = ?",
            (dashboard_id, user_id),
        )
    return cur.rowcount > 0


def _parse_dashboard(row) -> dict:
    """Convert a Row to a plain dict, deserialising the config JSON blob."""
    d = dict(row)
    d["config"] = json.loads(d.get("config") or "{}")
    return d


# ── Notebook-Dashboard junction queries ────────────────────────────────────────

def connect_dashboard_to_notebook(notebook_id: str, dashboard_id: int, user_id: str) -> bool:
    """Connect a dashboard to a specific notebook."""
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO notebook_dashboards (notebook_id, dashboard_id, user_id) VALUES (?, ?, ?)",
            (notebook_id, dashboard_id, user_id),
        )
    return True


def get_dashboards_for_notebook(notebook_id: str, user_id: str) -> list[dict]:
    """Get all dashboards connected to a specific notebook."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT d.* FROM dashboards d
               JOIN notebook_dashboards nd ON d.id = nd.dashboard_id
               WHERE nd.notebook_id = ? AND d.user_id = ?
               ORDER BY nd.connected_at DESC""",
            (notebook_id, user_id),
        ).fetchall()
    return [_parse_dashboard(r) for r in rows]


def disconnect_dashboard_from_notebook(notebook_id: str, dashboard_id: int) -> bool:
    """Remove a dashboard from a specific notebook."""
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM notebook_dashboards WHERE notebook_id = ? AND dashboard_id = ?",
            (notebook_id, dashboard_id),
        )
    return cur.rowcount > 0