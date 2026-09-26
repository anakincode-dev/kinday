"""Сборка APScheduler: два задания с параметрами из SPEC 5.4.

Планировщик не запускается: проверяется только конфигурация заданий — сами
функции тика и материализации закрыты тестами рядом.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from kinday.scheduler.setup import (
    DAILY_MATERIALIZATION_HOUR_UTC,
    DAILY_MISFIRE_GRACE_SECONDS,
    TICK_INTERVAL_SECONDS,
    TICK_MISFIRE_GRACE_SECONDS,
    build_scheduler,
)


async def _tick() -> None: ...


async def _materialize() -> None: ...


def test_build_scheduler_configures_tick_and_daily_job() -> None:
    scheduler = build_scheduler(_tick, _materialize)

    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {"tick", "daily_materialization"}

    tick = jobs["tick"]
    assert tick.func is _tick
    assert isinstance(tick.trigger, IntervalTrigger)
    assert tick.trigger.interval == timedelta(seconds=TICK_INTERVAL_SECONDS)
    # SPEC 5.4: max_instances=1 против наложения тиков, coalesce против пачки
    # догоняющих запусков — очередь отправки и так разбирается целиком.
    assert tick.max_instances == 1
    assert tick.coalesce is True
    # Пропущенный тик догоняется только в пределах минуты: дальше сроки
    # напоминаний всё равно подошли, и следующий тик заберёт их сам.
    assert tick.misfire_grace_time == TICK_MISFIRE_GRACE_SECONDS

    daily = jobs["daily_materialization"]
    assert daily.func is _materialize
    assert isinstance(daily.trigger, CronTrigger)
    # Раз в сутки в заданный час UTC: следующий запуск после полудня — назавтра.
    next_fire = daily.trigger.get_next_fire_time(None, datetime(2027, 1, 1, 12, tzinfo=UTC))
    assert next_fire is not None
    assert (next_fire.astimezone(UTC).date(), next_fire.astimezone(UTC).hour) == (
        date(2027, 1, 2),
        DAILY_MATERIALIZATION_HOUR_UTC,
    )
    assert daily.coalesce is True
    assert daily.max_instances == 1
    # Суточному заданию дан час: оно достраивает горизонт на 400 дней вперёд,
    # и запуск, опоздавший из-за перезапуска сервиса, терять нельзя (SPEC 5.4).
    assert daily.misfire_grace_time == DAILY_MISFIRE_GRACE_SECONDS
