"""HTTP API приложения.

Собрано в один модуль осознанно: маршрутов немного, а разнесение по десятку
файлов на этом объёме добавило бы навигации больше, чем ясности. Разделы
отмечены заголовками и идут в порядке сквозного сценария.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, log_action, require_role
from app.auth import hash_password, unique_login, verify_password
from app.clock import as_utc
from app.config import settings
from app.db import get_session
from app.events import bus
from app.ingest.loader import SUPPORTED, UnsupportedFormat, file_sha256, read_any
from app.llm.client import get_client
from app.models import (
    ActionLog,
    Assignment,
    DeadlineState,
    Job,
    Notification,
    Role,
    ScoringPreset,
    Submission,
    SubmissionStatus,
    User,
)
from app.notify.deadlines import evaluate, now_utc, review_status
from app.privacy.audit import audit
from app.queue import queue
from app.rubric.extract import extract_rubric
from app.rubric.models import Rubric

router = APIRouter(prefix="/api")


# ── система и офлайн-контур ───────────────────────────────────────────────────


@router.get("/health")
async def health() -> dict:
    """Состояние приложения и провайдера модели — баннер в интерфейсе."""
    return {
        "ok": True,
        "server_time": now_utc().isoformat(),
        "llm": get_client().health(),
        "audit": audit.summary(),
        "queue_depth": queue.depth,
        "subscribers": bus.subscriber_count,
    }


@router.get("/privacy")
async def privacy(session: AsyncSession = Depends(get_session)) -> dict:
    """Вкладка «Приватность»: что замаскировано и сколько было внешних вызовов."""
    from app.privacy.pii import scan

    subs = (await session.execute(select(Submission))).scalars().all()
    counts: dict[str, int] = {}
    scanned = 0
    for sub in subs[:50]:
        try:
            doc = read_any(sub.file_path)
        except Exception:  # noqa: BLE001 — файл мог быть удалён вручную
            continue
        scanned += 1
        for k, v in scan(doc.text).items():
            counts[k] = counts.get(k, 0) + v

    return {
        "audit": audit.summary(),
        "recent_calls": [
            {
                "at": r.at.isoformat(), "host": r.host, "kind": r.kind,
                "purpose": r.purpose, "ok": r.ok,
                "duration_ms": round(r.duration_ms) if r.duration_ms else None,
            }
            for r in audit.recent(60)
        ],
        "pii_found": counts,
        "documents_scanned": scanned,
        "pii_enabled": settings.pii_enabled,
        "pii_use_ner": settings.pii_use_ner,
    }


# ── вход ──────────────────────────────────────────────────────────────────────


DEMO_PASSWORD = settings.demo_password


class LoginIn(BaseModel):
    login: str
    password: str


@router.post("/login")
async def login(
    body: LoginIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """Вход по логину и паролю.

    Ответ на неверный логин и на неверный пароль **одинаковый**: разные
    тексты позволили бы перебором узнать, какие логины существуют. Пароль
    сверяется с `pbkdf2`-хешем сравнением постоянного времени.

    Токена нет: дальше запросы подписываются заголовком `X-User-Id`, как и
    раньше. Полноценная сессия с подписью — следующий шаг, и он не сделан
    честно, а не изображён.
    """
    login_value = (body.login or "").strip().lower()
    # Пустой логин отбрасывается до запроса. Иначе он совпал бы с
    # пользователем, у которого логин не задан: в базе это та же пустая
    # строка. Сейчас вход всё равно не прошёл бы — пустой хеш не проходит
    # проверку, — но полагаться на это как на защиту нельзя.
    user = (
        None
        if not login_value
        else (
            await session.execute(
                select(User).where(
                    User.login == login_value, User.login != "", User.active
                )
            )
        ).scalars().first()
    )

    if user is None or not verify_password(body.password or "", user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль"
        )

    await log_action(session, user, "login")
    await session.commit()
    return {
        "id": user.id, "name": user.name, "role": user.role,
        "capacity": user.capacity, "competencies": user.competencies,
        "competency_names": [_track_name(c) for c in (user.competencies or [])],
    }


class RegisterIn(BaseModel):
    name: str
    role: str
    login: str
    password: str


@router.post("/register")
async def register(
    body: RegisterIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """Самостоятельная регистрация.

    Роли, доступные для регистрации, задаются ключом
    `OPEN_REGISTRATION_ROLES`. На демонстрационном стенде открыты все три,
    чтобы можно было попробовать любую; в реальном внедрении там остаётся
    только `student` — роль ревьюера и методиста выдаёт человек, отвечающий
    за программу, а не тот, кто заполнил форму.

    Занятость логина проверяется явно и сообщается прямо: скрывать её
    бессмысленно, потому что при регистрации занятый логин всё равно виден
    по отказу, а невнятное сообщение только мешает.
    """
    allowed = {
        r.strip() for r in settings.open_registration_roles.split(",") if r.strip()
    }
    name = (body.name or "").strip()
    login_value = (body.login or "").strip().lower()
    password = body.password or ""

    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Укажите имя")
    if body.role not in allowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Самостоятельная регистрация для этой роли закрыта. "
            f"Доступно: {', '.join(sorted(allowed)) or '—'}.",
        )
    if len(login_value) < 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Логин короче трёх символов")
    # Явный набор символов, а не `isalnum()`: в Python кириллица тоже
    # «алфавитно-цифровая», и проверка молча пропускала логин «петров.и».
    # Логин печатают руками, в том числе на чужой клавиатуре, — латиница.
    allowed_chars = set("abcdefghijklmnopqrstuvwxyz0123456789._-")
    if not set(login_value) <= allowed_chars:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "В логине допустимы латинские буквы, цифры, точка, дефис и подчёркивание",
        )
    if len(password) < 6:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пароль короче шести символов")

    taken = (
        await session.execute(select(User).where(User.login == login_value))
    ).scalars().first()
    if taken is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Такой логин уже занят")

    prefix = {"coordinator": "coord", "reviewer": "rev", "student": "stu"}[body.role]
    new_user = User(
        id=f"{prefix}-{uuid.uuid4().hex[:8]}",
        name=name,
        role=body.role,
        capacity=6 if body.role == Role.REVIEWER else 10,
        competencies=[],
        login=login_value,
        password_hash=hash_password(password),
    )
    session.add(new_user)
    await log_action(session, new_user, "register", role_created=body.role)
    await session.commit()
    await bus.publish("users_changed", {"user_id": new_user.id, "role": new_user.role})
    return {
        "id": new_user.id, "name": new_user.name, "role": new_user.role,
        "capacity": new_user.capacity, "competencies": [], "competency_names": [],
    }


@router.get("/registration-roles")
async def registration_roles() -> dict:
    """Какие роли открыты для самостоятельной регистрации."""
    allowed = [
        r.strip() for r in settings.open_registration_roles.split(",") if r.strip()
    ]
    return {"roles": [t for t in allowed if t in {r.value for r in Role}]}


@router.get("/demo-credentials")
async def demo_credentials(session: AsyncSession = Depends(get_session)) -> dict:
    """Логины демонстрационных учётных записей для экрана входа.

    Без этого показ встаёт на первом экране: логины выведены из имён, и
    угадать их нельзя. Отдаются только логины и имена — хеши не покидают
    сервер никогда. Отключается ключом `DEMO_SHOW_CREDENTIALS=false`, и
    тогда ручка возвращает пустой список, а блок в интерфейсе исчезает.
    """
    if not settings.demo_show_credentials:
        return {"password": None, "accounts": []}

    users = (
        await session.execute(select(User).where(User.active, User.login != ""))
    ).scalars().all()
    return {
        "password": settings.demo_password,
        "accounts": [
            {"login": u.login, "name": u.name, "role": u.role} for u in users
        ],
    }


@router.get("/users")
async def list_users(
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Список участников. Логины видны, пароли и их хеши — нет.

    Ручка закрыта входом: до появления формы её дёргал экран входа, и она
    была обязана работать без авторизации. Теперь экрану входа она не
    нужна — он берёт подсказку из `/demo-credentials`, — а состав участников
    незачем отдавать кому попало.
    """
    users = (await session.execute(select(User).where(User.active))).scalars().all()
    return [
        {
            "id": u.id, "name": u.name, "role": u.role, "login": u.login,
            "capacity": u.capacity, "competencies": u.competencies,
            # Читаемые названия рядом с идентификаторами: интерфейс печатает
            # компетенции ревьюера в трёх местах, и везде это были слаги.
            "competency_names": [_track_name(c) for c in (u.competencies or [])],
        }
        for u in users
    ]


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict:
    return {"id": user.id, "name": user.name, "role": user.role}


class UserIn(BaseModel):
    name: str
    role: str
    capacity: int = 10
    competencies: list[str] = []
    email: str = ""
    # Пустой пароль означает «поставь общий демонстрационный»: заводя
    # участников на защите, методист не должен придумывать пароль каждому.
    password: str = ""


