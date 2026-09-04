"""Тесты предохранителей пайплайна.

Проверяется не «работает ли ревью», а что система делает, когда проверять
нечего. Молчаливое выставление балла работе, которую модель не видела, —
худший из возможных исходов: он выглядит как нормальный результат.

Случай не выдуман. В наборе примеров кейса есть решение, сданное сканом:
PDF на 1,3 МБ, одна страница, ноль символов извлечённого текста.
"""

from __future__ import annotations

import pytest

from app.agent.pipeline import (
    MIN_REVIEWABLE_CHARS,
    STEPS,
    run_review,
    step_pii,
    work_char_budget,
)
from app.ingest.document import Block, BlockKind, DocMeta, Document
from app.rubric.models import Criterion, Rubric


def _rubric() -> Rubric:
    r = Rubric(criteria=[Criterion(id="c1", name="Единственный критерий", max_score=10)])
    r.approve(by="test")
    return r


def _document(text: str, *, warnings: list[str] | None = None) -> Document:
    blocks = [Block(index=1, kind=BlockKind.PARAGRAPH, text=text)] if text else []
    return Document(
        source_name="работа.pdf",
        source_format="pdf",
        blocks=blocks,
        meta=DocMeta(),
        warnings=warnings or [],
    )


def test_scan_without_text_layer_is_not_scored() -> None:
    """Скан без текстового слоя не должен получить балл."""
    doc = _document(
        "",
        warnings=["1 из 1 страниц без текстового слоя — возможен скан."],
    )
    result = run_review(work=doc, rubric=_rubric(), condition=None, submission_id="s1")

    assert result.criteria == [], "модель не должна оценивать пустой документ"
    assert result.preliminary_score == 0.0

    first = result.trace[0]
    assert first.name == "читаемость документа"
    assert not first.ok, "шаг читаемости обязан отказать"
    assert "скан" in first.detail.lower()

    assert any("проверить вручную" in w for w in result.warnings)


def test_unreviewable_work_goes_to_the_top_of_the_queue() -> None:
    """Приоритет ручной проверки у непроверяемой работы — максимальный.

    Прерванный пайплайн не доходит до шага агрегации, и индекс оставался
    нулевым: работа, которую автоматика не смогла проверить вовсе, падала
    в самый низ очереди ревьюера — ровно туда, где её никто не увидит.
    """
    result = run_review(
        work=_document(""), rubric=_rubric(), condition=None, submission_id="s2"
    )
    assert result.priority_index == 1.0
    assert result.priority_reasons
    assert "не выполнена" in result.priority_reasons[0]


def test_threshold_separates_empty_from_merely_short() -> None:
    """Порог отсекает нечитаемое, а не короткие работы.

    Короткая работа — это оценка «мало сделано», её ставит ревьюер по
    критериям. Пустая — это отсутствие данных, и здесь автоматике сказать
    нечего. Разница принципиальная, поэтому порог намеренно низкий.
    """
    assert MIN_REVIEWABLE_CHARS <= 300, "порог не должен отсекать короткие работы"

    short = _document("к" * (MIN_REVIEWABLE_CHARS - 1))
    assert not run_review(
        work=short, rubric=_rubric(), condition=None, submission_id="s3"
    ).trace[0].ok

    # Дальше шага читаемости здесь идти незачем: остальные шаги обращаются
    # к модели, а проверяется именно порог.
    enough = _document("к" * MIN_REVIEWABLE_CHARS)
    assert run_review(
        work=enough, rubric=_rubric(), condition=None, submission_id="s4",
        steps=STEPS[:1],
    ).trace[0].ok, "работа с текстом обязана пройти шаг читаемости"


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_blank_documents_are_all_rejected(text: str) -> None:
    """Пробелы и переводы строк — тот же пустой документ."""
    result = run_review(
        work=_document(text), rubric=_rubric(), condition=None, submission_id="s5"
    )
    assert not result.trace[0].ok


# ── объём работы против окна контекста ────────────────────────────────────────


def _context(work: Document):
    """Контекст пайплайна с одной работой — для проверки отдельного шага."""
    from app.agent.pipeline import ReviewContext

    return ReviewContext(work=work, rubric=_rubric(), condition=None, submission_id="s")


def test_oversized_work_is_cut_with_a_warning() -> None:
    """Работу, не влезающую в контекст, обрезаем сами и говорим об этом.

    Иначе промпт молча укорачивает сервер модели, и балл выставляется по
    случайному куску работы — при этом ревьюер видит обычный результат.
    Проверено на наборе примеров кейса: решения в `.xlsx` доходят до 148
    тысяч символов при бюджете около 45 тысяч.
    """
    budget = work_char_budget()
    ctx = _context(_document("я" * (budget * 2)))

    step_pii(ctx)

    assert len(ctx.model_text) <= budget + 200, "текст для модели не обрезан"
    assert "обрезан" in ctx.model_text, "в тексте нет пометки об обрезке"
    assert any("велика для одного прохода" in w for w in ctx.result.warnings)


def test_normal_work_is_passed_whole() -> None:
    """Обычная работа не должна страдать от предохранителя."""
    ctx = _context(_document("текст работы. " * 200))
    step_pii(ctx)

    assert "обрезан" not in ctx.model_text
    assert not any("велика" in w for w in ctx.result.warnings)


def test_budget_follows_the_configured_context_window() -> None:
    """Бюджет считается от окна контекста, а не задан константой.

    Зашитое число разошлось бы с реальностью при первой же смене модели
    или профиля провайдера в `.env`.
    """
    from app.config import settings

    budget = work_char_budget()
    assert 0 < budget < settings.llm_num_ctx * 4, "бюджет не связан с окном контекста"
    assert budget > 20_000, "бюджет не должен резать обычные работы"
