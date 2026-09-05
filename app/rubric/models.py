"""Модель рубрики — набора критериев оценивания.

Рубрика приходит из трёх источников: автоизвлечение из условия, ручной
редактор, загрузка отдельного файла требований. Все три дают одну и ту же
структуру, поэтому ниже по течению разницы нет.

Инвариант: **ревью не стартует по неутверждённой рубрике**. Автоизвлечение —
это черновик для человека, а не источник истины. Ошибка в рубрике искажает
все последующие баллы, поэтому подтверждение обязательно.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator, model_validator


class Criterion(BaseModel):
    """Один критерий оценивания."""

    id: str = Field(description="краткий идентификатор, латиницей")
    name: str = Field(description="название критерия из условия")
    max_score: float = Field(ge=0, description="максимальный балл")
    min_score: float = Field(default=0.0, ge=0)
    # Дословная выдержка «идеального результата» из условия. Уходит в промпт
    # как эталон, поэтому переписывать своими словами нельзя.
    requirements: str = Field(default="", description="что требуется для полного балла")
    # Наблюдаемые признаки: помогают модели искать конкретику, а не рассуждать.
    indicators: list[str] = Field(default_factory=list)
    weight: float = Field(default=1.0, ge=0, description="вес в итоговой формуле")

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        return v.strip().lower().replace(" ", "_")

    @model_validator(mode="after")
    def _bounds(self) -> Criterion:
        if self.min_score > self.max_score:
            raise ValueError(f"min_score {self.min_score} > max_score {self.max_score}")
        return self


class Deadlines(BaseModel):
    """Сроки и правило штрафа — из условия задания.

    Значения по умолчанию соответствуют ДЗ «Карта рисков продукта»:
    досдача в течение суток стоит −1 балл, после — работа оценивается в ноль.
    """

    due_at: datetime | None = None
    hard_due_at: datetime | None = None
    review_due_at: datetime | None = None
    late_penalty: float = Field(default=1.0, ge=0, description="штраф за досдачу, в баллах")
    late_window_hours: int = Field(default=24, ge=0, description="окно досдачи после срока")
    review_window_days: int = Field(default=7, ge=0)
    # Балл после жёсткого дедлайна. Ноль — правило из условия.
    score_after_hard_due: float = 0.0


class Rubric(BaseModel):
    """Рубрика задания целиком."""

    assignment_title: str = ""
    course: str = ""
    track: str = Field(default="", description="направление: product_fraud, tech_QA, …")
    criteria: list[Criterion] = Field(default_factory=list)
    deadlines: Deadlines = Field(default_factory=Deadlines)

    # Формальные требования в сыром виде — переносятся в FormalRequirements.
    max_pages: int | None = None
    body_font: str | None = None
    body_size_pt: float | None = None
    table_font: str | None = None
    table_size_pt: float | None = None
    line_spacing_min: float | None = None
    line_spacing_max: float | None = None
    # Допустимые форматы файла решения. Пустой список означает «условие
    # формат не ограничивает» — а не «разрешены DOCX и PDF».
    allowed_formats: list[str] = Field(default_factory=list)
    requires_ai_declaration: bool = True

    # ── происхождение и утверждение ──────────────────────────────────────
    source: str = Field(default="manual", description="extracted | manual | file")
    source_sha256: str = ""
    approved: bool = False
    approved_by: str = ""
    approved_at: datetime | None = None
    notes: str = ""

    @property
    def total_max(self) -> float:
        return sum(c.max_score for c in self.criteria)

    def criterion(self, cid: str) -> Criterion | None:
        return next((c for c in self.criteria if c.id == cid), None)

    def fingerprint(self) -> str:
        """Отпечаток критериев — по чему оценивали работу.

        Считается только по тому, что влияет на балл: идентификаторы,
        названия, максимумы, веса, требования. Утверждение, автор и время
        в отпечаток не входят — от них оценка не зависит, а иначе он менялся
        бы при каждом нажатии «Утвердить».

        Нужен, чтобы отличить результат, посчитанный по нынешним критериям,
        от результата по прежним. Методист правит критерии и заново
        утверждает их, а проверенные до этого работы продолжают показывать
        баллы, посчитанные по другой рубрике, — и по виду это не отличить.
        """
        import hashlib
        import json

        payload = json.dumps(
            [
                {
                    "id": c.id, "name": c.name, "max_score": c.max_score,
                    "weight": c.weight, "requirements": c.requirements,
                    "indicators": list(c.indicators),
                }
                for c in self.criteria
            ],
            ensure_ascii=False, sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def approve(self, by: str) -> None:
        self.approved = True
        self.approved_by = by
        self.approved_at = datetime.now(timezone.utc)

    def to_formal_requirements(self):  # noqa: ANN201 — избегаем циклического импорта
        """Переносит формальные требования рубрики в конфигурацию слоя А.

        Незаполненные поля остаются на значениях по умолчанию — курс, который
        их не задаёт, не должен получать проверки с чужими порогами.
        """
        from app.formal.checks import FormalRequirements

        # Незаданное поле передаётся как `None` и означает «в условии нет
        # такого требования». Раньше здесь подставлялись значения по умолчанию
        # из ДЗ по продуктовому фроду, и любой другой курс молча получал чужие
        # пороги: работа по системному дизайну, которую условие разрешает
        # сдавать в Markdown, теряла балл за «формат не DOCX».
        return FormalRequirements(
            max_pages=self.max_pages,
            body_font=self.body_font,
            body_size_pt=self.body_size_pt,
            table_font=self.table_font,
            table_size_pt=self.table_size_pt,
            line_spacing_min=self.line_spacing_min,
            line_spacing_max=self.line_spacing_max,
            allowed_formats=tuple(self.allowed_formats or ()),
            requires_ai_declaration=self.requires_ai_declaration,
        )


class RubricNotApproved(RuntimeError):
    """Попытка запустить ревью по неутверждённой рубрике."""
