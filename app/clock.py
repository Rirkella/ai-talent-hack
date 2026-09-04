"""Время: единственный источник «сейчас» и приведение дат к UTC.

Зачем отдельный модуль, а не `datetime.now()` по месту:

1. **Одна точка подмены в тестах.** Сроки проверяются на переходах между
   состояниями, и подменять время нужно в одном месте, а не сдвигать
   системные часы. Часы на демонстрации не подкручиваются вообще —
   двигаются сами дедлайны.

2. **SQLite не хранит часовой пояс.** Дата, записанная как aware, читается
   обратно как naive. Любое вычитание такой даты из `datetime.now(UTC)`
   падает с `TypeError: can't subtract offset-naive and offset-aware
   datetimes`. Ошибка проявляется не сразу, а после первого перезапуска
   приложения, поэтому приведение к UTC обязано быть общим и применяться
   на каждой границе с базой.
"""

from __future__ import annotations

from datetime import datetime, timezone


def now() -> datetime:
    """Текущее время в UTC. Всегда aware."""
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """Приводит дату к aware-UTC. `None` пропускает без изменений.

    Naive-дата трактуется как UTC: именно в UTC приложение всё пишет,
    а часовой пояс теряет только SQLite при чтении.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def seconds_between(later: datetime | None, earlier: datetime | None) -> float | None:
    """Разница в секундах с безопасным приведением обеих дат."""
    a, b = as_utc(later), as_utc(earlier)
    if a is None or b is None:
        return None
    return (a - b).total_seconds()
