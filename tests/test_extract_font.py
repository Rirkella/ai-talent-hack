"""Кегль и шрифт из условия: три формулировки вместо одной.

Правило проекта: любая регулярка по условию проверяется минимум на трёх
разных курсах. Проверка показала, что распознавалась ровно одна запись —
«Размер шрифта — 11 пунктов, Arial». Условие «Оценка затрат на реализацию»
из набора примеров пишет «11 кегль, Arial», и оно молча теряло и шрифт, и
кегль: обе проверки оформления вставали в «не задано», то есть требование
из условия просто не применялось.

Отрицательные случаи здесь не менее важны положительных. Запись «Размер
шрифта — 12 пунктов, межстрочный интервал — 1,15-1,5» имени шрифта не
содержит, и выдумывать его нельзя: подстановка соседнего слова дала бы
нарушение на ровном месте.
"""

from __future__ import annotations

import pytest

from app.rubric.extract import extract_deterministic

# (текст, шрифт текста, кегль текста, шрифт таблиц, кегль таблиц)
CASES: list[tuple[str, str | None, float | None, str | None, float | None]] = [
    # Продуктовый фрод — запись, которая распознавалась и раньше.
    (
        "Размер шрифта — 11 пунктов, Arial; для таблиц — 10 пунктов, Arial",
        "Arial", 11.0, "Arial", 10.0,
    ),
    # Дизайн A/B-теста: кегль есть, имени шрифта нет.
    ("Размер шрифта — 12 пунктов, межстрочный интервал — 1,15-1,5", None, None, None, None),
    # «Оценка затрат на реализацию» — размер идёт первым, слова «шрифт» нет.
    ("объём ~2–3 листа, 11 кегль, Arial", "Arial", 11.0, None, None),
    ("при выводе листа на печать — не более 2х страниц, 11 кегль, Arial",
     "Arial", 11.0, None, None),
    # Обратный порядок: сначала имя, потом размер.
    ("Шрифт Arial, 12 пт.", "Arial", 12.0, None, None),
    ("Шрифт: Times New Roman, 14 пт", "Times New Roman", 14.0, None, None),
    # Требования нет вовсе — ничего не извлекаем.
    ("Оформите работу аккуратно. Требований к шрифту нет.", None, None, None, None),
    ("Решение сдаётся в Markdown. Диаграммы — mermaid.", None, None, None, None),
]


@pytest.mark.parametrize(("text", "body_font", "body_size", "table_font", "table_size"), CASES)
def test_font_phrasings(
    text: str,
    body_font: str | None,
    body_size: float | None,
    table_font: str | None,
    table_size: float | None,
) -> None:
    out = extract_deterministic(text)
    assert out.get("body_font") == body_font
    assert out.get("body_size_pt") == body_size
    assert out.get("table_font") == table_font
    assert out.get("table_size_pt") == table_size


def test_font_name_is_never_invented() -> None:
    """Пустое имя шрифта не записывается: «не задано» лучше выдумки."""
    out = extract_deterministic("Кегль 12 пт, интервал одинарный.")
    assert "body_font" not in out or out["body_font"]
