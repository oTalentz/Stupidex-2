"""SQLite-backed session and message storage.

Schema migrations are tracked in the `schema_version` table — we apply
each migration idempotently. The first run creates v1.
"""
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .config import DATA_DIR

DB_FILE = DATA_DIR / "stupidex.db"

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_INITIALIZED = False


@dataclass
class Session:
    id: str
    title: str
    created_at: float
    updated_at: float
    provider: str
    model: str
    pinned: bool = False
    archived: bool = False
    message_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "provider": self.provider,
            "model": self.model,
            "pinned": self.pinned,
            "archived": self.archived,
            "message_count": self.message_count,
        }


@dataclass
class DBMessage:
    id: int
    session_id: str
    role: str
    type: str
    content: str
    tool_calls: list[dict]
    tool_call_id: str | None
    metadata: dict
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "type": self.type,
            "content": self.content,
            "tool_calls": self.tool_calls,
            "tool_call_id": self.tool_call_id,
            "metadata": self.metadata,
        }


def _connect() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


_MIGRATIONS: list[str] = [
    # v1: initial schema
    """
    CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions (
        id          TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL,
        provider    TEXT NOT NULL,
        model       TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS messages (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id      TEXT NOT NULL,
        role            TEXT NOT NULL,
        type            TEXT NOT NULL DEFAULT 'text',
        content         TEXT NOT NULL DEFAULT '',
        tool_calls_json TEXT NOT NULL DEFAULT '[]',
        tool_call_id    TEXT,
        metadata_json   TEXT NOT NULL DEFAULT '{}',
        created_at      REAL NOT NULL,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
    CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
    """,
    # v2: add pinned/archived flags
    """
    ALTER TABLE sessions ADD COLUMN pinned   INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE sessions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
    CREATE INDEX IF NOT EXISTS idx_sessions_pinned   ON sessions(pinned   DESC, updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_sessions_archived ON sessions(archived,        updated_at DESC);
    """,
]


def _ensure_schema(conn: sqlite3.Connection) -> None:
    global _SCHEMA_INITIALIZED
    with _SCHEMA_LOCK:
        if _SCHEMA_INITIALIZED:
            return
        cur = conn.cursor()
        # The schema_version table itself must exist before we can read from it.
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
        )
        cur.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
        current = cur.fetchone()[0] or 0
        for i, sql in enumerate(_MIGRATIONS):
            v = i + 1
            if v <= current:
                continue
            cur.executescript(sql)
            cur.execute("INSERT INTO schema_version(version, applied_at) VALUES (?, ?)", (v, time.time()))
        conn.commit()
        _SCHEMA_INITIALIZED = True


