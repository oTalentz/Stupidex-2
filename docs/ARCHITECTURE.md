# Stupidex — Arquitetura do Sistema

## Visão Geral

Stupidex é um agente de código com IA que opera via interface web. O sistema
segue uma arquitetura monolítica modular: um servidor Flask que serve frontend
estático + API REST + SSE streaming, com módulos auxiliares para shell,
PostgreSQL, Redis e processamento em background.

```
                     Navegador
                        |
                  Flask (web.py)
                   /    |     \
             routes/  services/  llm/
              (API)   (lógica)  (agente)
                  \    |     /
                   (SSE streaming)
```

## Componentes

### `src/stupidex/web.py`
Ponto de entrada WSGI. Cria o app Flask, configura CORS/middleware/rate-limit,
e importa os módulos de rota. ~160 linhas — fino, sem lógica de negócio.

### `src/stupidex/routes/`
Pacote com 5 módulos de rota:

| Módulo | Rotas | Função |
|--------|-------|--------|
| `health.py` | `/`, `/api/health`, OPTIONS | Health check, preflight CORS |
| `auth.py` | `/api/auth/*`, `/api/integrations/github/*` | Login, registro, OAuth Google/GitHub |
| `providers.py` | `/api/providers`, `/api/config` | Provedores LLM, config do usuário |
| `sessions.py` | `/api/sessions/*` | CRUD de sessões, chat streaming, browser turn, agent bridge |
| `workspaces.py` | `/api/workspaces/*` | CRUD de workspaces, upload, git, shell, file tree |

### `src/stupidex/services/`
Pacote com lógica pura (sem dependência Flask):

| Módulo | Função |
|--------|--------|
| `auth_service.py` | Token request/cookie/secret management |
| `rate_limit.py` | Sliding window rate limiter por bucket |
| `stream_manager.py` | Gerenciamento de stream claims e session locks |
| `validation.py` | Validação de imagens, git URLs, browser tool traces |

### `src/stupidex/llm/`
Pacote do agente de IA:

| Módulo | Função |
|--------|--------|
| `handle_input.py` | Loop agente: history, tool execution, streaming SSE |
| `message.py` | Modelos ChatMessage, ToolCall, Usage |
| `providers.py` | Registro e descoberta de provedores LLM |
| `tools.py` | Definições e execução de ferramentas (read/write/shell/git/web) |
| `web_mcp.py` | MCP server experimental |

### Módulos de Infraestrutura

| Módulo | Função |
|--------|--------|
| `db.py` | SQLite persistence (usuários, sessões, mensagens) |
| `db_async.py` | PostgreSQL via SQLAlchemy + migrations (fallback SQLite) |
| `redis_client.py` | Redis: rate limits, locks, cache, job queue, pub/sub (fallback in-memory) |
| `worker.py` | Task handlers (clone, index, cleanup, agent) + background worker loop |
| `shell_executor.py` | Executor de shell estruturado: parser, allowlist, quotas, audit, cross-platform |
| `config.py` | Config unificada (`DATA_DIR`, `load_config`) |
| `launcher.py` | Entry point (`stupidex` CLI), detecta server/TUI mode |

## Fluxo de Dados

### Chat Streaming
```
Usuário → POST /api/sessions/<id>/chat
  → routes/sessions.py: _session_chat_impl()
    → claim_stream() (adquire lock)
    → build_context() (cria AgentContext com modo/tools)
    → stream_response() (generator de eventos SSE)
      → _history_for_llm() (carrega + system prompt)
      → litellm.completion() (chamada LLM)
      → loop de tool calls até MAX_TOOL_ITERATIONS
      → yield eventos {type: text|thinking|tool_calls|tool_result|done|error}
    → Response(event_stream(), mimetype=text/event-stream)
```

### Execução de Ferramentas
```
LLM → tool_call → _execute_tool(name, args, ctx)
  ├── read_file / write_file / edit_file → sistema de arquivos (sandbox workspace)
  ├── list_dir / search_files / mkdir / delete → sistema de arquivos
  ├── run_shell → shell_executor.run_command() (parser + quotas + audit)
  ├── git → tools.git() (com github_token efêmero)
  └── web_search → fetcher web
```

### Autenticação
```
Request → login_required decorator
  → request_token() (Bearer header ou cookie)
  → db.validate_token() → User
  → request.user = user
```

## Modos de Agente

O sistema possui 6 modos, cada um com prompt de sistema e conjunto de
ferramentas específicos:

| Modo | Ferramentas | Uso |
|------|------------|-----|
| `chat` | Nenhuma | Conversa sem acesso ao workspace |
| `ask` | `web_search` | Perguntas e respostas com pesquisa web |
| `plan` | Leitura + `git` (read-only) | Análise de arquitetura e planejamento |
| `agent` | Todas | Edição, teste, versionamento (default) |
| `review` | Leitura + `git diff/log` | Revisão de código |
| `debug` | Todas | Diagnóstico e correção de bugs |

## Banco de Dados

### SQLite (padrão)
- Arquivo único em `{DATA_DIR}/stupidex.db`
- Tabelas: `users`, `sessions`, `messages`, `auth_tokens`, `providers`, `config`

### PostgreSQL (via `DATABASE_URL`)
- SQLAlchemy com 10 migrations (001-010)
- Tabelas adicionais: `workspace_meta`, `shell_executions`, `audit_logs`,
  `usage_records`, `agent_runs`, `approvals`
- Ativado quando `DATABASE_URL` está presente no ambiente

## Redis (via `REDIS_URL`)
- Rate limits distribuídos, locks, cache, job queue, pub/sub
- Fallback in-memory quando Redis não está disponível

## Deploy

- **Square Cloud**: 3 apps (web, worker, litellm) compartilhando o mesmo repo
- **Web app**: `MAIN=bash scripts/start-square.sh` — gunicorn + migrations
- **Worker app**: `MAIN=bash scripts/start-worker.sh` — background worker loop
- **LiteLLM app**: `MAIN=bash scripts/start-litellm.sh` — proxy multi-provedor
- **Local**: `python -m stupidex.web` — Flask dev server em `localhost:5000`
