<#
.SYNOPSIS
    Готовит локальный Ollama к работе Avito AI Reviewer.

.DESCRIPTION
    Единственное место в проекте, знающее про Ollama. Весь остальной код
    работает через OpenAI SDK против OpenAI-совместимого API, поэтому смена
    провайдера выполняется правкой .env, а не правкой кода.

    Скрипт создаёт производные модели из scripts/Modelfile.reviewer и
    scripts/Modelfile.reviewer-fast, после чего проверяет фактические параметры
    через /api/show.

    Зачем производная модель, а не базовая qwen3.5:9b:

      1. num_ctx. Умолчание Ollama — 4096 токенов, а OpenAI-совместимый /v1
         не принимает поле options, поэтому задать контекст из кода нельзя.
         На 4096 хвост работы студента молча теряется, и ревью оценивает
         обрезанный документ, ничего об этом не сообщая.

      2. Штрафы сэмплирования. Базовый Modelfile qwen3.5 приносит
         presence_penalty 1.5 и top_k 20. При temperature=0 выбор жадный, но
         штрафы всё равно правят логиты до argmax, а структурный JSON состоит
         из повторяющихся токенов (кавычки, скобки, имена полей). Это бьёт и по
         воспроизводимости, и по валидности ответа.

    ВАЖНО — рассуждения. qwen3.5 умеет рассуждать, и при включённых
    рассуждениях Ollama складывает весь вывод в поле thinking, оставляя content
    пустым; генерация при этом не останавливается и запрос уходит в таймаут.
    Отключается это не Modelfile-параметром, а полем запроса, поэтому живёт в
    .env как LLM_REASONING_EFFORT=none. Скрипт проверяет, что связка отвечает.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/ollama_setup.ps1
#>

[CmdletBinding()]
param(
    [string]$Model     = "avito-reviewer",
    [string]$FastModel = "avito-reviewer-fast",
    [int]$ExpectedCtx  = 32768,
    [string]$OllamaHost = "http://localhost:11434"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Write-Step($msg) { Write-Host "`n=== $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "  !   $msg" -ForegroundColor Yellow }

# ── 1. Наличие ollama ────────────────────────────────────────────────────────
Write-Step "Проверка ollama"
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $candidate) { $ollama = $candidate }
    else { throw "ollama не найден. Установите с https://ollama.com/download и повторите." }
} else { $ollama = $ollama.Source }
Write-Ok "$ollama ($(& $ollama --version 2>&1 | Select-Object -First 1))"

# ── 2. Сервер запущен ────────────────────────────────────────────────────────
Write-Step "Проверка сервера $OllamaHost"
$serverUp = $false
try {
    Invoke-RestMethod -Uri "$OllamaHost/api/tags" -TimeoutSec 5 | Out-Null
    $serverUp = $true
} catch { }

if (-not $serverUp) {
    Write-Warn2 "Сервер не отвечает — запускаю 'ollama serve' в фоне"
    Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 30 -and -not $serverUp; $i++) {
        Start-Sleep -Seconds 1
        try { Invoke-RestMethod -Uri "$OllamaHost/api/tags" -TimeoutSec 3 | Out-Null; $serverUp = $true } catch { }
    }
}
if (-not $serverUp) { throw "Сервер Ollama не поднялся за 30 с. Запустите 'ollama serve' вручную." }
Write-Ok "сервер отвечает"

# ── 3. Базовые модели на месте ───────────────────────────────────────────────
Write-Step "Проверка базовых моделей"
$tags = (Invoke-RestMethod -Uri "$OllamaHost/api/tags").models.name
foreach ($base in @("qwen3.5:9b", "qwen3.5:4b")) {
    if ($tags -contains $base) { Write-Ok "$base на месте" }
    else {
        Write-Warn2 "$base отсутствует — качаю (нужен интернет, это единственный сетевой шаг)"
        & $ollama pull $base
    }
}

# ── 4. Производные модели ────────────────────────────────────────────────────
Write-Step "Создание производных моделей"
foreach ($pair in @(@($Model, "Modelfile.reviewer"), @($FastModel, "Modelfile.reviewer-fast"))) {
    $name = $pair[0]; $file = Join-Path $PSScriptRoot $pair[1]
    if (-not (Test-Path $file)) { throw "Не найден $file" }
    & $ollama create $name -f $file | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "ollama create $name завершился с кодом $LASTEXITCODE" }
    Write-Ok "$name создана из $($pair[1])"
}

# ── 5. Ассерт фактических параметров ─────────────────────────────────────────
# Проверяется не то, что записано в .env, а то, с чем сервер реально
# загрузит модель. Иначе ошибка обнаружится только на демо.
Write-Step "Проверка фактических параметров через /api/show"
$show = Invoke-RestMethod -Uri "$OllamaHost/api/show" -Method Post `
        -Body (@{ model = $Model } | ConvertTo-Json) -ContentType "application/json"

$params = @{}
foreach ($line in ($show.parameters -split "`n")) {
    $parts = $line.Trim() -split "\s+", 2
    if ($parts.Count -eq 2) { $params[$parts[0]] = $parts[1].Trim() }
}

if (-not $params.ContainsKey("num_ctx")) {
    throw "num_ctx не задан у модели $Model — действует умолчание Ollama 4096, хвост работы будет обрезан."
}
$actualCtx = [int]$params["num_ctx"]
if ($actualCtx -lt $ExpectedCtx) {
    throw "num_ctx=$actualCtx, ожидалось не меньше $ExpectedCtx."
}
Write-Ok "num_ctx = $actualCtx"

foreach ($check in @(@("temperature","0"), @("presence_penalty","0"), @("repeat_penalty","1"))) {
    $k = $check[0]
    if (-not $params.ContainsKey($k)) { Write-Warn2 "$k не задан"; continue }
    if ([double]$params[$k] -ne [double]$check[1]) {
        throw "$k = $($params[$k]), ожидалось $($check[1]). Воспроизводимость ревью не гарантируется."
    }
    Write-Ok "$k = $($params[$k])"
}

# ── 6. Живая проверка: рассуждения выключены, content не пустой ──────────────
# Самая коварная поломка: при включённых рассуждениях модель пишет в поле
# thinking, content остаётся пустым, а запрос не завершается вовсе.
Write-Step "Живая проверка ответа модели"
$body = @{
    model    = $Model
    messages = @(@{ role = "user"; content = 'Верни JSON {"ok": true} и ничего больше.' })
    stream   = $false
    think    = $false
} | ConvertTo-Json -Depth 5

$sw = [Diagnostics.Stopwatch]::StartNew()
$resp = Invoke-RestMethod -Uri "$OllamaHost/api/chat" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 120
$sw.Stop()

if ([string]::IsNullOrWhiteSpace($resp.message.content)) {
    throw ("Модель вернула пустой content — вывод ушёл в поле thinking. " +
           "Убедитесь, что в .env задано LLM_REASONING_EFFORT=none.")
}
Write-Ok "ответ за $([math]::Round($sw.Elapsed.TotalSeconds,1)) с: $($resp.message.content.Trim())"

Write-Host "`nГотово. Модели '$Model' и '$FastModel' настроены." -ForegroundColor Green
Write-Host "Дальше: python -m pytest tests/test_smoke_llm.py -q" -ForegroundColor Gray
