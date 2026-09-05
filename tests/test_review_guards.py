"""Отказ запустить проверку обязан называть настоящую причину.

Найдено на живом прогоне. В очереди ревьюера лежат строки без файла — это
нагрузка прошлого потока, заведённая ради демонстрации распределения: они
занимают ёмкость ревьюера, но содержимого у них нет. Нажатие «Запустить
проверку» на такой строке доходило до читателя файлов и возвращалось
ошибкой:

    Формат '' не поддерживается. Доступны: .c, .cpp, .cs, .docx, .go, …

Тридцать одно расширение в сообщении о работе, у которой просто нет файла.
Пользователь резонно решил, что сломана модель.

Здесь проверяется три вещи: отказ приходит до постановки в очередь, текст
отказа говорит про файл, а не про формат, и интерфейс получает признак
`has_file`, чтобы вообще не предлагать кнопку.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.clock import now as now_utc
from app.models import Assignment, Base, Role, Submission, User

RUBRIC = {
    "track": "product_fraud",
    "approved": True,
    "criteria": [
        {"id": "c1", "name": "Критерий", "max_score": 5, "weight": 1,
         "requirements": "Требование", "indicators": []}
    ],
}


@pytest.fixture
def client(tmp_path, isolated_client):
    """Готовое состояние поверх изолированной базы.

    База, каталог загрузок и очередь берутся из `isolated_client`: очередь
    ревью и планировщик ходят мимо подменённой зависимости, прямо в
    `session_scope`, и без общей подмены писали бы в рабочую базу.
    """
    maker = isolated_client.maker

    work = tmp_path / "работа.md"
    work.write_text("# Решение\n\nТекст работы.\n", encoding="utf-8")

    async def prepare():
        async with maker() as s:
            s.add(User(id="coord-1", name="Методист", role=Role.COORDINATOR))
            s.add(User(id="stu-1", name="Студент", role=Role.STUDENT))
            s.add(Assignment(id="asg", title="ДЗ", track="product_fraud",
                             rubric=RUBRIC, rubric_approved=True))
            s.add(Submission(id="ok", assignment_id="asg", student_id="stu-1",
                             file_path=str(work), file_name="работа.md",
                             track="product_fraud", submitted_at=now_utc()))
            s.add(Submission(id="nofile", assignment_id="asg", student_id="stu-1",
                             file_path="", file_name="[прошлый поток] работа 1",
                             track="product_fraud", submitted_at=now_utc()))
            s.add(Submission(id="lost", assignment_id="asg", student_id="stu-1",
                             file_path=str(tmp_path / "нет.docx"), file_name="нет.docx",
                             track="product_fraud", submitted_at=now_utc()))
            await s.commit()

    asyncio.run(prepare())
    yield isolated_client


COORD = {"X-User-Id": "coord-1"}


def test_review_of_fileless_work_is_refused_with_a_readable_reason(client) -> None:
    r = client.post("/api/submissions/nofile/review", headers=COORD)
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "файл" in detail.lower(), f"причина не названа: {detail}"
    assert "формат" not in detail.lower(), "отказ снова говорит про формат файла"


def test_missing_file_on_disk_is_reported_separately(client) -> None:
    """Файл записан, но его нет на диске — это другая беда, и текст другой."""
    r = client.post("/api/submissions/lost/review", headers=COORD)
    assert r.status_code == 400
    assert "не найден" in r.json()["detail"].lower()


def test_has_file_is_exposed_to_the_interface(client) -> None:
    """Интерфейс должен уметь погасить кнопку, не дожидаясь отказа."""
    rows = {s["id"]: s for s in client.get("/api/submissions", headers=COORD).json()}
    assert rows["ok"]["has_file"] is True
    assert rows["nofile"]["has_file"] is False
    assert rows["lost"]["has_file"] is False


def test_student_does_not_see_rows_without_a_file(client) -> None:
    """Строку без файла студент не сдавал — в его списке ей делать нечего.

    А вот работу, файл которой пропал с диска, скрывать нельзя: студент её
    действительно сдавал, и исчезновение сдачи из личного списка выглядело
    бы как потеря работы. Такая строка остаётся видимой, но проверку по ней
    запустить нельзя — это разные вещи.
    """
    rows = client.get("/api/submissions", headers={"X-User-Id": "stu-1"}).json()
    assert sorted(r["id"] for r in rows) == ["lost", "ok"]


def test_bulk_review_skips_fileless_and_says_so(client) -> None:
    r = client.post("/api/assignments/asg/review-all", headers=COORD)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] == 1, "в очередь ушли работы без файла"
    assert len(body["skipped"]) == 2
    assert "без файла" in body["note"]


def test_track_markers_are_not_mangled_by_encoding() -> None:
    """Маркеры направлений обязаны состоять из букв, которые бывают в работах.

    Найдено глазами: в наборе для системного дизайна вместо «систем» лежала
    строка «システ» — японская катакана, получившаяся из перекодировки. Такой
    маркер не совпадёт никогда, и работа по системному дизайну теряла самое
    очевидное ключевое слово. Признак порчи всегда один и тот же: символы
    письменности, которой в условиях курсов нет.
    """
    import re

    from app.api.routes import _TRACK_MARKERS

    foreign = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
    broken = [
        (track, marker)
        for track, markers in _TRACK_MARKERS.items()
        for marker in markers
        if foreign.search(marker)
    ]
    assert not broken, f"маркеры испорчены перекодировкой: {broken}"


def test_track_detection_recognises_system_design() -> None:
    """Проверка на настоящем признаке, а не только на отсутствии порчи."""
    from app.api.routes import _detect_track

    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = __import__("pathlib").Path(d) / "решение.md"
        p.write_text(
            "# Декомпозиция системы\n\nСистема разбита на сервисы. "
            "Кэш снимает нагрузку с базы.\n",
            encoding="utf-8",
        )
        assert _detect_track(p) == "system_design"


def test_reassign_rejects_unknown_reviewer(client) -> None:
    """Перенос на несуществующего ревьюера терял работу.

    Идентификатор не проверялся: опечатка переводила работу на человека,
    которого нет. Из очереди прежнего ревьюера она исчезала, у нового не
    появлялась — работа выпадала из потока целиком, и заметить это можно
    было только по несходящемуся счётчику.
    """
    r = client.put(
        "/api/submissions/ok/reviewer",
        json={"reviewer_id": "rev-которого-нет"},
        headers=COORD,
    )
    assert r.status_code == 400
    assert "ревьюер" in r.json()["detail"].lower()

    rows = {s["id"]: s for s in client.get("/api/submissions", headers=COORD).json()}
    assert rows["ok"]["reviewer_id"] is None, "работа всё-таки ушла в никуда"


def test_student_can_submit_a_free_topic_work(client) -> None:
    """Работа со своей темой заводит карточку на лету — но без критериев.

    Задание не всегда заведено заранее: преподаватель мог дать его на
    словах. Студент указывает название сам, работа попадает к ревьюеру,
    а критерии предстоит описать человеку — и до этого автоматическая
    проверка не запускается. Иначе балл выставлялся бы неизвестно за что.
    """
    r = client.post(
        "/api/submissions",
        data={
            "student_id": "stu-1",
            "track": "product_fraud",
            "new_assignment_title": "ДЗ №7. Своя тема",
        },
        files={"file": ("своя.md", b"# Reshenie\n\nTekst raboty dlinnee dvuhsot simvolov. " * 6, "text/markdown")},
        headers={"X-User-Id": "stu-1"},
    )
    assert r.status_code == 200, r.text
    sub_id = r.json()["submission_id"]

    rows = {s["id"]: s for s in client.get("/api/submissions", headers=COORD).json()}
    created = rows[sub_id]
    assert created["assignment_title"] == "ДЗ №7. Своя тема"
    assert created["has_file"] is True

    # Критериев нет — проверка обязана отказать, назвав причину.
    started = client.post(f"/api/submissions/{sub_id}/review", headers=COORD)
    assert started.status_code == 400
    assert "критери" in started.json()["detail"].lower()


def test_submission_without_assignment_or_title_is_refused(client) -> None:
    r = client.post(
        "/api/submissions",
        data={"student_id": "stu-1", "track": "product_fraud"},
        files={"file": ("работа.md", b"tekst", "text/markdown")},
        headers={"X-User-Id": "stu-1"},
    )
    assert r.status_code == 400
    assert "название" in r.json()["detail"].lower()


# ── подтверждение результата ──────────────────────────────────────────────────


REVIEWED = {
    "preliminary_score": 7.0,
    "max_score": 10.0,
    "criteria": [
        {"criterion_id": "c1", "criterion_name": "Первый", "score": 4.0,
         "max_score": 5.0, "weight": 1.0, "ai_score": 4.0, "verdict": "разбор"},
        {"criterion_id": "c2", "criterion_name": "Второй", "score": 3.0,
         "max_score": 5.0, "weight": 1.0, "ai_score": 3.0, "verdict": "разбор"},
    ],
    "penalties": [
        {"code": "deadline", "title": "Нарушение срока сдачи", "amount": 1.0,
         "detail": "досдача в течение суток"},
    ],
    "student_feedback": "разбор работы",
}


@pytest.fixture
def confirmable(client):
    """Работа с готовой проверкой, назначенная конкретному ревьюеру.

    Сроки заданы настоящие: сдача на час позже мягкого дедлайна и за 23
    часа до жёсткого — то самое состояние «досдача со штрафом −1», которое
    описывает условие задания. Раньше сроков у задания не было вовсе, а
    штраф лежал в сохранённом результате «просто так»; подтверждение
    доверяло этой записи, вместо того чтобы вывести штраф из дат.
    """
    import asyncio as aio
    from datetime import timedelta

    async def prepare():
        async with client.maker() as s:
            s.add(User(id="rev-1", name="Свой Ревьюер", role=Role.REVIEWER))
            s.add(User(id="rev-2", name="Чужой Ревьюер", role=Role.REVIEWER))
            work = await s.get(Submission, "ok")
            work.reviewer_id = "rev-1"
            work.review = REVIEWED
            assignment = await s.get(Assignment, "asg")
            assignment.due_at = work.submitted_at - timedelta(hours=1)
            assignment.hard_due_at = work.submitted_at + timedelta(hours=23)
            await s.commit()

    aio.run(prepare())
    return client


def _confirm(client, body, user):
    return client.post(
        "/api/submissions/ok/confirm", json=body, headers={"X-User-Id": user}
    )


def test_score_above_maximum_is_refused(confirmable) -> None:
    """Балл 999 из 10 публиковался студенту без единого возражения."""
    r = _confirm(confirmable, {"final_score": 999, "feedback": "x"}, "rev-1")
    assert r.status_code == 400
    assert "от 0 до 10" in r.json()["detail"]


def test_negative_score_is_refused(confirmable) -> None:
    r = _confirm(confirmable, {"final_score": -50, "feedback": "x"}, "rev-1")
    assert r.status_code == 400
    assert "отрицательным" in r.json()["detail"]


def test_another_reviewer_cannot_confirm(confirmable) -> None:
    """Смотреть чужую работу нельзя, а оценивать было можно.

    `get_submission` отдавал 403 чужому ревьюеру, а `confirm` не проверял
    ничего: любой ревьюер мог выставить оценку по чужой работе.
    """
    assert _confirm(confirmable, {"final_score": 5, "feedback": "x"}, "rev-2").status_code == 403
    assert _confirm(confirmable, {"final_score": 5, "feedback": "x"}, "rev-1").status_code == 200


def test_coordinator_may_confirm_any_work(confirmable) -> None:
    """Методист — старший над потоком, ему можно."""
    assert _confirm(confirmable, {"final_score": 5, "feedback": "x"}, "coord-1").status_code == 200


def test_criterion_score_above_its_maximum_is_refused(confirmable) -> None:
    r = _confirm(
        confirmable,
        {"final_score": 9, "feedback": "x", "criteria_scores": {"c1": 99}},
        "rev-1",
    )
    assert r.status_code == 400
    assert "Первый" in r.json()["detail"]


def test_deadline_penalty_cannot_be_lost(confirmable) -> None:
    """Штраф вычитал интерфейс — при обращении к API он просто исчезал.

    Правило «досдача в течение суток, штраф −1 балл» взято из условия
    задания, а не из мнения ревьюера. Поэтому итог считает сервер: сумма
    баллов по критериям минус штрафы.
    """
    r = _confirm(
        confirmable,
        # Клиент «забыл» вычесть штраф и прислал полную сумму 4 + 3.
        {"final_score": 7, "feedback": "x", "criteria_scores": {"c1": 4, "c2": 3}},
        "rev-1",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["final_score"] == 6.0, "штраф не применён"
    assert body["recomputed"] is True
    assert body["penalties_applied"] == 1.0


def test_work_past_the_hard_deadline_is_confirmed_with_zero(confirmable) -> None:
    """Правило условия: не сдано в срок — ноль баллов. Оно терялось дважды.

    Планировщик обнулял предварительный балл, но запись о штрафе получала
    `amount` уже ПОСЛЕ обнуления, то есть всегда ноль. Интерфейс вычитал
    из суммы по критериям этот ноль, отправлял полную сумму, и сервер
    принимал её без возражений: работа, которую условие велит оценить в
    ноль, уходила студенту с полным баллом.
    """
    import asyncio as aio
    from datetime import timedelta

    async def overdue():
        async with confirmable.maker() as s:
            # Двигается сам срок, а не флаг состояния: подтверждение
            # выводит правило из дат задания и времени сдачи, а не верит
            # сохранённому полю. Проставленный руками флаг проверял бы,
            # что сервер доверяет флагу, — то есть ровно то, чего делать
            # нельзя.
            work = await s.get(Submission, "ok")
            assignment = await s.get(Assignment, "asg")
            assignment.hard_due_at = work.submitted_at - timedelta(hours=1)
            await s.commit()

    aio.run(overdue())

    r = _confirm(
        confirmable,
        {"final_score": 7, "feedback": "x", "criteria_scores": {"c1": 4, "c2": 3}},
        "rev-1",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["final_score"] == 0.0, "жёсткий срок пройден, а балл не обнулён"
    assert body["forced_zero"] is True


def test_zero_penalty_record_keeps_the_lost_score(tmp_path) -> None:
    """Запись о штрафе должна называть снятый балл, а не ноль."""
    import asyncio as aio
    from datetime import timedelta

    from app.clock import now as now_utc
    from app.models import Assignment as A, Submission as S
    from app.notify.scheduler import refresh_submission_state

    review = {
        "submission_id": "x", "preliminary_score": 8.0, "max_score": 10.0,
        "criteria": [], "formal": [], "penalties": [], "warnings": [],
        "trace": [], "priority_index": 0.0, "priority_reasons": [],
        "student_feedback": "", "reviewer_summary": "", "duration_ms": 0,
        "model": "test",
    }
    now = now_utc()
    assignment = A(id="a", title="ДЗ", track="product_fraud",
                   due_at=now - timedelta(days=3), hard_due_at=now - timedelta(days=2),
                   review_due_at=now + timedelta(days=5), rubric={})
    sub = S(id="s", assignment_id="a", student_id="u", file_path="/tmp/x.md",
            file_name="x.md", track="product_fraud",
            submitted_at=now - timedelta(days=1), review=review)

    # Сессия нужна функции только для записи уведомлений. Настоящая база
    # здесь ни при чём: проверяется арифметика штрафа.
    class Collector:
        def __init__(self) -> None:
            self.added: list[object] = []

        def add(self, obj: object) -> None:
            self.added.append(obj)

        async def flush(self) -> None:
            return None

    aio.run(refresh_submission_state(Collector(), sub, assignment))

    assert sub.review["preliminary_score"] == 0.0
    penalty = sub.review["penalties"][0]
    assert penalty["amount"] == 8.0, f"штраф записан как {penalty['amount']}, а снято 8 баллов"


def test_double_click_does_not_queue_the_same_work_twice(client) -> None:
    """Два нажатия «Проверить» подряд отправляли работу модели дважды.

    Минута видеокарты впустую и гонка двух воркеров за одну запись
    результата. Кнопка блокируется на время запроса, но защита от двойного
    клика не может жить только в интерфейсе.
    """
    first = client.post("/api/submissions/ok/review", headers=COORD)
    second = client.post("/api/submissions/ok/review", headers=COORD)

    assert first.status_code == second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"], "заведено второе задание"


def test_manual_score_for_a_failed_criterion_counts(confirmable) -> None:
    """Критерий, который модель не смогла оценить, ревьюер оценивает сам.

    Интерфейс прямо предлагает: «оцените вручную». Сервер же суммировал
    только критерии без флага `failed` — выставленный человеком балл молча
    выбрасывался из итога.
    """
    import asyncio as aio
    from datetime import timedelta

    async def break_one():
        async with confirmable.maker() as s:
            work = await s.get(Submission, "ok")
            review = dict(REVIEWED)
            review["criteria"] = [
                dict(REVIEWED["criteria"][0]),
                dict(REVIEWED["criteria"][1], failed=True, score=0.0,
                     error="модель не ответила"),
            ]
            review["penalties"] = []
            work.review = review
            work.reviewer_id = "rev-1"
            # Сдача в срок: проверяется только арифметика по критериям,
            # штраф за просрочку здесь ни при чём.
            assignment = await s.get(Assignment, "asg")
            assignment.due_at = work.submitted_at + timedelta(hours=1)
            assignment.hard_due_at = work.submitted_at + timedelta(hours=25)
            await s.commit()

    aio.run(break_one())

    r = _confirm(
        confirmable,
        {"final_score": 9, "feedback": "x", "criteria_scores": {"c1": 4, "c2": 5}},
        "rev-1",
    )
    assert r.status_code == 200, r.text
    assert r.json()["final_score"] == 9.0, "балл за неоценённый критерий потерян"


# ── загрузка работы: вредные входы ────────────────────────────────────────────


def _upload(client, *, student="stu-1", name="работа.md", content=None, **extra):
    """Загрузка работы. `content=None` — осмысленный текст по умолчанию.

    Именно `None`, а не пустые байты: `b"" or default` подставил бы текст
    вместо пустого файла, и проверка пустого файла ничего бы не проверяла.
    """
    default = ("# Решение\n\n" + "Осмысленный текст работы. " * 20).encode("utf-8")
    return client.post(
        "/api/submissions",
        data={"assignment_id": "asg", "student_id": student,
              "track": "product_fraud", **extra},
        files={"file": (name, default if content is None else content, "text/markdown")},
        headers=COORD,
    )


def test_empty_file_is_refused(client) -> None:
    """Пустой файл — промах мышью, а не работа."""
    r = _upload(client, name="пусто.md", content=b"")
    assert r.status_code == 400
    assert "пуст" in r.json()["detail"].lower()


def test_unknown_student_is_refused(client) -> None:
    """Внешний ключ SQLite не соблюдает — проверять надо явно.

    Работа заводилась на несуществующего пользователя и висела в потоке
    ничьей: в интерфейсе вместо имени показывался идентификатор.
    """
    r = _upload(client, student="net-takogo")
    assert r.status_code == 400
    assert "студент" in r.json()["detail"].lower()


def test_reviewer_cannot_be_passed_as_the_author(client) -> None:
    """Роль тоже проверяется, а не только существование записи."""
    import asyncio as aio

    async def add_reviewer():
        async with client.maker() as s:
            s.add(User(id="rev-x", name="Ревьюер", role=Role.REVIEWER))
            await s.commit()

    aio.run(add_reviewer())
    assert _upload(client, student="rev-x").status_code == 400


def test_path_in_the_file_name_is_stripped(client) -> None:
    """Имя из запроса попадает в интерфейс и в выгрузку.

    Путь на диске генерируется сам, подмены каталога быть не может, но
    `../../../etc/passwd.md` сохранялось как есть и показывалось человеку.
    """
    r = _upload(client, name="../../../etc/passwd.md")
    assert r.status_code == 200, r.text
    rows = {s["id"]: s for s in client.get("/api/submissions", headers=COORD).json()}
    assert rows[r.json()["submission_id"]]["file_name"] == "passwd.md"


def test_absurdly_long_file_name_is_trimmed(client) -> None:
    """Колонка в базе — 300 символов; SQLite длину не проверяет, а другая СУБД проверит."""
    r = _upload(client, name="и" * 400 + ".md")
    assert r.status_code == 200, r.text
    rows = {s["id"]: s for s in client.get("/api/submissions", headers=COORD).json()}
    assert len(rows[r.json()["submission_id"]]["file_name"]) <= 200


def test_fileless_rows_sink_to_the_bottom_of_the_queue(client) -> None:
    """Занятое место не должно открываться первым.

    Очередь сортировалась по индексу приоритета и времени сдачи. У строк
    без файла приоритет нулевой, но сдача у них старая, и они всплывали
    наверх: ревьюер открывал экран, упирался в «проверять нечего» и делал
    вывод, что кнопки проверки в продукте нет вообще.
    """
    rows = client.get("/api/submissions", headers=COORD).json()
    with_file = [i for i, r in enumerate(rows) if r["has_file"]]
    without = [i for i, r in enumerate(rows) if not r["has_file"]]
    assert with_file and without, "в наборе должны быть строки обоих видов"
    assert max(with_file) < min(without), (
        "строка без файла оказалась выше работы с файлом: "
        + ", ".join(f"{r['file_name']}={r['has_file']}" for r in rows)
    )


def test_review_availability_is_reported_separately_from_the_score(client) -> None:
    """Разбор показывается по наличию критериев, а не по наличию балла.

    Балл может быть выставлен вручную по работе, которую модель не
    проверяла, — тогда показывать нечего, и кнопка «Разбор» появиться
    не должна.
    """
    rows = {s["id"]: s for s in client.get("/api/submissions", headers=COORD).json()}
    assert rows["ok"]["review_available"] is False
