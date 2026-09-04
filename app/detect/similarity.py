"""Схожесть работ: дублирование и признаки заимствования.

Дополнительная функция кейса — выявление дублирования и плагиата внутри
потока. Считается детерминированно, без моделей и без сети:

* **TF-IDF по символьным n-граммам.** Символьные n-граммы устойчивы к
  перестановке слов и смене окончаний — русский язык флективный, и пословное
  сравнение здесь заметно слабее.
* **Шинглы.** Пересечение множеств словесных n-грамм ловит дословно
  скопированные абзацы, которые косинусная мера на длинных работах размывает.

Отдельная эмбеддинг-модель сознательно не заводится: она потребовала бы
второй модели в памяти и не дала бы для задачи «найти списанное» ничего
сверх того, что даёт совпадение n-грамм.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

# Порог, с которого пара считается подозрительной. Учебные работы по одному
# заданию неизбежно похожи: те же термины, та же структура, тот же продукт.
# Значение подобрано так, чтобы не поднимать флаг на честном сходстве темы.
SUSPICIOUS = 0.55
SHINGLE_SIZE = 5

_WORD = re.compile(r"\b[а-яёa-z0-9]+\b", re.I)


@dataclass
class PairSimilarity:
    a_id: str
    b_id: str
    cosine: float
    shingle: float
    # Дословно совпавшие фрагменты — то, что ревьюер увидит глазами.
    shared_fragments: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        """Итоговая мера: берётся максимум, а не среднее.

        Работы могут совпадать либо по общей лексике, либо дословными
        кусками. Усреднение размыло бы второй случай, а он важнее.
        """
        return round(max(self.cosine, self.shingle), 3)

    @property
    def is_suspicious(self) -> bool:
        return self.score >= SUSPICIOUS


@dataclass
class SimilarityReport:
    pairs: list[PairSimilarity] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def for_work(self, work_id: str) -> list[PairSimilarity]:
        out = [p for p in self.pairs if work_id in (p.a_id, p.b_id)]
        return sorted(out, key=lambda p: -p.score)

    def max_for(self, work_id: str) -> float:
        pairs = self.for_work(work_id)
        return pairs[0].score if pairs else 0.0

    @property
    def suspicious(self) -> list[PairSimilarity]:
        return sorted(
            [p for p in self.pairs if p.is_suspicious], key=lambda p: -p.score
        )


def _shingles(text: str, size: int = SHINGLE_SIZE) -> set[str]:
    words = [w.lower() for w in _WORD.findall(text)]
    if len(words) < size:
        return set()
    return {" ".join(words[i : i + size]) for i in range(len(words) - size + 1)}


def _shared_fragments(a: str, b: str, limit: int = 5) -> list[str]:
    """Самые длинные дословно общие фрагменты — для показа ревьюеру."""
    common = _shingles(a) & _shingles(b)
    if not common:
        return []
    # Склеиваем пересекающиеся шинглы, чтобы показать связный фрагмент,
    # а не набор пятисловных обрывков.
    merged: list[str] = []
    for frag in sorted(common, key=len, reverse=True):
        if not any(frag in m for m in merged):
            merged.append(frag)
        if len(merged) >= limit:
            break
    return merged


def compare_texts(texts: dict[str, str]) -> SimilarityReport:
    """Попарно сравнивает работы потока."""
    report = SimilarityReport()
    ids = [k for k, v in texts.items() if v.strip()]

    if len(ids) < 2:
        report.notes.append("Для сравнения нужны минимум две непустые работы.")
        return report

    corpus = [texts[i] for i in ids]
    try:
        vec = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(3, 5),
            min_df=1,
            max_features=50000,
            sublinear_tf=True,
        )
        matrix = vec.fit_transform(corpus)
        normed = matrix / np.maximum(
            np.sqrt(matrix.multiply(matrix).sum(axis=1)), 1e-9
        )
        cosine = (normed @ normed.T).toarray()
    except ValueError as exc:
        report.notes.append(f"TF-IDF не построен: {exc}. Сравнение только по шинглам.")
        cosine = np.zeros((len(ids), len(ids)))

    shingle_sets = {i: _shingles(texts[i]) for i in ids}

    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            ia, ib = ids[a], ids[b]
            sa, sb = shingle_sets[ia], shingle_sets[ib]
            jaccard = len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0
            pair = PairSimilarity(
                a_id=ia,
                b_id=ib,
                cosine=round(float(cosine[a, b]), 3),
                shingle=round(jaccard, 3),
            )
            if pair.is_suspicious:
                pair.shared_fragments = _shared_fragments(texts[ia], texts[ib])
            report.pairs.append(pair)

    report.pairs.sort(key=lambda p: -p.score)
    if not report.suspicious:
        report.notes.append(
            f"Пар выше порога {SUSPICIOUS:.0%} не найдено. Сходство по теме и "
            f"терминологии для работ по одному заданию нормально."
        )
    return report
