# Stupidex na Square Cloud — Guia de Deploy

Este guia mostra como publicar o Stupidex no plano Hobby (R$ 24,99/mês) da
Square Cloud em ~5 minutos.

---

## ⚠️ O que mudou pro Square Cloud

Como o container Hobby **não tem `git` CLI**, o `git clone` foi
substituído por um **fallback que baixa o repositório como ZIP** via
HTTPS (suporta github.com e gitlab.com nativamente).

---

## Passo 1 — Pegar a API key

1. Acesse https://squarecloud.app/pt-br/account/security
2. Clique em **"Solicitar Chave da API"**
3. Copie a chave (ela é longa, tipo `sqc_abc123...`)

---

## Passo 2 — Instalar a CLI

A Square Cloud tem uma CLI em Node.js. Abra o terminal:

```powershell
npm install -g @squarecloud/cli
```

Se não tiver Node, instale: https://nodejs.org/

---

## Passo 3 — Login

```powershell
squarecloud auth login
```

Vai pedir a API key. Cole.

---

## Passo 4 — Preparar o ZIP

A Square Cloud aceita **ZIP** do projeto. Eles detectam Python automaticamente
se tiver `requirements.txt` na raiz e o config `squarecloud.app`.

**Estrutura do ZIP** (raiz):

```
stupidex.zip
├── squarecloud.app          ← config obrigatório
├── requirements.txt          ← dependências
├── launcher.py               ← entry point
├── pyproject.toml
├── README.md
├── DEPLOY.md
├── HOSTING.md (opcional)
└── src/
    └── stupidex/
        ├── __init__.py
        ├── config.py
        ├── db.py
        ├── folders.py (legacy, pode ignorar)
        ├── workspaces.py
        ├── web.py
        ├── main.py (TUI legacy, ignorado)
        ├── llm/
        │   ├── __init__.py
        │   ├── _bootstrap.py
        │   ├── handle_input.py
        │   ├── message.py
        │   ├── providers.py
        │   └── tools.py
        └── static/
            ├── index.html
            ├── style.css
            └── app.js
```

No Windows, pra zipar:
```powershell
# PowerShell
Compress-Archive -Path launcher.py, requirements.txt, squarecloud.app, src, pyproject.toml -DestinationPath stupidex.zip -Force
```

⚠️ **Não inclua** `.venv`, `__pycache__`, `dist/`, `build/`, `.git/`,
`test_*.py` (aumenta o zip e pode dar conflito).

---

## Passo 5 — Deploy

```powershell
squarecloud upload --file stupidex.zip
```

A CLI vai:
1. Fazer upload do ZIP
2. Pedir nome e subdomain
3. Disparar o deploy
4. Te dar a URL `https://stupidex.squareweb.app` (ou similar)

Para ver os logs:
```powershell
squarecloud logs
```

---

## Passo 6 — Configurar variáveis de ambiente

