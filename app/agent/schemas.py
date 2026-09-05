"""Схемы результатов ревью.

Всё, что возвращает модель, описано здесь и проверяется Pydantic. Ollama
принимает `response_format` и игнорирует его, поэтому именно эти схемы —
фактическое принуждение структуры, а не украшение.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


# Потолки на объём ответа модели. Не отвергают ответ, а обрезают его:
# см. валидаторы ниже. Значения подобраны так, чтобы ответ гарантированно
# укладывался в LLM_MAX_TOKENS и не обрывался на середине JSON.
QUOTE_MAX_CHARS = 300
EVIDENCE_MAX_ITEMS = 6
GAPS_MAX_ITEMS = 5


class EvidenceStatus(StrEnum):
    """Итог программной проверки цитаты.

    `VERIFIED` означает **дословное** вхождение и ничего больше. Раньше этот
    статус ставился по нечёткому сходству от 82 %, и внешний аудит показал,
    чем это кончается: цитата «Расчёт ROI выполнен, числовые данные
    отсутствуют» получала 93,9 % против исходного «Расчёт ROI **не**
    выполнен…», а «ROI составляет 90 процентов» — 97,1 % против «10
    процентов». То есть зелёная галочка подтверждала текст, которого в
    работе нет, причём с перевёрнутым смыслом. Нечёткое совпадение осталось,
    но теперь оно отдельный статус и требует взгляда человека.
    """

    VERIFIED = "verified"          # цитата дословно есть в указанном блоке
    APPROXIMATE = "approximate"    # похожий текст есть, но цитата не дословна
    WRONG_BLOCK = "wrong_block"    # дословно есть, но в другом блоке
    NOT_FOUND = "not_found"        # в работе такого текста нет
    NO_BLOCK = "no_block"          # модель не указала номер блока


class EvidenceIn(BaseModel):
    """Доказательство в том виде, в каком его возвращает модель.

    Схема намеренно узкая: только номер блока и цитата. Результаты проверки
    (`status`, `similarity`, `found_in_block`) сюда НЕ входят — их вычисляет
    код, и показывать их модели вредно вдвойне. Во-первых, они раздувают
    ответ: наблюдалась генерация, упиравшаяся в потолок `max_tokens` и
    обрывавшая JSON на середине. Во-вторых, модель начинала сама проставлять
    `"status": "verified", "similarity": 1.0` — то есть утверждать проверку,
    выполнить которую не может. Именно её мы и хотим у неё отобрать.
    """

    block: int = Field(description="номер блока §N, откуда взята цитата")
    quote: str = Field(description="дословная цитата из этого блока, 5–30 слов")

    @field_validator("quote", mode="before")
    @classmethod
    def _trim_quote(cls, v: object) -> object:
        """Слишком длинную цитату обрезаем, а не отвергаем.

        Отклонять весь ответ из-за многословной цитаты — плохой размен:
        цена ретрая десятки секунд, а нечёткий поиск всё равно ищет
        вхождение подстроки и на обрезанной цитате отработает.
        """
        return v[:QUOTE_MAX_CHARS] if isinstance(v, str) else v


class Evidence(EvidenceIn):
    """Доказательство после программной проверки."""

    # Заполняется кодом, не моделью.
    status: EvidenceStatus = EvidenceStatus.NOT_FOUND
    similarity: float = 0.0
    found_in_block: int | None = None

    @property
    def is_verified(self) -> bool:
        return self.status is EvidenceStatus.VERIFIED


class CriterionVerdict(BaseModel):
    """Оценка модели по одному критерию — до проверки доказательств."""

    score: float = Field(ge=0, description="балл в пределах максимума критерия")
    verdict: str = Field(description="краткий вывод, 1–2 предложения")
    # Ограничение сверху не косметическое: без него модель перечисляла
    # по 10–15 цитат, ответ упирался в потолок длины и обрывался на середине
    # JSON. Шести доказательств достаточно, чтобы обосновать балл.
    evidence: list[EvidenceIn] = Field(
        default_factory=list, description="до 6 цитат из работы, подтверждающих балл"
    )
    gaps: list[str] = Field(
        default_factory=list, description="чего не хватает до полного балла"
    )
    recommendation: str = Field(default="", description="рекомендация ревьюеру")

    @field_validator("evidence", mode="before")
    @classmethod
    def _cap_evidence(cls, v: object) -> object:
        """Лишние цитаты отбрасываем, ответ не отвергаем.

        Строгий `max_length` здесь означал бы полный ретрай из-за того, что
        модель проявила избыточное усердие. Первые шесть уже обосновывают балл.
        """
        return v[:EVIDENCE_MAX_ITEMS] if isinstance(v, list) else v

    @field_validator("gaps", mode="before")
    @classmethod
    def _cap_gaps(cls, v: object) -> object:
        return v[:GAPS_MAX_ITEMS] if isinstance(v, list) else v


class CriterionResult(BaseModel):
    """Оценка по критерию после проверки доказательств кодом."""

    criterion_id: str
    criterion_name: str
    max_score: float
    score: float
    # Вес критерия из рубрики. Хранится в самом результате, потому что балл
    # пересчитывается там, где рубрики под рукой нет: при наступлении срока
    # в планировщике и при подтверждении оценки человеком. Без веса эти
    # места считали сумму по-своему и расходились с первичным подсчётом.
    weight: float = 1.0
    # Балл, который поставила модель. Заполняется один раз при агрегации и
    # больше не меняется: `score` после правки ревьюером содержит решение
    # человека, а метрика согласия должна сравнивать с исходной оценкой
    # автоматики. `None` — у результатов, посчитанных до появления поля.
    ai_score: float | None = None
    verdict: str
    evidence: list[Evidence] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    recommendation: str = ""

    # Итог проверки цитат.
    evidence_verified: int = 0
    evidence_total: int = 0
    # True, если ни одна цитата не подтвердилась. Балл сохраняется, но
    # помечается как неподтверждённый и поднимает приоритет ручной проверки:
    # молча занижать оценку по технической причине нельзя.
    unverified: bool = False
    # Шаг не выполнился — критерий остался без оценки модели.
    failed: bool = False
    error: str = ""

    @property
    def confidence(self) -> float:
        """Доля подтверждённых цитат. Без цитат — ноль."""
        return self.evidence_verified / self.evidence_total if self.evidence_total else 0.0


class TraceStep(BaseModel):
    """Запись о шаге пайплайна.

    Упавший шаг попадает сюда и ревью продолжается. Трасса показывается
    ревьюеру: он должен видеть, что именно система успела проверить.
    """

    name: str
    ok: bool
    duration_ms: float
    detail: str = ""
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReviewResult(BaseModel):
    """Полный результат предварительного ревью одной работы."""

    submission_id: str = ""
    source_name: str = ""
    # Отпечаток критериев, по которым считался этот результат. Методист
    # правит критерии и заново их утверждает, а работы, проверенные до
    # правки, продолжают показывать баллы по прежней рубрике — отличить их
    # по виду было невозможно.
    rubric_fingerprint: str = ""

    criteria: list[CriterionResult] = Field(default_factory=list)
    formal: list[dict] = Field(default_factory=list)
    ai_signal: dict | None = None
    similarity: dict | None = None

    preliminary_score: float = 0.0
    max_score: float = 0.0
    penalties: list[dict] = Field(default_factory=list)

    # Индекс приоритета ручной проверки. Считается отдельно от балла:
    # кейс запрещает генеративной проверке влиять на оценку автоматически.
    priority_index: float = 0.0
    priority_reasons: list[str] = Field(default_factory=list)

    student_feedback: str = ""
    reviewer_summary: str = ""

    trace: list[TraceStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0
    model: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.trace)

    @property
    def failed_steps(self) -> list[str]:
        return [s.name for s in self.trace if not s.ok]
