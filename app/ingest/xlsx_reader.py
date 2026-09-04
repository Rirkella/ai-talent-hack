"""Чтение `.xlsx` через `openpyxl`.

Нужен второму курсу («Продуктовая бизнес-модель», решения — таблицы) и
техническому заданию по QA. В основном сценарии MVP не участвует, но именно
он доказывает масштабируемость: добавление курса с другим форматом решений
не требует правок в ревью, рубрике или интерфейсе — достаточно нового
читателя за тем же интерфейсом `Document`.

Лист режется на блоки `§N` по строкам, а не превращается в один блок.
Это не косметика: на реальном решении по QA лист занимал 19 тысяч символов,
и весь документ становился единственным блоком. Проверка цитат ищет цитату
внутри указанного блока — в блоке такого размера она найдёт почти любую
строку, то есть перестаёт быть проверкой. Ревьюер при этом видел в
просмотрщике сплошную стену текста без навигации.

Шапка таблицы повторяется в каждом блоке: без неё фрагмент со строками
40-60 состоит из значений без имён колонок и нечитаем ни моделью, ни
человеком.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

from app.ingest.document import Block, BlockKind, DocMeta, Document, ImageRef

# Предохранители: экспорт из Google Sheets часто отдаёт лист на миллион строк,
# из которых заполнены полторы сотни.
MAX_ROWS = 400
MAX_COLS = 40

# Целевой объём блока в символах. Ориентир — абзац `.docx`: блоки разных
# форматов должны быть сопоставимы, иначе проверка цитат работает в таблицах
# и в тексте с разной строгостью.
TARGET_BLOCK_CHARS = 1500
# Ниже этого числа строк резать бессмысленно — получится крошево.
MIN_ROWS_PER_BLOCK = 4


def read_xlsx(path: str | Path) -> Document:
    path = Path(path)
    # data_only=True — вместо формул подставляются вычисленные значения.
    # Для критерия «расчёт ROI» важны именно числа; сами формулы читаются
    # отдельным проходом ниже, потому что их наличие — тоже сигнал.
    wb_values = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    wb_formulas = openpyxl.load_workbook(str(path), data_only=False, read_only=True)

    props = wb_values.properties
    meta = DocMeta(
        author=props.creator or None,
        last_modified_by=props.lastModifiedBy or None,
        created=props.created,
        modified=props.modified,
        revision=int(props.revision) if str(props.revision or "").isdigit() else None,
        application="Excel/openpyxl",
        has_core_xml=bool(props.creator or props.created),
        has_app_xml=False,
    )

    blocks: list[Block] = []
    warnings: list[str] = []
    images: list[ImageRef] = []
    formula_count = 0
    sheet_count = 0
    truncated: list[str] = []

    for name in wb_values.sheetnames:
        ws = wb_values[name]
        ws_f = wb_formulas[name]

        rows: list[list[str]] = []
        for r, row in enumerate(ws.iter_rows(values_only=True)):
            if r >= MAX_ROWS:
                truncated.append(name)
                break
            cells = ["" if c is None else str(c).strip() for c in row[:MAX_COLS]]
            if any(cells):
                rows.append(cells)

        for r, row in enumerate(ws_f.iter_rows(values_only=True)):
            if r >= MAX_ROWS:
                break
            formula_count += sum(1 for c in row[:MAX_COLS] if isinstance(c, str) and c.startswith("="))

        if not rows:
            continue

        # Хвостовые пустые столбцы обрезаем, иначе текст листа состоит из «|».
        width = max((len(r) - _trailing_empty(r)) for r in rows) or 1
        rows = [r[:width] + [""] * max(0, width - len(r)) for r in rows]

        sheet_count += 1
        # Первая строка считается шапкой только если за ней есть данные:
        # у листа из одной строки шапки нет, есть единственная строка.
        header = rows[0] if len(rows) > 1 else None

        for first, chunk in _chunks(rows, header):
            # Шапка приклеивается ко всем блокам, кроме первого — в нём она
            # уже есть как обычная строка.
            table = ([header] + chunk) if header and first > 1 else chunk
            where = (
                f"Лист «{name}», строки {first}-{first + len(chunk) - 1}"
                if len(chunk) < len(rows)
                else f"Лист «{name}»"
            )
            blocks.append(
                Block(
                    index=len(blocks) + 1,
                    kind=BlockKind.SHEET,
                    text=f"{where}\n" + "\n".join(" | ".join(r) for r in table),
                    style=name,
                    table=table,
                )
            )

    wb_values.close()
    wb_formulas.close()

    if truncated:
        warnings.append(
            f"Листы {', '.join(sorted(set(truncated)))} обрезаны до {MAX_ROWS} строк — "
            f"типично для экспорта из Google Sheets. В проверку попала верхняя часть."
        )
    if not blocks:
        warnings.append("В книге не найдено заполненных ячеек.")
    if not meta.is_available:
        warnings.append("Метаданные книги недоступны: автор и даты не определены.")

    return Document(
        source_name=path.name,
        source_format="xlsx",
        blocks=blocks,
        meta=meta,
        images=images,
        facts={
            "block_count": len(blocks),
            # Листов, а не блоков: один лист даёт несколько блоков, а
            # формальная проверка «есть ли таблицы» считает таблицы.
            "sheet_count": sheet_count,
            "table_count": sheet_count,
            "formula_count": formula_count,
            # Наличие формул отличает живой расчёт от вписанных руками чисел —
            # прямой сигнал для критерия «реалистичный расчёт ROI».
            "has_formulas": formula_count > 0,
            "image_count": 0,
            "has_images": False,
            "has_tracked_changes": False,
        },
        warnings=warnings,
    )


def _chunks(
    rows: list[list[str]], header: list[str] | None
) -> list[tuple[int, list[list[str]]]]:
    """Режет строки листа на блоки по объёму текста.

    Возвращает пары «номер первой строки — строки блока». Номер нужен, чтобы
    ревьюер нашёл фрагмент в исходной книге: ссылка `§7` без привязки к
    строкам таблицы ему ничего не говорит.
    """
    out: list[tuple[int, list[list[str]]]] = []
    current: list[list[str]] = []
    start = 1
    size = 0
    header_size = sum(len(c) for c in header) if header else 0

    for i, row in enumerate(rows, start=1):
        current.append(row)
        size += sum(len(c) for c in row) + len(row)
        # Условие только по объёму, без требования к числу строк. Требование
        # «не меньше четырёх строк» здесь было ошибкой: у листа с тест-кейсами
        # одна строка занимает две-три тысячи символов, четыре таких строки
        # давали блок на десять тысяч — то есть ровно ту стену, от которой
        # резка и спасает. Крошево возникает только в хвосте, и он склеивается
        # с предыдущим блоком отдельно.
        if size + header_size >= TARGET_BLOCK_CHARS:
            out.append((start, current))
            current, size, start = [], 0, i + 1

    if current:
        # Короткий хвост прилипает к предыдущему блоку: иначе последний блок
        # часто состоит из одной строки и в очереди цитат выглядит мусором.
        if out and len(current) < MIN_ROWS_PER_BLOCK:
            prev_start, prev = out[-1]
            out[-1] = (prev_start, prev + current)
        else:
            out.append((start, current))
    return out or [(1, rows)]


def _trailing_empty(row: list[str]) -> int:
    n = 0
    for cell in reversed(row):
        if cell:
            break
        n += 1
    return n
