"""Пайплайн предварительного ревью.

Устроен как список шагов. Упавший шаг записывается в трассу, помечается
в результате и **не останавливает остальные**: ревью с четырьмя критериями
из пяти и честной пометкой полезнее, чем отсутствие ревью.

Порядок шагов выбран так, чтобы дешёвое и надёжное считалось раньше дорогого
и вероятностного: сначала детерминированные проверки, потом маскирование ПДн,
потом вызовы модели, и только в конце — агрегация.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.agent.evidence import verify_all
from app.agent.prompts import SYSTEM_FEEDBACK, SYSTEM_REVIEWER, build_criterion_prompt
from app.agent.schemas import (
    CriterionResult,
    CriterionVerdict,
    ReviewResult,
    TraceStep,
)
from app.formal.checks import run_checks
from app.ingest.document import BlockKind, Document
from app.llm.client import LLMFailure, get_client
from app.rubric.models import Rubric, RubricNotApproved

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, float], None]


@dataclass
class ReviewContext:
    """Всё, что нужно шагам, и всё, что они между собой передают."""

    work: Document
    rubric: Rubric
    condition: Document | None = None
    submission_id: str = ""
    fast: bool = False
    # Текст работы в том виде, в котором его увидит модель. Заполняется шагом
    # маскирования ПДн — до него ни один вызов модели не выполняется.
    model_text: str = ""
    pii_map: dict[str, str] = field(default_factory=dict)
    result: ReviewResult = field(default_factory=ReviewResult)
    progress: ProgressFn | None = None
    # Настройки формулы. None — значения по умолчанию из ScoringConfig.
    scoring: object | None = None

    def emit(self, stage: str, fraction: float) -> None:
        if self.progress:
            try:
                self.progress(stage, fraction)
            except Exception:  # noqa: BLE001 — прогресс не имеет права ломать ревью
                log.debug("progress callback failed", exc_info=True)


@dataclass
class Step:
    """Шаг пайплайна.

    `required=True` означает, что без него дальнейшие шаги бессмысленны
    (например, без маскирования ПДн нельзя обращаться к модели). Такой шаг
    при отказе останавливает пайплайн — но результат всё равно возвращается,
    с трассой и пометкой.
    """

    name: str
    run: Callable[[ReviewContext], str]
    required: bool = False


# Ниже этого объёма текста оценивать нечего. Порог низкий намеренно: он
# отсекает не короткие работы, а документы, из которых текст не извлёкся
# вовсе — скан без текстового слоя, битый файл, пустой шаблон.
MIN_REVIEWABLE_CHARS = 200

# Доля окна контекста, отдаваемая тексту работы. Остальное — системный
# промпт, схема ответа, выдержка из условия, установленные кодом факты и
# место под сам ответ модели.
WORK_CONTEXT_SHARE = 0.55
# Сколько символов русского текста приходится на токен. Оценка грубая и
# намеренно консервативная: ошибиться в меньшую сторону безопаснее, чем
# упереться в потолок контекста.
CHARS_PER_TOKEN = 2.5


def work_char_budget() -> int:
    """Сколько символов работы помещается в один вызов модели.

    Считается от фактического окна контекста, а не задаётся числом: окно
    меняется вместе с моделью и профилем провайдера, и зашитая константа
    разошлась бы с реальностью при первой же смене `.env`.

    Нужен потому, что работы из набора примеров кейса далеко не все
    «3–9 тысяч символов»: решения по QA и продуктовым курсам в `.xlsx`
    доходят до 148 тысяч. Без своей обрезки такую работу молча укорачивает
    сам сервер модели, и балл выставляется по случайному куску.
    """
    from app.config import settings

    return int(settings.llm_num_ctx * WORK_CONTEXT_SHARE * CHARS_PER_TOKEN)


# ── шаги ──────────────────────────────────────────────────────────────────────


class NothingToReview(RuntimeError):
    """Из документа не извлёкся текст — оценивать нечего."""


def step_readable(ctx: ReviewContext) -> str:
    """Проверяет, что в работе есть текст. Обязательный шаг, идёт первым.

    Найдено на реальных данных: в наборе примеров кейса есть решение,
    сданное сканом (`data_analysis`, ДЗ 3) — PDF на 1,3 МБ, одна страница,
    **ноль символов текста**. Читатель честно пишет предупреждение «страница
    без текстового слоя», но пайплайн этого предупреждения не читал и
    отправлял модели пустой документ. Модель, разумеется, что-то отвечала —
    то есть система выставляла баллы работе, которую не видела.

    Отказ здесь лучше любого балла: работа помечается как непроверяемая
    автоматически и уходит человеку целиком.
    """
    if ctx.work.char_count >= MIN_REVIEWABLE_CHARS:
        return f"текста {ctx.work.char_count} символов — достаточно для оценки"

    reason = "документ пуст"
    if any("текстового слоя" in w for w in ctx.work.warnings):
        reason = "у документа нет текстового слоя — вероятно, это скан"
    raise NothingToReview(
        f"{reason} (извлечено {ctx.work.char_count} символов). "
        f"Автоматическая оценка невозможна: работу нужно проверить вручную. "
        f"Если это скан, попросите студента сдать текстовый документ."
    )


def step_formal(ctx: ReviewContext) -> str:
    """Слой А: формальные проверки без модели."""
    results = run_checks(ctx.work, ctx.rubric.to_formal_requirements())
    ctx.result.formal = [
        {
            "code": r.code,
            "title": r.title,
            "status": r.status.value,
            "message": r.message,
            "evidence": r.evidence,
            "requirement": r.requirement,
            "block_refs": r.block_refs,
        }
        for r in results
    ]
    violations = [r.code for r in results if r.is_violation]
    return f"проверок: {len(results)}, нарушений: {len(violations)}"


def step_pii(ctx: ReviewContext) -> str:
    """ПДн-щит: псевдонимизация ДО любого обращения к модели.

    Шаг обязательный. Если маскирование не отработало, обращаться к модели
    нельзя: в метаданных и тексте работ лежат реальные ФИО.
    """
    from app.config import settings
    from app.privacy.pii import mask_document

    budget = work_char_budget()
    if ctx.work.char_count > budget:
        # Контекст модели конечен, и при его переполнении Ollama обрезает
        # промпт молча — работа оценивалась бы по случайному куску, а
        # ревьюер видел бы обычный балл. Обрезаем сами, по границам блоков,
        # с пометкой в тексте и предупреждением наружу.
        ctx.result.warnings.append(
            f"Работа велика для одного прохода: {ctx.work.char_count} символов при "
            f"лимите {budget}. В проверку попала первая часть — остальное "
            f"нужно посмотреть вручную."
        )

    if not settings.pii_enabled:
        ctx.model_text = ctx.work.numbered_text(max_chars=budget)
        return "маскирование выключено в конфигурации (PII_ENABLED=false)"

    masked, mapping = mask_document(ctx.work, max_chars=budget)
    ctx.model_text = masked
    ctx.pii_map = mapping
    return f"замаскировано сущностей: {len(mapping)}"


def _established_facts(ctx: ReviewContext) -> str:
    """Факты оформления, посчитанные кодом, — в промпт критериев.

    Без них модель судит об оформлении по извлечённому тексту, где нет ни
    шрифтов, ни разметки, зато есть служебные маркеры `§N`. Наблюдалось,
    что она снижает балл именно за них.
    """
    doc = ctx.work
    lines = [
        f"- формат файла: {doc.source_format.upper()}",
        f"- заголовков: {sum(1 for b in doc.blocks if b.kind is BlockKind.HEADING)}",
        f"- элементов списков: {sum(1 for b in doc.blocks if b.kind is BlockKind.LIST_ITEM)}",
        f"- таблиц: {len(doc.tables)}",
        f"- изображений: {doc.facts.get('image_count', 0)} "
        f"(растровые, их содержимое не анализировалось)",
        f"- объём: {doc.char_count} символов, {doc.word_count} слов"
        + (f", {doc.meta.pages} стр. по метаданным" if doc.meta.pages else ""),
    ]

    # Диаграммы mermaid — текст, а не картинка: их содержимое модели доступно,
    # и путать их с «изображение не оценено» нельзя. Тип определяет код:
    # без подсказки модель принимала обрывки `subgraph …` за шум и ставила
    # ноль за критерий «Построена диаграмма C4» работе с тремя диаграммами.
    if diagrams := int(doc.facts.get("diagram_count", 0) or 0):
        kinds = ", ".join(doc.facts.get("diagram_kinds") or []) or "тип не определён"
        lines.append(
            f"- диаграмм mermaid: {diagrams} ({kinds}); это текстовые схемы, "
            f"их содержимое доступно для оценки"
        )
    if blocks := int(doc.facts.get("code_block_count", 0) or 0):
        lines.append(f"- блоков кода: {blocks}")
    if doc.source_format == "docx":
        fonts = doc.fonts_used() or {"не определён"}
        sizes = doc.sizes_used()
        lines.append(f"- шрифты: {', '.join(sorted(fonts))}")
        if sizes:
            lines.append(f"- кегли: {', '.join(f'{s:g} пт' for s in sorted(sizes))}")
        spacing = sorted({b.line_spacing for b in doc.blocks if b.line_spacing is not None})
        lines.append(
            f"- межстрочный интервал: {', '.join(f'{v:g}' for v in spacing)}"
            if spacing else "- межстрочный интервал: не определён"
        )

    # Итоги слоя А: у модели не должно быть своего мнения о том,
    # что уже проверено детерминированно.
    #
    # Статус проставляется словом, а не сырым сообщением. Наблюдалось, что
    # модель принимает «не определено» за нарушение и снижает балл — ровно та
    # ошибка, ради которой в формальном слое заведён отдельный статус UNKNOWN.
    labels = {
        "fail": "НАРУШЕНИЕ",
        "pass": "СОБЛЮДЕНО",
        "unknown": "НЕ ОПРЕДЕЛЕНО — данных нет, это НЕ нарушение, балл за это не снижать",
        "na": "НЕПРИМЕНИМО",
    }
    if ctx.result.formal:
        lines.append("- результаты формальной проверки:")
        lines += [
            f"  · [{labels.get(f['status'], f['status'])}] {f['title']}: {f['message']}"
            for f in ctx.result.formal
        ]
    return "\n".join(lines)


def step_criteria(ctx: ReviewContext) -> str:
    """Слой Б: отдельный вызов модели на каждый критерий.

    Отказ по одному критерию не отменяет остальные — он записывается
    в сам критерий как `failed` и виден ревьюеру.
    """
    client = get_client(fast=ctx.fast)
    total = len(ctx.rubric.criteria)
    facts = _established_facts(ctx)
    condition_excerpt = ""
    if ctx.condition:
        condition_excerpt = ctx.condition.numbered_text(max_chars=4000)

    ok = 0
    for i, criterion in enumerate(ctx.rubric.criteria):
        ctx.emit(f"критерий: {criterion.name}", 0.3 + 0.5 * i / max(total, 1))
        try:
            out = client.complete_json(
                system=SYSTEM_REVIEWER,
                user=build_criterion_prompt(
                    criterion,
                    ctx.model_text,
                    assignment_title=ctx.rubric.assignment_title,
                    condition_excerpt=condition_excerpt,
                    established_facts=facts,
                ),
                schema=CriterionVerdict,
                purpose=f"review.{criterion.id}",
            )
        except LLMFailure as exc:
            ctx.result.criteria.append(
                CriterionResult(
                    criterion_id=criterion.id,
                    criterion_name=criterion.name,
                    max_score=criterion.max_score,
                    score=0.0,
                    verdict="Критерий не оценён: вызов модели не удался.",
                    failed=True,
                    error=exc.reason,
                    recommendation="Оценить этот критерий вручную.",
                )
            )
            continue

        verdict: CriterionVerdict = out.data
        checked, verified = verify_all(verdict.evidence, ctx.work)
        # Балл модели ограничивается максимумом критерия: схема этого
        # не гарантирует, а завышенный балл исказил бы сумму.
        score = max(0.0, min(float(verdict.score), criterion.max_score))

        ctx.result.criteria.append(
            CriterionResult(
                criterion_id=criterion.id,
                criterion_name=criterion.name,
                max_score=criterion.max_score,
                score=score,
                verdict=verdict.verdict.strip(),
                evidence=checked,
                gaps=verdict.gaps,
                recommendation=verdict.recommendation.strip(),
                evidence_verified=verified,
                evidence_total=len(checked),
                # Балл при этом НЕ снижается: неудачное сопоставление цитаты —
                # техническая проблема, а не доказательство невыполнения.
                unverified=bool(checked) and verified == 0,
            )
        )
        ok += 1

    if ok == 0 and total:
        raise RuntimeError("ни один критерий не оценён")
    return f"оценено критериев: {ok} из {total}"


def step_ai_detect(ctx: ReviewContext) -> str:
    """Детектор признаков генеративного ИИ."""
    from app.detect.ai_text import detect_ai_text

    signal = detect_ai_text(ctx.work, model_text=ctx.model_text, fast=ctx.fast)
    ctx.result.ai_signal = signal.model_dump(mode="json")
    return f"скор {signal.score:.2f}, уверенность «{signal.confidence}»"


def step_aggregate(ctx: ReviewContext) -> str:
    """Сборка балла и индекса приоритета — два независимых числа."""
    from app.scoring.formula import aggregate

    # Настройки формулы берутся текущие: методист мог поменять веса до
    # запуска ревью, и новый прогон обязан их учитывать.
    aggregate(ctx.result, ctx.rubric, ctx.scoring)
    return (
        f"предварительный балл {ctx.result.preliminary_score:g} "
        f"из {ctx.result.max_score:g}, приоритет {ctx.result.priority_index:.2f}"
    )


def step_feedback(ctx: ReviewContext) -> str:
    """Персонализированная обратная связь студенту.

    Шаг необязательный: без фидбека ревью остаётся полезным, а ревьюер
    в любом случае правит текст перед отправкой.
    """
    strengths: list[str] = []
    gaps: list[str] = []
    for c in ctx.result.criteria:
        if c.failed:
            continue
        if c.score >= c.max_score * 0.75:
            strengths.append(f"{c.criterion_name}: {c.verdict}")
        gaps.extend(f"{c.criterion_name}: {g}" for g in c.gaps[:2])

    summary = "\n".join(
        [
            "СИЛЬНЫЕ СТОРОНЫ:",
            *(f"- {s}" for s in strengths[:5] or ["- явных сильных сторон не отмечено"]),
            "",
            "ЧТО УЛУЧШИТЬ:",
            *(f"- {g}" for g in gaps[:8] or ["- существенных пробелов не отмечено"]),
        ]
    )
    text = get_client(fast=ctx.fast).complete_text(
        system=SYSTEM_FEEDBACK,
        user=(
            f"Задание: {ctx.rubric.assignment_title or 'домашнее задание'}.\n\n"
            f"Результаты проверки:\n{summary}\n\n"
            f"Напиши обратную связь студенту."
        ),
        purpose="feedback",
    )
    # Обратная подстановка: студент должен увидеть своё имя, а не псевдоним.
    from app.privacy.pii import unmask

    ctx.result.student_feedback = unmask(text, ctx.pii_map)
    return f"{len(ctx.result.student_feedback.split())} слов"


STEPS: list[Step] = [
    Step("читаемость документа", step_readable, required=True),
    Step("формальные проверки", step_formal),
    Step("маскирование ПДн", step_pii, required=True),
    Step("оценка по критериям", step_criteria, required=True),
    Step("признаки генеративного ИИ", step_ai_detect),
    Step("агрегация балла и приоритета", step_aggregate),
    Step("обратная связь студенту", step_feedback),
]


def run_review(
    work: Document,
    rubric: Rubric,
    *,
    condition: Document | None = None,
    submission_id: str = "",
    fast: bool = False,
    progress: ProgressFn | None = None,
    steps: list[Step] | None = None,
    scoring: object | None = None,
) -> ReviewResult:
    """Прогоняет ревью одной работы.

    Возвращает результат всегда — даже если часть шагов упала. Что именно
    не сработало, видно в трассе и в предупреждениях.
    """
    if not rubric.approved:
        raise RubricNotApproved(
            "Ревью по неутверждённой рубрике не запускается: ошибка в критериях "
            "исказила бы все баллы. Утвердите рубрику."
        )

    ctx = ReviewContext(
        work=work,
        rubric=rubric,
        condition=condition,
        submission_id=submission_id,
        fast=fast,
        progress=progress,
        scoring=scoring,
    )
    ctx.result.submission_id = submission_id
    ctx.result.source_name = work.source_name
    ctx.result.rubric_fingerprint = rubric.fingerprint()
    ctx.result.warnings = list(work.warnings)
    ctx.result.model = get_client(fast=fast).model

    started = time.perf_counter()
    plan = steps or STEPS

    for i, step in enumerate(plan):
        ctx.emit(step.name, i / len(plan))
        t0 = time.perf_counter()
        try:
            detail = step.run(ctx)
            ctx.result.trace.append(
                TraceStep(
                    name=step.name, ok=True,
                    duration_ms=(time.perf_counter() - t0) * 1000, detail=detail,
                )
            )
        except Exception as exc:  # noqa: BLE001 — отказ шага не роняет ревью
            log.warning("шаг %r упал: %s", step.name, exc, exc_info=True)
            ctx.result.trace.append(
                TraceStep(
                    name=step.name, ok=False,
                    duration_ms=(time.perf_counter() - t0) * 1000,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            ctx.result.warnings.append(f"Шаг «{step.name}» не выполнен: {exc}")
            if step.required:
                ctx.result.warnings.append(
                    "Шаг обязательный — дальнейшие шаги пропущены. "
                    "Работу нужно проверить вручную."
                )
                # Прерванный пайплайн не доходит до шага агрегации, и индекс
                # приоритета остаётся нулевым — работа, которую автоматика
                # не смогла проверить вовсе, оказывалась в самом низу очереди
                # ревьюера. Ровно наоборот: смотреть её нужно первой.
                ctx.result.priority_index = 1.0
                ctx.result.priority_reasons = [
                    f"автоматическая проверка не выполнена: шаг «{step.name}» отказал"
                ]
                break

    ctx.result.duration_ms = (time.perf_counter() - started) * 1000
    ctx.emit("готово", 1.0)
    return ctx.result
