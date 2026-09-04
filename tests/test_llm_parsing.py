"""Тесты разбора ответа модели — без обращения к провайдеру.

Все случаи здесь наблюдались на реальных прогонах. Каждый стоил девяти
вызовов модели (три формата × три ремонтных попытки) до того, как был
разобран кодом, — то есть десятков секунд на каждую работу.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from app.llm.client import _extract_json, _openai_schema, _unwrap_schema_echo


class _Verdict(BaseModel):
    likelihood: float = Field(ge=0.0, le=1.0)
    observations: list[str]
    note: str = ""


# ── извлечение JSON из ответа ─────────────────────────────────────────────────


def test_markdown_fence_is_stripped() -> None:
    raw = '```json\n{"likelihood": 0.5, "observations": ["a"]}\n```'
    assert json.loads(_extract_json(raw))["likelihood"] == 0.5


def test_preamble_is_stripped() -> None:
    raw = 'Вот результат анализа:\n{"likelihood": 0.1, "observations": []}\nГотово.'
    assert json.loads(_extract_json(raw))["likelihood"] == 0.1


def test_thinking_block_is_removed() -> None:
    """Рассуждающая модель может напечатать <think>…</think> перед ответом."""
    raw = '<think>Сначала прикину…</think>{"likelihood": 0.3, "observations": []}'
    assert json.loads(_extract_json(raw))["likelihood"] == 0.3


# ── эхо схемы ─────────────────────────────────────────────────────────────────


def test_schema_echo_is_unwrapped() -> None:
    """Модель вернула форму схемы вместо данных — разворачиваем, а не ретраим.

    Наблюдалось на самой короткой из трёх работ: значения лежали внутри
    ключа `properties`, рядом стояли `description`, `title`, `required`.
    Формально это JSON, но не данные. Ремонтный цикл стоит десятки секунд,
    а вложенные значения уже корректны.
    """
    echo = json.dumps(
        {
            "description": "Текст структурирован…",
            "title": "_Verdict",
            "type": "object",
            "required": ["likelihood", "observations"],
            "properties": {"likelihood": 0.65, "observations": ["клише", "ровные абзацы"]},
        },
        ensure_ascii=False,
    )
    parsed = _Verdict.model_validate_json(_unwrap_schema_echo(echo, _Verdict))
    assert parsed.likelihood == 0.65
    assert len(parsed.observations) == 2


def test_valid_response_is_untouched() -> None:
    ok = json.dumps({"likelihood": 0.2, "observations": ["x"]}, ensure_ascii=False)
    assert _unwrap_schema_echo(ok, _Verdict) == ok


def test_legitimate_properties_field_is_not_unwrapped() -> None:
    """Схема с настоящим полем `properties` не должна разворачиваться.

    Разворот выполняется только когда обязательных полей нет во внешнем
    объекте, но есть во вложенном.
    """

    class WithProperties(BaseModel):
        likelihood: float
        properties: dict

    legit = json.dumps({"likelihood": 0.1, "properties": {"likelihood": 9}})
    assert _unwrap_schema_echo(legit, WithProperties) == legit


def test_garbage_is_passed_through_for_normal_validation() -> None:
    """Неразбираемый ответ не должен ронять развёртку — он уйдёт на валидацию."""
    for junk in ("не json вовсе", "[1, 2, 3]", "", "{неполный"):
        assert _unwrap_schema_echo(junk, _Verdict) == junk


# ── схема для промпта ─────────────────────────────────────────────────────────


def test_schema_has_no_service_fields_from_model_side() -> None:
    """В схему для модели не попадают поля, которые заполняет код.

    `EvidenceIn` содержит только номер блока и цитату: статус проверки
    вычисляет код, и показывать его модели вредно — она начинала сама
    проставлять `"status": "verified"`.
    """
    from app.agent.schemas import CriterionVerdict

    schema = _openai_schema(CriterionVerdict)
    evidence = schema["$defs"]["EvidenceIn"]["properties"]
    assert set(evidence) == {"block", "quote"}
    assert "status" not in evidence
    assert "similarity" not in evidence


def test_strict_schema_requirements_are_satisfied() -> None:
    """strict-режим требует additionalProperties:false и полный required."""
    schema = _openai_schema(_Verdict)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    # Значения по умолчанию strict-режим не принимает.
    assert "default" not in schema["properties"]["note"]
