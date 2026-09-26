"""Точка входа: собирает зависимости и поднимает бота с планировщиком.

Единственное место, где сходятся все слои: конфигурация из окружения, соединение
с SQLite и миграции, порты ядра на репозиториях, notifier поверх aiogram,
планировщик с двумя заданиями и длинный опрос.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from kinday.clock import SystemClock
from kinday.config import Settings
from kinday.core.ports import Clock
from kinday.scheduler.materialize import run_daily_materialization
from kinday.scheduler.setup import build_scheduler, start_scheduler
from kinday.scheduler.tick import run_tick
from kinday.storage.db import Database, apply_migrations, connect
from kinday.telegram.bot import build_bot, build_deps, build_dispatcher, run_polling
from kinday.telegram.notifier import TelegramNotifier

logger = logging.getLogger(__name__)


def main() -> None:
    """Читает настройки, настраивает журнал и запускает сервис до остановки.

    Настройки читаются до цикла событий: нет BOT_TOKEN — падаем сразу понятной
    ошибкой (критерий приёмки 3), не открыв ни базы, ни соединения с Telegram.
    """
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_run(settings))


async def _run(settings: Settings) -> None:
    """Готовит базу и зависимости, поднимает планировщик, уходит в длинный опрос."""
    database_path = Path(settings.database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = connect(str(database_path))
    apply_migrations(connection)
    database = Database(connection)

    clock: Clock = SystemClock()
    deps = build_deps(database, clock)
    bot = build_bot(settings)
    dispatcher = build_dispatcher(deps)
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

    scheduler = build_scheduler(tick, materialize)
    # Только через start_scheduler: он закрывает строки, застрявшие в sending,
    # и лишь потом пускает задания (SPEC 5.5, критерий приёмки 9).
    await start_scheduler(scheduler, clock, deps.reminder)
    logger.info("kinday запущен, база %s", database_path)
    try:
        await run_polling(bot, dispatcher)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        database.close()
        logger.info("kinday остановлен")


if __name__ == "__main__":
    main()
