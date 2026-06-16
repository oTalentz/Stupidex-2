# Stupidex — Guia de Hospedagem 24/7

Este documento cobre como tirar o Stupidex da sua máquina local e colocar
online 24/7. As opções vão do mais simples (1 comando) ao mais robusto
(produção com Docker + nginx).

---

## Visão geral das opções

| Opção | Custo/mês | Dificuldade | Tempo de setup | Ideal para |
|---|---|---|---|---|
| **Railway.app** | ~US$ 5 (free tier disponível) | Muito fácil | 5 min | Demo, projeto pessoal |
| **Fly.io** | Free tier generoso | Fácil | 10 min | Apps globais edge |
| **Render.com** | Free tier (com sleep) | Muito fácil | 5 min | MVP, side project |
| **VPS (Hetzner/DigitalOcean)** | US$ 4-6 | Média | 30-60 min | Controle total, 24/7 real |
| **Oracle Cloud Free Tier** | **US$ 0 (always-free)** | Média | 30-60 min | Produção gratuita permanente |
| **Docker self-hosted** | depende do host | Média | 20 min | Já tem servidor |

---

## 0. Preparação: arquivos obrigatórios

Independente da opção, você vai precisar de:

### `requirements.txt`

```
flask>=3.0
litellm>=1.40
textual>=0.80
python-dotenv>=1.0
gunicorn>=21.0
```

### `.env.example`

```bash
# API DeepSeek
DEEPSEEK_API_KEY=sk-substitua-pela-sua-chave

# Configuração do servidor
STUPIDEX_HOST=0.0.0.0
STUPIDEX_PORT=5000
STUPIDEX_DEBUG=0
```

### `Procfile` (para Railway/Render/Heroku)

```
web: gunicorn 'stupidex.web:app' --bind 0.0.0.0:$PORT --workers 1 --timeout 120 --threads 8
```

### `Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY pyproject.toml ./

ENV PYTHONPATH=/app/src
ENV STUPIDEX_HOST=0.0.0.0

EXPOSE 5000

