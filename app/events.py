"""Шина событий: одна SSE-подписка на всё приложение.

Живая синхронизация между тремя вкладками: методист назначил — у ревьюера
появилось; ревьюер подтвердил — студент увидел; наступил срок — пинг обоим.
Без перезагрузок и без опроса сервера.

Выбор SSE вместо WebSocket: поток односторонний (сервер → клиент), а SSE
переподключается сам и не требует отдельного протокола поверх HTTP.

Адресация — по ролям и пользователям. Событие получает только тот, кому оно
предназначено: студент не должен видеть внутренние флаги чужих работ.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# Если клиент не читает, очередь не должна расти бесконечно.
QUEUE_MAX = 256


# eq=False обязательно: подписчики хранятся в set и различаются по
# идентичности, а не по значению. У обычного @dataclass с eq=True
# Python выставляет __hash__ = None, и set его не принимает —
# SSE-подписка падала с TypeError: unhashable type.
# Два подписчика с одинаковыми user_id и role — это две разные вкладки,
# и сравнивать их по полям было бы неверно.
@dataclass(eq=False)
class Subscriber:
    user_id: str
    role: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAX))


class EventBus:
    """Публикация событий подписчикам."""

    def __init__(self) -> None:
        self._subs: set[Subscriber] = set()
        self._lock = asyncio.Lock()
        # Взводится при остановке приложения. Открытые SSE-потоки живут в
        # цикле ожидания и по обычному Ctrl+C не завершались: uvicorn ждёт,
        # когда закроются соединения, а они не закрывались, пока открыта
        # хоть одна вкладка. Приходилось убивать процесс.
        self._closing = asyncio.Event()

    @property
    def closing(self) -> asyncio.Event:
        """Событие «приложение останавливается» — его ждут SSE-потоки."""
        return self._closing

    async def shutdown(self) -> None:
        """Просит открытые потоки завершиться и будит их немедленно."""
        self._closing.set()
        async with self._lock:
            targets = list(self._subs)
        for sub in targets:
            # Пустое событие только чтобы снять поток с ожидания очереди:
            # проверку `closing` он сделает сразу после пробуждения.
            with contextlib.suppress(asyncio.QueueFull):
                sub.queue.put_nowait(("shutdown", "{}"))

    async def subscribe(self, user_id: str, role: str) -> Subscriber:
        sub = Subscriber(user_id=user_id, role=role)
        async with self._lock:
            self._subs.add(sub)
        log.debug("подписка: %s (%s), всего %d", user_id, role, len(self._subs))
        return sub

    async def unsubscribe(self, sub: Subscriber) -> None:
        async with self._lock:
            self._subs.discard(sub)

    async def publish(
        self,
        event: str,
        data: dict[str, Any],
        *,
        users: list[str] | None = None,
        roles: list[str] | None = None,
    ) -> int:
        """Отправляет событие. `users`/`roles` не заданы — получают все.

        Возвращает число доставленных копий.
        """
        payload = json.dumps(
            {"event": event, "at": datetime.now(timezone.utc).isoformat(), **data},
            ensure_ascii=False,
            default=str,
        )
        sent = 0
        async with self._lock:
            targets = list(self._subs)
        for sub in targets:
            if users is not None and sub.user_id not in users:
                continue
            if roles is not None and sub.role not in roles:
                continue
            try:
                sub.queue.put_nowait((event, payload))
                sent += 1
            except asyncio.QueueFull:
                # Медленный клиент не имеет права тормозить остальных:
                # роняем самое старое событие и кладём новое.
                try:
                    sub.queue.get_nowait()
                    sub.queue.put_nowait((event, payload))
                    sent += 1
                except Exception:  # noqa: BLE001
                    log.warning("подписчик %s переполнен, событие потеряно", sub.user_id)
        return sent

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


bus = EventBus()
