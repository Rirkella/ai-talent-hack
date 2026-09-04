"""Тесты формального слоя на трёх реальных работах.

Смысл набора — доказать, что слой даёт различающий сигнал, а не украшение:
три работы обязаны получить три разных набора нарушений. Отдельно
проверяется, что неопределённость остаётся неопределённостью и не
превращается в нарушение.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import PROJECT_ROOT
from app.formal.checks import FormalRequirements, Status, run_checks
from app.ingest.loader import read_any

EXAMPLES = PROJECT_ROOT / "data" / "examples" / "product_fraud"
WEAK = EXAMPLES / "Product_Fraud_ДЗ2_Решение слабое.docx"
MEDIUM = EXAMPLES / "Product_Fraud_ДЗ2_Решение среднее.docx"
GOOD = EXAMPLES / "Product_Fraud_ДЗ2_Решение хорошее.docx"

pytestmark = pytest.mark.skipif(
    not WEAK.exists(), reason="нет примеров работ: выполните scripts/fetch_examples.ps1"
)


# Требования ДЗ «Карта рисков продукта» — те самые, что извлекаются из его
# условия. Раньше они были значениями по умолчанию в `FormalRequirements`,
# и тесты этим пользовались молча. Теперь требование задаётся явно: значения
# по умолчанию убраны как раз потому, что чужой курс получал чужие пороги.
FRAUD = FormalRequirements(
    max_pages=3,
    body_font="Arial",
    body_size_pt=11.0,
    table_font="Arial",
    table_size_pt=10.0,
    line_spacing_min=1.15,
    line_spacing_max=1.5,
    allowed_formats=("docx", "pdf"),
    requires_edit_history=True,
)


def _results(path: Path, req: FormalRequirements | None = None) -> dict[str, object]:
    return {r.code: r for r in run_checks(read_any(path), req or FRAUD)}


def test_weak_violates_table_font() -> None:
    """У слабого решения в таблицах посторонний шрифт."""
    r = _results(WEAK)["table_font"]
    assert r.status is Status.FAIL
    assert "Arial Unicode MS" in r.message


def test_medium_violates_body_size() -> None:
    """У среднего решения в основном тексте встречается 10 пт вместо 11 пт."""
    r = _results(MEDIUM)["body_size"]
    assert r.status is Status.FAIL
    assert "10" in r.message
    assert r.block_refs, "нарушение обязано ссылаться на конкретные блоки"


def test_good_has_no_formal_violations() -> None:
    """Хорошее решение формальных нарушений не содержит."""
    violations = [r.code for r in run_checks(read_any(GOOD)) if r.is_violation]
    assert violations == [], f"неожиданные нарушения: {violations}"


def test_three_works_differ() -> None:
    """Слой обязан различать работы, иначе он бесполезен."""
    sets = [
        frozenset(r.code for r in run_checks(read_any(p), FRAUD) if r.is_violation)
        for p in (WEAK, MEDIUM, GOOD)
    ]
    assert len(set(sets)) == 3, f"наборы нарушений совпали: {sets}"


def test_undefined_spacing_is_unknown_not_violation() -> None:
    """Главная ловушка: интервал не задан — это «не определено», а не нарушение.

    У хорошего решения межстрочный интервал отсутствует и в параграфах,
    и в `styles.xml`. Трактовка «нет значения → нарушение» оштрафовала бы
    полностью корректную работу.
    """
    r = _results(GOOD)["line_spacing"]
    assert r.status is Status.UNKNOWN
    assert r.status is not Status.FAIL


def test_missing_metadata_degrades_explicitly() -> None:
    """Нет `docProps/app.xml` — объём оценивается и помечается как оценка."""
    doc = read_any(WEAK)
    assert not doc.meta.has_app_xml, "фикстура изменилась: ожидался файл без app.xml"
    r = _results(WEAK)["volume"]
    assert r.status is Status.UNKNOWN
    assert "недоступно" in r.message.lower() or "оценка" in r.message.lower()
    assert any("метаданные" in w.lower() for w in doc.warnings)


def test_tolerated_font_is_not_a_violation() -> None:
    """`Cambria Math` приходит из редактора формул — за это не штрафуем."""
    doc = read_any(GOOD)
    assert "Cambria Math" in doc.fonts_used()
    assert _results(GOOD)["body_font"].status is Status.PASS


def test_images_are_flagged_as_unrated() -> None:
    """Изображения не игнорируются молча."""
    r = _results(GOOD)["images_unrated"]
    assert r.status is Status.UNKNOWN
    assert "не анализирует" in r.message


def test_checks_never_cascade_on_failure() -> None:
    """Падение одной проверки не должно ронять остальные."""
    from app.formal import checks as mod

    original = mod._REGISTRY["volume"]

    def boom(doc, req):  # noqa: ANN001, ARG001
        raise RuntimeError("нарочно сломано")

    mod._REGISTRY["volume"] = (original[0], boom)
    try:
        results = run_checks(read_any(GOOD))
        volume = next(r for r in results if r.code == "volume")
        assert volume.status is Status.UNKNOWN
        assert "нарочно сломано" in volume.message
        assert len(results) == len(mod._REGISTRY), "остальные проверки обязаны отработать"
    finally:
        mod._REGISTRY["volume"] = original


def test_requirements_are_configurable() -> None:
    """Требования приходят из рубрики: другой курс — другие пороги, тот же код."""
    strict = FormalRequirements(body_font="Times New Roman", body_size_pt=14.0)
    results = {r.code: r for r in run_checks(read_any(GOOD), strict)}
    assert results["body_font"].status is Status.FAIL
    assert results["body_size"].status is Status.FAIL


def test_unspecified_requirement_is_not_a_violation() -> None:
    """Требование, которого нет в условии, даёт «неприменимо», а не нарушение.

    Найдено на реальных данных: `FormalRequirements` держал значения ДЗ по
    продуктовому фроду как умолчания, и любой другой курс молча получал чужие
    пороги. Работа по системному дизайну, которую её условие разрешает сдавать
    в Markdown, теряла балл за «формат MD не входит в разрешённые (DOCX, PDF)»
    — за требование, которого в её условии никогда не было.
    """
    empty = FormalRequirements()
    results = {r.code: r for r in run_checks(read_any(GOOD), empty)}

    for code in ("volume", "body_font", "table_font", "body_size",
                 "table_size", "line_spacing", "file_format"):
        assert results[code].status is Status.NA, (
            f"{code}: без требования в условии ожидается NA, получено {results[code].status}"
        )
        assert "не задано" in results[code].message

    # Ни одна проверка не должна объявить нарушение на пустых требованиях.
    assert not [r for r in results.values() if r.status is Status.FAIL]
