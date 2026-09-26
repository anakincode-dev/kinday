"""SqliteReminderRepo: вставка без дублей, claim → send → mark, зачистка будущих строк.

Только SQLite: проверяется то, чего у фейков нет — уникальный индекс SPEC 5.3,
атомарный claim SPEC 5.5 и статусы напоминаний.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from tests.core.fakes import FixedClock
from tests.scenario_repos import World
from tests.scenarios import add_person_to, create_family_for

from kinday.core.models import Reminder, ReminderStatus
from kinday.core.relations import RelationKind
from kinday.storage.codecs import dump_datetime
from kinday.storage.db import Database

CLOCK = FixedClock(datetime(2027, 1, 1, tzinfo=UTC))


async def _reminders_of_one_event(world: World) -> list[Reminder]:
    """Семья с отцом: три напоминания владельцу о дне рождения отца."""
    family = await create_family_for(
        world, clock=CLOCK, telegram_user_id=100, name="Антон", birth_date=date(1990, 6, 15)
    )
    [anton] = await world.person.list_by_family(family.id)
    [membership] = await world.membership.list_by_family(family.id)
    await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=membership.account_id,
        family_id=family.id,
        name="Пётр",
        birth_date=date(1980, 3, 1),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=anton.id,
    )
    return sorted(await world.read_reminders(), key=lambda r: r.offset_days)


@pytest.mark.asyncio
async def test_add_many_does_not_duplicate_already_sent_reminder(sqlite_world: World) -> None:
    """PLAN, этап 4: после перематериализации отправленное напоминание не создаётся заново."""
    reminders = await _reminders_of_one_event(sqlite_world)
    sent = reminders[0]
    await sqlite_world.reminder.mark_sent(sent.id, datetime(2027, 3, 1, 6, tzinfo=UTC))

    again = replace(
        sent, id=0, due_at_utc=datetime(2027, 3, 1, 12, tzinfo=UTC), status=ReminderStatus.PENDING
    )
    await sqlite_world.reminder.add_many([again])

    stored = await sqlite_world.read_reminders()
    assert len(stored) == len(reminders)
    survivor = next(r for r in stored if r.id == sent.id)
    assert survivor.status == ReminderStatus.SENT
    assert survivor.due_at_utc == sent.due_at_utc


@pytest.mark.asyncio
async def test_add_many_ignores_duplicates_inside_one_batch(sqlite_world: World) -> None:
    """Уникальный индекс SPEC 5.3 работает и внутри одной пачки.

    Ключ берётся новый, которого в таблице ещё нет: иначе обе копии конфликтовали
    бы с уже лежащей строкой и тест прошёл бы, даже не сравнив копии между собой.
    """
    reminders = await _reminders_of_one_event(sqlite_world)
    fresh = replace(
        reminders[0], id=0, offset_days=30, due_at_utc=datetime(2027, 1, 30, 6, tzinfo=UTC)
    )

    await sqlite_world.reminder.add_many([fresh, fresh])

    stored = await sqlite_world.read_reminders()
    assert len(stored) == len(reminders) + 1
    assert len([r for r in stored if r.offset_days == 30]) == 1


@pytest.mark.asyncio
async def test_claim_due_takes_each_row_once(sqlite_world: World) -> None:
    """SPEC 5.5: строку забирает только тот, кто увидел её pending — повторный claim пуст."""
    reminders = await _reminders_of_one_event(sqlite_world)
    at = max(r.due_at_utc for r in reminders)

    claimed = await sqlite_world.reminder.claim_due(at, 10)
    assert {r.id for r in claimed} == {r.id for r in reminders}
    assert {r.status for r in claimed} == {ReminderStatus.SENDING}

    assert await sqlite_world.reminder.claim_due(at, 10) == []


@pytest.mark.asyncio
async def test_claim_due_respects_time_and_limit(sqlite_world: World) -> None:
    """Берутся только наступившие строки, не больше limit, в порядке срока."""
    reminders = await _reminders_of_one_event(sqlite_world)
    earliest = min(reminders, key=lambda r: r.due_at_utc)

    claimed = await sqlite_world.reminder.claim_due(earliest.due_at_utc, 10)
    assert [r.id for r in claimed] == [earliest.id]

    rest = sorted((r for r in reminders if r.id != earliest.id), key=lambda r: r.due_at_utc)
    limited = await sqlite_world.reminder.claim_due(max(r.due_at_utc for r in reminders), 1)
    assert [r.id for r in limited] == [rest[0].id]


@pytest.mark.asyncio
async def test_marks_record_status_and_moment(sqlite_world: World, database: Database) -> None:
    """mark_sent/mark_missed/mark_failed ставят статус и запоминают момент перехода."""
    sent, missed, failed = await _reminders_of_one_event(sqlite_world)
    at = datetime(2027, 2, 22, 6, tzinfo=UTC)

    await sqlite_world.reminder.mark_sent(sent.id, at)
    await sqlite_world.reminder.mark_missed(missed.id, at)
    await sqlite_world.reminder.mark_failed(failed.id, at)

    stored = {r.id: r for r in await sqlite_world.read_reminders()}
    assert stored[sent.id].status == ReminderStatus.SENT
    assert stored[sent.id].sent_at == at
    assert stored[missed.id].status == ReminderStatus.MISSED
    assert stored[missed.id].sent_at is None
    assert stored[failed.id].status == ReminderStatus.FAILED
    assert stored[failed.id].sent_at is None

    rows = await database.run(
        lambda c: c.execute("SELECT id, status_changed_at FROM reminders").fetchall()
    )
    assert all(row["status_changed_at"] is not None for row in rows)


@pytest.mark.asyncio
async def test_release_returns_row_to_pending_and_counts_attempt(sqlite_world: World) -> None:
    """SPEC 5.5: сетевая ошибка возвращает строку в pending и увеличивает attempts."""
    reminders = await _reminders_of_one_event(sqlite_world)
    [claimed] = await sqlite_world.reminder.claim_due(min(r.due_at_utc for r in reminders), 1)

    await sqlite_world.reminder.release(claimed.id)

    stored = {r.id: r for r in await sqlite_world.read_reminders()}
    assert stored[claimed.id].status == ReminderStatus.PENDING
    assert stored[claimed.id].attempts == 1

    again = await sqlite_world.reminder.claim_due(claimed.due_at_utc, 1)
    assert [r.id for r in again] == [claimed.id]
    assert again[0].attempts == 1


@pytest.mark.asyncio
async def test_fail_all_sending_closes_rows_left_by_a_crash(
    sqlite_world: World, database: Database
) -> None:
    """SPEC 5.5: при старте все строки sending переводятся в failed, повтора отправки нет."""
    reminders = await _reminders_of_one_event(sqlite_world)
    claimed = await sqlite_world.reminder.claim_due(max(r.due_at_utc for r in reminders), 10)
    restart_at = datetime(2027, 3, 2, 5, tzinfo=UTC)

    await sqlite_world.reminder.fail_all_sending(restart_at)

    stored = await sqlite_world.read_reminders()
    assert {r.status for r in stored} == {ReminderStatus.FAILED}
    assert len(claimed) == len(stored)
    rows = await database.run(
        lambda c: c.execute("SELECT DISTINCT status_changed_at FROM reminders").fetchall()
    )
    assert [row["status_changed_at"] for row in rows] == [dump_datetime(restart_at)]


@pytest.mark.asyncio
async def test_delete_future_pending_keeps_history(sqlite_world: World) -> None:
    """SPEC 5.6: перематериализация не переписывает прошлое — трогает только будущие pending."""
    reminders = await _reminders_of_one_event(sqlite_world)
    ordered = sorted(reminders, key=lambda r: r.due_at_utc)
    await sqlite_world.reminder.mark_sent(ordered[0].id, ordered[0].due_at_utc)

    await sqlite_world.reminder.delete_future_pending_for_event(
        ordered[0].event_id, ordered[1].due_at_utc
    )

    stored = await sqlite_world.read_reminders()
    assert {r.id for r in stored} == {ordered[0].id}


@pytest.mark.asyncio
async def test_delete_variants_narrow_to_person_and_event(sqlite_world: World) -> None:
    """Узкие удаления: по паре «событие и человек», по человеку, по событию целиком."""
    reminders = await _reminders_of_one_event(sqlite_world)
    event_id = reminders[0].event_id
    person_id = reminders[0].person_id
    past = datetime(2026, 1, 1, tzinfo=UTC)

    await sqlite_world.reminder.delete_future_pending_for_event_and_person(
        event_id, person_id + 1000, past
    )
    assert len(await sqlite_world.read_reminders()) == len(reminders)

    await sqlite_world.reminder.delete_future_pending_for_person(person_id, past)
    assert await sqlite_world.read_reminders() == []

    await sqlite_world.reminder.add_many([replace(r, id=0) for r in reminders])
    await sqlite_world.reminder.delete_all_for_event(event_id)
    assert await sqlite_world.read_reminders() == []
