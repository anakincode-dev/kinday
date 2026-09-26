"""Простые фейковые in-memory реализации портов ядра — без SQLite.

Общие для тестов разных сценариев (см. PLAN.md, этапы 3a/3b): каждый тестовый
модуль собирает нужный ему набор через `build_repos()` и передаёт репозитории
напрямую в функции `core/services.py`.

Фейки воспроизводят только уникальный индекс напоминаний (SPEC 5.3) — он влияет
на сам сценарий материализации. Остальных ограничений схемы, включая каскады по
внешним ключам, здесь нет: физически удалённый человек уносит в SQLite свои
приглашения и рёбра, а в фейке они остаются висеть. Поэтому тест не должен
опираться на состояние записей, привязанных к удалённому человеку — сравнивать
поведение двух бэкендов имеет смысл только там, где сама запись уцелела
(например, превратилась в заглушку). Общие сценарные тесты живут в
tests/storage/test_scenarios.py и проходят на обоих бэкендах.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from types import TracebackType

from kinday.core.models import (
    Account,
    Event,
    Family,
    Invite,
    Membership,
    Person,
    Relation,
    Reminder,
    ReminderOverride,
    ReminderStatus,
)


def _unique_key(reminder: Reminder) -> tuple[int, int, date, int]:
    """Ключ уникального индекса напоминаний из SPEC 5.3."""
    return (
        reminder.event_id,
        reminder.person_id,
        reminder.occurrence_date,
        reminder.offset_days,
    )


class FixedClock:
    """Простая подмена Clock с зафиксированным now(), без реальных часов."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def move_to(self, now: datetime) -> None:
        """Переводит часы: тесты двигают время подменой, а не `sleep` (SPEC 5.2)."""
        self._now = now


@dataclass
class RecordingNotifier:
    """Подмена Notifier: складывает сообщения в список вместо отправки в Telegram.

    `error` заставляет `send` поднимать заданное исключение — так тесты
    проигрывают 403 (`RecipientBlocked`) и сетевой сбой (`TransportError`),
    не зная ничего о самом транспорте.
    """

    sent: list[tuple[int, str]] = field(default_factory=list)
    error: Exception | None = None

    async def send(self, chat_id: int, text: str) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append((chat_id, text))


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

    async def update(self, event: Event) -> Event:
        self.events[event.id] = event
        return event

    async def delete(self, event_id: int) -> None:
        del self.events[event_id]

    async def list_by_family(self, family_id: int) -> list[Event]:
        return [e for e in self.events.values() if e.family_id == family_id]

    async def list_by_person(self, person_id: int) -> list[Event]:
        return [e for e in self.events.values() if e.person_id == person_id]

    async def list_all(self) -> list[Event]:
        return sorted(self.events.values(), key=lambda e: e.id)


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
    _next_id: int = 1

    def _by_id(self, reminder_id: int) -> Reminder:
        return next(r for r in self.reminders if r.id == reminder_id)

    async def claim_due(self, at: datetime, limit: int) -> list[Reminder]:
        """Повторяет условный UPDATE из SPEC 5.5: забирается только pending.

        Порядок — по сроку, как в SQLite, и наружу уходят копии: в настоящем
        хранилище тик держит в руках снимок строки, а не саму строку, и правки
        статуса идут только через методы репозитория.
        """
        claimed: list[Reminder] = []
        for reminder in sorted(self.reminders, key=lambda r: (r.due_at_utc, r.id)):
            if len(claimed) >= limit:
                break
            if reminder.status != ReminderStatus.PENDING or reminder.due_at_utc > at:
                continue
            reminder.status = ReminderStatus.SENDING
            claimed.append(replace(reminder))
        return claimed

    async def mark_sent(self, reminder_id: int, at: datetime) -> None:
        reminder = self._by_id(reminder_id)
        reminder.status = ReminderStatus.SENT
        reminder.sent_at = at

    async def mark_missed(self, reminder_id: int, at: datetime) -> None:
        self._by_id(reminder_id).status = ReminderStatus.MISSED

    async def mark_failed(self, reminder_id: int, at: datetime, *, attempted: bool) -> None:
        reminder = self._by_id(reminder_id)
        reminder.status = ReminderStatus.FAILED
        if attempted:
            reminder.attempts += 1

    async def release(self, reminder_id: int) -> None:
        reminder = self._by_id(reminder_id)
        reminder.status = ReminderStatus.PENDING
        reminder.attempts += 1

    async def add_many(self, reminders: list[Reminder]) -> None:
        """Эмулирует уникальный индекс SPEC 5.3, а не просто расширяет список.

        В SQLite вставка идёт через ON CONFLICT DO NOTHING по
        (event_id, person_id, occurrence_date, offset_days) — независимо от
        статуса уже лежащей строки. Без этой эмуляции фейк расходился бы
        с настоящим хранилищем ровно там, где перематериализация задевает
        отправленную строку: в базе новая строка была бы отброшена, а в фейке
        появился бы дубль.
        """
        for reminder in reminders:
            if not any(_unique_key(r) == _unique_key(reminder) for r in self.reminders):
                reminder.id = self._next_id
                self._next_id += 1
                self.reminders.append(reminder)

    async def delete_future_pending_for_event(self, event_id: int, after: datetime) -> None:
        self.reminders = [
            r
            for r in self.reminders
            if not (
                r.event_id == event_id
                and r.status == ReminderStatus.PENDING
                and r.due_at_utc >= after
            )
        ]

    async def delete_future_pending_for_person(self, person_id: int, after: datetime) -> None:
        self.reminders = [
            r
            for r in self.reminders
            if not (
                r.person_id == person_id
                and r.status == ReminderStatus.PENDING
                and r.due_at_utc >= after
            )
        ]

    async def delete_future_pending_for_event_and_person(
        self, event_id: int, person_id: int, after: datetime
    ) -> None:
        self.reminders = [
            r
            for r in self.reminders
            if not (
                r.event_id == event_id
                and r.person_id == person_id
                and r.status == ReminderStatus.PENDING
                and r.due_at_utc >= after
            )
        ]

    async def delete_all_for_event(self, event_id: int) -> None:
        self.reminders = [r for r in self.reminders if r.event_id != event_id]

    async def delete_all_for_person(self, person_id: int) -> None:
        self.reminders = [r for r in self.reminders if r.person_id != person_id]

    async def delete_recoverable_failed_for_person(
        self, person_id: int, attempted_after: datetime, unattempted_after: datetime
    ) -> None:
        def recoverable(reminder: Reminder) -> bool:
            after = attempted_after if reminder.attempts else unattempted_after
            return (
                reminder.person_id == person_id
                and reminder.status == ReminderStatus.FAILED
                and reminder.due_at_utc > after
            )

        self.reminders = [r for r in self.reminders if not recoverable(r)]

    async def fail_pending_for_person(self, person_id: int, at: datetime) -> int:
        closed = 0
        for reminder in self.reminders:
            if reminder.person_id == person_id and reminder.status == ReminderStatus.PENDING:
                reminder.status = ReminderStatus.FAILED
                closed += 1
        return closed

    async def fail_all_sending(self, at: datetime) -> int:
        closed = 0
        for reminder in self.reminders:
            if reminder.status == ReminderStatus.SENDING:
                reminder.status = ReminderStatus.FAILED
                # Отправка по такой строке шла: /start её не воскресит.
                reminder.attempts += 1
                closed += 1
        return closed


