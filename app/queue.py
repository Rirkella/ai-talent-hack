"""Очередь предварительного ревью.

`asyncio.Queue` плюс воркеры плюс таблица `Job`. Ни Redis, ни Celery:
демо — один процесс, а внешний брокер добавил бы инфраструктуру, которой
по условиям проекта быть не должно.

Таблица нужна не для распределённости, а для наблюдаемости: после перезапуска
должно быть видно, какие работы остались непроверенными.

Воркеров по умолчанию один. Больше на одной GPU бессмысленно — Ollama всё
равно сериализует запросы, а параллельные воркеры только раздули бы очередь
и сделали прогресс рваным.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.agent.pipeline import run_review
from app.config import settings
from app.db import session_scope
from app.events import bus
from app.ingest.loader import read_any
from app.models import Assignment, Job, JobStatus, Submission, SubmissionStatus
from app.rubric.models import Rubric

log = logging.getLogger(__name__)


class ReviewQueue:
    """Очередь заданий на ревью."""

    def __init__(self, workers: int = 1) -> None:
        self.workers = max(1, workers)
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for i in range(self.workers):
            self._tasks.append(
                asyncio.create_task(self._worker(i), name=f"review-worker-{i}")
            )
        await self._requeue_orphans()
        log.info("очередь ревью запущена, воркеров: %d", self.workers)

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    async def _requeue_orphans(self) -> None:
        """Возвращает в очередь задания, оставшиеся с прошлого запуска.

        Задание в статусе `running` после старта процесса означает, что
        приложение упало на середине ревью. Оставлять такую работу
        непроверенной молча нельзя.
        """
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(Job).where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
                )
            ).scalars().all()
            for job in rows:
                job.status = JobStatus.QUEUED
                job.stage = "возвращено в очередь после перезапуска"
                await self._queue.put(job.id)
            if rows:
                await session.commit()
                log.info("возвращено в очередь заданий: %d", len(rows))

    async def enqueue(self, submission_id: str) -> str:
        """Ставит работу в очередь. Возвращает id задания.

        Повторная постановка той же работы возвращает уже существующее
        задание, а не заводит второе. Без этого два нажатия «Проверить»
        подряд отправляли одну работу модели дважды: минута видеокарты
        впустую, а на выходе — гонка двух воркеров за одну и ту же запись
        результата. Кнопка блокируется на время запроса, но защита от
        двойного клика не может жить только в интерфейсе.
        """
        async with session_scope() as session:
            existing = (
                await session.execute(
                    select(Job)
                    .where(
                        Job.submission_id == submission_id,
                        Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                    )
                    .order_by(Job.created_at.desc())
                )
            ).scalars().first()
            if existing is not None:
                log.info("работа %s уже в очереди, задание %s", submission_id, existing.id)
                return existing.id

        job_id = str(uuid.uuid4())
        async with session_scope() as session:
            session.add(Job(id=job_id, submission_id=submission_id, status=JobStatus.QUEUED))
            sub = await session.get(Submission, submission_id)
            if sub:
                sub.status = SubmissionStatus.IN_REVIEW
            await session.commit()

        await self._queue.put(job_id)
        await bus.publish(
            "job_queued",
            {"job_id": job_id, "submission_id": submission_id, "position": self._queue.qsize()},
        )
        return job_id

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    async def _worker(self, index: int) -> None:
        while self._running:
            try:
                job_id = await self._queue.get()
            except asyncio.CancelledError:
                raise
            try:
                await self._process(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — воркер не имеет права умирать
                log.exception("воркер %d: задание %s упало", index, job_id)
            finally:
                self._queue.task_done()

    async def _process(self, job_id: str) -> None:
        """Выполняет одно ревью.

        Тяжёлая часть уносится в поток: пайплайн синхронный и упирается в
        сеть к Ollama. Без этого один вызов модели заблокировал бы event loop,
        и SSE-события с прогрессом до браузера просто не дошли бы.
        """
        async with session_scope() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            sub = await session.get(Submission, job.submission_id)
            if sub is None:
                job.status = JobStatus.ERROR
                job.error = "работа не найдена"
                await session.commit()
                return
            assignment = await session.get(Assignment, sub.assignment_id)
            if assignment is None:
                job.status = JobStatus.ERROR
                job.error = "задание не найдено"
                await session.commit()
                return

            job.status = JobStatus.RUNNING
            job.started_at = datetime.now(timezone.utc)
            file_path, sub_id = sub.file_path, sub.id
            rubric = Rubric.model_validate(assignment.rubric or {})
            condition_path = assignment.condition_path
            await session.commit()

        await bus.publish("job_started", {"job_id": job_id, "submission_id": sub_id})

        loop = asyncio.get_running_loop()

        def progress(stage: str, fraction: float) -> None:
            # Из рабочего потока в event loop: обычный await здесь недоступен.
            asyncio.run_coroutine_threadsafe(
                bus.publish(
                    "job_progress",
                    {
                        "job_id": job_id, "submission_id": sub_id,
                        "stage": stage, "progress": round(fraction, 3),
                    },
                ),
                loop,
            )

        # Настройки формулы читаются на момент запуска: методист мог
        # поменять веса, и ревью обязано считаться по актуальным.
        from app.api.routes import current_scoring

        scoring = current_scoring()

        def work() -> object:
            doc = read_any(file_path)
            condition = read_any(condition_path) if condition_path else None
            return run_review(
                doc, rubric, condition=condition,
                submission_id=sub_id, progress=progress, scoring=scoring,
            )

        try:
            result = await asyncio.to_thread(work)
        except Exception as exc:  # noqa: BLE001
            log.exception("ревью %s не выполнено", sub_id)
            async with session_scope() as session:
                job = await session.get(Job, job_id)
                sub = await session.get(Submission, sub_id)
                if job:
                    job.status = JobStatus.ERROR
                    job.error = f"{type(exc).__name__}: {exc}"
                    job.finished_at = datetime.now(timezone.utc)
                if sub:
                    sub.status = SubmissionStatus.FAILED
                await session.commit()
            await bus.publish(
                "job_failed",
                {"job_id": job_id, "submission_id": sub_id, "error": str(exc)},
            )
            return

        async with session_scope() as session:
            job = await session.get(Job, job_id)
            sub = await session.get(Submission, sub_id)
            if job:
                job.status = JobStatus.DONE
                job.progress = 1.0
                job.stage = "готово"
                job.finished_at = datetime.now(timezone.utc)
            if sub:
                sub.review = result.model_dump(mode="json")
                sub.ai_score = float((result.ai_signal or {}).get("score", 0.0))
                sub.priority_index = result.priority_index
                sub.status = SubmissionStatus.AI_REVIEWED
            await session.commit()
            reviewer_id = sub.reviewer_id if sub else None

        await bus.publish(
            "review_ready",
            {
                "submission_id": sub_id,
                "score": result.preliminary_score,
                "max_score": result.max_score,
                "priority_index": result.priority_index,
                "ai_score": float((result.ai_signal or {}).get("score", 0.0)),
                "failed_steps": result.failed_steps,
            },
        )

        if reviewer_id:
            from app.notify.scheduler import notify

            async with session_scope() as session:
                await notify(
                    session, user_id=reviewer_id, kind="review.ready",
                    title="Предварительное ревью готово",
                    body=(
                        f"«{result.source_name}»: {result.preliminary_score:g} "
                        f"из {result.max_score:g}. {result.reviewer_summary}"
                    ),
                    submission_id=sub_id,
                    severity="warning" if result.priority_index >= 0.5 else "info",
                )
                await session.commit()


queue = ReviewQueue(workers=settings.review_workers)