CMD ["gunicorn", "stupidex.web:app", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--timeout", "120", \
     "--threads", "8", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
```

### `runtime.txt`

```
python-3.12.6
```

### `.dockerignore`

```
.git
__pycache__
*.pyc
.venv
build/
dist/
*.spec
```

---

## 1. Railway.app (mais fácil)

**Free tier:** US$ 5 de crédito/mês. Mais que suficiente para uso pessoal.

### Passo a passo

```bash
# 1. Instalar CLI
npm install -g @railway/cli

# 2. Login
railway login

# 3. Inicializar projeto
cd C:\Users\leona\Downloads\Stupidex
railway init

# 4. Adicionar variável de ambiente
railway variables set DEEPSEEK_API_KEY=sk-substitua-pela-sua-chave

# 5. Deploy
railway up
```

Pronto. Railway detecta o `Procfile` ou `Dockerfile`, faz build e expõe uma URL `https://seu-app.up.railway.app`.

### Configurar domínio customizado (opcional)

`railway domain` para gerar domínio, ou adicione CNAME em `railway.app` no seu DNS.

---

## 2. Fly.io (grátis, edge global)

**Free tier:** 3 shared VMs, 3GB de storage.

### Passo a passo

```bash
# 1. Instalar
iwr https://fly.io/install.ps1 -useb | iex

# 2. Login
fly auth signup

# 3. Inicializar
cd C:\Users\leona\Downloads\Stupidex
fly launch --no-deploy
# Responda: nome do app, região (gru = São Paulo), sem Postgres, sem Redis

# 4. Adicionar secret
fly secrets set DEEPSEEK_API_KEY=sk-substitua-pela-sua-chave

# 5. Deploy
fly deploy
```

### Persistência (importante!)

A SQLite fica em `~/.stupidex/stupidex.db`. Sem volume, ela some a cada deploy. Crie um volume:

```bash
fly volumes create stupidex_data --size 1
```

E adicione ao `fly.toml`:

```toml
[mounts]
  source = "stupidex_data"
  destination = "/data"
```

E no `Dockerfile`, crie um link simbólico:

```dockerfile
RUN mkdir -p /root/.stupidex && ln -s /data/stupidex.db /root/.stupidex/stupidex.db
```

---

## 3. Render.com

**Free tier:** Apps web com sleep após 15 min de inatividade (acorda na próxima request). Bom pra MVP, ruim pra uso intenso 24/7.

1. Suba o código para GitHub
2. Em [render.com](https://render.com) → New → Web Service → conecte o repo
3. Configurações:
   - **Environment:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn 'stupidex.web:app' --bind 0.0.0.0:$PORT --timeout 120`
4. Add environment variable: `DEEPSEEK_API_KEY=sk-...`
5. Click **Deploy**

**Para evitar sleep**, use plano pago (US$ 7/mês) ou faça um cron externo que pinga a cada 14 min.

---

## 4. VPS (controle total)

Hetzner, DigitalOcean, Contabo, OVH. Mais barato a longo prazo e total controle.

### Setup inicial (Ubuntu 22.04)

```bash
# 1. Conectar
ssh root@SEU_IP

# 2. Atualizar
apt update && apt upgrade -y

# 3. Instalar Python, nginx, git
apt install -y python3.12 python3-pip python3-venv nginx certbot python3-certbot-nginx git

# 4. Criar usuário não-root
adduser stupidex --disabled-password --gecos ""
usermod -aG sudo stupidex
su - stupidex

# 5. Clonar/Stupidex
git clone https://github.com/Zeptiny/stupidex.git /home/stupidex/app
cd /home/stupidex/app

# 6. Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 7. Configurar .env
cp .env.example .env
nano .env  # editar DEEPSEEK_API_KEY

# 8. Testar
STUPIDEX_HOST=127.0.0.1 STUPIDEX_PORT=5000 python -m stupidex.web
# Ctrl+C para parar
```

### Systemd service (manter rodando 24/7)

```bash
sudo nano /etc/systemd/system/stupidex.service
```

```ini
[Unit]
Description=Stupidex web server
After=network.target

[Service]
Type=simple
User=stupidex
WorkingDirectory=/home/stupidex/app
EnvironmentFile=/home/stupidex/app/.env
ExecStart=/home/stupidex/app/.venv/bin/gunicorn 'stupidex.web:app' \
    --bind 127.0.0.1:5000 \
    --workers 1 \
    --timeout 120 \
    --threads 8 \
    --access-logfile /var/log/stupidex/access.log \
    --error-logfile /var/log/stupidex/error.log
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo mkdir -p /var/log/stupidex && sudo chown stupidex:stupidex /var/log/stupidex
sudo systemctl daemon-reload
sudo systemctl enable --now stupidex
sudo systemctl status stupidex
```

### Nginx reverse proxy + HTTPS

```bash
sudo nano /etc/nginx/sites-available/stupidex
```

```nginx
server {
    server_name chat.seudominio.com;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/stupidex /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d chat.seudominio.com
```

Pronto, está no ar em `https://chat.seudominio.com`.

---

## 5. Oracle Cloud Free Tier (sempre grátis)

A Oracle oferece **2 VMs ARM sempre gratuitas** (4 OCPUs, 24GB RAM cada). É a melhor opção gratuita permanente.

1. Criar conta em [cloud.oracle.com](https://cloud.oracle.com)
2. Compute → Instances → Create Instance
   - Shape: `VM.Standard.A1.Flex` (ARM, always free)
   - Image: Ubuntu 22.04
3. Salvar chave SSH, conectar, seguir **mesmo passo a passo do item 4** (VPS)

**Caveat:** a reserva da VM.A1.Flex pode demorar dias/semanas por estar sempre lotada. Tente em horários de menor movimento ou use listas de espera.

---

## 6. Segurança — checklist para produção

Quando subir online, **não** cometa estes erros:

### OBRIGATÓRIO

- [ ] **Trocar a API key** do DeepSeek — a `sk-a56f7...` está commitada no Git e em texto plano. Vá em [platform.deepseek.com](https://platform.deepseek.com/api_keys) e revogue/regenere.
- [ ] **Usar variável de ambiente** (`DEEPSEEK_API_KEY`), nunca hardcode
- [ ] **HTTPS** com Let's Encrypt (automático via certbot)
- [ ] **Não expor a porta 5000** diretamente — sempre passar por nginx/caddy

### RECOMENDADO

- [ ] Adicionar autenticação (auth básica, ou OAuth) — sem isso, qualquer um que souber a URL vai gastar seus créditos
- [ ] Rate limiting no nginx (ex.: 10 requests/min por IP)
- [ ] Backup regular do `stupidex.db` (`cp ~/.stupidex/stupidex.db backup-$(date +%F).db`)
- [ ] Monitoramento simples: UptimeRobot.com pinga sua URL a cada 5 min e te avisa se cair
- [ ] Log rotation: `logrotate` para `/var/log/stupidex/*.log`

### Adicionando autenticação simples

Edite `src/stupidex/web.py` antes do `app = Flask(...)`:

```python
import os
from functools import wraps
from flask import request, Response

BASIC_USER = os.environ.get("STUPIDEX_USER", "admin")
BASIC_PASS = os.environ.get("STUPIDEX_PASS", "")

def _check_auth():
    auth = request.authorization
    return auth and auth.username == BASIC_USER and auth.password == BASIC_PASS

def _require_auth(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not _check_auth():
            return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Stupidex"'})
        return f(*a, **kw)
    return wrapper

# Então decore cada rota:
# @_require_auth
# def index(): ...
```

E defina `STUPIDEX_USER` e `STUPIDEX_PASS` no `.env` (ou no Railway/Fly).

---

## 7. Próximos passos / ideias

- **Login com Google/GitHub** — usar Authlib + OAuth
- **Multi-tenancy** — adicionar `user_id` em `sessions` e isolar por usuário
- **Compartilhar conversa** — botão "share" que gera link público read-only
- **Fila de jobs** — Redis + RQ para processar mensagens longas sem travar o worker
- **Webhook** — Stupidex roda em background e te avisa via Telegram/Discord quando termina
- **Agendamento** — "toda segunda rode os testes do projeto X" (precisa de cron)
- **MCP (Model Context Protocol)** — já está no TODO do projeto original; daria acesso a integrações externas padronizadas

---

## TL;DR — escolha rápida

- **Quer online em 5 min, sem mexer em servidor:** Railway
- **Quer grátis permanente, com persistência:** Fly.io com volume
- **Quer controle total + domínio próprio:** VPS Hetzner + nginx + certbot
- **Quer US$ 0 e tem paciência:** Oracle Cloud Free Tier

Em qualquer um deles: **não esqueça de revogar/regenerar a API key** que está hardcoded.
