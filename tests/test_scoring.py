"""Тесты формулы: балл и индекс приоритета — два независимых числа.

Главный тест набора — `test_ai_signal_never_affects_score`. Кейс прямо
запрещает генеративной проверке автоматически влиять на оценку студента,
и это единственная проверка, которая доказывает соблюдение запрета
структурно, а не на словах.
"""

from __future__ import annotations

from app.agent.schemas import CriterionResult, Evidence, EvidenceStatus, ReviewResult, TraceStep
from app.rubric.models import Criterion, Rubric
from app.scoring.formula import ScoringConfig, aggregate, apply_deadline_penalty


def _rubric() -> Rubric:
    r = Rubric(
        criteria=[
            Criterion(id="c1", name="Описание продукта", max_score=1),
            Criterion(id="c2", name="Анализ рисков", max_score=4),
            Criterion(id="c3", name="Расчёт ROI", max_score=2),
        ]
    )
    r.approve(by="test")
    return r


def _result(*, ai_score: float = 0.0, unverified: bool = False) -> ReviewResult:
    return ReviewResult(
        criteria=[
            CriterionResult(
                criterion_id="c1", criterion_name="Описание продукта",
                max_score=1, score=1.0, verdict="",
                evidence=[Evidence(block=1, quote="цитата", status=EvidenceStatus.VERIFIED)],
                evidence_total=1, evidence_verified=1,
            ),
            CriterionResult(
                criterion_id="c2", criterion_name="Анализ рисков",
                max_score=4, score=3.0, verdict="",
                evidence=[Evidence(block=2, quote="цитата")],
                evidence_total=1,
                evidence_verified=0 if unverified else 1,
                unverified=unverified,
            ),
            CriterionResult(
                criterion_id="c3", criterion_name="Расчёт ROI",
                max_score=2, score=1.5, verdict="",
                evidence_total=0, evidence_verified=0,
            ),
        ],
        ai_signal={"score": ai_score, "confidence": "средняя"} if ai_score else None,
        trace=[TraceStep(name="оценка по критериям", ok=True, duration_ms=1)],
    )


def test_score_is_sum_of_criteria() -> None:
    r = aggregate(_result(), _rubric())
    assert r.max_score == 7.0
    assert r.preliminary_score == 5.5  # 1.0 + 3.0 + 1.5


def test_ai_signal_never_affects_score() -> None:
    """Требование кейса: генеративная проверка не влияет на оценку автоматически.

    Проверяется на трёх положениях: сигнала нет, сигнал максимальный,
    сигнал максимальный но тумблер выключен. Балл обязан быть одинаковым
    во всех трёх случаях, а приоритет — разным.
    """
    without = aggregate(_result(ai_score=0.0), _rubric())
    with_signal = aggregate(_result(ai_score=1.0), _rubric())

    cfg_off = ScoringConfig()
    cfg_off.weights.ai_signal_on = False
    toggled_off = aggregate(_result(ai_score=1.0), _rubric(), cfg_off)

    assert without.preliminary_score == with_signal.preliminary_score == toggled_off.preliminary_score

    # Приоритет при этом обязан реагировать — иначе сигнал вообще ни на что
    # не влияет и показывать его бессмысленно.
    assert with_signal.priority_index > without.priority_index
    # Выключенный тумблер гасит вклад в приоритет, но не в балл.
    assert toggled_off.priority_index < with_signal.priority_index


def test_unverified_evidence_raises_priority_not_lowers_score() -> None:
    """Непроверенная цитата — техническая неудача, а не невыполнение критерия."""
    verified = aggregate(_result(unverified=False), _rubric())
    unverified = aggregate(_result(unverified=True), _rubric())

    assert unverified.preliminary_score == verified.preliminary_score
    assert unverified.priority_index > verified.priority_index


def test_failed_criterion_does_not_zero_the_work() -> None:
    """Неоценённый критерий не приносит баллов, но максимум остаётся в знаменателе."""
    res = _result()
    res.criteria[1].failed = True
    res.criteria[1].score = 0.0
    out = aggregate(res, _rubric())
    assert out.max_score == 7.0, "максимум не должен уменьшаться"
    assert out.preliminary_score == 2.5  # 1.0 + 1.5


