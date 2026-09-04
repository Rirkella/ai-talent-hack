"""Распределение работ между ревьюерами.

Обязательная функция кейса: распределять с учётом **объёма курса и нагрузки
каждого ревьюера**. Задача сводится к назначению на двудольном графе и
решается венгерским алгоритмом (`scipy.optimize.linear_sum_assignment`) —
это точный оптимум, а не эвристика.

Почему не «раздать поровну»: у ревьюеров разные компетенции, разная
пропускная способность и уже разная текущая загрузка. Равное деление
даёт формально ровные числа и фактический перекос.

Стоимость назначения складывается из компонентов с настраиваемыми весами —
теми же тумблерами, что и в формуле балла. Методист может пересобрать
распределение под свою политику, не трогая код.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from scipy.optimize import linear_sum_assignment

from app.clock import as_utc, now as now_utc

# Стоимость назначения, которое запрещено: компетенция не совпадает вовсе
# или у ревьюера нет свободной ёмкости. Большое, но конечное — иначе при
# нехватке ревьюеров задача становится нерешаемой, а работы должны быть
# распределены в любом случае.
FORBIDDEN = 1e6


@dataclass
class ReviewerLoad:
    """Ревьюер с точки зрения распределения."""

    id: str
    name: str
    capacity: int = 10
    current_load: int = 0
    competencies: list[str] = field(default_factory=list)
    active: bool = True

    @property
    def free_slots(self) -> int:
        return max(0, self.capacity - self.current_load)

    @property
    def utilization(self) -> float:
        return self.current_load / self.capacity if self.capacity else 1.0


@dataclass
class WorkItem:
    """Работа, ожидающая назначения."""

    id: str
    track: str
    # Трудоёмкость: длинная работа стоит ревьюеру дороже короткой.
    effort: float = 1.0
    due_at: datetime | None = None
    # Ревьюер, смотревший предыдущую версию. При повторном ревью его
    # сохранение экономит время: контекст уже в голове.
    previous_reviewer_id: str | None = None
    is_repeat: bool = False


@dataclass
class AllocationWeights:
    """Веса компонентов стоимости. Каждый — с тумблером."""

    competence: float = 100.0
    competence_on: bool = True

    load_balance: float = 30.0
    load_balance_on: bool = True

    urgency: float = 15.0
    urgency_on: bool = True

    # Отрицательный вклад: сохранение ревьюера при повторном ревью выгодно.
    repeat_continuity: float = 25.0
    repeat_continuity_on: bool = True

    effort: float = 10.0
    effort_on: bool = True


@dataclass
class Allocation:
    work_id: str
    reviewer_id: str | None
    cost: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class AllocationResult:
    allocations: list[Allocation]
    # Загрузка до и после — для графика «до/после».
    load_before: dict[str, int]
    load_after: dict[str, int]
    unassigned: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def assigned_count(self) -> int:
        return sum(1 for a in self.allocations if a.reviewer_id is not None)

    @property
    def max_load_before(self) -> int:
        return max(self.load_before.values(), default=0)

    @property
    def max_load_after(self) -> int:
        return max(self.load_after.values(), default=0)
def _cost(
    work: WorkItem,
    reviewer: ReviewerLoad,
    projected_load: int,
    weights: AllocationWeights,
    now: datetime,
) -> tuple[float, list[str]]:
    """Стоимость назначения одной работы одному ревьюеру."""
    reasons: list[str] = []

    if not reviewer.active:
        return FORBIDDEN, ["ревьюер неактивен"]

    cost = 0.0

    if weights.competence_on and reviewer.competencies:
        if work.track not in reviewer.competencies:
            # Не запрет: при нехватке компетентных ревьюеров работу всё равно
            # надо кому-то отдать, но такой вариант выбирается последним.
            cost += weights.competence
            reasons.append(f"нет компетенции по направлению «{work.track}»")
        else:
            reasons.append("компетенция совпадает")

    if weights.load_balance_on:
        # Квадрат загрузки: выравнивание сильнее штрафует перекос, чем
        # линейная функция, и не даёт свалить всё на одного свободного.
        utilization = projected_load / reviewer.capacity if reviewer.capacity else 2.0
        cost += weights.load_balance * utilization**2
        if projected_load >= reviewer.capacity:
            cost += weights.load_balance
            reasons.append(f"ёмкость исчерпана ({projected_load}/{reviewer.capacity})")

    if weights.effort_on:
        cost += weights.effort * work.effort * (projected_load + 1) / max(reviewer.capacity, 1)

    if weights.urgency_on and work.due_at:
        # as_utc обязателен: SQLite отдаёт дату без часового пояса, и без
        # приведения вычитание падает с TypeError уже на первом запуске
        # с сохранёнными сроками.
        hours_left = (as_utc(work.due_at) - now).total_seconds() / 3600
        if hours_left < 24:
            # Срочную работу нельзя ставить в конец длинной очереди.
            cost += weights.urgency * (projected_load / max(reviewer.capacity, 1))
            reasons.append("срок горит")

    if weights.repeat_continuity_on and work.is_repeat and work.previous_reviewer_id:
        if reviewer.id == work.previous_reviewer_id:
            cost -= weights.repeat_continuity
            reasons.append("повторное ревью у того же ревьюера")

    return cost, reasons


def allocate(
    works: list[WorkItem],
    reviewers: list[ReviewerLoad],
    *,
    weights: AllocationWeights | None = None,
    now: datetime | None = None,
) -> AllocationResult:
    """Распределяет работы. Возвращает назначения и загрузку до/после.

    Венгерский алгоритм работает на квадратной матрице «работа × слот».
    Каждый ревьюер раскрывается в столько слотов, сколько работ он может
    взять; так одна задача о назначении покрывает и множественность, и
    ограничение по ёмкости.
    """
    weights = weights or AllocationWeights()
    now = as_utc(now) or now_utc()

    load_before = {r.id: r.current_load for r in reviewers}
    if not works:
        return AllocationResult([], load_before, dict(load_before), notes=["нет работ"])
    if not reviewers:
        return AllocationResult(
            [Allocation(w.id, None, FORBIDDEN, ["нет ревьюеров"]) for w in works],
            load_before, dict(load_before),
            unassigned=[w.id for w in works], notes=["нет доступных ревьюеров"],
        )

    active = [r for r in reviewers if r.active]
    notes: list[str] = []
    if not active:
        return AllocationResult(
            [Allocation(w.id, None, FORBIDDEN, ["все ревьюеры неактивны"]) for w in works],
            load_before, dict(load_before),
            unassigned=[w.id for w in works], notes=["все ревьюеры неактивны"],
        )

    # Слоты: по одному на каждую работу, которую ревьюер ещё может взять.
    # Если суммарной свободной ёмкости не хватает, слоты добавляются сверх
    # неё — распределить нужно всё, но перегруз попадёт в стоимость и в notes.
    slots: list[tuple[str, int]] = []  # (reviewer_id, порядковый номер слота)
    for r in active:
        for k in range(max(r.free_slots, 0)):
            slots.append((r.id, r.current_load + k))

    if len(slots) < len(works):
        deficit = len(works) - len(slots)
        notes.append(
            f"Свободной ёмкости не хватает на {deficit} работ(ы): "
            f"часть ревьюеров будет перегружена."
        )
        per_reviewer = deficit // len(active) + 1
        for r in active:
            for k in range(per_reviewer):
                slots.append((r.id, r.capacity + k))

    by_id = {r.id: r for r in active}
    n, m = len(works), len(slots)
    matrix = np.zeros((n, m), dtype=float)
    reasons_grid: list[list[list[str]]] = [[[] for _ in range(m)] for _ in range(n)]

    for i, work in enumerate(works):
        for j, (rid, projected) in enumerate(slots):
            cost, reasons = _cost(work, by_id[rid], projected, weights, now)
            matrix[i, j] = cost
            reasons_grid[i][j] = reasons

    rows, cols = linear_sum_assignment(matrix)

    allocations: list[Allocation] = []
    load_after = dict(load_before)
    assigned: set[int] = set()

    for i, j in zip(rows, cols, strict=True):
        rid = slots[j][0]
        allocations.append(
            Allocation(
                work_id=works[i].id,
                reviewer_id=rid,
                cost=round(float(matrix[i, j]), 2),
                reasons=reasons_grid[i][j],
            )
        )
        load_after[rid] = load_after.get(rid, 0) + 1
        assigned.add(i)

    unassigned = [w.id for i, w in enumerate(works) if i not in assigned]
    for wid in unassigned:
        allocations.append(Allocation(wid, None, FORBIDDEN, ["не хватило слотов"]))

    return AllocationResult(
        allocations=allocations,
        load_before=load_before,
        load_after=load_after,
        unassigned=unassigned,
        notes=notes,
    )


def balance_score(loads: dict[str, int], reviewers: list[ReviewerLoad]) -> float:
    """Насколько ровно распределена нагрузка: 1.0 — идеально ровно.

    Считается по разбросу утилизации, а не по абсолютным числам: ревьюер
    с ёмкостью 20 и пятью работами загружен меньше, чем ревьюер с ёмкостью 5
    и четырьмя, хотя абсолютное число у первого больше.
    """
    utils = [
        loads.get(r.id, 0) / r.capacity if r.capacity else 0.0
        for r in reviewers
        if r.active
    ]
    if len(utils) < 2:
        return 1.0
    spread = max(utils) - min(utils)
    return round(max(0.0, 1.0 - spread), 3)
