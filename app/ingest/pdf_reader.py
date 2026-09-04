"""Чтение `.pdf` через `pypdf`.

Используется прежде всего для условий заданий: условие выбранного ДЗ —
это печать страницы Stepik в PDF. Работы студентов тоже допускаются в PDF,
поэтому читатель общий.

Отдельные библиотеки OCR не нужны: в условии присутствуют шрифты с `ToUnicode`,
текст извлекается штатно. Если текстовый слой всё же пуст, читатель сообщает
об этом предупреждением, а не возвращает пустой документ молча.
"""

from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

from app.ingest.document import Block, BlockKind, DocMeta, Document, ImageRef

# Заголовок: короткая строка без завершающей точки, часто с номером пункта.
_HEADING = re.compile(r"^\s*(?:\d+[.)]\s+)?[^.!?]{3,80}$")
_LIST = re.compile(r"^\s*(?:[-–—•*]|\d+[.)])\s+")


def _looks_like_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 90 or s.endswith((".", "!", "?", ";", ":")):
        return False
    # Заголовок обычно не заканчивается запятой и не начинается со строчной.
    return bool(_HEADING.match(s)) and (s[0].isupper() or s[0].isdigit())


def read_pdf(path: str | Path) -> Document:
    path = Path(path)
    reader = PdfReader(str(path))
    warnings: list[str] = []

    info = reader.metadata or {}
    meta = DocMeta(
        author=_clean(info.get("/Author")),
        application=_clean(info.get("/Producer")) or _clean(info.get("/Creator")),
        has_core_xml=bool(info),
        pages=len(reader.pages),
    )

    images: list[ImageRef] = []
    blocks: list[Block] = []
    idx = 0
    empty_pages = 0

    for page_no, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if not text.strip():
            empty_pages += 1

        # Склеенные переносом слова: «риск-\nменеджмент» → «риск-менеджмент».
        text = re.sub(r"-\n(?=[а-яёa-z])", "", text)

        for raw in text.split("\n"):
            # Печать из Stepik (Producer = Skia/PDF) расставляет двойные пробелы
            # между словами и пробел перед знаками препинания. Без нормализации
            # нечёткий поиск цитат ищет строку, которой в работе нет, а рубрика
            # извлекается с рваным текстом.
            line = re.sub(r"\s+", " ", raw).strip()
            line = re.sub(r"\s+([,.;:!?)])", r"\1", line)
            line = re.sub(r"([(])\s+", r"\1", line)
            if not line:
                continue
            idx += 1
            kind = (
                BlockKind.HEADING
                if _looks_like_heading(line)
                else BlockKind.LIST_ITEM
                if _LIST.match(line)
                else BlockKind.PARAGRAPH
            )
            blocks.append(Block(index=idx, kind=kind, text=line, page_hint=page_no))

        try:
            for img in page.images:
                images.append(
                    ImageRef(name=img.name or f"page{page_no}", size_bytes=len(img.data))
                )
        except Exception:  # noqa: BLE001 — битые XObject не должны ронять разбор
            warnings.append(f"Страница {page_no}: изображения не перечислены.")

    if empty_pages:
        warnings.append(
            f"{empty_pages} из {len(reader.pages)} страниц без текстового слоя — "
            f"возможен скан. Текст с этих страниц в проверку не попал."
        )
    if images:
        warnings.append(
            f"В документе {len(images)} изображени(е/я) — текстовой моделью не анализируются."
        )

    return Document(
        source_name=path.name,
        source_format="pdf",
        blocks=blocks,
        meta=meta,
        images=images,
        facts={
            "block_count": len(blocks),
            "page_count": len(reader.pages),
            "image_count": len(images),
            "has_images": bool(images),
            "empty_pages": empty_pages,
            "table_count": 0,
            "has_tracked_changes": False,
        },
        warnings=warnings,
    )


def _clean(v: object) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None
