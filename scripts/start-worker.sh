#!/usr/bin/env bash
# start-worker.sh — Square Cloud worker app entry point.
# Runs the background worker for async tasks (clones, indexing, cleanup, agent runs).
set -euo pipefail

cd "$(dirname "$0")/.." || exit 1

# Run database migrations on startup
echo "[worker] running migrations..."
python -c "from stupidex.db_async import run_migrations; run_migrations()" || true

echo "[worker] starting worker loop..."
python -c "
import sys
import time
sys.path.insert(0, '.')
from stupidex.worker import start_worker
from stupidex.redis_client import get_client

# Wait up to 15s for Redis to be available
for i in range(30):
    if get_client() is not None:
        break
    time.sleep(0.5)

start_worker('default', poll_interval=0.5)
print('[worker] running... press Ctrl+C to stop')

try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    print('[worker] shutting down')
"
