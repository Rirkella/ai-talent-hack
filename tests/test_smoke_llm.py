"""Smoke-тест LLM-слоя.

Главная проверка — фактическое окно контекста. У Ollama `num_ctx` по умолчанию
равен 4096, а OpenAI-совместимый `/v1` не принимает поле `options`, поэтому
задать контекст из кода нельзя. Если производная модель не создана,
приложение продолжит работать, молча теряя хвост каждой работы.

Поэтому тест спрашивает значение у самого сервера через `/api/show` —
единственное место в тестах, где допустим ollama-специфичный вызов.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

import pytest
from pydantic import BaseModel, Field

from app.config import settings
from app.llm.client import get_client

pytestmark = pytest.mark.smoke


def _ollama_root() -> str:
    """`http://host:port` из `LLM_BASE_URL` — без суффикса `/v1`."""
    u = urlparse(settings.llm_base_url)
    return f"{u.scheme}://{u.netloc}"


def _api_show(model: str) -> dict:
    req = urllib.request.Request(
        f"{_ollama_root()}/api/show",
        data=json.dumps({"model": model}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _llm_available() -> bool:
    return bool(get_client().health().get("ok"))


requires_llm = pytest.mark.skipif(
    not _llm_available(), reason="LLM-провайдер недоступен: запустите `ollama serve`"
)


@requires_llm
def test_provider_reachable_and_model_present() -> None:
    health = get_client().health()
    assert health["ok"], health
    assert health["model_available"], (
        f"Модель {settings.llm_model!r} не найдена. "
        f"Выполните: powershell -File scripts/ollama_setup.ps1"
    )


@requires_llm
def test_num_ctx_is_actually_applied() -> None:
    """Ассерт на ФАКТИЧЕСКИЙ `num_ctx`, а не на значение из `.env`.

    Значение из конфига здесь ничего не доказывает: проверяется то,
    с чем сервер реально загрузит модель.
    """
    info = _api_show(settings.llm_model)
    params = info.get("parameters") or ""
    actual: int | None = None
    for line in params.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "num_ctx":
            actual = int(parts[1])
            break

    assert actual is not None, (
        f"В параметрах модели {settings.llm_model!r} нет num_ctx — значит действует "
        f"умолчание Ollama (4096) и хвост работы будет молча обрезан.\n"
        f"Параметры: {params!r}"
    )
    assert actual >= settings.llm_num_ctx, (
        f"num_ctx={actual}, ожидалось не меньше {settings.llm_num_ctx}. "
        f"Пересоздайте модель: ollama create {settings.llm_model} -f scripts/Modelfile.reviewer"
    )


@requires_llm
def test_sampling_is_deterministic_by_construction() -> None:
    """temperature=0 и seed зафиксированы, а унаследованные штрафы погашены.

    qwen3.5 приносит из базового Modelfile `presence_penalty 1.5`. Штрафы
    правят логиты до argmax даже при temperature=0, а структурный JSON состоит
    из повторяющихся токенов — это ломает и стабильность, и валидность.
    """
    params = _api_show(settings.llm_model).get("parameters") or ""
    values = {
        p[0]: p[1] for p in (line.split() for line in params.splitlines()) if len(p) == 2
    }
    assert float(values.get("temperature", 1)) == 0.0, values
    assert float(values.get("presence_penalty", 0)) == 0.0, values
    assert float(values.get("repeat_penalty", 1)) == 1.0, values
    assert "seed" in values, values


@requires_llm
def test_reasoning_is_disabled_and_content_is_not_empty() -> None:
    """Рассуждения обязаны быть выключены.

    qwen3.5 — рассуждающая модель. При включённых рассуждениях Ollama кладёт
    весь вывод в поле `thinking`, `content` остаётся пустым, а генерация не
    останавливается: тривиальный запрос уходил в таймаут на 180 секунд.
    Лечится `reasoning_effort="none"` (стандартный параметр OpenAI).

    Тест ловит именно молчаливую регрессию: без него поломка выглядит как
    «модель почему-то отвечает медленно», а не как пустой ответ.
    """
    started = time.perf_counter()
    text = get_client().complete_text(
        system="Отвечай кратко.", user="Ответь одним словом: столица Франции?", purpose="smoke"
    )
    elapsed = time.perf_counter() - started
    assert text.strip(), (
        "Модель вернула пустой content. Почти наверняка включены рассуждения "
        "и вывод ушёл в поле thinking. Проверьте LLM_REASONING_EFFORT=none в .env."
    )
    assert elapsed < 60, (
        f"Тривиальный запрос занял {elapsed:.0f}s. Признак разгона генерации "
        f"при включённых рассуждениях."
    )


def test_max_tokens_cap_is_configured() -> None:
    """Потолок длины ответа задан — очередь не может встать из-за одного вызова."""
    assert settings.llm_max_tokens > 0
    assert settings.llm_max_tokens <= 8192, "слишком большой потолок обесценивает предохранитель"


class _Verdict(BaseModel):
    """Минимальная схема для проверки structured output."""

    score: int = Field(ge=0, le=10)
    verdict: str
    reasons: list[str]


@requires_llm
def test_structured_output_validates_into_pydantic() -> None:
    result = get_client().complete_json(
        system=(
            "Ты помощник ревьюера. Отвечай строго JSON-объектом по схеме, "
            "без пояснений и без markdown."
        ),
        user=(
            "Работа студента состоит из одного предложения и не содержит ни таблицы "
            "рисков, ни расчёта ROI. Оцени полноту от 0 до 10, дай краткий вердикт "
            "и перечисли причины."
        ),
        schema=_Verdict,
        purpose="smoke",
    )
    v = result.data
    assert isinstance(v, _Verdict)
    assert 0 <= v.score <= 10
    assert v.reasons, "модель обязана вернуть непустой список причин"
    # Пустая работа не может получить высокий балл — проверка вменяемости связки.
    assert v.score <= 4, f"неожиданно высокий балл {v.score} за пустую работу"


@requires_llm
def test_repair_retry_recovers_from_bad_schema() -> None:
    """Ремонтный ретрай: схема нарочно узкая, с первого раза модель промахнётся."""

    class Strict(BaseModel):
        risk_type: str = Field(description="ровно одно слово в нижнем регистре")
        confidence: float = Field(ge=0.0, le=1.0)

    result = get_client().complete_json(
        system="Отвечай только JSON по схеме.",
        user="Определи тип риска «утечка персональных данных пользователей» и уверенность.",
        schema=Strict,
        purpose="smoke.repair",
    )
    assert 0.0 <= result.data.confidence <= 1.0
    assert result.attempts >= 1


def test_offline_guard_blocks_foreign_host() -> None:
    """Офлайн-контур обязан ронять вызов наружу, а не просто логировать.

    Тест не требует запущенной модели: проверяется страж, а не провайдер.
    """
    from app.privacy.audit import OutboundBlocked, audit

    if not settings.offline_enforce:
        pytest.skip("OFFLINE_ENFORCE выключен в .env")

    before = audit.summary()["blocked_calls"]
    with pytest.raises(OutboundBlocked):
        audit.guard("https://api.openai.com/v1/chat/completions", purpose="test")
    assert audit.summary()["blocked_calls"] == before + 1
