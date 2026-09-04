"""Тесты планировщика: уведомления обязаны переживать тик.

Главный тест здесь — `test_review_reminder_is_sent_once_not_every_tick`.
Он закрывает конкретную поломку: коммит в конце тика стоял под условием
«изменилось хотя бы одно состояние работы». Напоминание о сроке проверки
и сводка методисту создаются как раз в тиках, где ничего не меняется, —
такое уведомление уходило по SSE, но откатывалось вместе с сессией.
Проверка «уже отправляли?» на следующем тике снова не находила записи
в базе, и ревьюер получал одно и то же сообщение раз в секунду.

Внешне это выглядело бы как «колокольчик сошёл с ума», а причина лежала
в одной строке с `if`.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.clock import now as now_utc
from app.models import (
    Assignment,
    Base,
    Notification,
    Role,
    Submission,
    SubmissionStatus,
    User,
)
from app.notify.scheduler import DeadlineScheduler


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """Отдельная временная база и подменённая фабрика сессий планировщика."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def scope():
        async with maker() as session:
            yield session

    monkeypatch.setattr("app.notify.scheduler.session_scope", scope)
    yield maker
    await engine.dispose()


async def _seed(maker, *, review_overdue: bool = True, reviewed: bool = False) -> str:
    """Одна работа, назначенная ревьюеру, со сроком проверки в прошлом."""
    now = now_utc()
    async with maker() as session:
        session.add(User(id="rev", name="Ревьюер", role=Role.REVIEWER))
        session.add(User(id="coord", name="Методист", role=Role.COORDINATOR))
        session.add(User(id="stu", name="Студент", role=Role.STUDENT))
        session.add(
            Assignment(
                id="asg", title="ДЗ", track="product_fraud",
                due_at=now - timedelta(days=3),
                hard_due_at=now - timedelta(days=2),
                review_due_at=(
                    now - timedelta(days=1) if review_overdue else now + timedelta(days=5)
                ),
            )
        )
        sub_id = str(uuid.uuid4())
        session.add(
            Submission(
                id=sub_id, assignment_id="asg", student_id="stu", reviewer_id="rev",
                file_path="", file_name="работа.docx", track="product_fraud",
                status=(
                    SubmissionStatus.AI_REVIEWED if reviewed else SubmissionStatus.ASSIGNED
                ),
                review={"preliminary_score": 7.0} if reviewed else None,
                submitted_at=now - timedelta(days=4),
            )
        )
        await session.commit()
    return sub_id


async def _notifications(maker, kind: str) -> list[Notification]:
    async with maker() as session:
        rows = await session.execute(
            select(Notification).where(Notification.kind == kind)
        )
        return list(rows.scalars())


@pytest.mark.asyncio
async def test_review_reminder_is_sent_once_not_every_tick(db) -> None:
    await _seed(db)
    # Сводка здесь не при чём: интервал заведомо больше длительности теста.
    scheduler = DeadlineScheduler(digest_interval_s=10_000)

    for _ in range(3):
        await scheduler.tick()

    sent = await _notifications(db, "review.overdue")
    assert len(sent) == 1, f"три тика дали {len(sent)} уведомлений вместо одного"


@pytest.mark.asyncio
async def test_digest_repeats_only_when_flow_changed(db) -> None:
    """Сводка методисту: интервал прошёл, но цифры те же — молчим."""
    await _seed(db, review_overdue=False, reviewed=True)
    scheduler = DeadlineScheduler(digest_interval_s=0)

    await scheduler.tick()
    first = await _notifications(db, "flow.digest")
    assert len(first) == 1, "первая сводка не отправлена"
    assert "ждут подтверждения: 1" in first[0].body

    await scheduler.tick()
    assert len(await _notifications(db, "flow.digest")) == 1, "сводка повторилась без изменений"

    # В поток пришла нераспределённая работа — цифры изменились.
    async with db() as session:
        session.add(
            Submission(
                id=str(uuid.uuid4()), assignment_id="asg", student_id="stu",
                file_path="", file_name="вторая.docx", track="product_fraud",
                submitted_at=now_utc(),
            )
        )
        await session.commit()

    await scheduler.tick()
    sent = await _notifications(db, "flow.digest")
    assert len(sent) == 2, "изменение потока не попало в сводку"
    assert "не распределено: 1" in sent[-1].body


@pytest.mark.asyncio
async def test_digest_is_silent_on_empty_flow(db, tmp_path) -> None:
    """Пустой поток не повод будить методиста."""
    async with db() as session:
        session.add(User(id="coord", name="Методист", role=Role.COORDINATOR))
        session.add(Assignment(id="asg", title="ДЗ", track="product_fraud"))
        await session.commit()

    scheduler = DeadlineScheduler(digest_interval_s=0)
    await scheduler.tick()
    assert await _notifications(db, "flow.digest") == []
