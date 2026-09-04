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
def client(tmp_path):
    from app.db import get_session
    from app.main import app

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'g.db'}")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    work = tmp_path / "работа.md"
    work.write_text("# Решение\n\nТекст работы.\n", encoding="utf-8")

    async def prepare():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
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

    async def override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


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
