"""Единая точка входа разбора файлов.

Точка расширения для новых форматов: чтобы добавить формат, достаточно
написать читателя, возвращающего `Document`, и зарегистрировать его в
`READERS`. Ни ревью, ни рубрика, ни интерфейс об этом не узнают.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path

from app.ingest.code_reader import LANGUAGES, read_code, read_notebook
from app.ingest.document import Block, BlockKind, Document
from app.ingest.docx_reader import read_docx
from app.ingest.pdf_reader import read_pdf
from app.ingest.xlsx_reader import read_xlsx


class UnsupportedFormat(ValueError):
    """Расширение файла не обслуживается ни одним читателем."""


# Типы диаграмм mermaid. Нужны, чтобы факт «в работе есть диаграмма C4»
# устанавливал код, а не угадывала модель по обрывкам текста.
_MERMAID_KINDS: dict[str, str] = {
    "c4context": "C4 контекстная",
    "c4container": "C4 контейнеры",
    "c4component": "C4 компоненты",
    "c4dynamic": "C4 динамическая",
    "flowchart": "блок-схема",
    "graph": "блок-схема",
    "sequencediagram": "последовательностей",
    "classdiagram": "классов",
    "erdiagram": "сущность-связь",
    "statediagram": "состояний",
    "gantt": "диаграмма Ганта",
    "journey": "путь пользователя",
}

_FENCE_RE = re.compile(r"^```([\w+-]*)[ \t]*$", re.M)


def _mermaid_kind(body: str) -> str:
    """Тип диаграммы по первой значащей строке mermaid-блока."""
    for line in body.splitlines():
        token = line.strip().split()[0].lower() if line.strip() else ""
        token = token.rstrip(":;")
        if token in _MERMAID_KINDS:
            return _MERMAID_KINDS[token]
        if token:
            return "диаграмма"
    return "диаграмма"


def read_text_file(path: str | Path) -> Document:
    """Простой текст и Markdown — условия и решения технических курсов.

    Блок в тройных кавычках остаётся **одним** блоком `§N`. Это не косметика:
    решения по системному дизайну содержат диаграммы mermaid, внутри которых
    есть пустые строки. Разбиение по пустой строке рвало диаграмму на четыре
    куска, и модель видела вместо схемы обрывки `subgraph …`. Работа с тремя
    диаграммами C4 получала ноль за критерий «Построена диаграмма C4» —
    проверено на реальном решении из набора примеров кейса.

    Тип диаграммы определяется кодом и пишется в начало блока: модель не
    должна догадываться, что `C4Context` — это контекстная диаграмма C4.
    """
    path = Path(path)
    raw = path.read_text(encoding="utf-8", errors="replace")

    blocks: list[Block] = []
    diagrams: list[str] = []
    code_blocks = 0

    def add(text: str, kind: BlockKind, style: str = "") -> None:
        if text.strip():
            blocks.append(
                Block(index=len(blocks) + 1, kind=kind, text=text.strip(), style=style)
            )

    def add_prose(text: str) -> None:
        for chunk in text.split("\n\n"):
            chunk = chunk.strip()
            if chunk:
                add(chunk, BlockKind.HEADING if chunk.startswith("#") else BlockKind.PARAGRAPH)

    pos = 0
    while (m := _FENCE_RE.search(raw, pos)) is not None:
        add_prose(raw[pos : m.start()])
        lang = (m.group(1) or "").lower()
        close = _FENCE_RE.search(raw, m.end())
        end = close.start() if close else len(raw)
        body = raw[m.end() : end].strip("\n")
        code_blocks += 1

        if lang == "mermaid":
            kind = _mermaid_kind(body)
            diagrams.append(kind)
            # Подпись перед кодом: она попадает и в цитату, и в подсветку.
            add(f"Диаграмма mermaid ({kind}):\n{body}", BlockKind.CODE, style="mermaid")
        else:
            add(f"Код ({lang or 'без языка'}):\n{body}", BlockKind.CODE, style=lang or "код")
        pos = close.end() if close else len(raw)

    add_prose(raw[pos:])

    return Document(
        source_name=path.name,
        source_format=path.suffix.lstrip(".").lower() or "txt",
        blocks=blocks,
        facts={
            "block_count": len(blocks),
            "table_count": 0,
            "image_count": 0,
            "has_images": False,
            "has_tracked_changes": False,
            # Диаграммы mermaid текстовые, и в отличие от картинок их
            # содержимое модели доступно. Считаем отдельно от изображений.
            "diagram_count": len(diagrams),
            "diagram_kinds": sorted(set(diagrams)),
            "code_block_count": code_blocks,
        },
    )


def _read_archive(path: str | Path) -> Document:
    """Ленивый импорт: `archive_reader` импортирует этот модуль в ответ."""
    from app.ingest.archive_reader import read_archive

    return read_archive(path)


READERS: dict[str, Callable[[str | Path], Document]] = {
    ".docx": read_docx,
    ".pdf": read_pdf,
    ".xlsx": read_xlsx,
    ".xlsm": read_xlsx,
    ".md": read_text_file,
    ".txt": read_text_file,
    # Технические задания: решение — репозиторий или ноутбук.
    ".ipynb": read_notebook,
    ".zip": _read_archive,
    **{ext: read_code for ext in LANGUAGES},
}

SUPPORTED = sorted(READERS)


def read_any(path: str | Path) -> Document:
    """Разбирает файл подходящим читателем."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        # Пустое расширение — это не «неизвестный формат», а отсутствующий
        # путь. Общее сообщение со списком 31 расширения тут только сбивает
        # с толку: искать надо не формат, а файл.
        if not path.suffix:
            raise UnsupportedFormat(
                f"У файла нет расширения, определить формат нечем: {path}"
            )
        raise UnsupportedFormat(
            f"Формат {path.suffix!r} не поддерживается. Доступны: {', '.join(SUPPORTED)}"
        )
    doc = reader(path)
    doc.facts.setdefault("sha256", file_sha256(path))
    doc.facts.setdefault("size_bytes", path.stat().st_size)
    return doc


def file_sha256(path: str | Path) -> str:
    """Хеш файла — ключ кэша рубрики и признак повторной загрузки той же работы."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()
