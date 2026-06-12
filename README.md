# Stupidex

**Agente de Código com IA** - Um assistente inteligente para desenvolvimento de software que entende, edita e executa código.

## 🚀 Início Rápido

### Pré-requisitos
- Python 3.10+
- Git (recomendado, para clonagem de repositórios e versionamento local)

### Instalação

```bash
# Criar ambiente virtual
python -m venv .venv

# Ativar ambiente
# Linux/Mac:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

# Instalar dependências
pip install -e .
```

### Executar

#### Interface Terminal (TUI)
```bash
stupidex
```

#### Interface Web
```bash
stupidex-web
```

Abra o navegador em: `http://127.0.0.1:5000`

#### Executável Standalone (Windows)
```bash
# Gerar executável com PyInstaller
pyinstaller stupidex.spec
# O executável será gerado em: dist/Stupidex.exe
```

Dê duplo clique no `Stupidex.exe` para iniciar o servidor e abrir o navegador automaticamente.

## 🔒 Segurança

- Autenticação web utiliza cookies `HttpOnly`; tokens bearer continuam suportados para clientes API
- Configurações de provedor, modelo e API key são armazenadas por usuário
- **Todas as chaves e tokens são criptografados em repouso** (Fernet)
- Tokens OAuth do GitHub e Google são criptografados
- **Execução de shell está habilitada por padrão no launcher** - Para ambientes de produção, configure:
  ```bash
  STUPIDEX_ENABLE_SHELL=1
  # Opcionalmente, restrinja executáveis (git está incluído por padrão):
  STUPIDEX_SHELL_COMMANDS="python,python3,pytest,node,npm,git"
  ```
- Em produção, persista o diretório `STUPIDEX_DATA_DIR` (a imagem Docker usa `/data`)
- **Workspace com git integrado**: Ao clonar repositórios, um diretório `.git` é criado automaticamente, permitindo versionamento local com commands como `git add`, `git commit`, `git push` (quando STUPIDEX_ENABLE_SHELL=1)

## 🔗 Integrações

### GitHub (Repositórios Privados)

Para clonar repositórios privados do GitHub, configure um GitHub OAuth App:

1. **Criar App no GitHub**:
   - Acesse: https://github.com/settings/developers
   - New OAuth App
   - Callback URL: `https://seu-dominio.com/api/integrations/github/callback` (para desenvolvimento local: `http://localhost:5000/api/integrations/github/callback`)

2. **Configurar variáveis de ambiente**:
```bash
GITHUB_CLIENT_ID=seu_client_id
GITHUB_CLIENT_SECRET=seu_client_secret
GITHUB_REDIRECT_URI=https://seu-dominio.com/api/integrations/github/callback
FRONTEND_URL=https://seu-dominio.com
```

   **Para desenvolvimento local**:
   ```bash
   GITHUB_CLIENT_ID=seu_client_id
   GITHUB_CLIENT_SECRET=seu_client_secret
   GITHUB_REDIRECT_URI=http://localhost:5000/api/integrations/github/callback
   FRONTEND_URL=http://localhost:5000
   ```

3. **Escopo**: O OAuth solicita o escopo `repo`, permitindo que o usuário conectado clone e atualize repositórios privados que tem acesso.

   > **Importante**: Após configurar, reinicie o servidor. A integração será automaticamente detectada.

✅ **Repositórios públicos do GitHub e GitLab funcionam sem conexão**
✅ **Clonagem de repositórios privados do GitHub funciona com OAuth conectado**

### Google OAuth (Login)

Para habilitar login com Google:
```bash
GOOGLE_CLIENT_ID=seu_client_id
GOOGLE_CLIENT_SECRET=seu_client_secret
# Opcional: URL de callback (padrão: http://localhost:5000/api/auth/google/callback)
GOOGLE_REDIRECT_URI=https://seu-dominio.com/api/auth/google/callback
```

1. Crie um projeto no [Google Cloud Console](https://console.cloud.google.com/)
2. Configure as credenciais OAuth 2.0
3. Adicione a URI de redirecionamento
4. Habilite a API Google People

✅ **Ao fazer login com Google, o avatar e nome do usuário são exibidos no banner de perfil**

## 📁 Estrutura do Projeto

```
.
├── src/
│   └── stupidex/
│       ├── main.py         # Entry point TUI (Terminal)
│       ├── web.py          # Servidor Flask + SSE streaming
│       ├── launcher.py     # Launcher desktop (servidor + navegador)
│       ├── config.py       # Configurações
│       ├── db.py           # Banco de dados SQLite + autenticação
│       ├── workspaces.py   # Gerenciamento de workspaces
│       ├── static/         # Frontend (HTML, CSS, JS)
│       │   ├── index.html  # SPA principal
│       │   ├── app.js       # Lógica da aplicação
│       │   └── style.css   # Estilos
│       └── llm/
│           ├── handle_input.py   # Streaming LLM + tool-calling
│           ├── message.py        # Modelos de dados de mensagens
│           ├── providers.py      # Provedores de IA
│           └── tools.py          # Ferramentas para o agente
├── pyproject.toml
├── requirements.txt
├── Dockerfile
└── README.md
```

## 🔧 Configuração Avançada

### Variáveis de Ambiente Importantes

```bash
# Porta do servidor (padrão: 5000)
PORT=8080

# Diretório de dados (padrão: ~/.stupidex)
STUPIDEX_DATA_DIR=/caminho/para/dados

# Habilitar execução de shell (DANGER!)
STUPIDEX_ENABLE_SHELL=1
STUPIDEX_SHELL_COMMANDS="python,python3,pytest,node,npm"

# CORS (para desenvolvimento)
STUPIDEX_CORS=http://localhost:3000,http://localhost:5173

# Limites
MAX_WORKSPACE_BYTES=200000000  # 200MB
MAX_ARCHIVE_BYTES=50000000    # 50MB
```

## 📦 Deploy

Consulte os arquivos:
- `DEPLOY.md` - Instruções detalhadas de deploy
- `HOSTING.md` - Opções de hospedagem
- `SQUARECLOUD.md` - Deploy no SquareCloud
- `Dockerfile` - Container Docker

## 🛠️ Desenvolvimento

### Testes
```bash
# Rodar testes
python -m pytest tests/

# Testes específicos
python test_clone.py      # Teste de clonagem
python test_integration.py # Testes de integração
```

### Estrutura de Mensagens
- Suporta **tool calls** com especificação OpenAI
- Streaming via SSE (Server-Sent Events)
- Markdown com syntax highlighting
- Suporta imagens no chat (modelos com visão)

## ✨ Recursos Implementados

- ✅ **Banner de perfil**: Exibe avatar e nome do usuário (via tooltip) acima da engrenagem de configurações
- ✅ **Git integrado**: Workspaces clonados automaticamente inicializam um repositório git local com `.git`
- ✅ **Shell seguro**: Execução de comandos shell com lista de executáveis permitidos (inclui git por padrão)
- ✅ **Clonagem de repositórios privados**: Funciona com GitHub OAuth conectado
- ✅ **Terminal widget**: Interface de terminal integrado no painel workspace
- ✅ **Botão Git Pull**: Sincronização fácil com o repositório remoto

## 📝 Roadmap

- [ ] Collapse de thinking
- [ ] Comandos personalizados
- [ ] Sessões com retomada
- [ ] Seletor de modelos
- [ ] MCP (Model Context Protocol)
- [ ] Subagentes
- [ ] Otimização de renderização

## 🐛 Problemas Conhecidos
- Mensagens de entrada não são enfileiradas (pode causar conflitos com conexões concorrentes)
