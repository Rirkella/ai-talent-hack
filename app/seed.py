"""Наполнение демонстрационными данными.

Пользователи, задание, рубрика и пул работ — чтобы сквозной сценарий
проходился сразу после установки, без ручной подготовки.

**Только синтетические данные.** Все имена вымышлены. Реальные ФИО
лежат в метаданных примеров работ, и переносить их в репозиторий нельзя:
кейс запрещает передачу персональных данных, а документация проекта
обязана быть обезличенной.

    python -m app.seed            # создать
    python -m app.seed --reset    # пересоздать с нуля
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, select

from app.config import PROJECT_ROOT, settings
from app.db import init_db, session_scope
from app.ingest.loader import file_sha256, read_any
from app.auth import hash_password, unique_login
from app.models import (
    ActionLog,
    Assignment,
    Job,
    Notification,
    Role,
    Submission,
    SubmissionStatus,
    User,
)
from app.notify.deadlines import now_utc
from app.rubric.extract import extract_rubric
from app.rubric.models import Rubric

EXAMPLES = PROJECT_ROOT / "data" / "examples" / "product_fraud"
CONDITION = EXAMPLES / "Product_Fraud_ДЗ2_условия.pdf"

# Имена вымышленные. Реальные ФИО есть в метаданных примеров работ, и
# переносить их в репозиторий нельзя. Роль указана отдельным полем, поэтому
# в самих именах её кодировать не нужно — обычные имена читаются как люди,
# а не как ярлыки.
USERS = [
    # Методист
    ("coord-1", "Ирина Соколова", Role.COORDINATOR, 0, []),
    # Ревьюеры с разной ёмкостью и разными компетенциями — иначе
    # распределение по нагрузке нечего показывать.
    ("rev-1", "Павел Кузнецов", Role.REVIEWER, 4, ["product_fraud", "product_business_models"]),
    ("rev-2", "Ольга Дьяченко", Role.REVIEWER, 6, ["product_fraud"]),
    ("rev-3", "Тимур Гареев", Role.REVIEWER, 3, ["tech_QA", "system_design"]),
    # Студенты
    ("stu-1", "Анна Лебедева", Role.STUDENT, 0, []),
    ("stu-2", "Борис Мельник", Role.STUDENT, 0, []),
    ("stu-3", "Вера Зотова", Role.STUDENT, 0, []),
]

# Файл примера → студент. Ярлыки уровней остаются во внутренних именах файлов,
# в интерфейсе видно только имя студента.
WORKS = [
    ("Product_Fraud_ДЗ2_Решение слабое.docx", "stu-1"),
    ("Product_Fraud_ДЗ2_Решение среднее.docx", "stu-2"),
    ("Product_Fraud_ДЗ2_Решение хорошее.docx", "stu-3"),
]

ASSIGNMENT_ID = "hw-product-fraud-2"

# Второй курс. Раньше он заводился вручную через интерфейс, и это же было
# аргументом «курс добавляется без правок кода» — но `--reset` его стирал,
# и демо-данные тихо теряли половину истории. Способ добавления курса это
# не меняет: путь через интерфейс остался, здесь просто зафиксировано
# исходное состояние стенда.
SECOND_COURSE = {
    "id": "hw-tech-qa-1",
    "title": "ДЗ №1. Тест-кейсы для формы объявления",
    "track": "tech_QA",
    "course": "Технический QA",
    "homework_no": 1,
    "dir": PROJECT_ROOT / "data" / "examples" / "tech_QA",
    "condition": "Tech_QA_ДЗ1_условия.pdf",
    "works": [
        ("Tech_QA_ДЗ1_Решение_слабое.xlsx", "stu-1"),
        ("Tech_QA_ДЗ1_Решение_среднее.xlsx", "stu-2"),
        ("Tech_QA_ДЗ1_Решение_хорошее.xlsx", "stu-3"),
    ],
}


async def _add_second_course(session, now, *, extract: bool) -> None:  # noqa: ANN001
    """Второй курс: доказательство того, что решение не заточено под одно ДЗ.

    Другой формат работ (`.xlsx` вместо `.docx`), другая рубрика, другой
    максимум балла — 20 вместо 10. Именно на нём видно, почему складывать
    баллы разных заданий нельзя и почему аналитика по потоку считает доли
    от максимума, а не сырые баллы.

    Критерии здесь сразу утверждены: на защите живьём утверждается рубрика
    основного задания, и повторять этот шаг дважды незачем.
    """
    spec = SECOND_COURSE
    src_dir: Path = spec["dir"]
    condition_src = src_dir / spec["condition"]
    if not condition_src.exists():
        print(f"! второй курс пропущен: нет {condition_src}")
        return

    stored_condition = settings.upload_dir / condition_src.name
    shutil.copy2(condition_src, stored_condition)

    rubric = Rubric(track=spec["track"], course=spec["course"])
    if extract:
        print(f"извлечение рубрики второго курса ({spec['course']})…", flush=True)
        try:
            rubric = await asyncio.to_thread(
                extract_rubric, read_any(stored_condition),
                track=spec["track"], course=spec["course"],
            )
            print(f"  критериев: {len(rubric.criteria)}, максимум {rubric.total_max:g}")
        except Exception as exc:  # noqa: BLE001
            print(f"  не удалось ({exc}); критерии нужно ввести вручную")
    if rubric.criteria:
        rubric.approve(by="seed")

    session.add(
        Assignment(
            id=spec["id"],
            title=spec["title"],
            track=spec["track"],
            course=spec["course"],
            homework_no=spec["homework_no"],
            channel="stepik",
            condition_path=str(stored_condition),
            condition_sha256=file_sha256(stored_condition),
            rubric=rubric.model_dump(mode="json"),
            rubric_approved=bool(rubric.criteria),
            due_at=now + timedelta(days=5),
            hard_due_at=now + timedelta(days=6),
            review_due_at=now + timedelta(days=13),
            due_soon_lead_s=3600,
        )
    )

    added = 0
    for i, (filename, student_id) in enumerate(spec["works"]):
        src = src_dir / filename
        if not src.exists():
            print(f"! нет файла {filename}")
            continue
        stored = settings.upload_dir / f"seed_{uuid.uuid4().hex[:8]}{src.suffix}"
        shutil.copy2(src, stored)
        session.add(
            Submission(
                id=str(uuid.uuid4()),
                assignment_id=spec["id"],
                student_id=student_id,
                # Ревьюер с компетенцией именно по этому направлению.
                reviewer_id="rev-3",
                file_path=str(stored),
                file_name=filename,
                file_sha256=file_sha256(stored),
                track=spec["track"],
                status=SubmissionStatus.ASSIGNED,
                submitted_at=now - timedelta(hours=30 - i * 4),
            )
        )
        added += 1
    print(f"второй курс: {spec['course']}, работ {added}")


async def seed(*, reset: bool = False, extract: bool = True) -> None:
    settings.ensure_dirs()
    await init_db()

    async with session_scope() as session:
        if reset:
            for model in (Job, Notification, ActionLog, Submission, Assignment, User):
                await session.execute(delete(model))
            await session.commit()

        existing = (await session.execute(select(User))).scalars().first()
        if existing and not reset:
            print("Данные уже есть. Для пересоздания: python -m app.seed --reset")
            return

        # Логин выводится из имени, пароль у всех демонстрационных учётных
        # записей общий — иначе на защите пришлось бы держать список из
        # девятнадцати паролей. Хеш считается один раз на пользователя:
        # pbkdf2 намеренно медленный, и в цикле это заметно.
        taken: set[str] = set()
        demo_hash = hash_password(settings.demo_password)
        for uid, name, role, capacity, comps in USERS:
            login = unique_login(name, taken)
            taken.add(login)
            session.add(
                User(
                    id=uid, name=name, role=role.value,
                    capacity=capacity or 10, competencies=comps,
                    email=f"{uid}@example.local",
                    login=login, password_hash=demo_hash,
                )
            )
        print(f"пользователей: {len(USERS)}, пароль у всех: {settings.demo_password}")

        # ── задание и рубрика ─────────────────────────────────────────────
        rubric = Rubric(track="product_fraud", course="Продуктовый фрод")
        condition_path = ""

        if CONDITION.exists():
            stored = settings.upload_dir / CONDITION.name
            shutil.copy2(CONDITION, stored)
            condition_path = str(stored)
            if extract:
                print("извлечение рубрики из условия…", flush=True)
                try:
                    condition = read_any(stored)
                    rubric = await asyncio.to_thread(
                        extract_rubric, condition,
                        track="product_fraud", course="Продуктовый фрод",
                    )
                    print(f"  критериев: {len(rubric.criteria)}, максимум {rubric.total_max:g}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  не удалось ({exc}); рубрику нужно ввести вручную")
        else:
            print(f"! условие не найдено: {CONDITION}")

        now = now_utc()
        assignment = Assignment(
            id=ASSIGNMENT_ID,
            title=rubric.assignment_title or "ДЗ №2. Карта рисков продукта",
            track="product_fraud",
            course="Продуктовый фрод",
            homework_no=2,
            channel="stepik",
            condition_path=condition_path,
            condition_sha256=file_sha256(condition_path) if condition_path else "",
            rubric=rubric.model_dump(mode="json"),
            # Рубрика НЕ утверждена: утверждение — шаг методиста в сценарии,
            # и показать его надо живьём.
            rubric_approved=False,
            # Сроки с запасом: панель методиста переводит их в демо-режим
            # (мягкий +10 с, жёсткий +40 с) прямо на защите.
            due_at=now + timedelta(days=2),
            hard_due_at=now + timedelta(days=3),
            review_due_at=now + timedelta(days=10),
            due_soon_lead_s=3600,
        )
        session.add(assignment)
        print(f"задание: {assignment.title}")

        # ── работы ────────────────────────────────────────────────────────
        added = 0
        for i, (filename, student_id) in enumerate(WORKS):
            src = EXAMPLES / filename
            if not src.exists():
                print(f"! нет файла {filename}")
                continue
            stored = settings.upload_dir / f"seed_{uuid.uuid4().hex[:8]}{src.suffix}"
            shutil.copy2(src, stored)
            session.add(
                Submission(
                    id=str(uuid.uuid4()),
                    assignment_id=ASSIGNMENT_ID,
                    student_id=student_id,
                    file_path=str(stored),
                    file_name=filename,
                    file_sha256=file_sha256(stored),
                    track="product_fraud",
                    status=SubmissionStatus.UPLOADED,
                    # Разное время сдачи: у одной работы оно после мягкого срока,
                    # чтобы правило штрафа было видно и без ожидания таймера.
                    submitted_at=now - timedelta(hours=6 - i * 2),
                )
            )
            added += 1
        print(f"работ: {added}")

        # Предварительная загрузка ревьюера: без перекоса распределение
        # выглядело бы тривиальным, а показать надо именно выравнивание.
        #
        # Статус ASSIGNED: это работы, уже лежащие в очереди у ревьюера,
        # и именно они создают перекос нагрузки, который выравнивает
        # распределение. Файла у них нет, поэтому массовый запуск ревью
        # обязан их пропускать — см. фильтр по file_path в review_all.
        session.add_all([
            Submission(
                id=str(uuid.uuid4()), assignment_id=ASSIGNMENT_ID,
                student_id="stu-1", reviewer_id="rev-1",
                file_path="", file_name=f"[прошлый поток] работа {n + 1}",
                track="product_fraud", status=SubmissionStatus.ASSIGNED,
                submitted_at=now - timedelta(days=3),
            )
            for n in range(3)
        ])
        print("текущая очередь первого ревьюера: 3 работы (перекос для демонстрации)")

        await _add_second_course(session, now, extract=extract)

        await session.commit()

    print("\nГотово. Запуск: powershell -File scripts/run.ps1")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Демо-данные")
    p.add_argument("--reset", action="store_true", help="удалить и создать заново")
    p.add_argument("--no-extract", action="store_true", help="без обращения к модели")
    a = p.parse_args()
    asyncio.run(seed(reset=a.reset, extract=not a.no_extract))
