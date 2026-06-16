#!/usr/bin/env bash
# start-litellm.sh — Square Cloud LiteLLM app entry point.
# Runs an OpenAI-compatible proxy that routes to multiple providers.
set -euo pipefail

cd "$(dirname "$0")/../litellm" 2>/dev/null || {
    echo "[litellm] litellm/ directory not found — installing..."
    pip install litellm 2>/dev/null || true
    cd "$(dirname "$0")/.." || exit 1
}

LITELLM_PORT="${PORT:-8080}"
LITELLM_CONFIG="${LITELLM_CONFIG:-config.yaml}"

echo "[litellm] starting on port $LITELLM_PORT (config=$LITELLM_CONFIG)"

# If config doesn't exist, generate a basic one
if [ ! -f "$LITELLM_CONFIG" ]; then
    cat > "$LITELLM_CONFIG" << 'YAML'
general_settings:
  master_key: ${LITELLM_MASTER_KEY:-sk-litellm-master}
  database_url: ${DATABASE_URL:-}
  otel: false

model_list:
  - model_name: gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: ${OPENAI_API_KEY:-}
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: ${OPENAI_API_KEY:-}
  - model_name: claude-3-5-sonnet
    litellm_params:
      model: anthropic/claude-3-5-sonnet-20241022
      api_key: ${ANTHROPIC_API_KEY:-}
  - model_name: claude-3-haiku
    litellm_params:
      model: anthropic/claude-3-haiku-20240307
      api_key: ${ANTHROPIC_API_KEY:-}
  - model_name: gemini-2.0-flash
    litellm_params:
      model: gemini/gemini-2.0-flash-exp
      api_key: ${GEMINI_API_KEY:-}
  - model_name: deepseek-chat
    litellm_params:
      model: deepseek/deepseek-chat
      api_key: ${DEEPSEEK_API_KEY:-}
YAML
    echo "[litellm] generated default config"
fi

exec litellm --port "$LITELLM_PORT" --config "$LITELLM_CONFIG" --detailed_response
