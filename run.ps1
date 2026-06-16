# ========================================
# STUPIDEX - Iniciando Servidor
# ========================================

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  STUPIDEX - DeepSeek V4 Pro" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[INFO] Configuracao aplicada:" -ForegroundColor Yellow
Write-Host "  - Modelo: deepseek-ai/deepseek-v4-pro" -ForegroundColor Gray
Write-Host "  - Provider: NVIDIA NIM" -ForegroundColor Gray
Write-Host "  - Reasoning Effort: HIGH" -ForegroundColor Gray
Write-Host "  - Temperature: 1.0" -ForegroundColor Gray
Write-Host "  - Max Tokens: 8192" -ForegroundColor Gray
Write-Host ""

Write-Host "[*] Iniciando servidor..." -ForegroundColor Yellow
Write-Host ""

& ".venv\Scripts\python.exe" launcher.py
