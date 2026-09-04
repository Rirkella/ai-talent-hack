<#
.SYNOPSIS
    Однократная установка Avito AI Reviewer.

.DESCRIPTION
    ЕДИНСТВЕННЫЙ скрипт проекта, которому нужен интернет. Всё остальное —
    и в частности рантайм приложения — работает полностью офлайн:
    единственный внешний адрес это http://localhost:11434.

    Что делает:
      1. проверяет Python и ставит зависимости из requirements.txt;
      2. создаёт .env из .env.example;
      3. настраивает Ollama и производные модели (scripts/ollama_setup.ps1);
      4. ставит Node портативно, если его нет, и собирает интерфейс в web/dist;
      5. выгружает примеры работ, если их нет;
      6. наполняет базу демонстрационными данными;
      7. прогоняет тесты.

    Node нужен ТОЛЬКО здесь. На демонстрации он не требуется: web/dist собран
    и лежит в репозитории, а всё отдаёт один процесс uvicorn.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
#>

[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipFrontend,
    [switch]$SkipSeed,
    [switch]$SkipExamples
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Write-Step($m) { Write-Host "`n=== $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  OK  $m" -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "  !   $m" -ForegroundColor Yellow }

Write-Host "Avito AI Reviewer — установка" -ForegroundColor Cyan
Write-Host "Интернет нужен только этому скрипту." -ForegroundColor Gray

# ── 1. Python и зависимости ──────────────────────────────────────────────────
Write-Step "Python и зависимости"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "Python не найден. Нужен Python 3.13." }
$ver = & python -c "import sys; print('.'.join(map(str, sys.version_info[:2])))"
Write-Ok "Python $ver ($($py.Source))"

python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "установка зависимостей не удалась" }
Write-Ok "зависимости установлены"

# ── 2. Конфигурация ──────────────────────────────────────────────────────────
Write-Step "Конфигурация"
if (Test-Path ".env") {
    Write-Ok ".env уже существует — оставлен без изменений"
} else {
    Copy-Item ".env.example" ".env"
    Write-Ok ".env создан из .env.example"
}

# ── 3. Ollama и производные модели ───────────────────────────────────────────
Write-Step "Ollama и производные модели"
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "ollama_setup.ps1")
if ($LASTEXITCODE -ne 0) { throw "настройка Ollama не удалась" }

# ── 4. Интерфейс ─────────────────────────────────────────────────────────────
if ($SkipFrontend) {
    Write-Warn2 "сборка интерфейса пропущена"
} else {
    Write-Step "Сборка интерфейса"

    $nodeDir = Join-Path $env:LOCALAPPDATA "Programs\nodejs"
    if ((-not (Get-Command node -ErrorAction SilentlyContinue)) -and
        (Test-Path (Join-Path $nodeDir "node.exe"))) {
        $env:Path = "$env:Path;$nodeDir"
    }

    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        # Портативная установка из zip. MSI-установщик требует прав
        # администратора и на закрытом UAC-приглашении просто отменяется.
        Write-Warn2 "Node не найден — ставлю портативно, без прав администратора"
        $nodeVer = "v24.19.0"
        $zip = Join-Path $env:TEMP "node-$nodeVer-win-x64.zip"
        if (-not (Test-Path $zip)) {
            Invoke-WebRequest -Uri "https://nodejs.org/dist/$nodeVer/node-$nodeVer-win-x64.zip" `
                              -OutFile $zip
        }
        $programs = Join-Path $env:LOCALAPPDATA "Programs"
        Expand-Archive -Path $zip -DestinationPath $programs -Force
        if (Test-Path $nodeDir) { Remove-Item -Recurse -Force $nodeDir }
        Rename-Item (Join-Path $programs "node-$nodeVer-win-x64") $nodeDir

        $env:Path = "$env:Path;$nodeDir"
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($userPath -notlike "*$nodeDir*") {
            [Environment]::SetEnvironmentVariable("Path", "$userPath;$nodeDir", "User")
        }
        Write-Ok "Node установлен в $nodeDir и добавлен в PATH пользователя"
    }
    Write-Ok "node $(& node --version), npm $(& npm --version)"

    Push-Location web
    try {
        npm install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw "npm install завершился с ошибкой" }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build завершился с ошибкой" }
    } finally {
        Pop-Location
    }

    if (-not (Test-Path "web\dist\index.html")) {
        throw "сборка не создала web\dist\index.html"
    }
    Write-Ok "интерфейс собран в web\dist (коммитится в репозиторий)"
}

# ── 5. Примеры работ ─────────────────────────────────────────────────────────
if (-not $SkipExamples) {
    Write-Step "Примеры работ"
    if (Test-Path "data\examples\product_fraud") {
        $n = (Get-ChildItem "data\examples\product_fraud" -File).Count
        Write-Ok "примеры на месте (файлов: $n)"
    } else {
        & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "fetch_examples.ps1")
        if ($LASTEXITCODE -ne 0) { Write-Warn2 "не удалось выгрузить примеры" }
    }
}

# ── 6. Демонстрационные данные ───────────────────────────────────────────────
if (-not $SkipSeed) {
    Write-Step "Демонстрационные данные"
    python -m app.seed --reset
    if ($LASTEXITCODE -ne 0) { Write-Warn2 "наполнение базы завершилось с ошибкой" }
}

# ── 7. Тесты ─────────────────────────────────────────────────────────────────
if (-not $SkipTests) {
    Write-Step "Тесты"
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) { Write-Warn2 "часть тестов не прошла — см. вывод выше" }
    else { Write-Ok "все тесты пройдены" }
}

Write-Host "`nУстановка завершена." -ForegroundColor Green
Write-Host "Запуск:  powershell -File scripts\run.ps1" -ForegroundColor Gray
Write-Host "С этого момента интернет не нужен — приложение работает офлайн." -ForegroundColor Gray
