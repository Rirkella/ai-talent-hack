"""Тесты ПДн-щита и офлайн-контура.

Главный тест — `test_original_values_never_reach_the_model`: он проверяет
не наличие маскирования как функции, а отсутствие исходных значений в том,
что фактически уходит в модель. Это единственная формулировка, которая
что-то доказывает.
"""

from __future__ import annotations

import pytest

from app.ingest.document import Block, BlockKind, DocMeta, Document
from app.privacy.audit import OutboundBlocked, audit
from app.privacy.pii import mask_document, mask_text, scan, unmask

# Синтетические данные. Реальные ФИО есть в метаданных примеров работ,
# и переносить их в тесты нельзя — документация проекта обезличена.
FIXTURE = (
    "Работу выполнил Иванов Пётр Сергеевич, студент группы ПМ-21. "
    "Контакты: petr.ivanov@example.com, +7 (999) 123-45-67, телеграм @petr_ivanov. "
    "СНИЛС 123-456-789 01, паспорт 45 12 345678. "
    "Дата рождения 15.03.1998. "
    "Расчёт ROI приведён по ссылке https://docs.google.com/spreadsheets/d/abc123. "
    "Ожидаемые потери составляют 1500000 рублей за квартал."
)


def test_formatted_identifiers_are_masked() -> None:
    r = mask_text(FIXTURE, use_ner=False)
    for original in (
        "petr.ivanov@example.com",
        "+7 (999) 123-45-67",
        "@petr_ivanov",
        "123-456-789 01",
        "45 12 345678",
        "15.03.1998",
        "https://docs.google.com/spreadsheets/d/abc123",
    ):
        assert original not in r.text, f"не замаскировано: {original}"


def test_masking_is_reversible() -> None:
    """Карта замен локальна и применяется обратно в фидбеке студенту.

    Без обратной подстановки студент увидел бы «ЛИЦО_1» вместо своего имени.
    """
    r = mask_text(FIXTURE, use_ner=False)
    assert unmask(r.text, r.mapping) == FIXTURE


def test_same_value_gets_same_token() -> None:
    """Одинаковые значения получают один псевдоним, иначе рвутся связи в тексте."""
    text = "Пишите на a@b.com. Ещё раз: a@b.com."
    r = mask_text(text, use_ner=False)
    tokens = [t for t, v in r.mapping.items() if v == "a@b.com"]
    assert len(tokens) == 1
    assert r.text.count(tokens[0]) == 2


def test_business_numbers_are_not_mistaken_for_cards() -> None:
    """Суммы в расчёте ROI не должны попадать под маску номера карты.

    Номер карты проверяется алгоритмом Луна именно поэтому: без проверки
    под маску уходило бы любое длинное число, включая убытки и выручку.
    """
    r = mask_text("Потери 1500000 рублей, снижение 900000, стоимость 250000.", use_ner=False)
    assert "1500000" in r.text
    assert "КАРТА" not in r.text


def test_valid_card_number_is_masked() -> None:
    # Тестовый номер Visa, проходящий проверку Луна.
    r = mask_text("Оплата картой 4111 1111 1111 1111 прошла.", use_ner=False)
    assert "4111" not in r.text
    assert any(t.startswith("КАРТА") for t in r.mapping)


def test_financial_figures_are_not_mistaken_for_documents() -> None:
    """Расчётные числа из финансовой модели не должны попадать под маску.

    Найдено на реальной работе по бизнес-модели: без требования контекста
    в таблице обнаружилось 34 «ИНН» и 1 «паспорт», а 13-значные числа
    случайно проходили проверку Луна и маскировались как карты. Щит
    уничтожал бы ровно то содержание, по которому работу и оценивают.
    """
    text = (
        "Прогноз выручки 959458749992 рублей. Показатель 4852249654 в модели. "
        "Промежуточный расчёт 5676171967807. Потери 1500000, снижение 900000."
    )
    r = mask_text(text, use_ner=False)
    assert r.text == text, f"замаскировано лишнее: {r.mapping}"


