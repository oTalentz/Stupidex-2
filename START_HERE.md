# 🎯 COMECE AQUI - Stupidex

## 📋 Checklist de Primeiros Passos

Siga esta lista na ordem:

### ✅ 1. Pré-requisitos

- [ ] Python 3.11+ instalado
  - Verifique: `python --version`
  - Download: https://www.python.org/downloads/
  - ⚠️ Marque "Add Python to PATH" na instalação

- [ ] Git instalado (opcional, mas recomendado)
  - Verifique: `git --version`
  - Download: https://git-scm.com/

- [ ] PowerShell funcionando
  - Já vem com Windows

---

### ✅ 2. Configuração Inicial

Abra o PowerShell nesta pasta e execute:

```powershell
# Se houver erro de ExecutionPolicy:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Testar API (opcional mas recomendado)
python test_api.py
```

**Resultado esperado:** Mensagem "✅ SUCESSO! A API está funcionando corretamente!"

---

### ✅ 3. Iniciar o Stupidex

```powershell
.\start.ps1
```

**O que acontece:**
1. ✓ Cria ambiente virtual (.venv)
2. ✓ Instala dependências
3. ✓ Inicia servidor Flask
4. ✓ Abre navegador automaticamente

**URL:** http://localhost:5000

---

### ✅ 4. Primeiro Uso

1. **Criar conta:**
   - Clique em "Criar conta"
   - Digite username e senha
   - Faça login

2. **Criar workspace:**
   - Clique em "Novo Workspace"
   - Opção 1: Upload de arquivos
   - Opção 2: Clonar repositório Git

3. **Conversar com a IA:**
   - Digite sua pergunta no chat
   - O modelo usado: **DeepSeek V4 Pro** (via NVIDIA NIM)
   - Suporta comandos, edição de código, git, shell, etc.

---

## 🚨 Problemas Comuns

### ❌ "Python não encontrado"
**Solução:** Instale Python 3.11+ e reinicie o PowerShell
```powershell
# Após instalar, teste:
python --version
```

### ❌ "Execution Policy Error"
**Solução:**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### ❌ "Port 5000 already in use"
**Solução:** Outra aplicação está usando a porta. Duas opções:

1. Feche a aplicação que usa porta 5000
2. Ou mude a porta no `.env`:
   ```
   PORT=5001
   ```

### ❌ "Module not found"
**Solução:** Reinstale as dependências:
```powershell
.venv\Scripts\activate
pip install -e .
```

### ❌ "API Error" / "Connection failed"
**Solução:**
- Verifique sua conexão com internet
- Execute: `python test_api.py` para diagnóstico
- A chave da API está embutida, mas pode ter expirado

---

## 📖 Próximas Leituras

Após iniciar com sucesso:

1. **[QUICKSTART.md](QUICKSTART.md)** - Guia rápido de uso
2. **[CHANGES.md](CHANGES.md)** - O que foi configurado
3. **[README.md](README.md)** - Documentação completa
4. **Code Issues Panel** - Problemas de segurança identificados

---

## 🎮 Comandos Úteis

### Parar o servidor:
```
Ctrl + C
```

### Reiniciar:
```powershell
.\start.ps1
```

### Modo terminal (TUI):
```powershell
.venv\Scripts\activate
stupidex
```

### Rodar testes:
```powershell
.venv\Scripts\activate
python -m pytest tests/
```

### Ver logs detalhados:
```powershell
$env:STUPIDEX_LOG_LEVEL="debug"
.\start.ps1
```

---

## 🆘 Precisa de Ajuda?

1. **Verifique logs** no terminal onde executou `start.ps1`
2. **Consulte documentação** em `docs/`
3. **Revise Code Issues Panel** para problemas conhecidos
4. **Teste API** com `python test_api.py`

---

## ✨ Tudo Funcionando?

Se você vê a interface web em http://localhost:5000:

🎉 **PARABÉNS!** O Stupidex está funcionando!

Próximos passos:
- Explore os modos: Chat, Ask, Plan, Agent, Review, Debug
- Teste as ferramentas: git, shell, edit_file, etc
- Clone um repositório e peça para a IA analisá-lo
- Configure OAuth (Google/GitHub) se necessário

**Divirta-se codando com IA!** 🚀