def test_formal_penalty_is_off_by_default() -> None:
    """Штраф за оформление условием не предусмотрен — по умолчанию выключен."""
    res = _result()
    res.formal = [{"status": "fail", "title": "Шрифт в таблицах"}]

    default = aggregate(_result() if False else res, _rubric())
    assert default.penalties == []

    cfg = ScoringConfig()
    cfg.formal_penalty_on = True
    res2 = _result()
    res2.formal = [{"status": "fail", "title": "Шрифт в таблицах"}]
    penalised = aggregate(res2, _rubric(), cfg)
    assert penalised.penalties
    assert penalised.preliminary_score < default.preliminary_score


def test_deadline_penalty_applies_once() -> None:
    """Правило из условия: досдача в течение суток стоит −1 балл."""
    res = aggregate(_result(), _rubric())
    base = res.preliminary_score

    apply_deadline_penalty(res, penalty=1.0, reason="досдача")
    assert res.preliminary_score == base - 1.0

    # Повторный вызов не должен штрафовать дважды: планировщик тикает
    # раз в секунду и вызывает пересчёт многократно.
    apply_deadline_penalty(res, penalty=1.0, reason="досдача")
    assert res.preliminary_score == base - 1.0


def test_failed_step_raises_priority() -> None:
    res = _result()
    res.trace.append(TraceStep(name="признаки генеративного ИИ", ok=False, duration_ms=1))
    out = aggregate(res, _rubric())
    assert out.priority_index > 0
    assert any("не выполнены шаги" in r for r in out.priority_reasons)


def test_priority_is_capped_at_one() -> None:
    """Индекс — величина в [0,1], иначе сортировка очереди теряет смысл."""
    res = _result(ai_score=1.0, unverified=True)
    res.formal = [{"status": "fail", "title": f"нарушение {i}"} for i in range(5)]
    res.similarity = {"max_similarity": 1.0}
    res.trace.append(TraceStep(name="шаг", ok=False, duration_ms=1))
    out = aggregate(res, _rubric())
    assert 0.0 <= out.priority_index <= 1.0


def test_weights_are_configurable() -> None:
    """Веса меняются настройкой, а не правкой кода."""
    cfg = ScoringConfig()
    cfg.weights.ai_signal = 1.0
    strong = aggregate(_result(ai_score=0.5), _rubric(), cfg)

    cfg2 = ScoringConfig()
    cfg2.weights.ai_signal = 0.1
    weak = aggregate(_result(ai_score=0.5), _rubric(), cfg2)

    assert strong.priority_index > weak.priority_index
    assert strong.preliminary_score == weak.preliminary_score


# ── границы настроек формулы ──────────────────────────────────────────────────


def test_weights_outside_zero_one_are_refused() -> None:
    """Отрицательный вес переворачивает смысл очереди.

    API принимал любое число: вес −5 и вес 99 сохранялись молча. При
    отрицательном весе работа с признаками генеративного ИИ опускалась бы
    в самый низ очереди — ровно туда, где её никто не смотрит.
    """
    from app.api.routes import _scoring, set_scoring  # noqa: F401
    from fastapi.testclient import TestClient

    from app.api.deps import current_user
    from app.main import app
    from app.models import Role, User

    app.dependency_overrides[current_user] = lambda: User(
        id="coord-1", name="Методист", role=Role.COORDINATOR
    )
    client = TestClient(app)
    try:
        for bad in (-5, 1.5, 99):
            r = client.put("/api/scoring", json={"weights": {"ai_signal": bad}})
            assert r.status_code == 400, f"вес {bad} принят"
            assert "от 0 до 1" in r.json()["detail"]

        # Опечатка в имени тоже отвергается: раньше она оседала в настройках
        # мёртвым грузом, и методист думал, что настроил, а не менялось ничего.
        r = client.put("/api/scoring", json={"weights": {"ai_signal_typo": 0.5}})
        assert r.status_code == 400
        assert "Неизвестный вес" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()
