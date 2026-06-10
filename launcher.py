"""Stupidex launcher.

Three modes:
  - Desktop (default): picks a free port on 127.0.0.1 and opens the browser.
  - Server (STUPIDEX_SERVER=1): binds 0.0.0.0:$PORT (used by Docker / cloud hosts).
  - Dev: just runs the Flask app with debug.

To stop: Ctrl+C. The bundled .exe (PyInstaller) wraps this with hidden-console flags.
"""
import logging
import os
import socket
import sys
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


def main() -> int:
    log_path = _setup_logging()

    is_server = os.environ.get("STUPIDEX_SERVER") == "1"
    host = os.environ.get("STUPIDEX_HOST", "0.0.0.0" if is_server else "127.0.0.1")
    preferred = int(os.environ.get("STUPIDEX_PORT", "5000"))
    port = _find_free_port(preferred) if not is_server else preferred
    url = f"http://{host}:{port}/" if is_server else f"http://127.0.0.1:{port}/"

    logging.info("Stupidex starting on %s (pid=%d)", url, os.getpid())

    from stupidex.web import app

    if not is_server:
        threading.Thread(target=_open_browser_when_ready, args=("http://127.0.0.1:" + str(port), "127.0.0.1", port), daemon=True).start()

    # Use gunicorn-style binding when in server mode (more robust for cloud hosts).
    # For desktop mode, just use Flask's dev server.
    if is_server:
        # Production: hand off to a proper WSGI server
        try:
            from gunicorn.app.wsgiapp import WSGIApplication
            from gunicorn import config
            cfg = config.Config()
            cfg.bind = f"{host}:{port}"
            cfg.workers = 1
            cfg.threads = 8
            cfg.timeout = 120
            cfg.accesslog = "-"
            cfg.errorlog = "-"
            WSGIApplication(cfg).run()
        except ImportError:
            # Fallback if gunicorn missing (e.g., local dev)
            app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)
    else:
        app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
