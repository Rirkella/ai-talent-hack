"""Изоляция тестов от пользовательских данных.

Найдено внешним аудитом. Тесты подменяли зависимость `get_session`, и это
покрывало только HTTP-обработчики: очередь ревью, планировщик сроков и
уведомления берут сессию не через зависимость, а через `session_scope`,
который смотрит в **рабочую** базу. Последствия были двойные.

1. На машине без `data/app.db` два теста падали с `no such table: jobs` —
   они честно доходили до постановки задания в очередь и упирались в
   отсутствующую базу.
2. На машине с базой они проходили, но записывали `Job` в базу
   пользователя, ссылаясь на работу, которой в ней нет. То есть тесты
   писали в те самые данные, которые показывают на защите.

Здесь обе беды закрываются в одном месте: подменяются фабрика сессий,
движок и каталог загрузок, а очередь получает свежий экземпляр на каждый
тест. Подмена работает и для `session_scope`, потому что он берёт
`SessionLocal` из модуля в момент вызова, а не при импорте.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Временная база, каталог загрузок и пустая очередь.

    Возвращает фабрику сессий: тесты готовят через неё состояние и читают
    результат в новой сессии, а не только по ответу HTTP.
    """
    import app.db as db_module
    from app.config import settings

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def create_schema() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(create_schema())

    # Фоновым путям — та же база. `session_scope` и `get_session` читают
    # `SessionLocal` в момент вызова, поэтому подмены атрибута достаточно.
    monkeypatch.setattr(db_module, "SessionLocal", maker)
    monkeypatch.setattr(db_module, "engine", engine)

    # Файлы работ тоже не должны попадать в пользовательский каталог.
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(settings, "upload_dir", uploads, raising=False)

    # Очередь — модульный синглтон. Без замены задание, оставшееся от
    # предыдущего теста, находилось бы как «уже в очереди».
    import app.api.routes as routes_module
    import app.queue as queue_module

    fresh = queue_module.ReviewQueue(workers=1)
    monkeypatch.setattr(queue_module, "queue", fresh, raising=False)
    monkeypatch.setattr(routes_module, "queue", fresh, raising=False)

    yield maker

    asyncio.run(engine.dispose())


@pytest.fixture
def isolated_client(isolated_db, monkeypatch: pytest.MonkeyPatch) -> Iterator:
    """`TestClient` поверх изолированной базы.

    Без `with`: lifespan не запускается, а вместе с ним не поднимаются
    очередь и планировщик — их `asyncio.Queue` привязался бы к циклу
    событий, который закроется после первого же теста.
    """
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.main import app

    async def override():
        async with isolated_db() as session:
            yield session

    app.dependency_overrides[get_session] = override
    client = TestClient(app)
    client.maker = isolated_db  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()
