"""Планировщик сроков и уведомлений.

Два механизма вместе, потому что по отдельности каждый ненадёжен:

* **Точные разовые триггеры** на моменты переходов между состояниями. Дают
  срабатывание секунда в секунду — именно это видно на демонстрации.
* **Секундный сторож** как страховка. Если триггер не поставили (работу
  загрузили после планирования) или он не сработал, сторож догонит состояние
  на следующем тике.

Уведомления доставляются только внутри приложения, через SSE. Внешних
каналов нет намеренно: контур офлайн, а Telegram или SMTP потребовали бы
выхода в сеть.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select

from app.db import session_scope
from app.events import bus
from app.models import (
    Assignment,
    DeadlineState,
    Notification,
    Submission,
    SubmissionStatus,
)
from app.notify.deadlines import evaluate, now_utc, review_status

log = logging.getLogger(__name__)

WATCHDOG_INTERVAL_S = 1.0

# Как часто методист получает сводку по потоку. Не секунда: сводка обязана
# быть редкой, иначе она перестаёт читаться и превращается в шум.
DIGEST_INTERVAL_S = 300.0


async def notify(
    session,  # noqa: ANN001
    *,
    user_id: str,
    kind: str,
    title: str,
    body: str = "",
    submission_id: str | None = None,
    severity: str = "info",
) -> Notification:
    """Создаёт уведомление и немедленно доставляет его по SSE."""
    n = Notification(
        id=str(uuid.uuid4()),
        user_id=user_id,
        kind=kind,
        title=title,
        body=body,
        submission_id=submission_id,
        severity=severity,
    )
    session.add(n)
    await session.flush()
    await bus.publish(
        "notification",
        {
            "id": n.id, "kind": kind, "title": title, "body": body,
            "severity": severity, "submission_id": submission_id,
        },
        users=[user_id],
    )
    return n


# Тексты переходов. Вынесены из логики, чтобы правка формулировки не требовала
# правки механизма.
_TRANSITION_TEXT: dict[DeadlineState, tuple[str, str, str]] = {
    DeadlineState.DUE_SOON: (
        "warning", "Скоро дедлайн",
        "Приближается срок сдачи «{title}». После него работа принимается со штрафом.",
    ),
    DeadlineState.LATE_PENALTY: (
        "warning", "Срок сдачи прошёл",
        "По «{title}» наступил срок сдачи. Досдача ещё возможна, но балл "
        "снижается на {penalty:g}.",
    ),
    DeadlineState.LATE_ZERO: (
        "error", "Жёсткий срок прошёл",
        "По «{title}» истёк жёсткий срок. По условию задания работа оценивается в 0 баллов.",
    ),
}


def apply_deadline_rules(submission: Submission, assignment: Assignment) -> "DeadlineStatus":  # noqa: F821
    """Приводит работу и сохранённый результат ревью в согласие со сроками.

    Единственное место, где правило срока превращается в число. Вызывается
    из трёх мест: при сохранении результата проверки, в тике планировщика и
    при подтверждении оценки человеком.

    Так пришлось сделать после находки аудита: штраф применял только
    планировщик и только в момент **смены** состояния. Повторный запуск
    ревью перезаписывал результат заново посчитанным баллом без штрафа, а
    планировщик на следующем тике видел, что состояние не изменилось, и
    ничего не трогал. Просроченная работа получала полный балл: 8 вместо 7.

    Функция идемпотентна — повторный вызов не вычитает штраф дважды.
    """
    status = evaluate(
        submitted_at=submission.submitted_at,
        due_at=assignment.due_at,
        hard_due_at=assignment.hard_due_at,
        late_penalty=float((assignment.rubric or {}).get("deadlines", {}).get("late_penalty", 1.0)),
        due_soon_lead_s=assignment.due_soon_lead_s,
    )

    submission.deadline_state = status.state.value
    submission.late_penalty = status.penalty

    if submission.review:
        from app.agent.schemas import ReviewResult
        from app.scoring.formula import apply_deadline_penalty

        try:
            result = ReviewResult.model_validate(submission.review)
        except ValidationError:
            # Результат сохранён другой версией схемы. Состояние срока
            # выставить всё равно надо: отказ целиком означал бы 500 на
            # подтверждении оценки из-за формы старой записи.
            log.warning("результат ревью %s не разобран, штраф не пересчитан", submission.id)
        else:
            apply_deadline_penalty(
                result,
                penalty=status.penalty,
                reason=status.detail,
                forces_zero=status.forces_zero,
            )
            # Присваивание нового объекта обязательно: SQLAlchemy не следит
            # за изменениями внутри JSON-колонки.
            submission.review = result.model_dump(mode="json")

    return status


async def refresh_submission_state(session, submission: Submission, assignment: Assignment) -> bool:  # noqa: ANN001
    """Пересчитывает состояние срока и штраф. True — состояние изменилось.

    Пересчёт выполняется **всегда**, а признак изменения нужен только для
    того, чтобы не слать уведомление о переходе повторно.
    """
    before_state = submission.deadline_state
    before_penalty = submission.late_penalty

    status = apply_deadline_rules(submission, assignment)
    changed = status.state.value != before_state or status.penalty != before_penalty
    if not changed:
        return False

    # Событие несёт предварительный балл — значит, оно для сотрудников.
    # Смена состояния срока сама по себе студенту видна: она приходит
    # уведомлением ниже, но уже без непубликованного числа.
    await bus.publish(
        "deadline_changed",
        {
            "submission_id": submission.id,
            "state": status.state.value,
            "label": status.label,
            "detail": status.detail,
            "penalty": status.penalty,
            "score": (submission.review or {}).get("preliminary_score"),
        },
        roles=["reviewer", "coordinator"],
    )
    await bus.publish(
        "deadline_changed",
        {
            "submission_id": submission.id,
            "state": status.state.value,
            "label": status.label,
            "detail": status.detail,
            "penalty": status.penalty,
        },
        roles=["student"],
    )

    if (text := _TRANSITION_TEXT.get(status.state)) is not None:
        severity, title, body = text
        body = body.format(title=assignment.title, penalty=status.penalty)
        for uid in filter(None, [submission.student_id, submission.reviewer_id]):
            await notify(
                session, user_id=uid, kind=f"deadline.{status.state.value}",
                title=title, body=body, submission_id=submission.id, severity=severity,
            )
    return True


class DeadlineScheduler:
    """Сторож сроков.

    Реализован простым асинхронным циклом, а не APScheduler: приложение уже
    живёт в event loop, тик раз в секунду ничего не стоит, а зависимость
    и её конфигурация обошлись бы дороже.
    """

    def __init__(
        self,
        interval_s: float = WATCHDOG_INTERVAL_S,
        digest_interval_s: float = DIGEST_INTERVAL_S,
    ) -> None:
        self.interval_s = interval_s
        self.digest_interval_s = digest_interval_s
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self.ticks = 0
        self.transitions = 0
        self._last_digest: datetime | None = None
        self._last_digest_state: tuple | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._loop(), name="deadline-scheduler")
            log.info("планировщик сроков запущен, тик %.1f с", self.interval_s)

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — сторож не имеет права умирать
                log.exception("тик планировщика упал")
            await asyncio.sleep(self.interval_s)

    async def tick(self) -> int:
        """Один проход. Возвращает число изменившихся работ."""
        self.ticks += 1
        changed = 0
        async with session_scope() as session:
            assignments = {
                a.id: a for a in (await session.execute(select(Assignment))).scalars()
            }
            if not assignments:
                return 0

            submissions = (await session.execute(select(Submission))).scalars().all()
            for sub in submissions:
                assignment = assignments.get(sub.assignment_id)
                if assignment is None:
                    continue
                if await refresh_submission_state(session, sub, assignment):
                    changed += 1
                    self.transitions += 1

            await self._check_review_deadlines(session, submissions, assignments)
            await self._send_digest(session, submissions, assignments)
            # Коммит безусловный. Раньше он стоял под `if changed`, и это был
            # не оптимизация, а ошибка: напоминания о сроке проверки и сводка
            # создаются в тиках, где ни одно состояние работы не меняется.
            # Такое уведомление уходило по SSE, но откатывалось вместе с
            # сессией — а значит, проверка «уже отправляли?» на следующем тике
            # снова не находила записи и слала его заново. Раз в секунду.
            await session.commit()
        return changed

    async def _send_digest(self, session, submissions, assignments) -> None:  # noqa: ANN001
        """Периодическая сводка по потоку методисту.

        Два предохранителя от спама. Первый — интервал: сводка не может
        приходить чаще, чем раз в `digest_interval_s`. Второй — содержимое:
        если с прошлой сводки цифры не изменились, новая не отправляется.
        Одного интервала мало — в спокойном потоке методист получал бы
        одинаковое сообщение каждые пять минут.
        """
        from app.models import Role, User

        now = now_utc()
        if self._last_digest is not None:
            if (now - self._last_digest).total_seconds() < self.digest_interval_s:
                return

        live = [s for s in submissions if not s.superseded]
        overdue_submissions = sum(
            1 for s in live
            if s.deadline_state in {DeadlineState.LATE_PENALTY, DeadlineState.LATE_ZERO}
        )
        unassigned = sum(1 for s in live if s.reviewer_id is None)
        awaiting = sum(
            1 for s in live
            if s.review is not None and s.status != SubmissionStatus.CONFIRMED
        )
        overdue_reviews = 0
        for s in live:
            a = assignments.get(s.assignment_id)
            if a is None or a.review_due_at is None:
                continue
            state, _, _ = review_status(
                review_due_at=a.review_due_at, confirmed_at=s.confirmed_at
            )
            if state == "overdue":
                overdue_reviews += 1

        state = (overdue_submissions, unassigned, awaiting, overdue_reviews)
        self._last_digest = now
        if state == self._last_digest_state or not any(state):
            self._last_digest_state = state
            return
        self._last_digest_state = state

        parts = []
        if unassigned:
            parts.append(f"не распределено: {unassigned}")
        if awaiting:
            parts.append(f"ждут подтверждения: {awaiting}")
        if overdue_submissions:
            parts.append(f"просрочены сдачи: {overdue_submissions}")
        if overdue_reviews:
            parts.append(f"просрочены проверки: {overdue_reviews}")

        coordinators = (
            await session.execute(
                select(User).where(User.role == Role.COORDINATOR, User.active)
            )
        ).scalars().all()
        for c in coordinators:
            await notify(
                session, user_id=c.id, kind="flow.digest",
                title="Сводка по потоку",
                body="; ".join(parts) + ".",
                severity="warning" if (overdue_submissions or overdue_reviews) else "info",
            )

    async def _check_review_deadlines(self, session, submissions, assignments) -> None:  # noqa: ANN001
        """Напоминания ревьюерам о сроке проверки.

        Уведомление шлётся один раз на переход, а не каждый тик: секундный
        сторож иначе завалил бы ревьюера сотнями одинаковых сообщений.
        """
        for sub in submissions:
            if sub.reviewer_id is None or sub.status == SubmissionStatus.CONFIRMED:
                continue
            assignment = assignments.get(sub.assignment_id)
            if assignment is None or assignment.review_due_at is None:
                continue

            state, text, _ = review_status(
                review_due_at=assignment.review_due_at, confirmed_at=sub.confirmed_at
            )
            if state not in {"overdue", "due_soon"}:
                continue

            kind = f"review.{state}"
            already = (
                await session.execute(
                    select(Notification).where(
                        Notification.user_id == sub.reviewer_id,
                        Notification.kind == kind,
                        Notification.submission_id == sub.id,
                    )
                )
            ).scalars().first()
            if already:
                continue

            await notify(
                session, user_id=sub.reviewer_id, kind=kind,
                title=("Проверка просрочена" if state == "overdue" else "Скоро срок проверки"),
                body=f"Работа «{sub.file_name}»: {text}.",
                submission_id=sub.id,
                severity="error" if state == "overdue" else "warning",
            )


scheduler = DeadlineScheduler()


def demo_deadlines(*, soft_s: int = 10, hard_s: int = 40) -> dict[str, datetime]:
    """Сроки для демонстрации: мягкий через 10 с, жёсткий через 40 с.

    Часы при этом остаются настоящими — сдвигаются только сами дедлайны.
    Тот же код обслуживает и реальные сроки в днях.
    """
    now = now_utc()
    return {
        "due_at": now + timedelta(seconds=soft_s),
        "hard_due_at": now + timedelta(seconds=hard_s),
        "review_due_at": now + timedelta(seconds=hard_s + 60),
    }
