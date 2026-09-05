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


def iso(dt: datetime | None) -> str | None:
    """Дата для API — всегда с явным смещением.

    `datetime.isoformat()` по прочитанной из SQLite дате даёт строку без
    пояса: `2026-09-07T11:30:31.033275`. Браузер обязан трактовать такую
    строку как **местное** время, поэтому сервер имел в виду 11:30 UTC, а
    пользователь в Москве читал 11:30 по Москве — расхождение в три часа,
    из-за которого время подтверждения и обратный отсчёт до дедлайна
    показывали не тот момент. С `+00:00` неоднозначности нет.
    """
    aware = as_utc(dt)
    return aware.isoformat() if aware else None


def local(dt: datetime | None) -> datetime | None:
    """Дата в часовом поясе машины — только для текста, который читает человек.

    Контур офлайн и однопользовательский: браузер и сервер работают на
    одной машине, поэтому местный пояс сервера — это пояс читателя.
    Сравнения и хранение остаются в UTC; локальное время появляется
    исключительно в готовых к показу строках вроде «Сдано 05.09 14:30».
    """
    aware = as_utc(dt)
    return aware.astimezone() if aware else None


def tz_name() -> str:
    """Подпись часового пояса машины: `UTC+3`.

    Именно смещение, а не системное название: Windows отдаёт в `%Z` строку
    вида «RTZ 2 (зима)», которая в тексте «Сдано 05.09 08:30 RTZ 2 (зима)»
    читается как мусор и ничего не сообщает.
    """
    offset = datetime.now().astimezone().utcoffset()
    if offset is None:
        return "UTC"
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    hours, mins = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours}" + (f":{mins:02d}" if mins else "")