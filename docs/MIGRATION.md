# Guia de Migração

## Migration 001-010: PostgreSQL / SQLAlchemy

### O que mudou
O banco de dados agora suporta PostgreSQL via SQLAlchemy além do SQLite
existente. As migrations são definidas em `src/stupidex/db_async.py`.

### Como migrar

**SQLite (padrão, sem ação necessária):**
```bash
# Nada a fazer — o sistema continua usando SQLite como antes
```

**PostgreSQL (novo):**
```bash
# 1. Configure a variável de ambiente
export DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/stupidex"

# 2. As migrations rodam automaticamente na inicialização do web.py
python -m stupidex.web
```

### O que as migrations criam
| Migration | Tabela | Descrição |
|-----------|--------|-----------|
| 001 | `users` | Usuários (espelho do SQLite) |
| 002 | `sessions` | Sessões de chat |
| 003 | `messages` | Mensagens das sessões |
| 004 | `auth_tokens` | Tokens de autenticação |
| 005 | `workspace_meta` | Metadados de workspace |
| 006 | `shell_executions` | Log de comandos shell |
| 007 | `audit_logs` | Trilha de auditoria |
| 008 | `usage_records` | Uso de tokens LLM |
| 009 | `agent_runs` | Execuções do agente |
| 010 | `approvals` | Aprovações de comandos |

---

## Redis (Cache Distribuído)

### O que mudou
Rate limits, locks e cache agora suportam Redis como backend distribuído.
Quando Redis não está disponível, o sistema usa fallback in-memory.

### Como ativar
```bash
export REDIS_URL="redis://user:pass@host:6379/0"
```

### Funcionalidades com Redis
- Rate limits distribuídos (entre múltiplos workers)
- Locks de sessão compartilhados
- Cache de respostas com TTL
- Fila de jobs para o worker
- Pub/sub para eventos em tempo real

### Fallback in-memory
Se `REDIS_URL` não for configurada, tudo continua funcionando com
armazenamento em memória local (não compartilhado entre processos).

---

## Worker (Processamento em Background)

### O que mudou
Tarefas pesadas (clone, index, cleanup) podem ser executadas em background
por um worker loop, liberando o web server.

### Como ativar

**Web + Worker integrado (default):**
```python
# Em web.py:main() — o worker roda como daemon thread dentro do web server
```

**Worker dedicado (Square Cloud):**
```bash
# Crie um segundo app Square Cloud com:
MAIN=bash scripts/start-worker.sh
```

### Tasks disponíveis
| Task | Descrição |
|------|-----------|
| `clone_repo` | Clona repositório git |
| `index_workspace` | Indexa arquivos do workspace |
| `cleanup_workspace` | Limpa workspaces órfãos |
| `dispatch_agent_run` | Executa agente assíncrono |

---

## LiteLLM Gateway

### O que mudou
O Stupidex pode usar um proxy LiteLLM para rotear requests entre
múltiplos provedores LLM (OpenAI, Anthropic, Google, etc.).

### Como ativar
```bash
# Crie um terceiro app Square Cloud com:
MAIN=bash scripts/start-litellm.sh

# Configure as chaves dos provedores no ambiente do app LiteLLM
```

### Configuração automática
O script `start-litellm.sh` gera um `config.yaml` a partir das variáveis
de ambiente (`LITELLM_*`), eliminando configuração manual.

---

## Shell Executor Estruturado

### O que mudou
O comando `run_shell` foi refatorado para usar `shell_executor.py`, que
adiciona parsing, allowlist, quotas, audit logging e suporte cross-platform.

### Compatibilidade
- A API pública (`run_shell(command, cwd?, timeout?)`) não mudou
- O formato de retorno é idêntico ao anterior
- Git commands continuam roteados para o `git()` tool dedicado

### Comandos permitidos (default)
```
cat, ls, find, grep, head, tail, wc, sort, uniq, echo, printf, which,
python, python3, pip, pip3, npm, npx, node, dotnet, cargo, rustc, go,
make, gcc, g++, clang, cmake, git, docker (apenas info), pwd, mkdir,
rmdir, touch, cp, mv, rm (restrito)
```

---

## Modos de Agente

### O que mudou
O sistema agora tem 6 modos em vez de um toggle liga/desliga.

### Tabela de compatibilidade
| Funcionalidade | Antes | Depois |
|----------------|-------|--------|
| Ativar ferramentas | `agentModeEnabled: true/false` | `mode: "agent"` |
| Modo chat | Apenas "Agente on/off" | `chat, ask, plan, agent, review, debug` |
| Frontend | Botão toggle `#composer-agent` | Seletor de 6 modos `#composer-modes` |
| API request body | `{ ... }` | `{ ..., mode: "agent" }` |

### localStorage
```javascript
// Antes
localStorage.getItem("stupidex_agent_mode") // "0" | "1"

// Depois
localStorage.getItem("stupidex_mode") // "chat" | "ask" | "plan" | "agent" | "review" | "debug"
```

---

## Web.py Refatorado

### O que mudou
O `web.py` foi reduzido de ~2100 linhas para ~160 linhas. A lógica foi
extraída para `routes/`, `services/`, e `llm/`.

### Mapa de migração (para contribuidores)
| Arquivo antigo | Localização nova |
|----------------|------------------|
| `web.py` (validação) | `services/validation.py` |
| `web.py` (rate limit) | `services/rate_limit.py` |
| `web.py` (auth helpers) | `services/auth_service.py` |
| `web.py` (stream) | `services/stream_manager.py` |
| `web.py` (rotas auth) | `routes/auth.py` |
| `web.py` (rotas health) | `routes/health.py` |
| `web.py` (rotas providers) | `routes/providers.py` |
| `web.py` (rotas sessions) | `routes/sessions.py` |
| `web.py` (rotas workspaces) | `routes/workspaces.py` |

### Import paths preservados
```python
# Todos estes imports continuam funcionando:
from stupidex.web import app, login_required, rate_limited
from stupidex.llm.handle_input import stream_response, build_context
```

---

## Rollback

Se precisar voltar à versão anterior:

```bash
# Reverta os commits
git log --oneline -20
git revert HEAD~n..HEAD

# Ou use git stash se não tiver commitado
git stash pop
```
