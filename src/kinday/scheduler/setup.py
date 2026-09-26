"""Сборка APScheduler AsyncIOScheduler с двумя заданиями: тик и суточная материализация.

Хранилище заданий — в памяти: источник истины по напоминаниям только таблица
reminders, расписание известно на старте и создаётся заново при каждом запуске.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC
from typing import Protocol

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from kinday.core.ports import Clock, ReminderRepo
from kinday.scheduler.tick import fail_stuck_sending

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


class SchedulerLike(Protocol):
    """Что нужно от планировщика при старте: только запуск.

    Узкий протокол вместо AsyncIOScheduler, чтобы `start_scheduler` можно было
    проверить тестом, не поднимая настоящий APScheduler с его циклом событий.
    """

    def start(self) -> None: ...


async def start_scheduler(
    scheduler: SchedulerLike, clock: Clock, reminder_repo: ReminderRepo
) -> None:
    """Закрывает строки, застрявшие в sending, и только потом запускает задания.

    Порядок обязателен (SPEC 5.5, критерий приёмки 9): строки в `sending`
    остались от падения между send и mark, и повторная отправка хуже пропуска.
    Запусти задания раньше — первый же тик начал бы разбирать очередь, пока эти
    строки ещё не закрыты, и в журнале они оказались бы после новых отправок.

    Точка входа сервиса (этап 6) обязана поднимать планировщик только через эту
    функцию, а не вызывать `scheduler.start()` напрямую.
    """
    await fail_stuck_sending(clock, reminder_repo)
    scheduler.start()
