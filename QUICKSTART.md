# 🚀 Início Rápido - Stupidex

## ⚡ Começar em 2 Passos

### 1️⃣ Teste a API (Opcional)

```powershell
python test_api.py
```

Este teste verifica se a conexão com a NVIDIA API está funcionando.

### 2️⃣ Execute o Script

```powershell
.\start.ps1
```

### 3️⃣ Acesse o Navegador

O navegador abrirá automaticamente em: **http://localhost:5000**

---

## 🤖 Modelo de IA

O projeto já vem configurado com:
- **DeepSeek V4 Pro** via NVIDIA NIM
- Chave da API já embutida no código
- Pronto para uso imediatamente!

---

## 🔧 Problemas Comuns

### ❌ "Python não encontrado"
- Instale Python 3.11+: https://www.python.org/downloads/
- Marque a opção "Add Python to PATH" durante instalação

### ❌ "Execution Policy Error" ao executar .ps1
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### ❌ "Port already in use"
- Outro processo está usando a porta 5000
- Mude no `.env`: adicione `PORT=5001`

### ❌ "API Key inválida"
- O projeto já vem com a chave da NVIDIA configurada
- Se houver problemas, verifique a conexão com a internet

---

## 📝 Comandos Úteis

### Interface Terminal (TUI)
```bash
.venv\Scripts\activate
stupidex
```

### Interface Web
```bash
.venv\Scripts\activate
stupidex-web
```

### Executar com Python
```bash
.venv\Scripts\activate
python launcher.py
```

---

## 🔒 Segurança

- ✅ Chaves API são criptografadas
- ✅ Shell está habilitado mas restrito ao workspace
- ✅ Para desabilitar shell: `STUPIDEX_ENABLE_SHELL=0` no `.env`

---

## 📖 Documentação Completa

- [README.md](README.md) - Documentação completa
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - Arquitetura
- [docs/DEPLOY.md](docs/DEPLOY.md) - Deploy em produção
- [docs/SECURITY.md](docs/SECURITY.md) - Segurança

---

## 💬 Suporte

Se encontrar problemas:
1. Verifique os logs no terminal
2. Consulte a documentação em `docs/`
3. Verifique o painel Code Issues para problemas de código
