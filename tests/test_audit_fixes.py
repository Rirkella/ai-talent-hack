"""Регрессии на дефекты, найденные внешним аудитом 05.09.2026.

Каждый тест воспроизводит ровно ту последовательность, которой аудит
показал ошибку, и читает результат **в новой сессии базы**: несколько из
этих дефектов ответ HTTP показывал правильным, а сохранённое состояние
оказывалось прежним.

* A01 — правка балла по критерию не доезжала до базы.
* A03 — повторный запуск ревью отменял штраф за просрочку.
* A04 — студент видел предварительный балл до подтверждения.
* A05 — даты уезжали на три часа: ISO без часового пояса.
* A12 — чужой ревьюер мог запускать проверку и менять вердикт.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.clock import now as now_utc
from app.models import Assignment, Role, Submission, SubmissionStatus, User

RUBRIC = {
    "track": "product_fraud",
    "approved": True,
    "criteria": [
        {"id": "c1", "name": "Первый", "max_score": 5, "weight": 1,
         "requirements": "Требование", "indicators": []},
        {"id": "c2", "name": "Второй", "max_score": 5, "weight": 1,
         "requirements": "Требование", "indicators": []},
    ],
}


def _review(scores: tuple[float, float]) -> dict:
    """Результат проверки в том виде, в каком его пишет пайплайн."""
    return {
        "submission_id": "ok",
        "criteria": [
            {"criterion_id": "c1", "criterion_name": "Первый", "max_score": 5.0,
             "score": scores[0], "weight": 1.0, "ai_score": scores[0],
             "verdict": "разбор", "evidence": []},
            {"criterion_id": "c2", "criterion_name": "Второй", "max_score": 5.0,
             "score": scores[1], "weight": 1.0, "ai_score": scores[1],
             "verdict": "разбор", "evidence": []},
        ],
        "formal": [],
        "penalties": [],
        "preliminary_score": sum(scores),
        "max_score": 10.0,
        "student_feedback": "разбор работы",
        "reviewer_summary": "сводка",
        "trace": [],
        "model": "test",
    }


@pytest.fixture
def stand(isolated_client, tmp_path):
    """Работа с готовой проверкой, сданная вовремя, назначенная rev-1."""
    maker = isolated_client.maker
    work_file = tmp_path / "работа.md"
    work_file.write_text("# Решение\n\nТекст.\n", encoding="utf-8")
    now = now_utc()

    async def prepare():
        async with maker() as s:
            s.add(User(id="coord-1", name="Методист", role=Role.COORDINATOR))
            s.add(User(id="rev-1", name="Свой", role=Role.REVIEWER))
            s.add(User(id="rev-2", name="Чужой", role=Role.REVIEWER))
            s.add(User(id="stu-1", name="Студент", role=Role.STUDENT))
            s.add(Assignment(
                id="asg", title="ДЗ", track="product_fraud",
                rubric=RUBRIC, rubric_approved=True,
                due_at=now + timedelta(hours=2),
                hard_due_at=now + timedelta(hours=26),
                review_due_at=now + timedelta(days=7),
            ))
            s.add(Submission(
                id="ok", assignment_id="asg", student_id="stu-1", reviewer_id="rev-1",
                file_path=str(work_file), file_name="работа.md", track="product_fraud",
                status=SubmissionStatus.AI_REVIEWED, submitted_at=now,
                review=_review((4.0, 3.5)),
            ))
            await s.commit()

    asyncio.run(prepare())
    return isolated_client


def _confirm(client, body, user="rev-1"):
    return client.post(
        "/api/submissions/ok/confirm", json=body, headers={"X-User-Id": user}
    )


def _stored(client) -> Submission:
    """Читает работу заново — не из ответа HTTP, а из базы."""

    async def read():
        async with client.maker() as s:
            return await s.get(Submission, "ok")

    return asyncio.run(read())


# ── A01: ручные оценки критериев ──────────────────────────────────────────────


def test_manual_criterion_score_survives_a_new_session(stand) -> None:
    """Ответ приходил с новым итогом, а в базе оставался прежний балл.

    `dict(s.review)` копирует только верхний уровень: словари критериев
    остаются общими с загруженным значением. Правка на месте меняла и
    «прежнее» состояние сессии, SQLAlchemy не видел разницы и не включал
    колонку в UPDATE. Ревьюер поднимал балл с 0,5 до 1, ответ показывал
    новый итог, а следующий GET — старый.
    """
    r = _confirm(stand, {
        "final_score": 8.0, "feedback": "итог",
        "criteria_scores": {"c1": 5.0, "c2": 3.5},
    })
    assert r.status_code == 200, r.text
    assert r.json()["final_score"] == 8.5

    stored = _stored(stand)
    scores = {c["criterion_id"]: c["score"] for c in stored.review["criteria"]}
    assert scores["c1"] == 5.0, "правка балла по критерию не сохранилась"
    assert stored.final_score == 8.5


def test_shallow_copy_of_a_json_column_loses_nested_edits(isolated_db) -> None:
    """Причина A01 в чистом виде, без HTTP.

    SQLAlchemy не следит за изменениями внутри JSON-колонки и на флаше
    сравнивает новое значение с загруженным. `dict(...)` копирует только
    верхний уровень, поэтому правка вложенного словаря меняет **и** ту
    структуру, с которой идёт сравнение: разницы нет, колонка не попадает
    в UPDATE, изменение теряется без единой ошибки.

    Тест написан отдельно от подтверждения оценки намеренно: ловушка не в
    том обработчике, а в способе работы с JSON-колонкой, и повторить её
    можно в любом другом месте.
    """
    from copy import deepcopy

    async def roundtrip(copier) -> float:  # noqa: ANN001
        async with isolated_db() as s:
            s.add(Submission(
                id="probe", assignment_id="a", student_id="u",
                file_path="/tmp/a.md", file_name="a.md", track="t",
                submitted_at=now_utc(),
                review={"criteria": [{"criterion_id": "c1", "score": 0.5}]},
            ))
            await s.commit()
        async with isolated_db() as s:
            sub = await s.get(Submission, "probe")
            review = copier(sub.review)
            review["criteria"][0]["score"] = 1.0
            sub.review = review
            await s.commit()
        async with isolated_db() as s:
            sub = await s.get(Submission, "probe")
            score = sub.review["criteria"][0]["score"]
            await s.delete(sub)
            await s.commit()
            return score

    assert asyncio.run(roundtrip(dict)) == 0.5, (
        "поверхностная копия внезапно начала сохранять правку — "
        "проверьте, не появился ли MutableDict, и упростите обработчик"
    )
    assert asyncio.run(roundtrip(deepcopy)) == 1.0


def test_model_score_is_kept_next_to_the_human_one(stand) -> None:
    """Метрика согласия сравнивает решение человека с оценкой автоматики.

    Если правка затирает балл модели, сравнивать становится не с чем.
    """
    _confirm(stand, {
        "final_score": 8.5, "feedback": "итог",
        "criteria_scores": {"c1": 5.0, "c2": 3.5},
    })
    stored = _stored(stand)
    first = stored.review["criteria"][0]
    assert first["score"] == 5.0
    assert first["ai_score"] == 4.0, "исходная оценка модели затёрта"


def test_confirming_twice_does_not_roll_the_score_back(stand) -> None:
    """Повторное подтверждение без правок обязано оставить тот же итог."""
    _confirm(stand, {
        "final_score": 8.5, "feedback": "итог",
        "criteria_scores": {"c1": 5.0, "c2": 3.5},
    })
    again = _confirm(stand, {"final_score": 8.5, "feedback": "итог"})
    assert again.status_code == 200, again.text
    assert again.json()["final_score"] == 8.5, "итог откатился к оценке модели"
    assert _stored(stand).final_score == 8.5


# ── A03: штраф за просрочку ───────────────────────────────────────────────────


def _make_late(client) -> None:
    """Двигает срок так, что работа оказывается досдачей со штрафом."""

    async def shift():
        async with client.maker() as s:
            work = await s.get(Submission, "ok")
            assignment = await s.get(Assignment, "asg")
            assignment.due_at = work.submitted_at - timedelta(hours=1)
            assignment.hard_due_at = work.submitted_at + timedelta(hours=23)
            await s.commit()

    asyncio.run(shift())


def test_penalty_is_applied_when_the_review_is_saved(stand) -> None:
    """Штраф не должен ждать смены состояния в планировщике.

    Планировщик применял его только в момент **перехода**. Повторный запуск
    ревью перезаписывал результат заново посчитанным баллом без штрафа, и на
    следующем тике планировщик видел, что состояние не изменилось, и не
    трогал ничего. Работа с 8 баллами публиковалась как 8 вместо 7.
    """
    from app.agent.schemas import ReviewResult
    from app.queue import save_review_result

    _make_late(stand)

    async def rerun():
        async with stand.maker() as s:
            work = await s.get(Submission, "ok")
            assignment = await s.get(Assignment, "asg")
            # Ровно то, что делает воркер очереди после ответа модели:
            # результат без штрафа, потому что пайплайн о сроках не знает.
            fresh = ReviewResult.model_validate(_review((4.0, 3.5)))
            await save_review_result(s, work, fresh, assignment)
            await s.commit()

    asyncio.run(rerun())

    stored = _stored(stand)
    assert stored.review["preliminary_score"] == 6.5, "штраф не применён при сохранении"
    codes = [p["code"] for p in stored.review["penalties"]]
    assert codes == ["deadline"]


def test_penalty_is_not_subtracted_twice(stand) -> None:
    """Правило применяется трижды за жизнь работы — оно обязано быть идемпотентным."""
    from app.notify.scheduler import apply_deadline_rules

    _make_late(stand)

    async def apply_three_times():
        async with stand.maker() as s:
            work = await s.get(Submission, "ok")
            assignment = await s.get(Assignment, "asg")
            for _ in range(3):
                apply_deadline_rules(work, assignment)
            await s.commit()

    asyncio.run(apply_three_times())
    assert _stored(stand).review["preliminary_score"] == 6.5


def test_confirm_applies_the_penalty_without_criteria_scores(stand) -> None:
    """Отсутствие `criteria_scores` не должно обходить штраф.

    Итог брался прямо из тела запроса, если баллы по критериям не переданы:
    клиент присылал 7,5, сервер публиковал 7,5.
    """
    _make_late(stand)
    r = _confirm(stand, {"final_score": 7.5, "feedback": "итог"})
    assert r.status_code == 200, r.text
    assert r.json()["final_score"] == 6.5, "штраф обойдён запросом без criteria_scores"
    assert _stored(stand).final_score == 6.5


def test_on_time_work_is_not_penalised_later(stand) -> None:
    """Сданная вовремя работа не штрафуется из-за того, что сейчас уже поздно."""
    from app.notify.scheduler import apply_deadline_rules

    async def move_deadline_into_the_past():
        async with stand.maker() as s:
            work = await s.get(Submission, "ok")
            assignment = await s.get(Assignment, "asg")
            # Срок прошёл, но работа сдана до него.
            assignment.due_at = work.submitted_at + timedelta(minutes=1)
            assignment.hard_due_at = work.submitted_at + timedelta(minutes=2)
            apply_deadline_rules(work, assignment)
            await s.commit()

    asyncio.run(move_deadline_into_the_past())
    stored = _stored(stand)
    assert stored.late_penalty == 0.0
    assert stored.review["preliminary_score"] == 7.5


# ── A04: студент не видит непубликованное ─────────────────────────────────────


STUDENT = {"X-User-Id": "stu-1"}


def test_student_does_not_see_the_preliminary_score(stand) -> None:
    """Карточка писала «на проверке», а рядом стояло 16,5 из 20."""
    rows = stand.get("/api/submissions", headers=STUDENT).json()
    assert rows and "preliminary_score" not in rows[0], "предварительный балл ушёл студенту"

    detail = stand.get("/api/submissions/ok", headers=STUDENT).json()
    assert "preliminary_score" not in detail
    assert detail["final_score"] is None


def test_student_profile_hides_unconfirmed_scores(stand) -> None:
    profile = stand.get("/api/students/stu-1/profile", headers=STUDENT).json()
    assert profile["timeline"][0]["score"] is None, "неподтверждённый балл в динамике"
    assert profile["summary"]["mean_score"] is None
    assert profile["summary"]["best"] is None


def test_staff_still_sees_the_draft(stand) -> None:
    """Сотрудникам предварительная оценка нужна — это их рабочий материал."""
    rows = stand.get("/api/submissions", headers={"X-User-Id": "coord-1"}).json()
    assert rows[0]["preliminary_score"] == 7.5

    profile = stand.get(
        "/api/students/stu-1/profile", headers={"X-User-Id": "coord-1"}
    ).json()
    assert profile["timeline"][0]["score"] == 7.5


def test_student_sees_the_score_after_confirmation(stand) -> None:
    _confirm(stand, {
        "final_score": 8.5, "feedback": "итоговый комментарий",
        "criteria_scores": {"c1": 5.0, "c2": 3.5},
    })
    detail = stand.get("/api/submissions/ok", headers=STUDENT).json()
    assert detail["final_score"] == 8.5
    assert detail["published"] is True
    assert detail["feedback"] == "итоговый комментарий"


# ── A05: время ────────────────────────────────────────────────────────────────


def test_dates_carry_an_explicit_timezone(stand) -> None:
    """ISO без пояса браузер читает как местное время — расхождение в три часа."""
    from datetime import datetime

    rows = stand.get("/api/submissions", headers={"X-User-Id": "coord-1"}).json()
    submitted = rows[0]["submitted_at"]
    assert datetime.fromisoformat(submitted).tzinfo is not None, (
        f"дата без часового пояса: {submitted}"
    )

    assignment = stand.get("/api/assignments/asg", headers={"X-User-Id": "coord-1"}).json()
    for field in ("due_at", "hard_due_at", "review_due_at"):
        value = assignment[field]
        assert datetime.fromisoformat(value).tzinfo is not None, f"{field}: {value}"


def test_the_moment_survives_a_round_trip(stand) -> None:
    """PUT сроков и последующий GET обязаны означать один и тот же момент."""
    from datetime import datetime, timezone

    moment = datetime(2026, 9, 7, 11, 30, 31, tzinfo=timezone.utc)
    r = stand.put(
        "/api/assignments/asg/deadlines",
        json={"due_at": moment.isoformat()},
        headers={"X-User-Id": "coord-1"},
    )
    assert r.status_code == 200, r.text

    got = stand.get("/api/assignments/asg", headers={"X-User-Id": "coord-1"}).json()
    assert datetime.fromisoformat(got["due_at"]) == moment


# ── A12: права ревьюера ───────────────────────────────────────────────────────


OTHER = {"X-User-Id": "rev-2"}


def test_other_reviewer_cannot_start_a_review(stand) -> None:
    """Смотреть чужую работу запрещал 403, а запускать по ней модель — нет."""
    assert stand.post("/api/submissions/ok/review", headers=OTHER).status_code == 403


def test_other_reviewer_cannot_set_the_ai_verdict(stand) -> None:
    r = stand.post(
        "/api/submissions/ok/ai-verdict",
        json={"verdict": "rejected", "comment": ""},
        headers=OTHER,
    )
    assert r.status_code == 403


def test_assigned_reviewer_and_coordinator_keep_their_rights(stand) -> None:
    assert stand.post(
        "/api/submissions/ok/ai-verdict",
        json={"verdict": "confirmed", "comment": "проверил"},
        headers={"X-User-Id": "rev-1"},
    ).status_code == 200
    assert stand.post(
        "/api/submissions/ok/ai-verdict",
        json={"verdict": "rejected", "comment": "не согласен"},
        headers={"X-User-Id": "coord-1"},
    ).status_code == 200


# ── A09: выгрузка как ведомость ───────────────────────────────────────────────


def _export_rows(client, **params) -> list[dict]:
    r = client.get(
        "/api/assignments/asg/export",
        params={"fmt": "json", **params},
        headers={"X-User-Id": "coord-1"},
    )
    assert r.status_code == 200, r.text
    import json as _json

    return _json.loads(r.text)["rows"]


def test_export_lists_only_real_current_works(stand) -> None:
    """Три файла превращались в пять строк, а после пересдачи — в шесть.

    В выгрузку шло всё подряд: демонстрационная нагрузка без файлов и
    прежние версии пересданных работ. По такой таблице оценки в журнал
    не выставить.
    """

    async def add_noise():
        async with stand.maker() as s:
            s.add(Submission(
                id="empty", assignment_id="asg", student_id="stu-1",
                file_path="", file_name="[прошлый поток] работа 1",
                track="product_fraud", submitted_at=now_utc(),
            ))
            old = await s.get(Submission, "ok")
            old.superseded = True
            s.add(Submission(
                id="v2", assignment_id="asg", student_id="stu-1",
                file_path=old.file_path, file_name="работа.md", version=2,
                previous_id="ok", track="product_fraud", submitted_at=now_utc(),
            ))
            await s.commit()

    asyncio.run(add_noise())

    rows = _export_rows(stand)
    assert len(rows) == 1, [r["Работа"] for r in rows]
    assert rows[0]["Версия"] == 2

    # История доступна отдельным режимом, а не подмешана в ведомость.
    full = _export_rows(stand, history=True)
    assert len(full) == 3


def test_export_carries_the_published_result(stand) -> None:
    """Ведомости нужны итог, комментарий, версия, максимум и идентификатор."""
    _confirm(stand, {
        "final_score": 8.5, "feedback": "итоговый комментарий",
        "criteria_scores": {"c1": 5.0, "c2": 3.5},
    })
    row = _export_rows(stand)[0]
    assert row["Итоговый балл"] == 8.5
    assert row["Комментарий студенту"] == "итоговый комментарий"
    assert row["Максимум"] == "10"
    assert row["Версия"] == 1
    assert row["Идентификатор работы"] == "ok"


def test_export_formats_all_work(stand) -> None:
    for fmt in ("csv", "xlsx", "json"):
        r = stand.get(
            "/api/assignments/asg/export",
            params={"fmt": fmt},
            headers={"X-User-Id": "coord-1"},
        )
        assert r.status_code == 200, f"{fmt}: {r.text[:200]}"
        assert r.content, f"{fmt}: пустой ответ"


# ── A10: оригинал файла из карточки ───────────────────────────────────────────


def test_reviewer_can_download_the_original(stand) -> None:
    """Модель не смотрит изображения — значит, ревьюер должен смотреть сам."""
    r = stand.get("/api/submissions/ok/file", headers={"X-User-Id": "rev-1"})
    assert r.status_code == 200
    assert "Решение" in r.text


def test_foreign_reviewer_cannot_download_the_original(stand) -> None:
    """Тем же правилом, что и карточка: чужую работу — нельзя."""
    assert stand.get("/api/submissions/ok/file", headers=OTHER).status_code == 403


def test_student_downloads_only_their_own_work(stand) -> None:
    assert stand.get("/api/submissions/ok/file", headers=STUDENT).status_code == 200

    async def add_foreign():
        async with stand.maker() as s:
            s.add(User(id="stu-2", name="Чужой студент", role=Role.STUDENT))
            await s.commit()

    asyncio.run(add_foreign())
    assert stand.get(
        "/api/submissions/ok/file", headers={"X-User-Id": "stu-2"}
    ).status_code == 403


def test_missing_file_is_reported_not_crashed(stand) -> None:
    async def drop_file():
        async with stand.maker() as s:
            work = await s.get(Submission, "ok")
            work.file_path = ""
            await s.commit()

    asyncio.run(drop_file())
    r = stand.get("/api/submissions/ok/file", headers={"X-User-Id": "rev-1"})
    assert r.status_code == 404
    assert "файл" in r.json()["detail"].lower()


# ── A11: оценки по прежней рубрике ────────────────────────────────────────────


def test_review_by_an_older_rubric_is_marked_stale(stand) -> None:
    """После правки критериев прежние баллы нельзя принимать за актуальные."""
    from app.rubric.models import Rubric

    async def stamp_and_change():
        async with stand.maker() as s:
            assignment = await s.get(Assignment, "asg")
            work = await s.get(Submission, "ok")
            review = dict(work.review)
            review["rubric_fingerprint"] = Rubric.model_validate(
                assignment.rubric
            ).fingerprint()
            work.review = review
            await s.commit()

    asyncio.run(stamp_and_change())

    rows = stand.get("/api/submissions", headers={"X-User-Id": "coord-1"}).json()
    assert rows[0]["rubric_stale"] is False, "актуальная проверка помечена устаревшей"

    # Методист меняет вес критерия — рубрика стала другой.
    r = stand.put(
        "/api/assignments/asg/rubric",
        json={"rubric": {
            "track": "product_fraud",
            "criteria": [
                {"id": "c1", "name": "Первый", "max_score": 5, "weight": 2,
                 "requirements": "Требование", "indicators": []},
                {"id": "c2", "name": "Второй", "max_score": 5, "weight": 1,
                 "requirements": "Требование", "indicators": []},
            ],
        }},
        headers={"X-User-Id": "coord-1"},
    )
    assert r.status_code == 200, r.text

    rows = stand.get("/api/submissions", headers={"X-User-Id": "coord-1"}).json()
    assert rows[0]["rubric_stale"] is True, "оценка по прежней рубрике не помечена"


def test_bulk_review_reports_works_needing_a_rerun(stand) -> None:
    """Массовый запуск обязан сказать, каким работам нужна перепроверка."""
    from app.models import SubmissionStatus as St

    async def mark_reviewed():
        async with stand.maker() as s:
            work = await s.get(Submission, "ok")
            work.status = St.AI_REVIEWED
            review = dict(work.review)
            review["rubric_fingerprint"] = "устаревший"
            work.review = review
            await s.commit()

    asyncio.run(mark_reviewed())

    r = stand.post("/api/assignments/asg/review-all", headers={"X-User-Id": "coord-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stale"] == ["работа.md"]
    assert "прежним критериям" in body["note"]


def test_scheduler_pass_after_confirmation_keeps_the_draft_score(stand) -> None:
    """Предварительный балл — это оценка автоматики, и она не должна плыть.

    После правки ревьюером `criteria[].score` содержит решение человека.
    Если пересчёт срока возьмёт сумму по этим числам, предварительный балл
    станет равен итоговому, и метрика согласия «как часто человек правит
    балл» начнёт сравнивать число само с собой.
    """
    from app.notify.scheduler import apply_deadline_rules

    _confirm(stand, {
        "final_score": 9.5, "feedback": "итог",
        "criteria_scores": {"c1": 5.0, "c2": 4.5},
    })

    async def tick():
        async with stand.maker() as s:
            work = await s.get(Submission, "ok")
            assignment = await s.get(Assignment, "asg")
            apply_deadline_rules(work, assignment)
            await s.commit()

    asyncio.run(tick())

    stored = _stored(stand)
    assert stored.review["preliminary_score"] == 7.5, "предварительный балл подменён оценкой человека"
    assert stored.final_score == 9.5
    assert stored.score_edited is True


def test_rerunning_a_review_keeps_the_published_verdict(stand) -> None:
    """Повторная проверка не отменяет решение человека.

    Статус сбрасывался в «проверено автоматикой» у работы, чей балл студент
    уже видит: карточка выглядела непроверенной при опубликованной оценке.
    """
    from app.agent.schemas import ReviewResult
    from app.models import SubmissionStatus as St
    from app.queue import save_review_result

    _confirm(stand, {
        "final_score": 8.5, "feedback": "итог",
        "criteria_scores": {"c1": 5.0, "c2": 3.5},
    })

    async def rerun():
        async with stand.maker() as s:
            work = await s.get(Submission, "ok")
            assignment = await s.get(Assignment, "asg")
            fresh = ReviewResult.model_validate(_review((4.0, 3.0)))
            await save_review_result(s, work, fresh, assignment)
            await s.commit()

    asyncio.run(rerun())

    stored = _stored(stand)
    assert stored.status == St.CONFIRMED, "подтверждённая работа снова стала непроверенной"
    assert stored.final_score == 8.5, "опубликованный балл затёрт повторной проверкой"
    assert stored.review["preliminary_score"] == 7.0, "разбор автоматики не обновился"
