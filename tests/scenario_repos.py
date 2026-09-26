"""Один набор портов на двух бэкендах: in-memory фейки и настоящий SQLite.

PLAN.md, этап 4: сценарные тесты гоняются дважды — на фейках из
`tests/core/fakes.py` и на адаптерах `kinday.storage.repos`. Тесты видят только
протоколы `core/ports.py` плюс `World.snapshot`, поэтому один и тот же
сценарий проверяет оба хранилища и любое расхождение между ними всплывает.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime

from tests.core import fakes

from kinday.core.models import (
    Account,
    Event,
    Invite,
    Membership,
    Person,
    Relation,
    Reminder,
    ReminderStatus,
)
from kinday.core.ports import (
    AccountRepo,
    EventRepo,
    FamilyRepo,
    InviteRepo,
    MembershipRepo,
    PersonRepo,
    RelationRepo,
    ReminderOverrideRepo,
    ReminderRepo,
    UnitOfWork,
)
from kinday.storage.codecs import load_date, load_datetime
from kinday.storage.db import Database
from kinday.storage.repos import (
    SqliteAccountRepo,
    SqliteEventRepo,
    SqliteFamilyRepo,
    SqliteInviteRepo,
    SqliteMembershipRepo,
    SqlitePersonRepo,
    SqliteRelationRepo,
    SqliteReminderOverrideRepo,
    SqliteReminderRepo,
    SqliteUnitOfWork,
)


def _reminder_key(reminder: Reminder) -> tuple[int, int, date, int]:
    """Порядок сравнения: ключ уникального индекса из SPEC 5.3.

    Список напоминаний в двух хранилищах приходит в разном порядке (в SQLite —
    по rowid, в фейке — по порядку вставки), а сравнивать нужно содержимое.
    """
    return (
        reminder.event_id,
        reminder.person_id,
        reminder.occurrence_date,
        reminder.offset_days,
    )


@dataclass(frozen=True)
class Snapshot:
    """Состояние одной семьи, прочитанное через порты, для сравнения в тестах."""

    people: list[Person]
    memberships: list[Membership]
    accounts: list[Account]
    events: list[Event]
    relations: list[Relation]
    reminders: list[Reminder]
    invites: list[Invite]


@dataclass
class World:
    """Набор портов одного бэкенда плюс чтение состояния для проверок.

    `read_reminders` — единственное, что нельзя получить через порты:
    `ReminderRepo` умеет только забирать напоминания к отправке (`claim_due`
    меняет статус), а тестам нужен неразрушающий список. Поэтому каждый бэкенд
    отдаёт свою реализацию чтения, а сами тесты об устройстве хранилища
    по-прежнему ничего не знают.
    """

    family: FamilyRepo
    person: PersonRepo
    account: AccountRepo
    membership: MembershipRepo
    event: EventRepo
    relation: RelationRepo
    reminder: ReminderRepo
    invite: InviteRepo
    override: ReminderOverrideRepo
    uow: UnitOfWork
    read_reminders: Callable[[], Awaitable[list[Reminder]]]

    async def snapshot(self, family_id: int) -> Snapshot:
        people = sorted(await self.person.list_by_family(family_id), key=lambda p: p.id)
        memberships = sorted(
            await self.membership.list_by_family(family_id), key=lambda m: m.person_id
        )
        accounts = [await self.account.get(m.account_id) for m in memberships]
        events = sorted(await self.event.list_by_family(family_id), key=lambda e: e.id)
        relations = await self.relation.list_by_family(family_id)
        invites: list[Invite] = []
        for person in people:
            invites.extend(await self.invite.list_by_person(person.id))
        event_ids = {event.id for event in events}
        reminders = [r for r in await self.read_reminders() if r.event_id in event_ids]
        return Snapshot(
            people=people,
            memberships=memberships,
            accounts=accounts,
            events=events,
            relations=relations,
            reminders=sorted(reminders, key=_reminder_key),
            invites=sorted(invites, key=lambda i: i.id),
        )


def build_memory_world() -> World:
    repos = fakes.build_repos()

    async def read_reminders() -> list[Reminder]:
        return sorted(repos.reminder.reminders, key=_reminder_key)

    return World(
        family=repos.family,
        person=repos.person,
        account=repos.account,
        membership=repos.membership,
        event=repos.event,
        relation=repos.relation,
        reminder=repos.reminder,
        invite=repos.invite,
        override=repos.override,
        uow=repos.uow,
        read_reminders=read_reminders,
    )


def reminder_from_row(row: sqlite3.Row) -> Reminder:
    """Разбор строки `reminders` — тесты читают таблицу в обход портов."""
    sent_at: datetime | None = None
    if row["sent_at"] is not None:
        sent_at = load_datetime(row["sent_at"])
    return Reminder(
        id=int(row["id"]),
        event_id=int(row["event_id"]),
        person_id=int(row["person_id"]),
        offset_days=int(row["offset_days"]),
        occurrence_date=load_date(row["occurrence_date"]),
        due_at_utc=load_datetime(row["due_at_utc"]),
        status=ReminderStatus(row["status"]),
        attempts=int(row["attempts"]),
        sent_at=sent_at,
    )


def build_sqlite_world(database: Database) -> World:
    async def read_reminders() -> list[Reminder]:
        rows = await database.run(lambda c: c.execute("SELECT * FROM reminders").fetchall())
        return sorted((reminder_from_row(row) for row in rows), key=_reminder_key)

    return World(
        family=SqliteFamilyRepo(database),
        person=SqlitePersonRepo(database),
        account=SqliteAccountRepo(database),
        membership=SqliteMembershipRepo(database),
        event=SqliteEventRepo(database),
        relation=SqliteRelationRepo(database),
        reminder=SqliteReminderRepo(database),
        invite=SqliteInviteRepo(database),
        override=SqliteReminderOverrideRepo(database),
        uow=SqliteUnitOfWork(database),
        read_reminders=read_reminders,
    )
