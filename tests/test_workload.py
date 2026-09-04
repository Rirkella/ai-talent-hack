"""Тесты распределения и схожести работ.

Требование кейса — распределять с учётом объёма курса и нагрузки каждого
ревьюера. Проверяется именно это: при перекошенной нагрузке максимум по
ревьюерам обязан снизиться, а не просто «работы должны разойтись».
"""

from __future__ import annotations

from datetime import timedelta

from app.clock import now
from app.detect.similarity import SUSPICIOUS, compare_texts
from app.workload.allocator import (
    AllocationWeights,
    ReviewerLoad,
    WorkItem,
    allocate,
    balance_score,
)


def test_skewed_load_is_evened_out() -> None:
    """Перекошенная нагрузка обязана выровняться."""
    reviewers = [
        ReviewerLoad("r1", "А", capacity=6, current_load=5, competencies=["product_fraud"]),
        ReviewerLoad("r2", "Б", capacity=6, current_load=0, competencies=["product_fraud"]),
    ]
    works = [WorkItem(f"w{i}", "product_fraud") for i in range(4)]

    result = allocate(works, reviewers)
    assert result.assigned_count == 4

    # Загруженный ревьюер получает меньше свободного — это и есть учёт нагрузки.
    got_r1 = sum(1 for a in result.allocations if a.reviewer_id == "r1")
    got_r2 = sum(1 for a in result.allocations if a.reviewer_id == "r2")
    assert got_r2 > got_r1, f"свободный ревьюер должен получить больше: r1={got_r1}, r2={got_r2}"


def test_balance_improves_when_capacity_allows() -> None:
    """Ровность загрузки после распределения не должна ухудшаться."""
    reviewers = [
        ReviewerLoad("r1", "А", capacity=10, current_load=8, competencies=["t"]),
        ReviewerLoad("r2", "Б", capacity=10, current_load=1, competencies=["t"]),
        ReviewerLoad("r3", "В", capacity=10, current_load=0, competencies=["t"]),
    ]
    works = [WorkItem(f"w{i}", "t") for i in range(9)]
    result = allocate(works, reviewers)

    before = balance_score(result.load_before, reviewers)
    after = balance_score(result.load_after, reviewers)
    assert after >= before, f"ровность ухудшилась: {before} → {after}"


def test_competence_is_respected_when_possible() -> None:
    """Работа уходит компетентному ревьюеру, если такой есть."""
    reviewers = [
        ReviewerLoad("qa", "QA", capacity=5, competencies=["tech_QA"]),
        ReviewerLoad("pf", "PF", capacity=5, competencies=["product_fraud"]),
    ]
    result = allocate([WorkItem("w1", "product_fraud")], reviewers)
    assert result.allocations[0].reviewer_id == "pf"


def test_incompetent_reviewer_is_last_resort_not_a_ban() -> None:
    """При отсутствии компетентных работа всё равно распределяется.

    Полный запрет сделал бы задачу нерешаемой, а работы обязаны быть
    распределены — несоответствие уходит в стоимость и в причины.
    """
    reviewers = [ReviewerLoad("qa", "QA", capacity=5, competencies=["tech_QA"])]
    result = allocate([WorkItem("w1", "product_fraud")], reviewers)
    assert result.allocations[0].reviewer_id == "qa"
    assert any("компетенц" in r for r in result.allocations[0].reasons)


def test_repeat_review_stays_with_same_reviewer() -> None:
    """При повторном ревью выгодно сохранить прежнего ревьюера: контекст в голове."""
    reviewers = [
        ReviewerLoad("r1", "А", capacity=5, current_load=2, competencies=["t"]),
        ReviewerLoad("r2", "Б", capacity=5, current_load=2, competencies=["t"]),
    ]
    work = WorkItem("w1", "t", previous_reviewer_id="r1", is_repeat=True)
    result = allocate([work], reviewers)
    assert result.allocations[0].reviewer_id == "r1"


def test_capacity_shortage_is_reported_not_hidden() -> None:
    """Нехватка ёмкости попадает в notes, а работы всё равно распределяются."""
    reviewers = [ReviewerLoad("r1", "А", capacity=2, current_load=2, competencies=["t"])]
    works = [WorkItem(f"w{i}", "t") for i in range(4)]
    result = allocate(works, reviewers)
    assert result.notes, "перегруз должен быть явно отмечен"
    assert result.assigned_count == 4


