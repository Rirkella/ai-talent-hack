"""Программная проверка доказательств — главный анти-галлюцинационный механизм.

Модель обязана подкреплять каждый балл дословной цитатой с указанием блока
`§N`. Здесь код проверяет, что процитированный текст действительно есть в
работе, и именно в названном блоке.

Почему нечёткое сравнение, а не точное совпадение: модель нормализует пробелы,
раскрывает сокращения, меняет регистр и падежные окончания при переносе цитаты.
Требовать посимвольного равенства значило бы отбраковывать честные цитаты.
Поэтому сравнение ведётся по нормализованному тексту с порогом схожести.

Что делает непройденная проверка: балл **не снижается автоматически** —
техническая неудача сопоставления не доказывает, что критерий не выполнен.
Критерий помечается как неподтверждённый, и это поднимает индекс приоритета
ручной проверки. Решение остаётся за человеком.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from app.agent.schemas import Evidence, EvidenceIn, EvidenceStatus
from app.ingest.document import Document

# Порог схожести для засчитывания цитаты. Подобран так, чтобы пережить
# нормализацию пробелов и мелкие расхождения, но отбраковать пересказ.
SIMILARITY_THRESHOLD = 82.0
# Слишком короткая цитата совпадает со случайным текстом и ничего не доказывает.
MIN_QUOTE_CHARS = 12

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[«»\"'`´‘’“”\[\]()]+")


def normalize(text: str) -> str:
    """Приводит текст к виду, устойчивому к косметическим расхождениям."""
    t = text.lower().replace("ё", "е")
    t = _PUNCT.sub(" ", t)
    t = t.replace("—", "-").replace("–", "-")
    return _WS.sub(" ", t).strip()


def verify_evidence(ev: Evidence, doc: Document) -> Evidence:
    """Проверяет одну цитату. Возвращает её же с заполненным статусом.

    Порядок проверок:

    1. дословное вхождение в названный блок — `verified`;
    2. дословное вхождение в другой блок — `wrong_block`;
    3. похожий текст (нечёткое сравнение) — `approximate`, кандидат на
       ручную проверку;
    4. ничего — `not_found`.

    Разделение первых трёх случаев появилось после внешнего аудита: до него
    `verified` ставился по нечёткому сходству, и цитата с отброшенной
    частицей «не» или изменённым числом считалась подтверждённой. Механизм,
    который должен ловить выдумки, сам их подтверждал.
    """
    quote = normalize(ev.quote)

    if len(quote) < MIN_QUOTE_CHARS:
        ev.status = EvidenceStatus.NOT_FOUND
        ev.similarity = 0.0
        return ev

    target = doc.block(ev.block)

    # ── (1) дословно в названном блоке ──────────────────────────────────
    if target is not None and quote in normalize(target.text):
        ev.status = EvidenceStatus.VERIFIED
        ev.found_in_block = target.index
        ev.similarity = 100.0
        return ev

    # ── (2) дословно, но в другом блоке ─────────────────────────────────
    for b in doc.blocks:
        if target is not None and b.index == target.index:
            continue
        if quote in normalize(b.text):
            ev.status = EvidenceStatus.WRONG_BLOCK
            ev.found_in_block = b.index
            ev.similarity = 100.0
            return ev

    # ── (3) похоже, но не дословно ──────────────────────────────────────
    best_block, best_score = None, 0.0
    if target is not None:
        best_block = target.index
        best_score = _best_match(quote, normalize(target.text))
    for b in doc.blocks:
        if target is not None and b.index == target.index:
            continue
        score = _best_match(quote, normalize(b.text))
        if score > best_score:
            best_block, best_score = b.index, score

    ev.similarity = round(best_score, 1)
    if best_score >= SIMILARITY_THRESHOLD:
        ev.status = EvidenceStatus.APPROXIMATE
        ev.found_in_block = best_block
    elif target is None:
        ev.status = EvidenceStatus.NO_BLOCK
    else:
        ev.status = EvidenceStatus.NOT_FOUND
    return ev


def _best_match(needle: str, haystack: str) -> float:
    """Схожесть цитаты с наиболее похожим фрагментом блока.

    `partial_ratio` ищет лучшее вхождение более короткой строки в более
    длинную. Пока цитата короче блока, это то, что нужно: цитата — фрагмент
    блока, а не весь блок целиком.

    Но `partial_ratio` симметричен по длине, и когда цитата **длиннее**
    блока, он начинает мерить обратное — насколько блок содержится в цитате.
    Модель, склеившая цитату из нескольких блоков, получала за это 100 %
    рядом с пометкой «не дословно»: в карточке стояло «≈ … 100 %», то есть
    два взаимоисключающих утверждения. Здесь это не так: если цитата длиннее
    блока, блок её содержать не может, и сравнение идёт целиком.
    """
    if not needle or not haystack:
        return 0.0
    if len(needle) > len(haystack):
        return float(fuzz.ratio(needle, haystack))
    return float(fuzz.partial_ratio(needle, haystack))


def verify_all(
    evidence: list[EvidenceIn], doc: Document
) -> tuple[list[Evidence], int]:
    """Проверяет цитаты модели. Возвращает обогащённые и число подтверждённых.

    На вход приходит `EvidenceIn` — то, что вернула модель. На выходе
    `Evidence` со статусом, посчитанным здесь. Разделение типов гарантирует,
    что статус проверки не может прийти из ответа модели.
    """
    checked = [
        verify_evidence(Evidence(block=ev.block, quote=ev.quote), doc) for ev in evidence
    ]
    return checked, sum(1 for e in checked if e.is_verified)