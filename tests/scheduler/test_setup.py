"""Сборка APScheduler: два задания с параметрами из SPEC 5.4.

Планировщик не запускается: проверяется только конфигурация заданий — сами
функции тика и материализации закрыты тестами рядом.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from tests.core.fakes import FakeReminderRepo

from kinday.core.models import Reminder, ReminderStatus
from kinday.scheduler.setup import (
    DAILY_MATERIALIZATION_HOUR_UTC,
    DAILY_MISFIRE_GRACE_SECONDS,
    TICK_INTERVAL_SECONDS,
    TICK_MISFIRE_GRACE_SECONDS,
    build_scheduler,
    start_scheduler,
)


async def _tick() -> None: ...


async def _materialize() -> None: ...


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


@dataclass
class _RecordingReminderRepo(FakeReminderRepo):
    """Фейковый репозиторий напоминаний, отмечающий в общем журнале сам факт вызова."""

    order: list[str] = field(default_factory=list)

    async def fail_all_sending(self, at: datetime) -> int:
        self.order.append("fail_all_sending")
        return await super().fail_all_sending(at)


@pytest.mark.asyncio
async def test_start_scheduler_closes_stuck_sending_before_starting_jobs() -> None:
    """Критерий 9: строки, застрявшие в sending, закрываются до запуска заданий.

    Порядок важен: тик, успевший стартовать раньше, разбирал бы очередь, пока
    строки прошлого запуска ещё висят в sending, и в журнал они попали бы после
    первых отправок. Планировщик здесь фейковый — настоящий APScheduler в тестах
    не запускается.
    """
    order: list[str] = []

    class _Scheduler:
        def start(self) -> None:
            order.append("start")

    reminder_repo = _RecordingReminderRepo(order=order)
    reminder_repo.reminders.append(
        Reminder(
            id=1,
            event_id=1,
            person_id=1,
            offset_days=0,
            occurrence_date=date(2027, 2, 14),
            due_at_utc=datetime(2027, 2, 14, 6, tzinfo=UTC),
            status=ReminderStatus.SENDING,
        )
    )

    await start_scheduler(
        _Scheduler(), _FixedClock(datetime(2027, 2, 15, tzinfo=UTC)), reminder_repo
    )

    assert order == ["fail_all_sending", "start"]
    assert [r.status for r in reminder_repo.reminders] == [ReminderStatus.FAILED]


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