@router.post("/users")
async def create_user(
    body: UserIn,
    user: User = Depends(require_role(Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Заводит пользователя: ревьюера или студента.

    Нужно, чтобы поток можно было наполнить реальными людьми, а не только
    демонстрационными. Ёмкость и компетенции ревьюера сразу участвуют
    в распределении.
    """
    if body.role not in {r.value for r in Role}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Роль должна быть одной из: {', '.join(r.value for r in Role)}",
        )
    if not body.name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Имя не может быть пустым")

    # Идентификатор читаемый: он же попадает в заголовок X-User-Id и в журнал.
    prefix = {"coordinator": "coord", "reviewer": "rev", "student": "stu"}[body.role]
    taken = {
        u.login
        for u in (await session.execute(select(User.login))).scalars()
        if u
    }
    new = User(
        id=f"{prefix}-{uuid.uuid4().hex[:8]}",
        name=body.name.strip(),
        role=body.role,
        capacity=max(1, body.capacity),
        competencies=[c.strip() for c in body.competencies if c.strip()],
        email=body.email.strip(),
        login=unique_login(body.name.strip(), taken),
        password_hash=hash_password(body.password.strip() or DEMO_PASSWORD),
    )
    session.add(new)
    await log_action(session, user, "user.create", target=new.id, role_created=body.role)
    await session.commit()
    await bus.publish("users_changed", {"user_id": new.id, "role": new.role})
    return {
        "ok": True, "id": new.id, "name": new.name, "role": new.role,
        "capacity": new.capacity, "competencies": new.competencies,
        "login": new.login,
        # Пароль возвращается один раз — методисту, который завёл участника,
        # и только если это общий демонстрационный. Хеш наружу не отдаётся
        # никогда.
        "password": DEMO_PASSWORD if not body.password.strip() else None,
    }


@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserIn,
    user: User = Depends(require_role(Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Правка ёмкости и компетенций ревьюера — вход распределения."""
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    target.name = body.name.strip() or target.name
    target.capacity = max(1, body.capacity)
    target.competencies = [c.strip() for c in body.competencies if c.strip()]
    if body.email.strip():
        target.email = body.email.strip()
    await log_action(session, user, "user.update", target=user_id)
    await session.commit()
    await bus.publish("users_changed", {"user_id": user_id, "role": target.role})
    return {"ok": True, "id": target.id, "capacity": target.capacity,
            "competencies": target.competencies}


@router.post("/users/{user_id}/deactivate")
async def deactivate_user(
    user_id: str,
    user: User = Depends(require_role(Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Отключает пользователя, не удаляя его.

    Удаление оборвало бы ссылки в уже проверенных работах и сломало бы
    аналитику. Неактивный ревьюер просто перестаёт получать назначения.
    """
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    target.active = False
    await log_action(session, user, "user.deactivate", target=user_id)
    await session.commit()
    await bus.publish("users_changed", {"user_id": user_id, "role": target.role})
    return {"ok": True}


# ── события ───────────────────────────────────────────────────────────────────


@router.get("/events")
async def events(request: Request, user_id: str, role: str = "") -> StreamingResponse:
    """Единая SSE-шина.

    Идентификатор передаётся параметром запроса, а не заголовком: `EventSource`
    в браузере не умеет задавать заголовки.
    """

    async def stream():  # noqa: ANN202
        sub = await bus.subscribe(user_id, role)
        try:
            yield f"event: connected\ndata: {json.dumps({'user_id': user_id})}\n\n"
            while True:
                # Остановка приложения проверяется первой: без этого поток
                # висел до отключения браузера, uvicorn ждал его закрытия,
                # и Ctrl+C не останавливал процесс, пока открыта хоть одна
                # вкладка. Приходилось убивать процесс из диспетчера задач.
                if bus.closing.is_set() or await request.is_disconnected():
                    break
                try:
                    event, payload = await asyncio.wait_for(sub.queue.get(), timeout=15)
                    if event == "shutdown":
                        break
                    yield f"event: {event}\ndata: {payload}\n\n"
                except TimeoutError:
                    # Комментарий-пинг: держит соединение живым через прокси
                    # и позволяет заметить обрыв.
                    yield ": ping\n\n"
        finally:
            await bus.unsubscribe(sub)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ── задания и рубрика ─────────────────────────────────────────────────────────


class AssignmentIn(BaseModel):
    title: str
    track: str
    course: str = ""
    homework_no: int = 1
    channel: str = "stepik"


# Реестр направлений — данные, а не код. Новое направление добавляется строкой.
TRACKS: list[dict[str, str]] = [
    {"id": "product_fraud", "name": "Продуктовый фрод"},
    {"id": "product_business_models", "name": "Продуктовые бизнес-модели"},
    {"id": "product", "name": "Продуктовый менеджмент"},
    {"id": "data_analysis", "name": "Анализ данных"},
    {"id": "data_science", "name": "Data Science"},
    {"id": "tech_QA", "name": "Тестирование (QA)"},
    {"id": "system_design", "name": "Системный дизайн"},
    {"id": "go", "name": "Разработка на Go"},
]

CHANNELS: list[dict[str, str]] = [
    {"id": "stepik", "name": "Stepik"},
    {"id": "google_docs", "name": "Google Docs"},
    {"id": "github", "name": "GitHub"},
    {"id": "upload", "name": "Загрузка файла"},
]


@router.get("/tracks")
async def list_tracks() -> dict:
    """Справочники для формы создания задания."""
    return {"tracks": TRACKS, "channels": CHANNELS, "formats": SUPPORTED}


# ── формула балла и индекс приоритета ─────────────────────────────────────────

# Настройки формулы живут в памяти процесса: это параметры сессии методиста,
# а не данные потока. Пересчёт применяется к уже сохранённым результатам,
# поэтому менять их можно на ходу и видеть эффект сразу.
_scoring: dict[str, object] = {}


def current_scoring():  # noqa: ANN201
    from app.scoring.formula import PriorityWeights, ScoringConfig

    cfg = ScoringConfig()
    for key, value in _scoring.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
        elif hasattr(cfg.weights, key):
            setattr(cfg.weights, key, value)
    _ = PriorityWeights  # тип используется через ScoringConfig
    return cfg


@router.get("/scoring")
async def get_scoring() -> dict:
    """Текущие веса и тумблеры формулы."""
    cfg = current_scoring()
    return {
        "weights": {
            "ai_signal": {"weight": cfg.weights.ai_signal, "on": cfg.weights.ai_signal_on,
                          "title": "Признаки генеративного ИИ"},
            "similarity": {"weight": cfg.weights.similarity, "on": cfg.weights.similarity_on,
                           "title": "Схожесть с другой работой"},
            "unverified_evidence": {"weight": cfg.weights.unverified_evidence,
                                    "on": cfg.weights.unverified_evidence_on,
                                    "title": "Цитаты не подтверждены"},
            "formal_violations": {"weight": cfg.weights.formal_violations,
                                  "on": cfg.weights.formal_violations_on,
                                  "title": "Формальные нарушения"},
            "borderline_score": {"weight": cfg.weights.borderline_score,
                                 "on": cfg.weights.borderline_score_on,
                                 "title": "Балл у границы"},
            "failed_steps": {"weight": cfg.weights.failed_steps,
                             "on": cfg.weights.failed_steps_on,
                             "title": "Не выполнены шаги"},
        },
        "formal_penalty_on": cfg.formal_penalty_on,
        "formal_penalty_per_violation": cfg.formal_penalty_per_violation,
        "formal_penalty_max": cfg.formal_penalty_max,
        # Пояснение здесь, а не только в документации: методист должен видеть
        # границу прямо в том месте, где двигает ползунок.
        "note": (
            "Веса влияют ТОЛЬКО на индекс приоритета ручной проверки. "
            "Сигнал генеративного ИИ не входит в балл ни при каком положении "
            "тумблера: кейс запрещает генеративной проверке автоматически "
            "влиять на оценку. Штраф за оформление — единственный компонент, "
            "меняющий балл, и по умолчанию он выключен."
        ),
    }


class ScoringIn(BaseModel):
    weights: dict[str, float] | None = None
    toggles: dict[str, bool] | None = None
    formal_penalty_on: bool | None = None
    formal_penalty_per_violation: float | None = None


@router.put("/scoring")
async def set_scoring(
    body: ScoringIn,
    assignment_id: str | None = None,
    user: User = Depends(require_role(Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Меняет веса и тумблеры и пересчитывает уже готовые ревью.

    Пересчёт на месте — смысл настройки: методист двигает вес и сразу видит,
    как переупорядочилась очередь ревьюеров, а не ждёт нового прогона модели.
    """
    # Имена и границы проверяются. Раньше принималось что угодно: вес −5
    # и вес 99 сохранялись молча, а отрицательный вес переворачивает смысл —
    # работы с признаками ИИ опускались бы в самый низ очереди, ровно туда,
    # где их никто не смотрит. Неизвестное имя тоже принималось и оседало
    # в настройках мёртвым грузом: методист думал, что настроил, а не менялось
    # ничего.
    from app.scoring.formula import PriorityWeights

    known = set(PriorityWeights.__dataclass_fields__)
    for k, v in (body.weights or {}).items():
        if k not in known:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Неизвестный вес «{k}». Доступны: "
                f"{', '.join(sorted(x for x in known if not x.endswith('_on')))}.",
            )
        value = float(v)
        if not 0.0 <= value <= 1.0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Вес «{k}» должен быть от 0 до 1, получено {value:g}.",
            )
        _scoring[k] = value
    for k, v in (body.toggles or {}).items():
        key = k if k.endswith("_on") else f"{k}_on"
        if key not in known:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Неизвестный тумблер «{k}»."
            )
        _scoring[key] = bool(v)
    if body.formal_penalty_on is not None:
        _scoring["formal_penalty_on"] = body.formal_penalty_on
    if body.formal_penalty_per_violation is not None:
        _scoring["formal_penalty_per_violation"] = float(body.formal_penalty_per_violation)

    recalculated = await _recalculate_reviews(session, assignment_id)

    await log_action(session, user, "scoring.update", target=assignment_id or "all")
    await session.commit()
    await bus.publish("scoring_updated", {"recalculated": recalculated})
    return {"ok": True, "recalculated": recalculated}


async def _recalculate_reviews(
    session: AsyncSession, assignment_id: str | None
) -> int:
    """Пересчитывает готовые ревью под текущую конфигурацию формулы.

    Общий шаг для правки одного веса и для применения пресета: обе операции
    обязаны давать одинаковый результат, а две копии этого цикла разошлись бы
    при первой же правке.
    """
    from app.agent.schemas import ReviewResult
    from app.scoring.formula import aggregate, apply_deadline_penalty

    cfg = current_scoring()

    q = select(Submission).where(Submission.review.is_not(None))
    if assignment_id:
        q = q.where(Submission.assignment_id == assignment_id)
    subs = (await session.execute(q)).scalars().all()

    assignments = {a.id: a for a in (await session.execute(select(Assignment))).scalars()}
    recalculated = 0
    for s in subs:
        a = assignments.get(s.assignment_id)
        if a is None:
            continue
        result = ReviewResult.model_validate(s.review)
        aggregate(result, Rubric.model_validate(a.rubric or {}), cfg)
        # Штраф за срок применяется поверх: он задан правилом условия и
        # не относится к настраиваемым весам.
        if s.late_penalty:
            apply_deadline_penalty(
                result, penalty=s.late_penalty, reason="нарушение срока сдачи"
            )
        s.review = result.model_dump(mode="json")
        s.priority_index = result.priority_index
        recalculated += 1
    return recalculated


@router.post("/assignments")
async def create_assignment(
    body: AssignmentIn,
    user: User = Depends(require_role(Role.COORDINATOR, Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Создаёт задание — то, что студент потом выбирает при сдаче.

    Заводить задания может и ревьюер: он ближе к предмету, чем координатор
    потока, и типовое ДЗ своего курса опишет точнее. Утверждение критериев
    при этом остаётся за методистом — ответственность за оценку не
    передаётся вместе с правом завести карточку.

    Вместе с загрузкой условия и утверждением критериев это полный путь
    добавления нового курса или ДЗ **через интерфейс, без правок кода**.
    """
    if not body.title.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Название не может быть пустым")
    if body.track not in {t["id"] for t in TRACKS}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Неизвестное направление. Доступны: {', '.join(t['id'] for t in TRACKS)}",
        )

    now = now_utc()
    a = Assignment(
        id=f"hw-{body.track}-{body.homework_no}-{uuid.uuid4().hex[:6]}",
        title=body.title.strip(),
        track=body.track,
        course=body.course.strip() or next(
            (t["name"] for t in TRACKS if t["id"] == body.track), body.track
        ),
        homework_no=body.homework_no,
        channel=body.channel,
        # Рубрика пустая и НЕ утверждённая: её ещё предстоит извлечь из
        # условия или ввести вручную, и утвердить человеку.
        rubric=Rubric(track=body.track, course=body.course).model_dump(mode="json"),
        rubric_approved=False,
        # Сроки по умолчанию — с запасом; методист задаёт свои.
        due_at=now + timedelta(days=7),
        hard_due_at=now + timedelta(days=8),
        review_due_at=now + timedelta(days=15),
    )
    session.add(a)
    await log_action(session, user, "assignment.create", target=a.id, manual_actions_saved=2)
    await session.commit()
    await bus.publish("assignments_changed", {"assignment_id": a.id})
    return {"ok": True, **_assignment_dict(a)}


@router.get("/assignments")
async def list_assignments(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await session.execute(select(Assignment))).scalars().all()
    out = []
    for a in rows:
        count = (
            await session.execute(
                select(func.count()).select_from(Submission).where(
                    Submission.assignment_id == a.id
                )
            )
        ).scalar_one()
        out.append(_assignment_dict(a) | {"submissions_count": count})
    return out


def _assignment_dict(a: Assignment) -> dict:
    rubric = a.rubric or {}
    return {
        "id": a.id, "title": a.title, "track": a.track, "course": a.course,
        "homework_no": a.homework_no, "channel": a.channel,
        "rubric_approved": a.rubric_approved,
        "criteria_count": len(rubric.get("criteria", [])),
        "total_max": sum(c.get("max_score", 0) for c in rubric.get("criteria", [])),
        "due_at": a.due_at.isoformat() if a.due_at else None,
        "hard_due_at": a.hard_due_at.isoformat() if a.hard_due_at else None,
        "review_due_at": a.review_due_at.isoformat() if a.review_due_at else None,
        "due_soon_lead_s": a.due_soon_lead_s,
    }


@router.get("/assignments/{assignment_id}")
async def get_assignment(assignment_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    a = await session.get(Assignment, assignment_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задание не найдено")
    return _assignment_dict(a) | {"rubric": a.rubric}


@router.post("/assignments/{assignment_id}/condition")
async def upload_condition(
    assignment_id: str,
    file: UploadFile = File(...),
    user: User = Depends(require_role(Role.COORDINATOR, Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Загрузка условия и автоизвлечение рубрики.

    Рубрика возвращается как ЧЕРНОВИК: `rubric_approved` остаётся `False`,
    и ревью по ней не запустится, пока методист не утвердит.
    """
    a = await session.get(Assignment, assignment_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задание не найдено")
    # Критерии может задать и ревьюер — но только пока они не утверждены.
    # После утверждения замена критериев обнулила бы уже выставленные баллы
    # всего потока, и такое решение остаётся за методистом.
    if user.role == Role.REVIEWER and a.rubric_approved:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Критерии уже утверждены. Изменить их может методист: замена "
            "критериев на ходу сбросила бы баллы всех проверенных работ.",
        )

    path = _save_upload(file, prefix="condition")
    a.condition_path = str(path)
    a.condition_sha256 = file_sha256(path)

    try:
        condition = read_any(path)
        rubric = await asyncio.to_thread(
            extract_rubric, condition, track=a.track, course=a.course
        )
    except UnsupportedFormat as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        # Отказ извлечения не должен блокировать методиста: критерии можно
        # ввести руками, и такой путь предусмотрен интерфейсом.
        a.rubric = Rubric(track=a.track, source="manual",
                          notes=f"Автоизвлечение не удалось: {exc}").model_dump(mode="json")
        a.rubric_approved = False
        await session.commit()
        return {"ok": False, "error": str(exc), "rubric": a.rubric}

    if rubric.assignment_title and not a.title:
        a.title = rubric.assignment_title
    _apply_rubric_deadlines(a, rubric)
    a.rubric = rubric.model_dump(mode="json")
    a.rubric_approved = False

    await log_action(session, user, "rubric.extract", target=a.id, manual_actions_saved=1)
    await session.commit()
    await bus.publish("rubric_updated", {"assignment_id": a.id, "approved": False})
    return {"ok": True, "rubric": a.rubric, "notes": rubric.notes}


class ConditionTextIn(BaseModel):
    """Условие, набранное или вставленное текстом прямо в интерфейсе."""

    text: str


@router.post("/assignments/{assignment_id}/condition/text")
async def upload_condition_text(
    assignment_id: str,
    body: ConditionTextIn,
    user: User = Depends(require_role(Role.COORDINATOR, Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Условие текстом — третий источник критериев наравне с файлом.

    Не у всякого задания есть файл условия: требования часто живут в письме,
    в карточке курса или в голове методиста. Текст сохраняется как обычное
    условие `.txt` и проходит ровно тот же путь извлечения, что и `.pdf`, —
    отдельной ветки разбора нет, иначе два источника разошлись бы в
    поведении.
    """
    text = (body.text or "").strip()
    if len(text) < 40:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Слишком короткий текст условия: нужны хотя бы названия критериев "
            "и баллы за них.",
        )

    settings.ensure_dirs()
    path = settings.upload_dir / f"condition_{uuid.uuid4().hex[:8]}.txt"
    path.write_text(text, encoding="utf-8")

    a = await session.get(Assignment, assignment_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задание не найдено")
    # Критерии может задать и ревьюер — но только пока они не утверждены.
    # После утверждения замена критериев обнулила бы уже выставленные баллы
    # всего потока, и такое решение остаётся за методистом.
    if user.role == Role.REVIEWER and a.rubric_approved:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Критерии уже утверждены. Изменить их может методист: замена "
            "критериев на ходу сбросила бы баллы всех проверенных работ.",
        )

    a.condition_path = str(path)
    a.condition_sha256 = file_sha256(path)

    try:
        condition = read_any(path)
        rubric = await asyncio.to_thread(
            extract_rubric, condition, track=a.track, course=a.course
        )
    except Exception as exc:  # noqa: BLE001
        a.rubric = Rubric(track=a.track, source="manual",
                          notes=f"Автоизвлечение не удалось: {exc}").model_dump(mode="json")
        a.rubric_approved = False
        await session.commit()
        return {"ok": False, "error": str(exc), "rubric": a.rubric}

    if rubric.assignment_title and not a.title:
        a.title = rubric.assignment_title
    _apply_rubric_deadlines(a, rubric)
    a.rubric = rubric.model_dump(mode="json")
    a.rubric_approved = False

    await log_action(session, user, "rubric.extract_text", target=a.id, manual_actions_saved=1)
    await session.commit()
    await bus.publish("rubric_updated", {"assignment_id": a.id, "approved": False})
    return {"ok": True, "rubric": a.rubric, "notes": rubric.notes}


def _apply_rubric_deadlines(a: Assignment, rubric: Rubric) -> None:
    """Проставляет сроки из условия, если методист их ещё не задал."""
    if a.due_at is not None:
        return
    d = rubric.deadlines
    if d.due_at:
        a.due_at = d.due_at
        a.hard_due_at = d.due_at + timedelta(hours=d.late_window_hours)
        a.review_due_at = a.hard_due_at + timedelta(days=d.review_window_days)


class RubricIn(BaseModel):
    rubric: dict


@router.put("/assignments/{assignment_id}/rubric")
async def update_rubric(
    assignment_id: str,
    body: RubricIn,
    user: User = Depends(require_role(Role.COORDINATOR, Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Ручное редактирование рубрики — второй из трёх источников."""
    a = await session.get(Assignment, assignment_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задание не найдено")
    # Критерии может задать и ревьюер — но только пока они не утверждены.
    # После утверждения замена критериев обнулила бы уже выставленные баллы
    # всего потока, и такое решение остаётся за методистом.
    if user.role == Role.REVIEWER and a.rubric_approved:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Критерии уже утверждены. Изменить их может методист: замена "
            "критериев на ходу сбросила бы баллы всех проверенных работ.",
        )

    try:
        rubric = Rubric.model_validate(body.rubric)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Критерии не проходят проверку: {exc}") from exc

    # Правка сбрасывает утверждение: изменённые критерии должен подтвердить человек.
    rubric.approved = False
    a.rubric = rubric.model_dump(mode="json")
    a.rubric_approved = False
    await log_action(session, user, "rubric.edit", target=a.id)
    await session.commit()
    await bus.publish("rubric_updated", {"assignment_id": a.id, "approved": False})
    return {"ok": True, "rubric": a.rubric}


@router.post("/assignments/{assignment_id}/rubric/approve")
async def approve_rubric(
    assignment_id: str,
    user: User = Depends(require_role(Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Утверждение рубрики человеком. Без него ревью не стартует."""
    a = await session.get(Assignment, assignment_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задание не найдено")
    rubric = Rubric.model_validate(a.rubric or {})
    if not rubric.criteria:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Критериев нет: добавьте хотя бы один перед утверждением.",
        )
    rubric.approve(by=user.name)
    a.rubric = rubric.model_dump(mode="json")
    a.rubric_approved = True
    await log_action(session, user, "rubric.approve", target=a.id, manual_actions_saved=1)
    await session.commit()
    await bus.publish("rubric_updated", {"assignment_id": a.id, "approved": True})
    return {"ok": True, "approved_by": user.name}


# ── сроки ─────────────────────────────────────────────────────────────────────


class DeadlinesIn(BaseModel):
    due_at: datetime | None = None
    hard_due_at: datetime | None = None
    review_due_at: datetime | None = None
    due_soon_lead_s: int | None = None


@router.put("/assignments/{assignment_id}/deadlines")
async def set_deadlines(
    assignment_id: str,
    body: DeadlinesIn,
    user: User = Depends(require_role(Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Установка трёх сроков. Часы системы не трогаются — двигаются сроки."""
    a = await session.get(Assignment, assignment_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задание не найдено")
    for field in ("due_at", "hard_due_at", "review_due_at", "due_soon_lead_s"):
        if (v := getattr(body, field)) is not None:
            setattr(a, field, v)
    await log_action(session, user, "deadlines.set", target=a.id)
    await session.commit()
    await bus.publish("deadlines_updated", _assignment_dict(a))
    return _assignment_dict(a)


@router.post("/assignments/{assignment_id}/deadlines/demo")
async def set_demo_deadlines(
    assignment_id: str,
    soft_s: int = 10,
    hard_s: int = 40,
    user: User = Depends(require_role(Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Пресет для демонстрации: мягкий срок через 10 с, жёсткий через 40 с."""
    from app.notify.scheduler import demo_deadlines

    a = await session.get(Assignment, assignment_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задание не найдено")
    for k, v in demo_deadlines(soft_s=soft_s, hard_s=hard_s).items():
        setattr(a, k, v)
    a.due_soon_lead_s = max(1, soft_s // 2)
    await session.commit()
    await bus.publish("deadlines_updated", _assignment_dict(a))
    return _assignment_dict(a)


@router.post("/assignments/{assignment_id}/deadlines/normal")
async def set_normal_deadlines(
    assignment_id: str,
    user: User = Depends(require_role(Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Обычные сроки: сдача через двое суток, окно опоздания сутки, проверка неделя.

    Обратный ход к режиму демонстрации. Без него данные оставались сломанными
    навсегда: пресет ставит сроки в десять и сорок секунд, они проходят, и
    дальше у всех работ висит «просрочено», а у панели — три момента времени
    в пределах одной минуты. Именно так демо-данные и выглядели после первого
    же показа правила штрафа.
    """
    a = await session.get(Assignment, assignment_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задание не найдено")

    now = now_utc()
    a.due_at = now + timedelta(days=2)
    a.hard_due_at = now + timedelta(days=3)
    a.review_due_at = now + timedelta(days=10)
    a.due_soon_lead_s = 3600
    await log_action(session, user, "deadlines.normal", target=a.id)
    await session.commit()
    await bus.publish("deadlines_updated", _assignment_dict(a))
    return _assignment_dict(a)


# ── работы ────────────────────────────────────────────────────────────────────


# Пустой файл — это промах мышью, а не работа. Двести байт с запасом
# отделяют его от осмысленного документа: даже пустой `.docx` весит больше.
MIN_UPLOAD_BYTES = 200


def safe_file_name(raw: str | None) -> str:
    """Имя файла для показа: только имя, без пути и без хвоста в 300 знаков.

    Путь на диске генерируется сам, так что подмены каталога здесь быть не
    может. Но имя из запроса попадает в интерфейс и в выгрузку, а прислать
    можно что угодно: проверено запросом — `../../../etc/passwd.md` и имя
    из трёхсот символов принимались как есть.
    """
    name = Path((raw or "").replace("\\", "/")).name.strip() or "работа"
    return name[:200]


def _save_upload(file: UploadFile, *, prefix: str = "work") -> Path:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Формат {suffix or '—'} не поддерживается. Доступны: {', '.join(SUPPORTED)}",
        )
    settings.ensure_dirs()
    path = settings.upload_dir / f"{prefix}_{uuid.uuid4().hex[:8]}{suffix}"
    with path.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    if path.stat().st_size < MIN_UPLOAD_BYTES:
        path.unlink(missing_ok=True)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Файл пустой или почти пустой — проверять в нём нечего. "
            "Возможно, выбран не тот файл.",
        )
    return path


@router.post("/submissions")
async def upload_submission(
    assignment_id: str = Form(default=""),
    student_id: str = Form(...),
    track: str = Form(...),
    file: UploadFile = File(...),
    submitted_at: datetime | None = Form(default=None),
    replaces: str | None = Form(default=None),
    # Свободная сдача: работа не по карточке из списка, а со своим
    # названием. Карточка заводится на лету, критериев у неё нет —
    # их предстоит задать человеку, и до этого проверка не запустится.
    new_assignment_title: str = Form(default=""),
    user: User = Depends(require_role(Role.COORDINATOR, Role.STUDENT)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Загрузка работы. Тег направления обязателен.

    Система дополнительно определяет направление сама и предупреждает при
    расхождении — но решение остаётся за человеком. Молча положить работу
    другого курса в этот поток нельзя.

    `replaces` превращает загрузку в **повторную сдачу**: создаётся новая
    версия, старая помечается `superseded` и уходит из очереди, но остаётся
    в истории. Ревьюер по умолчанию сохраняется — контекст работы уже у него
    в голове, и распределение это учитывает отдельным компонентом стоимости.
    """
    # Студент сдаёт только за себя. Поле формы приходит от клиента, и без
    # этой строки любой студент мог бы приписать работу однокурснику.
    if user.role == Role.STUDENT:
        student_id = user.id

    # Студент должен существовать. Внешнего ключа SQLite по умолчанию не
    # соблюдает, и работа заводилась на несуществующего пользователя:
    # проверено запросом с `student_id: "net-takogo"` — HTTP 200. В интерфейсе
    # такая работа показывалась с идентификатором вместо имени и висела
    # в потоке ничьей.
    student = await session.get(User, student_id)
    if student is None or student.role != Role.STUDENT or not student.active:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Студент не найден. Выберите его из списка или заведите в разделе "
            "«Участники».",
        )

    title = (new_assignment_title or "").strip()
    if not assignment_id and not title:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Выберите задание из списка или укажите название своей работы",
        )

    if assignment_id:
        assignment = await session.get(Assignment, assignment_id)
        if assignment is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Задание не найдено")
    else:
        # Работа со свободной темой. Критериев у неё нет и быть не может:
        # их знает только человек, который будет её проверять. Карточка
        # создаётся неутверждённой, и проверка по ней не запустится, пока
        # ревьюер или методист не опишет, за что ставить баллы.
        if track not in {t["id"] for t in TRACKS}:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Неизвестное направление. Доступны: {', '.join(t['id'] for t in TRACKS)}",
            )
        now = now_utc()
        assignment = Assignment(
            id=f"hw-free-{uuid.uuid4().hex[:8]}",
            title=title,
            track=track,
            course=next((x["name"] for x in TRACKS if x["id"] == track), track),
            homework_no=1,
            channel="upload",
            rubric=Rubric(
                track=track,
                source="manual",
                notes="Работа со свободной темой: критерии задаёт ревьюер или методист.",
            ).model_dump(mode="json"),
            rubric_approved=False,
            due_at=now + timedelta(days=7),
            hard_due_at=now + timedelta(days=8),
            review_due_at=now + timedelta(days=15),
        )
        session.add(assignment)
        await session.flush()
        assignment_id = assignment.id
        await bus.publish("assignments_changed", {"assignment_id": assignment.id})

    previous: Submission | None = None
    if replaces:
        previous = await session.get(Submission, replaces)
        if previous is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Предыдущая версия не найдена")
        if previous.assignment_id != assignment_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Предыдущая версия относится к другому заданию",
            )
        if user.role == Role.STUDENT and previous.student_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Это чужая работа")

    path = _save_upload(file)
    sha = file_sha256(path)

    duplicate = (
        await session.execute(
            select(Submission).where(
                Submission.file_sha256 == sha,
                Submission.assignment_id == assignment_id,
            )
        )
    ).scalars().first()

    detected = _detect_track(path)
    sub = Submission(
        id=str(uuid.uuid4()),
        assignment_id=assignment_id,
        student_id=previous.student_id if previous else student_id,
        file_path=str(path),
        file_name=safe_file_name(file.filename) or path.name,
        file_sha256=sha,
        track=track,
        detected_track=detected,
        submitted_at=submitted_at or now_utc(),
        version=(previous.version + 1) if previous else 1,
        previous_id=previous.id if previous else None,
        # Тот же ревьюер по умолчанию: он уже читал предыдущую версию.
        # Заменить его можно вручную или тумблером преемственности при
        # автораспределении.
        reviewer_id=previous.reviewer_id if previous else None,
        status=(
            SubmissionStatus.ASSIGNED
            if previous and previous.reviewer_id
            else SubmissionStatus.UPLOADED
        ),
    )
    session.add(sub)
    if previous is not None:
        previous.superseded = True
    await log_action(session, user, "submission.upload", target=sub.id, manual_actions_saved=2)
    await session.commit()

    warnings: list[str] = []
    if previous is not None:
        warnings.append(
            f"Это версия {sub.version}. Предыдущая версия сохранена и доступна "
            f"для сравнения; из очереди она убрана."
        )
        if previous.reviewer_id:
            from app.notify.scheduler import notify

            await notify(
                session, user_id=previous.reviewer_id, kind="submission.revision",
                title=f"Повторная сдача, версия {sub.version}",
                body=f"«{sub.file_name}». Предыдущий балл: "
                     f"{previous.final_score if previous.final_score is not None else '—'}.",
                submission_id=sub.id,
            )
            await session.commit()
    if detected and detected != track:
        warnings.append(
            f"Система определила направление «{detected}», а указано «{track}». "
            f"Проверьте: возможно, работа относится к другому курсу."
        )
    if duplicate:
        warnings.append(
            f"Файл с таким же содержимым уже загружен ({duplicate.file_name}). "
            f"Возможна повторная отправка."
        )

    await bus.publish("submission_created", {"submission_id": sub.id, "warnings": warnings})
    return {"ok": True, "submission_id": sub.id, "id": sub.id, "version": sub.version,
            "warnings": warnings, "detected_track": detected}


# Ключевые слова направлений. Простое и объяснимое правило: модель здесь
# не нужна, а ошибка обходится дорого — она молча увела бы работу в чужой поток.
_TRACK_MARKERS: dict[str, tuple[str, ...]] = {
    "product_fraud": ("риск", "cjm", "митигац", "roi", "фрод", "вероятность"),
    "product_business_models": ("unit-экономик", "юнит-экономик", "ltv", "cac",
                                "бизнес-модел", "выручк", "маржа"),
    "tech_QA": ("тест-кейс", "тестирован", "баг", "чек-лист", "требовани", "qa"),
    "system_design": ("архитектур", "систем", "нагрузк", "шардир", "кэш", "sla"),
    "go": ("golang", "goroutine", "func main", "package main"),
}


def _detect_track(path: Path) -> str:
    """Определение направления по содержимому — вспомогательный сигнал."""
    try:
        text = read_any(path).text.lower()
    except Exception:  # noqa: BLE001
        return ""
    scores = {
        track: sum(1 for m in markers if m in text)
        for track, markers in _TRACK_MARKERS.items()
    }
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] >= 2 else ""


@router.get("/submissions")
async def list_submissions(
    assignment_id: str | None = None,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Список работ, отфильтрованный по роли.

    Ревьюер видит свою очередь, студент — только свои работы, методист — всё.
    Фильтрация на сервере, а не в интерфейсе: студент не должен получать
    чужие внутренние флаги даже в теле ответа.
    """
    q = select(Submission)
    if assignment_id:
        q = q.where(Submission.assignment_id == assignment_id)
    if user.role == Role.REVIEWER:
        # Устаревшие версии не занимают очередь: проверять нужно последнюю.
        q = q.where(Submission.reviewer_id == user.id, Submission.superseded.is_(False))
    elif user.role == Role.STUDENT:
        # Студенту, наоборот, видны все свои попытки — это его история.
        # Кроме строк без файла: это нагрузка прошлого потока, заведённая
        # ради демонстрации распределения. Студент такую работу не сдавал,
        # и в его списке она выглядела бы как чужая сдача «на проверке».
        q = q.where(Submission.student_id == user.id, Submission.file_path != "")

    subs = (await session.execute(q)).scalars().all()
    assignments = {
        a.id: a for a in (await session.execute(select(Assignment))).scalars()
    }
    users = {u.id: u for u in (await session.execute(select(User))).scalars()}

    return [
        _submission_dict(s, assignments.get(s.assignment_id), users, role=user.role)
        for s in sorted(subs, key=lambda s: (-s.priority_index, s.submitted_at))
    ]


def _rubric_max(a: Assignment | None) -> float | None:
    """Сумма максимумов критериев задания. `None`, если критериев нет."""
    criteria = ((a.rubric or {}) if a else {}).get("criteria", [])
    total = sum(float(c.get("max_score") or 0.0) for c in criteria)
    return total or None


def _track_name(track_id: str) -> str:
    """Человеческое название направления по идентификатору."""
    return next((t["name"] for t in TRACKS if t["id"] == track_id), track_id)


def _submission_dict(
    s: Submission, a: Assignment | None, users: dict[str, User], *, role: str
) -> dict:
    review = s.review or {}
    status_info = evaluate(
        submitted_at=s.submitted_at,
        due_at=a.due_at if a else None,
        hard_due_at=a.hard_due_at if a else None,
        due_soon_lead_s=a.due_soon_lead_s if a else 3600,
    )
    rstate, rtext, _ = review_status(
        review_due_at=a.review_due_at if a else None, confirmed_at=s.confirmed_at
    )

    data = {
        "id": s.id,
        "assignment_id": s.assignment_id,
        "assignment_title": a.title if a else "",
        "file_name": s.file_name,
        # Интерфейс обязан знать, есть ли что проверять: иначе он предлагает
        # «Запустить проверку» на строке без файла и получает отказ.
        "has_file": s.has_file,
        "track": s.track,
        # Читаемое имя направления. Слаг `product_business_models` в карточке
        # работы читался как внутренний идентификатор — потому что им и был.
        "track_name": _track_name(s.track),
        "detected_track": s.detected_track,
        "status": s.status,
        "submitted_at": s.submitted_at.isoformat(),
        "version": s.version,
        "previous_id": s.previous_id,
        "superseded": s.superseded,
        "student_id": s.student_id,
        "student_name": (u.name if (u := users.get(s.student_id)) else s.student_id),
        "reviewer_id": s.reviewer_id,
        "reviewer_name": (u.name if (u := users.get(s.reviewer_id or "")) else None),
        "deadline_state": s.deadline_state,
        "deadline_label": status_info.label,
        "deadline_detail": status_info.detail,
        "late_penalty": s.late_penalty,
        "review_state": rstate,
        "review_state_text": rtext,
        "preliminary_score": review.get("preliminary_score"),
        # Максимум берётся из проверки, а если её ещё не было — из критериев
        # задания. Интерфейс подставлял на это место десятку, и работа курса
        # с максимумом 20 показывалась студенту как «X / 10».
        "max_score": review.get("max_score") or _rubric_max(a),
        "final_score": s.final_score,
        "confirmed_at": s.confirmed_at.isoformat() if s.confirmed_at else None,
    }

    # Студент не видит внутренние флаги: приоритет проверки, сигнал генИИ,
    # схожесть с чужими работами. Это внутренняя кухня ревью.
    if role != Role.STUDENT:
        data |= {
            "priority_index": s.priority_index,
            "ai_score": s.ai_score,
            "ai_verdict": s.ai_verdict,
            "summary": review.get("reviewer_summary", ""),
            "failed_steps": [t["name"] for t in review.get("trace", []) if not t.get("ok")],
            # Отправленный текст обратной связи нужен и ревьюеру: без него
            # у подтверждённой работы поле в интерфейсе оказывалось пустым,
            # и ревьюер не видел, что именно он отправил студенту.
            "feedback": s.final_feedback,
        }
    else:
        data |= {"feedback": s.final_feedback if s.confirmed_at else ""}
    return data


@router.get("/submissions/{submission_id}")
async def get_submission(
    submission_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Полная карточка работы: ревью, документ по блокам, аннотации."""
    s = await session.get(Submission, submission_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Работа не найдена")
    if user.role == Role.STUDENT and s.student_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это чужая работа")
    if user.role == Role.REVIEWER and s.reviewer_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Работа назначена другому ревьюеру")

    a = await session.get(Assignment, s.assignment_id)
    users = {u.id: u for u in (await session.execute(select(User))).scalars()}
    data = _submission_dict(s, a, users, role=user.role)

    if user.role == Role.STUDENT:
        # Студенту — только подтверждённый человеком результат.
        return data | {
            "feedback": s.final_feedback if s.confirmed_at else "",
            "published": s.confirmed_at is not None,
        }

    blocks: list[dict] = []
    try:
        doc = read_any(s.file_path)
        blocks = [
            {"index": b.index, "kind": b.kind.value, "text": b.text,
             "table": b.table, "style": b.style,
             # Четвёртый слой просмотрщика: что именно было заменено
             # псевдонимом перед отправкой в модель. Показывается по
             # исходному тексту — ревьюер видит и значение, и факт замены.
             "pii": _pii_spans(b.text)}
            for b in doc.blocks
        ]
    except Exception as exc:  # noqa: BLE001
        data["document_error"] = f"Документ не прочитан: {exc}"

    # Повторная сдача: краткая карточка предыдущей версии для сравнения.
    previous = None
    if s.previous_id and (p := await session.get(Submission, s.previous_id)) is not None:
        prev_review = p.review or {}
        previous = {
            "id": p.id,
            "version": p.version,
            "file_name": p.file_name,
            "submitted_at": p.submitted_at.isoformat(),
            "final_score": p.final_score,
            "preliminary_score": prev_review.get("preliminary_score"),
            "max_score": prev_review.get("max_score"),
            "feedback": p.final_feedback,
            "per_criterion": {
                c.get("criterion_id"): c.get("score")
                for c in prev_review.get("criteria", [])
            },
        }

    return data | {"review": s.review, "blocks": blocks,
                   "previous": previous,
                   "rubric": (a.rubric if a else None),
                   "rubric_approved": bool(a.rubric_approved) if a else False}


def _pii_spans(text: str) -> list[dict]:
    """Позиции персональных данных в исходном тексте блока.

    Считается тем же щитом, что маскирует текст перед вызовом модели, а не
    отдельной эвристикой для показа: иначе интерфейс рисовал бы одно, а в
    модель уходило другое, и подсветка стала бы декорацией.
    """
    from app.privacy.pii import mask_text

    if not text:
        return []
    report = mask_text(text)
    spans: list[dict] = []
    for token, original in report.mapping.items():
        label = token.rsplit("_", 1)[0]
        start = 0
        while (pos := text.find(original, start)) != -1:
            spans.append({"start": pos, "end": pos + len(original), "label": label})
            start = pos + len(original)
    # Пересечения ломают разрезание строки: телефон внутри ссылки, имя внутри
    # почты. Оставляем длинное совпадение, короткое отбрасываем.
    spans.sort(key=lambda s: (s["start"], -(s["end"] - s["start"])))
    out: list[dict] = []
    for span in spans:
        if out and span["start"] < out[-1]["end"]:
            continue
        out.append(span)
    return out


# ── распределение ─────────────────────────────────────────────────────────────


@router.post("/assignments/{assignment_id}/allocate")
async def allocate_submissions(
    assignment_id: str,
    user: User = Depends(require_role(Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Автораспределение работ по ревьюерам с учётом нагрузки."""
    from app.workload.allocator import ReviewerLoad, WorkItem, allocate, balance_score

    a = await session.get(Assignment, assignment_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задание не найдено")

    pending = (
        await session.execute(
            select(Submission).where(
                Submission.assignment_id == assignment_id,
                Submission.reviewer_id.is_(None),
                Submission.superseded.is_(False),
            )
        )
    ).scalars().all()
    if not pending:
        return {"ok": True, "assigned": 0, "note": "нераспределённых работ нет"}

    reviewers = (
        await session.execute(select(User).where(User.role == Role.REVIEWER, User.active))
    ).scalars().all()
    if not reviewers:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нет активных ревьюеров")

    loads: list[ReviewerLoad] = []
    for r in reviewers:
        current = (
            await session.execute(
                select(func.count()).select_from(Submission).where(
                    Submission.reviewer_id == r.id,
                    Submission.status != SubmissionStatus.CONFIRMED,
                )
            )
        ).scalar_one()
        loads.append(
            ReviewerLoad(id=r.id, name=r.name, capacity=r.capacity,
                         current_load=current, competencies=list(r.competencies or []))
        )

    # Кто проверял предыдущую версию — компонент стоимости «преемственность
    # ревьюера». Читается одним запросом, а не по работе, чтобы распределение
    # не превращалось в N+1.
    prev_reviewers: dict[str, str] = {}
    prev_ids = [s.previous_id for s in pending if s.previous_id]
    if prev_ids:
        rows = (
            await session.execute(
                select(Submission.id, Submission.reviewer_id).where(Submission.id.in_(prev_ids))
            )
        ).all()
        prev_reviewers = {sid: rid for sid, rid in rows if rid}

    works = []
    for s in pending:
        try:
            effort = min(3.0, max(0.5, read_any(s.file_path).char_count / 5000))
        except Exception:  # noqa: BLE001
            effort = 1.0
        works.append(
            WorkItem(
                id=s.id, track=s.track, effort=effort, due_at=a.review_due_at,
                is_repeat=s.version > 1,
                previous_reviewer_id=prev_reviewers.get(s.previous_id or ""),
            )
        )

    result = allocate(works, loads)
    by_id = {s.id: s for s in pending}
    assigned = 0
    for alloc in result.allocations:
        if alloc.reviewer_id is None:
            continue
        sub = by_id[alloc.work_id]
        sub.reviewer_id = alloc.reviewer_id
        sub.status = SubmissionStatus.ASSIGNED
        assigned += 1

    # Одно нажатие заменяет ручное распределение каждой работы.
    await log_action(session, user, "allocate", target=assignment_id,
                     manual_actions_saved=assigned)
    await session.commit()

    from app.notify.scheduler import notify

    for alloc in result.allocations:
        if alloc.reviewer_id:
            await notify(
                session, user_id=alloc.reviewer_id, kind="assignment.new",
                title="Назначена работа на проверку",
                body=f"«{by_id[alloc.work_id].file_name}». {', '.join(alloc.reasons[:2])}",
                submission_id=alloc.work_id,
            )
    await session.commit()

    payload = {
        "ok": True,
        "assigned": assigned,
        "load_before": result.load_before,
        "load_after": result.load_after,
        "balance_before": balance_score(result.load_before, loads),
        "balance_after": balance_score(result.load_after, loads),
        "max_before": result.max_load_before,
        "max_after": result.max_load_after,
        "reviewers": [{"id": r.id, "name": r.name, "capacity": r.capacity} for r in loads],
        "unassigned": result.unassigned,
        "notes": result.notes,
        "allocations": [
            {"work_id": x.work_id, "reviewer_id": x.reviewer_id,
             "cost": x.cost, "reasons": x.reasons}
            for x in result.allocations
        ],
    }
    await bus.publish("allocation_done", payload)
    return payload


class ReassignIn(BaseModel):
    reviewer_id: str


@router.put("/submissions/{submission_id}/reviewer")
async def reassign(
    submission_id: str,
    body: ReassignIn,
    user: User = Depends(require_role(Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Ручной перенос работы — последнее слово за методистом."""
    s = await session.get(Submission, submission_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Работа не найдена")
    # Идентификатор ревьюера не проверялся вовсе. Опечатка в нём молча
    # переводила работу на несуществующего человека: из очереди она
    # исчезала у всех и не появлялась ни у кого — работа просто пропадала.
    if body.reviewer_id:
        target = await session.get(User, body.reviewer_id)
        if target is None or target.role != Role.REVIEWER or not target.active:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Такого ревьюера нет — работа осталась у прежнего.",
            )
    s.reviewer_id = body.reviewer_id or None
    s.status = (
        SubmissionStatus.ASSIGNED if body.reviewer_id else SubmissionStatus.UPLOADED
    )
    await log_action(session, user, "reassign", target=submission_id)
    await session.commit()
    await bus.publish("submission_updated", {"submission_id": submission_id})
    return {"ok": True}


# ── запуск ревью ──────────────────────────────────────────────────────────────


@router.post("/submissions/{submission_id}/review")
async def start_review(
    submission_id: str,
    user: User = Depends(require_role(Role.COORDINATOR, Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    s = await session.get(Submission, submission_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Работа не найдена")
    # Работы без файла проверять нечем. Отказ обязан называть причину:
    # раньше такая попытка доходила до читателя и возвращалась ошибкой
    # «Формат '' не поддерживается» со списком 31 расширения — по ней
    # нельзя было понять, что файла просто нет.
    if not s.has_file:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "К этой работе не приложен файл, проверять нечего. "
            "Так выглядят строки нагрузки прошлого потока: они заняты у "
            "ревьюера, но содержимого у них нет. Попросите студента "
            "загрузить файл или удалите строку."
            if not s.file_path
            else f"Файл работы не найден на диске: {s.file_name}. "
                 f"Загрузите работу заново.",
        )
    a = await session.get(Assignment, s.assignment_id)
    if a is None or not a.rubric_approved:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Критерии оценивания не утверждены — проверка не запускается: "
            "ошибка в критериях исказила бы баллы всех работ разом. "
            "Задайте критерии и утвердите их у методиста.",
        )
    job_id = await queue.enqueue(submission_id)
    await log_action(session, user, "review.start", target=submission_id,
                     manual_actions_saved=1)
    await session.commit()
    return {"ok": True, "job_id": job_id, "queue_depth": queue.depth}


@router.post("/assignments/{assignment_id}/review-all")
async def review_all(
    assignment_id: str,
    user: User = Depends(require_role(Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Массовый запуск ревью по всем работам задания."""
    a = await session.get(Assignment, assignment_id)
    if a is None or not a.rubric_approved:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Критерии оценивания не утверждены — проверка не запускается",
        )

    subs = (
        await session.execute(
            select(Submission).where(
                Submission.assignment_id == assignment_id,
                Submission.superseded.is_(False),
                Submission.status.in_([
                    SubmissionStatus.UPLOADED, SubmissionStatus.ASSIGNED,
                    SubmissionStatus.FAILED,
                ]),
            )
        )
    ).scalars().all()

    # Работы без файла в очередь не ставим. Такие строки бывают у нагрузки
    # прошлого потока: они занимают ёмкость ревьюера, но проверять в них
    # нечего, и попытка дала бы отказ «формат не поддерживается» на каждой.
    ready = [s for s in subs if s.has_file]
    skipped = [s.file_name for s in subs if s not in ready]

    jobs = [await queue.enqueue(s.id) for s in ready]
    await log_action(session, user, "review.start_all", target=assignment_id,
                     manual_actions_saved=len(jobs) * 3)
    await session.commit()
    return {
        "ok": True,
        "queued": len(jobs),
        "job_ids": jobs,
        "skipped": skipped,
        "note": (
            f"Пропущено работ без файла: {len(skipped)}." if skipped else ""
        ),
    }


# ── подтверждение ревьюером ───────────────────────────────────────────────────


class ConfirmIn(BaseModel):
    final_score: float
    feedback: str = ""
    criteria_scores: dict[str, float] | None = None


@router.post("/submissions/{submission_id}/confirm")
async def confirm(
    submission_id: str,
    body: ConfirmIn,
    user: User = Depends(require_role(Role.REVIEWER, Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Подтверждение результата человеком.

    Только после этого шага студент видит оценку. Предварительный балл сам по
    себе никогда не публикуется: итоговое решение принимает ревьюер.
    """
    s = await session.get(Submission, submission_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Работа не найдена")

    # Подтверждает назначенный ревьюер либо методист. Проверки не было
    # вовсе: любой ревьюер мог выставить оценку по чужой работе — при том
    # что открыть её ему запрещено (403 в `get_submission`). Смотреть
    # нельзя, а оценивать можно — так быть не должно.
    if user.role == Role.REVIEWER and s.reviewer_id != user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Работа назначена другому ревьюеру. Подтвердить результат может "
            "он или методист.",
        )

    review = dict(s.review or {})
    criteria = review.get("criteria", [])

    # Максимум балла: из результата проверки, а если её не было — из
    # критериев задания. Без этого запаса у непроверенной работы верхняя
    # граница оказывалась нулевой, проверка отключалась, и балл 999
    # публиковался студенту. Ровно это и произошло на живом запросе.
    max_score = float(review.get("max_score") or 0.0)
    if not max_score:
        assignment = await session.get(Assignment, s.assignment_id)
        rubric_criteria = ((assignment.rubric or {}) if assignment else {}).get("criteria", [])
        max_score = sum(float(c.get("max_score") or 0.0) for c in rubric_criteria)

    # Баллы по критериям: каждый в своих границах. Опечатка в поле ввода
    # уходила студенту как есть.
    if body.criteria_scores:
        for c in criteria:
            new = body.criteria_scores.get(c["criterion_id"])
            if new is None:
                continue
            top = float(c.get("max_score") or 0.0)
            if new < 0 or (top and new > top):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Балл по критерию «{c.get('criterion_name', c['criterion_id'])}» "
                    f"должен быть от 0 до {top:g}, получено {new:g}.",
                )
            c["score"] = new

    # Итог считает сервер, а не клиент.
    #
    # Раньше `final_score` принимался как есть: проверено запросом — балл
    # 999 из 20 и балл −50 публиковались студенту без единого возражения.
    # Хуже другое: штраф за нарушение срока вычитал **интерфейс**, и при
    # обращении к API мимо него штраф просто исчезал — при том что правило
    # «досдача в течение суток, штраф −1 балл» взято из условия задания.
    #
    # Рычаг ревьюера — баллы по критериям; итог из них выводится. Поэтому
    # при переданных баллах сервер пересчитывает сумму сам и сам вычитает
    # штрафы.
    penalties = sum(float(p.get("amount") or 0.0) for p in review.get("penalties", []))
    if body.criteria_scores and criteria:
        # Суммируются ВСЕ критерии, включая те, что модель оценить не смогла.
        # Флаг `failed` означает «автоматика не справилась», и интерфейс прямо
        # предлагает ревьюеру поставить балл руками — «оцените вручную».
        # Исключать такие критерии из суммы значило бы молча выбросить
        # выставленную человеком оценку.
        base = sum(float(c.get("score") or 0.0) for c in criteria)
        final_score = round(max(0.0, base - penalties), 2)
    else:
        final_score = body.final_score

    # Жёсткий срок пройден — по правилу из условия работа оценивается в ноль.
    # Это не мнение ревьюера и не вычитаемый штраф, а прямое требование, и
    # соблюдать его должен сервер: до этого работа, просроченная
    # окончательно, подтверждалась с полным баллом.
    forced_zero = s.deadline_state == DeadlineState.LATE_ZERO
    if forced_zero:
        final_score = 0.0

    if final_score < 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Итоговый балл не может быть отрицательным, получено {final_score:g}.",
        )
    if max_score and final_score > max_score:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Итоговый балл должен быть от 0 до {max_score:g}, получено {final_score:g}.",
        )
    if not max_score:
        # Максимум неизвестен ни из проверки, ни из критериев — значит
        # критерии ещё не заданы. Оценивать нечем, и лучше сказать это,
        # чем принять любое число.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Максимальный балл неизвестен: у задания не заданы критерии. "
            "Задайте их, иначе непонятно, из чего выставляется оценка.",
        )

    preliminary = review.get("preliminary_score")
    s.final_score = final_score
    s.final_feedback = body.feedback or review.get("student_feedback", "")
    s.confirmed_by = user.id
    s.confirmed_at = now_utc()
    s.status = SubmissionStatus.CONFIRMED
    s.score_edited = preliminary is not None and abs(preliminary - final_score) > 1e-6

    if body.criteria_scores and s.review:
        s.review = review

    await log_action(
        session, user, "review.confirm", target=submission_id,
        manual_actions_saved=3,  # заменяет перенос балла, комментария и статуса в таблицу
        score_edited=s.score_edited,
    )

    from app.notify.scheduler import notify

    await notify(
        session, user_id=s.student_id, kind="review.published",
        title="Проверка завершена",
        body=f"По работе «{s.file_name}» готова обратная связь. Балл: {final_score:g}.",
        submission_id=s.id, severity="success",
    )
    await session.commit()

    await bus.publish(
        "review_confirmed",
        {"submission_id": s.id, "final_score": final_score,
         "score_edited": s.score_edited},
    )
    return {
        "ok": True,
        "final_score": final_score,
        "score_edited": s.score_edited,
        # Клиент прислал одно, сервер посчитал другое — об этом надо сказать,
        # а не молча разойтись в цифрах.
        "client_score": body.final_score,
        "recomputed": abs(body.final_score - final_score) > 1e-6,
        "penalties_applied": penalties,
        "forced_zero": forced_zero,
    }


class AIVerdictIn(BaseModel):
    verdict: str  # confirmed | rejected
    comment: str = ""


@router.post("/submissions/{submission_id}/ai-verdict")
async def ai_verdict(
    submission_id: str,
    body: AIVerdictIn,
    user: User = Depends(require_role(Role.REVIEWER, Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Решение ревьюера по сигналу генеративного ИИ.

    Сигнал — не доказательство. Подтвердить или отклонить его может только
    человек, и его решение фиксируется.
    """
    if body.verdict not in {"confirmed", "rejected"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "verdict: confirmed | rejected")
    s = await session.get(Submission, submission_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Работа не найдена")
    s.ai_verdict = body.verdict
    s.ai_verdict_comment = body.comment
    await log_action(session, user, "ai.verdict", target=submission_id, verdict=body.verdict)
    await session.commit()
    await bus.publish("ai_verdict", {"submission_id": s.id, "verdict": body.verdict})
    return {"ok": True}


# ── схожесть работ ────────────────────────────────────────────────────────────


@router.get("/assignments/{assignment_id}/similarity")
async def similarity(
    assignment_id: str,
    user: User = Depends(require_role(Role.COORDINATOR, Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Попарная схожесть работ потока: дублирование и заимствования."""
    from app.detect.similarity import SUSPICIOUS, compare_texts

    # Устаревшие версии в сравнение не идут. Доработанная работа неизбежно
    # похожа на собственную предыдущую версию — это не заимствование, а
    # нормальный ход процесса, и в отчёте о плагиате такая пара была бы
    # ложным обвинением. Сравниваются только актуальные версии.
    subs = (
        await session.execute(
            select(Submission).where(
                Submission.assignment_id == assignment_id,
                Submission.superseded.is_(False),
            )
        )
    ).scalars().all()

    texts: dict[str, str] = {}
    names: dict[str, str] = {}
    for s in subs:
        try:
            texts[s.id] = read_any(s.file_path).text
            names[s.id] = s.file_name
        except Exception:  # noqa: BLE001
            continue

    report = await asyncio.to_thread(compare_texts, texts)
    return {
        "threshold": SUSPICIOUS,
        "pairs": [
            {
                "a_id": p.a_id, "b_id": p.b_id,
                "a_name": names.get(p.a_id, ""), "b_name": names.get(p.b_id, ""),
                "cosine": p.cosine, "shingle": p.shingle, "score": p.score,
                "suspicious": p.is_suspicious, "fragments": p.shared_fragments,
            }
            for p in report.pairs
        ],
        "notes": report.notes,
    }


# ── уведомления ───────────────────────────────────────────────────────────────


@router.get("/notifications")
async def notifications(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[dict]:
    rows = (
        await session.execute(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc())
            .limit(80)
        )
    ).scalars().all()
    return [
        {"id": n.id, "kind": n.kind, "title": n.title, "body": n.body,
         "severity": n.severity, "read": n.read, "submission_id": n.submission_id,
         "created_at": n.created_at.isoformat()}
        for n in rows
    ]


@router.post("/notifications/read")
async def mark_read(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict:
    rows = (
        await session.execute(
            select(Notification).where(
                Notification.user_id == user.id, Notification.read.is_(False)
            )
        )
    ).scalars().all()
    for n in rows:
        n.read = True
    await session.commit()
    return {"ok": True, "marked": len(rows)}


# ── аналитика ─────────────────────────────────────────────────────────────────

# Базовая линия: сколько ручных действий на одну работу требует процесс AS-IS.
# Числа выведены из семи шагов, описанных в кейсе, и зафиксированы в
# docs/as-is-to-be.md, чтобы метрику можно было проверить, а не принять на веру.
MANUAL_BASELINE_PER_SUBMISSION = 11


@router.get("/analytics")
async def analytics(
    assignment_id: str | None = None,
    user: User = Depends(require_role(Role.COORDINATOR, Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Аналитика прогресса и качества проверки, включая метрики эффекта."""
    q = select(Submission)
    if assignment_id:
        q = q.where(Submission.assignment_id == assignment_id)
    subs = (await session.execute(q)).scalars().all()
    assignments = {a.id: a for a in (await session.execute(select(Assignment))).scalars()}
    users = {u.id: u for u in (await session.execute(select(User))).scalars()}

    total = len(subs)
    confirmed = [s for s in subs if s.confirmed_at]
    reviewed = [s for s in subs if s.review]

    # Согласие ревьюера с автоматикой: доля подтверждений без правки балла.
    agreed = sum(1 for s in confirmed if not s.score_edited)

    overdue_reviews = 0
    for s in subs:
        a = assignments.get(s.assignment_id)
        if a is None:
            continue
        state, _, _ = review_status(review_due_at=a.review_due_at, confirmed_at=s.confirmed_at)
        if state in {"overdue", "overdue_closed"}:
            overdue_reviews += 1

    scores = [s.final_score for s in confirmed if s.final_score is not None]
    prelim = [
        s.review["preliminary_score"] for s in reviewed
        if s.review and s.review.get("preliminary_score") is not None
    ]

    # Доли от максимума. Складывать сырые баллы разных заданий нельзя:
    # максимум у продуктового фрода 10, у QA 20, у системного дизайна 6, и
    # «средний балл 11,3» по такому набору не значит ничего. Для одного
    # задания доля и сырой балл несут одно и то же, для потока — только доля.
    shares: list[float] = []
    for s in reviewed:
        review = s.review or {}
        top = review.get("max_score") or 0
        value = s.final_score if s.final_score is not None else review.get("preliminary_score")
        if top and value is not None:
            shares.append(round(float(value) / float(top), 3))

    # Базовая линия считается только по работам, реально прошедшим процесс.
    # Строки без файла — нагрузка прошлого потока, они занимают ёмкость
    # ревьюера, но через проверку в этом потоке не проходили. Включать их в
    # знаменатель значило бы завысить эффект: метрику должно быть можно
    # оспорить, а не только показать.
    processed = [s for s in subs if s.file_path]
    baseline = len(processed) * MANUAL_BASELINE_PER_SUBMISSION

    # Фактические действия из журнала — не оценка, а счётчик.
    #
    # Считаются РОВНО те действия, что относятся к работам из знаменателя.
    # Это правило нарушалось дважды. Сначала числитель брал весь журнал, а
    # знаменатель — одно задание: со вторым курсом метрика показывала
    # «было 33, стало 45, сокращение −36 %». Потом фильтр по заданию
    # починили, но в режиме «весь поток» числитель по-прежнему считал
    # журнал целиком — вместе с входами в систему, регистрациями, правкой
    # критериев и заведением участников. Появление формы входа мгновенно
    # это проявило: «было 110, стало 146».
    #
    # Ни вход в систему, ни создание задания не являются работой над
    # конкретной сдачей, и сравнивать их с базовой линией «11 действий на
    # работу» бессмысленно. Поэтому в числителе — только действия с
    # указателем на работу из знаменателя.
    scope = {s.id for s in processed}
    actions = (
        (
            await session.execute(select(ActionLog).where(ActionLog.target.in_(scope)))
        ).scalars().all()
        if scope
        else []
    )
    actual_actions = len(actions)
    saved = sum(a.manual_actions_saved for a in actions)

    durations = [
        s.review.get("duration_ms", 0) / 1000 for s in reviewed if s.review
    ]

    by_reviewer: dict[str, dict] = {}
    for s in subs:
        if not s.reviewer_id:
            continue
        r = by_reviewer.setdefault(
            s.reviewer_id,
            {"name": users[s.reviewer_id].name if s.reviewer_id in users else s.reviewer_id,
             "capacity": users[s.reviewer_id].capacity if s.reviewer_id in users else 0,
             "assigned": 0, "confirmed": 0},
        )
        r["assigned"] += 1
        if s.confirmed_at:
            r["confirmed"] += 1

    return {
        "funnel": {
            "uploaded": total,
            "assigned": sum(1 for s in subs if s.reviewer_id),
            "ai_reviewed": len(reviewed),
            "confirmed": len(confirmed),
        },
        "deadline_states": {
            st.value: sum(1 for s in subs if s.deadline_state == st.value)
            for st in DeadlineState
        },
        "scores": {
            "final": scores,
            "preliminary": prelim,
            "shares": shares,
            "mean_final": (sum(scores) / len(scores)) if scores else None,
            "mean_preliminary": (sum(prelim) / len(prelim)) if prelim else None,
            "mean_share": (sum(shares) / len(shares)) if shares else None,
            # Одна шкала на весь поток есть только когда задание одно.
            "single_scale": len({s.assignment_id for s in reviewed}) <= 1,
        },
        "quality": {
            "confirmed": len(confirmed),
            "agreed_without_edit": agreed,
            "agreement_rate": (agreed / len(confirmed)) if confirmed else None,
            "mean_review_seconds": (sum(durations) / len(durations)) if durations else None,
        },
        "effect": {
            # Формулировки — дословно из критериев успеха кейса.
            "manual_actions_baseline": baseline,
            "manual_actions_actual": actual_actions,
            "submissions_processed": len(processed),
            "manual_actions_saved": saved,
            "reduction_rate": (
                round(1 - actual_actions / baseline, 3) if baseline else None
            ),
            "overdue_reviews": overdue_reviews,
            "mean_processing_seconds": (sum(durations) / len(durations)) if durations else None,
        },
        "by_reviewer": list(by_reviewer.values()),
        "by_criterion": _by_criterion(reviewed),
        "priority_buckets": _priority_buckets(reviewed),
        "agreement": _agreement(confirmed),
        "durations": sorted(round(d, 1) for d in durations),
        "by_day": _by_day(subs),
        "common_gaps": _common_gaps(reviewed),
    }


def _by_criterion(subs: list[Submission]) -> list[dict]:
    """Средняя доля от максимума по каждому критерию — где проседает поток.

    Именно доля, а не сырой балл: критерии весят от 0,5 до 8, и на абсолютной
    шкале самый дорогой критерий всегда выглядел бы худшим.

    Рядом — доля подтверждённых цитат. Низкая доля означает не слабую работу,
    а слабое место самого механизма доказательств на этом критерии.
    """
    acc: dict[str, dict] = {}
    for s in subs:
        for c in (s.review or {}).get("criteria", []):
            top = float(c.get("max_score") or 0)
            if not top:
                continue
            e = acc.setdefault(
                c.get("criterion_id") or c.get("criterion_name", "?"),
                {"name": c.get("criterion_name", "—"), "share": 0.0, "works": 0,
                 "verified": 0, "total": 0, "max_score": top},
            )
            e["share"] += float(c.get("score") or 0) / top
            e["works"] += 1
            e["verified"] += int(c.get("evidence_verified") or 0)
            e["total"] += int(c.get("evidence_total") or 0)

    out = [
        {
            "criterion_id": cid,
            "name": e["name"],
            "max_score": e["max_score"],
            "mean_share": round(e["share"] / e["works"], 3),
            "works": e["works"],
            "verified_rate": round(e["verified"] / e["total"], 3) if e["total"] else None,
        }
        for cid, e in acc.items()
    ]
    return sorted(out, key=lambda x: x["mean_share"])


def _priority_buckets(subs: list[Submission]) -> dict:
    """Сколько работ в каждой зоне приоритета ручной проверки.

    Границы те же, что в очереди ревьюера: интерфейс и аналитика обязаны
    называть «высоким» одно и то же.
    """
    buckets = {"low": 0, "medium": 0, "high": 0}
    for s in subs:
        p = s.priority_index or 0.0
        buckets["high" if p >= 0.5 else "medium" if p >= 0.25 else "low"] += 1
    return buckets


def _agreement(confirmed: list[Submission]) -> dict:
    """Насколько ревьюер соглашается с предварительным баллом.

    Это метрика доверия к автоматике, и она честнее доли подтверждений: важно
    не только сколько раз балл правили, но и на сколько. Правка на полбалла и
    правка на четыре — разные истории.
    """
    deltas: list[float] = []
    for s in confirmed:
        pre = (s.review or {}).get("preliminary_score")
        if pre is not None and s.final_score is not None:
            deltas.append(round(float(s.final_score) - float(pre), 2))
    edited = [d for d in deltas if abs(d) > 1e-9]
    return {
        "confirmed": len(confirmed),
        "without_edit": len(confirmed) - len(edited),
        "with_edit": len(edited),
        "deltas": sorted(deltas),
        "mean_abs_delta": round(sum(abs(d) for d in edited) / len(edited), 2) if edited else 0.0,
    }


def _by_day(subs: list[Submission]) -> list[dict]:
    """Сдачи и подтверждения по дням.

    Дни идут подряд, включая пустые: пропуск дня без сдач превратил бы
    провал в потоке в ровный график и спрятал бы именно то, ради чего
    методист сюда смотрит.
    """
    from collections import Counter
    from datetime import date, timedelta

    if not subs:
        return []
    submitted: Counter[date] = Counter()
    confirmed: Counter[date] = Counter()
    for s in subs:
        submitted[as_utc(s.submitted_at).date()] += 1
        if s.confirmed_at:
            confirmed[as_utc(s.confirmed_at).date()] += 1

    start, end = min(submitted), max(list(submitted) + list(confirmed))
    out: list[dict] = []
    day = start
    # Ограничение сверху — защита от случайной даты из будущего в данных:
    # такой ряд растянул бы график на годы пустых столбцов.
    while day <= end and len(out) < 120:
        out.append({
            "date": day.isoformat(),
            "submitted": submitted.get(day, 0),
            "confirmed": confirmed.get(day, 0),
        })
        day += timedelta(days=1)
    return out


def _common_gaps(subs: list[Submission], limit: int = 12) -> list[dict]:
    """Типовые ошибки потока → предложения по улучшению материалов.

    Кластеризация нарочно грубая — по нормализованному первому предложению
    пробела. Тонкая кластеризация потребовала бы эмбеддингов, а для «что
    объяснить курсу подробнее» хватает и частотного списка.
    """
    import re
    from collections import Counter

    counter: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for s in subs:
        for c in (s.review or {}).get("criteria", []):
            for gap in c.get("gaps", []):
                key = re.sub(r"[^а-яёa-z ]", "", gap.lower())
                key = " ".join(key.split()[:6])
                if len(key) < 12:
                    continue
                counter[key] += 1
                examples.setdefault(key, gap)
    return [
        {"pattern": examples[k], "count": n}
        for k, n in counter.most_common(limit)
        if n > 1
    ]


# ── профиль студента ──────────────────────────────────────────────────────────


@router.get("/students/{student_id}/profile")
async def student_profile(
    student_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Профиль студента: динамика по сдачам и профиль по критериям.

    Радар строится по **долям от максимума** критерия, а не по абсолютным
    баллам: критерии в рубрике весят от 1 до 4, и на абсолютной шкале самый
    дорогой критерий всегда выглядел бы сильнейшей стороной студента.

    Динамика считается по подтверждённому баллу, если он есть: итог
    студента — тот, который поставил человек.
    """
    if user.role == Role.STUDENT and user.id != student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это чужой профиль")

    student = await session.get(User, student_id)
    if student is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Студент не найден")

    # Тот же фильтр, что и в списке работ студента: строки без файла —
    # демонстрационная нагрузка прошлого потока, студент их не сдавал.
    # Без фильтра профиль насчитывал 5 работ там, где в списке было 2.
    subs = (
        await session.execute(
            select(Submission).where(
                Submission.student_id == student_id, Submission.file_path != ""
            )
        )
    ).scalars().all()
    subs.sort(key=lambda s: as_utc(s.submitted_at))
    assignments = {a.id: a for a in (await session.execute(select(Assignment))).scalars()}

    timeline: list[dict] = []
    acc: dict[str, dict] = {}

    for s in subs:
        review = s.review or {}
        a = assignments.get(s.assignment_id)
        score = s.final_score if s.final_score is not None else review.get("preliminary_score")
        timeline.append({
            "submission_id": s.id,
            "assignment_title": a.title if a else "",
            "version": s.version,
            "submitted_at": s.submitted_at.isoformat(),
            "score": score,
            "max_score": review.get("max_score"),
            "confirmed": s.confirmed_at is not None,
            "deadline_state": s.deadline_state,
            "late_penalty": s.late_penalty,
        })
        # В радар идут только подтверждённые работы: показывать студенту
        # сильные и слабые стороны по неподтверждённой автоматике нельзя —
        # решение по баллу ещё не принято человеком.
        if not s.confirmed_at:
            continue
        for c in review.get("criteria", []):
            cid = c.get("criterion_id")
            maximum = float(c.get("max_score") or 0)
            if not cid or maximum <= 0:
                continue
            entry = acc.setdefault(
                cid, {"name": c.get("criterion_name", cid), "sum": 0.0, "n": 0}
            )
            entry["sum"] += float(c.get("score") or 0) / maximum
            entry["n"] += 1

    radar = [
        {"criterion_id": cid, "name": e["name"], "share": round(e["sum"] / e["n"], 3)}
        for cid, e in acc.items()
        if e["n"]
    ]

    scored = [t["score"] for t in timeline if t["score"] is not None]
    return {
        "student": {"id": student.id, "name": student.name},
        "timeline": timeline,
        "radar": radar,
        "summary": {
            "submissions": len(subs),
            "confirmed": sum(1 for s in subs if s.confirmed_at),
            "mean_score": round(sum(scored) / len(scored), 2) if scored else None,
            "best": max(scored) if scored else None,
            "late": sum(1 for s in subs if s.late_penalty),
            # Пустой радар — не ошибка, а нормальное состояние до первого
            # подтверждения. Интерфейс обязан сказать это словами.
            "radar_available": bool(radar),
        },
    }


# ── качество: результаты бенчмарка ────────────────────────────────────────────


@router.get("/quality")
async def quality_report() -> dict:
    """Отчёт бенчмарка из `docs/results/bench_report.json`.

    Отчёт читается с диска, а не пересчитывается по запросу: прогон занимает
    минуты и требует запущенной модели, а страница должна открываться
    мгновенно и работать даже когда провайдер выключен. Отсутствие файла —
    нормальное состояние до первого прогона, а не ошибка.
    """
    path = Path(__file__).resolve().parents[2] / "docs" / "results" / "bench_report.json"
    if not path.exists():
        return {
            "available": False,
            "hint": "Бенчмарк ещё не прогонялся: python -m app.bench.run_bench --runs 3",
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "hint": f"Отчёт не прочитан: {exc}"}
    return {"available": True, **data}


# ── пресеты формулы ───────────────────────────────────────────────────────────

# Встроенные пресеты. Нужны как точка возврата: после кручения ползунков
# методист одним нажатием возвращает поток к известной конфигурации, а не
# вспоминает исходные числа.
BUILTIN_PRESETS: dict[str, dict] = {
    "Базовый": {},
    "Строгий к автоматике": {
        "ai_signal": 0.5, "unverified_evidence": 0.35, "similarity": 0.35,
        "formal_violations": 0.2, "borderline_score": 0.2, "failed_steps": 0.4,
    },
    "Только формальные признаки": {
        "ai_signal_on": False, "similarity_on": False,
        "unverified_evidence": 0.3, "formal_violations": 0.4,
    },
}


@router.get("/scoring/presets")
async def list_presets(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Встроенные пресеты плюс сохранённые методистом."""
    saved = (await session.execute(select(ScoringPreset))).scalars().all()
    saved_names = {p.name for p in saved}
    out = [
        {"name": name, "config": cfg, "builtin": True}
        for name, cfg in BUILTIN_PRESETS.items()
        if name not in saved_names
    ]
    out += [
        {"name": p.name, "config": p.config or {}, "builtin": p.builtin} for p in saved
    ]
    return out


class PresetIn(BaseModel):
    name: str


@router.post("/scoring/presets")
async def save_preset(
    body: PresetIn,
    user: User = Depends(require_role(Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Сохраняет текущие веса и тумблеры под именем."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Имя пресета пустое")
    if name in BUILTIN_PRESETS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Имя занято встроенным пресетом"
        )

    config = dict(_scoring)
    existing = (
        await session.execute(select(ScoringPreset).where(ScoringPreset.name == name))
    ).scalars().first()
    if existing is not None:
        existing.config = config
    else:
        session.add(ScoringPreset(id=str(uuid.uuid4()), name=name, config=config))
    await log_action(session, user, "scoring.preset_save", target=name)
    await session.commit()
    return {"ok": True, "name": name}


@router.post("/scoring/presets/apply")
async def apply_preset(
    body: PresetIn,
    assignment_id: str | None = None,
    user: User = Depends(require_role(Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Применяет пресет к потоку и пересчитывает готовые ревью."""
    name = body.name.strip()
    saved = (
        await session.execute(select(ScoringPreset).where(ScoringPreset.name == name))
    ).scalars().first()
    if saved is not None:
        config = dict(saved.config or {})
    elif name in BUILTIN_PRESETS:
        config = dict(BUILTIN_PRESETS[name])
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пресет не найден")

    # Пресет задаёт конфигурацию целиком, а не поверх текущей: иначе после
    # применения «только формальные признаки» в настройках оставались бы
    # хвосты предыдущего, и одно и то же имя давало бы разный поток.
    _scoring.clear()
    _scoring.update(config)

    recalculated = await _recalculate_reviews(session, assignment_id)
    await log_action(session, user, "scoring.preset_apply", target=name)
    await session.commit()
    await bus.publish("scoring_updated", {"recalculated": recalculated, "preset": name})
    return {"ok": True, "name": name, "recalculated": recalculated}


@router.delete("/scoring/presets")
async def delete_preset(
    name: str,
    user: User = Depends(require_role(Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Удаляет сохранённый пресет. Встроенные не удаляются."""
    saved = (
        await session.execute(select(ScoringPreset).where(ScoringPreset.name == name))
    ).scalars().first()
    if saved is None or saved.builtin or name in BUILTIN_PRESETS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Пресет не найден или встроенный"
        )
    await session.delete(saved)
    await log_action(session, user, "scoring.preset_delete", target=name)
    await session.commit()
    return {"ok": True}


# ── экспорт ───────────────────────────────────────────────────────────────────


@router.get("/assignments/{assignment_id}/export")
async def export(
    assignment_id: str,
    fmt: str = "csv",
    user: User = Depends(require_role(Role.COORDINATOR, Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Выгрузка итогов — замена ручного переноса в Google Sheets.

    Форматы локальные: CSV и XLSX открываются той же таблицей, куда результаты
    переносили руками, но без ручного переноса. JSON — для передачи в другую
    систему. Внешних интеграций нет: контур офлайн.
    """
    # Неизвестный формат отвергается, а не подменяется молча. Раньше любой
    # `fmt` кроме `xlsx` отдавал CSV с кодом 200 и заголовком `text/csv`:
    # запрос `?fmt=json` получал таблицу, выглядящую как успешный ответ.
    allowed = {"csv", "xlsx", "json"}
    if fmt not in allowed:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Формат {fmt!r} не поддерживается. Доступны: {', '.join(sorted(allowed))}.",
        )

    subs = (
        await session.execute(
            select(Submission).where(Submission.assignment_id == assignment_id)
        )
    ).scalars().all()
    users = {u.id: u for u in (await session.execute(select(User))).scalars()}
    a = await session.get(Assignment, assignment_id)

    headers = [
        "Студент", "Файл", "Направление", "Сдано", "Состояние срока", "Штраф",
        "Предварительный балл", "Итоговый балл", "Балл изменён ревьюером",
        "Приоритет проверки", "Сигнал ИИ", "Вердикт по ИИ", "Ревьюер",
        "Статус", "Подтверждено",
    ]
    rows = []
    for s in subs:
        review = s.review or {}
        rows.append([
            users[s.student_id].name if s.student_id in users else s.student_id,
            s.file_name, s.track, s.submitted_at.strftime("%d.%m.%Y %H:%M"),
            s.deadline_state, f"{s.late_penalty:g}",
            review.get("preliminary_score", ""), s.final_score if s.final_score is not None else "",
            "да" if s.score_edited else "нет",
            f"{s.priority_index:.2f}", f"{s.ai_score:.2f}", s.ai_verdict or "",
            users[s.reviewer_id].name if s.reviewer_id in users else "",
            s.status, s.confirmed_at.strftime("%d.%m.%Y %H:%M") if s.confirmed_at else "",
        ])

    name = f"review_{(a.track if a else 'export')}_{datetime.now():%Y%m%d_%H%M}"

    if fmt == "json":
        payload = json.dumps(
            {
                "assignment": {"id": assignment_id, "title": a.title if a else "",
                               "track": a.track if a else ""},
                "exported_at": now_utc().isoformat(),
                "columns": headers,
                "rows": [dict(zip(headers, r, strict=True)) for r in rows],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        return StreamingResponse(
            iter([payload]),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{name}.json"'},
        )

    if fmt == "xlsx":
        import io

        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill

        wb = Workbook()
        ws = wb.active
        ws.title = "Проверка"
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F6FEB")
            cell.alignment = Alignment(horizontal="center", wrap_text=True)
        for r in rows:
            ws.append(r)
        for i, h in enumerate(headers, start=1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = max(12, min(28, len(h) + 4))
        ws.freeze_panes = "A2"

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
        )

    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(headers)
    w.writerows(rows)
    # BOM: без него Excel открывает CSV с кириллицей как мусор.
    data = "﻿" + buf.getvalue()
    return StreamingResponse(
        iter([data]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


@router.get("/submissions/{submission_id}/export")
async def export_review(
    submission_id: str,
    fmt: str = "md",
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Выгрузка одного ревью: Markdown, JSON или версия для студента."""
    s = await session.get(Submission, submission_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Работа не найдена")
    if user.role == Role.STUDENT and s.student_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это чужая работа")

    review = s.review or {}
    # Студенту — только фидбек, без внутренних флагов.
    student_view = user.role == Role.STUDENT or fmt == "student"

    if fmt == "json" and not student_view:
        return StreamingResponse(
            iter([json.dumps(review, ensure_ascii=False, indent=2)]),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="review_{s.id[:8]}.json"'},
        )

    lines = [f"# Результат проверки: {s.file_name}", ""]
    if student_view:
        lines += [
            f"**Балл:** {s.final_score if s.final_score is not None else '—'}",
            "",
            "## Обратная связь",
            "",
            s.final_feedback or review.get("student_feedback", "Обратная связь готовится."),
        ]
    else:
        lines += [
            f"- Предварительный балл: **{review.get('preliminary_score', '—')} "
            f"из {review.get('max_score', '—')}**",
            f"- Итоговый балл: **{s.final_score if s.final_score is not None else 'не подтверждён'}**",
            f"- Приоритет ручной проверки: {s.priority_index:.2f}",
            f"- Состояние срока: {s.deadline_state}, штраф {s.late_penalty:g}",
            "",
            "## Формальные проверки",
            "",
        ]
        icons = {"pass": "✔", "fail": "✘", "unknown": "?", "na": "—"}
        for f in review.get("formal", []):
            lines.append(f"- {icons.get(f['status'], '?')} **{f['title']}** — {f['message']}")

        lines += ["", "## Критерии", ""]
        for c in review.get("criteria", []):
            lines.append(f"### {c['criterion_name']} — {c['score']:g} из {c['max_score']:g}")
            lines.append("")
            lines.append(c.get("verdict", ""))
            if c.get("evidence"):
                lines.append("")
                lines.append("**Доказательства:**")
                for ev in c["evidence"]:
                    mark = "✔" if ev["status"] == "verified" else "✘"
                    lines.append(f"- {mark} §{ev['block']} ({ev['similarity']:g}%): «{ev['quote']}»")
            if c.get("gaps"):
                lines.append("")
                lines.append("**Пробелы:**")
                lines += [f"- {g}" for g in c["gaps"]]
            lines.append("")

        if ai := review.get("ai_signal"):
            lines += [
                "## Признаки генеративного ИИ", "",
                f"- Скор: {ai['score']:.2f}, уверенность: {ai['confidence']}",
                f"- Вердикт ревьюера: {s.ai_verdict or 'не вынесен'}",
                "", "**Основания:**",
                *[f"- {g}" for g in ai.get("grounds", [])],
                "", "**Ограничения метода:**",
                *[f"- {l}" for l in ai.get("limitations", [])],
                "",
            ]
        if fb := (s.final_feedback or review.get("student_feedback")):
            lines += ["## Обратная связь студенту", "", fb]

    body = "\n".join(lines)
    ext = "md"
    if fmt == "html":
        body = _md_to_html(body, title=f"Ревью: {s.file_name}")
        ext = "html"

    return StreamingResponse(
        iter([body]),
        media_type=f"text/{'html' if ext == 'html' else 'markdown'}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="review_{s.id[:8]}.{ext}"'},
    )


def _md_to_html(md: str, *, title: str) -> str:
    """Минимальный Markdown → HTML для печати в PDF из браузера.

    Полноценный конвертер не нужен: разметка в выгрузке своя и ограниченная,
    а лишняя зависимость противоречит принципу «никаких лишних библиотек».
    """
    import html as html_mod
    import re

    out: list[str] = []
    in_list = False
    for line in md.split("\n"):
        esc = html_mod.escape(line)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
        esc = re.sub(r"«(.+?)»", r"<em>«\1»</em>", esc)

        if esc.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{esc[2:]}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False

        if m := re.match(r"^(#{1,4}) (.+)$", esc):
            out.append(f"<h{len(m.group(1))}>{m.group(2)}</h{len(m.group(1))}>")
        elif esc.strip():
            out.append(f"<p>{esc}</p>")
    if in_list:
        out.append("</ul>")

    return (
        "<!doctype html><html lang='ru'><meta charset='utf-8'>"
        f"<title>{html_mod.escape(title)}</title>"
        "<style>body{font:15px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;"
        "max-width:52em;margin:2em auto;padding:0 1em;color:#111}"
        "h1,h2,h3{line-height:1.25}h1{border-bottom:2px solid #eee;padding-bottom:.3em}"
        "li{margin:.25em 0}em{color:#444}"
        "@media print{body{margin:0;max-width:none}}</style>"
        + "\n".join(out)
        + "</html>"
    )


# ── журнал действий ───────────────────────────────────────────────────────────


@router.get("/jobs")
async def jobs(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (
        await session.execute(select(Job).order_by(Job.created_at.desc()).limit(40))
    ).scalars().all()
    return [
        {"id": j.id, "submission_id": j.submission_id, "status": j.status,
         "stage": j.stage, "progress": j.progress, "error": j.error}
        for j in rows
    ]


@router.post("/demo/reset")
async def demo_reset(
    user: User = Depends(require_role(Role.COORDINATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Сброс демо-данных: очищает работы, ревью, уведомления и журнал.

    Нужен на защите: сценарий должен проходиться несколько раз подряд,
    а сроки и уведомления после первого прогона уже сработали.
    """
    for model in (Job, Notification, ActionLog, Submission):
        await session.execute(delete(model))
    await session.commit()
    audit.reset()
    await bus.publish("demo_reset", {})
    return {"ok": True}
