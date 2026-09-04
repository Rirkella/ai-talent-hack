"""Подключение к БД и сессии."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base

settings.ensure_dirs()

engine = create_async_engine(
    settings.db_url,
    echo=False,
    # SQLite не любит долгие блокировки при параллельных записях воркера
    # очереди и HTTP-обработчиков; ожидание вместо мгновенной ошибки.
    connect_args={"timeout": 30} if settings.db_url.startswith("sqlite") else {},
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

log = logging.getLogger(__name__)


async def init_db() -> None:
    """Создаёт схему и добавляет недостающие колонки.

    Полноценных миграций нет намеренно — срок жизни MVP короче их пользы.
    Но `create_all` создаёт только отсутствующие *таблицы* и молча
    игнорирует новые колонки в существующих: после обновления кода
    приложение падало бы на базе, оставшейся с прошлого запуска. Поэтому
    здесь ровно один шаг — добавление недостающих колонок. Он аддитивен:
    ничего не переименовывает, не удаляет и не переносит данные.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
    await _backfill_logins()


async def _backfill_logins() -> None:
    """Выдаёт логин и пароль тем, кто заведён до появления входа по паролю.

    Аддитивное добавление колонок оставляет их пустыми, а пустой логин — это
    учётная запись, которой нельзя войти. На базе, пережившей обновление,
    экран входа не пустил бы вообще никого, и выглядело бы это как поломка
    входа, а не как незаполненные данные.

    Шаг идемпотентный: пользователей с уже заданным логином не трогает.
    """
    from sqlalchemy import select

    from app.auth import hash_password, unique_login
    from app.config import settings
    from app.models import User

    async with SessionLocal() as session:
        users = (await session.execute(select(User))).scalars().all()
        taken = {u.login for u in users if u.login}
        need = [u for u in users if not u.login or not u.password_hash]
        if not need:
            return
        # Хеш считается один раз: pbkdf2 намеренно медленный.
        demo_hash = hash_password(settings.demo_password)
        for u in need:
            if not u.login:
                u.login = unique_login(u.name, taken)
                taken.add(u.login)
            if not u.password_hash:
                u.password_hash = demo_hash
        await session.commit()
        log.info("выдано логинов существующим пользователям: %d", len(need))


def _add_missing_columns(conn) -> None:  # noqa: ANN001
    """`ALTER TABLE ADD COLUMN` для колонок, которых нет в существующей базе."""
    from sqlalchemy import inspect, text

    if not engine.url.get_backend_name().startswith("sqlite"):
        return
    inspector = inspect(conn)
    existing = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue
            type_sql = column.type.compile(conn.dialect)
            default = column.default.arg if column.default is not None else None
            suffix = ""
            if isinstance(default, (int, float, bool)):
                suffix = f" DEFAULT {int(default) if isinstance(default, bool) else default}"
            elif isinstance(default, str):
                suffix = f" DEFAULT '{default}'"
            conn.execute(
                text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {type_sql}{suffix}')
            )


async def get_session() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI."""
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Сессия для фоновых задач, вне запроса."""
    async with SessionLocal() as session:
        yield session
