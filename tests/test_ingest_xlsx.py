"""Тесты читателя `.xlsx`.

Главное здесь — размер блока. Блок `§N` это единица, на которую ссылается
модель и внутри которой код ищет цитату. Если лист целиком становится одним
блоком, проверка цитат находит почти любую строку и перестаёт быть проверкой,
а ревьюер видит в просмотрщике стену текста без навигации.

Случай не выдуман: решение по QA из набора примеров кейса разбиралось
в один блок на 19 тысяч символов.
"""

from __future__ import annotations

import openpyxl

from app.ingest.xlsx_reader import MAX_ROWS, TARGET_BLOCK_CHARS, read_xlsx


def _book(tmp_path, rows: list[list[str]], *, sheets: int = 1):
    wb = openpyxl.Workbook()
    for i in range(sheets):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = f"Лист{i + 1}"
        for row in rows:
            ws.append(row)
    path = tmp_path / "работа.xlsx"
    wb.save(path)
    return path


def test_long_sheet_is_split_into_several_blocks(tmp_path) -> None:
    """Длинный лист не должен превращаться в один блок."""
    rows = [["Функционал", "Шаги", "Результат"]]
    rows += [[f"экран {i}", "открыть форму и заполнить поля " * 8, "ок"] for i in range(40)]
    doc = read_xlsx(_book(tmp_path, rows))

    assert len(doc.blocks) > 1, "лист остался единственным блоком"
    biggest = max(len(b.text) for b in doc.blocks)
    assert biggest < 8000, f"блок на {biggest} символов слишком велик для проверки цитат"


def test_header_is_repeated_in_every_block(tmp_path) -> None:
    """Фрагмент без шапки нечитаем: остаются значения без имён колонок."""
    rows = [["Функционал", "Шаги", "Результат"]]
    rows += [[f"экран {i}", "длинное описание шагов " * 12, "ок"] for i in range(30)]
    doc = read_xlsx(_book(tmp_path, rows))

    assert len(doc.blocks) > 2
    for block in doc.blocks:
        assert "Функционал" in block.text, f"в блоке §{block.index} нет шапки"
        assert block.table and block.table[0][0] == "Функционал"


def test_block_says_which_rows_it_covers(tmp_path) -> None:
    """Ссылка «§7» бесполезна, если непонятно, какие это строки книги."""
    rows = [["Колонка"]] + [["значение " * 40] for _ in range(20)]
    doc = read_xlsx(_book(tmp_path, rows))

    assert len(doc.blocks) > 1
    assert "строки" in doc.blocks[1].text.splitlines()[0]


def test_single_row_sheet_has_no_phantom_header(tmp_path) -> None:
    """У листа из одной строки шапки нет — есть одна строка."""
    doc = read_xlsx(_book(tmp_path, [["единственная строка"]]))
    assert len(doc.blocks) == 1
    assert doc.blocks[0].text.count("единственная строка") == 1


def test_sheet_count_counts_sheets_not_blocks(tmp_path) -> None:
    """Формальная проверка «есть ли таблицы» считает таблицы, а не блоки.

    После резки листов на блоки счётчик, приравненный к числу блоков, начал
    бы утверждать, что в работе тридцать таблиц вместо двух.
    """
    rows = [["Колонка"]] + [["значение " * 40] for _ in range(20)]
    doc = read_xlsx(_book(tmp_path, rows, sheets=2))

    assert doc.facts["sheet_count"] == 2
    assert doc.facts["table_count"] == 2
    assert doc.facts["block_count"] == len(doc.blocks) > 2


def test_block_indexes_are_continuous_across_sheets(tmp_path) -> None:
    """Номера блоков должны идти подряд по всему документу, а не по листу."""
    rows = [["Колонка"]] + [["значение " * 40] for _ in range(12)]
    doc = read_xlsx(_book(tmp_path, rows, sheets=3))
    assert [b.index for b in doc.blocks] == list(range(1, len(doc.blocks) + 1))


def test_huge_sheet_is_truncated_with_a_warning(tmp_path) -> None:
    """Экспорт из Google Sheets отдаёт лист на миллион строк — с пометкой."""
    rows = [["Колонка"]] + [[f"строка {i}"] for i in range(MAX_ROWS + 50)]
    doc = read_xlsx(_book(tmp_path, rows))
    assert any("обрезан" in w for w in doc.warnings)


def test_target_size_is_a_paragraph_not_a_page() -> None:
    """Блок таблицы сопоставим с абзацем `.docx`, а не со страницей.

    Порог зафиксирован тестом намеренно: подняв его «чтобы было меньше
    блоков», легко вернуть исходную проблему.
    """
    assert 500 <= TARGET_BLOCK_CHARS <= 3000