@dataclass
class FakeInviteRepo:
    invites: dict[int, Invite] = field(default_factory=dict)
    _next_id: int = 1

    async def get(self, invite_id: int) -> Invite:
        return self.invites[invite_id]

    async def get_by_code(self, code: str) -> Invite | None:
        return next((i for i in self.invites.values() if i.code == code), None)

    async def list_by_person(self, person_id: int) -> list[Invite]:
        return [i for i in self.invites.values() if i.person_id == person_id]

    async def create(self, invite: Invite) -> Invite:
        invite.id = self._next_id
        self._next_id += 1
        self.invites[invite.id] = invite
        return invite

    async def revoke(self, invite_id: int, at: datetime) -> None:
        self.invites[invite_id].revoked_at = at

    async def mark_used(self, invite_id: int, at: datetime) -> None:
        self.invites[invite_id].used_at = at


@dataclass
class FakeReminderOverrideRepo:
    overrides: dict[tuple[int, int], ReminderOverride] = field(default_factory=dict)

    async def get(self, account_id: int, event_id: int) -> ReminderOverride | None:
        return self.overrides.get((account_id, event_id))

    async def set(self, override: ReminderOverride) -> None:
        self.overrides[(override.account_id, override.event_id)] = override

    async def revoke(self, account_id: int, event_id: int) -> None:
        self.overrides.pop((account_id, event_id), None)


class FakeUnitOfWork:
    """Единица работы без транзакции: фейки не рвутся на середине записи.

    Сценарию нужен объект, поддерживающий `async with`, и ничего больше:
    проверку самого откатa ведут тесты на SQLite (tests/storage), где
    транзакция настоящая.
    """

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


@dataclass
class Repos:
    family: FakeFamilyRepo
    person: FakePersonRepo
    account: FakeAccountRepo
    membership: FakeMembershipRepo
    event: FakeEventRepo
    relation: FakeRelationRepo
    reminder: FakeReminderRepo
    invite: FakeInviteRepo
    override: FakeReminderOverrideRepo
    uow: FakeUnitOfWork


def build_repos() -> Repos:
    return Repos(
        family=FakeFamilyRepo(),
        person=FakePersonRepo(),
        account=FakeAccountRepo(),
        membership=FakeMembershipRepo(),
        event=FakeEventRepo(),
        relation=FakeRelationRepo(),
        reminder=FakeReminderRepo(),
        invite=FakeInviteRepo(),
        override=FakeReminderOverrideRepo(),
        uow=FakeUnitOfWork(),
    )
