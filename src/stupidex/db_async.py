"""Database abstraction layer — PostgreSQL via SQLAlchemy, SQLite fallback.

Environment:
  DATABASE_URL=postgresql://user:pass@host:5432/stupidex  → production
  DATABASE_URL unset                                         → SQLite (local dev)

SQLAlchemy is used with the existing SQLite schema (db.py) via reflection
when in SQLite mode, and native DDL for PostgreSQL.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

_engine = None
_SessionLocal = None

SQLITE_PATH = os.environ.get("STUPIDEX_DATA_DIR", str(Path.home() / ".stupidex")) / Path("stupidex.db")


def get_database_url() -> str:
    """Return the database URL, defaulting to SQLite."""
    url = os.environ.get("DATABASE_URL", "")
    if url:
        return url
    db_path = Path(str(SQLITE_PATH))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


def create_engine():
    """Create SQLAlchemy engine based on DATABASE_URL."""
    global _engine
    if _engine is not None:
        return _engine

    url = get_database_url()
    is_sqlite = url.startswith("sqlite")

    try:
        from sqlalchemy import create_engine as sa_create
        kwargs = {"pool_pre_ping": True, "echo": False}
        if is_sqlite:
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_size"] = int(os.environ.get("DB_POOL_SIZE", "10"))
            kwargs["max_overflow"] = int(os.environ.get("DB_POOL_OVERFLOW", "20"))
        _engine = sa_create(url, **kwargs)
        logger.info("db_async: engine created (%s)", "SQLite" if is_sqlite else "PostgreSQL")
    except ImportError:
        logger.warning("db_async: SQLAlchemy not installed, falling back to SQLite")
        _engine = None

    return _engine


def get_session():
    """Get a SQLAlchemy session."""
    global _SessionLocal
    if _SessionLocal is not None:
        return _SessionLocal()

    engine = create_engine()
    if engine is None:
        return None

    try:
        from sqlalchemy.orm import sessionmaker
        _SessionLocal = sessionmaker(bind=engine)
        return _SessionLocal()
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Migration helper
# ---------------------------------------------------------------------------

MIGRATIONS_SQL = {
    "001_users": """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            email TEXT,
            password_hash TEXT,
            avatar_url TEXT,
            oauth_provider TEXT,
            created_at REAL NOT NULL,
            last_login REAL NOT NULL,
            github_access_token TEXT DEFAULT '',
            github_login TEXT DEFAULT '',
            github_avatar_url TEXT DEFAULT '',
            github_connected_at REAL DEFAULT 0
        );
    """,
    "002_sessions": """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            title TEXT DEFAULT '',
            provider TEXT DEFAULT '',
            model TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            trashed INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
    """,
    "003_messages": """
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            role TEXT NOT NULL,
            content TEXT DEFAULT '',
            tool_calls_json TEXT DEFAULT '',
            tool_call_id TEXT DEFAULT '',
            name TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
    """,
    "004_auth_tokens": """
        CREATE TABLE IF NOT EXISTS auth_tokens (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id);
    """,
    "005_workspace_meta": """
        CREATE TABLE IF NOT EXISTS workspace_meta (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            source TEXT DEFAULT '',
            git_url TEXT DEFAULT '',
            git_branch TEXT DEFAULT '',
            size_bytes INTEGER DEFAULT 0,
            file_count INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            last_activity REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_workspace_user ON workspace_meta(user_id);
    """,
    "006_shell_executions": """
        CREATE TABLE IF NOT EXISTS shell_executions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            workspace_id TEXT,
            executable TEXT NOT NULL,
            args TEXT DEFAULT '',
            cwd TEXT DEFAULT '',
            timeout INTEGER DEFAULT 30,
            duration REAL DEFAULT 0,
            exit_code INTEGER DEFAULT 0,
            output_size INTEGER DEFAULT 0,
            stdout TEXT DEFAULT '',
            stderr TEXT DEFAULT '',
            approved INTEGER DEFAULT 0,
            timed_out INTEGER DEFAULT 0,
            cancelled INTEGER DEFAULT 0,
            blocked INTEGER DEFAULT 0,
            block_reason TEXT DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_shell_user ON shell_executions(user_id);
    """,
    "007_audit_log": """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id TEXT PRIMARY KEY,
            user_id TEXT REFERENCES users(id),
            action TEXT NOT NULL,
            resource_type TEXT DEFAULT '',
            resource_id TEXT DEFAULT '',
            details TEXT DEFAULT '',
            ip_address TEXT DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id);
        CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
    """,
    "008_usage_records": """
        CREATE TABLE IF NOT EXISTS usage_records (
            id TEXT PRIMARY KEY,
            user_id TEXT REFERENCES users(id),
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            cost REAL DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_records(user_id);
    """,
    "009_agent_runs": """
        CREATE TABLE IF NOT EXISTS agent_runs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            session_id TEXT REFERENCES sessions(id),
            mode TEXT DEFAULT 'agent',
            status TEXT DEFAULT 'queued',
            goal TEXT DEFAULT '',
            plan TEXT DEFAULT '',
            current_step INTEGER DEFAULT 0,
            total_steps INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            completed_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_runs_user ON agent_runs(user_id);
    """,
    "010_approvals": """
        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            run_id TEXT REFERENCES agent_runs(id),
            tool TEXT NOT NULL,
            arguments TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            risk TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at REAL NOT NULL,
            resolved_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_approvals_user ON approvals(user_id);
    """,
}


def run_migrations(engine=None) -> None:
    """Run all pending migrations."""
    eng = engine or create_engine()
    if eng is None:
        logger.warning("db_async: no engine, skipping migrations")
        return

    try:
        from sqlalchemy import inspect, text as sa_text
        inspector = inspect(eng)
        existing = set(inspector.get_table_names())

        for name, sql in MIGRATIONS_SQL.items():
            table = name.split("_", 1)[1]
            if table in existing:
                continue
            logger.info("db_async: running migration %s", name)
            with eng.begin() as conn:
                conn.execute(sa_text(sql))
        logger.info("db_async: migrations complete (%d tables)", len(MIGRATIONS_SQL))
    except Exception as e:
        logger.warning("db_async: migration error (non-fatal): %s", e)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def record_usage(
    user_id: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cost: float,
    duration_ms: int,
) -> None:
    """Record token usage to the database."""
    import time
    import uuid
    session = get_session()
    if session is None:
        return
    try:
        from sqlalchemy import text as sa_text
        session.execute(
            sa_text("""
                INSERT INTO usage_records
                (id, user_id, provider, model, prompt_tokens, completion_tokens,
                 total_tokens, cost, duration_ms, created_at)
                VALUES (:id, :uid, :prov, :model, :pt, :ct, :tt, :cost, :dur, :now)
            """),
            {
                "id": uuid.uuid4().hex[:16],
                "uid": user_id,
                "prov": provider,
                "model": model,
                "pt": prompt_tokens,
                "ct": completion_tokens,
                "tt": total_tokens,
                "cost": cost,
                "dur": duration_ms,
                "now": time.time(),
            },
        )
        session.commit()
    except Exception as e:
        logger.warning("db_async: failed to record usage: %s", e)
    finally:
        session.close()


def record_audit(
    user_id: str,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    details: str = "",
    ip_address: str = "",
) -> None:
    """Record an audit event."""
    import time
    import uuid
    session = get_session()
    if session is None:
        return
    try:
        from sqlalchemy import text as sa_text
        session.execute(
            sa_text("""
                INSERT INTO audit_logs
                (id, user_id, action, resource_type, resource_id, details, ip_address, created_at)
                VALUES (:id, :uid, :act, :rtype, :rid, :det, :ip, :now)
            """),
            {
                "id": uuid.uuid4().hex[:16],
                "uid": user_id,
                "act": action,
                "rtype": resource_type,
                "rid": resource_id,
                "det": details[:500],
                "ip": ip_address,
                "now": time.time(),
            },
        )
        session.commit()
    except Exception as e:
        logger.warning("db_async: failed to record audit: %s", e)
    finally:
        session.close()
