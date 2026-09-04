<#
.SYNOPSIS
    Выгружает примеры домашних работ из публичного репозитория кейса.

.DESCRIPTION
    Нужен интернет — это часть установки, а не рантайма.

    Загружаются не все 483 файла репозитория, а только каталоги выбранного
    сценария: product_fraud (основной) и product_business_models
    (демонстрация масштабируемости на другой формат решений).

    Две ловушки, из-за которых скрипт устроен именно так:

    1. Обычный `git clone` на Windows выгружает историю и падает на checkout:
       в репозитории есть пути, недопустимые для файловой системы Windows.
       Поэтому клонируется bare-копия — рабочее дерево не создаётся вовсе,
       и падать нечему.

    2. Файлы извлекаются перенаправлением через `cmd /c`, а не оператором `>`
       PowerShell. PowerShell пропускает поток через текстовый конвейер и
       перекодирует его, что необратимо ломает .docx и .pdf. Перенаправление
       cmd побайтовое.
#>

[CmdletBinding()]
param(
    [string]$Repo = "https://github.com/ai-talent-hub-avito/homework_examples.git",
    [string[]]$Folders = @("product_fraud", "product_business_models")
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$target = Join-Path $root "data\examples"
$temp = Join-Path $env:TEMP "avito_hw_examples.git"

function Write-Ok($m) { Write-Host "  OK  $m" -ForegroundColor Green }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git не найден. Установите git и повторите."
}

Write-Host "`n=== Выгрузка примеров работ" -ForegroundColor Cyan
Write-Host "Это шаг установки: нужен интернет. Рантайм работает офлайн." -ForegroundColor Gray

if (Test-Path $temp) { Remove-Item -Recurse -Force $temp }
git clone --bare --depth 1 $Repo $temp 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "не удалось выгрузить $Repo" }
Write-Ok "репозиторий получен (bare, без рабочего дерева)"

New-Item -ItemType Directory -Force -Path $target | Out-Null

# -z и разбор по NUL: имена файлов содержат пробелы и кириллицу, а обычный
# вывод ls-tree их экранирует и портит.
$raw = & git --git-dir=$temp ls-tree -r -z --name-only HEAD | Out-String
$files = $raw -split "`0" | Where-Object { $_ }

# Запятые внутри элементов разбираются здесь. Причина: при запуске через
# `powershell -File` аргументы приходят строками, и `-Folders a,b,c`
# привязывается к [string[]] как ОДИН элемент "a,b,c". Скрипт искал каталог
# с таким именем, не находил ничего и рапортовал об успехе. Теперь список
# нормализуется, а пустой результат считается ошибкой, а не успехом.
$wanted = $Folders |
    ForEach-Object { $_ -split "," } |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ }
if (-not $wanted) { throw "список каталогов пуст" }

$known = $files |
    Where-Object { $_ -match "/" } |
    ForEach-Object { ($_ -split "/")[0] } |
    Sort-Object -Unique

$total = 0
$skipped = 0
foreach ($folder in $wanted) {
    if ($known -notcontains $folder) {
        throw "в репозитории нет каталога '$folder'. Доступны: $($known -join ', ')"
    }
    $dest = Join-Path $target $folder
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $count = 0

    foreach ($f in $files) {
        if (-not $f.StartsWith("$folder/")) { continue }

        # Структура подкаталогов сохраняется. Раньше брался только `-Leaf`,
        # и файлы с одинаковыми именами в разных ДЗ затирали друг друга:
        # каталог product отдавал 23 файла, а на диск ложилось 11 — при
        # этом счётчик рапортовал 23. Молчаливая потеря данных.
        $rel = $f.Substring($folder.Length + 1)

        # В репозитории есть имена, недопустимые для файловой системы
        # Windows. Такой файл не роняет выгрузку: он пропускается с
        # предупреждением, иначе один плохой путь обесценивал бы весь шаг.
        $bad = $false
        $parts = $rel -split "/" | ForEach-Object {
            $seg = $_
            foreach ($ch in [System.IO.Path]::GetInvalidFileNameChars()) {
                if ($seg.IndexOf($ch) -ge 0) { $bad = $true }
            }
            $seg
        }
        if ($bad) {
            Write-Host "  --  пропущен (недопустимое имя): $f" -ForegroundColor DarkYellow
            $skipped++
            continue
        }

        $out = Join-Path $dest ($parts -join [System.IO.Path]::DirectorySeparatorChar)
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $out) | Out-Null

        # Побайтовое извлечение через cmd: оператор > в PowerShell
        # перекодирует поток и ломает бинарные .docx и .pdf.
        & cmd /c "git --git-dir=`"$temp`" cat-file blob `"HEAD:$f`" > `"$out`""
        if ($LASTEXITCODE -ne 0) { throw "не удалось извлечь $f" }
        $count++
    }

    if ($count -eq 0) { throw "каталог '$folder' не дал ни одного файла" }
    Write-Ok "$folder — файлов: $count"
    $total += $count
}

Remove-Item -Recurse -Force $temp

if ($skipped) {
    Write-Host "`nПропущено файлов с недопустимыми для Windows именами: $skipped" -ForegroundColor DarkYellow
}
Write-Host "`nГотово: $total файлов в data\examples\" -ForegroundColor Green
Write-Host "Каталог data/ в репозиторий не попадает (см. .gitignore)." -ForegroundColor Gray
