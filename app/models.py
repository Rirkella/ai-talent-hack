"""Модель данных.

SQLite + SQLAlchemy 2.0 async. Выбор продиктован ограничением «ноль
инфраструктуры»: демо — один процесс `uvicorn`, без сервера БД, без Docker.

Хранение результатов ревью — намеренно в JSON-колонках. Структура результата
эволюционирует вместе с пайплайном, а миграции на хакатоне обходятся дороже,
чем гибкость. Всё, по чему идёт фильтрация и сортировка (баллы, статусы,
сроки), вынесено в обычные колонки.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ── перечисления ──────────────────────────────────────────────────────────────


class Role(StrEnum):
    COORDINATOR = "coordinator"  # методист
    REVIEWER = "reviewer"
    STUDENT = "student"


class SubmissionStatus(StrEnum):
    UPLOADED = "uploaded"          # загружена, не распределена
    ASSIGNED = "assigned"          # назначена ревьюеру
    IN_REVIEW = "in_review"        # идёт предварительное ревью
    AI_REVIEWED = "ai_reviewed"    # предварительное ревью готово
    CONFIRMED = "confirmed"        # ревьюер подтвердил результат
    RETURNED = "returned"          # отправлена на доработку
    FAILED = "failed"              # ревью не удалось


class DeadlineState(StrEnum):
    """Состояния из правила условия. Переходы считаются по реальному времени."""

    ON_TIME = "on_time"            # в срок
    DUE_SOON = "due_soon"          # скоро дедлайн
    LATE_PENALTY = "late_penalty"  # просрочено, принимается со штрафом −1
    LATE_ZERO = "late_zero"        # просрочено окончательно, 0 баллов


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


# ── таблицы ───────────────────────────────────────────────────────────────────


class User(Base):
    """Участник процесса: методист, ревьюер или студент.

    Вход по логину и паролю. Пароль хранится не текстом, а как
    `pbkdf2_hmac(sha256)` с индивидуальной солью — даже у локального
    демо-стенда нет причин держать пароли в открытом виде.

    Это не полноценная система аутентификации: нет ни блокировки после
    подбора, ни срока действия сессии, ни двух факторов. Она отвечает на
    вопрос «кто вы», а не защищает контур — контур защищён тем, что
    приложение вообще не выходит наружу.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32), index=True)
    email: Mapped[str] = mapped_column(String(200), default="")

    # Логин латиницей: кириллица в поле ввода на защите — лишний риск
    # раскладки. Пустой логин означает пользователя, заведённого до
    # появления входа по паролю: такому вход закрыт до задания логина.
    login: Mapped[str] = mapped_column(String(64), default="", index=True)
    password_hash: Mapped[str] = mapped_column(String(255), default="")

    # Для ревьюера: сколько работ он берёт за цикл проверки.
    capacity: Mapped[int] = mapped_column(Integer, default=10)
    # Направления, которые ревьюер компетентен проверять.
    competencies: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Assignment(Base):
    """Домашнее задание: условие, рубрика, сроки."""

    __tablename__ = "assignments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(400))
    track: Mapped[str] = mapped_column(String(64), index=True)
    course: Mapped[str] = mapped_column(String(200), default="")
    homework_no: Mapped[int] = mapped_column(Integer, default=1)
    channel: Mapped[str] = mapped_column(String(32), default="stepik")

    condition_path: Mapped[str] = mapped_column(String(500), default="")
    condition_sha256: Mapped[str] = mapped_column(String(64), default="")

    # Рубрика целиком — модель Pydantic в JSON.
    rubric: Mapped[dict] = mapped_column(JSON, default=dict)
    rubric_approved: Mapped[bool] = mapped_column(Boolean, default=False)

    # Три срока из правила условия. Управляются панелью методиста.
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hard_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # За сколько секунд до срока показывать «скоро дедлайн».
    due_soon_lead_s: Mapped[int] = mapped_column(Integer, default=3600)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    submissions: Mapped[list[Submission]] = relationship(back_populates="assignment")


