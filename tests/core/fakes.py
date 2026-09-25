"""Простые фейковые in-memory реализации портов ядра — без SQLite.

Общие для тестов разных сценариев (см. PLAN.md, этапы 3a/3b): каждый тестовый
модуль собирает нужный ему набор через `build_repos()` и передаёт репозитории
напрямую в функции `core/services.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from kinday.core.models import Account, Event, Family, Membership, Person, Relation, Reminder


class FixedClock:
    """Простая подмена Clock с зафиксированным now(), без реальных часов."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


@dataclass
class FakeFamilyRepo:
    families: dict[int, Family] = field(default_factory=dict)
    _next_id: int = 1

    async def get(self, family_id: int) -> Family:
        return self.families[family_id]

    async def create(self, family: Family) -> Family:
        family.id = self._next_id
        self._next_id += 1
        self.families[family.id] = family
        return family


@dataclass
class FakePersonRepo:
    people: dict[int, Person] = field(default_factory=dict)
    _next_id: int = 1

    async def get(self, person_id: int) -> Person:
        return self.people[person_id]

    async def create(self, person: Person) -> Person:
        person.id = self._next_id
        self._next_id += 1
        self.people[person.id] = person
        return person

    async def update(self, person: Person) -> Person:
        self.people[person.id] = person
        return person

    async def delete(self, person_id: int) -> None:
        del self.people[person_id]

    async def list_by_family(self, family_id: int) -> list[Person]:
        return [p for p in self.people.values() if p.family_id == family_id]


@dataclass
class FakeAccountRepo:
    accounts: dict[int, Account] = field(default_factory=dict)
    _next_id: int = 1

    async def get(self, account_id: int) -> Account:
        return self.accounts[account_id]

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> Account | None:
        return next(
            (a for a in self.accounts.values() if a.telegram_user_id == telegram_user_id), None
        )

    async def save(self, account: Account) -> Account:
        if account.id == 0:
            account.id = self._next_id
            self._next_id += 1
        self.accounts[account.id] = account
        return account


@dataclass
class FakeMembershipRepo:
    memberships: list[Membership] = field(default_factory=list)

    async def get_by_account_and_family(self, account_id: int, family_id: int) -> Membership | None:
        return next(
            (
                m
                for m in self.memberships
                if m.account_id == account_id and m.family_id == family_id
            ),
            None,
        )

    async def list_by_account(self, account_id: int) -> list[Membership]:
        return [m for m in self.memberships if m.account_id == account_id]

    async def list_by_family(self, family_id: int) -> list[Membership]:
        return [m for m in self.memberships if m.family_id == family_id]

    async def get_by_person(self, person_id: int) -> Membership | None:
        return next((m for m in self.memberships if m.person_id == person_id), None)

    async def create(self, membership: Membership) -> Membership:
        self.memberships.append(membership)
        return membership

    async def delete(self, person_id: int) -> None:
        self.memberships = [m for m in self.memberships if m.person_id != person_id]


@dataclass
class FakeEventRepo:
    events: dict[int, Event] = field(default_factory=dict)
    _next_id: int = 1

    async def get(self, event_id: int) -> Event:
        return self.events[event_id]

    async def create(self, event: Event) -> Event:
        event.id = self._next_id
        self._next_id += 1
        self.events[event.id] = event
        return event


@dataclass
class FakeRelationRepo:
    relations: dict[int, list[Relation]] = field(default_factory=dict)

    async def list_by_family(self, family_id: int) -> list[Relation]:
        return list(self.relations.get(family_id, []))

    async def add(self, family_id: int, relation: Relation) -> None:
        self.relations.setdefault(family_id, []).append(relation)

    async def remove(self, family_id: int, relation: Relation) -> None:
        self.relations[family_id].remove(relation)


@dataclass
class FakeReminderRepo:
    reminders: list[Reminder] = field(default_factory=list)

    async def claim_due(self, at: datetime, limit: int) -> list[Reminder]:
        raise NotImplementedError

    async def mark_sent(self, reminder_id: int, at: datetime) -> None:
        raise NotImplementedError

    async def mark_missed(self, reminder_id: int, at: datetime) -> None:
        raise NotImplementedError

    async def mark_failed(self, reminder_id: int, at: datetime) -> None:
        raise NotImplementedError

    async def release(self, reminder_id: int) -> None:
        raise NotImplementedError

    async def add_many(self, reminders: list[Reminder]) -> None:
        self.reminders.extend(reminders)

    async def delete_future_pending_for_event(self, event_id: int, after: datetime) -> None:
        raise NotImplementedError

    async def delete_future_pending_for_person(self, person_id: int, after: datetime) -> None:
        raise NotImplementedError

    async def fail_all_sending(self) -> None:
        raise NotImplementedError


@dataclass
class Repos:
    family: FakeFamilyRepo
    person: FakePersonRepo
    account: FakeAccountRepo
    membership: FakeMembershipRepo
    event: FakeEventRepo
    relation: FakeRelationRepo
    reminder: FakeReminderRepo


def build_repos() -> Repos:
    return Repos(
        family=FakeFamilyRepo(),
        person=FakePersonRepo(),
        account=FakeAccountRepo(),
        membership=FakeMembershipRepo(),
        event=FakeEventRepo(),
        relation=FakeRelationRepo(),
        reminder=FakeReminderRepo(),
    )
