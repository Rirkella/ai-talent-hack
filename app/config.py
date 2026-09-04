"""Конфигурация приложения.

Единственный источник настроек. Всё читается из `.env` через pydantic-settings,
поэтому смена LLM-провайдера, порогов и тумблеров не требует правок кода.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Настройки, читаемые из `.env`."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Поля называются llm_model / llm_model_fast, а pydantic резервирует
        # префикс model_ под собственные настройки. Снимаем защиту префикса.
        protected_namespaces=(),
    )

    # ── LLM ──────────────────────────────────────────────────────────────
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "avito-reviewer"
    llm_model_fast: str = "avito-reviewer-fast"
    llm_temperature: float = 0.0
    llm_seed: int = 42
    llm_timeout_s: float = 180.0
    llm_max_retries: int = 2
    llm_num_ctx: int = 32768
    # Потолок длины ответа. Предохранитель от разгона генерации: без него
    # рассуждающая модель может не остановиться и заблокировать очередь.
    llm_max_tokens: int = 3072
    # Управление рассуждениями (стандартный параметр OpenAI).
    # Для qwen3.5 через Ollama обязательно "none": иначе весь вывод уходит
    # в поле thinking, content остаётся пустым, а генерация не завершается.
    # Пустая строка — параметр не отправляется (нужно облачным провайдерам,
    # которые не знают значения "none").
    llm_reasoning_effort: str = "none"

    # ── Офлайн-контур ────────────────────────────────────────────────────
    # NoDecode: без него pydantic-settings пытается разобрать значение как JSON
    # ещё до валидатора и падает на строке «localhost,127.0.0.1».
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1"]
    )
    offline_enforce: bool = True

    # ── ПДн ──────────────────────────────────────────────────────────────
    pii_enabled: bool = True
    pii_use_ner: bool = True

    # ── Вход ─────────────────────────────────────────────────────────────
    # Общий пароль демонстрационных учётных записей. Вынесен в .env, чтобы
    # не быть зашитым в код, и печатается на экране входа: без этого показ
    # встанет на первом же экране. Для реального внедрения — задать свой и
    # убрать подсказку с экрана (DEMO_SHOW_CREDENTIALS=false).
    demo_password: str = "avito2026"
    demo_show_credentials: bool = True
    # Роли, доступные для самостоятельной регистрации. На демо открыты все
    # три, чтобы жюри могло попробовать любую. В реальном внедрении здесь
    # остаётся только `student`: роль ревьюера и методиста выдаёт человек,
    # отвечающий за программу, а не тот, кто заполнил форму.
    open_registration_roles: str = "coordinator,reviewer,student"

    # ── Приложение ───────────────────────────────────────────────────────
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    db_url: str = "sqlite+aiosqlite:///./data/app.db"
    data_dir: Path = PROJECT_ROOT / "data"
    upload_dir: Path = PROJECT_ROOT / "data" / "uploads"
    review_workers: int = 1
    log_level: str = "INFO"

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, v: object) -> object:
        """`ALLOWED_HOSTS` в `.env` — строка через запятую, а не JSON-список."""
        if isinstance(v, str):
            return [h.strip() for h in v.split(",") if h.strip()]
        return v

    @field_validator("data_dir", "upload_dir", mode="after")
    @classmethod
    def _absolutize(cls, v: Path) -> Path:
        """Относительные пути из `.env` считаем от корня проекта, а не от cwd.

        Иначе запуск `uvicorn` из другой директории создаёт вторую базу.
        """
        return v if v.is_absolute() else (PROJECT_ROOT / v).resolve()
    @property
    def is_local_llm(self) -> bool:
        """True, если LLM обслуживается внутри контура.

        Используется баннером «внешних вызовов: 0» и вкладкой «Приватность».
        """
        from urllib.parse import urlparse

        host = urlparse(self.llm_base_url).hostname or ""
        return host in {"localhost", "127.0.0.1", "::1"}

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
