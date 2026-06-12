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

import hashlib
import json
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from cryptography.fernet import Fernet, InvalidToken

try:
    from werkzeug.security import check_password_hash, generate_password_hash
except ImportError:
    # Fallback: PBKDF2 (NIST-recommended) — better than raw sha256, no extra deps.
    import hashlib
    import hmac
    import os as _os

    _PBKDF2_ITERS = 200_000
    _PBKDF2_ALGO = "sha256"

    def generate_password_hash(pw: str) -> str:
        salt = _os.urandom(16)
        dk = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, pw.encode("utf-8"), salt, _PBKDF2_ITERS)
        return f"pbkdf2_{_PBKDF2_ALGO}${_PBKDF2_ITERS}${salt.hex()}${dk.hex()}"

    def check_password_hash(hash_val: str, pw: str) -> bool:
        try:
            scheme, iters_s, salt_hex, dk_hex = hash_val.split("$", 3)
            if not scheme.startswith("pbkdf2_"):
                return False
            algo = scheme.split("_", 1)[1]
            iters = int(iters_s)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(dk_hex)
            candidate = hashlib.pbkdf2_hmac(algo, pw.encode("utf-8"), salt, iters)
            return hmac.compare_digest(expected, candidate)
        except Exception:
            return False


from .config import DATA_DIR

DB_FILE = DATA_DIR / "stupidex.db"

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_INITIALIZED = False
_TOKEN_BYTES = 32
_MIN_PASSWORD_LEN = 8
_MAX_PASSWORD_LEN = 200
_MAX_USERNAME_LEN = 50
_MAX_MODEL_LEN = 200
_DUMMY_PASSWORD_HASH = generate_password_hash("stupidex-invalid-password")
_KEY_LOCK = threading.Lock()
_KEY_FILE = DATA_DIR / ".keyvault"
_FERNET: Fernet | None = None


def _fernet() -> Fernet:
    global _FERNET
    with _KEY_LOCK:
        if _FERNET is not None:
            return _FERNET
        if _KEY_FILE.exists():
            key = _KEY_FILE.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _KEY_FILE.with_suffix(".tmp")
            tmp.write_bytes(key)
            try:
                tmp.chmod(0o600)
            except OSError:
                pass
            tmp.replace(_KEY_FILE)
        _FERNET = Fernet(key)
        return _FERNET


