"""SQLite-backed user, session and message storage.

Multi-tenant: every session belongs to a user. Users authenticate via
username+password (hashed with werkzeug). Auth tokens are opaque UUIDs.

Migrations:
  v1 – sessions + messages
  v2 – pinned / archived flags
  v3 – users + auth_tokens
  v4 – sessions.user_id FK
  v5 – Google OAuth (email, avatar_url, oauth_provider)
"""
import json
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from werkzeug.security import check_password_hash, generate_password_hash

from .config import DATA_DIR

DB_FILE = DATA_DIR / "stupidex.db"

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_INITIALIZED = False
_TOKEN_BYTES = 32


@dataclass
class User:
    id: str
    username: str
    created_at: float
    last_login: float
    api_key: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    oauth_provider: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "avatar_url": self.avatar_url,
            "oauth_provider": self.oauth_provider,
            "created_at": self.created_at,
            "last_login": self.last_login,
            "has_api_key": bool(self.api_key),
        }


@dataclass
class Session:
    id: str
    user_id: str
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
            "user_id": self.user_id,
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
            "id": self.id, "role": self.role, "type": self.type,
            "content": self.content, "tool_calls": self.tool_calls,
            "tool_call_id": self.tool_call_id, "metadata": self.metadata,
        }


# ============================================================
# Connection & migrations
# ============================================================

def _connect() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


