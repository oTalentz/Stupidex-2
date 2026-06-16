# Script de Inicialização Stupidex
# Execute com: .\start.ps1

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Stupidex - Setup e Inicialização" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Verifica Python
Write-Host "[1/4] Verificando Python..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "✓ $pythonVersion encontrado" -ForegroundColor Green
} catch {
    Write-Host "✗ Python não encontrado. Instale Python 3.11+" -ForegroundColor Red
    exit 1
}

# Verifica/Cria ambiente virtual
Write-Host "`n[2/4] Configurando ambiente virtual..." -ForegroundColor Yellow
if (!(Test-Path ".venv")) {
    Write-Host "  Criando .venv..." -ForegroundColor Gray
    python -m venv .venv
    Write-Host "✓ Ambiente virtual criado" -ForegroundColor Green
} else {
    Write-Host "✓ Ambiente virtual já existe" -ForegroundColor Green
}

# Ativa ambiente
Write-Host "`n[3/4] Ativando ambiente e instalando dependências..." -ForegroundColor Yellow
& ".venv\Scripts\Activate.ps1"

# Instala dependências
Write-Host "  Instalando/Atualizando pacotes..." -ForegroundColor Gray
python -m pip install --upgrade pip --quiet
pip install -e . --quiet

Write-Host "✓ Dependências instaladas" -ForegroundColor Green

# Verifica .env
Write-Host "`n[4/4] Verificando configuração..." -ForegroundColor Yellow
if (Test-Path ".env") {
    Write-Host "✓ Arquivo .env configurado" -ForegroundColor Green
    Write-Host "  Usando NVIDIA NIM com DeepSeek V4 Pro" -ForegroundColor Gray
} else {
    Write-Host "✗ Arquivo .env não encontrado!" -ForegroundColor Red
    exit 1
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Iniciando Stupidex..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Servidor será aberto em: http://localhost:5000" -ForegroundColor Green
Write-Host "Pressione Ctrl+C para parar o servidor" -ForegroundColor Yellow
Write-Host ""

# Inicia servidor
python launcher.py
