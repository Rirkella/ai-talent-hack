"""ПДн-щит: псевдонимизация до обращения к модели.

Не декоративный модуль. В метаданных реальных работ лежат настоящие ФИО
авторов, а в тексте встречаются контакты. Кейс прямо запрещает передачу
персональных данных студентов в незащищённые сервисы и требует описать меры
защиты, поэтому маскирование выполняется **всегда до вызова модели**, а не
после, и не «при необходимости».

Два слоя:

* **Регулярные выражения** — для форматных данных: почта, телефон, ник в
  мессенджере, ссылка, ИНН, СНИЛС, паспорт, номер карты (с проверкой Луна),
  дата рождения. Здесь регулярка точнее любой модели.
* **NER `natasha`** — для имён людей, которые формат не выдаёт. Работает на
  CPU, без torch. Если библиотека недоступна, слой отключается, а работа
  продолжается на одних регулярках — с явной пометкой, а не молча.

Замены обратимы: карта псевдонимов остаётся локальной и применяется в обратную
сторону при выдаче фидбека студенту, чтобы он видел своё имя, а не `ЛИЦО_1`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.ingest.document import Document

log = logging.getLogger(__name__)

# ── регулярные выражения ──────────────────────────────────────────────────────

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")),
    # Телефон РФ в любом популярном оформлении.
    # Разделители заданы как `*`, а не `?`: между кодом страны и кодом
    # оператора стоят ДВА символа — пробел и скобка («+7 (999) 123-45-67»),
    # и с `?` номер утекал в модель незамаскированным.
    (
        "ТЕЛЕФОН",
        re.compile(r"(?:\+7|\b8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}\b"),
    ),
    ("МЕССЕНДЖЕР", re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{4,31}\b")),
    ("ССЫЛКА", re.compile(r"https?://\S+|(?<![\w@.])www\.\S+")),
    # СНИЛС узнаётся по разделителям, дополнительный контекст не нужен.
    ("СНИЛС", re.compile(r"\b\d{3}-\d{3}-\d{3}\s?\d{2}\b")),
    # ИНН и паспорт — только при явном упоминании рядом.
    #
    # Без требования контекста под маску уходило любое 12-значное (ИНН) и
    # 10-значное (паспорт) число. Проверено на реальной работе по бизнес-модели:
    # в таблице финансовой модели нашлось 34 «ИНН» и 1 «паспорт» — это были
    # расчётные показатели. Маскировать выручку в работе, которую оценивают
    # именно по расчётам, недопустимо: щит уничтожил бы содержание.
    (
        "ИНН",
        re.compile(r"(?:\bИНН\b|\bинн\b)[\s:№]*(\d{10}|\d{12})\b"),
    ),
    (
        "ПАСПОРТ",
        re.compile(
            r"(?:паспорт\w*|серия\s+и\s+номер)[\s:№]*(\d{2}\s?\d{2}\s?\d{6})\b",
            re.I,
        ),
    ),
    ("ДАТА_РОЖДЕНИЯ", re.compile(r"\b\d{2}[./]\d{2}[./](?:19|20)\d{2}\b")),
]

# Номер карты: алгоритм Луна плюс два ограничения.
#
# Одного Луна мало — случайное 13-значное число проходит проверку примерно
# в одном случае из десяти, и в финансовой таблице таких чисел много.
# Поэтому требуется ровно 16 цифр и начало, соответствующее реальным
# платёжным системам: 2 (Мир), 4 (Visa), 5 (Mastercard), 6 (Maestro/UnionPay).
# Комбинация «16 цифр + допустимый префикс + Лун» даёт вероятность ложного
# срабатывания около 0,4 % вместо 10 %.
_CARD = re.compile(r"\b([2456]\d{3})[ -]?(\d{4})[ -]?(\d{4})[ -]?(\d{4})\b")


def _luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass
class MaskReport:
    """Что именно было замаскировано — для вкладки «Приватность»."""

    text: str
    mapping: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    ner_available: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.mapping)


# ── NER ───────────────────────────────────────────────────────────────────────

_ner_cache: object | None = None
_ner_failed = False


def _get_ner():  # noqa: ANN202
    """Ленивая инициализация natasha: модели грузятся секунды, и только раз."""
    global _ner_cache, _ner_failed
    if _ner_cache is not None or _ner_failed:
        return _ner_cache
    try:
        from natasha import (
            Doc,
            NamesExtractor,
            NewsEmbedding,
            NewsNERTagger,
            NewsMorphTagger,
            MorphVocab,
            Segmenter,
        )

        _ner_cache = {
            "segmenter": Segmenter(),
            "emb": (emb := NewsEmbedding()),
            "ner": NewsNERTagger(emb),
            "morph": NewsMorphTagger(emb),
            "vocab": (vocab := MorphVocab()),
            "names": NamesExtractor(vocab),
            "Doc": Doc,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("natasha недоступна, работают только регулярки: %s", exc)
        _ner_failed = True
        _ner_cache = None
    return _ner_cache


def _looks_like_person(ner: dict, fragment: str) -> bool:
    """Отсеивает ложные `PER` — второй фильтр поверх NER.

    Проблема нашлась на реальной работе, когда подсветка ПДн появилась в
    интерфейсе: `NewsNERTagger` уверенно помечал `PER` названия продукта
    («Автотека», «Автотеки») и заголовок таблицы «Верхнеуровневая CJM».
    Замена названия продукта на `ЛИЦО_1` — не перестраховка, а порча: работу
    оценивают в том числе по корректности описания продукта, а модель видела
    на этом месте псевдоним человека.

    Фильтр — `NamesExtractor` из той же natasha: он разбирает фрагмент на
    имя, фамилию и отчество. Проверено на выборке: разбирается всё, что
    действительно является именем, — одиночная фамилия, инициалы, полное
    ФИО, нерусские имена кириллицей; не разбирается ровно то, что оказалось
    ложным срабатыванием.

    Плата за это честная: `PER`-фрагмент, который разборщик не осилил
    (например, имя латиницей), маскироваться перестанет. Риск ограничен —
    такие имена `NewsNERTagger` и без того не находит, а форматные
    идентификаторы (почта, телефон, паспорт) ловятся регулярками, которые
    этот фильтр не трогает. ФИО автора из метаданных заменяется по точному
    совпадению строки и через NER вообще не проходит.
    """
    extractor = ner.get("names")
    if extractor is None:
        return True
    try:
        match = extractor.find(fragment)
    except Exception:  # noqa: BLE001 — разборщик не обязан справляться со всем
        return True
    if match is None:
        return False
    fact = match.fact
    return any(getattr(fact, part, None) for part in ("first", "last", "middle"))


def _mask_names(text: str, mapping: dict[str, str], counts: dict[str, int]) -> tuple[str, bool]:
    """Маскирует имена людей через NER. Возвращает текст и доступность NER."""
    ner = _get_ner()
    if not ner:
        return text, False

    doc = ner["Doc"](text)
    doc.segment(ner["segmenter"])
    doc.tag_ner(ner["ner"])

    # Правим с конца, чтобы смещения предыдущих совпадений оставались верными.
    spans = [s for s in doc.spans if s.type == "PER" and _looks_like_person(ner, text[s.start : s.stop])]
    seen: dict[str, str] = {}
    for span in sorted(spans, key=lambda s: s.start, reverse=True):
        original = text[span.start : span.stop]
        if original in seen:
            token = seen[original]
        else:
            # Считаются КЛЮЧИ карты, а не значения. Раньше здесь перебирались
            # `mapping.values()` — то есть исходные ФИО, которые, разумеется,
            # никогда не начинаются с «ЛИЦО_». Счётчик всегда оставался нулём,
            # и каждый следующий человек получал тот же самый `ЛИЦО_1`: в
            # тексте два разных человека сливались в одного, а обратная
            # подстановка возвращала студенту чужое имя.
            token = f"ЛИЦО_{len([k for k in mapping if k.startswith('ЛИЦО_')]) + 1}"
            seen[original] = token
            mapping[token] = original
            counts["ФИО"] = counts.get("ФИО", 0) + 1
        text = text[: span.start] + token + text[span.stop :]
    return text, True


# ── публичный интерфейс ───────────────────────────────────────────────────────


def mask_text(text: str, *, use_ner: bool | None = None) -> MaskReport:
    """Заменяет персональные данные на псевдонимы."""
    from app.config import settings

    use_ner = settings.pii_use_ner if use_ner is None else use_ner
    mapping: dict[str, str] = {}
    counts: dict[str, int] = {}
    notes: list[str] = []

    def replace(label: str, pattern: re.Pattern[str], s: str) -> str:
        def sub(m: re.Match[str]) -> str:
            # Если в шаблоне есть группа, маскируется только она: у ИНН и
            # паспорта совпадение включает ключевое слово («ИНН 7701234567»),
            # и затирать сам термин не нужно — он не персональные данные,
            # а контекст, по которому шаблон и сработал.
            target = m.group(1) if m.groups() and m.group(1) else m.group(0)
            prefix = m.group(0)[: m.start(1) - m.start(0)] if m.groups() and m.group(1) else ""

            # Одинаковое значение всегда получает один и тот же псевдоним,
            # иначе в тексте распадаются связи между упоминаниями.
            for tok, val in mapping.items():
                if val == target:
                    return prefix + tok
            token = f"{label}_{counts.get(label, 0) + 1}"
            counts[label] = counts.get(label, 0) + 1
            mapping[token] = target
            return prefix + token

        return pattern.sub(sub, s)

    out = text
    for label, pattern in PATTERNS:
        out = replace(label, pattern, out)

    def card_sub(m: re.Match[str]) -> str:
        raw = m.group(0)
        digits = re.sub(r"\D", "", raw)
        # Шаблон уже гарантирует 16 цифр и допустимый префикс; Лун —
        # последний фильтр.
        if not (len(digits) == 16 and _luhn(digits)):
            return raw
        token = f"КАРТА_{counts.get('КАРТА', 0) + 1}"
        counts["КАРТА"] = counts.get("КАРТА", 0) + 1
        mapping[token] = raw
        return token

    out = _CARD.sub(card_sub, out)

    ner_available = True
    if use_ner:
        out, ner_available = _mask_names(out, mapping, counts)
        if not ner_available:
            notes.append(
                "NER-модель недоступна: имена людей маскируются только там, где их "
                "выдаёт формат. Работают регулярные выражения."
            )
    else:
        ner_available = False
        notes.append("NER отключён настройкой PII_USE_NER=false.")

    return MaskReport(text=out, mapping=mapping, counts=counts,
                      ner_available=ner_available, notes=notes)


def mask_document(doc: Document, *, max_chars: int | None = None) -> tuple[str, dict[str, str]]:
    """Маскирует текст работы вместе с метаданными.

    Метаданные обрабатываются отдельно: ФИО автора лежит в `docProps`, а не
    в тексте, и без этого шага утекло бы именно оно.

    `max_chars` ограничивает объём текста, уходящего в модель. Обрезка
    выполняется по границам блоков и оставляет в тексте явную пометку —
    молча укоротить работу нельзя.
    """
    report = mask_text(doc.numbered_text(max_chars=max_chars))
    text, mapping = report.text, dict(report.mapping)

    for value in (doc.meta.author, doc.meta.last_modified_by):
        if not value or value in mapping.values():
            continue
        token = f"ЛИЦО_{len([v for v in mapping if v.startswith('ЛИЦО_')]) + 1}"
        mapping[token] = value
        # Имя из метаданных может встречаться и в самом тексте.
        text = text.replace(value, token)

    return text, mapping


def unmask(text: str, mapping: dict[str, str]) -> str:
    """Возвращает исходные значения на место псевдонимов."""
    for token, original in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(token, original)
    return text


def scan(text: str) -> dict[str, int]:
    """Сколько ПДн нашлось — без изменения текста. Для вкладки «Приватность»."""
    return mask_text(text).counts
