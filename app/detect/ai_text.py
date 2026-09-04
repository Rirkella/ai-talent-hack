"""Признаки использования генеративного ИИ.

Кейс задаёт рамку жёстко: сигнал **не является доказательством нарушения**,
не влияет на оценку автоматически, и решение принимает ревьюер. Модуль
устроен ровно под эту рамку — он выдаёт не вердикт, а четыре вещи:
скор, уверенность, основания и **ограничения метода**.

Ансамбль независимых сигналов, чтобы ни один не решал в одиночку:

1. **Стилометрия** — равномерность длин предложений (burstiness), лексическое
   разнообразие, доля клише, дефицит конкретики. Считается кодом.
2. **Форматные артефакты** — типографика, характерная для вставки из чата.
3. **Метаданные** — темп написания. Считается **только если `app.xml` есть**;
   иначе сигнал честно помечается как недоступный, а не как нулевой.
4. **Суждение модели** — с обязательными цитатами.

Уверенность понижается, когда часть сигналов недоступна: вывод по двум
признакам из четырёх не может быть таким же уверенным, как по всем.
"""

from __future__ import annotations

import logging
import re
import statistics
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.agent.prompts import SYSTEM_AI_JUDGE
from app.ingest.document import Document
from app.llm.client import LLMFailure, get_client

log = logging.getLogger(__name__)


class Confidence(StrEnum):
    LOW = "низкая"
    MEDIUM = "средняя"
    HIGH = "высокая"
    UNAVAILABLE = "недостаточно данных"


class SignalDetail(BaseModel):
    """Один сигнал ансамбля."""

    code: str
    title: str
    # None означает «сигнал неприменим»: это не то же самое, что 0.0.
    value: float | None = None
    available: bool = True
    weight: float = 0.0
    detail: str = ""
    evidence: list[str] = Field(default_factory=list)


class AISignal(BaseModel):
    """Итоговый сигнал — рекомендательный, с основаниями и ограничениями."""

    score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: Confidence = Confidence.LOW
    signals: list[SignalDetail] = Field(default_factory=list)
    grounds: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    # Декларация студента об использовании ИИ, которую требует условие.
    declaration_found: bool = False

    # Вердикт ревьюера. Заполняется человеком, а не системой.
    reviewer_verdict: str | None = None
    reviewer_comment: str = ""


# ── 1. стилометрия ────────────────────────────────────────────────────────────

_SENT = re.compile(r"[.!?…]+\s+")

# Обороты, типичные для сгенерированного делового текста на русском.
_CLICHES = (
    "важно отметить", "стоит отметить", "необходимо отметить", "следует отметить",
    "в современном мире", "играет ключевую роль", "играет важную роль",
    "комплексный подход", "в заключение", "таким образом", "подводя итог",
    "не только", "но и", "динамично развива", "широкий спектр",
    "эффективное решение", "позволяет обеспечить", "ряд преимуществ",
    "в целом можно сказать", "является одним из ключевых",
)

# Конкретика: цифры, проценты, деньги, даты. Её дефицит — признак «воды».
_SPECIFIC = re.compile(r"\d+[.,]?\d*\s*(?:%|руб|₽|тыс|млн|млрд|шт|дн|мес|год|кв)", re.I)


def _stylometry(text: str) -> SignalDetail:
    sentences = [s.strip() for s in _SENT.split(text) if len(s.strip()) > 15]
    if len(sentences) < 8:
        return SignalDetail(
            code="stylometry", title="Стилометрия", available=False,
            detail=f"Текста слишком мало для статистики: {len(sentences)} предложений, нужно от 8.",
        )

    lengths = [len(s.split()) for s in sentences]
    mean = statistics.mean(lengths)
    stdev = statistics.pstdev(lengths)
    # Burstiness: у человека длины предложений скачут сильнее.
    burstiness = stdev / mean if mean else 0.0

    words = [w.lower() for w in re.findall(r"\b[а-яёa-z]{3,}\b", text.lower())]
    ttr = len(set(words)) / len(words) if words else 0.0

    low = text.lower()
    cliche_hits = [c for c in _CLICHES if c in low]
    cliche_rate = len(cliche_hits) / max(len(sentences), 1)

    specifics = len(_SPECIFIC.findall(text))
    specific_rate = specifics / max(len(sentences), 1)

    # Каждый компонент нормируется в [0,1], где 1 — «больше похоже на машину».
    burst_score = max(0.0, min(1.0, (0.55 - burstiness) / 0.35))
    ttr_score = max(0.0, min(1.0, (ttr - 0.42) / 0.25))
    cliche_score = max(0.0, min(1.0, cliche_rate / 0.35))
    specific_score = max(0.0, min(1.0, (0.30 - specific_rate) / 0.30))

    value = 0.35 * burst_score + 0.20 * ttr_score + 0.25 * cliche_score + 0.20 * specific_score

    ev = [
        f"равномерность длин предложений (burstiness): {burstiness:.2f} "
        f"(у человека обычно выше 0,55)",
        f"лексическое разнообразие: {ttr:.2f}",
        f"клише: {len(cliche_hits)} на {len(sentences)} предложений",
        f"конкретных величин (цифры, %, деньги): {specifics}",
    ]
    if cliche_hits:
        ev.append("найденные обороты: " + ", ".join(f"«{c}»" for c in cliche_hits[:5]))

    return SignalDetail(
        code="stylometry", title="Стилометрия", value=round(value, 3), weight=0.30,
        detail=f"Композитная оценка по 4 показателям: {value:.2f}.", evidence=ev,
    )