_MIGRATIONS: list[str] = [
    # v1
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
    # v2
    """
    ALTER TABLE sessions ADD COLUMN pinned   INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE sessions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
    CREATE INDEX IF NOT EXISTS idx_sessions_pinned   ON sessions(pinned   DESC, updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_sessions_archived ON sessions(archived,        updated_at DESC);
    """,
    # v3 — users + auth_tokens
    """
    CREATE TABLE IF NOT EXISTS users (
        id            TEXT PRIMARY KEY,
        username      TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        api_key       TEXT,
        created_at    REAL NOT NULL,
        last_login    REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS auth_tokens (
        token      TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id);
    """,
    # v4 — sessions.user_id FK
    """
    ALTER TABLE sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT '';
    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, updated_at DESC);
    """,
    # v5 — Google OAuth support
    """
    ALTER TABLE users ADD COLUMN email         TEXT DEFAULT '';
    ALTER TABLE users ADD COLUMN avatar_url    TEXT DEFAULT '';
    ALTER TABLE users ADD COLUMN oauth_provider TEXT DEFAULT '';
    """,
    # v6 — composite unique on (email, oauth_provider)
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_provider ON users(email, oauth_provider) WHERE email != '';
    """,
]


def _ensure_schema(conn: sqlite3.Connection) -> None:
    global _SCHEMA_INITIALIZED
    with _SCHEMA_LOCK:
        if _SCHEMA_INITIALIZED:
            return
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
        )
        cur.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
        current = cur.fetchone()[0] or 0
        for i, sql in enumerate(_MIGRATIONS):
            v = i + 1
            if v <= current:
                continue
            # executescript() can invalidate the cursor — use execute() for
            # single statements, or split multi-statement scripts manually.
            for stmt in _split_statements(sql):
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_version(version, applied_at) VALUES (?, ?)", (v, time.time()))
        conn.commit()
        _SCHEMA_INITIALIZED = True


def _split_statements(sql: str) -> list[str]:
    """Split a SQL script into individual statements, skipping blanks and comments."""
    stmts = []
    for raw in sql.split(";"):
        stmt = raw.strip()
        if stmt and not stmt.startswith("--"):
            stmts.append(stmt)
    return stmts


@contextmanager
def db_cursor() -> Iterator[sqlite3.Cursor]:
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
    with db_cursor() as cur:
        cur.execute("SELECT 1")


# ============================================================
# Users & Auth
# ============================================================

def create_user(username: str, password: str) -> tuple[User, str]:
    username = username.strip().lower()
    if not username or not password:
        raise ValueError("username and password required")
    if len(password) < 4:
        raise ValueError("password must be at least 4 characters")

    now = time.time()
    uid = uuid.uuid4().hex[:12]
    pwhash = generate_password_hash(password)

    with db_cursor() as cur:
        existing = cur.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            raise ValueError("username already taken")
        cur.execute(
            "INSERT INTO users(id, username, password_hash, created_at, last_login) "
            "VALUES (?, ?, ?, ?, ?)",
            (uid, username, pwhash, now, now),
        )

    token = _create_token(uid)
    return User(id=uid, username=username, created_at=now, last_login=now), token


def find_or_create_oauth_user(email: str, name: str, avatar_url: str, provider: str) -> tuple[User, str]:
    """Find existing OAuth user by email, or create a new one. Returns (User, token)."""
    email = email.strip().lower()
    name = name.strip() or email.split("@")[0]

    with db_cursor() as cur:
        row = cur.execute(
            "SELECT id, username, password_hash, api_key, email, avatar_url, oauth_provider, created_at, last_login "
            "FROM users WHERE email = ? AND oauth_provider = ?",
            (email, provider),
        ).fetchone()

        now = time.time()
        if row:
            uid = row["id"]
            cur.execute(
                "UPDATE users SET avatar_url = ?, last_login = ? WHERE id = ?",
                (avatar_url or row["avatar_url"], now, uid),
            )
        else:
            uid = uuid.uuid4().hex[:12]
            base = name.strip().lower().replace(" ", "")[:20] or "user"
            username = base
            if cur.execute("SELECT 1 FROM users WHERE username = ?", (base,)).fetchone():
                suffix = 2
                while True:
                    username = f"{base}{suffix}"
                    if not cur.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                        break
                    suffix += 1
            cur.execute(
                "INSERT INTO users(id, username, password_hash, email, avatar_url, oauth_provider, created_at, last_login) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, username, "", email, avatar_url, provider, now, now),
            )

    token = _create_token(uid)
    user = User(id=uid, username=name, created_at=now, last_login=now,
                email=email, avatar_url=avatar_url, oauth_provider=provider)
    return user, token





def authenticate_user(username: str, password: str) -> tuple[User, str]:
    username = username.strip().lower()
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT id, username, password_hash, api_key, created_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if not row or not check_password_hash(row["password_hash"], password):
        raise ValueError("invalid username or password")

    token = _create_token(row["id"])
    with db_cursor() as cur:
        cur.execute("UPDATE users SET last_login = ? WHERE id = ?", (time.time(), row["id"]))
    user = User(id=row["id"], username=row["username"], created_at=row["created_at"],
                last_login=time.time(), api_key=row["api_key"])
    return user, token


def _create_token(user_id: str) -> str:
    token = secrets.token_hex(_TOKEN_BYTES)
    now = time.time()
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO auth_tokens(token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now, now + 86400 * 90),  # 90-day tokens
        )
    return token


def validate_token(token: str) -> User | None:
    token = (token or "").strip()
    if not token:
        return None
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT u.id, u.username, u.api_key, u.email, u.avatar_url, u.oauth_provider, u.created_at, u.last_login "
            "FROM auth_tokens t JOIN users u ON u.id = t.user_id "
            "WHERE t.token = ? AND t.expires_at > ?",
            (token, time.time()),
        ).fetchone()
    if not row:
        return None
    return User(id=row["id"], username=row["username"], created_at=row["created_at"],
                last_login=row["last_login"], api_key=row["api_key"],
                email=row["email"], avatar_url=row["avatar_url"], oauth_provider=row["oauth_provider"])


def logout_token(token: str) -> None:
    with db_cursor() as cur:
        cur.execute("DELETE FROM auth_tokens WHERE token = ?", (token,))


def update_user_api_key(user_id: str, api_key: str | None) -> None:
    with db_cursor() as cur:
        cur.execute("UPDATE users SET api_key = ? WHERE id = ?", (api_key or "", user_id))


# ============================================================
# Sessions
# ============================================================

def create_session(user_id: str, provider: str, model: str, title: str = "Nova conversa") -> Session:
    sid = uuid.uuid4().hex[:12]
    now = time.time()
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO sessions(id, user_id, title, created_at, updated_at, provider, model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, user_id, title, now, now, provider, model),
        )
    return Session(sid, user_id, title, now, now, provider, model, False, False, 0)


def list_sessions(user_id: str, include_archived: bool = False) -> list[Session]:
    with db_cursor() as cur:
        if include_archived:
            rows = cur.execute(
                "SELECT s.id, s.user_id, s.title, s.created_at, s.updated_at, s.provider, s.model, "
                "s.pinned, s.archived, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS mc "
                "FROM sessions s WHERE s.user_id = ? ORDER BY s.pinned DESC, s.updated_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = cur.execute(
                "SELECT s.id, s.user_id, s.title, s.created_at, s.updated_at, s.provider, s.model, "
                "s.pinned, s.archived, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS mc "
                "FROM sessions s WHERE s.user_id = ? AND s.archived = 0 ORDER BY s.pinned DESC, s.updated_at DESC",
                (user_id,),
            ).fetchall()
    return [_row_to_session(r) for r in rows]


def get_session(sid: str) -> Session | None:
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT s.id, s.user_id, s.title, s.created_at, s.updated_at, s.provider, s.model, "
            "s.pinned, s.archived, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS mc "
            "FROM sessions s WHERE s.id = ?", (sid,)
        ).fetchone()
    return _row_to_session(row) if row else None


def get_session_for_user(sid: str, user_id: str) -> Session | None:
    s = get_session(sid)
    return s if s and s.user_id == user_id else None


def rename_session(sid: str, title: str) -> bool:
    with db_cursor() as cur:
        cur.execute("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                    (title.strip() or "Sem título", time.time(), sid))
        return cur.rowcount > 0


def delete_session(sid: str) -> bool:
    with db_cursor() as cur:
        cur.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
        cur.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        return cur.rowcount > 0


def set_pinned(sid: str, pinned: bool) -> bool:
    with db_cursor() as cur:
        cur.execute("UPDATE sessions SET pinned = ?, updated_at = ? WHERE id = ?",
                    (1 if pinned else 0, time.time(), sid))
        return cur.rowcount > 0


def set_archived(sid: str, archived: bool) -> bool:
    with db_cursor() as cur:
        cur.execute("UPDATE sessions SET archived = ?, updated_at = ? WHERE id = ?",
                    (1 if archived else 0, time.time(), sid))
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


def search_sessions(user_id: str, query: str) -> list[Session]:
    q = f"%{query.strip()}%"
    with db_cursor() as cur:
        rows = cur.execute(
            """
            SELECT DISTINCT s.id, s.user_id, s.title, s.created_at, s.updated_at, s.provider, s.model,
                   s.pinned, s.archived,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS mc
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            WHERE s.user_id = ? AND s.archived = 0
              AND (s.title LIKE ? OR m.content LIKE ?)
            ORDER BY s.updated_at DESC
            LIMIT 100
            """,
            (user_id, q, q),
        ).fetchall()
    return [_row_to_session(r) for r in rows]


def _row_to_session(r) -> Session:
    d = dict(r)
    return Session(d["id"], d.get("user_id", ""), d["title"], d["created_at"], d["updated_at"],
                   d["provider"], d["model"], bool(d.get("pinned", 0)), bool(d.get("archived", 0)), d.get("mc", 0))


# ============================================================
# Messages
# ============================================================

def append_message(session_id: str, role: str, content: str = "", type_: str = "text",
                   tool_calls: list[dict] | None = None, tool_call_id: str | None = None,
                   metadata: dict | None = None) -> DBMessage:
    now = time.time()
    tc_json = json.dumps(tool_calls or [], ensure_ascii=False)
    md_json = json.dumps(metadata or {}, ensure_ascii=False)
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO messages(session_id, role, type, content, tool_calls_json, "
            "tool_call_id, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, role, type_, content, tc_json, tool_call_id, md_json, now),
        )
        mid = cur.lastrowid
        cur.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
    return DBMessage(mid, session_id, role, type_, content, tool_calls or [],
                     tool_call_id, metadata or {}, now)


def get_messages(session_id: str) -> list[DBMessage]:
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT id, session_id, role, type, content, tool_calls_json, "
            "tool_call_id, metadata_json, created_at "
            "FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,),
        ).fetchall()
    return [_row_to_msg(r) for r in rows]


def get_last_user_message(session_id: str) -> DBMessage | None:
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT id, session_id, role, type, content, tool_calls_json, "
            "tool_call_id, metadata_json, created_at "
            "FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    return _row_to_msg(row) if row else None


def clear_messages(session_id: str) -> None:
    with db_cursor() as cur:
        cur.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        cur.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (time.time(), session_id))


def delete_messages_from(session_id: str, from_id: int) -> None:
    with db_cursor() as cur:
        cur.execute("DELETE FROM messages WHERE session_id = ? AND id >= ?", (session_id, from_id))
        cur.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (time.time(), session_id))


def _row_to_msg(r) -> DBMessage:
    d = dict(r)
    return DBMessage(d["id"], d["session_id"], d["role"], d["type"], d["content"],
                     json.loads(d["tool_calls_json"]), d["tool_call_id"],
                     json.loads(d["metadata_json"]), d["created_at"])
