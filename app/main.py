"""Точка входа приложения.

Один процесс `uvicorn` обслуживает API, SSE и статику интерфейса. Ни Node,
ни отдельного веб-сервера, ни контейнера: чем меньше движущихся частей,
тем меньше шансов, что что-то откажет на защите.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import settings
from app.db import init_db
from app.events import bus
from app.notify.scheduler import scheduler
from app.privacy.audit import OutboundBlocked
from app.queue import queue
from app.rubric.models import RubricNotApproved

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("avito_reviewer")

WEB_DIR = Path(__file__).resolve().parent.parent / "web" / "dist"


def _release_streams_on_signal() -> None:
    """Отпускать SSE-потоки сразу по Ctrl+C, а не в конце остановки.

    Порядок остановки у uvicorn такой: сначала он ждёт, когда закроются
    открытые соединения, и только потом выполняет завершение lifespan.
    Поэтому `bus.shutdown()` в lifespan срабатывает слишком поздно —
    к тому моменту сервер уже пять секунд ждёт потоки, которые ждут его.
    Наблюдалось прямо: процесс завершался ровно по таймауту и с руганью
    «timeout graceful shutdown exceeded».

    Обработчик сигнала будит потоки в тот момент, когда пользователь нажал
    Ctrl+C, и передаёт управление штатному обработчику uvicorn — своей
    логики остановки здесь нет, только пробуждение.
    """
    loop = asyncio.get_running_loop()

    signals = [signal.SIGINT, signal.SIGTERM]
    # На Windows Ctrl+Break приходит отдельным сигналом.
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)

    for sig in signals:
        previous = signal.getsignal(sig)

        def handler(signum, frame, _prev=previous):  # noqa: ANN001, ANN202
            # Из обработчика сигнала в цикл событий: трогать asyncio-объекты
            # напрямую отсюда нельзя.
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(bus.shutdown()))
            if callable(_prev):
                _prev(signum, frame)

        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            # Не главный поток или сигнал не поддерживается — не повод падать.
            log.debug("не удалось перехватить сигнал %s", sig)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    settings.ensure_dirs()
    await init_db()
    await queue.start()
    await scheduler.start()
    _release_streams_on_signal()
    log.info(
        "запущено на http://%s:%d | модель %s | офлайн-контур: %s",
        settings.app_host, settings.app_port, settings.llm_model,
        "включён" if settings.offline_enforce else "выключен",
    )
    yield
    # Порядок важен: сначала отпускаем открытые SSE-потоки. Иначе uvicorn
    # ждёт закрытия соединений, а они живут, пока открыта вкладка браузера,
    # и Ctrl+C не останавливает процесс.
    log.info("остановка…")
    await bus.shutdown()
    await scheduler.stop()
    await queue.stop()


app = FastAPI(
    title="Avito AI Reviewer",
    description="Помощник ревьюера и координатора образовательных программ",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)


@app.exception_handler(OutboundBlocked)
async def outbound_blocked_handler(request, exc: OutboundBlocked):  # noqa: ANN001, ANN201
    """Заблокированный офлайн-контуром вызов — осмысленная ошибка, а не 500."""
    return JSONResponse(
        status_code=502,
        content={
            "detail": str(exc),
            "hint": "Проверьте ALLOWED_HOSTS и LLM_BASE_URL в .env.",
        },
    )


@app.exception_handler(RubricNotApproved)
async def rubric_not_approved_handler(request, exc: RubricNotApproved):  # noqa: ANN001, ANN201
    return JSONResponse(status_code=400, content={"detail": str(exc)})


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        """Страница приложения.

        `Cache-Control: no-store` обязателен. `FileResponse` ставит только
        `ETag` и `Last-Modified`, а без `Cache-Control` браузеру разрешено
        эвристическое кэширование: он вправе отдать `index.html` из кэша
        вообще без обращения к серверу. Имена файлов сборки содержат хеш,
        поэтому старый `index.html` намертво прибивает старый интерфейс —
        пересобранный фронт до пользователя не доезжает, и обновление
        страницы ничего не меняет. Сами файлы сборки кэшировать можно и
        нужно: их имя меняется при каждой сборке.
        """
        return FileResponse(
            WEB_DIR / "index.html",
            headers={"Cache-Control": "no-store, must-revalidate"},
        )
else:  # pragma: no cover — только при повреждённой выгрузке

    @app.get("/")
    async def index_missing() -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Интерфейс не найден: ожидался каталог {WEB_DIR}"},
        )


def run() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        log_level=settings.log_level.lower(),
        # Без предела ожидания Ctrl+C не завершает процесс, пока открыта
        # вкладка с живой подпиской на события. Пяти секунд хватает, чтобы
        # отдать уже начатые ответы.
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    run()
