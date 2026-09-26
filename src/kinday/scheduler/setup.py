"""Сборка APScheduler AsyncIOScheduler с двумя заданиями: тик и суточная материализация.

Хранилище заданий — в памяти: источник истины по напоминаниям только таблица
reminders, расписание известно на старте и создаётся заново при каждом запуске.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

TICK_INTERVAL_SECONDS = 60

# Суточная материализация — страховка (SPEC 5.4), её час выбран в ночном
# затишье по UTC: к этому времени напоминания на утро по любому поясу уже
# построены сценариями, так что задание только сдвигает горизонт.
DAILY_MATERIALIZATION_HOUR_UTC = 3
DAILY_MATERIALIZATION_MINUTE = 17

# Тик, опоздавший больше чем на интервал, смысла не имеет: следующий запуск
# заберёт ту же очередь целиком, а просрочку разберёт сам тик (SPEC 5.5).
TICK_MISFIRE_GRACE_SECONDS = TICK_INTERVAL_SECONDS
DAILY_MISFIRE_GRACE_SECONDS = 60 * 60

Job = Callable[[], Awaitable[None]]


def build_scheduler(tick: Job, materialize: Job) -> AsyncIOScheduler:
    """Настраивает тик (max_instances=1) и суточную материализацию (coalesce=True).

    Задания приходят готовыми функциями без аргументов: репозитории и часы
    связывает точка входа, а планировщик знает только расписание.

    `coalesce=True` у обоих: после паузы (спящий ноутбук, остановленный сервис)
    APScheduler иначе запустил бы пачку догоняющих вызовов, а смысла в них нет —
    и тик, и материализация каждый раз смотрят на текущее состояние базы целиком.
    `max_instances=1` не даёт двум тикам разбирать очередь одновременно (SPEC 5.4,
    вместе с условным UPDATE в claim это исключает двойную отправку).
    """
    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(
        tick,
        IntervalTrigger(seconds=TICK_INTERVAL_SECONDS),
        id="tick",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=TICK_MISFIRE_GRACE_SECONDS,
    )
    scheduler.add_job(
        materialize,
        CronTrigger(
            hour=DAILY_MATERIALIZATION_HOUR_UTC, minute=DAILY_MATERIALIZATION_MINUTE, timezone=UTC
        ),
        id="daily_materialization",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=DAILY_MISFIRE_GRACE_SECONDS,
    )
    return scheduler
