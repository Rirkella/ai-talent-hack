"""Слой А — формальные проверки без LLM.

Всё, что определяется однозначно, считается кодом: объём, шрифт, кегль,
межстрочный интервал, формат файла, наличие таблиц и схем, признаки истории
изменений, декларация об использовании ИИ. Быстро, точно, объяснимо построчно
и полностью воспроизводимо — модель к этим выводам не привлекается.

Ключевое решение — **четыре статуса, а не два**. `UNKNOWN` существует потому,
что у хорошего решения межстрочный интервал не задан ни в параграфах, ни в
стилях: честный ответ здесь «не определено». Трактовать отсутствие данных как
нарушение значит штрафовать корректную работу, а как соответствие —
пропускать реальное. Оба варианта неприемлемы, поэтому неопределённость
доводится до ревьюера как отдельное состояние.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from app.ingest.document import BlockKind, Document

# Символов на страницу — для оценки объёма, когда метаданных нет.
# Ориентир: Arial 11 pt, интервал 1,15, поля по умолчанию.
CHARS_PER_PAGE = 3000


class Status(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    # Данных для вывода недостаточно. Не нарушение и не соответствие.
    UNKNOWN = "unknown"
    # Проверка неприменима к этому формату (например, кегль в PDF).
    NA = "na"


@dataclass
class CheckResult:
    code: str
    title: str
    status: Status
    message: str
    # На что опирается вывод: конкретные значения и ссылки на блоки.
    evidence: list[str] = field(default_factory=list)
    # Требование из условия задания — чтобы ревьюер видел источник правила.
    requirement: str = ""
    # Ссылки на блоки §N для подсветки в просмотрщике.
    block_refs: list[int] = field(default_factory=list)

    @property
    def is_violation(self) -> bool:
        return self.status is Status.FAIL


@dataclass
class FormalRequirements:
    """Требования из условия задания.

    **`None` значит «в условии не задано», а не «значение по умолчанию».**
    Разница принципиальная. Раньше здесь стояли числа из ДЗ «Карта рисков
    продукта» — 3 страницы, Arial 11 пт, форматы DOCX и PDF, — и любой другой
    курс молча получал чужие требования. Работа по системному дизайну,
    которую условие разрешает сдавать в Markdown, теряла балл за «формат MD
    не входит в разрешённые (DOCX, PDF)»: требование, которого в её условии
    никогда не было.

    Незаданное требование даёт статус `NA` — «неприменимо». Это честнее
    выдуманного порога и заметнее молчаливого пропуска.
    """

    max_pages: int | None = None
    body_font: str | None = None
    body_size_pt: float | None = None
    table_font: str | None = None
    table_size_pt: float | None = None
    line_spacing_min: float | None = None
    line_spacing_max: float | None = None
    allowed_formats: tuple[str, ...] = ()
    requires_edit_history: bool = False
    requires_ai_declaration: bool = True
    # Шрифты, которые не считаются нарушением: Word подставляет их сам.
    # `Cambria Math` появляется в любой формуле, вставленной редактором формул,
    # и штрафовать за это студента было бы неверно.
    tolerated_fonts: tuple[str, ...] = ("Cambria Math", "Symbol", "Wingdings")


def _not_specified(code: str, title: str, what: str) -> CheckResult:
    """Требование в условии не задано — проверять нечего.

    Отдельный статус, а не пропуск: ревьюер должен видеть, что проверка
    существует и почему не выполнялась.
    """
    return CheckResult(
        code=code, title=title, status=Status.NA,
        message=f"В условии задания не задано требование: {what}.",
        requirement="не задано условием",
    )


Check = Callable[[Document, FormalRequirements], CheckResult]

_REGISTRY: dict[str, tuple[str, Check]] = {}


def check(code: str, title: str) -> Callable[[Check], Check]:
    """Регистрирует проверку. Новая проверка = новая функция, и всё."""

    def deco(fn: Check) -> Check:
        _REGISTRY[code] = (title, fn)
        return fn

    return deco


# ── объём ─────────────────────────────────────────────────────────────────────


@check("volume", "Объём работы")
def check_volume(doc: Document, req: FormalRequirements) -> CheckResult:
    """Объём в страницах.

    Точное число страниц знает только Word и кладёт его в `docProps/app.xml`.
    Этого файла у экспорта из Google Docs нет, поэтому объём оценивается по
    числу символов — и результат помечается как оценка, а не как факт.
    """
    if req.max_pages is None:
        return _not_specified("volume", "Объём работы", "предельный объём в страницах")

    declared = doc.meta.pages
    if declared:
        status = Status.PASS if declared <= req.max_pages else Status.FAIL
        return CheckResult(
            code="volume",
            title="Объём работы",
            status=status,
            message=(
                f"{declared} стр. при лимите {req.max_pages}."
                if status is Status.PASS
                else f"{declared} стр. — превышен лимит {req.max_pages} стр."
            ),
            evidence=[f"docProps/app.xml: Pages={declared}"],
            requirement=f"Объём — не более {req.max_pages} страниц без приложений.",
        )

    est = max(1, round(doc.char_count / CHARS_PER_PAGE))
    # Порог с запасом: оценка по символам неточна, и штрафовать по ней нельзя.
    over = est > req.max_pages + 1
    return CheckResult(
        code="volume",
        title="Объём работы",
        status=Status.FAIL if over else Status.UNKNOWN,
        message=(
            f"Точное число страниц недоступно (нет docProps/app.xml). "
            f"Оценка по объёму текста: ~{est} стр. при {doc.char_count} символах."
            + (f" Оценка превышает лимит {req.max_pages} стр." if over else
               " В пределах допустимого, но требует взгляда ревьюера.")
        ),
        evidence=[f"символов: {doc.char_count}", f"оценка: ~{est} стр.",
                  "метаданные Word отсутствуют"],
        requirement=f"Объём — не более {req.max_pages} страниц без приложений.",
    )


# ── шрифт и кегль ─────────────────────────────────────────────────────────────


@check("body_font", "Шрифт основного текста")
def check_body_font(doc: Document, req: FormalRequirements) -> CheckResult:
    return _font_check(doc, req, in_tables=False, expected=req.body_font,
                       code="body_font", title="Шрифт основного текста")


@check("table_font", "Шрифт в таблицах")
def check_table_font(doc: Document, req: FormalRequirements) -> CheckResult:
    return _font_check(doc, req, in_tables=True, expected=req.table_font,
                       code="table_font", title="Шрифт в таблицах")


def _font_check(
    doc: Document, req: FormalRequirements, *, in_tables: bool,
    expected: str | None, code: str, title: str,
) -> CheckResult:
    if expected is None:
        return _not_specified(code, title, "требуемый шрифт")
    if doc.source_format not in {"docx"}:
        return CheckResult(code, title, Status.NA,
                           f"Для формата {doc.source_format} шрифт не извлекается.",
                           requirement=f"Шрифт — {expected}.")
    if in_tables and not doc.tables:
        return CheckResult(code, title, Status.NA, "В работе нет таблиц.",
                           requirement=f"Шрифт в таблицах — {expected}.")

    used = doc.fonts_used(in_tables=in_tables)
    if not used:
        return CheckResult(code, title, Status.UNKNOWN,
                           "Шрифт не определён: явных значений нет и наследование не дало результата.",
                           requirement=f"Шрифт — {expected}.")

    tolerated = set(req.tolerated_fonts)
    foreign = {f for f in used if f != expected and f not in tolerated}
    ignored = {f for f in used if f in tolerated}

    ev = [f"использованы: {', '.join(sorted(used))}"]
    if ignored:
        ev.append(f"не считаются нарушением (служебные): {', '.join(sorted(ignored))}")

    if foreign:
        refs = [b.index for b in doc.blocks
                if (b.kind is BlockKind.TABLE) == in_tables and (b.fonts & foreign)]
        return CheckResult(code, title, Status.FAIL,
                           f"Посторонний шрифт: {', '.join(sorted(foreign))} при требуемом {expected}.",
                           evidence=ev, requirement=f"Шрифт — {expected}.", block_refs=refs[:12])
    return CheckResult(code, title, Status.PASS, f"Везде {expected}.",
                       evidence=ev, requirement=f"Шрифт — {expected}.")


@check("body_size", "Кегль основного текста")
def check_body_size(doc: Document, req: FormalRequirements) -> CheckResult:
    return _size_check(doc, req, in_tables=False, expected=req.body_size_pt,
                       code="body_size", title="Кегль основного текста")


@check("table_size", "Кегль в таблицах")
def check_table_size(doc: Document, req: FormalRequirements) -> CheckResult:
    return _size_check(doc, req, in_tables=True, expected=req.table_size_pt,
                       code="table_size", title="Кегль в таблицах")


def _size_check(
    doc: Document, req: FormalRequirements, *, in_tables: bool,
    expected: float | None, code: str, title: str,
) -> CheckResult:
    if expected is None:
        return _not_specified(code, title, "требуемый кегль")
    if doc.source_format not in {"docx"}:
        return CheckResult(code, title, Status.NA,
                           f"Для формата {doc.source_format} кегль не извлекается.",
                           requirement=f"Кегль — {expected:g} пт.")
    if in_tables and not doc.tables:
        return CheckResult(code, title, Status.NA, "В работе нет таблиц.",
                           requirement=f"Кегль в таблицах — {expected:g} пт.")

    used = doc.sizes_used(in_tables=in_tables)
    if not used:
        return CheckResult(code, title, Status.UNKNOWN, "Кегль не определён.",
                           requirement=f"Кегль — {expected:g} пт.")

    wrong = {s for s in used if abs(s - expected) > 0.01}
    ev = [f"кегли: {', '.join(f'{s:g} пт' for s in sorted(used))}"]
    if wrong:
        refs = [b.index for b in doc.blocks
                if (b.kind is BlockKind.TABLE) == in_tables and (b.sizes_pt & wrong)]
        return CheckResult(
            code, title, Status.FAIL,
            f"Отклонение от {expected:g} пт: встречается "
            f"{', '.join(f'{s:g} пт' for s in sorted(wrong))}.",
            evidence=ev, requirement=f"Кегль — {expected:g} пт.", block_refs=refs[:12])
    return CheckResult(code, title, Status.PASS, f"Везде {expected:g} пт.",
                       evidence=ev, requirement=f"Кегль — {expected:g} пт.")


# ── межстрочный интервал ──────────────────────────────────────────────────────


@check("line_spacing", "Межстрочный интервал")
def check_line_spacing(doc: Document, req: FormalRequirements) -> CheckResult:
    """Интервал с разрешением наследования из стилей.

    Значение может быть не задано ни в параграфе, ни в стиле, ни в
    `docDefaults` — тогда Word применяет собственное умолчание, а мы не можем
    утверждать, каким оно окажется при открытии. Ответ — «не определено».
    """
    if req.line_spacing_min is None or req.line_spacing_max is None:
        return _not_specified(
            "line_spacing", "Межстрочный интервал", "допустимый межстрочный интервал"
        )

    req_text = f"Межстрочный интервал — {req.line_spacing_min}–{req.line_spacing_max}."
    if doc.source_format != "docx":
        return CheckResult("line_spacing", "Межстрочный интервал", Status.NA,
                           f"Для формата {doc.source_format} интервал не извлекается.",
                           requirement=req_text)

    values = {b.line_spacing for b in doc.blocks if b.line_spacing is not None}
    undefined = sum(1 for b in doc.blocks if b.line_spacing is None and b.text.strip())

    if not values:
        return CheckResult(
            "line_spacing", "Межстрочный интервал", Status.UNKNOWN,
            f"Интервал не задан ни в параграфах, ни в стилях ({undefined} блоков) — "
            f"применяется умолчание Word. Проверить визуально.",
            evidence=["в параграфах не задан", "в styles.xml и docDefaults не задан"],
            requirement=req_text)

    out = {v for v in values if not (req.line_spacing_min - 1e-6 <= v <= req.line_spacing_max + 1e-6)}
    ev = [f"значения: {', '.join(f'{v:g}' for v in sorted(values))}"]
    if undefined:
        ev.append(f"ещё {undefined} блоков без явного значения — наследуют умолчание")

    if out:
        refs = [b.index for b in doc.blocks if b.line_spacing in out]
        return CheckResult("line_spacing", "Межстрочный интервал", Status.FAIL,
                           f"Вне диапазона {req.line_spacing_min}–{req.line_spacing_max}: "
                           f"{', '.join(f'{v:g}' for v in sorted(out))}.",
                           evidence=ev, requirement=req_text, block_refs=refs[:12])
    return CheckResult("line_spacing", "Межстрочный интервал", Status.PASS,
                       f"{', '.join(f'{v:g}' for v in sorted(values))} — в пределах нормы.",
                       evidence=ev, requirement=req_text)


# ── формат, история изменений, структура ──────────────────────────────────────


@check("file_format", "Формат файла")
def check_file_format(doc: Document, req: FormalRequirements) -> CheckResult:
    if not req.allowed_formats:
        return _not_specified("file_format", "Формат файла", "допустимые форматы файла")

    ok = doc.source_format in req.allowed_formats
    return CheckResult(
        "file_format", "Формат файла",
        Status.PASS if ok else Status.FAIL,
        f"{doc.source_format.upper()}" + ("" if ok else
        f" — не входит в разрешённые: {', '.join(f.upper() for f in req.allowed_formats)}"),
        evidence=[f"файл: {doc.source_name}"],
        requirement=f"Формат — {', '.join(f.upper() for f in req.allowed_formats)}.")


@check("edit_history", "История изменений")
def check_edit_history(doc: Document, req: FormalRequirements) -> CheckResult:
    """Признаки истории правок.

    Условие требует доступ на редактирование с видимой историей изменений.
    Полноценно это проверяется только в самом сервисе (Google Docs, Word
    Online), поэтому проверка честно ограничена косвенными признаками внутри
    файла и никогда не выносит `FAIL` — только `PASS` или `UNKNOWN`.
    Утверждать по локальной копии, что истории нет, было бы неверно.
    """
    req_text = "Доступ на редактирование, должна быть видна история изменений."
    if not req.requires_edit_history:
        return CheckResult("edit_history", "История изменений", Status.NA,
                           "Требование не предъявляется.", requirement=req_text)

    signals: list[str] = []
    if doc.facts.get("has_tracked_changes"):
        signals.append("в документе есть правки в режиме рецензирования")
    if doc.facts.get("has_comments"):
        signals.append("есть примечания")
    if (rev := doc.meta.revision) and rev > 1:
        signals.append(f"номер редакции: {rev}")
    if doc.meta.created and doc.meta.modified and doc.meta.modified > doc.meta.created:
        signals.append("дата изменения позже даты создания")

    if signals:
        return CheckResult("edit_history", "История изменений", Status.PASS,
                           "Найдены признаки истории правок.", evidence=signals,
                           requirement=req_text)
    return CheckResult(
        "edit_history", "История изменений", Status.UNKNOWN,
        "Признаков истории правок в файле нет. По локальной копии это не доказывает "
        "их отсутствия: история живёт в сервисе, где работа велась. Проверить ссылку вручную.",
        evidence=["метаданные о редакциях недоступны"] if not doc.meta.is_available else [],
        requirement=req_text)


@check("structure", "Структурные элементы")
def check_structure(doc: Document, req: FormalRequirements) -> CheckResult:
    """Заголовки, списки, таблицы, схемы — основа критерия «качество оформления»."""
    headings = sum(1 for b in doc.blocks if b.kind is BlockKind.HEADING)
    lists = sum(1 for b in doc.blocks if b.kind is BlockKind.LIST_ITEM)
    tables = len(doc.tables)
    images = int(doc.facts.get("image_count", 0))

    ev = [f"заголовков: {headings}", f"списков: {lists}",
          f"таблиц: {tables}", f"изображений: {images}"]
    present = sum(1 for n in (headings, lists, tables, images) if n)

    if present >= 2:
        return CheckResult("structure", "Структурные элементы", Status.PASS,
                           f"Используются {present} из 4 типов структурных элементов.",
                           evidence=ev, requirement="Использованы заголовки, списки, таблицы, схемы.")
    return CheckResult("structure", "Структурные элементы", Status.FAIL,
                       f"Структурных элементов почти нет ({present} из 4) — "
                       f"работа читается как сплошной текст.",
                       evidence=ev, requirement="Использованы заголовки, списки, таблицы, схемы.")


@check("images_unrated", "Изображения не оценены")
def check_images(doc: Document, req: FormalRequirements) -> CheckResult:
    """Явная отметка о том, чего проверка не умеет.

    Схемы CJM и матрицы рисков часто нарисованы картинкой. Текстовая модель их
    не видит. Молчать об этом нельзя: ревьюер должен знать, что часть работы
    осталась вне автоматической проверки.
    """
    n = int(doc.facts.get("image_count", 0))
    if not n:
        return CheckResult("images_unrated", "Изображения не оценены", Status.NA,
                           "Изображений в работе нет.")
    return CheckResult(
        "images_unrated", "Изображения не оценены", Status.UNKNOWN,
        f"В работе {n} изображени(е/я). Текстовая модель их не анализирует — "
        f"содержание схем и матриц оценивает ревьюер.",
        evidence=[i.name for i in doc.images],
        requirement="Ограничение автоматической проверки.")


# ── декларация об использовании ИИ ────────────────────────────────────────────

# Условие обязывает студента раскрыть факт применения ИИ. Формулировка
# свободная, поэтому ищем устойчивые сочетания, а решение оставляем человеку.
_AI_DECLARATION_MARKERS = (
    "использовал ии", "использовала ии", "использовался ии", "с помощью ии",
    "использовал искусственный интеллект", "применял ии", "применялся ии",
    "chatgpt", "gpt-4", "gpt‑4", "нейросет", "языков модел", "llm",
    "yandexgpt", "гигачат", "gigachat", "claude", "deepseek",
    "ai-инструмент", "ии-инструмент", "ии инструмент", "сгенерирован",
)


@check("ai_declaration", "Декларация об использовании ИИ")
def check_ai_declaration(doc: Document, req: FormalRequirements) -> CheckResult:
    """Есть ли в работе упоминание об использовании ИИ.

    Отсутствие декларации само по себе не нарушение: студент мог не применять
    ИИ вовсе. Проверка лишь снабжает ревьюера фактом и связывает его с сигналом
    генеративного детектора.
    """
    req_text = "Если вы использовали ИИ — укажите это в работе и опишите, как именно."
    if not req.requires_ai_declaration:
        return CheckResult("ai_declaration", "Декларация об использовании ИИ", Status.NA,
                           "Требование не предъявляется.", requirement=req_text)

    low = doc.text.lower()
    hits = [m for m in _AI_DECLARATION_MARKERS if m in low]
    if hits:
        refs = [b.index for b in doc.blocks
                if any(m in b.text.lower() for m in hits)]
        return CheckResult("ai_declaration", "Декларация об использовании ИИ", Status.PASS,
                           "В работе есть упоминание об использовании ИИ.",
                           evidence=[f"совпадения: {', '.join(hits[:6])}"],
                           requirement=req_text, block_refs=refs[:6])
    return CheckResult(
        "ai_declaration", "Декларация об использовании ИИ", Status.UNKNOWN,
        "Упоминаний об использовании ИИ в работе нет. Это не нарушение: студент мог "
        "не применять ИИ. Сопоставьте с сигналом генеративного детектора.",
        requirement=req_text)


# ── запуск ────────────────────────────────────────────────────────────────────


def run_checks(
    doc: Document,
    req: FormalRequirements | None = None,
    *,
    only: list[str] | None = None,
) -> list[CheckResult]:
    """Прогоняет все зарегистрированные проверки.

    Упавшая проверка не роняет остальные: её отказ становится `UNKNOWN`
    с текстом ошибки. Каскадных отказов у формального слоя быть не должно.
    """
    req = req or FormalRequirements()
    out: list[CheckResult] = []
    for code, (title, fn) in _REGISTRY.items():
        if only and code not in only:
            continue
        try:
            out.append(fn(doc, req))
        except Exception as exc:  # noqa: BLE001
            out.append(CheckResult(code, title, Status.UNKNOWN,
                                   f"Проверка не выполнена: {type(exc).__name__}: {exc}"))
    return out