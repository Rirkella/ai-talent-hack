"""Профиль студента не складывает баллы разных заданий.

Тот же класс ошибки, что и в аналитике потока: у продуктового фрода
максимум 10, у технического QA — 20. В профиле студента, у которого есть
работы обоих курсов, «средний балл» считался по сырым числам, а график
динамики рисовал 17 из 20 выше, чем 8 из 10 — то есть показывал худший
результат как рост.

Здесь проверяется, что сервер сам сообщает интерфейсу, одна ли шкала, и
отдаёт доли от максимума на случай, когда шкала не одна.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.clock import now as now_utc
from app.models import Assignment, Base, Role, Submission, SubmissionStatus, User


def _review(score: float, maximum: float) -> dict:
    return {
        "preliminary_score": score,
        "max_score": maximum,
        "criteria": [
            {"criterion_id": "c1", "criterion_name": "Критерий",
             "max_score": maximum, "score": score, "verdict": "", "evidence": []}
        ],
    }


@pytest.fixture
def client(tmp_path):
    from app.db import get_session
    from app.main import app

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'p.db'}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = now_utc()

    async def prepare():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as s:
            s.add(User(id="coord-1", name="Методист", role=Role.COORDINATOR))
            s.add(User(id="stu-1", name="Студент", role=Role.STUDENT))
            # Два курса с разными максимумами — ровно как в демо-данных.
            s.add(Assignment(id="fraud", title="Фрод", track="product_fraud"))
            s.add(Assignment(id="qa", title="QA", track="tech_QA"))
            s.add(Submission(
                id="s-fraud", assignment_id="fraud", student_id="stu-1",
                file_path="/tmp/a.docx", file_name="a.docx", track="product_fraud",
                status=SubmissionStatus.CONFIRMED, final_score=8.0,
                review=_review(8.0, 10.0), confirmed_at=now,
                submitted_at=now - timedelta(days=2),
            ))
            s.add(Submission(
                id="s-qa", assignment_id="qa", student_id="stu-1",
                file_path="/tmp/b.xlsx", file_name="b.xlsx", track="tech_QA",
                status=SubmissionStatus.CONFIRMED, final_score=12.0,
                review=_review(12.0, 20.0), confirmed_at=now,
                submitted_at=now - timedelta(days=1),
            ))
            await s.commit()

    asyncio.run(prepare())

    async def override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = override
    test_client = TestClient(app)
    test_client.maker = maker  # type: ignore[attr-defined]
    yield test_client
    app.dependency_overrides.clear()


COORD = {"X-User-Id": "coord-1"}


def test_mixed_maxima_are_reported_as_such(client) -> None:
    s = client.get("/api/students/stu-1/profile", headers=COORD).json()["summary"]
    assert s["single_scale"] is False, (
        "работы с максимумами 10 и 20 объявлены одной шкалой — "
        "интерфейс покажет их средним баллом"
    )
    # 8/10 и 12/20 — это 80 % и 60 %, в среднем 70 %.
    assert s["mean_share"] == pytest.approx(0.7)
    assert s["best_share"] == pytest.approx(0.8)


def test_best_by_share_is_not_the_biggest_number(client) -> None:
    """Лучшая работа — 8 из 10, а не 12 из 20, хотя 12 больше восьми."""
    s = client.get("/api/students/stu-1/profile", headers=COORD).json()["summary"]
    assert s["best"] == 12.0, "сырое значение по-прежнему нужно для одной шкалы"
    assert s["best_share"] > s["mean_share"]


def test_single_scale_keeps_raw_scores(client) -> None:
    """Внутри одного курса средний балл остаётся баллом, а не процентом."""

    async def drop_qa():
        async with client.maker() as s:  # type: ignore[attr-defined]
            work = await s.get(Submission, "s-qa")
            await s.delete(work)
            await s.commit()

    asyncio.run(drop_qa())
    s = client.get("/api/students/stu-1/profile", headers=COORD).json()["summary"]
    assert s["single_scale"] is True
    assert s["mean_score"] == 8.0