def _encrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    return "enc:v1:" + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    if not value.startswith("enc:v1:"):
        return value
    try:
        return _fernet().decrypt(value[7:].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


# Per-user login throttling: at most N failures in M seconds before a temporary lock.
_LOGIN_FAIL_LIMIT = 8
_LOGIN_FAIL_WINDOW = 600.0  # 10 min
_LOGIN_FAIL_LOCKOUT = 30.0  # sec blocked after the limit is hit
_LOGIN_FAIL: dict[str, list[float]] = {}
_LOGIN_FAIL_LOCK = threading.Lock()
_LOGIN_BLOCKED: dict[str, float] = {}
_LOGIN_BLOCKED_LOCK = threading.Lock()


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
    provider: str = ""
    model: str = ""
    custom_model: str = ""

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
    trashed: bool = False
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
            "trashed": self.trashed,
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
    # v7 — per-user LLM preferences
    """
    ALTER TABLE users ADD COLUMN provider TEXT NOT NULL DEFAULT '';
    ALTER TABLE users ADD COLUMN model TEXT NOT NULL DEFAULT '';
    ALTER TABLE users ADD COLUMN custom_model TEXT NOT NULL DEFAULT '';
    """,
    # v8 — trash support for sessions
    """
    ALTER TABLE sessions ADD COLUMN trashed INTEGER NOT NULL DEFAULT 0;
    CREATE INDEX IF NOT EXISTS idx_sessions_trashed ON sessions(trashed, updated_at DESC);
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
            conn.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                (v, time.time()),
            )
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


def _check_login_lockout(key: str) -> None:
    """Raise ValueError if this key is currently locked out."""
    with _LOGIN_BLOCKED_LOCK:
        until = _LOGIN_BLOCKED.get(key, 0.0)
    if until > time.time():
        raise ValueError("too many failed attempts — try again later")


def _record_login_failure(key: str) -> None:
    # NOTE: must be careful with lock order. We hold _LOGIN_FAIL_LOCK and
    # then acquire _LOGIN_BLOCKED_LOCK — never the reverse, to avoid deadlock.
    should_block_until: float | None = None
    now = time.time()
    with _LOGIN_FAIL_LOCK:
        if len(_LOGIN_FAIL) > 10_000:
            cutoff = now - _LOGIN_FAIL_WINDOW
            for stale_key in [
                k
                for k, values in _LOGIN_FAIL.items()
                if not values or values[-1] < cutoff
            ]:
                _LOGIN_FAIL.pop(stale_key, None)
        if key not in _LOGIN_FAIL and len(_LOGIN_FAIL) >= 20_000:
            return
        history = _LOGIN_FAIL.setdefault(key, [])
        history[:] = [t for t in history if t > now - _LOGIN_FAIL_WINDOW]
        history.append(now)
        if len(history) >= _LOGIN_FAIL_LIMIT:
            should_block_until = now + _LOGIN_FAIL_LOCKOUT
            _LOGIN_FAIL.pop(key, None)
    if should_block_until is not None:
        with _LOGIN_BLOCKED_LOCK:
            _LOGIN_BLOCKED[key] = should_block_until


def _record_login_success(key: str) -> None:
    with _LOGIN_FAIL_LOCK:
        _LOGIN_FAIL.pop(key, None)
    with _LOGIN_BLOCKED_LOCK:
        _LOGIN_BLOCKED.pop(key, None)


def _is_valid_username(u: str) -> bool:
    if not u or len(u) > _MAX_USERNAME_LEN:
        return False
    # Allow a-z, 0-9, _, ., -, @
    return all(c.isalnum() or c in "_.@-" for c in u)


def create_user(username: str, password: str) -> tuple[User, str]:
    username = username.strip().lower()
    if not _is_valid_username(username):
        raise ValueError("invalid username (allowed: letters, digits, _ . @ -)")
    if not password or len(password) < _MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {_MIN_PASSWORD_LEN} characters")
    if len(password) > _MAX_PASSWORD_LEN:
        raise ValueError(f"password too long (max {_MAX_PASSWORD_LEN})")

    now = time.time()
    uid = uuid.uuid4().hex[:12]
    pwhash = generate_password_hash(password)

    with db_cursor() as cur:
        existing = cur.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing:
            raise ValueError("username already taken")
        cur.execute(
            "INSERT INTO users(id, username, password_hash, created_at, last_login) "
            "VALUES (?, ?, ?, ?, ?)",
            (uid, username, pwhash, now, now),
        )

    token = _create_token(uid)
    return User(id=uid, username=username, created_at=now, last_login=now), token


def find_or_create_oauth_user(
    email: str, name: str, avatar_url: str, provider: str
) -> tuple[User, str]:
    """Find existing OAuth user by email, or create a new one. Returns (User, token)."""
    email = email.strip().lower()
    name = name.strip() or email.split("@")[0]

    with db_cursor() as cur:
        row = cur.execute(
            "SELECT id, username, password_hash, api_key, email, avatar_url, oauth_provider, "
            "provider, model, custom_model, created_at, last_login "
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
            if cur.execute(
                "SELECT 1 FROM users WHERE username = ?", (base,)
            ).fetchone():
                suffix = 2
                while True:
                    username = f"{base}{suffix}"
                    if not cur.execute(
                        "SELECT 1 FROM users WHERE username = ?", (username,)
                    ).fetchone():
                        break
                    suffix += 1
            cur.execute(
                "INSERT INTO users(id, username, password_hash, email, avatar_url, oauth_provider, created_at, last_login) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, username, "", email, avatar_url, provider, now, now),
            )

    token = _create_token(uid)
    user = User(
        id=uid,
        username=(row["username"] if row else username),
        created_at=(row["created_at"] if row else now),
        last_login=now,
        api_key=(_decrypt_secret(row["api_key"]) if row else None),
        email=email,
        avatar_url=avatar_url,
        oauth_provider=provider,
        provider=(row["provider"] if row else ""),
        model=(row["model"] if row else ""),
        custom_model=(row["custom_model"] if row else ""),
    )
    return user, token


def authenticate_user(username: str, password: str) -> tuple[User, str]:
    username = username.strip().lower()
    key = f"login:{username}"
    _check_login_lockout(key)
    # Always run the hash check (constant-time-ish) even on missing user
    # to avoid user-enumeration timing attacks.
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT id, username, password_hash, api_key, provider, model, custom_model, created_at "
            "FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    valid = check_password_hash(
        row["password_hash"] if row else _DUMMY_PASSWORD_HASH, password
    )
    if not row or not valid:
        _record_login_failure(key)
        raise ValueError("invalid username or password")

    _record_login_success(key)
    token = _create_token(row["id"])
    with db_cursor() as cur:
        cur.execute(
            "UPDATE users SET last_login = ? WHERE id = ?", (time.time(), row["id"])
        )
    user = User(
        id=row["id"],
        username=row["username"],
        created_at=row["created_at"],
        last_login=time.time(),
        api_key=_decrypt_secret(row["api_key"]),
        provider=row["provider"],
        model=row["model"],
        custom_model=row["custom_model"],
    )
    return user, token


def _create_token(user_id: str) -> str:
    token = secrets.token_hex(_TOKEN_BYTES)
    stored_token = _token_digest(token)
    now = time.time()
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO auth_tokens(token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (stored_token, user_id, now, now + 86400 * 30),
        )
    return token


def validate_token(token: str) -> User | None:
    token = (token or "").strip()
    if not token:
        return None
    with db_cursor() as cur:
        stored_token = _token_digest(token)
        row = cur.execute(
            "SELECT u.id, u.username, u.api_key, u.email, u.avatar_url, u.oauth_provider, "
            "u.provider, u.model, u.custom_model, u.created_at, u.last_login, t.token AS stored_token "
            "FROM auth_tokens t JOIN users u ON u.id = t.user_id "
            "WHERE t.token IN (?, ?) AND t.expires_at > ?",
            (stored_token, token, time.time()),
        ).fetchone()
        if row and row["stored_token"] == token:
            cur.execute(
                "UPDATE auth_tokens SET token = ? WHERE token = ?",
                (stored_token, token),
            )
    if not row:
        return None
    return User(
        id=row["id"],
        username=row["username"],
        created_at=row["created_at"],
        last_login=row["last_login"],
        api_key=_decrypt_secret(row["api_key"]),
        email=row["email"],
        avatar_url=row["avatar_url"],
        oauth_provider=row["oauth_provider"],
        provider=row["provider"],
        model=row["model"],
        custom_model=row["custom_model"],
    )


def logout_token(token: str) -> None:
    with db_cursor() as cur:
        cur.execute(
            "DELETE FROM auth_tokens WHERE token IN (?, ?)",
            (_token_digest(token), token),
        )


def update_user_api_key(user_id: str, api_key: str | None) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE users SET api_key = ? WHERE id = ?",
            (_encrypt_secret(api_key), user_id),
        )


def update_user_config(
    user_id: str,
    *,
    provider: str,
    model: str,
    custom_model: str,
    api_key: str | None = None,
    clear_api_key: bool = False,
) -> None:
    provider = provider.strip()[:100]
    model = model.strip()[:_MAX_MODEL_LEN]
    custom_model = custom_model.strip()[:_MAX_MODEL_LEN]
    with db_cursor() as cur:
        cur.execute(
            "UPDATE users SET provider = ?, model = ?, custom_model = ? WHERE id = ?",
            (provider, model, custom_model, user_id),
        )
        if api_key is not None:
            cur.execute(
                "UPDATE users SET api_key = ? WHERE id = ?",
                (_encrypt_secret(api_key.strip()), user_id),
            )
        elif clear_api_key:
            cur.execute("UPDATE users SET api_key = '' WHERE id = ?", (user_id,))


def _token_digest(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


# ============================================================
# Sessions
# ============================================================


def create_session(
    user_id: str, provider: str, model: str, title: str = "Nova conversa"
) -> Session:
    sid = uuid.uuid4().hex[:12]
    now = time.time()
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO sessions(id, user_id, title, created_at, updated_at, provider, model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, user_id, title, now, now, provider, model),
        )
    return Session(
        sid, user_id, title, now, now, provider, model, False, False, False, 0
    )


def list_sessions(
    user_id: str,
    include_archived: bool = False,
    include_trashed: bool = False,
    only_trashed: bool = False,
) -> list[Session]:
    with db_cursor() as cur:
        conditions = ["s.user_id = ?"]
        if not include_archived:
            conditions.append("s.archived = 0")
        if only_trashed:
            conditions.append("s.trashed = 1")
        elif not include_trashed:
            conditions.append("s.trashed = 0")
        rows = cur.execute(
            "SELECT s.id, s.user_id, s.title, s.created_at, s.updated_at, s.provider, s.model, "
            "s.pinned, s.archived, s.trashed, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS mc "
            f"FROM sessions s WHERE {' AND '.join(conditions)} "
            "ORDER BY s.pinned DESC, s.updated_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_session(r) for r in rows]


def get_session(sid: str) -> Session | None:
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT s.id, s.user_id, s.title, s.created_at, s.updated_at, s.provider, s.model, "
            "s.pinned, s.archived, s.trashed, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS mc "
            "FROM sessions s WHERE s.id = ?",
            (sid,),
        ).fetchone()
    return _row_to_session(row) if row else None


def get_session_for_user(sid: str, user_id: str) -> Session | None:
    s = get_session(sid)
    return s if s and s.user_id == user_id else None


def rename_session(sid: str, title: str) -> bool:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title.strip() or "Sem título", time.time(), sid),
        )
        return cur.rowcount > 0


def delete_session(sid: str) -> bool:
    with db_cursor() as cur:
        cur.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
        cur.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        return cur.rowcount > 0


def set_pinned(sid: str, pinned: bool) -> bool:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE sessions SET pinned = ?, updated_at = ? WHERE id = ?",
            (1 if pinned else 0, time.time(), sid),
        )
        return cur.rowcount > 0


def set_archived(sid: str, archived: bool) -> bool:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE sessions SET archived = ?, updated_at = ? WHERE id = ?",
            (1 if archived else 0, time.time(), sid),
        )
        return cur.rowcount > 0


def set_trashed(sid: str, trashed: bool) -> bool:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE sessions SET trashed = ?, pinned = CASE WHEN ? THEN 0 ELSE pinned END, "
            "updated_at = ? WHERE id = ?",
            (1 if trashed else 0, 1 if trashed else 0, time.time(), sid),
        )
        return cur.rowcount > 0


def touch_session(sid: str) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (time.time(), sid)
        )


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
                   s.pinned, s.archived, s.trashed,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS mc
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            WHERE s.user_id = ? AND s.archived = 0 AND s.trashed = 0
              AND (s.title LIKE ? OR m.content LIKE ?)
            ORDER BY s.updated_at DESC
            LIMIT 100
            """,
            (user_id, q, q),
        ).fetchall()
    return [_row_to_session(r) for r in rows]


