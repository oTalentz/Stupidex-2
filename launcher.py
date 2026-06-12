"""Stupidex launcher.

Two modes:
  - Desktop: picks a free port on 127.0.0.1 and opens the browser.
  - Cloud (auto-detect): binds to 0.0.0.0:$PORT (detected when PORT env is set).

WSGI server selection (simplified — no more gunicorn churn):
  - waitress — primary, works on Linux/Windows/macOS, no deps beyond Python.
  - gunicorn — only used if STUPIDEX_WSGI=gunicorn is explicitly set.
    (Square Cloud's gunicorn versions have per-version breaking changes.)
  - Flask dev server — last resort, fine for single-user or testing.

To stop: SIGTERM or Ctrl+C.
"""

import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# ============================================================
# Bootstrap: make `stupidex` package importable.
# ============================================================

_THIS_DIR = Path(__file__).resolve().parent
for _candidate in (_THIS_DIR, _THIS_DIR / "src", _THIS_DIR.parent):
    pkg = _candidate / "stupidex"
    if pkg.is_dir() and (pkg / "__init__.py").is_file():
        sp = str(_candidate)
        if sp not in sys.path:
            sys.path.insert(0, sp)
        break


# ============================================================
# Helpers
# ============================================================


def _find_free_port(preferred: int = 5000) -> int:
    for port in (preferred, 5001, 5002, 5003, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port found")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="[%(asctime)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Also write to stderr so Square Cloud captures it
    for h in logging.getLogger().handlers:
        h.setStream(sys.stderr) if hasattr(h, "setStream") else None


# ============================================================
# WSGI server selection
# ============================================================


def _run_server(app, host: str, port: int) -> None:
    """Start the WSGI server. Tries servers in order, catches everything."""
    _setup_logging()

    servers = []

    explicit = os.environ.get("STUPIDEX_WSGI", "").strip().lower()
    if explicit == "gunicorn":
        servers.append(_try_gunicorn)
    elif explicit == "flask":
        servers.append(_try_flask)
    else:
        # Default: waitress -> gunicorn -> Flask
        servers.extend([_try_waitress, _try_gunicorn, _try_flask])

    for server_fn in servers:
        try:
            server_fn(app, host, port)
            return  # _try_* raising any exception returns control; success runs forever
        except Exception as e:
            logging.warning("%s failed: %s", server_fn.__name__, e)

    raise RuntimeError("no WSGI server could start")


def _try_waitress(app, host: str, port: int) -> None:
    from waitress import serve

    logging.info("Starting waitress on %s:%d", host, port)
    serve(
        app,
        host=host,
        port=port,
        threads=8,
        ident="Stupidex",
        connection_limit=100,
        channel_timeout=300,
    )


def _try_gunicorn(app, host: str, port: int) -> None:
    from gunicorn import config as gconfig
    from gunicorn.app.wsgiapp import WSGIApplication

    cfg = gconfig.Config()
    cfg.set("bind", f"{host}:{port}")
    cfg.set("workers", 1)
    cfg.set("threads", 8)
    cfg.set("timeout", 120)
    cfg.set("wsgi_app", "stupidex.web:app")
    cfg.set("default_proc_name", "stupidex")
    logging.info("Starting gunicorn on %s:%d", host, port)
    WSGIApplication(cfg).run()


def _try_flask(app, host: str, port: int) -> None:
    logging.info("Starting Flask dev server on %s:%d", host, port)
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


# ============================================================
# Entry point
# ============================================================


def main() -> int:
    _setup_logging()

    # Default: enable shell tool unless explicitly disabled
    if os.environ.get("STUPIDEX_ENABLE_SHELL", "").lower() not in ("0", "false", "no"):
        os.environ.setdefault("STUPIDEX_ENABLE_SHELL", "1")

    # GitHub OAuth configuration (optional, for private repository cloning)
    # Set these environment variables to enable GitHub integration:
    #   GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_REDIRECT_URI, FRONTEND_URL
    # See README.md for detailed setup instructions.

    # Auto-detect server mode. Cloud hosts (Square Cloud, Render, Fly, etc.)
    # often don't set PORT — but they run on Linux in a non-TTY container.
    # The desktop .exe is a separate build (PyInstaller) and doesn't use this.
    is_server = (
        os.environ.get("STUPIDEX_SERVER") == "1"
        or os.environ.get("PORT") is not None
        or os.environ.get("STUPIDEX_HOST") == "0.0.0.0"
        or (
            os.name == "posix"
            and not sys.stdout.isatty()
            and os.environ.get("STUPIDEX_DESKTOP") != "1"
        )
    )

    host = os.environ.get("STUPIDEX_HOST", "0.0.0.0" if is_server else "127.0.0.1")
    port = int(
        os.environ.get("PORT")
        or os.environ.get("STUPIDEX_PORT")
        or ("80" if is_server else "5000")
    )

    # On cloud, don't try random ports — use exactly what they give us.
    if is_server:
        _validate_port_available(port)

    logging.info(
        "Stupidex starting: host=%s port=%d server=%s pid=%d",
        host,
        port,
        is_server,
        os.getpid(),
    )

    from stupidex.web import app

    if not is_server:
        threading.Thread(
            target=lambda: _open_browser_when_ready(
                f"http://127.0.0.1:{port}", "127.0.0.1", port
            ),
            daemon=True,
        ).start()

    _run_server(app, host, port)
    return 0


def _validate_port_available(port: int) -> None:
    """Warn if the port is already in use (helpful for debugging)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("0.0.0.0", port))
    except OSError as e:
        logging.warning("Port %d is already in use: %s. The server may fail.", port, e)


def _open_browser_when_ready(url: str, host: str, port: int) -> None:
    for _ in range(60):
        try:
            with socket.create_connection((host, port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.5)
    webbrowser.open(url)


if __name__ == "__main__":
    raise SystemExit(main())
