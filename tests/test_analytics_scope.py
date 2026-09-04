"""Тест главной метрики эффекта: числитель и знаменатель считают одно и то же.

Кейс требует измеримых показателей — прежде всего сокращения числа ручных
действий. Считалось так: базовая линия по работам **выбранного** задания,
а фактические действия — по **всему** журналу. Пока задание одно, числа
сходятся; со вторым курсом метрика показала «было 33, стало 45, сокращение
−36 %» — то есть заявила, что система увеличила ручную работу.

Метрика, которой команда защищает проект, обязана быть проверяемой. Здесь
она и проверяется: два задания в базе, действия в обоих.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.clock import now as now_utc
from app.models import ActionLog, Assignment, Base, Role, Submission, User


@pytest.fixture
def client(tmp_path):
    """Приложение с временной базой вместо рабочей.

    `TestClient` создаётся без `with`: так не запускается lifespan, а вместе
    с ним очередь ревью. Очередь — модульный синглтон с `asyncio.Queue`,
    и на втором тесте она оказалась бы привязана к уже закрытому циклу
    событий. Для аналитики очередь не нужна вовсе.
    """
    import asyncio

    from app.db import get_session
    from app.main import app

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def prepare():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        now = now_utc()
        async with maker() as s:
            s.add(User(id="coord-1", name="Методист", role=Role.COORDINATOR))
            for aid in ("asg-a", "asg-b"):
                s.add(Assignment(id=aid, title=aid, track="product_fraud"))
                for i in range(2):
                    sid = f"{aid}-sub{i}"
                    s.add(Submission(
                        id=sid, assignment_id=aid, student_id="coord-1",
                        file_path=f"/tmp/{sid}.docx", file_name=f"{sid}.docx",
                        track="product_fraud", submitted_at=now - timedelta(days=1),
                    ))
                    # По три действия на работу — как в реальном потоке.
                    for action in ("submission.upload", "review.start", "review.confirm"):
                        s.add(ActionLog(user_id="coord-1", role=Role.COORDINATOR,
                                        action=action, target=sid))
                s.add(ActionLog(user_id="coord-1", role=Role.COORDINATOR,
                                action="allocate", target=aid))
            await s.commit()

    asyncio.run(prepare())

    async def override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = override
    test_client = TestClient(app)
    # Фабрика сессий нужна тестам, которые досыпают записи в журнал прямо
    # в базу: через API такие действия не создать, а проверять их надо.
    test_client.maker = maker  # type: ignore[attr-defined]
    yield test_client
    app.dependency_overrides.clear()


def _effect(client, assignment_id: str | None):
    q = f"?assignment_id={assignment_id}" if assignment_id else ""
    r = client.get(f"/api/analytics{q}", headers={"X-User-Id": "coord-1"})
    assert r.status_code == 200, r.text
    return r.json()["effect"]


def test_second_course_does_not_spoil_the_first_metric(client) -> None:
    """Действия второго задания не должны попадать в счётчик первого.

    Считаются только действия НАД РАБОТАМИ: по три на каждую из двух, итого
    шесть. Действие `allocate` указывает на задание, а не на работу, и в
    числитель не идёт — базовая линия задана «на работу», и складывать с ней
    операции другого масштаба нельзя. Метрика от этого занижает эффект, и
    это осознанный выбор: показатель, которым защищаются, должен выдерживать
    придирку, а не выглядеть красиво.
    """
    a = _effect(client, "asg-a")
    b = _effect(client, "asg-b")

    assert a["submissions_processed"] == 2
    assert a["manual_actions_actual"] == 6, "в счётчик попали посторонние действия"
    assert a == b, "задания симметричны — метрики обязаны совпадать"


def test_reduction_is_positive_when_automation_helps(client) -> None:
    """Сокращение должно быть положительным, а не отрицательным."""
    e = _effect(client, "asg-a")
    assert e["manual_actions_actual"] < e["manual_actions_baseline"]
    assert e["reduction_rate"] > 0


def test_whole_flow_sums_both_assignments(client) -> None:
    """Без фильтра метрика считает весь поток — и база, и факт растут вместе.

    Режим «весь поток» когда-то брал журнал целиком: туда попадали входы в
    систему, регистрации и правки критериев, и на живых данных метрика
    показала «было 110, стало 146». Числитель обязан считать те же работы,
    что и знаменатель, в обоих режимах.
    """
    whole = _effect(client, None)
    single = _effect(client, "asg-a")

    assert whole["submissions_processed"] == 2 * single["submissions_processed"]
    assert whole["manual_actions_baseline"] == 2 * single["manual_actions_baseline"]
    assert whole["manual_actions_actual"] == 2 * single["manual_actions_actual"]
    assert whole["reduction_rate"] == pytest.approx(single["reduction_rate"], abs=1e-9)


def test_login_and_setup_actions_are_not_counted_as_manual_work(client) -> None:
    """Вход в систему — не работа над сдачей.

    Найдено сразу после появления формы входа: каждый вход писался в журнал,
    журнал целиком шёл в числитель, и показатель эффекта на живых данных
    превратился в «сокращение −33 %» — то есть заявил, что система
    увеличила ручную работу. Базовая линия задана «на работу», значит и
    числитель считает только действия над работами.
    """
    before = _effect(client, None)

    # Действия, не относящиеся ни к какой сдаче: вход, регистрация,
    # заведение задания, правка критериев, добавление участника.
    import asyncio as aio

    async def add_noise():
        async with client.maker() as s:
            for action, target in (
                ("login", None),
                ("register", None),
                ("assignment.create", "asg-a"),
                ("rubric.extract", "asg-a"),
                ("user.create", "coord-1"),
            ):
                s.add(ActionLog(user_id="coord-1", role=Role.COORDINATOR,
                                action=action, target=target))
            await s.commit()

    aio.run(add_noise())

    after = _effect(client, None)
    assert after["manual_actions_actual"] == before["manual_actions_actual"], (
        "посторонние действия попали в счётчик ручной работы"
    )
    assert after["reduction_rate"] == before["reduction_rate"]