# ── 2. форматные артефакты ────────────────────────────────────────────────────


def _format_artifacts(doc: Document) -> SignalDetail:
    text = doc.text
    hits: list[str] = []

    # Типографика, которую редко ставят руками, но всегда ставит генератор.
    if text.count("—") > max(3, len(text) // 900):
        hits.append(f"частые длинные тире: {text.count('—')}")
    if "“" in text or "”" in text:
        hits.append("английские кавычки-«лапки» в русском тексте")
    if re.search(r"(?m)^\s*\*\*[^*]+\*\*\s*$", text):
        hits.append("markdown-выделение **жирным** внутри документа")
    if re.search(r"(?m)^\s*#{1,4}\s+\S", text):
        hits.append("markdown-заголовки внутри документа")
    if re.search(r"\bAs an AI\b|как языковая модель|как ии-ассистент", text, re.I):
        hits.append("прямая реплика ассистента в тексте")

    # Абзацы почти одинаковой длины — след пословной генерации по шаблону.
    paras = [len(b.text) for b in doc.blocks if b.kind.value == "paragraph" and len(b.text) > 120]
    if len(paras) >= 5:
        cv = statistics.pstdev(paras) / statistics.mean(paras)
        if cv < 0.25:
            hits.append(f"абзацы почти одинаковой длины (разброс {cv:.2f})")

    value = min(1.0, len(hits) / 4)
    return SignalDetail(
        code="format", title="Форматные артефакты", value=round(value, 3), weight=0.15,
        detail=(f"Найдено признаков: {len(hits)}." if hits else "Форматных признаков нет."),
        evidence=hits,
    )


# ── 3. метаданные ─────────────────────────────────────────────────────────────


def _metadata(doc: Document) -> SignalDetail:
    """Темп написания по метаданным Word.

    Деградирует явно: у экспорта из Google Docs `docProps/app.xml` нет вовсе,
    и сигнал обязан сообщить «недоступно», а не «признаков не найдено».
    Разница принципиальная — второе ввело бы ревьюера в заблуждение.
    """
    if not doc.meta.has_app_xml:
        return SignalDetail(
            code="metadata", title="Метаданные документа", available=False,
            detail=(
                "docProps/app.xml отсутствует — вероятен экспорт из Google Docs. "
                "Время редактирования и число слов недоступны. Это НЕ означает "
                "отсутствия признаков: сигнал неприменим."
            ),
        )

    minutes = doc.meta.total_time_min
    words = doc.meta.words or doc.word_count
    if not minutes or minutes <= 0:
        return SignalDetail(
            code="metadata", title="Метаданные документа", available=False,
            detail="Время редактирования не заполнено — сигнал неприменим.",
        )

    wpm = words / minutes
    # Осмысленный набор с обдумыванием — единицы слов в минуту. 25+ слов
    # в минуту на протяжении всей работы означает вставку готового текста.
    value = max(0.0, min(1.0, (wpm - 8) / 20))
    ev = [
        f"время редактирования: {minutes} мин",
        f"слов: {words}",
        f"темп: {wpm:.1f} слов/мин",
    ]
    if doc.meta.application:
        ev.append(f"редактор: {doc.meta.application}")

    return SignalDetail(
        code="metadata", title="Метаданные документа", value=round(value, 3), weight=0.20,
        detail=(
            f"Темп {wpm:.1f} слов/мин."
            + (" Высокий для самостоятельного набора." if wpm > 15 else " В пределах обычного.")
        ),
        evidence=ev,
    )


# ── 4. суждение модели ────────────────────────────────────────────────────────


class _JudgeOut(BaseModel):
    """Вывод судьи.

    Списки ограничены сверху и **обрезаются**, а не отвергаются: длинный
    ответ упирается в потолок токенов и обрывается на середине JSON, а
    полный ретрай из-за избыточного усердия модели — плохой размен.
    """

    likelihood: float = Field(ge=0.0, le=1.0, description="вероятность машинной генерации")
    observations: list[str] = Field(description="наблюдаемые стилистические признаки, до 5")
    quotes: list[str] = Field(default_factory=list, description="до 3 цитат с указанием §N")
    alternative_explanations: list[str] = Field(
        default_factory=list,
        description="до 3 объяснений признаков без применения ИИ",
    )

    @field_validator("observations", "quotes", "alternative_explanations", mode="before")
    @classmethod
    def _cap(cls, v: object) -> object:
        return v[:5] if isinstance(v, list) else v


def _judge(model_text: str, *, fast: bool) -> SignalDetail:
    try:
        out = get_client(fast=fast).complete_json(
            system=SYSTEM_AI_JUDGE,
            user=(
                "Оцени, насколько текст ниже похож на сгенерированный языковой моделью.\n"
                "Учти: это учебная работа по продуктовому менеджменту, деловой стиль "
                "для неё естественен.\n\n"
                f"ТЕКСТ:\n{model_text}"
            ),
            schema=_JudgeOut,
            purpose="detect.ai_judge",
        )
    except LLMFailure as exc:
        # Ревьюеру нужен факт «сигнал недоступен», а не трассировка Pydantic:
        # полный текст ошибки со ссылками на документацию занимал в панели
        # больше места, чем все остальные сигналы вместе. Подробности
        # остаются в логе и в трассе ревью.
        log.warning("судья по генИИ недоступен: %s", exc.reason)
        return SignalDetail(
            code="judge", title="Суждение модели", available=False,
            detail=(
                "Модель не вернула разбираемый ответ, сигнал не учтён. "
                "Итоговая уверенность снижена. Подробности — в трассе выполнения."
            ),
        )

    d: _JudgeOut = out.data
    return SignalDetail(
        code="judge", title="Суждение модели", value=round(float(d.likelihood), 3),
        weight=0.35,
        detail=f"Оценка модели: {d.likelihood:.2f}.",
        evidence=[*d.observations[:5], *(f"цитата: {q}" for q in d.quotes[:3])],
    )


# ── сборка ────────────────────────────────────────────────────────────────────

_DECLARATION = (
    "использовал ии", "использовала ии", "с помощью ии", "chatgpt", "нейросет",
    "gigachat", "гигачат", "yandexgpt", "claude", "языковой модел", "llm",
    "ии-инструмент", "сгенерирован",
)


def detect_ai_text(doc: Document, *, model_text: str = "", fast: bool = False) -> AISignal:
    """Собирает ансамбль сигналов в рекомендацию."""
    signals = [
        _stylometry(doc.text),
        _format_artifacts(doc),
        _metadata(doc),
        _judge(model_text or doc.numbered_text(), fast=fast),
    ]

    available = [s for s in signals if s.available and s.value is not None]
    unavailable = [s for s in signals if not s.available]

    if available:
        total_weight = sum(s.weight for s in available)
        score = sum(s.value * s.weight for s in available) / total_weight  # type: ignore[operator]
    else:
        score = 0.0

    # Уверенность зависит от полноты ансамбля, а не только от величины скора.
    coverage = sum(s.weight for s in available)
    if not available:
        confidence = Confidence.UNAVAILABLE
    elif coverage >= 0.75 and (score >= 0.65 or score <= 0.2):
        confidence = Confidence.HIGH
    elif coverage >= 0.5:
        confidence = Confidence.MEDIUM
    else:
        confidence = Confidence.LOW

    grounds = [f"{s.title}: {s.detail}" for s in available if s.value and s.value > 0.3]
    if not grounds:
        grounds = ["Выраженных признаков генеративного происхождения не обнаружено."]

    limitations = [
        "Сигнал вероятностный и не является доказательством нарушения. "
        "Решение принимает ревьюер.",
        "Оценка не влияет на балл автоматически — только на приоритет ручной проверки.",
        "Метод не отличает текст, написанный с помощью ИИ и затем переработанный "
        "студентом, от полностью самостоятельного.",
    ]
    limitations += [f"{s.title}: {s.detail}" for s in unavailable]
    if doc.images:
        limitations.append(
            f"В работе {len(doc.images)} изображени(е/я) — их содержимое не анализировалось."
        )

    low = doc.text.lower()
    declared = any(m in low for m in _DECLARATION)
    if declared:
        grounds.append(
            "В работе есть декларация об использовании ИИ — условие задания это допускает "
            "при явном описании способа применения."
        )

    return AISignal(
        score=round(min(max(score, 0.0), 1.0), 3),
        confidence=confidence,
        signals=signals,
        grounds=grounds,
        limitations=limitations,
        declaration_found=declared,
    )
