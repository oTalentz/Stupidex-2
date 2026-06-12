"""Centralized configuration for Stupidex.

Sources, in priority order:
  1. Environment variables (STUPIDEX_*)
  2. Local config file (~/.stupidex/config.json)
  3. Project-level .env (./.env, loaded via python-dotenv)
  4. Hard-coded defaults

Deployment-level API keys may come from this config or the environment.
Per-user API keys are encrypted by the database layer. The web API exposes
only has_api_key, never the secret value.
"""
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --- Defaults baked into the binary so the app is usable out of the box. ----
# Override any of these by editing ~/.stupidex/config.json, .env, or env vars.

DEFAULT_PROVIDER = "deepseek-v4-flash"
DEFAULT_MODEL = "deepseek-v4-flash"
# API key is intentionally EMPTY in the binary. Users must set it via env
# (DEEPSEEK_API_KEY / STUPIDEX_API_KEY) or the .env / config.json. This
# prevents the key from being shipped in compiled binaries and shared with
# every user. The server will refuse to start a chat if no key is found.
DEFAULT_API_KEY = ""
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"

CONFIG_DIR = Path.home() / ".stupidex"
CONFIG_FILE = CONFIG_DIR / "config.json"
DATA_DIR = CONFIG_DIR  # SQLite DB and history go here too
DB_FILE = DATA_DIR / "stupidex.db"

# On cloud hosts (Render, Fly.io, Railway, Square Cloud) the data dir may
# be wiped between deploys. Prefer a persistent volume if mounted.
def _resolve_data_dir() -> Path:
    for candidate in (
        os.environ.get("STUPIDEX_DATA_DIR"),
        os.environ.get("DATA_DIR"),
        "/data" if Path("/data").is_dir() else None,
        "/var/data" if Path("/var/data").is_dir() else None,
    ):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return CONFIG_DIR


def _resolve_workspaces_dir() -> Path:
    base = _resolve_data_dir()
    if os.environ.get("STUPIDEX_WORKSPACES_DIR"):
        return Path(os.environ["STUPIDEX_WORKSPACES_DIR"])
    return base / "workspaces"


DATA_DIR = _resolve_data_dir()
CONFIG_DIR = DATA_DIR
CONFIG_FILE = DATA_DIR / "config.json"
DB_FILE = DATA_DIR / "stupidex.db"


@dataclass
class AppConfig:
    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    api_key: str = DEFAULT_API_KEY
    base_url: str = DEFAULT_BASE_URL
    custom_model: str = ""  # user override, otherwise empty
    extra: dict = field(default_factory=dict)


def _load_env_file() -> None:
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        try:
            from dotenv import dotenv_values
            for k, v in dotenv_values(env_path).items():
                if v and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass


def _load_file() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def load_config() -> AppConfig:
    _load_env_file()
    raw = _load_file()

    env_key = os.environ.get("STUPIDEX_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")

    return AppConfig(
        provider=raw.get("provider", os.environ.get("STUPIDEX_PROVIDER", DEFAULT_PROVIDER)),
        model=raw.get("model", os.environ.get("STUPIDEX_MODEL", DEFAULT_MODEL)),
        api_key=raw.get("api_key") or env_key or DEFAULT_API_KEY,
        base_url=raw.get("base_url", os.environ.get("STUPIDEX_BASE_URL", DEFAULT_BASE_URL)),
        custom_model=raw.get("custom_model", os.environ.get("STUPIDEX_CUSTOM_MODEL", "")),
        extra=raw.get("extra", {}),
    )


def has_api_key() -> bool:
    """True if a usable LLM API key is configured anywhere."""
    return bool(load_config().api_key)


def save_config(cfg: AppConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = asdict(cfg)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(CONFIG_FILE)


def update_config(**kwargs) -> AppConfig:
    cfg = load_config()
    for k, v in kwargs.items():
        if v is None:
            continue
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    save_config(cfg)
    return cfg
