"""Тесты читателя Markdown — прежде всего диаграмм.

Решения технических курсов держат архитектурные схемы в блоках mermaid.
Блок mermaid содержит пустые строки, а читатель резал текст по пустой
строке: диаграмма разлеталась на четыре блока `§N`, и модель видела
обрывки `subgraph …` вместо схемы.

Цена дефекта измерена: работа с тремя диаграммами получала **ноль** за
критерий «Построена диаграмма C4». После починки — полный балл, а общая
оценка выросла с 4,75 до 5,75 из 6.
"""

from __future__ import annotations

import pytest

from app.ingest.document import BlockKind
from app.ingest.loader import read_text_file

DIAGRAM = """# Архитектура

Ниже контекстная диаграмма.

```mermaid
C4Context
    title Сервис — контекст

    Person(user, "Пользователь")

    System(app, "Сервис")
```

Дальше идёт описание интерфейсов.
"""


def _doc(tmp_path, text: str, name: str = "решение.md"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return read_text_file(path)


def test_diagram_stays_one_block(tmp_path) -> None:
    """Пустые строки внутри mermaid не должны рвать диаграмму."""
    doc = _doc(tmp_path, DIAGRAM)
    diagrams = [b for b in doc.blocks if b.style == "mermaid"]

    assert len(diagrams) == 1, "диаграмма разбита на несколько блоков"
    body = diagrams[0].text
    assert "C4Context" in body and "Person(user" in body and "System(app" in body


def test_diagram_kind_is_recognised_by_code(tmp_path) -> None:
    """Тип диаграммы определяет код, а не догадывается модель."""
    doc = _doc(tmp_path, DIAGRAM)
    assert doc.facts["diagram_count"] == 1
    assert doc.facts["diagram_kinds"] == ["C4 контекстная"]
    assert doc.blocks[[b.style for b in doc.blocks].index("mermaid")].text.startswith(
        "Диаграмма mermaid (C4 контекстная)"
    )


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("flowchart LR", "блок-схема"),
        ("sequenceDiagram", "последовательностей"),
        ("classDiagram", "классов"),
        ("C4Container", "C4 контейнеры"),
        ("erDiagram", "сущность-связь"),
    ],
)
def test_known_diagram_kinds(tmp_path, header: str, expected: str) -> None:
    doc = _doc(tmp_path, f"Текст.\n\n```mermaid\n{header}\n    A --> B\n```\n")
    assert doc.facts["diagram_kinds"] == [expected]


def test_code_block_is_not_counted_as_diagram(tmp_path) -> None:
    """JSON-контракт — это код, а не схема."""
    doc = _doc(tmp_path, 'Контракт.\n\n```json\n{\n  "id": 1\n}\n```\n')
    assert doc.facts["diagram_count"] == 0
    assert doc.facts["code_block_count"] == 1
    code = [b for b in doc.blocks if b.kind is BlockKind.CODE]
    assert len(code) == 1 and code[0].text.startswith("Код (json)")


def test_prose_around_fences_survives(tmp_path) -> None:
    """Текст до и после блока остаётся отдельными блоками."""
    doc = _doc(tmp_path, DIAGRAM)
    texts = [b.text for b in doc.blocks]
    assert any(t.startswith("# Архитектура") for t in texts)
    assert any("Ниже контекстная диаграмма" in t for t in texts)
    assert any("Дальше идёт описание интерфейсов" in t for t in texts)


def test_unclosed_fence_does_not_lose_text(tmp_path) -> None:
    """Незакрытый блок — обычная опечатка студента, а не повод потерять хвост."""
    doc = _doc(tmp_path, "Начало.\n\n```mermaid\nflowchart LR\n    A --> B\n")
    assert doc.facts["diagram_count"] == 1
    assert "A --> B" in "\n".join(b.text for b in doc.blocks)
