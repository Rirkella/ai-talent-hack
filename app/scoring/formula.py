"""Агрегация: два независимых числа.

**(а) Предварительный балл** — сумма баллов по критериям с весами, минус
штрафы. Это оценка работы.

**(б) Индекс приоритета ручной проверки** — насколько срочно работу должен
посмотреть человек. Это оценка *риска ошибки автоматики*, а не работы.

Разделение обязательно по условию кейса: генеративная проверка не может
автоматически влиять на оценку студента. Поэтому сигнал детектора входит
только в (б) и никогда в (а). Тумблер детектора гасит его вклад в приоритет,
но и включённым он не касается балла.

Все веса и тумблеры вынесены в `ScoringConfig` и правятся из интерфейса:
методист должен уметь настроить формулу, не трогая код.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agent.schemas import ReviewResult
from app.rubric.models import Rubric


@dataclass
class PriorityWeights:
    """Веса компонентов индекса приоритета. Каждый — с тумблером."""

    ai_signal: float = 0.35
    ai_signal_on: bool = True

    similarity: float = 0.25
    similarity_on: bool = True

    unverified_evidence: float = 0.20
    unverified_evidence_on: bool = True

    formal_violations: float = 0.10
    formal_violations_on: bool = True

    # Балл у границы между оценками — там ошибка автоматики дороже всего.
    borderline_score: float = 0.10
    borderline_score_on: bool = True

    failed_steps: float = 0.30
    failed_steps_on: bool = True


@dataclass
class ScoringConfig:
    """Настройки формулы. Пресеты сохраняются и применяются к потоку."""

    weights: PriorityWeights = field(default_factory=PriorityWeights)
    # Штраф за формальные нарушения — отдельный компонент с тумблером.
    formal_penalty_on: bool = False
    formal_penalty_per_violation: float = 0.5
    formal_penalty_max: float = 1.0
    # Границы, вблизи которых балл считается пограничным.
    borderline_band: float = 0.15


def aggregate(
    result: ReviewResult, rubric: Rubric, config: ScoringConfig | None = None
) -> ReviewResult:
    """Считает предварительный балл и индекс приоритета. Меняет `result` на месте."""
    cfg = config or ScoringConfig()

    # ── (а) предварительный балл ─────────────────────────────────────────
    total = 0.0
    max_total = 0.0
    for c in result.criteria:
        weight = 1.0
        if (crit := rubric.criterion(c.criterion_id)) is not None:
            weight = crit.weight
        max_total += c.max_score * weight
        # Неоценённый критерий не приносит баллов, но и не обнуляет работу:
        # его максимум остаётся в знаменателе, а ревьюер видит пометку.
        if not c.failed:
            total += c.score * weight

    penalties: list[dict] = []

    if cfg.formal_penalty_on:
        violations = [f for f in result.formal if f.get("status") == "fail"]
        if violations:
            amount = min(
                len(violations) * cfg.formal_penalty_per_violation, cfg.formal_penalty_max
            )
            penalties.append({
                "code": "formal",
                "title": "Нарушения оформления",
                "amount": amount,
                "detail": ", ".join(v["title"] for v in violations),
            })

    result.max_score = round(max_total, 2)
    result.preliminary_score = round(max(0.0, total - sum(p["amount"] for p in penalties)), 2)
    result.penalties = penalties

    # ── (б) индекс приоритета ручной проверки ────────────────────────────
    w = cfg.weights
    index = 0.0
    reasons: list[str] = []

    if w.ai_signal_on and result.ai_signal:
        # ВАЖНО: сигнал генИИ входит только сюда и никогда в балл.
        score = float(result.ai_signal.get("score", 0.0))
        if score > 0:
            index += w.ai_signal * score
            reasons.append(
                f"признаки генеративного ИИ: {score:.2f} "
                f"(уверенность «{result.ai_signal.get('confidence', '—')}»)"
            )

    if w.similarity_on and result.similarity:
        sim = float(result.similarity.get("max_similarity", 0.0))
        if sim > 0:
            index += w.similarity * sim
            reasons.append(f"схожесть с другой работой: {sim:.0%}")

    if w.unverified_evidence_on and result.criteria:
        rated = [c for c in result.criteria if not c.failed and c.evidence_total]
        if rated:
            share = sum(1 for c in rated if c.unverified) / len(rated)
            if share:
                index += w.unverified_evidence * share
                reasons.append(f"критериев без подтверждённых цитат: {share:.0%}")
        # Критерий вообще без цитат — тоже повод посмотреть глазами.
        no_ev = [c for c in result.criteria if not c.failed and not c.evidence_total]
        if no_ev:
            index += w.unverified_evidence * (len(no_ev) / len(result.criteria)) * 0.5
            reasons.append(f"критериев без цитат: {len(no_ev)}")

    if w.formal_violations_on:
        violations = [f for f in result.formal if f.get("status") == "fail"]
        if violations:
            index += w.formal_violations * min(1.0, len(violations) / 3)
            reasons.append(f"формальных нарушений: {len(violations)}")

    if w.borderline_score_on and result.max_score:
        share = result.preliminary_score / result.max_score
        # Ближе всего к границе — половина шкалы и порог зачёта.
        for edge in (0.5, 0.6):
            if abs(share - edge) <= cfg.borderline_band:
                index += w.borderline_score
                reasons.append(f"балл у границы: {share:.0%} от максимума")
                break

    if w.failed_steps_on and (failed := result.failed_steps):
        index += w.failed_steps * min(1.0, len(failed) / 2)
        reasons.append(f"не выполнены шаги: {', '.join(failed)}")

    result.priority_index = round(min(index, 1.0), 3)
    result.priority_reasons = reasons
    result.reviewer_summary = _summary(result)
    return result


def _summary(result: ReviewResult) -> str:
    """Короткая сводка для карточки в очереди ревьюера."""
    parts = [f"Предварительно {result.preliminary_score:g} из {result.max_score:g}."]

    if failed := [c for c in result.criteria if c.failed]:
        parts.append(f"Не оценено критериев: {len(failed)}.")
    if unverified := [c for c in result.criteria if c.unverified]:
        parts.append(f"Без подтверждённых цитат: {len(unverified)}.")
    if violations := [f for f in result.formal if f.get("status") == "fail"]:
        parts.append(f"Формальных нарушений: {len(violations)}.")
    if result.priority_index >= 0.5:
        parts.append("Высокий приоритет ручной проверки.")

    return " ".join(parts)


def apply_deadline_penalty(
    result: ReviewResult, *, penalty: float, reason: str
) -> ReviewResult:
    """Штраф за просрочку по правилу из условия.

    Вынесен отдельно от `aggregate`, потому что применяется по событию
    наступления срока и пересчитывается на лету, когда работа переходит
    из состояния в состояние.
    """
    result.penalties = [p for p in result.penalties if p["code"] != "deadline"]
    if penalty > 0:
        result.penalties.append(
            {"code": "deadline", "title": "Нарушение срока сдачи",
             "amount": penalty, "detail": reason}
        )
    base = sum(
        c.score * 1.0 for c in result.criteria if not c.failed
    )
    total_penalty = sum(p["amount"] for p in result.penalties)
    result.preliminary_score = round(max(0.0, base - total_penalty), 2)
    result.reviewer_summary = _summary(result)
    return result