def test_documents_are_masked_when_context_present() -> None:
    """С ключевым словом рядом ИНН и паспорт маскируются, слово остаётся.

    Сам термин «ИНН» персональными данными не является — он контекст,
    по которому сработал шаблон, и затирать его незачем.
    """
    r = mask_text("ИНН 7701234567, паспорт 45 12 345678.", use_ner=False)
    assert "7701234567" not in r.text
    assert "45 12 345678" not in r.text
    assert "ИНН" in r.text and "паспорт" in r.text.lower()
    assert set(r.counts) == {"ИНН", "ПАСПОРТ"}


def test_short_luhn_valid_number_is_not_a_card() -> None:
    """13 цифр с валидным Луном — не карта: требуется ровно 16 и префикс."""
    r = mask_text("Промежуточный расчёт 5676171967807 за период.", use_ner=False)
    assert "5676171967807" in r.text


def test_author_from_metadata_is_masked() -> None:
    """ФИО автора лежит в метаданных `.docx`, а не в тексте.

    Без отдельной обработки метаданных утекло бы именно оно — проверено
    на реальных примерах работ.
    """
    doc = Document(
        source_name="работа.docx",
        source_format="docx",
        blocks=[Block(1, BlockKind.PARAGRAPH, "Текст работы без имён и контактов.")],
        meta=DocMeta(author="Сидорова Мария Ивановна", has_core_xml=True),
    )
    text, mapping = mask_document(doc)
    assert "Сидорова Мария Ивановна" not in text
    assert "Сидорова Мария Ивановна" in mapping.values()


def test_author_mentioned_in_body_is_also_replaced() -> None:
    doc = Document(
        source_name="работа.docx",
        source_format="docx",
        blocks=[Block(1, BlockKind.PARAGRAPH, "Автор работы: Сидорова Мария. Далее по тексту.")],
        meta=DocMeta(author="Сидорова Мария", has_core_xml=True),
    )
    text, _ = mask_document(doc)
    assert "Сидорова Мария" not in text


def test_original_values_never_reach_the_model() -> None:
    """Самая важная проверка: в теле запроса к модели нет исходных ПДн.

    Проверяется не «работает ли маскирование», а то, что именно уходит
    в модель после полного прохода `mask_document`.
    """
    doc = Document(
        source_name="работа.docx",
        source_format="docx",
        blocks=[Block(i + 1, BlockKind.PARAGRAPH, part) for i, part in enumerate(FIXTURE.split(". "))],
        meta=DocMeta(author="Иванов Пётр Сергеевич", has_core_xml=True),
    )
    model_text, _ = mask_document(doc)

    forbidden = [
        "petr.ivanov@example.com",
        "+7 (999) 123-45-67",
        "@petr_ivanov",
        "123-456-789 01",
        "45 12 345678",
        "Иванов Пётр Сергеевич",
    ]
    leaked = [v for v in forbidden if v in model_text]
    assert leaked == [], f"в модель ушли персональные данные: {leaked}"


def test_scan_counts_without_changing_text() -> None:
    counts = scan(FIXTURE)
    assert counts.get("EMAIL", 0) >= 1
    assert counts.get("ТЕЛЕФОН", 0) >= 1