No dashboard (https://squarecloud.app/pt-br/dashboard) ou via CLI:

```powershell
squarecloud env set DEEPSEEK_API_KEY=sk-substitua-pela-sua-chave
squarecloud env set STUPIDEX_TOKEN=uma-senha-segura-de-pelo-menos-32-chars
squarecloud env set GITHUB_CLIENT_ID=seu-client-id
squarecloud env set GITHUB_CLIENT_SECRET=seu-client-secret
squarecloud env set GITHUB_REDIRECT_URI=https://<seu-subdomain>.squareweb.app/api/integrations/github/callback
squarecloud env set FRONTEND_URL=https://<seu-subdomain>.squareweb.app
```

---

## Passo 7 — Primeiro acesso

1. Abra `https://<seu-subdomain>.squareweb.app` no navegador
2. Faça login com o token que você definiu (cabeçalho `Authorization: Bearer <token>`)
3. O Stupidex vai pedir pra **escolher um workspace** — clique em "Upload" ou "Git clone"
4. **Git clone funciona** porque o app baixa o ZIP em vez de depender do executável Git
5. Para projetos privados, conecte sua conta GitHub no modal de clonagem ou nas configurações

---

## Como funciona o fallback de Git

Quando você cola uma URL do GitHub no modal "Git clone":

| Antes (com git CLI) | Agora (sem git CLI) |
|---|---|
| `git clone https://github.com/user/repo` | `https://codeload.github.com/user/repo/zip/refs/heads/main` |
| Preserva histórico, submodules, LFS | Só o snapshot do branch |
| Funciona com qualquer host git | Só github.com e gitlab.com |

Para outros hosts Git, o app mostra uma mensagem amigável pedindo que
você faça upload do ZIP manualmente.

---

## Config (`squarecloud.app`)

```ini
MAIN=launcher.py
MEMORY=2048
VERSION=recommended
SUBDOMAIN=stupidex
DISPLAY_NAME=Stupidex
DESCRIPTION=Stupidex - agente de código com IA (DeepSeek)
```

- `MAIN=launcher.py` — entry point
- `MEMORY=2048` — 2 GB de RAM (Hobby (2) tem 2 GB)
- `SUBDOMAIN=stupidex` — subdomínio customizado grátis
- O launcher detecta `STUPIDEX_SERVER=1` e usa gunicorn (se disponível) ou Flask dev server

---

## Variáveis de ambiente recomendadas

| Var | Valor | Descrição |
|---|---|---|
| `DEEPSEEK_API_KEY` | `sk-...` | Key da API DeepSeek |
| `STUPIDEX_TOKEN` | (string aleatória) | Token de auth pra UI |
| `STUPIDEX_HOST` | `0.0.0.0` | (já é o default no server mode) |
| `STUPIDEX_SERVER` | `1` | Liga modo servidor no launcher |
| `STUPIDEX_DATA_DIR` | `/data` | (opcional) Onde salvar SQLite e workspaces |
| `STUPIDEX_WORKSPACES_DIR` | `/data/workspaces` | (opcional) Onde salvar workspaces clonados |

---

## ⚠️ Limitações conhecidas no plano Hobby

| Limitação | Workaround |
|---|---|
| **Sem `git` CLI** | Já contornado com download via ZIP |
| **Sem volumes persistentes grátis** | SQLite e workspaces podem ser perdidos entre deploys (a não ser que você use disco persistente do plano Standard) |
| **Sem domínio customizado grátis** | Você recebe `<subdomain>.squareweb.app` (grátis). Domínio próprio só no Standard+ |
| **Cold start leve** | Primeiro request após 5 min parado pode demorar 5-10s |
| **Sem SSH** | Use `squarecloud logs` e o dashboard pra debug |
| **Sem Uptime 100%** | Plano Hobby tem "garantia" apenas comercial. Pra SLA real, precisa Enterprise |

---

## Workflow diário

```powershell
# Ver logs em tempo real
squarecloud logs -f

# Reiniciar o app
squarecloud restart

# Ver info do app
squarecloud status

# Listar apps
squarecloud apps list
```

---

## Troubleshooting

| Sintoma | Causa | Fix |
|---|---|---|
| "O arquivo principal é inválido" | `MAIN` no config não bate | Confira `MAIN=launcher.py` |
| "Memória insuficiente" | LiteLLM + Flask > 1GB | Upgrade Hobby (2) com 2GB |
| Chat falha com "cl100k_base" | PyInstaller issue, mas isso é só no .exe local | Não afeta deploy |
| Site abre mas não responde | App ainda iniciando | Aguarde 30s e recarregue |
| Workspace some após deploy | Disco sem persistência | Faça backup do `.db` antes de redeploy |

---

## Backup do histórico

Como o disco pode resetar, faça backup regular do banco:

```bash
# Em algum lugar externo, baixe:
https://<subdomain>.squareweb.app/api/sessions/<id>/export?format=json
```

Ou programe um cron no seu PC:
```bash
# Toda hora, baixa o DB
curl -H "Authorization: Bearer $TOKEN" \
  https://<subdomain>.squareweb.app/api/sessions > sessions.json
```

---

## TL;DR

1. ZIP do projeto (sem `.venv`, `__pycache__`, `test_*.py`)
2. `squarecloud upload --file stupidex.zip`
3. `squarecloud env set DEEPSEEK_API_KEY=sk-...`
4. `squarecloud env set STUPIDEX_TOKEN=senha-segura`
5. Abrir `https://<subdomain>.squareweb.app`
6. ✅ Online 24/7 por R$ 24,99/mês
