"""Stupidex launcher.

Three modes:
  - Desktop (default): picks a free port on 127.0.0.1 and opens the browser.
  - Server (STUPIDEX_SERVER=1): binds 0.0.0.0:$PORT (cloud hosts).
  - Dev: just runs the Flask dev server with debug.

Server-mode WSGI server:
  - On Linux/macOS: gunicorn (production-grade, multi-worker)
  - On Windows: waitress (gunicorn needs `fcntl` which is Linux-only)
  - Falls back to Flask dev server if neither is available.

To stop: Ctrl+C.
"""
import logging
import os
import platform
import socket
import threading
import time
import webbrowser


def _find_free_port(preferred: int = 5000) -> int:
    for port in (preferred, 5001, 5002, 5003, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port found")


def _open_browser_when_ready(url: str, host: str, port: int) -> None:
    for _ in range(60):
        try:
            with socket.create_connection((host, port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.5)
    webbrowser.open(url)


def _setup_logging() -> str:
    log_path = os.path.join(os.path.expanduser("~"), ".stupidex", "stupidex.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
    )
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        root.addHandler(handler)
    return log_path


def _run_server(app, host: str, port: int) -> None:
    """Run Flask app with the best WSGI server available for this OS."""
    is_windows = platform.system() == "Windows"

    # Try gunicorn first (Linux/macOS only — needs `fcntl`)
    if not is_windows:
        try:
            from gunicorn.app.wsgiapp import WSGIApplication
            from gunicorn import config as gconfig

            cfg = gconfig.Config()
            cfg.bind = f"{host}:{port}"
            cfg.workers = 1
            cfg.threads = 8
            cfg.timeout = 120
            cfg.accesslog = "-"
            cfg.errorlog = "-"
            logging.info("Using gunicorn on %s:%d", host, port)
            WSGIApplication(cfg).run()
            return
        except ImportError as e:
            logging.warning("gunicorn not available (%s), falling back", e)

    # Try waitress (works on Windows + Linux)
    try:
        from waitress import serve
        logging.info("Using waitress on %s:%d", host, port)
        serve(app, host=host, port=port, threads=8, ident="Stupidex")
        return
    except ImportError as e:
        logging.warning("waitress not available (%s), falling back to Flask dev server", e)

    # Last resort: Flask's built-in dev server (fine for single-user dev)
    logging.info("Using Flask dev server on %s:%d", host, port)
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


def main() -> int:
    log_path = _setup_logging()

    is_server = os.environ.get("STUPIDEX_SERVER") == "1"
    host = os.environ.get("STUPIDEX_HOST", "0.0.0.0" if is_server else "127.0.0.1")
    # Cloud platforms (Render, Fly, Square Cloud, Railway, Heroku) all
    # expose a `PORT` env var; honor that first, then STUPIDEX_PORT, then 5000.
    preferred = int(
        os.environ.get("PORT")
        or os.environ.get("STUPIDEX_PORT")
        or "5000"
    )
    port = _find_free_port(preferred) if not is_server else preferred
    url = f"http://{host}:{port}/" if is_server else f"http://127.0.0.1:{port}/"

    logging.info("Stupidex starting on %s (pid=%d)", url, os.getpid())

    from stupidex.web import app

    if is_server:
        _run_server(app, host, port)
    else:
        threading.Thread(
            target=_open_browser_when_ready,
            args=(f"http://127.0.0.1:{port}", "127.0.0.1", port),
            daemon=True,
        ).start()
        _run_server(app, host, port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
