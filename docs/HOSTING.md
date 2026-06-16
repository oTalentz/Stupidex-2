# Stupidex — Guia de Hospedagem 24/7

Este documento cobre como colocar o Stupidex online de forma confiável
24/7. Há opções que vão de "grátis permanente" até "produção séria".

---

## TL;DR — escolha rápida

| Objetivo | Plataforma | Custo | Tempo |
|---|---|---|---|
| Demo / teste pessoal | **Localhost** + `ngrok` | US$ 0 | 1 min |
| MVP / side project | **Render** ou **Railway** | US$ 0–7/mês | 5 min |
| Bot pessoal 24/7 | **VPS Hetzner** + systemd | US$ 4/mês | 30 min |
| Bot compartilhado multi-usuário | **VPS** + **nginx** + **gunicorn** + **HTTPS** | US$ 4–6/mês | 1 h |
| Grátis permanente | **Oracle Cloud Free Tier** | US$ 0 | 1 h (cota limitada) |
| Global / edge | **Fly.io** | US$ 0–5/mês | 10 min |
| Híbrido (UI desktop + servidor remoto) | Use o `.exe` local + tunnel `cloudflared` | US$ 0 | 2 min |

---

## ⚠️ Antes de tudo: segurança

O Stupidex tem um sistema de autenticação embutido, mas a key da API
DeepSeek está em texto plano no `config.json` por padrão. Para qualquer
deploy público:

1. **Revogue** a key atual: https://platform.deepseek.com/api_keys
2. Defina via env var: `DEEPSEEK_API_KEY=sk-nova...`
3. Defina um token pra UI acessar: `STUPIDEX_TOKEN=uma-senha-grande`
4. **Sempre** rode atrás de HTTPS (nginx + certbot)
5. Faça backup do `~/.stupidex/stupidex.db` regularmente

---

## Opção 1 — Localhost + tunnel (mais simples, grátis)

Para acessar do celular ou compartilhar com amigos sem deploy:

```powershell
# No seu PC
$env:DEEPSEEK_API_KEY = "sk-nova-key"
$env:STUPIDEX_TOKEN = "minhasenha"
py -3 -m stupidex.web

# Em outro terminal
winget install Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:5000
```

O `cloudflared` te dá uma URL pública `*.trycloudflare.com` grátis.
Compartilhe essa URL com quem quiser (com a senha STUPIDEX_TOKEN).

---

## Opção 2 — Render.com (free tier)

**Free tier:** apps web com sleep após 15 min de inatividade.
**Plano pago:** US$ 7/mês → sem sleep.

1. Suba o código num repositório Git
2. https://render.com → New → Web Service → conecte o repo
3. Configurações:
   - **Runtime:** Python
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn 'stupidex.web:app' --bind 0.0.0.0:$PORT --workers 1 --timeout 120 --threads 8`
4. Adicione environment variables:
   - `DEEPSEEK_API_KEY` = sua key
   - `STUPIDEX_TOKEN` = senha para acessar a UI
5. Deploy

---

## Opção 3 — Railway.app

```powershell
npm install -g @railway/cli
railway login
railway init
railway variables set DEEPSEEK_API_KEY=sk-nova-key
railway variables set STUPIDEX_TOKEN=minhasenha
railway up
```

URL `*.up.railway.app` gerada automaticamente. Plano grátis com US$ 5
de crédito mensal (mais que suficiente para uso pessoal).

---

## Opção 4 — Fly.io (global edge, free tier generoso)

1. Instale o CLI: `iwr https://fly.io/install.ps1 -useb | iex`
2. Crie um `Dockerfile` na raiz do projeto (inclua no repo):

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY pyproject.toml ./
ENV PYTHONPATH=/app/src
EXPOSE 5000
CMD ["gunicorn", "stupidex.web:app", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "120", "--threads", "8"]
```

3. `fly launch --no-deploy`
4. `fly secrets set DEEPSEEK_API_KEY=sk-nova-key`
5. `fly secrets set STUPIDEX_TOKEN=minhasenha`
6. `fly deploy`

URL: `stupidex.fly.dev`. Free tier: 3 shared VMs.

Para persistir a SQLite entre deploys, monte um volume:

```powershell
fly volumes create stupidex_data --size 1
```

E adicione ao `fly.toml`:

```toml
[mounts]
  source = "stupidex_data"
  destination = "/data"