def _row_to_session(r) -> Session:
    d = dict(r)
    return Session(
        d["id"],
        d.get("user_id", ""),
        d["title"],
        d["created_at"],
        d["updated_at"],
        d["provider"],
        d["model"],
        bool(d.get("pinned", 0)),
        bool(d.get("archived", 0)),
        bool(d.get("trashed", 0)),
        d.get("mc", 0),
    )


# ============================================================
# Messages
# ============================================================


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
        cur.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
        )
    return DBMessage(
        mid,
        session_id,
        role,
        type_,
        content,
        tool_calls or [],
        tool_call_id,
        metadata or {},
        now,
    )


def get_messages(session_id: str) -> list[DBMessage]:
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT id, session_id, role, type, content, tool_calls_json, "
            "tool_call_id, metadata_json, created_at "
            "FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
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
        cur.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (time.time(), session_id)
        )


def delete_messages_from(session_id: str, from_id: int) -> None:
    with db_cursor() as cur:
        cur.execute(
            "DELETE FROM messages WHERE session_id = ? AND id >= ?",
            (session_id, from_id),
        )
        cur.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (time.time(), session_id)
        )


def _row_to_msg(r) -> DBMessage:
    d = dict(r)
    # Defensive: corrupted JSON in DB shouldn't crash the entire message list.
    try:
        tc = json.loads(d["tool_calls_json"])
    except (ValueError, TypeError):
        tc = []
    try:
        md = json.loads(d["metadata_json"])
    except (ValueError, TypeError):
        md = {}
    return DBMessage(
        d["id"],
        d["session_id"],
        d["role"],
        d["type"],
        d["content"],
        tc,
        d["tool_call_id"],
        md,
        d["created_at"],
    )
