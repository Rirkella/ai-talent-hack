"""LLM-клиент: единственное место в коде, знающее про модель.

Провайдеро-независим по построению — работает через OpenAI SDK против любого
OpenAI-совместимого API. Смена провайдера выполняется правкой `.env`.
Ollama-специфика вынесена целиком в `scripts/ollama_setup.ps1`.

Три свойства, ради которых модуль существует:

1. **Валидация вывода.** Ответ модели всегда разбирается в Pydantic-схему.
   При невалидном JSON выполняется ремонтный ретрай: модели показывают
   её собственный ответ и текст ошибки валидации.
2. **Деградация форматов.** Сначала `json_schema` (строгий structured output),
   при отказе провайдера — `json_object`, затем свободный текст с извлечением
   JSON из ответа. Разные серверы поддерживают разный набор.
3. **Отсутствие каскадных отказов.** Любая ошибка возвращается вызывающему
   как `LLMFailure` с причиной, а не роняет процесс: шаг пайплайна обязан
   уметь записать отказ в трассу и продолжить.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from openai import APIError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.privacy.audit import audit

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Модель может обернуть JSON в ```json ... ``` вопреки инструкции.
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
# qwen3.5 — рассуждающая модель: при некоторых настройках печатает <think>…</think>
# перед ответом. Вырезаем до разбора, иначе JSON не найдётся.
_THINK = re.compile(r"<think>.*?</think>", re.S)


class LLMFailure(RuntimeError):
    """Вызов не удался. Несёт причину для записи в трассу ревью."""

    def __init__(self, reason: str, *, stage: str = "", raw: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.stage = stage
        self.raw = raw


@dataclass
class LLMResult:
    """Разобранный ответ вместе с телеметрией вызова."""

    data: Any
    raw: str
    model: str
    duration_ms: float
    attempts: int
    mode: str  # json_schema | json_object | text
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def _extract_json(text: str) -> str:
    """Достаёт JSON-объект из ответа модели."""
    text = _THINK.sub("", text).strip()
    if m := _FENCE.search(text):
        text = m.group(1).strip()
    # Отрезаем возможную преамбулу вида «Вот результат:» до первой скобки.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def _unwrap_schema_echo(raw: str, schema: type[BaseModel]) -> str:
    """Разворачивает ответ, повторяющий форму схемы.

    Модель иногда возвращает не экземпляр схемы, а её «конверт»: значения
    лежат внутри ключа `properties`, рядом появляются `description`, `title`,
    `required`. Формально это JSON, но не данные. Наблюдалось на самой
    короткой из работ.

    Развернуть такой ответ дешевле, чем ретраить: один ремонтный цикл стоит
    десятки секунд, а вложенные значения уже корректны. Разворачиваем только
    при явном совпадении — когда внутри `properties` есть обязательные поля
    схемы; иначе ответ остаётся как есть и уходит на обычную валидацию.
    """
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    if not isinstance(obj, dict):
        return raw

    inner = obj.get("properties")
    if not isinstance(inner, dict):
        return raw

    required = {
        name for name, f in schema.model_fields.items() if f.is_required()
    } or set(schema.model_fields)
    # Во внешнем объекте обязательных полей нет, а во внутреннем есть —
    # значит это эхо схемы, а не легитимное поле с именем "properties".
    if required & obj.keys():
        return raw
    if not required <= inner.keys():
        return raw

    log.warning("модель вернула форму схемы вместо данных — разворачиваю properties")
    return json.dumps(inner, ensure_ascii=False)


class LLMClient:
    """Тонкая обёртка над OpenAI SDK с валидацией схемой и ремонтом."""

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url or settings.llm_base_url
        self.model = model or settings.llm_model
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=api_key or settings.llm_api_key,
            timeout=settings.llm_timeout_s,
            # Ретраи SDK выключены: своя логика ремонта информативнее
            # и должна попадать в трассу, а не молча повторяться внутри SDK.
            max_retries=0,
        )

    # ── низкий уровень ────────────────────────────────────────────────────

    def _chat(self, messages: list[dict[str, str]], response_format: dict | None) -> Any:
        audit.guard(self.base_url, purpose="llm.chat")
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": settings.llm_temperature,
            "seed": settings.llm_seed,
            # Предохранитель от разгона генерации: рассуждающая модель без
            # потолка способна писать бесконечно и занять очередь целиком.
            "max_tokens": settings.llm_max_tokens,
        }
        if effort := settings.llm_reasoning_effort.strip():
            # Стандартный параметр OpenAI, поэтому провайдеро-независим.
            # Для qwen3.5 через Ollama значение "none" критично: при включённых
            # рассуждениях весь вывод уходит в поле `thinking`, `content`
            # остаётся пустым, и запрос не завершается вовсе.
            kwargs["reasoning_effort"] = effort
        if response_format:
            kwargs["response_format"] = response_format
        return self._client.chat.completions.create(**kwargs)

    # ── публичный интерфейс ───────────────────────────────────────────────

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        purpose: str = "review",
        max_repair: int | None = None,
    ) -> LLMResult:
        """Возвращает ответ модели, разобранный в `schema`.

        Порядок деградации форматов: `json_schema` → `json_object` → текст.
        При провале валидации выполняется ремонтный ретрай с текстом ошибки.
        """
        max_repair = settings.llm_max_retries if max_repair is None else max_repair
        started = time.perf_counter()

        # Схема уходит в промпт, а не только в `response_format`.
        # Проверено на Ollama 0.24: её OpenAI-совместимый `/v1` принимает
        # `response_format` и молча игнорирует — при строгой схеме модель всё
        # равно возвращает произвольные имена полей. Полагаться на серверное
        # принуждение нельзя, поэтому схема описывается текстом, а фактическим
        # контролем служит валидация Pydantic с ремонтным ретраем ниже.
        # Провайдерам, которые схему соблюдают (OpenAI, vLLM), лишнее описание
        # в промпте не мешает.
        # Порядок частей важен, и он выстрадан двумя наблюдениями.
        #
        # Схема НЕ должна быть последней: если промпт заканчивается схемой,
        # модель продолжает её и возвращает саму схему вместо экземпляра —
        # {"properties": {...}, "required": [...], "title": "…"}. Формально
        # JSON, но не данные.
        #
        # Последней должна быть ЗАДАЧА вызывающего. Когда после схемы шла
        # общая инструкция про формат, задача («оцени по критерию X от 0 до N»)
        # оказывалась в середине, и дисциплина оценки слабела: слабая работа
        # получала 6,5 вместо 5,2.
        #
        # Поэтому блок формата идёт первым, а текст задачи — последним.
        user_with_schema = (
            f"ФОРМАТ ОТВЕТА — ОДИН JSON-объект со значениями по этой схеме:\n"
            f"{json.dumps(_openai_schema(schema), ensure_ascii=False, indent=2)}\n"
            f"Не повторяй саму схему: поля \"properties\", \"required\", \"type\" "
            f"и \"title\" в ответе не нужны. Никакого текста до или после, "
            f"без markdown-ограждений.\n\n"
            f"{user}"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_with_schema},
        ]

        json_schema_fmt = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": _openai_schema(schema),
                "strict": True,
            },
        }
        formats: list[tuple[str, dict | None]] = [
            ("json_schema", json_schema_fmt),
            ("json_object", {"type": "json_object"}),
            ("text", None),
        ]

        attempts = 0
        last_error = ""
        last_raw = ""

        for mode, fmt in formats:
            convo = list(messages)
            for repair in range(max_repair + 1):
                attempts += 1
                try:
                    resp = self._chat(convo, fmt)
                except APITimeoutError as exc:
                    last_error = f"таймаут {settings.llm_timeout_s}s: {exc}"
                    break  # ретраить тем же форматом смысла нет
                except (APIError, httpx.HTTPError) as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    # Провайдер может не знать формат — пробуем следующий.
                    break

                raw = (resp.choices[0].message.content or "").strip()
                last_raw = raw
                try:
                    payload = _unwrap_schema_echo(_extract_json(raw), schema)
                    parsed = schema.model_validate_json(payload)
                except (ValidationError, json.JSONDecodeError) as exc:
                    last_error = str(exc)
                    if repair >= max_repair:
                        break
                    log.warning("LLM вернула невалидный ответ (%s), ремонт %d", mode, repair + 1)
                    convo = convo + [
                        {"role": "assistant", "content": raw},
                        {
                            "role": "user",
                            "content": (
                                "Ответ не прошёл валидацию по схеме.\n"
                                f"Ошибка: {exc}\n\n"
                                "Верни ИСПРАВЛЕННЫЙ ответ. Только JSON-объект, "
                                "без пояснений, без markdown-ограждений."
                            ),
                        },
                    ]
                    continue

                duration_ms = (time.perf_counter() - started) * 1000
                usage = getattr(resp, "usage", None)
                audit.record(self.base_url, purpose, duration_ms=duration_ms, ok=True)
                return LLMResult(
                    data=parsed,
                    raw=raw,
                    model=self.model,
                    duration_ms=duration_ms,
                    attempts=attempts,
                    mode=mode,
                    prompt_tokens=getattr(usage, "prompt_tokens", None),
                    completion_tokens=getattr(usage, "completion_tokens", None),
                )

        duration_ms = (time.perf_counter() - started) * 1000
        audit.record(self.base_url, purpose, duration_ms=duration_ms, ok=False, detail=last_error)
        raise LLMFailure(
            f"Модель не вернула валидный ответ за {attempts} попыт(ок): {last_error}",
            stage=purpose,
            raw=last_raw,
        )

    def complete_text(self, *, system: str, user: str, purpose: str = "text") -> str:
        """Свободный текст без схемы — для персонализированного фидбека."""
        started = time.perf_counter()
        try:
            resp = self._chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                None,
            )
        except (APIError, httpx.HTTPError) as exc:
            audit.record(self.base_url, purpose, ok=False, detail=str(exc))
            raise LLMFailure(f"{type(exc).__name__}: {exc}", stage=purpose) from exc
        audit.record(
            self.base_url, purpose, duration_ms=(time.perf_counter() - started) * 1000, ok=True
        )
        return _THINK.sub("", resp.choices[0].message.content or "").strip()

    # ── диагностика ───────────────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        """Состояние провайдера для баннера в интерфейсе."""
        try:
            audit.guard(self.base_url, purpose="llm.health")
            models = [m.id for m in self._client.models.list().data]
            return {
                "ok": True,
                "base_url": self.base_url,
                "model": self.model,
                "model_available": _model_in(self.model, models),
                "models": models,
                "is_local": settings.is_local_llm,
            }
        except Exception as exc:  # noqa: BLE001 — health не имеет права падать
            return {
                "ok": False,
                "base_url": self.base_url,
                "model": self.model,
                "error": f"{type(exc).__name__}: {exc}",
                "is_local": settings.is_local_llm,
            }


def _model_in(name: str, available: list[str]) -> bool:
    """Сравнение имён моделей с учётом неявного тега.

    Ollama перечисляет модели как `avito-reviewer:latest`, а в `.env` пишут
    `avito-reviewer`. Без нормализации баннер показывал бы «модель недоступна»
    при полностью рабочей установке.
    """
    norm = lambda s: s if ":" in s else f"{s}:latest"  # noqa: E731
    return norm(name) in {norm(a) for a in available}


def _openai_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON-схема в виде, который принимает strict-режим structured outputs.

    Требования strict-режима: у каждого объекта `additionalProperties: false`
    и все свойства перечислены в `required`. Pydantic этого не гарантирует,
    поэтому схему дочищаем. `$defs`/`$ref` оставляем — они поддерживаются.
    """
    schema = model.model_json_schema()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            # Значения по умолчанию strict-режим не принимает.
            node.pop("default", None)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return schema


_default_client: LLMClient | None = None


def get_client(fast: bool = False) -> LLMClient:
    """Общий клиент. `fast=True` — younger-модель для черновых прогонов."""
    global _default_client
    if fast:
        return LLMClient(model=settings.llm_model_fast)
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client