```

No `Dockerfile` adicione:

```dockerfile
RUN mkdir -p /root/.stupidex && ln -s /data/stupidex.db /root/.stupidex/stupidex.db
```

---

## Opção 5 — VPS (controle total)

Hetzner, DigitalOcean, Contabo, OVH. US$ 4-6/mês.

### Setup inicial (Ubuntu 22.04)

```bash
ssh root@SEU_IP
apt update && apt upgrade -y
apt install -y python3.12 python3-pip python3-venv nginx certbot python3-certbot-nginx git

adduser stupidex --disabled-password --gecos ""
usermod -aG sudo stupidex
su - stupidex

git clone https://github.com/Zeptiny/stupidex.git /home/stupidex/app
cd /home/stupidex/app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Variáveis de ambiente

```bash
cat > /home/stupidex/app/.env <<'EOF'
DEEPSEEK_API_KEY=sk-nova-key
STUPIDEX_TOKEN=uma-senha-grande-de-pelo-menos-32-chars
STUPIDEX_HOST=127.0.0.1
STUPIDEX_PORT=5000
EOF
```

### Systemd service (manter rodando 24/7)

```bash
sudo tee /etc/systemd/system/stupidex.service > /dev/null <<'EOF'
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
EOF

sudo mkdir -p /var/log/stupidex && sudo chown stupidex:stupidex /var/log/stupidex
sudo systemctl daemon-reload
sudo systemctl enable --now stupidex
sudo systemctl status stupidex
```

### nginx + HTTPS

```bash
sudo tee /etc/nginx/sites-available/stupidex > /dev/null <<'EOF'
server {
    server_name chat.seudominio.com;

    client_max_body_size 250M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE: disable buffering
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
        proxy_set_header Connection '';
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/stupidex /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# HTTPS grátis via Let's Encrypt
sudo certbot --nginx -d chat.seudominio.com
```

### Backup automático

```bash
crontab -e
# Adicione:
0 */6 * * * cp /home/stupidex/.stupidex/stupidex.db /home/stupidex/backups/stupidex-$(date +\%Y\%m\%d-\%H\%M).db
```

---

## Opção 6 — Oracle Cloud Free Tier (sempre grátis)

Oracle oferece **2 VMs ARM** (4 OCPUs, 24GB RAM cada) de graça
permanente. Cota limitada — tente em horário de menor movimento.

Passos:
1. https://cloud.oracle.com → Compute → Instances → Create
2. Shape: **VM.Standard.A1.Flex** (4 OCPU, 24GB)
3. OS: Ubuntu 22.04 (Canonical)
4. Salve a chave SSH, conecte
5. Siga o mesmo tutorial do **Opção 5**

---

## Compatibilidade

| Recurso | Funciona em | Observações |
|---|---|---|
| Chat streaming | Todas | SSE |
| Upload files | Todas | multipart |
| Git clone | Todas com `git` instalado | Pré-instale: `apt install git` |
| Drag & drop | Todas | navegador moderno |
| Temas (claro/escuro) | Todas | localStorage |
| Multi-sessão | Todas | SQLite |
| Multi-usuário | **VPS+nginx** (recomendado) | Free tier não aguenta |

---

## Monitoramento

### UptimeRobot (grátis)
1. https://uptimerobot.com → Add Monitor
2. Tipo: HTTP(s)
3. URL: `https://seu-dominio/api/health`
4. Intervalo: 5 min
5. Avisa por email/Telegram se cair

### Logs centralizados
```bash
sudo journalctl -u stupidex -f
```

---

## Escalabilidade

- **< 50 usuários:** gunicorn com 1 worker + 8 threads (atual) é suficiente
- **50-500:** 2-4 workers gunicorn, adicionar Redis para sessões de SSE
- **500+:** migrar de SQLite para PostgreSQL (alterar `db.py`)

---

## TL;DR final

**Se você quer 24/7 de graça:** Oracle Cloud Free Tier (quando conseguir)
ou VPS Hetzner de US$ 4/mês.

**Se quer 5 minutos:** Render.com ou Railway.app, com `STUPIDEX_TOKEN`
no env.

**Se quer profissional:** VPS + nginx + gunicorn + HTTPS + backup
automático.

A versão desktop (`dist/Stupidex.exe`) **continua funcionando** —
ela é a mesma aplicação, só muda o launcher.
