"""Общие зависимости API: текущий пользователь и журнал действий.

Аутентификация упрощена намеренно: вход — выбор из списка демо-пользователей,
токен равен идентификатору и живёт в `sessionStorage`. `sessionStorage`
изолирован по вкладке, поэтому три вкладки одного браузера живут под тремя
разными ролями без режима инкогнито — это и есть сценарий демонстрации.

Форма пароля здесь была бы вредна: она создавала бы впечатление
защищённости, которой в MVP нет.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import ActionLog, Role, User


async def current_user(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    session: AsyncSession = Depends(get_session),
) -> User:
    if not x_user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Не выбран пользователь")
    user = await session.get(User, x_user_id)
    if user is None or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь не найден")
    return user


def require_role(*roles: Role):  # noqa: ANN201
    """Ограничение доступа по роли."""

    async def dep(user: User = Depends(current_user)) -> User:
        if user.role not in {r.value for r in roles}:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Действие доступно ролям: {', '.join(r.value for r in roles)}",
            )
        return user

    return dep


async def log_action(
    session: AsyncSession,
    user: User,
    action: str,
    *,
    target: str = "",
    manual_actions_saved: int = 0,
    **detail: object,
) -> None:
    """Пишет действие в журнал.

    `manual_actions_saved` — сколько шагов ручного процесса заменило это одно
    действие. Число берётся из разбора AS-IS (семь шагов) и позволяет считать
    метрику «сокращение числа ручных действий» по фактическим событиям,
    а не по оценке на глаз.
    """
    session.add(
        ActionLog(
            user_id=user.id,
            role=user.role,
            action=action,
            target=target,
            manual_actions_saved=manual_actions_saved,
            detail=detail,
        )
    )
