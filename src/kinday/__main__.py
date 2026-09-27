"""Точка входа: читает настройки, открывает базу и поднимает собранное приложение.

Сама проводка слоёв живёт в `kinday.app`: её делят боевой запуск и сквозной
тест (SPEC 9). Здесь остаётся то, что есть только в бою, — конфигурация из
окружения, файл базы с миграциями, настоящий `Bot` и длинный опрос.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from kinday.app import build_application, start_application, stop_application
from kinday.clock import SystemClock
from kinday.config import Settings
from kinday.core.ports import Clock
from kinday.storage.db import Database, apply_migrations, connect
from kinday.telegram.bot import build_bot, run_polling

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
    bot = build_bot(settings)
    app = build_application(database, bot, clock)

    # Только через start_application: он закрывает строки, застрявшие в sending,
    # и лишь потом пускает задания (SPEC 5.5, критерий приёмки 9).
    await start_application(app)
    logger.info("kinday запущен, база %s", database_path)
    try:
        await run_polling(bot, app.dispatcher)
    finally:
        await stop_application(app)
        logger.info("kinday остановлен")


if __name__ == "__main__":
    main()