def test_inactive_reviewer_gets_nothing() -> None:
    reviewers = [
        ReviewerLoad("r1", "А", capacity=5, competencies=["t"], active=False),
        ReviewerLoad("r2", "Б", capacity=5, competencies=["t"]),
    ]
    result = allocate([WorkItem("w1", "t")], reviewers)
    assert result.allocations[0].reviewer_id == "r2"


def test_urgent_work_avoids_loaded_reviewer() -> None:
    reviewers = [
        ReviewerLoad("busy", "Загружен", capacity=5, current_load=4, competencies=["t"]),
        ReviewerLoad("free", "Свободен", capacity=5, current_load=0, competencies=["t"]),
    ]
    urgent = WorkItem("w1", "t", due_at=now() + timedelta(hours=2))
    result = allocate([urgent], reviewers)
    assert result.allocations[0].reviewer_id == "free"


def test_naive_due_date_does_not_crash() -> None:
    """SQLite отдаёт даты без часового пояса — распределение обязано это пережить.

    Без приведения к UTC вычитание падало с TypeError, и весь эндпоинт
    распределения отвечал 500 после первого перезапуска приложения.
    """
    naive = (now() + timedelta(hours=2)).replace(tzinfo=None)
    result = allocate(
        [WorkItem("w1", "t", due_at=naive)],
        [ReviewerLoad("r1", "А", capacity=5, competencies=["t"])],
    )
    assert result.allocations[0].reviewer_id == "r1"


def test_no_reviewers_degrades_gracefully() -> None:
    result = allocate([WorkItem("w1", "t")], [])
    assert result.allocations[0].reviewer_id is None
    assert result.unassigned == ["w1"]
    assert result.notes


def test_weights_toggles_change_behaviour() -> None:
    """Тумблер компетенции действительно её отключает."""
    reviewers = [
        ReviewerLoad("qa", "QA", capacity=5, current_load=0, competencies=["tech_QA"]),
        ReviewerLoad("pf", "PF", capacity=5, current_load=4, competencies=["product_fraud"]),
    ]
    work = [WorkItem("w1", "product_fraud")]

    with_competence = allocate(work, reviewers)
    assert with_competence.allocations[0].reviewer_id == "pf"

    without = allocate(work, reviewers, weights=AllocationWeights(competence_on=False))
    # Без учёта компетенции решает только загрузка.
    assert without.allocations[0].reviewer_id == "qa"


# ── схожесть работ ────────────────────────────────────────────────────────────


def test_identical_texts_are_flagged() -> None:
    text = (
        "Продукт — маркетплейс запчастей. Риск мошенничества с картами оценён как "
        "средний по вероятности и высокий по влиянию на выручку и репутацию бренда."
    )
    report = compare_texts({"a": text, "b": text})
    assert report.pairs[0].score >= SUSPICIOUS
    assert report.pairs[0].is_suspicious


def test_different_texts_are_not_flagged() -> None:
    report = compare_texts({
        "a": "Продукт — маркетплейс запчастей для автомобилей с доставкой по России.",
        "b": "Сервис бронирования столиков в ресторанах с онлайн-оплатой и отзывами.",
    })
    assert not report.pairs[0].is_suspicious


def test_shared_fragments_are_shown_for_suspicious_pairs() -> None:
    """Ревьюер должен видеть, что именно совпало, а не только число."""
    common = (
        "Риск мошенничества с банковскими картами при оплате заказа оценивается "
        "как средний по вероятности и высокий по уровню влияния на выручку."
    )
    report = compare_texts({
        "a": f"Начало первой работы. {common} Конец первой работы.",
        "b": f"Другое начало. {common} Другой конец.",
    })
    pair = report.pairs[0]
    assert pair.is_suspicious
    assert pair.shared_fragments, "совпавшие фрагменты обязаны быть показаны"


def test_single_work_reports_note_not_error() -> None:
    report = compare_texts({"a": "Единственная работа в потоке."})
    assert report.pairs == []
    assert report.notes
