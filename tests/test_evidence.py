"""Тесты проверки доказательств — главного анти-галлюцинационного механизма.

Ключевой тест здесь — `test_invented_quote_is_rejected`. Если он перестанет
проходить, система начнёт превращать выдуманные цитаты в баллы, и весь
остальной продукт потеряет смысл.
"""

from __future__ import annotations

from app.agent.evidence import SIMILARITY_THRESHOLD, normalize, verify_all, verify_evidence
from app.agent.schemas import Evidence, EvidenceIn, EvidenceStatus
from app.ingest.document import Block, BlockKind, Document


def _doc() -> Document:
    return Document(
        source_name="работа.docx",
        source_format="docx",
        blocks=[
            Block(1, BlockKind.HEADING, "Описание продукта"),
            Block(
                2,
                BlockKind.PARAGRAPH,
                "Наш продукт — маркетплейс запчастей для автомобилей. "
                "Он соединяет частных продавцов и покупателей в одном каталоге.",
            ),
            Block(
                3,
                BlockKind.PARAGRAPH,
                "Риск: мошенничество с банковскими картами при оплате заказа. "
                "Вероятность средняя, влияние высокое на выручку и репутацию.",
            ),
            Block(4, BlockKind.PARAGRAPH, "ROI митигации рассчитан за квартал."),
        ],
    )


def test_exact_quote_is_verified() -> None:
    ev = verify_evidence(Evidence(block=2, quote="маркетплейс запчастей для автомобилей"), _doc())
    assert ev.status is EvidenceStatus.VERIFIED
    assert ev.found_in_block == 2
    assert ev.similarity >= SIMILARITY_THRESHOLD


def test_invented_quote_is_rejected() -> None:
    """Главная защита: выдуманной цитаты в работе нет — балл не подтверждён.

    Формулировка правдоподобна для этого задания, но такого текста в работе
    не существует. Проверка обязана это увидеть.
    """
    ev = verify_evidence(
        Evidence(
            block=3,
            quote="Внедрён биометрический контроль и трёхфакторная аутентификация продавцов",
        ),
        _doc(),
    )
    assert ev.status is EvidenceStatus.NOT_FOUND
    assert not ev.is_verified
    assert ev.similarity < SIMILARITY_THRESHOLD


def test_cosmetic_differences_survive() -> None:
    """Нормализация пробелов, регистра и кавычек не должна ломать цитату.

    Модель переносит текст неточно: меняет регистр, схлопывает пробелы,
    подставляет другие кавычки. Требовать посимвольного равенства значило бы
    отбраковывать честные цитаты.
    """
    ev = verify_evidence(
        Evidence(block=3, quote="«МОШЕННИЧЕСТВО   с  банковскими картами»  при оплате заказа"),
        _doc(),
    )
    assert ev.status is EvidenceStatus.VERIFIED


def test_wrong_block_is_distinguished_from_invention() -> None:
    """Верная цитата с ошибочным номером блока — отдельный, более лёгкий случай."""
    ev = verify_evidence(
        Evidence(block=4, quote="маркетплейс запчастей для автомобилей"), _doc()
    )
    assert ev.status is EvidenceStatus.WRONG_BLOCK
    assert ev.found_in_block == 2
    assert not ev.is_verified


def test_nonexistent_block_is_reported() -> None:
    ev = verify_evidence(Evidence(block=99, quote="какой-то текст подлиннее"), _doc())
    assert ev.status in {EvidenceStatus.NO_BLOCK, EvidenceStatus.NOT_FOUND}
    assert not ev.is_verified


def test_too_short_quote_does_not_count() -> None:
    """Короткая цитата совпадает со случайным текстом и ничего не доказывает."""
    ev = verify_evidence(Evidence(block=2, quote="наш"), _doc())
    assert not ev.is_verified


def test_verify_all_counts_verified() -> None:
    checked, verified = verify_all(
        [
            EvidenceIn(block=2, quote="маркетплейс запчастей для автомобилей"),
            EvidenceIn(block=3, quote="выдуманный фрагмент, которого в работе нет вовсе"),
            EvidenceIn(block=4, quote="ROI митигации рассчитан за квартал"),
        ],
        _doc(),
    )
    assert len(checked) == 3
    assert verified == 2


def test_model_cannot_declare_its_own_verification() -> None:
    """Статус проверки не может прийти из ответа модели.

    Схема `EvidenceIn` содержит только номер блока и цитату. Наблюдалось,
    что модель самостоятельно проставляла `"status": "verified"` и
    `"similarity": 1.0` — то есть утверждала проверку, которую выполнить
    не может. Разделение типов делает это невозможным.
    """
    assert set(EvidenceIn.model_fields) == {"block", "quote"}

    # Даже если модель пришлёт лишние поля, они будут отброшены.
    ev = EvidenceIn.model_validate(
        {"block": 3, "quote": "мошенничество с банковскими картами", "status": "verified",
         "similarity": 1.0}
    )
    assert not hasattr(ev, "status")

    checked, verified = verify_all([ev], _doc())
    # Статус посчитан кодом и оказался верным по существу.
    assert checked[0].status is EvidenceStatus.VERIFIED
    assert verified == 1


def test_normalize_is_idempotent() -> None:
    text = "  Риск:  «мошенничество»  —  высокий\n\n"
    assert normalize(normalize(text)) == normalize(text)
    assert "ё" not in normalize("ёлка")
