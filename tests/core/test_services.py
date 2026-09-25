"""Тесты сценариев ядра: create_family, add_person, add_event (этап 3a).

Фейковые in-memory реализации портов вместо хранилища, как предписывает PLAN.md.
Критерии приёмки 14 (напоминание сразу при добавлении человека), 15 (не себе),
16 (один аккаунт в двух семьях получает напоминания обеих).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time

import pytest

from kinday.core.models import (
    Account,
    Event,
    Family,
    Gender,
    Membership,
    ParentOf,
    Person,
    Relation,
    Reminder,
    SpouseOf,
)
from kinday.core.relations import RelationKind, SiblingsQuestionRequired
from kinday.core.services import (
    DEFAULT_OFFSETS_DAYS,
    NotFamilyOwner,
    add_event,
    add_person,
    create_family,
)


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


def _repos() -> Repos:
    return Repos(
        family=FakeFamilyRepo(),
        person=FakePersonRepo(),
        account=FakeAccountRepo(),
        membership=FakeMembershipRepo(),
        event=FakeEventRepo(),
        relation=FakeRelationRepo(),
        reminder=FakeReminderRepo(),
    )


CLOCK = FixedClock(datetime(2027, 1, 1, tzinfo=UTC))


async def _create_family(
    repos: Repos,
    *,
    telegram_user_id: int,
    name: str,
    birth_date: date,
    timezone: str = "Europe/Moscow",
) -> Family:
    return await create_family(
        telegram_user_id,
        name,
        Gender.MALE,
        birth_date,
        timezone,
        repos.family,
        repos.person,
        repos.account,
        repos.membership,
        repos.event,
        repos.reminder,
        CLOCK,
    )


async def _add_person(
    repos: Repos,
    *,
    acting_account_id: int,
    family_id: int,
    name: str,
    gender: Gender,
    birth_date: date,
    relation_kind: RelationKind,
    relative_to_person_id: int,
    also_parent_of_siblings: bool | None = None,
) -> Person:
    return await add_person(
        acting_account_id,
        family_id,
        name,
        gender,
        birth_date,
        relation_kind,
        relative_to_person_id,
        also_parent_of_siblings,
        repos.account,
        repos.event,
        repos.family,
        repos.person,
        repos.relation,
        repos.membership,
        repos.reminder,
        CLOCK,
    )


@pytest.mark.asyncio
async def test_create_family_creates_owner_person_and_membership_without_self_reminder() -> None:
    """Критерий приёмки 15: владелец не получает напоминание о своём дне рождения."""
    repos = _repos()

    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )

    assert family.id != 0
    [owner_person] = repos.person.people.values()
    assert owner_person.name == "Антон"
    assert owner_person.family_id == family.id

    [account] = repos.account.accounts.values()
    assert account.telegram_user_id == 111
    assert account.current_family_id == family.id

    [membership] = repos.membership.memberships
    assert membership.account_id == account.id
    assert membership.family_id == family.id
    assert membership.person_id == owner_person.id

    [event] = repos.event.events.values()
    assert event.title == "День рождения"
    assert event.person_id == owner_person.id

    assert repos.reminder.reminders == []


@pytest.mark.asyncio
async def test_create_family_reuses_existing_account_for_second_family() -> None:
    """Один Telegram-аккаунт может создать/состоять в нескольких семьях (SPEC 2.1)."""
    repos = _repos()

    family1 = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    family2 = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )

    assert family1.id != family2.id
    assert len(repos.account.accounts) == 1
    assert family1.owner_account_id == family2.owner_account_id

    [account] = repos.account.accounts.values()
    assert account.current_family_id == family1.id


@pytest.mark.asyncio
async def test_add_person_materializes_reminder_immediately() -> None:
    """Критерий приёмки 14: добавление человека сразу создаёт напоминания о его дне рождения."""
    repos = _repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()

    father = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Пётр",
        gender=Gender.MALE,
        birth_date=date(1960, 3, 1),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=owner_person.id,
    )

    assert len(repos.reminder.reminders) == len(DEFAULT_OFFSETS_DAYS)
    assert {r.person_id for r in repos.reminder.reminders} == {owner_person.id}
    assert {r.event_id for r in repos.reminder.reminders} == {
        e.id for e in repos.event.events.values() if e.person_id == father.id
    }


@pytest.mark.asyncio
async def test_add_person_father_creates_parent_edge() -> None:
    """Критерий приёмки 17 на уровне сценария: связь «отец» даёт ребро parent_of."""
    repos = _repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()

    father = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Пётр",
        gender=Gender.MALE,
        birth_date=date(1960, 3, 1),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=owner_person.id,
    )

    [relation] = repos.relation.relations[family.id]
    assert isinstance(relation, ParentOf)
    assert relation.parent_id == father.id
    assert relation.child_id == owner_person.id


@pytest.mark.asyncio
async def test_add_person_sibling_without_known_parents_creates_placeholder() -> None:
    """Критерий приёмки 18: сестра без известных родителей создаёт заглушку."""
    repos = _repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()

    sister = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=date(1992, 4, 4),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=owner_person.id,
    )

    placeholders = [p for p in repos.person.people.values() if p.is_placeholder]
    assert len(placeholders) == 1
    [placeholder] = placeholders

    relations = repos.relation.relations[family.id]
    assert all(isinstance(r, ParentOf) for r in relations)
    parent_edges = [r for r in relations if isinstance(r, ParentOf)]
    assert {r.child_id for r in parent_edges} == {owner_person.id, sister.id}
    assert all(r.parent_id == placeholder.id for r in parent_edges)


@pytest.mark.asyncio
async def test_add_person_father_merges_with_placeholder() -> None:
    """Критерий приёмки 19: первый настоящий родитель сливается с заглушкой.

    Заглушка (созданная ранее для сестры без известных родителей) должна
    реально исчезнуть из хранилища людей, а её рёбра — перейти к отцу.
    """
    repos = _repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()

    sister = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=date(1992, 4, 4),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=owner_person.id,
    )
    [placeholder] = [p for p in repos.person.people.values() if p.is_placeholder]

    father = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Пётр",
        gender=Gender.MALE,
        birth_date=date(1960, 3, 1),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=owner_person.id,
    )

    assert placeholder.id not in repos.person.people
    parent_edges = [r for r in repos.relation.relations[family.id] if isinstance(r, ParentOf)]
    assert {r.child_id for r in parent_edges if r.parent_id == father.id} == {
        owner_person.id,
        sister.id,
    }
    assert all(r.parent_id != placeholder.id for r in parent_edges)


@pytest.mark.asyncio
async def test_add_person_rejects_relative_from_another_family() -> None:
    """SPEC 2: независимые семьи не видят данные друг друга.

    relative_to_person_id, указывающий на человека из чужой семьи, отклоняется,
    иначе через add_person можно было бы протянуть ребро между семьями.
    """
    repos = _repos()
    family1 = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    family2 = await _create_family(
        repos, telegram_user_id=222, name="Борис", birth_date=date(1985, 8, 20)
    )
    [owner1_account] = [a for a in repos.account.accounts.values() if a.telegram_user_id == 111]
    [boris] = [p for p in repos.person.people.values() if p.family_id == family2.id]
    people_before = set(repos.person.people)
    relations_before = dict(repos.relation.relations)

    with pytest.raises(ValueError):
        await _add_person(
            repos,
            acting_account_id=owner1_account.id,
            family_id=family1.id,
            name="Пётр",
            gender=Gender.MALE,
            birth_date=date(1960, 3, 1),
            relation_kind=RelationKind.FATHER,
            relative_to_person_id=boris.id,
        )

    assert set(repos.person.people) == people_before
    assert repos.relation.relations == relations_before


@pytest.mark.asyncio
async def test_add_person_second_parent_asks_about_siblings_and_keeps_storage_clean() -> None:
    """Критерий приёмки 20: второй родитель требует ответа про братьев/сестёр.

    Отклонённая (без ответа) попытка не должна оставлять в хранилище лишнего человека.
    """
    repos = _repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()

    father = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Пётр",
        gender=Gender.MALE,
        birth_date=date(1960, 3, 1),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=owner_person.id,
    )
    brother = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Дима",
        gender=Gender.MALE,
        birth_date=date(1993, 5, 5),
        relation_kind=RelationKind.BROTHER,
        relative_to_person_id=owner_person.id,
    )
    people_before = set(repos.person.people)

    with pytest.raises(SiblingsQuestionRequired) as exc_info:
        await _add_person(
            repos,
            acting_account_id=owner_account.id,
            family_id=family.id,
            name="Мария",
            gender=Gender.FEMALE,
            birth_date=date(1962, 7, 7),
            relation_kind=RelationKind.MOTHER,
            relative_to_person_id=owner_person.id,
            also_parent_of_siblings=None,
        )
    assert exc_info.value.sibling_ids == [brother.id]
    assert set(repos.person.people) == people_before

    mother = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Мария",
        gender=Gender.FEMALE,
        birth_date=date(1962, 7, 7),
        relation_kind=RelationKind.MOTHER,
        relative_to_person_id=owner_person.id,
        also_parent_of_siblings=True,
    )

    parent_edges = [r for r in repos.relation.relations[family.id] if isinstance(r, ParentOf)]
    mother_edges = {r.child_id for r in parent_edges if r.parent_id == mother.id}
    assert mother_edges == {owner_person.id, brother.id}
    assert father.id != mother.id


@pytest.mark.asyncio
async def test_add_person_rejects_third_parent_without_persisting() -> None:
    """Критерий приёмки 21: третий родитель отклоняется, хранилище не меняется."""
    repos = _repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()

    await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Пётр",
        gender=Gender.MALE,
        birth_date=date(1960, 3, 1),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=owner_person.id,
    )
    await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Мария",
        gender=Gender.FEMALE,
        birth_date=date(1962, 7, 7),
        relation_kind=RelationKind.MOTHER,
        relative_to_person_id=owner_person.id,
    )
    people_before = set(repos.person.people)
    relations_before = list(repos.relation.relations[family.id])

    with pytest.raises(ValueError):
        await _add_person(
            repos,
            acting_account_id=owner_account.id,
            family_id=family.id,
            name="Чужой",
            gender=Gender.MALE,
            birth_date=date(1959, 1, 1),
            relation_kind=RelationKind.FATHER,
            relative_to_person_id=owner_person.id,
        )

    assert set(repos.person.people) == people_before
    assert repos.relation.relations[family.id] == relations_before


@pytest.mark.asyncio
async def test_add_person_husband_creates_spouse_edge() -> None:
    repos = _repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Марина", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()

    husband = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Игорь",
        gender=Gender.MALE,
        birth_date=date(1988, 2, 2),
        relation_kind=RelationKind.HUSBAND,
        relative_to_person_id=owner_person.id,
    )

    [relation] = repos.relation.relations[family.id]
    assert isinstance(relation, SpouseOf)
    assert {relation.a_id, relation.b_id} == {owner_person.id, husband.id}


@pytest.mark.asyncio
async def test_add_person_rejects_non_owner() -> None:
    """Критерий приёмки 27: не-владелец не может изменять дерево."""
    repos = _repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    intruder_account = await repos.account.save(
        Account(
            id=0,
            telegram_user_id=999,
            current_family_id=None,
            timezone="Europe/Moscow",
            offsets_days=DEFAULT_OFFSETS_DAYS,
            time_of_day=time(9, 0),
        )
    )

    with pytest.raises(NotFamilyOwner):
        await _add_person(
            repos,
            acting_account_id=intruder_account.id,
            family_id=family.id,
            name="Пётр",
            gender=Gender.MALE,
            birth_date=date(1960, 3, 1),
            relation_kind=RelationKind.FATHER,
            relative_to_person_id=owner_person.id,
        )
    assert repos.person.people == {owner_person.id: owner_person}


@pytest.mark.asyncio
async def test_add_event_materializes_reminders_for_existing_members() -> None:
    """SPEC 3.4: напоминания о разовом событии строятся всем членам семьи,

    включая самого героя события (исключение из общего правила — только
    собственный день рождения, критерий приёмки 15).
    """
    repos = _repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()

    marina_account = await repos.account.save(
        Account(
            id=0,
            telegram_user_id=222,
            current_family_id=family.id,
            timezone="Europe/Moscow",
            offsets_days=DEFAULT_OFFSETS_DAYS,
            time_of_day=time(9, 0),
        )
    )
    marina = await repos.person.create(
        Person(id=0, family_id=family.id, name="Марина", gender=Gender.FEMALE, birth_date=None)
    )
    await repos.membership.create(
        Membership(account_id=marina_account.id, family_id=family.id, person_id=marina.id)
    )

    event = await add_event(
        owner_account.id,
        family.id,
        marina.id,
        "Выпускной",
        date(2027, 6, 3),
        False,
        repos.account,
        repos.event,
        repos.family,
        repos.person,
        repos.membership,
        repos.reminder,
        CLOCK,
    )

    assert event.is_recurring_yearly is False
    assert {r.person_id for r in repos.reminder.reminders} == {owner_person.id, marina.id}
    assert len(repos.reminder.reminders) == 2 * len(DEFAULT_OFFSETS_DAYS)


@pytest.mark.asyncio
async def test_add_event_rejects_person_from_another_family() -> None:
    """SPEC 2: событие нельзя завести человеку из чужой семьи."""
    repos = _repos()
    family1 = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    family2 = await _create_family(
        repos, telegram_user_id=222, name="Борис", birth_date=date(1985, 8, 20)
    )
    [owner1_account] = [a for a in repos.account.accounts.values() if a.telegram_user_id == 111]
    [boris] = [p for p in repos.person.people.values() if p.family_id == family2.id]

    with pytest.raises(ValueError):
        await add_event(
            owner1_account.id,
            family1.id,
            boris.id,
            "Выпускной",
            date(2027, 6, 3),
            False,
            repos.account,
            repos.event,
            repos.family,
            repos.person,
            repos.membership,
            repos.reminder,
            CLOCK,
        )


@pytest.mark.asyncio
async def test_add_event_rejects_non_owner() -> None:
    repos = _repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    intruder_account = await repos.account.save(
        Account(
            id=0,
            telegram_user_id=999,
            current_family_id=None,
            timezone="Europe/Moscow",
            offsets_days=DEFAULT_OFFSETS_DAYS,
            time_of_day=time(9, 0),
        )
    )

    with pytest.raises(NotFamilyOwner):
        await add_event(
            intruder_account.id,
            family.id,
            owner_person.id,
            "Выпускной",
            date(2027, 6, 3),
            False,
            repos.account,
            repos.event,
            repos.family,
            repos.person,
            repos.membership,
            repos.reminder,
            CLOCK,
        )


@pytest.mark.asyncio
async def test_account_in_two_families_gets_reminders_from_both() -> None:
    """Критерий приёмки 16: один аккаунт, состоящий в двух семьях, получает напоминания обеих."""
    repos = _repos()
    family1 = await _create_family(
        repos, telegram_user_id=1, name="Антон", birth_date=date(1990, 6, 15)
    )
    family2 = await _create_family(
        repos, telegram_user_id=2, name="Борис", birth_date=date(1985, 8, 20)
    )
    [anton_account] = [a for a in repos.account.accounts.values() if a.telegram_user_id == 1]
    [anton1] = [p for p in repos.person.people.values() if p.family_id == family1.id]

    anton_in_family2 = await repos.person.create(
        Person(id=0, family_id=family2.id, name="Антон", gender=Gender.MALE, birth_date=None)
    )
    await repos.membership.create(
        Membership(account_id=anton_account.id, family_id=family2.id, person_id=anton_in_family2.id)
    )
    [boris_account] = [a for a in repos.account.accounts.values() if a.telegram_user_id == 2]
    [boris2] = [
        p for p in repos.person.people.values() if p.family_id == family2.id and p.name == "Борис"
    ]

    await _add_person(
        repos,
        acting_account_id=anton_account.id,
        family_id=family1.id,
        name="Пётр",
        gender=Gender.MALE,
        birth_date=date(1960, 3, 1),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=anton1.id,
    )
    await _add_person(
        repos,
        acting_account_id=boris_account.id,
        family_id=family2.id,
        name="Сергей",
        gender=Gender.MALE,
        birth_date=date(1955, 4, 4),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=boris2.id,
    )

    recipient_person_ids = {r.person_id for r in repos.reminder.reminders}
    assert recipient_person_ids == {anton1.id, boris2.id, anton_in_family2.id}
    assert (
        anton_in_family2.id in recipient_person_ids
    )  # тот же account, что и anton1, во второй семье
    assert len(repos.reminder.reminders) == 3 * len(DEFAULT_OFFSETS_DAYS)