def test_ner_degrades_without_crashing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отсутствие natasha отключает слой имён, но не роняет работу.

    Деградация обязана быть явной: пользователь должен узнать, что имена
    маскируются только по формату.
    """
    import app.privacy.pii as pii

    monkeypatch.setattr(pii, "_get_ner", lambda: None)
    r = pii.mask_text(FIXTURE, use_ner=True)
    assert not r.ner_available
    assert any("NER" in n for n in r.notes)
    # Форматные данные всё равно замаскированы.
    assert "petr.ivanov@example.com" not in r.text


# ── офлайн-контур ─────────────────────────────────────────────────────────────


def test_outbound_to_foreign_host_is_blocked() -> None:
    """Контур обязан ронять вызов наружу, а не только записывать его."""
    from app.config import settings

    if not settings.offline_enforce:
        pytest.skip("OFFLINE_ENFORCE выключен")
    with pytest.raises(OutboundBlocked):
        audit.guard("https://api.openai.com/v1/chat/completions", purpose="test")


def test_localhost_is_allowed_and_counted_as_internal() -> None:
    audit.guard("http://localhost:11434/v1", purpose="test")
    assert audit.classify("http://localhost:11434/v1") == "internal"
    assert audit.classify("http://127.0.0.1:8000") == "internal"


def test_audit_summary_reports_zero_external_calls() -> None:
    """Баннер «внешних вызовов: 0» опирается на этот счётчик, а не на декларацию."""
    audit.reset()
    audit.record("http://localhost:11434/v1", "llm.chat")
    s = audit.summary()
    assert s["external_calls"] == 0
    assert s["internal_calls"] == 1
    assert s["llm_is_local"] is True


# ── подсветка в просмотрщике ──────────────────────────────────────────────────


def test_pii_spans_point_at_the_same_values_the_shield_masks() -> None:
    """Четвёртый слой просмотрщика обязан показывать ровно то, что маскируется.

    Слой строится тем же щитом, а не отдельной эвристикой: иначе ревьюер
    видел бы одну разметку, а в модель уходила другая, и подсветка стала бы
    декорацией. Тест это и фиксирует — позиции указывают на значения,
    которых нет в замаскированном тексте.
    """
    from app.api.routes import _pii_spans
    from app.privacy.pii import mask_text

    text = "Связаться: ivan@example.com или +7 (999) 123-45-67."
    spans = _pii_spans(text)

    assert {s["label"] for s in spans} == {"EMAIL", "ТЕЛЕФОН"}

    masked = mask_text(text).text
    for span in spans:
        value = text[span["start"] : span["end"]]
        assert value not in masked, f"{value!r} подсвечено, но не замаскировано"


def test_pii_spans_do_not_overlap() -> None:
    """Пересечения ломали бы разрезание строки в интерфейсе."""
    from app.api.routes import _pii_spans

    text = "Почта ivan@example.com, телефон 8 999 123 45 67, ещё раз ivan@example.com"
    spans = _pii_spans(text)
    assert len(spans) >= 3
    for a, b in zip(spans, spans[1:]):
        assert a["end"] <= b["start"], "интервалы пересеклись"


def test_pii_spans_are_empty_on_clean_text() -> None:
    """Работа без контактов не должна светиться слоем ПДн."""
    from app.api.routes import _pii_spans

    assert _pii_spans("Карта рисков продукта: вероятность, влияние, митигация.") == []


# ── NER: точность распознавания людей ─────────────────────────────────────────


def test_product_names_are_not_masked_as_people() -> None:
    """Название продукта — не персональные данные.

    Найдено на реальной работе, когда подсветка ПДн появилась в интерфейсе:
    NER помечал `PER` название продукта и заголовок таблицы, и в модель
    вместо продукта уходило «ЛИЦО_1». Работу оценивают в том числе по
    корректности описания продукта — такая замена портит саму оценку.
    """
    from app.config import settings

    if not settings.pii_use_ner:
        pytest.skip("NER отключён настройкой")

    r = mask_text("Автотека помогает покупателю проверить историю автомобиля.")
    assert r.mapping == {}, f"замаскировано лишнее: {r.mapping}"

    r2 = mask_text("Верхнеуровневая CJM показывает этапы пути пользователя.")
    assert r2.mapping == {}, f"замаскировано лишнее: {r2.mapping}"


def test_real_names_are_still_masked() -> None:
    """Фильтр ложных срабатываний не должен ослаблять защиту."""
    from app.config import settings

    if not settings.pii_use_ner:
        pytest.skip("NER отключён настройкой")

    r = mask_text("Работу проверил Павел Кузнецов.")
    assert "Павел Кузнецов" not in r.text
    assert "Павел Кузнецов" in r.mapping.values()


def test_different_people_get_different_pseudonyms() -> None:
    """Два человека не должны сливаться в одного.

    Счётчик псевдонимов перебирал значения карты вместо ключей и всегда
    оставался нулём: каждый следующий человек получал тот же «ЛИЦО_1».
    В тексте разные люди становились одним, а обратная подстановка
    возвращала студенту чужое имя.
    """
    from app.config import settings

    if not settings.pii_use_ner:
        pytest.skip("NER отключён настройкой")

    text = "Ревьюер Павел Кузнецов. Методист Ирина Соколова. Студент Анна Лебедева."
    r = mask_text(text)
    tokens = [t for t in r.mapping if t.startswith("ЛИЦО_")]

    assert len(tokens) == len(set(tokens)) >= 2, f"псевдонимы слиплись: {r.mapping}"
    for token in tokens:
        assert r.text.count(token) >= 1
    assert unmask(r.text, r.mapping) == text, "обратная подстановка не восстановила текст"
