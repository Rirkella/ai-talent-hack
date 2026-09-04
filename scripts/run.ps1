<#
.SYNOPSIS
    Запуск Avito AI Reviewer.

.DESCRIPTION
    Один процесс uvicorn обслуживает API, SSE и собранный интерфейс.
    Node на демонстрации не нужен: web/dist собран заранее и лежит в репозитории.

    Скрипт проверяет предпосылки до старта, чтобы поломка обнаружилась
    здесь, а не на защите: наличие .env, собранного фронта, базы и
    отвечающей модели.
#>

[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$Reseed
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Write-Ok($m)   { Write-Host "  OK  $m" -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "  !   $m" -ForegroundColor Yellow }

Write-Host "`n=== Avito AI Reviewer" -ForegroundColor Cyan

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Warn2 ".env создан из .env.example"
}

if (-not (Test-Path "web/dist/index.html")) {
    throw "Интерфейс не собран: нет web/dist/index.html. Выполните scripts/setup.ps1."
}
Write-Ok "интерфейс собран"

# Ollama должен отвечать: без модели ревью не выполнится.
try {
    Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 5 | Out-Null
    Write-Ok "Ollama отвечает"
} catch {
    Write-Warn2 "Ollama не отвечает — запускаю в фоне"
    $ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    if (-not $ollama) { $ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe" }
    Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 4
}

if ($Reseed) {
    Write-Host "`n=== Пересоздание демо-данных" -ForegroundColor Cyan
    python -m app.seed --reset
}
elseif (-not (Test-Path "data/app.db")) {
    Write-Host "`n=== Первичное наполнение демо-данными" -ForegroundColor Cyan
    python -m app.seed
}

$url = "http://127.0.0.1:8000"
Write-Host "`nИнтерфейс: $url" -ForegroundColor Green
Write-Host "Откройте три вкладки и войдите методистом, ревьюером и студентом." -ForegroundColor Gray
Write-Host "Остановка: Ctrl+C`n" -ForegroundColor Gray

if (-not $NoBrowser) { Start-Process $url }

# Тот же предел ожидания, что и в app.main.run(): без него Ctrl+C не
# останавливает процесс, пока открыта вкладка с живой подпиской SSE.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 5