@contextmanager
def db_cursor() -> Iterator[sqlite3.Cursor]:
    """Thread-safe connection. Use this for every DB call.

    Always wraps in a transaction. Safe to call from multiple threads
    (WAL mode + per-call connections).
    """
    conn = _connect()
    try:
        _ensure_schema(conn)
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Eager init (optional — schema is also created lazily on first use)."""
    with db_cursor() as cur:
        cur.execute("SELECT 1")


# ====================== Sessions ======================

def create_session(provider: str, model: str, title: str = "Nova conversa") -> Session:
    sid = uuid.uuid4().hex[:12]
    now = time.time()
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO sessions(id, title, created_at, updated_at, provider, model) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, title, now, now, provider, model),
        )
    return Session(sid, title, now, now, provider, model, False, False, 0)


def list_sessions(include_archived: bool = False) -> list[Session]:
    with db_cursor() as cur:
        if include_archived:
            rows = cur.execute(
                "SELECT s.id, s.title, s.created_at, s.updated_at, s.provider, s.model, "
                "       s.pinned, s.archived, "
                "       (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS mc "
                "FROM sessions s ORDER BY s.pinned DESC, s.updated_at DESC"
            ).fetchall()
        else:
            rows = cur.execute(
                "SELECT s.id, s.title, s.created_at, s.updated_at, s.provider, s.model, "
                "       s.pinned, s.archived, "
                "       (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS mc "
                "FROM sessions s WHERE s.archived = 0 ORDER BY s.pinned DESC, s.updated_at DESC"
            ).fetchall()
    return [
        Session(
            id=r["id"], title=r["title"], created_at=r["created_at"], updated_at=r["updated_at"],
            provider=r["provider"], model=r["model"], pinned=bool(r["pinned"]),
            archived=bool(r["archived"]), message_count=r["mc"],
        )
        for r in rows
    ]


def get_session(sid: str) -> Session | None:
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT s.id, s.title, s.created_at, s.updated_at, s.provider, s.model, "
            "       s.pinned, s.archived, "
            "       (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS mc "
            "FROM sessions s WHERE s.id = ?", (sid,)
        ).fetchone()
    if not row:
        return None
    return Session(
        id=row["id"], title=row["title"], created_at=row["created_at"], updated_at=row["updated_at"],
        provider=row["provider"], model=row["model"], pinned=bool(row["pinned"]),
        archived=bool(row["archived"]), message_count=row["mc"],
    )


def rename_session(sid: str, title: str) -> bool:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title.strip() or "Sem título", time.time(), sid),
        )
        return cur.rowcount > 0


def delete_session(sid: str) -> bool:
    """Hard-delete the session AND all its messages. User must call this explicitly."""
    with db_cursor() as cur:
        cur.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
        cur.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        return cur.rowcount > 0


def set_pinned(sid: str, pinned: bool) -> bool:
    with db_cursor() as cur:
        cur.execute("UPDATE sessions SET pinned = ?, updated_at = ? WHERE id = ?", (1 if pinned else 0, time.time(), sid))
        return cur.rowcount > 0


def set_archived(sid: str, archived: bool) -> bool:
    with db_cursor() as cur:
        cur.execute("UPDATE sessions SET archived = ?, updated_at = ? WHERE id = ?", (1 if archived else 0, time.time(), sid))
        return cur.rowcount > 0


def touch_session(sid: str) -> None:
    with db_cursor() as cur:
        cur.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (time.time(), sid))


def auto_title(session_id: str, user_text: str, max_len: int = 60) -> None:
    s = get_session(session_id)
    if not s or s.title != "Nova conversa":
        return
    text = " ".join(user_text.split())
    title = text if len(text) <= max_len else text[: max_len - 1] + "…"
    rename_session(session_id, title)


def search_sessions(query: str) -> list[Session]:
    """Search sessions by title or message content (LIKE-based)."""
    q = f"%{query.strip()}%"
    with db_cursor() as cur:
        rows = cur.execute(
            """
            SELECT DISTINCT s.id, s.title, s.created_at, s.updated_at, s.provider, s.model,
                   s.pinned, s.archived,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS mc
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            WHERE s.archived = 0
              AND (s.title LIKE ? OR m.content LIKE ?)
            ORDER BY s.updated_at DESC
            LIMIT 100
            """,
            (q, q),
        ).fetchall()
    return [
        Session(
            id=r["id"], title=r["title"], created_at=r["created_at"], updated_at=r["updated_at"],
            provider=r["provider"], model=r["model"], pinned=bool(r["pinned"]),
            archived=bool(r["archived"]), message_count=r["mc"],
        )
        for r in rows
    ]


# ====================== Messages ======================

def append_message(
    session_id: str,
    role: str,
    content: str = "",
    type_: str = "text",
    tool_calls: list[dict] | None = None,
    tool_call_id: str | None = None,
    metadata: dict | None = None,
) -> DBMessage:
    now = time.time()
    tool_calls_json = json.dumps(tool_calls or [], ensure_ascii=False)
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO messages(session_id, role, type, content, tool_calls_json, "
            "tool_call_id, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, role, type_, content, tool_calls_json, tool_call_id, metadata_json, now),
        )
        mid = cur.lastrowid
        cur.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
    return DBMessage(
        id=mid, session_id=session_id, role=role, type=type_,
        content=content, tool_calls=tool_calls or [],
        tool_call_id=tool_call_id, metadata=metadata or {}, created_at=now,
    )


def get_messages(session_id: str) -> list[DBMessage]:
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT id, session_id, role, type, content, tool_calls_json, "
            "tool_call_id, metadata_json, created_at "
            "FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    out: list[DBMessage] = []
    for r in rows:
        d = dict(r)
        out.append(DBMessage(
            id=d["id"], session_id=d["session_id"], role=d["role"],
            type=d["type"], content=d["content"],
            tool_calls=json.loads(d["tool_calls_json"]),
            tool_call_id=d["tool_call_id"],
            metadata=json.loads(d["metadata_json"]),
            created_at=d["created_at"],
        ))
    return out


def get_messages_after(session_id: str, after_id: int) -> list[DBMessage]:
    """Return messages with id > after_id, used for regenerate/branching."""
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT id, session_id, role, type, content, tool_calls_json, "
            "tool_call_id, metadata_json, created_at "
            "FROM messages WHERE session_id = ? AND id > ? ORDER BY id ASC",
            (session_id, after_id),
        ).fetchall()
    out: list[DBMessage] = []
    for r in rows:
        d = dict(r)
        out.append(DBMessage(
            id=d["id"], session_id=d["session_id"], role=d["role"],
            type=d["type"], content=d["content"],
            tool_calls=json.loads(d["tool_calls_json"]),
            tool_call_id=d["tool_call_id"],
            metadata=json.loads(d["metadata_json"]),
            created_at=d["created_at"],
        ))
    return out


def clear_messages(session_id: str) -> None:
    with db_cursor() as cur:
        cur.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        cur.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (time.time(), session_id))


def delete_messages_from(session_id: str, from_id: int) -> None:
    """Delete a message and everything after it. Used for regenerate."""
    with db_cursor() as cur:
        cur.execute("DELETE FROM messages WHERE session_id = ? AND id >= ?", (session_id, from_id))
        cur.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (time.time(), session_id))


def get_last_user_message(session_id: str) -> DBMessage | None:
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT id, session_id, role, type, content, tool_calls_json, "
            "tool_call_id, metadata_json, created_at "
            "FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    return DBMessage(
        id=d["id"], session_id=d["session_id"], role=d["role"],
        type=d["type"], content=d["content"],
        tool_calls=json.loads(d["tool_calls_json"]),
        tool_call_id=d["tool_call_id"],
        metadata=json.loads(d["metadata_json"]),
        created_at=d["created_at"],
    )
