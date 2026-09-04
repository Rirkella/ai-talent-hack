"""Чтение `.zip` — решение, состоящее из многих файлов.

Технические задания кейса (Go, backend) сдаются репозиторием: одно решение —
это два-три десятка файлов в каталогах. Один файл на работу здесь не годится,
а заводить по работе на каждый файл бессмысленно: критерии оценивают проект
целиком.

Архив разбирается в **один** `Document` со сквозной нумерацией блоков.
В каждом блоке указан путь файла и диапазон строк, поэтому ссылка `§7`
остаётся находимой: ревьюер открывает нужное место в проекте.

Что не читается и почему:

* каталоги сборки и зависимостей (`.git`, `node_modules`, `vendor`,
  `mlruns`) — это не работа студента, а её окружение;
* лок-файлы (`go.sum`, `package-lock.json`) — машинные списки хешей,
  которые вытеснили бы из контекста настоящий код;
* вложенные архивы — распаковка архива из архива открывает путь к zip-бомбе,
  а пользы для проверки ДЗ не даёт.
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from app.ingest.document import Block, DocMeta, Document

# Предохранители. Репозиторий с артефактами экспериментов (в примерах кейса
# такой есть — 366 файлов, из них 233 служебных) иначе утащит в проверку
# журналы запусков вместо кода.
MAX_FILES = 120
MAX_TOTAL_CHARS = 400_000
MAX_UNPACKED_BYTES = 80 * 1024 * 1024

SKIP_DIRS = {
    ".git", ".idea", ".vscode", "__pycache__", "node_modules", "vendor",
    "dist", "build", "target", ".venv", "venv", "mlruns", ".pytest_cache",
    ".mypy_cache", "site-packages",
}
SKIP_NAMES = {"go.sum", "package-lock.json", "yarn.lock", "poetry.lock", "pnpm-lock.yaml"}
SKIP_SUFFIXES = {".zip", ".tar", ".gz", ".rar", ".7z", ".exe", ".dll", ".so", ".bin"}

# Порядок обхода: сначала то, что описывает проект, потом код. Ревьюер и
# модель читают документ сверху вниз, и начинать с `README` полезнее, чем
# со случайного файла конфигурации.
PRIORITY = ("readme", "task", "main.go", "main.py", "docker-compose", "makefile")


def _is_interesting(name: str) -> bool:
    p = Path(name)
    if name.endswith("/"):
        return False
    if any(part in SKIP_DIRS for part in p.parts[:-1]):
        return False
    if p.name.lower() in SKIP_NAMES or p.suffix.lower() in SKIP_SUFFIXES:
        return False
    return True


def _sort_key(name: str) -> tuple[int, str]:
    low = Path(name).name.lower()
    for i, marker in enumerate(PRIORITY):
        if marker in low:
            return (i, name)
    return (len(PRIORITY), name)


def _common_root(names: list[str]) -> str:
    """Общая верхняя папка архива, если она одна.

    Выгрузка репозитория с GitHub кладёт всё в папку, имя которой содержит
    хеш коммита. В ссылке `§7` он не нужен: путь должен читаться как
    `internal/handlers/controller.go`. Пустая строка означает «обрезать
    нечего» — файлы лежат в корне или папок несколько.
    """
    tops = {n.split("/", 1)[0] for n in names if "/" in n}
    if len(tops) != 1 or any("/" not in n for n in names):
        return ""
    return next(iter(tops)) + "/"


def read_archive(path: str | Path) -> Document:
    """Разбирает `.zip` в один документ со сквозной нумерацией блоков."""
    # Импорт здесь: loader импортирует этот модуль, а мы — loader.
    from app.ingest.loader import READERS, UnsupportedFormat, read_any

    path = Path(path)
    blocks: list[Block] = []
    warnings: list[str] = []
    read_files: list[str] = []
    skipped_format: set[str] = set()
    total_chars = 0

    with zipfile.ZipFile(path) as zf:
        infos = [i for i in zf.infolist() if _is_interesting(i.filename)]
        unpacked = sum(i.file_size for i in infos)
        if unpacked > MAX_UNPACKED_BYTES:
            raise ValueError(
                f"Архив распаковывается в {unpacked // 1024 // 1024} МБ — "
                f"это не похоже на решение домашнего задания."
            )

        names = sorted((i.filename for i in infos), key=_sort_key)
        root_prefix = _common_root(names)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in names:
                if len(read_files) >= MAX_FILES or total_chars >= MAX_TOTAL_CHARS:
                    warnings.append(
                        f"В проверку попали первые {len(read_files)} файлов: "
                        f"дальше сработал предохранитель по объёму."
                    )
                    break

                suffix = Path(name).suffix.lower()
                if suffix not in READERS:
                    skipped_format.add(suffix or "(без расширения)")
                    continue

                shown = name[len(root_prefix) :] if root_prefix else name

                # Извлекаем по одному: путь внутри архива может быть
                # недопустимым для файловой системы Windows. Имя файла при
                # этом сохраняется настоящее — читатель кода подставляет его
                # в заголовок блока, и «файл item0.go» вместо «файл main.go»
                # сделал бы ссылку бесполезной.
                target = root / Path(shown).name
                if target.exists():
                    target = root / f"{len(read_files)}-{Path(shown).name}"
                try:
                    with zf.open(name) as src, target.open("wb") as dst:
                        dst.write(src.read())
                    doc = read_any(target)
                except (UnsupportedFormat, ValueError, OSError) as exc:
                    warnings.append(f"{name}: не прочитан ({type(exc).__name__}).")
                    continue

                for b in doc.blocks:
                    # Путь файла ставится в начало блока: без него цитата из
                    # `handlers/controller.go` неотличима от цитаты из
                    # `dto/courier.go`, и ссылка `§N` перестаёт быть адресом.
                    text = b.text if b.text.startswith(shown) else f"{shown}\n{b.text}"
                    blocks.append(
                        Block(index=len(blocks) + 1, kind=b.kind, text=text,
                              table=b.table, style=shown)
                    )
                    total_chars += len(text)
                read_files.append(shown)
                warnings.extend(doc.warnings)

    if not blocks:
        warnings.append("В архиве не найдено файлов поддерживаемых форматов.")
    if skipped_format:
        warnings.append(
            "Пропущены файлы форматов, которые система не читает: "
            + ", ".join(sorted(skipped_format)[:8])
        )

    lowered = [f.lower() for f in read_files]
    return Document(
        source_name=path.name,
        source_format="zip",
        blocks=blocks,
        meta=DocMeta(application="архив"),
        facts={
            "block_count": len(blocks),
            "file_count": len(read_files),
            "files": read_files[:MAX_FILES],
            # Признаки инженерной культуры: их спрашивают критерии технических
            # заданий, и считать их кодом надёжнее, чем спрашивать модель.
            "has_tests": any("_test." in f or "/tests/" in f or f.startswith("tests/")
                             for f in lowered),
            "has_readme": any("readme" in f for f in lowered),
            "has_dockerfile": any("dockerfile" in f or "docker-compose" in f for f in lowered),
            "has_ci": any(".github/workflows" in f or ".gitlab-ci" in f for f in lowered),
            "table_count": 0,
            "image_count": 0,
            "has_images": False,
            "has_tracked_changes": False,
        },
        warnings=warnings,
    )
