"""Тесты шины событий.

Первый тест закрывает конкретную поломку: `Subscriber` был обычным
`@dataclass`, из-за чего Python выставлял `__hash__ = None`, и хранение
в `set` падало с `TypeError: unhashable type`. Внешне это выглядело как
«интерфейс не обновляется»: SSE-соединение обрывалось, браузер молча
переподключался в цикле, а причина видна была только в логе сервера.
"""

from __future__ import annotations

import asyncio

import pytest

from app.events import EventBus, Subscriber


def test_subscriber_is_hashable() -> None:
    """Подписчик должен храниться в set и различаться по идентичности."""
    a = Subscriber(user_id="u1", role="reviewer")
    b = Subscriber(user_id="u1", role="reviewer")
    holder = {a, b}
    # Одинаковые поля, но это две разные вкладки — оба должны остаться.
    assert len(holder) == 2


@pytest.mark.asyncio
async def test_publish_reaches_only_addressed_users() -> None:
    """Адресация по пользователю: чужие события не приходят."""
    bus = EventBus()
    alice = await bus.subscribe("alice", "reviewer")
    bob = await bus.subscribe("bob", "student")

    sent = await bus.publish("review_ready", {"score": 8}, users=["alice"])
    assert sent == 1
    assert alice.queue.qsize() == 1
    assert bob.queue.qsize() == 0


@pytest.mark.asyncio
async def test_publish_by_role() -> None:
    bus = EventBus()
    reviewer = await bus.subscribe("r", "reviewer")
    student = await bus.subscribe("s", "student")

    await bus.publish("deadline_changed", {"state": "late_penalty"}, roles=["student"])
    assert student.queue.qsize() == 1
    assert reviewer.queue.qsize() == 0


@pytest.mark.asyncio
async def test_broadcast_reaches_everyone() -> None:
    bus = EventBus()
    subs = [await bus.subscribe(f"u{i}", "reviewer") for i in range(3)]
    assert await bus.publish("demo_reset", {}) == 3
    assert all(s.queue.qsize() == 1 for s in subs)


@pytest.mark.asyncio
async def test_slow_subscriber_does_not_block_others() -> None:
    """Переполненная очередь не имеет права остановить рассылку.

    Клиент, который перестал читать, теряет самые старые события, но
    остальные подписчики продолжают получать всё.
    """
    bus = EventBus()
    slow = await bus.subscribe("slow", "reviewer")
    fast = await bus.subscribe("fast", "reviewer")

    # Забиваем очередь медленного подписчика до предела.
    while not slow.queue.full():
        slow.queue.put_nowait(("filler", "{}"))

    sent = await bus.publish("review_ready", {"score": 1})
    assert sent == 2, "рассылка должна дойти до обоих"
    assert fast.queue.qsize() == 1
    assert slow.queue.full(), "у медленного вытеснено старое событие, размер не вырос"


@pytest.mark.asyncio
async def test_unsubscribe_removes_subscriber() -> None:
    bus = EventBus()
    sub = await bus.subscribe("u", "reviewer")
    assert bus.subscriber_count == 1
    await bus.unsubscribe(sub)
    assert bus.subscriber_count == 0
    assert await bus.publish("x", {}) == 0


@pytest.mark.asyncio
async def test_payload_is_json_with_cyrillic_intact() -> None:
    """Кириллица не должна экранироваться в \\uXXXX — её читают в интерфейсе."""
    import json

    bus = EventBus()
    sub = await bus.subscribe("u", "reviewer")
    await bus.publish("notification", {"title": "Скоро дедлайн"})
    _, payload = await asyncio.wait_for(sub.queue.get(), timeout=1)
    assert "Скоро дедлайн" in payload
    assert json.loads(payload)["title"] == "Скоро дедлайн"
