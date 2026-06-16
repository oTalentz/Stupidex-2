#!/usr/bin/env bash
# Stupidex — Square Cloud startup script
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

# Persistent data directory (Square Cloud persists /data)
DATA_DIR="${STUPIDEX_DATA_DIR:-${DATA_DIR:-./data}}"
mkdir -p "$DATA_DIR"/{workspaces,db,logs,tmp}
export STUPIDEX_DATA_DIR="$DATA_DIR"

# Verify/default env vars
# FRONTEND_URL: usado pelo OAuth redirect — opcional em modo local
: "${FRONTEND_URL:=https://${HOSTNAME:-localhost}}"
# NVIDIA_API_KEY: se não definida, usa a chave embutida em providers.py
: "${NVIDIA_API_KEY:=}"
# Shell: auto-approve por padrão (mude para "ask" para exigir confirmação)
: "${STUPIDEX_ENABLE_SHELL:=1}"
: "${STUPIDEX_SHELL_APPROVAL_MODE:=auto}"

# Log startup
echo "[start] Stupidex starting — data=$DATA_DIR frontend=$FRONTEND_URL"

# Run migrations (idempotent)
python -m stupidex.db --migrate 2>&1 | sed 's/^/[db] /' || echo "[db] WARN: migration failed (might be first run)"

# Health check: verify DB
python -c "
from stupidex.db import get_db
conn = get_db()
cursor = conn.execute('SELECT COUNT(*) FROM users')
print(f'[health] DB OK — {cursor.fetchone()[0]} users')
" 2>&1 || echo "[health] WARN: DB check failed"

# Start Gunicorn with centralized config
exec gunicorn \
  -c gunicorn.conf.py \
  --bind "0.0.0.0:${PORT:-8080}" \
  stupidex.web:app