class Submission(Base):
    """Работа студента."""

    __tablename__ = "submissions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignments.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    reviewer_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )

    file_path: Mapped[str] = mapped_column(String(500))
    file_name: Mapped[str] = mapped_column(String(300))
    file_sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    # Тег типа работы обязателен при загрузке: он не даёт молча положить
    # работу по DS в поток по продуктовому фроду.
    track: Mapped[str] = mapped_column(String(64), index=True)
    # Что определила система сама. Расхождение показывается предупреждением,
    # но решение остаётся за человеком.
    detected_track: Mapped[str] = mapped_column(String(64), default="")

    status: Mapped[str] = mapped_column(String(32), default=SubmissionStatus.UPLOADED, index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Повторная сдача после доработки. Версия — сквозной номер попытки,
    # `previous_id` ведёт к предыдущей. Прошлая версия не удаляется и не
    # переписывается: сравнение «что изменилось» — половина смысла повторного
    # ревью, а перезапись файла это сравнение уничтожает.
    version: Mapped[int] = mapped_column(Integer, default=1)
    previous_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Работа, у которой есть более новая версия, уходит из очереди ревьюера,
    # но остаётся в истории студента.
    superseded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # Состояние по срокам и вытекающий штраф. Пересчитывается планировщиком.
    deadline_state: Mapped[str] = mapped_column(String(32), default=DeadlineState.ON_TIME)
    late_penalty: Mapped[float] = mapped_column(Float, default=0.0)

    @property
    def has_file(self) -> bool:
        """Есть ли что проверять.

        Строки без файла — это нагрузка прошлого потока: они занимают
        ёмкость ревьюера и нужны, чтобы распределение выравнивало
        реальный перекос. Проверять в них нечего, и попытка запустить
        ревью раньше давала невнятный отказ «Формат '' не поддерживается».
        """
        from pathlib import Path as _Path

        return bool(self.file_path) and _Path(self.file_path).exists()

    # Результат предварительного ревью целиком.
    review: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ai_score: Mapped[float] = mapped_column(Float, default=0.0)
    priority_index: Mapped[float] = mapped_column(Float, default=0.0, index=True)

    # Итог после человека. Именно он публикуется студенту.
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_feedback: Mapped[str] = mapped_column(Text, default="")
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Согласился ли ревьюер с предварительным баллом без правки — метрика
    # качества автоматики.
    score_edited: Mapped[bool] = mapped_column(Boolean, default=False)

    # Вердикт человека по сигналу генИИ: confirmed | rejected | None.
    ai_verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_verdict_comment: Mapped[str] = mapped_column(Text, default="")

    assignment: Mapped[Assignment] = relationship(back_populates="submissions")


class Job(Base):
    """Задача очереди ревью.

    Таблица, а не только `asyncio.Queue`: после перезапуска процесса должно
    быть видно, какие работы остались непроверенными.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.QUEUED, index=True)
    stage: Mapped[str] = mapped_column(String(200), default="")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Notification(Base):
    """Уведомление внутри приложения.

    Внешних каналов нет намеренно: контур офлайн, а Telegram и SMTP
    потребовали бы выхода в сеть. Доставка — через SSE.
    """

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    submission_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ActionLog(Base):
    """Журнал действий — основа метрики «число ручных действий».

    Кейс требует измеримых показателей эффекта. Считать их по памяти нельзя,
    поэтому фактические действия пользователей пишутся сюда, а базовая линия
    берётся из семи шагов процесса AS-IS.
    """

    __tablename__ = "action_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(200), default="")
    # Сколько ручных действий заменило это одно действие в интерфейсе.
    manual_actions_saved: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ScoringPreset(Base):
    """Именованный набор весов и тумблеров формулы приоритета.

    Пресет хранится в базе, а не в памяти процесса: смысл пресета в том,
    что настройку «строгий поток» можно применить завтра и на другом
    задании, а не только в текущей сессии методиста.

    Конфигурация лежит одним JSON, а не разложена по колонкам: состав весов
    задаётся `ScoringConfig` и будет меняться, а превращать каждое изменение
    формулы в миграцию базы — плохой обмен.
    """

    __tablename__ = "scoring_presets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # Встроенные пресеты нельзя удалить — иначе методист на демо останется
    # без точки возврата к значениям по умолчанию.
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
