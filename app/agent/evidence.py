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
    """Проверяет одну цитату. Возвращает её же с заполненным статусом."""
    quote = normalize(ev.quote)

    if len(quote) < MIN_QUOTE_CHARS:
        ev.status = EvidenceStatus.NOT_FOUND
        ev.similarity = 0.0
        return ev

    target = doc.block(ev.block)
    if target is None:
        ev.status = EvidenceStatus.NO_BLOCK
    else:
        score = _best_match(quote, normalize(target.text))
        ev.similarity = round(score, 1)
        if score >= SIMILARITY_THRESHOLD:
            ev.status = EvidenceStatus.VERIFIED
            ev.found_in_block = target.index
            return ev
        ev.status = EvidenceStatus.NOT_FOUND

    # Цитата могла быть верной, но с ошибкой в номере блока. Это менее тяжёлый
    # случай, чем выдумка, и ревьюеру полезно знать разницу.
    best_block, best_score = None, 0.0
    for b in doc.blocks:
        if b.index == ev.block:
            continue
        score = _best_match(quote, normalize(b.text))
        if score > best_score:
            best_block, best_score = b.index, score

    if best_block is not None and best_score >= SIMILARITY_THRESHOLD:
        ev.status = EvidenceStatus.WRONG_BLOCK
        ev.found_in_block = best_block
        ev.similarity = round(best_score, 1)
    else:
        ev.similarity = round(max(ev.similarity, best_score), 1)
    return ev


def _best_match(needle: str, haystack: str) -> float:
    """Схожесть цитаты с наиболее похожим фрагментом блока.

    `partial_ratio` ищет лучшее вхождение подстроки, что и требуется:
    цитата — фрагмент блока, а не весь блок целиком.
    """
    if not needle or not haystack:
        return 0.0
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


def explain(ev: Evidence) -> str:
    """Человекочитаемое пояснение статуса — попадает в интерфейс ревьюера."""
    match ev.status:
        case EvidenceStatus.VERIFIED:
            return f"подтверждено в §{ev.found_in_block} (схожесть {ev.similarity:g}%)"
        case EvidenceStatus.WRONG_BLOCK:
            return (
                f"текст найден, но в §{ev.found_in_block}, а не в §{ev.block} "
                f"(схожесть {ev.similarity:g}%)"
            )
        case EvidenceStatus.NO_BLOCK:
            return f"блока §{ev.block} в работе нет"
        case _:
            return f"в работе не найдено (лучшая схожесть {ev.similarity:g}%)"
