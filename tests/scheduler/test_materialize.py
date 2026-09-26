"""Суточная материализация на двух хранилищах: фейки и настоящий SQLite.

PLAN.md, этап 5: задание достраивает напоминания на 400 дней вперёд для всех
событий и всех людей с аккаунтами. Это страховка, а не основной путь (SPEC 5.4),
поэтому проверяется главное: горизонт сдвигается, повторный запуск дублей не
создаёт, переопределения по событию учитываются.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from tests.core.fakes import FixedClock
from tests.scenario_repos import World
from tests.scenarios import (
    accept_invite_in,
    add_person_to,
    create_family_for,
    issue_invite_in,
    set_override_in,
)

from kinday.core.models import EventKind, ReminderStatus
from kinday.core.ports import Clock
from kinday.core.relations import RelationKind
from kinday.scheduler.materialize import run_daily_materialization

ANTON_BIRTH = date(1990, 6, 15)
PETR_BIRTH = date(1980, 3, 1)

START = datetime(2027, 2, 15, tzinfo=UTC)
# Через год: наступление 1 марта 2029 года попадает в горизонт только отсюда.
NEXT_YEAR = datetime(2028, 3, 2, tzinfo=UTC)


async def _family_with_father(world: World, clock: Clock) -> tuple[int, int, int]:
    """Семья Антона и его отец Пётр. Возвращает id семьи, аккаунта Антона и Петра."""
    family = await create_family_for(
        world, clock=clock, telegram_user_id=100, name="Антон", birth_date=ANTON_BIRTH
    )
    [anton] = await world.person.list_by_family(family.id)
    [membership] = await world.membership.list_by_family(family.id)
    petr = await add_person_to(
        world,
        clock=clock,
        acting_account_id=membership.account_id,
        family_id=family.id,
        name="Пётр",
        birth_date=PETR_BIRTH,
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=anton.id,
    )
    return family.id, membership.account_id, petr.id


async def _materialize(world: World, clock: Clock) -> None:
    await run_daily_materialization(
        clock,
        world.account,
        world.event,
        world.membership,
        world.override,
        world.reminder,
        world.uow,
    )


async def _occurrences(world: World) -> list[date]:
    return sorted({r.occurrence_date for r in await world.read_reminders()})


@pytest.mark.asyncio
async def test_daily_job_extends_horizon_and_is_idempotent(world: World) -> None:
    """Через год задание достраивает следующее наступление, повторный запуск дублей не создаёт."""
    clock = FixedClock(START)
    await _family_with_father(world, clock)

    assert await _occurrences(world) == [date(2027, 3, 1), date(2028, 3, 1)]
    before = len(await world.read_reminders())

    clock.move_to(NEXT_YEAR)
    await _materialize(world, clock)

    assert await _occurrences(world) == [date(2027, 3, 1), date(2028, 3, 1), date(2029, 3, 1)]
    after = len(await world.read_reminders())
    assert after == before + 3  # три смещения по умолчанию

    await _materialize(world, clock)

    assert len(await world.read_reminders()) == after


@pytest.mark.asyncio
async def test_daily_job_does_not_revive_sent_reminders(world: World) -> None:
    """Отправленное напоминание задание заново не создаёт: прошлое не переписывается."""
    clock = FixedClock(START)
    await _family_with_father(world, clock)

    claimed = await world.reminder.claim_due(datetime(2027, 3, 1, 6, tzinfo=UTC), 10)
    for reminder in claimed:
        await world.reminder.mark_sent(reminder.id, datetime(2027, 3, 1, 6, tzinfo=UTC))
    sent_count = len([r for r in await world.read_reminders() if r.status == ReminderStatus.SENT])
    assert sent_count == 3

    await _materialize(world, clock)

    statuses = [r.status for r in await world.read_reminders()]
    assert statuses.count(ReminderStatus.SENT) == sent_count
    assert len(statuses) == 6


@pytest.mark.asyncio
async def test_daily_job_builds_for_every_account_in_family(world: World) -> None:
    """Задание достраивает напоминания всем аккаунтам семьи, а не только первому.

    Пётр принимает приглашение и получает свой аккаунт: теперь у семьи два
    получателя, и в новом горизонте должны появиться и напоминания Антону
    (о дне рождения Петра), и напоминания Петру (о дне рождения Антона).
    """
    clock = FixedClock(START)
    family_id, account_id, petr_id = await _family_with_father(world, clock)
    invite = await issue_invite_in(
        world, clock=clock, acting_account_id=account_id, person_id=petr_id
    )
    await accept_invite_in(world, clock=clock, code=invite.code, telegram_user_id=200)
    [anton] = [p for p in await world.person.list_by_family(family_id) if p.id != petr_id]

    clock.move_to(NEXT_YEAR)
    await _materialize(world, clock)

    fresh = [r for r in await world.read_reminders() if r.occurrence_date > NEXT_YEAR.date()]
    assert {r.occurrence_date for r in fresh} == {date(2028, 6, 15), date(2029, 3, 1)}
    # Получатель дня рождения Петра — Антон, и наоборот: каждый аккаунт получил своё.
    for occurrence, recipient_id in ((date(2029, 3, 1), anton.id), (date(2028, 6, 15), petr_id)):
        recipients = {r.person_id for r in fresh if r.occurrence_date == occurrence}
        assert recipients == {recipient_id}


@pytest.mark.asyncio
async def test_daily_job_respects_override(world: World) -> None:
    """Переопределение по событию действует и в суточной материализации (SPEC 5.6)."""
    clock = FixedClock(START)
    _, account_id, petr_id = await _family_with_father(world, clock)
    [birthday] = [
        event
        for event in await world.event.list_by_person(petr_id)
        if event.kind == EventKind.BIRTHDAY
    ]
    await set_override_in(
        world,
        clock=clock,
        account_id=account_id,
        event_id=birthday.id,
        offsets_days=(3,),
        time_of_day=time(8, 0),
    )

    clock.move_to(NEXT_YEAR)
    await _materialize(world, clock)

    new_year = [r for r in await world.read_reminders() if r.occurrence_date == date(2029, 3, 1)]
    assert [r.offset_days for r in new_year] == [3]
    assert new_year[0].due_at_utc == datetime(2029, 2, 26, 5, tzinfo=UTC)
