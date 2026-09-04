"""Чтение исходного кода и Jupyter-ноутбуков.

Нужно техническим заданиям кейса: разработка на Go, backend, DS. Условие у
них лежит в `.md`, а решение — репозиторий или ноутбук.

Два правила, общие с остальными читателями:

* **Блок `§N` соизмерим с абзацем.** Файл целиком одним блоком обесценил бы
  проверку цитат — в блоке на десять тысяч символов нечёткий поиск находит
  почти любую строку. Код режется по границам строк.
* **Ссылка должна быть находимой.** В каждом блоке указаны имя файла и
  диапазон строк: ревьюер по `§7` обязан открыть нужное место в редакторе,
  а не искать по всему проекту.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.ingest.document import Block, BlockKind, DocMeta, Document

# Ориентир тот же, что у таблиц: блок сопоставим с абзацем текста.
TARGET_BLOCK_CHARS = 1200
# Предохранитель от одного гигантского файла (сгенерированный код, дамп).
MAX_CHARS_PER_FILE = 120_000

# Расширение → язык. Список закрытый: читать наугад любой файл нельзя,
# иначе в проверку попадут лок-файлы и бинарники.
LANGUAGES: dict[str, str] = {
    ".go": "Go", ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".java": "Java", ".kt": "Kotlin", ".rs": "Rust",
    ".c": "C", ".cpp": "C++", ".cs": "C#", ".rb": "Ruby", ".php": "PHP",
    ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell",
    ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML", ".ini": "INI",
    ".mod": "Go module", ".gradle": "Gradle", ".proto": "Protobuf",
}


def _blocks_from_lines(lines: list[str], *, origin: str, first_index: int) -> list[Block]:
    """Режет строки на блоки по объёму, не разрывая строку."""
    blocks: list[Block] = []
    current: list[str] = []
    start_line = 1
    size = 0

    def flush(end_line: int) -> None:
        nonlocal current, size, start_line
        if not current:
            return
        header = f"{origin}, строки {start_line}-{end_line}"
        blocks.append(
            Block(
                index=first_index + len(blocks),
                kind=BlockKind.CODE,
                text=header + "\n" + "\n".join(current),
                style=origin,
            )
        )
        current, size, start_line = [], 0, end_line + 1

    for i, line in enumerate(lines, start=1):
        current.append(line)
        size += len(line) + 1
        if size >= TARGET_BLOCK_CHARS:
            flush(i)
    flush(len(lines))
    return blocks


def read_code(path: str | Path, *, first_index: int = 1, origin: str = "") -> Document:
    """Файл исходного кода как документ с блоками `§N`."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")[:MAX_CHARS_PER_FILE]
    lines = text.splitlines()
    language = LANGUAGES.get(path.suffix.lower(), "код")
    blocks = _blocks_from_lines(
        lines, origin=origin or f"файл {path.name}", first_index=first_index
    )

    return Document(
        source_name=path.name,
        source_format="code",
        blocks=blocks,
        meta=DocMeta(application=language),
        facts={
            "block_count": len(blocks),
            "line_count": len(lines),
            "language": language,
            "file_count": 1,
            "table_count": 0,
            "image_count": 0,
            "has_images": False,
            "has_tracked_changes": False,
        },
        warnings=(
            [f"Файл обрезан до {MAX_CHARS_PER_FILE} символов."]
            if len(text) >= MAX_CHARS_PER_FILE
            else []
        ),
    )


def read_notebook(path: str | Path, *, first_index: int = 1, origin: str = "") -> Document:
    """Jupyter-ноутбук: ячейки кода и текста подряд, с выводами.

    Вывод ячейки читается сокращённо: у обученной модели длинные трассы и
    таблицы результатов вытесняют из контекста сам код, а оценивают в
    ноутбуке прежде всего код и выводы автора.
    """
    path = Path(path)
    try:
        nb = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Ноутбук не разобран: {exc}") from exc

    blocks: list[Block] = []
    code_cells = md_cells = 0
    warnings: list[str] = []

    for n, cell in enumerate(nb.get("cells", []), start=1):
        source = "".join(cell.get("source", []) or []).strip()
        if not source:
            continue
        kind = cell.get("cell_type")
        where = f"{origin or 'ноутбук'}, ячейка {n}"

        if kind == "code":
            code_cells += 1
            pieces = [f"{where} (код)", source]
            out = _notebook_outputs(cell)
            if out:
                pieces.append("вывод:\n" + out)
            blocks.append(Block(index=first_index + len(blocks), kind=BlockKind.CODE,
                                text="\n".join(pieces), style="код"))
        elif kind == "markdown":
            md_cells += 1
            blocks.append(Block(index=first_index + len(blocks), kind=BlockKind.PARAGRAPH,
                                text=f"{where} (текст)\n{source}", style="markdown"))

    if not blocks:
        warnings.append("В ноутбуке нет заполненных ячеек.")

    return Document(
        source_name=path.name,
        source_format="ipynb",
        blocks=blocks,
        meta=DocMeta(application="Jupyter"),
        facts={
            "block_count": len(blocks),
            "code_cells": code_cells,
            "markdown_cells": md_cells,
            "language": (nb.get("metadata", {}).get("language_info", {}) or {}).get("name", "python"),
            "file_count": 1,
            "table_count": 0,
            "image_count": 0,
            "has_images": False,
            "has_tracked_changes": False,
        },
        warnings=warnings,
    )


def _notebook_outputs(cell: dict, limit: int = 600) -> str:
    """Текстовый вывод ячейки, сокращённо."""
    parts: list[str] = []
    for out in cell.get("outputs", []) or []:
        if text := out.get("text"):
            parts.append("".join(text))
        elif data := out.get("data"):
            if plain := data.get("text/plain"):
                parts.append("".join(plain))
        elif out.get("output_type") == "error":
            parts.append(f"{out.get('ename', 'Error')}: {out.get('evalue', '')}")
    joined = "\n".join(parts).strip()
    return joined[:limit] + ("…" if len(joined) > limit else "")
