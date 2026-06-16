# ⚠️ PYTHON NÃO ENCONTRADO

## 📥 Passo 1: Instalar Python

Você precisa instalar o Python primeiro:

### Opção A: Microsoft Store (Recomendado)
1. Pressione `Windows + S`
2. Digite "Microsoft Store"
3. Procure por "Python 3.12" ou "Python 3.11"
4. Clique em "Obter" / "Instalar"
5. Aguarde a instalação
6. Reinicie o PowerShell

### Opção B: Site Oficial
1. Acesse: https://www.python.org/downloads/
2. Clique em "Download Python 3.12.x"
3. Execute o instalador
4. **IMPORTANTE:** Marque "Add Python to PATH"
5. Clique em "Install Now"
6. Aguarde e reinicie o PowerShell

---

## ✅ Passo 2: Verificar Instalação

Abra um NOVO PowerShell e execute:

```powershell
python --version
```

Deve exibir algo como: `Python 3.12.x`

---

## 🚀 Passo 3: Executar o Stupidex

Depois de instalar o Python, execute:

```powershell
# Navegar até a pasta do projeto
cd "c:\Users\leona\Downloads\Stupidex"

# Executar script de instalação
.\start.ps1
```

---

## 📝 O Que o Script Faz

1. ✓ Cria ambiente virtual `.venv`
2. ✓ Instala dependências automaticamente
3. ✓ Inicia o servidor
4. ✓ Abre o navegador

---

## 🆘 Ainda com Problemas?

Se após instalar o Python ainda aparecer erro "Python not found":

1. **Feche TODOS os PowerShell/CMD abertos**
2. **Abra um NOVO PowerShell**
3. Teste: `python --version`
4. Execute: `.\start.ps1`

### Alternativa: Usar py launcher
```powershell
py --version
py test_api.py
```

---

## ⏭️ Próximos Comandos

Após instalar Python, execute UM POR VEZ:

```powershell
# 1. Criar ambiente virtual
python -m venv .venv

# 2. Ativar ambiente
.\.venv\Scripts\Activate.ps1

# 3. Instalar dependências
pip install -e .

# 4. Testar API
python test_api.py

# 5. Iniciar servidor
python launcher.py
```

O navegador abrirá em: http://localhost:5000

---

## 🎯 Status Atual

- ❌ Python não instalado ou não está no PATH
- ⏳ Aguardando instalação do Python
- ✅ Projeto configurado e pronto para iniciar

**Instale o Python e volte aqui!** 🚀
