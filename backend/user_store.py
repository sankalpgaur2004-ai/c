# user_store.py
# SQLite-backed persistent store for users, sessions, data sources, and chats.

import sqlite3
import json
import secrets
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).resolve().parent / "data" / "users.db"


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db():
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          TEXT PRIMARY KEY,
                email       TEXT UNIQUE NOT NULL,
                first_name  TEXT DEFAULT '',
                last_name   TEXT DEFAULT '',
                persona     TEXT DEFAULT NULL,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS user_sessions (
                token       TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at  TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS user_data_sources (
                id               TEXT PRIMARY KEY,
                user_id          TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                alias            TEXT NOT NULL,
                db_type          TEXT NOT NULL,
                connection_config TEXT NOT NULL,
                selected_tables  TEXT DEFAULT '[]',
                created_at       TEXT DEFAULT (datetime('now')),
                updated_at       TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, alias)
            );

            CREATE TABLE IF NOT EXISTS user_schema (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                alias       TEXT NOT NULL,
                schema_yaml TEXT NOT NULL,
                updated_at  TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, alias)
            );

            CREATE TABLE IF NOT EXISTS user_chats (
                id         TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title      TEXT DEFAULT 'New Chat',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS user_messages (
                id           TEXT PRIMARY KEY,
                chat_id      TEXT NOT NULL REFERENCES user_chats(id) ON DELETE CASCADE,
                role         TEXT NOT NULL,
                question     TEXT,
                sql_query    TEXT,
                summary      TEXT,
                chart_data   TEXT,
                chart_type   TEXT,
                columns_json TEXT,
                data_json    TEXT,
                error        TEXT,
                source_filter TEXT,
                follow_up_questions TEXT,
                out_of_scope BOOLEAN,
                referenced_documents TEXT,
                created_at   TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS chat_shares (
                token         TEXT PRIMARY KEY,
                chat_id       TEXT NOT NULL,
                notebook_id   TEXT,
                owner_id      TEXT NOT NULL,
                chat_title    TEXT,
                notebook_name TEXT,
                persona       TEXT,
                owner_name    TEXT,
                messages_json TEXT NOT NULL,
                created_at    TEXT DEFAULT (datetime('now')),
                revoked       BOOLEAN DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS chat_share_forks (
                token              TEXT NOT NULL,
                viewer_id          TEXT NOT NULL,
                forked_notebook_id TEXT NOT NULL,
                forked_chat_id     TEXT NOT NULL,
                created_at         TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (token, viewer_id)
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user   ON user_sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_sources_user    ON user_data_sources(user_id);
            CREATE INDEX IF NOT EXISTS idx_chats_user      ON user_chats(user_id);
            CREATE INDEX IF NOT EXISTS idx_messages_chat   ON user_messages(chat_id);
            CREATE INDEX IF NOT EXISTS idx_shares_chat      ON chat_shares(chat_id);
            CREATE INDEX IF NOT EXISTS idx_shares_owner     ON chat_shares(owner_id);
        """)
    # Add persona column if it doesn't exist (migration)
    with _conn() as c:
        try:
            c.execute("ALTER TABLE users ADD COLUMN persona TEXT DEFAULT NULL")
        except sqlite3.OperationalError:
            pass
    logger.info("User DB initialised at %s", _DB_PATH)
    _migrate_db()
    _ensure_notebooks_table()


def _migrate_db():
    """Apply schema migrations to handle new columns"""
    try:
        with _conn() as c:
            # Add follow_up_questions column if it doesn't exist
            try:
                c.execute("ALTER TABLE user_messages ADD COLUMN follow_up_questions TEXT")
                logger.info("Migration: Added follow_up_questions column")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Add out_of_scope column if it doesn't exist
            try:
                c.execute("ALTER TABLE user_messages ADD COLUMN out_of_scope BOOLEAN")
                logger.info("Migration: Added out_of_scope column")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Add referenced_documents column if it doesn't exist
            try:
                c.execute("ALTER TABLE user_messages ADD COLUMN referenced_documents TEXT")
                logger.info("Migration: Added referenced_documents column")
            except sqlite3.OperationalError:
                pass  # Column already exists
    except Exception as e:
        logger.error(f"Migration error: {e}")


# ── Users ──────────────────────────────────────────────────────────────────────

def upsert_user(email: str, first_name: str = "", last_name: str = "") -> Dict:
    uid = hashlib.sha256(email.lower().encode()).hexdigest()[:32]
    with _conn() as c:
        c.execute("""
            INSERT INTO users (id, email, first_name, last_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                first_name = excluded.first_name,
                last_name  = excluded.last_name
        """, (uid, email.lower(), first_name, last_name))
    # Read AFTER the `with` block above has exited (and committed the write).
    # get_user_by_id() opens its own connection — querying it while the insert's
    # transaction was still open (the previous version of this function) meant
    # a brand-new user's first-ever login always raced the commit and read back
    # None, crashing the SSO callback at user["id"].
    return get_user_by_id(uid)


def get_user_by_id(user_id: str) -> Optional[Dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def update_user_persona(user_id: str, persona: str) -> Optional[Dict]:
    with _conn() as c:
        c.execute("UPDATE users SET persona = ? WHERE id = ?", (persona, user_id))
    return get_user_by_id(user_id)


# ── Sessions ───────────────────────────────────────────────────────────────────

def create_session(user_id: str, hours: int = 8) -> str:
    token = secrets.token_urlsafe(48)
    expires = (datetime.utcnow() + timedelta(hours=hours)).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO user_sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires)
        )
    return token


def get_session(token: str) -> Optional[Dict]:
    with _conn() as c:
        row = c.execute("""
            SELECT s.token, s.user_id, s.expires_at,
                   u.email, u.first_name, u.last_name, u.persona
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
              AND datetime(s.expires_at) > datetime('now')
        """, (token,)).fetchone()
    return dict(row) if row else None


def delete_session(token: str):
    with _conn() as c:
        c.execute("DELETE FROM user_sessions WHERE token = ?", (token,))


def purge_expired_sessions():
    with _conn() as c:
        c.execute("DELETE FROM user_sessions WHERE datetime(expires_at) <= datetime('now')")


# ── Data Sources ───────────────────────────────────────────────────────────────

def save_data_source(user_id: str, alias: str, db_type: str,
                     connection_config: Dict, selected_tables: List[str],
                     notebook_id: str = None):
    import uuid
    sid = str(uuid.uuid4())
    with _conn() as c:
        c.execute("""
            INSERT INTO user_data_sources
                (id, user_id, alias, db_type, connection_config, selected_tables, notebook_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(user_id, alias) DO UPDATE SET
                db_type           = excluded.db_type,
                connection_config = excluded.connection_config,
                selected_tables   = excluded.selected_tables,
                notebook_id       = excluded.notebook_id,
                updated_at        = datetime('now')
        """, (sid, user_id, alias, db_type,
              json.dumps(connection_config), json.dumps(selected_tables), notebook_id))


def update_selected_tables(user_id: str, alias: str, selected_tables: List[str]):
    with _conn() as c:
        c.execute("""
            UPDATE user_data_sources
            SET selected_tables = ?, updated_at = datetime('now')
            WHERE user_id = ? AND alias = ?
        """, (json.dumps(selected_tables), user_id, alias))


def get_user_data_sources(user_id: str, notebook_id: str = None) -> List[Dict]:
    with _conn() as c:
        if notebook_id:
            rows = c.execute(
                "SELECT * FROM user_data_sources WHERE user_id = ? AND notebook_id = ? ORDER BY created_at",
                (user_id, notebook_id)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM user_data_sources WHERE user_id = ? ORDER BY created_at",
                (user_id,)
            ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["connection_config"] = json.loads(d["connection_config"])
        d["selected_tables"]   = json.loads(d["selected_tables"])
        result.append(d)
    return result


def delete_data_source(user_id: str, alias: str):
    with _conn() as c:
        c.execute(
            "DELETE FROM user_data_sources WHERE user_id = ? AND alias = ?",
            (user_id, alias)
        )


# ── Chats ──────────────────────────────────────────────────────────────────────

def create_chat(user_id: str, chat_id: str, title: str = "New Chat"):
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO user_chats (id, user_id, title) VALUES (?, ?, ?)",
            (chat_id, user_id, title)
        )


def update_chat_title(chat_id: str, title: str):
    """Update chat title (persona is immutable and cannot be changed)."""
    with _conn() as c:
        c.execute(
            "UPDATE user_chats SET title = ?, updated_at = datetime('now') WHERE id = ?",
            (title, chat_id)
        )


def get_chat_persona(chat_id: str) -> Optional[str]:
    """Get the persona for a chat (returns None if not set)."""
    with _conn() as c:
        row = c.execute(
            "SELECT persona FROM user_chats WHERE id = ?",
            (chat_id,)
        ).fetchone()
    return row[0] if row else None


def get_user_chats(user_id: str) -> List[Dict]:
    with _conn() as c:
        rows = c.execute("""
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   COUNT(m.id) AS message_count
            FROM user_chats c
            LEFT JOIN user_messages m ON m.chat_id = c.id
            WHERE c.user_id = ?
            GROUP BY c.id
            ORDER BY c.updated_at DESC
        """, (user_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_chat(chat_id: str, user_id: str):
    with _conn() as c:
        c.execute(
            "DELETE FROM user_chats WHERE id = ? AND user_id = ?",
            (chat_id, user_id)
        )


def create_notebook_chat(user_id: str, notebook_id: str, title: str = "New Chat", persona: str = None) -> Dict:
    """Create a new chat session scoped to a notebook with immutable persona."""
    import uuid
    cid = str(uuid.uuid4())
    with _conn() as c:
        c.execute(
            "INSERT INTO user_chats (id, user_id, notebook_id, title, persona) VALUES (?, ?, ?, ?, ?)",
            (cid, user_id, notebook_id, title, persona)
        )
    return get_notebook_chat_detail(cid, user_id)


def get_notebook_chat_detail(chat_id: str, user_id: str) -> Optional[Dict]:
    """Get chat details including persona and message count."""
    with _conn() as c:
        row = c.execute("""
            SELECT c.id, c.title, c.notebook_id, c.persona, c.created_at, c.updated_at,
                   COUNT(m.id) AS message_count
            FROM user_chats c
            LEFT JOIN user_messages m ON m.chat_id = c.id
            WHERE c.id = ? AND c.user_id = ?
            GROUP BY c.id
        """, (chat_id, user_id)).fetchone()
    return dict(row) if row else None


def list_notebook_chats(notebook_id: str, user_id: str) -> List[Dict]:
    """List all chats for a notebook, newest first, with message count and persona."""
    with _conn() as c:
        rows = c.execute("""
            SELECT c.id, c.title, c.notebook_id, c.persona, c.created_at, c.updated_at,
                   COUNT(m.id) AS message_count
            FROM user_chats c
            LEFT JOIN user_messages m ON m.chat_id = c.id
            WHERE c.notebook_id = ? AND c.user_id = ?
            GROUP BY c.id
            ORDER BY c.updated_at DESC
        """, (notebook_id, user_id)).fetchall()
    return [dict(r) for r in rows]


# ── Messages ───────────────────────────────────────────────────────────────────

def save_message(chat_id: str, msg_id: str, role: str, **fields):
    import uuid
    with _conn() as c:
        c.execute("""
            INSERT OR REPLACE INTO user_messages
                (id, chat_id, role, question, sql_query, summary,
                 chart_data, chart_type, columns_json, data_json, error, source_filter,
                 follow_up_questions, out_of_scope, referenced_documents)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            msg_id, chat_id, role,
            fields.get("question"),
            fields.get("sql_query"),
            fields.get("summary"),
            json.dumps(fields.get("chart_data")) if fields.get("chart_data") else None,
            fields.get("chart_type"),
            json.dumps(fields.get("columns")) if fields.get("columns") else None,
            json.dumps(fields.get("data")) if fields.get("data") else None,
            fields.get("error"),
            fields.get("source_filter"),
            json.dumps(fields.get("follow_up_questions")) if fields.get("follow_up_questions") else None,
            fields.get("out_of_scope"),
            json.dumps(fields.get("referenced_documents")) if fields.get("referenced_documents") else None,
        ))
        # bump chat updated_at
        c.execute(
            "UPDATE user_chats SET updated_at = datetime('now') WHERE id = ?",
            (chat_id,)
        )


def get_chat_messages(chat_id: str) -> List[Dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM user_messages WHERE chat_id = ? ORDER BY created_at",
            (chat_id,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("chart_data"):
            try: d["chart_data"] = json.loads(d["chart_data"])
            except: pass
        if d.get("columns_json"):
            try: d["columns"] = json.loads(d["columns_json"])
            except: d["columns"] = []
        if d.get("data_json"):
            try: d["data"] = json.loads(d["data_json"])
            except: d["data"] = []
        if d.get("follow_up_questions"):
            try: d["follow_up_questions"] = json.loads(d["follow_up_questions"])
            except: d["follow_up_questions"] = []
        if d.get("referenced_documents"):
            try: d["referenced_documents"] = json.loads(d["referenced_documents"])
            except: d["referenced_documents"] = []
        result.append(d)
    return result


# ── Chat sharing (read-only, frozen-snapshot links) ────────────────────────────
#
# A share is a point-in-time copy of a chat's messages, addressed by an opaque
# token. Any authenticated user (not just the owner) can fetch a share via its
# token — that's the whole point of "send someone a link" — but the fetch is
# scoped to exactly that one snapshot. There is no path from a share token to
# the owner's notebook, other chats, or data sources.

def _share_row_to_dict(row) -> Optional[Dict]:
    if not row:
        return None
    d = dict(row)
    try:
        d["messages"] = json.loads(d.pop("messages_json") or "[]")
    except (TypeError, ValueError):
        d["messages"] = []
    return d


def get_active_chat_share(chat_id: str) -> Optional[Dict]:
    """Return the current non-revoked share for a chat, if one exists."""
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM chat_shares WHERE chat_id = ? AND revoked = 0 "
            "ORDER BY created_at DESC LIMIT 1",
            (chat_id,)
        ).fetchone()
    return _share_row_to_dict(row)


def create_chat_share(chat_id: str, notebook_id: str, owner_id: str) -> Dict:
    """
    Snapshot a chat's current messages and issue a read-only share token.

    Idempotent: if an active share already exists for this chat, it's returned
    unchanged rather than re-snapshotting — the whole point is a link frozen
    at first-share time. Revoke it first if a fresh snapshot is wanted.
    """
    existing = get_active_chat_share(chat_id)
    if existing:
        return existing

    chat = get_notebook_chat_detail(chat_id, owner_id)
    if not chat:
        raise ValueError("Chat not found")

    notebook   = get_notebook(notebook_id, owner_id)
    owner      = get_user_by_id(owner_id)
    owner_name = (
        f"{owner.get('first_name', '')} {owner.get('last_name', '')}".strip()
        if owner else None
    ) or (owner.get("email") if owner else None)

    messages = get_chat_messages(chat_id)
    token    = secrets.token_urlsafe(24)

    with _conn() as c:
        c.execute("""
            INSERT INTO chat_shares
                (token, chat_id, notebook_id, owner_id, chat_title, notebook_name,
                 persona, owner_name, messages_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            token, chat_id, notebook_id, owner_id,
            chat.get("title"),
            notebook.get("name") if notebook else None,
            chat.get("persona"),
            owner_name,
            json.dumps(messages),
        ))

    return get_active_chat_share(chat_id)


def get_chat_share(token: str) -> Optional[Dict]:
    """
    Fetch a share by token. Deliberately NOT scoped to any user_id — any
    authenticated caller may view it, since the token itself is the access
    grant. Returns None if the token is unknown or has been revoked.
    """
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM chat_shares WHERE token = ? AND revoked = 0",
            (token,)
        ).fetchone()
    return _share_row_to_dict(row)


def revoke_chat_share(chat_id: str, owner_id: str) -> bool:
    """Revoke the active share for a chat. Only the owning user may revoke."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE chat_shares SET revoked = 1 WHERE chat_id = ? AND owner_id = ? AND revoked = 0",
            (chat_id, owner_id)
        )
    return cur.rowcount > 0


# ── Share forks (turning a share link into a private, live copy) ──────────────
#
# Opening a share link doesn't just show a snapshot — it forks the chat,
# its messages, and its connected sources into a brand-new notebook owned by
# the viewer, so they land on the normal chat UI and can keep asking
# questions against a live (but fully separate) copy. This table just
# remembers "viewer X already has a fork of share token Y" so reopening the
# same link is idempotent instead of creating a new notebook every time.

def get_share_fork(token: str, viewer_id: str) -> Optional[Dict]:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM chat_share_forks WHERE token = ? AND viewer_id = ?",
            (token, viewer_id)
        ).fetchone()
    return dict(row) if row else None


def record_share_fork(token: str, viewer_id: str, notebook_id: str, chat_id: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO chat_share_forks (token, viewer_id, forked_notebook_id, forked_chat_id) "
            "VALUES (?, ?, ?, ?)",
            (token, viewer_id, notebook_id, chat_id)
        )


# ── Notebooks ──────────────────────────────────────────────────────────────────

def _ensure_notebooks_table():
    """Create notebooks table and migrate data sources if not already done."""
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS notebooks (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name            TEXT NOT NULL DEFAULT 'Untitled notebook',
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_notebooks_user ON notebooks(user_id);
        """)
        # Add notebook_id to data sources if not present
        try:
            c.execute("ALTER TABLE user_data_sources ADD COLUMN notebook_id TEXT DEFAULT NULL")
            logger.info("Migration: Added notebook_id to user_data_sources")
        except sqlite3.OperationalError:
            pass
        # Add notebook_id to user_schema if not present
        try:
            c.execute("ALTER TABLE user_schema ADD COLUMN notebook_id TEXT DEFAULT NULL")
            logger.info("Migration: Added notebook_id to user_schema")
        except sqlite3.OperationalError:
            pass
        # Add persona, instructions, description to notebooks
        for col, default in [
            ("persona", "NULL"),
            ("instructions", "NULL"),
            ("description", "NULL"),
        ]:
            try:
                c.execute(f"ALTER TABLE notebooks ADD COLUMN {col} TEXT DEFAULT {default}")
                logger.info(f"Migration: Added {col} to notebooks")
            except sqlite3.OperationalError:
                pass

        # Mark notebooks created by opening a share link, and who shared it —
        # lets the homepage list these separately under "Shared chats".
        try:
            c.execute("ALTER TABLE notebooks ADD COLUMN is_shared INTEGER DEFAULT 0")
            logger.info("Migration: Added is_shared to notebooks")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE notebooks ADD COLUMN shared_by TEXT DEFAULT NULL")
            logger.info("Migration: Added shared_by to notebooks")
        except sqlite3.OperationalError:
            pass

        # Add notebook_id to user_chats so chats are scoped per project
        try:
            c.execute("ALTER TABLE user_chats ADD COLUMN notebook_id TEXT DEFAULT NULL")
            logger.info("Migration: Added notebook_id to user_chats")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_chats_notebook ON user_chats(notebook_id)")
        except sqlite3.OperationalError:
            pass


def create_notebook(
    user_id: str,
    name: str = "Untitled project",
    persona: str = None,
    instructions: str = None,
    description: str = None,
    is_shared: bool = False,
    shared_by: str = None,
) -> Dict:
    import uuid
    nid = str(uuid.uuid4())
    with _conn() as c:
        c.execute(
            "INSERT INTO notebooks (id, user_id, name, persona, instructions, description, is_shared, shared_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (nid, user_id, name, persona, instructions, description, int(is_shared), shared_by)
        )
    return get_notebook(nid, user_id)


def get_notebook(notebook_id: str, user_id: str) -> Optional[Dict]:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM notebooks WHERE id = ? AND user_id = ?",
            (notebook_id, user_id)
        ).fetchone()
    if not row:
        return None
    nb = dict(row)
    nb.update(_notebook_source_counts(notebook_id))
    return nb


def list_notebooks(user_id: str) -> List[Dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM notebooks WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,)
        ).fetchall()
    result = []
    for r in rows:
        nb = dict(r)
        nb.update(_notebook_source_counts(nb["id"]))
        result.append(nb)
    return result


def rename_notebook(notebook_id: str, user_id: str, name: str) -> Optional[Dict]:
    with _conn() as c:
        c.execute(
            "UPDATE notebooks SET name = ?, updated_at = datetime('now') WHERE id = ? AND user_id = ?",
            (name, notebook_id, user_id)
        )
    return get_notebook(notebook_id, user_id)


def update_notebook_config(
    notebook_id: str,
    user_id: str,
    name: str = None,
    persona: str = None,
    instructions: str = None,
    description: str = None,
) -> Optional[Dict]:
    fields, values = [], []
    if name is not None:        fields.append("name = ?");         values.append(name)
    if persona is not None:     fields.append("persona = ?");      values.append(persona)
    if instructions is not None:fields.append("instructions = ?"); values.append(instructions)
    if description is not None: fields.append("description = ?");  values.append(description)
    if not fields:
        return get_notebook(notebook_id, user_id)
    fields.append("updated_at = datetime('now')")
    values += [notebook_id, user_id]
    with _conn() as c:
        c.execute(
            f"UPDATE notebooks SET {', '.join(fields)} WHERE id = ? AND user_id = ?",
            values
        )
    return get_notebook(notebook_id, user_id)


def delete_notebook(notebook_id: str, user_id: str):
    """Delete notebook and all its associated sources, schema, and chats."""
    with _conn() as c:
        # Delete scoped data sources
        c.execute(
            "DELETE FROM user_data_sources WHERE notebook_id = ? AND user_id = ?",
            (notebook_id, user_id)
        )
        # Delete scoped schema
        c.execute(
            "DELETE FROM user_schema WHERE notebook_id = ? AND user_id = ?",
            (notebook_id, user_id)
        )
        # Delete all chats for this notebook (messages cascade via FK)
        c.execute(
            "DELETE FROM user_chats WHERE notebook_id = ? AND user_id = ?",
            (notebook_id, user_id)
        )
        # Delete the notebook itself
        c.execute(
            "DELETE FROM notebooks WHERE id = ? AND user_id = ?",
            (notebook_id, user_id)
        )
    logger.info("Deleted notebook %s and all its resources", notebook_id)


def _notebook_source_counts(notebook_id: str) -> Dict:
    """Return db_count, table_count, doc_count, website_count, dashboard_count, source_count for a notebook."""
    with _conn() as c:
        # Pull selected_tables alongside the row count so we can derive table_count
        # from the same query instead of a second COUNT(*) round-trip.
        rows = c.execute(
            "SELECT selected_tables FROM user_data_sources WHERE notebook_id = ?",
            (notebook_id,)
        ).fetchall()

    db_count = len(rows)
    table_count = 0
    for r in rows:
        try:
            tables = json.loads(r["selected_tables"] or "[]")
            table_count += len(tables)
        except (TypeError, ValueError):
            # Malformed/legacy rows shouldn't blow up the count — just skip them
            pass

    # Dashboard count is in a separate database (dashboards.db)
    try:
        from database import get_db
        with get_db() as c:
            dashboard_count = c.execute(
                "SELECT COUNT(*) FROM notebook_dashboards WHERE notebook_id = ?",
                (notebook_id,)
            ).fetchone()[0]
    except Exception:
        dashboard_count = 0

    # Document count from document manager — split websites from regular docs
    doc_count     = 0
    website_count = 0
    try:
        from document_manager import DocumentManager
        dm   = DocumentManager()
        docs = dm.list_documents(notebook_id=notebook_id) or []
        for d in docs:
            tags        = d.get("tags") or {}
            source_type = tags.get("source_type", "")
            filename    = d.get("filename", "")
            if source_type == "website" or filename.endswith(".website.txt"):
                website_count += 1
            else:
                doc_count += 1
    except Exception as e:
        logger.error(f"Failed to count documents for notebook {notebook_id}: {e}")

    return {
        "db_count":        db_count,
        "table_count":     table_count,
        "doc_count":       doc_count,
        "website_count":   website_count,
        "dashboard_count": dashboard_count,
        "source_count":    db_count + doc_count + website_count + dashboard_count,
    }


def get_notebook_data_sources(notebook_id: str, user_id: str) -> List[Dict]:
    return get_user_data_sources(user_id, notebook_id=notebook_id)