"""UnitOfWork: одна транзакция на сценарий, половинчатого результата не остаётся.

PLAN.md, этап 4: ошибка на середине `add_person` не оставляет в базе ни
человека, ни рёбер. Сценарий пишет в несколько таблиц (человек, рёбра,
событие, напоминания), и падение между записями недопустимо.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from tests.core.fakes import FixedClock
from tests.scenario_repos import World
from tests.scenarios import add_person_to, create_family_for

from kinday.core.models import Reminder
from kinday.core.ports import ReminderRepo
from kinday.core.relations import RelationKind
from kinday.storage.db import Database

CLOCK = FixedClock(datetime(2027, 1, 1, tzinfo=UTC))


class BrokenAddMany:
    """ReminderRepo, падающий на последнем шаге сценария — после человека и рёбер."""

    def __init__(self, wrapped: ReminderRepo) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    async def add_many(self, reminders: list[Reminder]) -> None:
        raise RuntimeError("хранилище напоминаний недоступно")


@pytest.mark.asyncio
async def test_failed_add_person_leaves_no_person_and_no_relations(
    sqlite_world: World, database: Database
) -> None:
    family = await create_family_for(
        sqlite_world, clock=CLOCK, telegram_user_id=100, name="Антон", birth_date=date(1990, 6, 15)
    )
    [anton] = await sqlite_world.person.list_by_family(family.id)
    [membership] = await sqlite_world.membership.list_by_family(family.id)
    before = await sqlite_world.snapshot(family.id)

    sqlite_world.reminder = BrokenAddMany(sqlite_world.reminder)  # ty: ignore[invalid-assignment]
    with pytest.raises(RuntimeError, match="недоступно"):
        await add_person_to(
            sqlite_world,
            clock=CLOCK,
            acting_account_id=membership.account_id,
            family_id=family.id,
            name="Пётр",
            birth_date=date(1980, 3, 1),
            relation_kind=RelationKind.FATHER,
            relative_to_person_id=anton.id,
        )

    rows = await database.run(
        lambda c: c.execute("SELECT COUNT(*) AS n FROM people").fetchone()["n"]
    )
    assert rows == 1
    after = await sqlite_world.snapshot(family.id)
    assert after == before


@pytest.mark.asyncio
async def test_successful_scenario_is_committed(sqlite_world: World, database: Database) -> None:
    """После выхода из транзакции данные видны обычным запросом, транзакция закрыта."""
    family = await create_family_for(
        sqlite_world, clock=CLOCK, telegram_user_id=100, name="Антон", birth_date=date(1990, 6, 15)
    )

    in_transaction = await database.run(lambda c: c.in_transaction)
    assert in_transaction is False
    names = await database.run(
        lambda c: [row["name"] for row in c.execute("SELECT name FROM people").fetchall()]
    )
    assert names == ["Антон"]
    assert (await sqlite_world.family.get(family.id)).name == "Семья Антон"
