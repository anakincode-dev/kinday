"""Сборка приложения: единственное место, где сходятся все слои.

Репозитории SQLite подставляются в порты ядра, поверх них собираются диалоги
(`Dispatcher`), notifier и два задания планировщика. Точка входа (`__main__`)
добавляет к этому только чтение настроек, открытие базы и длинный опрос —
сама проводка живёт здесь, чтобы сквозной тест (SPEC 9) поднимал ровно то же
приложение, что и боевой запуск, а не собирал свою копию.

Снаружи задаются три вещи: база, часы и `Bot`. Тест подменяет часы и сессию
`Bot`, всё остальное настоящее.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from kinday.core.ports import Clock
from kinday.scheduler.materialize import run_daily_materialization
from kinday.scheduler.setup import build_scheduler, start_scheduler
from kinday.scheduler.tick import run_tick
from kinday.storage.db import Database
from kinday.telegram.bot import build_deps, build_dispatcher
from kinday.telegram.notifier import TelegramNotifier
from kinday.telegram.routers import Deps

Job = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class Application:
    """Собранное приложение: порты, диалоги, задания и планировщик.

    `tick` и `materialize` — те самые функции, которые планировщик вызывает по
    расписанию. Они лежат здесь отдельным полем не ради удобства: сквозной тест
    двигает часы подменой и вызывает тик сам, и вызывать он обязан ровно то, что
    в бою вызывает APScheduler, иначе тест проверял бы собственную сборку.
    """

    database: Database
    bot: Bot
    deps: Deps
    notifier: TelegramNotifier
    dispatcher: Dispatcher
    scheduler: AsyncIOScheduler
    tick: Job
    materialize: Job


def build_application(database: Database, bot: Bot, clock: Clock) -> Application:
    """Связывает слои: порты на SQLite, диалоги поверх них, notifier поверх Bot.

    `bot` и база берутся готовыми, а не собираются из настроек: точка входа
    передаёт настоящий `Bot` с токеном из окружения и файл на диске, тест —
    такой же `Bot` с подменённой сессией и базу в `tmp_path`. Оба остаются в
    `Application`, потому что закрывает их `stop_application`.
    """
    deps = build_deps(database, clock)
    notifier = TelegramNotifier(bot)

    async def tick() -> None:
        await run_tick(
            clock,
            notifier,
            deps.account,
            deps.event,
            deps.membership,
            deps.person,
            deps.relation,
            deps.reminder,
            deps.uow,
        )

    async def materialize() -> None:
        await run_daily_materialization(
            clock,
            deps.account,
            deps.event,
            deps.membership,
            deps.override,
            deps.reminder,
            deps.uow,
        )

    return Application(
        database=database,
        bot=bot,
        deps=deps,
        notifier=notifier,
        dispatcher=build_dispatcher(deps),
        scheduler=build_scheduler(tick, materialize),
        tick=tick,
        materialize=materialize,
    )


async def start_application(app: Application) -> None:
    """Поднимает планировщик через `start_scheduler`, а не `scheduler.start()`.

    Порядок обязателен (SPEC 5.5, критерий приёмки 9): строки, застрявшие в
    `sending` после падения, закрываются до того, как первый тик начнёт
    разбирать очередь. Обёртка нужна, чтобы этот порядок был один на боевой
    запуск и на тест, а не повторялся в каждом месте сборки.
    """
    await start_scheduler(app.scheduler, app.deps.clock, app.deps.reminder)


async def stop_application(app: Application) -> None:
    """Гасит всё в обратном порядке: сначала задания, потом транспорт, потом база.

    Порядок тоже обязателен: идущий тик держит и сессию Telegram, и соединение с
    базой, поэтому планировщик останавливается первым. `wait=False` — остановка
    приходит по SIGTERM, и ждать текущего тика до сетевого таймаута systemd не
    станет: незавершённая строка останется в `sending` и будет закрыта при
    следующем старте (SPEC 5.5).
    """
    app.scheduler.shutdown(wait=False)
    await app.bot.session.close()
    app.database.close()
