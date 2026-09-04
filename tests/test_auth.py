"""Вход по логину и паролю.

Экран входа был выбором из списка всех участников. Это читалось не как
вход, а как справочник: пользователь искал форму и не находил её. Здесь
проверяется то, что пришло на замену, — и прежде всего то, что легко
сделать неправильно.

Главное свойство, которое тут закреплено: ответ на неверный **логин** и на
неверный **пароль** совпадает дословно. Разные тексты позволили бы перебором
составить список существующих учётных записей.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import hash_password, make_login, unique_login, verify_password
from app.models import Base, Role, User


# ── хеширование ───────────────────────────────────────────────────────────────


def test_password_roundtrip() -> None:
    h = hash_password("правильный-пароль")
    assert verify_password("правильный-пароль", h)
    assert not verify_password("другой-пароль", h)


def test_hash_is_not_the_password() -> None:
    """Очевидное, но именно это и ломается: пароль не хранится текстом."""
    h = hash_password("secret123")
    assert "secret123" not in h
    assert h.startswith("pbkdf2_sha256$")


def test_same_password_gives_different_hashes() -> None:
    """Соль индивидуальная: одинаковые пароли не видны по совпадению хешей."""
    assert hash_password("одинаковый") != hash_password("одинаковый")


@pytest.mark.parametrize(
    "broken",
    ["", "не-хеш", "pbkdf2_sha256$мало$частей", "другой$1$00$00", "$$$"],
)
def test_broken_hash_is_a_failed_login_not_a_crash(broken: str) -> None:
    """Испорченная строка хеша — «пароль не подошёл», а не пятисотка."""
    assert verify_password("любой", broken) is False


def test_empty_password_never_matches() -> None:
    assert verify_password("", hash_password("непустой")) is False
    with pytest.raises(ValueError):
        hash_password("")


# ── логины ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Ирина Соколова", "sokolova.i"),
        ("Павел Кузнецов", "kuznecov.p"),
        ("Анна Лебедева", "lebedeva.a"),
        ("Пётр Ким", "kim.p"),
        ("Лиана Юсупова", "yusupova.l"),
        ("Мадонна", "madonna"),
    ],
)
def test_login_from_name(name: str, expected: str) -> None:
    assert make_login(name) == expected


def test_namesakes_get_distinct_logins() -> None:
    """Два «Ивана Петрова» не должны делить одну учётную запись."""
    taken: set[str] = set()
    logins = []
    for _ in range(3):
        login = unique_login("Иван Петров", taken)
        taken.add(login)
        logins.append(login)
    assert logins == ["petrov.i", "petrov.i2", "petrov.i3"]
    assert len(set(logins)) == 3


# ── эндпоинт входа ────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path):
    from app.db import get_session
    from app.main import app

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def prepare():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as s:
            s.add(User(id="coord-1", name="Ирина Соколова", role=Role.COORDINATOR,
                       login="sokolova.i", password_hash=hash_password("верный")))
            # Уволенный участник: логин остался, вход закрыт.
            s.add(User(id="rev-9", name="Бывший Ревьюер", role=Role.REVIEWER,
                       login="revyuer.b", password_hash=hash_password("верный"),
                       active=False))
            # Заведён до появления входа по паролю: логина нет.
            s.add(User(id="stu-9", name="Старый Студент", role=Role.STUDENT))
            await s.commit()

    asyncio.run(prepare())

    async def override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, login: str, password: str):
    return client.post("/api/login", json={"login": login, "password": password})


def test_correct_credentials_return_the_user(client) -> None:
    r = _login(client, "sokolova.i", "верный")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "coord-1"
    assert body["role"] == "coordinator"
    assert "password_hash" not in body and "password" not in body


def test_wrong_password_and_unknown_login_answer_identically(client) -> None:
    """Иначе перебором составляется список существующих логинов."""
    wrong_password = _login(client, "sokolova.i", "неверный")
    unknown_login = _login(client, "takogo-net", "верный")

    assert wrong_password.status_code == unknown_login.status_code == 401
    assert wrong_password.json() == unknown_login.json()


def test_login_is_case_insensitive(client) -> None:
    """Раскладка и регистр не должны мешать входу на защите."""
    assert _login(client, "Sokolova.I", "верный").status_code == 200
    assert _login(client, "  sokolova.i  ", "верный").status_code == 200


def test_deactivated_user_cannot_log_in(client) -> None:
    assert _login(client, "revyuer.b", "верный").status_code == 401


def test_user_without_login_cannot_log_in_with_empty_login(client) -> None:
    """Пустой логин не должен совпадать с пользователем без логина.

    Пустая строка в базе и пустое поле формы — это одно и то же значение,
    и без явной проверки вход пустым логином открывал бы чужой профиль.
    """
    assert _login(client, "", "").status_code == 401
    assert _login(client, "", "верный").status_code == 401


def test_demo_credentials_never_expose_hashes(client) -> None:
    r = client.get("/api/demo-credentials")
    assert r.status_code == 200
    body = r.json()
    assert all("password_hash" not in a for a in body["accounts"])
    logins = {a["login"] for a in body["accounts"]}
    assert "sokolova.i" in logins
    assert "revyuer.b" not in logins, "выключенная учётная запись в подсказке"


# ── регистрация ───────────────────────────────────────────────────────────────


def test_registration_creates_a_working_account(client) -> None:
    r = client.post("/api/register", json={
        "name": "Иван Петров", "role": "student",
        "login": "petrov.i", "password": "parol123",
    })
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "student"
    # Учётной записью сразу можно войти — иначе регистрация бессмысленна.
    assert _login(client, "petrov.i", "parol123").status_code == 200


def test_registration_refuses_a_taken_login(client) -> None:
    """Занятость логина сообщается прямо: скрывать её при регистрации незачем."""
    r = client.post("/api/register", json={
        "name": "Двойник", "role": "student",
        "login": "sokolova.i", "password": "parol123",
    })
    assert r.status_code == 409
    assert "занят" in r.json()["detail"].lower()


@pytest.mark.parametrize(
    ("field", "value", "why"),
    [
        ("login", "ab", "логин короче трёх символов"),
        ("login", "логин.кириллицей", "кириллица в логине"),
        ("password", "12345", "пароль короче шести символов"),
        ("name", "   ", "пустое имя"),
    ],
)
def test_registration_validates_input(client, field: str, value: str, why: str) -> None:
    body = {"name": "Иван Петров", "role": "student",
            "login": "petrov.i", "password": "parol123"}
    body[field] = value
    r = client.post("/api/register", json=body)
    assert r.status_code == 400, why


def test_registration_respects_the_allowed_roles(client, monkeypatch) -> None:
    """Роли для самостоятельной регистрации задаются настройкой, а не кодом.

    В реальном внедрении открыт только студент: роль ревьюера и методиста
    выдаёт человек, отвечающий за программу.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "open_registration_roles", "student")

    ok = client.post("/api/register", json={
        "name": "Студент Новый", "role": "student",
        "login": "novyy.s", "password": "parol123",
    })
    closed = client.post("/api/register", json={
        "name": "Самозванец", "role": "coordinator",
        "login": "samozvanec.s", "password": "parol123",
    })
    assert ok.status_code == 200
    assert closed.status_code == 403
    assert client.get("/api/registration-roles").json()["roles"] == ["student"]
