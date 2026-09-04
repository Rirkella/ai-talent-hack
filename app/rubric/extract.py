"""Автоизвлечение рубрики из условия задания.

Разделение труда между кодом и моделью здесь принципиальное:

* **Код** достаёт всё, что выражено числом или устойчивой формулировкой:
  максимумы баллов, объём в страницах, кегль, шрифт, интервал, сроки, штраф.
  Регулярные выражения на этом не ошибаются, а модель — может.
* **Модель** извлекает только то, где нужна семантика: названия критериев и
  описания «идеального результата» связным текстом.

Результат — всегда черновик. Ревью по неутверждённой рубрике не запускается:
ошибка в рубрике исказила бы все баллы разом.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field

from app.ingest.document import Document
from app.llm.client import LLMFailure, get_client
from app.rubric.models import Criterion, Deadlines, Rubric

# ── детерминированные извлечения ──────────────────────────────────────────────

# «Максимум: 0-1 балл», «0-4 балла», «0–2 балла»
_MAX_SCORE = re.compile(r"(\d+)\s*[-–—]\s*(\d+)\s*балл", re.I)
# «(8 баллов)», «(1 балл)» — вторая распространённая запись максимума.
#
# Нужна не для полноты, а потому что без неё проверка молча выключалась.
# Диапазонная запись «0–4 балла» — формат условия по продуктовому фроду;
# условие по QA пишет «Использование техник тест-дизайна (8 баллов)», и
# регулярка не находила ничего. Кросс-проверка суммы не срабатывала, а
# методист не получал ни одного предупреждения — рубрика с ошибкой модели
# выглядела нормальной.
_MAX_SCORE_PLAIN = re.compile(r"\((\d+)\s*балл\w*\)", re.I)
# «Максимальный балл за сдачу всей домашней работы: 20 баллов», «Итого — 10 баллов»
#
# Это самое надёжное число в условии: заявленный итог, с которым обязана
# сойтись сумма максимумов. На условии по QA модель вернула 21 при
# заявленных 20 — именно такой случай проверка и обязана поймать.
_DECLARED_TOTAL = re.compile(
    # «макс.» без точки после сокращения тоже встречается, а условие по
    # системному дизайну пишет «(макс. 6 баллов, зачёт – 4 балла)» — форма,
    # которую полная словоформа «максимальный балл» не ловит.
    r"(?:максимальн\w*\s+балл\w*|макс\.?\s|итого|всего)[^\n:]{0,70}?[:—–\-]?\s*"
    r"(\d+(?:[.,]\d+)?)\s*балл",
    re.I,
)
# Допустимые форматы файла: «Docx / PDF / Google Документ»,
# «в виде единого документа (PDF, DOCX, Markdown)».
#
# Требование обязано приходить из условия. Пока список форматов был значением
# по умолчанию, работа по системному дизайну — которую её условие разрешает
# сдавать в Markdown — теряла балл за «формат MD не входит в разрешённые».
_FORMATS = re.compile(
    r"(?:формат\w*|документ\w*|сда\w+|принима\w+)[^\n.]{0,60}?"
    r"((?:\b(?:docx?|pdf|markdown|md|xlsx?|google\s+(?:документ|таблиц)\w*|ipynb|zip)\b"
    r"[\s,/|или]*){2,})",
    re.I,
)
_FORMAT_WORDS = {
    "doc": "docx", "docx": "docx", "pdf": "pdf", "markdown": "md", "md": "md",
    "xls": "xlsx", "xlsx": "xlsx", "ipynb": "ipynb", "zip": "zip",
}

# «не более 3-х страниц», «не более 3 страниц»
_PAGES = re.compile(r"не\s+более\s+(\d+)\s*-?\s*х?\s*страниц", re.I)
# Кегль пишут тремя способами, и все три встречаются в реальных условиях
# кейса. Проверено на шести каталогах примеров:
#   «Размер шрифта — 11 пунктов, Arial»      — продуктовый фрод, дизайн A/B
#   «11 кегль, Arial»                        — «Оценка затрат на реализацию»
#   «Шрифт Arial, 12 пт»                     — распространённая формулировка
# Раньше распознавалась только первая: условие с «11 кегль, Arial» молча
# теряло и шрифт, и кегль, а проверки оформления вставали в «не задано».
_UNIT = r"(?:пункт\w*|кегл\w*|пт\b|pt\b)"
_NAME = r"[A-Za-z][A-Za-z0-9 ]{1,24}"
_FONT_MAIN = re.compile(
    rf"шрифта?\s*[—–:-]?\s*(\d+)\s*{_UNIT}\s*,?\s*({_NAME})", re.I
)
# «11 кегль, Arial» — размер идёт первым, слова «шрифт» рядом нет вовсе.
_FONT_SIZE_NAME = re.compile(rf"(\d+)\s*{_UNIT}\s*,\s*({_NAME})", re.I)
# «Шрифт Arial, 12 пт» — сначала имя, потом размер.
_FONT_NAME_SIZE = re.compile(rf"шрифт\w*\s*[—–:-]?\s*({_NAME}?)\s*,?\s*(\d+)\s*{_UNIT}", re.I)
_FONT_TABLE = re.compile(
    rf"для\s+таблиц\s*[—–:-]?\s*(\d+)\s*{_UNIT}\s*,?\s*({_NAME})", re.I
)
# «Межстрочный интервал — 1,15-1,5»
_SPACING = re.compile(r"межстрочн\w*\s+интервал\s*[—–-]?\s*(\d+[.,]\d+)\s*[-–—]\s*(\d+[.,]\d+)", re.I)
# «с штрафом –1 балл», «штраф -1 балл»
_PENALTY = re.compile(r"штраф\w*\s*[—–-]?\s*(\d+)\s*балл", re.I)
# «в течение 1 дня после дедлайна»
_LATE_WINDOW = re.compile(r"в\s+течение\s+(\d+)\s*дн\w*\s+после\s+дедлайн", re.I)
# «занимает не более 7 дней»
_REVIEW_WINDOW = re.compile(r"проверк\w*.{0,40}?не\s+более\s+(\d+)\s*дн\w*", re.I | re.S)
# «Срок сдачи: 9 февраля, 23:59»
_DUE = re.compile(
    r"срок\s+сдачи\s*:?\s*(\d{1,2})\s+([а-яё]+)\s*,?\s*(\d{1,2})[:.](\d{2})", re.I
)

_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}


def extract_deterministic(text: str, *, year: int | None = None) -> dict[str, object]:
    """Числа и формальные требования — без участия модели."""
    out: dict[str, object] = {}

    if m := _PAGES.search(text):
        out["max_pages"] = int(m.group(1))
    # Формулировки перебираются от самой явной к самой общей: «размер шрифта
    # — 11 пунктов, Arial» надёжнее, чем «11 кегль, Arial», где рядом может
    # оказаться любое число с единицей.
    for pattern, order in (
        (_FONT_MAIN, "size_name"),
        (_FONT_NAME_SIZE, "name_size"),
        (_FONT_SIZE_NAME, "size_name"),
    ):
        if m := pattern.search(text):
            size, name = (m.group(1), m.group(2)) if order == "size_name" else (m.group(2), m.group(1))
            name = name.strip().rstrip(";,.").strip()
            if not name:
                continue
            out["body_size_pt"] = float(size)
            out["body_font"] = name
            break
    if m := _FONT_TABLE.search(text):
        out["table_size_pt"] = float(m.group(1))
        out["table_font"] = m.group(2).strip().rstrip(";,.").strip()
    if m := _SPACING.search(text):
        out["line_spacing_min"] = float(m.group(1).replace(",", "."))
        out["line_spacing_max"] = float(m.group(2).replace(",", "."))

    if m := _PENALTY.search(text):
        out["late_penalty"] = float(m.group(1))
    if m := _LATE_WINDOW.search(text):
        out["late_window_hours"] = int(m.group(1)) * 24
    if m := _REVIEW_WINDOW.search(text):
        out["review_window_days"] = int(m.group(1))

    if m := _DUE.search(text):
        day, month_word, hh, mm = m.groups()
        month = next((v for k, v in _MONTHS.items() if month_word.lower().startswith(k)), None)
        if month:
            # Год в условии не указан: это шаблон курса, повторяющийся ежегодно.
            # Подставляем текущий и помечаем, чтобы методист проверил.
            y = year or datetime.now().year
            out["due_at_naive"] = datetime(y, month, int(day), int(hh), int(mm))
            out["due_year_assumed"] = year is None

    # Сначала диапазонная запись, затем запись в скобках: смешивать нельзя,
    # иначе одно и то же число попадёт в список дважды.
    maxima = [int(b) for _, b in _MAX_SCORE.findall(text)]
    if not maxima:
        maxima = [int(b) for b in _MAX_SCORE_PLAIN.findall(text)]
    if maxima:
        out["max_scores"] = maxima
        out["total_max"] = sum(maxima)

    if m := _FORMATS.search(text):
        found: list[str] = []
        for word in re.findall(r"[a-zа-яё]+", m.group(1).lower()):
            fmt = _FORMAT_WORDS.get(word)
            if fmt and fmt not in found:
                found.append(fmt)
        # Google Документ и Google Таблица — это экспорт в docx и xlsx:
        # отдельного формата файла у них нет.
        if "google" in m.group(1).lower():
            for extra in ("docx", "pdf"):
                if extra not in found:
                    found.append(extra)
        if len(found) >= 2:
            out["allowed_formats"] = found

    if m := _DECLARED_TOTAL.search(text):
        out["declared_total"] = float(m.group(1).replace(",", "."))
    return out


# ── семантическое извлечение ──────────────────────────────────────────────────


class _CriterionOut(BaseModel):
    name: str = Field(description="название критерия дословно из условия")
    max_score: float = Field(ge=0, description="максимальный балл за критерий")
    requirements: str = Field(description="описание идеального результата дословно из условия")
    indicators: list[str] = Field(
        default_factory=list, description="3–6 проверяемых признаков полного выполнения"
    )


class _RubricOut(BaseModel):
    assignment_title: str = Field(description="название задания")
    criteria: list[_CriterionOut]


_SYSTEM = (
    "Ты методист образовательной программы. Твоя работа — извлекать структуру "
    "критериев оценивания из текста условия домашнего задания.\n"
    "Правила:\n"
    "1. Извлекай ТОЛЬКО то, что явно написано в условии. Ничего не добавляй от себя.\n"
    "2. Названия критериев и описания идеального результата переноси дословно.\n"
    "3. Признаки (indicators) формулируй как проверяемые утверждения, "
    "опираясь на текст условия.\n"
    "4. Не меняй порядок критериев и не объединяй их."
)


def extract_rubric(
    condition: Document,
    *,
    track: str = "",
    course: str = "",
    fast: bool = False,
) -> Rubric:
    """Извлекает черновик рубрики из условия.

    Числовые максимумы, полученные регулярками, имеют приоритет над числами
    от модели: в условии они записаны однозначно, и доверять здесь модели
    незачем.

    Отказ модели не фатален — возвращается рубрика с формальными требованиями
    и пустым списком критериев, который методист заполнит вручную. Это прямо
    предусмотренный сценарий: ручной ввод критериев есть в интерфейсе.
    """
    text = condition.text
    det = extract_deterministic(text)

    rubric = Rubric(
        track=track,
        course=course,
        source="extracted",
        source_sha256=str(condition.facts.get("sha256", "")),
        max_pages=det.get("max_pages"),  # type: ignore[arg-type]
        body_font=det.get("body_font"),  # type: ignore[arg-type]
        body_size_pt=det.get("body_size_pt"),  # type: ignore[arg-type]
        table_font=det.get("table_font"),  # type: ignore[arg-type]
        table_size_pt=det.get("table_size_pt"),  # type: ignore[arg-type]
        line_spacing_min=det.get("line_spacing_min"),  # type: ignore[arg-type]
        line_spacing_max=det.get("line_spacing_max"),  # type: ignore[arg-type]
        allowed_formats=list(det.get("allowed_formats") or []),  # type: ignore[arg-type]
        deadlines=Deadlines(
            late_penalty=float(det.get("late_penalty", 1.0)),  # type: ignore[arg-type]
            late_window_hours=int(det.get("late_window_hours", 24)),  # type: ignore[arg-type]
            review_window_days=int(det.get("review_window_days", 7)),  # type: ignore[arg-type]
        ),
    )

    try:
        result = get_client(fast=fast).complete_json(
            system=_SYSTEM,
            user=(
                "Извлеки критерии оценивания из условия задания.\n\n"
                f"УСЛОВИЕ:\n{condition.numbered_text(max_chars=20000)}"
            ),
            schema=_RubricOut,
            purpose="rubric.extract",
        )
    except LLMFailure as exc:
        rubric.notes = (
            f"Автоизвлечение критериев не удалось ({exc.reason}). "
            f"Формальные требования извлечены кодом и заполнены. "
            f"Критерии нужно ввести вручную или загрузить файлом."
        )
        return rubric

    out: _RubricOut = result.data
    # Модель видит текст с маркерами §N и иногда переносит их в название.
    rubric.assignment_title = re.sub(r"^\s*§\d+\s*", "", out.assignment_title).strip()

    # Максимумы из регулярок надёжнее: в условии они записаны цифрами.
    det_maxima: list[int] = det.get("max_scores", [])  # type: ignore[assignment]
    for i, c in enumerate(out.criteria):
        max_score = float(det_maxima[i]) if i < len(det_maxima) else c.max_score
        rubric.criteria.append(
            Criterion(
                id=_slug(c.name, i),
                name=c.name.strip(),
                max_score=max_score,
                requirements=c.requirements.strip(),
                indicators=[s.strip() for s in c.indicators if s.strip()],
            )
        )

    notes: list[str] = []
    if det_maxima and len(det_maxima) != len(out.criteria):
        notes.append(
            f"Модель вернула {len(out.criteria)} критери(ев), а в условии найдено "
            f"{len(det_maxima)} максимум(ов) баллов. Проверьте соответствие."
        )
    if (total := det.get("total_max")) and rubric.total_max != total:
        notes.append(f"Сумма максимумов {rubric.total_max:g} ≠ {total} из условия.")
    # Заявленный в условии итог — самое надёжное число, и сверка с ним
    # обязательна: именно она поймала ошибку модели на условии по QA
    # (сумма 21 при заявленных 20 баллах).
    if (declared := det.get("declared_total")) and rubric.total_max != declared:
        notes.append(
            f"В условии заявлен максимум {declared:g} баллов, а сумма критериев "
            f"даёт {rubric.total_max:g}. Один из максимумов извлечён неверно — "
            f"поправьте до утверждения."
        )
    # Одинаковые названия — признак того, что модель приняла заголовок группы
    # за название каждого подкритерия. Ревьюер в таком списке не разберётся.
    seen = [c.name.strip().lower() for c in rubric.criteria]
    dupes = sorted({n for n in seen if seen.count(n) > 1})
    if dupes:
        notes.append(
            "Названия критериев повторяются (" + "; ".join(dupes) + "). "
            "Вероятно, у подкритериев подставлено имя группы — переименуйте."
        )
    if det.get("due_year_assumed"):
        notes.append("Год срока сдачи в условии не указан — подставлен текущий, проверьте.")
    rubric.notes = " ".join(notes)
    return rubric


_TRANSLIT = str.maketrans(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    "abvgdeejzijklmnoprstufhccss_y_eua",
)


def _slug(name: str, i: int) -> str:
    """Идентификатор критерия: устойчивый и читаемый."""
    s = name.lower().translate(_TRANSLIT)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    s = "_".join(s.split("_")[:4])
    return s or f"criterion_{i + 1}"
