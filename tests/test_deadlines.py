"""Тесты сроков и штрафов.

Правило проверяется целиком: переход мягкий → штрафной → нулевой, применение
штрафа −1 и обнуление после жёсткого срока. Время не подкручивается —
сдвигаются сами дедлайны, как и в приложении.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import DeadlineState
from app.notify.deadlines import evaluate, review_status, transitions_for

NOW = datetime(2026, 2, 9, 12, 0, 0, tzinfo=timezone.utc)
DUE = datetime(2026, 2, 9, 23, 59, 0, tzinfo=timezone.utc)
HARD = DUE + timedelta(hours=24)


def test_submitted_on_time_has_no_penalty() -> None:
    s = evaluate(submitted_at=DUE - timedelta(hours=3), due_at=DUE,
                 hard_due_at=HARD, now=NOW)
    assert s.state is DeadlineState.ON_TIME
    assert s.penalty == 0.0


def test_late_within_one_day_costs_one_point() -> None:
    """Правило из условия: досдача в течение суток — штраф −1 балл."""
    s = evaluate(submitted_at=DUE + timedelta(hours=5), due_at=DUE,
                 hard_due_at=HARD, now=HARD)
    assert s.state is DeadlineState.LATE_PENALTY
    assert s.penalty == 1.0
    assert not s.forces_zero


def test_after_hard_deadline_score_is_zero() -> None:
    """Работы, не сданные в срок, оцениваются в 0 баллов."""
    s = evaluate(submitted_at=HARD + timedelta(minutes=1), due_at=DUE,
                 hard_due_at=HARD, now=HARD + timedelta(hours=1))
    assert s.state is DeadlineState.LATE_ZERO
    assert s.forces_zero


def test_unsubmitted_work_changes_state_over_time() -> None:
    """Несданная работа проходит все четыре состояния по мере хода времени."""
    kw = {"submitted_at": None, "due_at": DUE, "hard_due_at": HARD, "due_soon_lead_s": 3600}
    assert evaluate(**kw, now=DUE - timedelta(hours=5)).state is DeadlineState.ON_TIME
    assert evaluate(**kw, now=DUE - timedelta(minutes=30)).state is DeadlineState.DUE_SOON
    assert evaluate(**kw, now=DUE + timedelta(hours=2)).state is DeadlineState.LATE_PENALTY
    assert evaluate(**kw, now=HARD + timedelta(seconds=1)).state is DeadlineState.LATE_ZERO


def test_demo_scale_seconds_work_like_days() -> None:
    """Тот же механизм на десятисекундных сроках — так идёт демонстрация."""
    now = datetime.now(timezone.utc)
    due = now + timedelta(seconds=10)
    hard = now + timedelta(seconds=40)
    kw = {"submitted_at": None, "due_at": due, "hard_due_at": hard, "due_soon_lead_s": 5}

    assert evaluate(**kw, now=now).state is DeadlineState.ON_TIME
    assert evaluate(**kw, now=now + timedelta(seconds=6)).state is DeadlineState.DUE_SOON
    assert evaluate(**kw, now=now + timedelta(seconds=11)).state is DeadlineState.LATE_PENALTY
    assert evaluate(**kw, now=now + timedelta(seconds=41)).state is DeadlineState.LATE_ZERO


def test_penalty_applies_to_score() -> None:
    """Штраф действительно уменьшает предварительный балл."""
    from app.agent.schemas import CriterionResult, ReviewResult
    from app.scoring.formula import apply_deadline_penalty

    result = ReviewResult(
        criteria=[
            CriterionResult(criterion_id="c1", criterion_name="К1", max_score=10,
                            score=8.0, verdict=""),
        ],
        max_score=10.0,
        preliminary_score=8.0,
    )
    apply_deadline_penalty(result, penalty=1.0, reason="досдача в течение суток")
    assert result.preliminary_score == 7.0
    assert any(p["code"] == "deadline" for p in result.penalties)

    # Повторное применение не должно штрафовать дважды.
    apply_deadline_penalty(result, penalty=1.0, reason="досдача в течение суток")
    assert result.preliminary_score == 7.0


def test_naive_datetimes_from_sqlite_do_not_crash() -> None:
    """SQLite отдаёт даты без часового пояса — расчёт обязан это пережить.

    Без приведения к UTC сравнение naive и aware дат роняет весь пересчёт
    сроков после первого же перезапуска приложения.
    """
    naive_due = DUE.replace(tzinfo=None)
    naive_submitted = (DUE - timedelta(hours=1)).replace(tzinfo=None)
    s = evaluate(submitted_at=naive_submitted, due_at=naive_due,
                 hard_due_at=None, now=NOW)
    assert s.state is DeadlineState.ON_TIME


def test_transitions_are_ordered_moments() -> None:
    """Планировщик получает три точных момента в хронологическом порядке."""
    points = transitions_for(due_at=DUE, hard_due_at=HARD, due_soon_lead_s=3600)
    assert len(points) == 3
    assert [p[0] for p in points] == sorted(p[0] for p in points)
    assert [p[1] for p in points] == [
        DeadlineState.DUE_SOON, DeadlineState.LATE_PENALTY, DeadlineState.LATE_ZERO
    ]


def test_review_overdue_is_detected() -> None:
    """Просроченная проверка — прямая метрика эффекта из кейса."""
    state, _, _ = review_status(
        review_due_at=NOW - timedelta(days=1), confirmed_at=None, now=NOW
    )
    assert state == "overdue"

    state, _, _ = review_status(
        review_due_at=NOW + timedelta(days=3), confirmed_at=None, now=NOW
    )
    assert state == "on_time"

    state, _, _ = review_status(
        review_due_at=NOW, confirmed_at=NOW - timedelta(hours=2), now=NOW
    )
    assert state == "done"
