"""Единое представление разобранного документа.

Все читатели (`docx`, `pdf`, `xlsx`) сводятся к одному типу `Document`.
Ниже по течению — формальные проверки, LLM-ревью, детектор генИИ, просмотрщик
с подсветкой — работают только с ним и про исходный формат не знают.

Ключевая деталь — **блоки `§N`**. Документ режется на пронумерованные блоки,
и модель обязана ссылаться на номер блока в каждом доказательстве. Затем код
ищет процитированный фрагмент внутри указанного блока. Это главный
анти-галлюцинационный механизм проекта: непроверенная цитата не превращается
в балл, а поднимает приоритет ручной проверки.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST_ITEM = "list_item"
    IMAGE = "image"
    SHEET = "sheet"
    # Фрагмент исходного кода — технические задания: Go, backend, ноутбуки.
    CODE = "code"


@dataclass
class Block:
    """Пронумерованный фрагмент документа — единица цитирования.

    `text` для таблиц — построчная развёртка с разделителем `|`; исходная
    структура сохраняется в `table`, потому что «таблица заполнена корректно»
    проверяется по ячейкам, а не по склеенному тексту.
    """

    index: int  # номер N в §N, начиная с 1
    kind: BlockKind
    text: str
    style: str = ""
    # Структура таблицы: список строк, каждая — список ячеек.
    table: list[list[str]] | None = None
    # Технические факты параграфа, нужные формальному слою.
    fonts: set[str] = field(default_factory=set)
    sizes_pt: set[float] = field(default_factory=set)
    line_spacing: float | None = None
    page_hint: int | None = None

    @property
    def ref(self) -> str:
        return f"§{self.index}"

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass
class ImageRef:
    """Изображение внутри работы.

    Текстовая модель схемы не анализирует. Кейс требует честности, поэтому
    изображения не игнорируются молча — каждое попадает в отчёт с пометкой
    «изображение не оценено».
    """

    name: str
    size_bytes: int
    block_index: int | None = None


@dataclass
class DocMeta:
    """Метаданные файла.

    Каждое поле опционально осознанно: у экспорта из Google Docs
    `docProps/app.xml` отсутствует целиком. Отсутствие метаданных — это
    «недоступно», а не «ноль»; сигнал генИИ обязан деградировать явно.
    """

    author: str | None = None
    last_modified_by: str | None = None
    created: datetime | None = None
    modified: datetime | None = None
    # Суммарное время редактирования в минутах. Сильный сигнал: 1448 слов
    # за 4 минуты — вероятная вставка готового текста.
    total_time_min: int | None = None
    revision: int | None = None
    words: int | None = None
    pages: int | None = None
    application: str | None = None
    # Какие контейнеры метаданных вообще присутствовали в файле.
    has_app_xml: bool = False
    has_core_xml: bool = False

    @property
    def is_available(self) -> bool:
        return self.has_app_xml or self.has_core_xml


@dataclass
class Document:
    """Разобранный документ, готовый к проверке."""

    source_name: str
    source_format: str  # docx | pdf | xlsx | md | txt
    blocks: list[Block]
    meta: DocMeta = field(default_factory=DocMeta)
    images: list[ImageRef] = field(default_factory=list)
    # Факты, посчитанные читателем формата: шрифты, кегли, интервалы, страницы.
    # Формальный слой опирается на них и не разбирает файл повторно.
    facts: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    # ── текстовые представления ───────────────────────────────────────────

    @property
    def text(self) -> str:
        """Сплошной текст без разметки — для стилометрии и схожести."""
        return "\n\n".join(b.text for b in self.blocks if b.text.strip())

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def numbered_text(self, max_chars: int | None = None) -> str:
        """Текст с маркерами `§N` — ровно то, что видит модель.

        Модель обязана ссылаться на эти номера, поэтому нумерация в промпте
        и нумерация при проверке цитат берутся из одного источника.
        """
        parts: list[str] = []
        used = 0
        for b in self.blocks:
            if not b.text.strip():
                continue
            chunk = f"{b.ref} {b.text}"
            if max_chars is not None and used + len(chunk) > max_chars:
                parts.append(f"\n[…документ обрезан по лимиту {max_chars} символов…]")
                break
            parts.append(chunk)
            used += len(chunk)
        return "\n\n".join(parts)

    def block(self, index: int) -> Block | None:
        for b in self.blocks:
            if b.index == index:
                return b
        return None

    # ── агрегаты для формального слоя ─────────────────────────────────────

    @property
    def tables(self) -> list[Block]:
        return [b for b in self.blocks if b.kind is BlockKind.TABLE]

    def fonts_used(self, *, in_tables: bool | None = None) -> set[str]:
        """Шрифты текста и/или таблиц.

        `in_tables=None` — все; `True` — только таблицы; `False` — только вне
        таблиц. Разделение нужно потому, что условие задаёт разные требования:
        11 pt для текста и 10 pt для таблиц.
        """
        out: set[str] = set()
        for b in self.blocks:
            if in_tables is True and b.kind is not BlockKind.TABLE:
                continue
            if in_tables is False and b.kind is BlockKind.TABLE:
                continue
            out |= b.fonts
        return out

    def sizes_used(self, *, in_tables: bool | None = None) -> set[float]:
        out: set[float] = set()
        for b in self.blocks:
            if in_tables is True and b.kind is not BlockKind.TABLE:
                continue
            if in_tables is False and b.kind is BlockKind.TABLE:
                continue
            out |= b.sizes_pt
        return out
