"""Аудит исходящих вызовов.

Кейс требует описать меры защиты данных. Одних слов мало, поэтому каждый
исходящий вызов регистрируется здесь, а интерфейс показывает счётчик
«внешних вызовов: N». Число берётся из фактически перехваченных вызовов,
а не из декларации в презентации.

Классификация проста: хост из `ALLOWED_HOSTS` и при этом петлевой — вызов
внутренний; всё остальное — внешний.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from app.config import settings

LOOPBACK = {"localhost", "127.0.0.1", "::1", ""}

CallKind = Literal["internal", "external", "blocked"]


class OutboundBlocked(RuntimeError):
    """Вызов наружу при включённом `OFFLINE_ENFORCE`."""


@dataclass(frozen=True)
class CallRecord:
    at: datetime
    host: str
    url: str
    kind: CallKind
    purpose: str
    duration_ms: float | None = None
    ok: bool = True
    detail: str = ""


@dataclass
class _AuditState:
    records: deque[CallRecord] = field(default_factory=lambda: deque(maxlen=2000))
    internal: int = 0
    external: int = 0
    blocked: int = 0


class CallAudit:
    """Потокобезопасный журнал вызовов.

    Живёт в памяти процесса: демо — один `uvicorn`, история за пределы сессии
    не нужна, а лишняя таблица в БД только усложнила бы восстановление
    состояния при перезапуске.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = _AuditState()

    @staticmethod
    def classify(url: str) -> CallKind:
        host = (urlparse(url).hostname or "").lower()
        if host not in {h.lower() for h in settings.allowed_hosts}:
            return "blocked" if settings.offline_enforce else "external"
        return "internal" if host in LOOPBACK else "external"

    def guard(self, url: str, purpose: str) -> None:
        """Проверяет вызов до его выполнения. Бросает `OutboundBlocked`.

        Вызывается перед каждым обращением к LLM. Это не декоративная
        проверка: профиль `openai.env` физически не заработает, пока
        `api.openai.com` не добавлен в `ALLOWED_HOSTS` осознанно.
        """
        kind = self.classify(url)
        if kind == "blocked":
            self.record(url, purpose, kind="blocked", ok=False, detail="хост вне ALLOWED_HOSTS")
            raise OutboundBlocked(
                f"Вызов к {url!r} заблокирован офлайн-контуром. "
                f"Разрешены только: {', '.join(settings.allowed_hosts)}."
            )

    def record(
        self,
        url: str,
        purpose: str,
        *,
        kind: CallKind | None = None,
        duration_ms: float | None = None,
        ok: bool = True,
        detail: str = "",
    ) -> CallRecord:
        kind = kind or self.classify(url)
        rec = CallRecord(
            at=datetime.now(timezone.utc),
            host=(urlparse(url).hostname or "?"),
            url=url,
            kind=kind,
            purpose=purpose,
            duration_ms=duration_ms,
            ok=ok,
            detail=detail,
        )
        with self._lock:
            self._state.records.append(rec)
            if kind == "internal":
                self._state.internal += 1
            elif kind == "external":
                self._state.external += 1
            else:
                self._state.blocked += 1
        return rec

    def summary(self) -> dict[str, object]:
        with self._lock:
            s = self._state
            return {
                "internal_calls": s.internal,
                "external_calls": s.external,
                "blocked_calls": s.blocked,
                "offline_enforce": settings.offline_enforce,
                "allowed_hosts": list(settings.allowed_hosts),
                "llm_is_local": settings.is_local_llm,
            }

    def recent(self, limit: int = 100) -> list[CallRecord]:
        with self._lock:
            return list(self._state.records)[-limit:][::-1]

    def reset(self) -> None:
        with self._lock:
            self._state = _AuditState()


audit = CallAudit()
