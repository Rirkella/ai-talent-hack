"""Сроки и штрафы по правилу из условия задания.

Правило взято дословно из ДЗ «Карта рисков продукта»:

* срок сдачи — **мягкий дедлайн**;
* досдача в течение 1 дня после него — **штраф −1 балл**;
* работы, не сданные в срок, — **0 баллов** (жёсткий дедлайн);
* проверка начинается после дедлайна и занимает не более 7 дней.

Часы **не подкручиваются**. Время всегда реальное, управляются сами сроки:
на демонстрации мягкий дедлайн ставится на +10 секунд, жёсткий на +40, и
переходы происходят на глазах. Один и тот же механизм обслуживает и
реальные сроки в днях, и десятисекундные на демо.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.clock import as_utc as _aware, local as _local, now as now_utc, tz_name
from app.models import DeadlineState


@dataclass
class DeadlineStatus:
    """Состояние работы по срокам и вытекающий штраф."""

    state: DeadlineState
    penalty: float
    label: str
    detail: str
    # Секунды до следующего перехода. Нужны и обратному отсчёту в интерфейсе,
    # и планировщику для точного триггера.
    seconds_to_next: float | None = None
    next_state: DeadlineState | None = None
    # Балл принудительно обнуляется правилом жёсткого дедлайна.
    forces_zero: bool = False


def evaluate(
    *,
    submitted_at: datetime | None,
    due_at: datetime | None,
    hard_due_at: datetime | None,
    late_penalty: float = 1.0,
    due_soon_lead_s: int = 3600,
    now: datetime | None = None,
) -> DeadlineStatus:
    """Определяет состояние работы по срокам.

    Момент отсчёта — время сдачи, если работа сдана, иначе текущее время.
    Это важно: уже сданная вовремя работа не должна «просрочиться» из-за
    того, что ревьюер открыл её позже дедлайна.
    """
    now = _aware(now) or now_utc()
    due_at = _aware(due_at)
    hard_due_at = _aware(hard_due_at)
    submitted_at = _aware(submitted_at)

    if due_at is None:
        return DeadlineStatus(
            DeadlineState.ON_TIME, 0.0, "срок не задан",
            "Мягкий дедлайн не установлен — штрафы не применяются.",
        )

    if hard_due_at is None:
        hard_due_at = due_at + timedelta(hours=24)

    reference = submitted_at or now
    is_submitted = submitted_at is not None

    # ── работа сдана: состояние зафиксировано моментом сдачи ─────────────
    if is_submitted:
        if reference <= due_at:
            return DeadlineStatus(
                DeadlineState.ON_TIME, 0.0, "сдано в срок",
                f"Сдано {_show(reference)}, до срока "
                f"{(due_at - reference).total_seconds() / 60:.0f} мин.",
            )
        if reference <= hard_due_at:
            return DeadlineStatus(
                DeadlineState.LATE_PENALTY, late_penalty,
                f"досдача, штраф −{late_penalty:g}",
                f"Сдано {_show(reference)}, после срока "
                f"{_show(due_at)}. По условию задания — штраф "
                f"−{late_penalty:g} балл.",
            )
        return DeadlineStatus(
            DeadlineState.LATE_ZERO, 0.0, "просрочено, 0 баллов",
            f"Сдано {_show(reference)}, после жёсткого срока "
            f"{_show(hard_due_at)}. По условию — 0 баллов.",
            forces_zero=True,
        )

    # ── работа не сдана: состояние живое и меняется со временем ──────────
    if now < due_at:
        left = (due_at - now).total_seconds()
        if left <= due_soon_lead_s:
            return DeadlineStatus(
                DeadlineState.DUE_SOON, 0.0, "скоро дедлайн",
                f"До срока {_human(left)}.",
                seconds_to_next=left, next_state=DeadlineState.LATE_PENALTY,
            )
        return DeadlineStatus(
            DeadlineState.ON_TIME, 0.0, "в срок",
            f"До срока {_human(left)}.",
            seconds_to_next=left - due_soon_lead_s, next_state=DeadlineState.DUE_SOON,
        )

    if now < hard_due_at:
        left = (hard_due_at - now).total_seconds()
        return DeadlineStatus(
            DeadlineState.LATE_PENALTY, late_penalty,
            f"просрочено, приём со штрафом −{late_penalty:g}",
            f"Мягкий срок прошёл. Досдача возможна ещё {_human(left)}, "
            f"балл будет снижен на {late_penalty:g}.",
            seconds_to_next=left, next_state=DeadlineState.LATE_ZERO,
        )

    return DeadlineStatus(
        DeadlineState.LATE_ZERO, 0.0, "просрочено окончательно, 0 баллов",
        f"Жёсткий срок {_show(hard_due_at)} прошёл. По условию — 0 баллов.",
        forces_zero=True,
    )



def _show(dt: datetime | None) -> str:
    """Момент времени для человека: местный пояс и его подпись.

    В тексте стояла UTC-дата без пояса — «Сдано 07.09 11:30». Рядом
    интерфейс показывал ту же дату из ISO-поля в местном времени, и два
    числа расходились на три часа. Контур локальный: пояс сервера и есть
    пояс читателя.
    """
    shown = _local(dt)
    return f"{shown:%d.%m %H:%M:%S} {tz_name()}" if shown else "—"


def _human(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} с"
    if seconds < 3600:
        return f"{seconds // 60} мин {seconds % 60} с"
    if seconds < 86400:
        return f"{seconds // 3600} ч {(seconds % 3600) // 60} мин"
    return f"{seconds // 86400} дн {(seconds % 86400) // 3600} ч"


def review_status(
    *, review_due_at: datetime | None, confirmed_at: datetime | None, now: datetime | None = None
) -> tuple[str, str, float | None]:
    """Состояние срока ПРОВЕРКИ. Основа метрики «количество просроченных проверок»."""
    now = _aware(now) or now_utc()
    review_due_at = _aware(review_due_at)
    confirmed_at = _aware(confirmed_at)

    if review_due_at is None:
        return "no_deadline", "срок проверки не задан", None
    if confirmed_at is not None:
        late = (confirmed_at - review_due_at).total_seconds()
        if late > 0:
            return "overdue_closed", f"проверено с опозданием на {_human(late)}", late
        return "done", f"проверено за {_human(-late)} до срока", late

    left = (review_due_at - now).total_seconds()
    if left < 0:
        return "overdue", f"проверка просрочена на {_human(-left)}", left
    if left < 86400:
        return "due_soon", f"до срока проверки {_human(left)}", left
    return "on_time", f"до срока проверки {_human(left)}", left


def transitions_for(
    *, due_at: datetime | None, hard_due_at: datetime | None, due_soon_lead_s: int = 3600
) -> list[tuple[datetime, DeadlineState]]:
    """Моменты переходов между состояниями — для точных триггеров планировщика.

    Планировщик ставит разовые задачи ровно на эти моменты, а не опрашивает
    базу в цикле. Секундный сторож остаётся страховкой на случай, если
    задача не сработала.
    """
    due_at = _aware(due_at)
    hard_due_at = _aware(hard_due_at)
    if due_at is None:
        return []
    if hard_due_at is None:
        hard_due_at = due_at + timedelta(hours=24)
    return [
        (due_at - timedelta(seconds=due_soon_lead_s), DeadlineState.DUE_SOON),
        (due_at, DeadlineState.LATE_PENALTY),
        (hard_due_at, DeadlineState.LATE_ZERO),
    ]
