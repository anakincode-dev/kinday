"""Адаптеры репозиториев поверх SQLite, реализуют протоколы из core.ports.

Каждый вызов уходит в `Database.run`, то есть в `asyncio.to_thread` (SPEC 6.2):
наружу репозитории асинхронные, внутри — обычный синхронный sqlite3.
Преобразование значений в столбцы и обратно живёт в codecs.py.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from types import TracebackType

from kinday.core.models import (
    Account,
    Event,
    EventKind,
    Family,
    Invite,
    Membership,
    ParentOf,
    Person,
    Relation,
    Reminder,
    ReminderOverride,
    ReminderStatus,
    SpouseOf,
)
from kinday.storage.codecs import (
    dump_date,
    dump_datetime,
    dump_gender,
    dump_offsets,
    dump_time,
    load_date,
    load_datetime,
    load_gender,
    load_offsets,
    load_time,
)
from kinday.storage.db import Database

_PARENT_OF = "parent_of"
_SPOUSE_OF = "spouse_of"


def _family(row: sqlite3.Row) -> Family:
    return Family(
        id=int(row["id"]), name=row["name"], owner_account_id=int(row["owner_account_id"])
    )


def _person(row: sqlite3.Row) -> Person:
    return Person(
        id=int(row["id"]),
        family_id=int(row["family_id"]),
        name=row["name"],
        gender=load_gender(row["gender"]),
        birth_date=None if row["birth_date"] is None else load_date(row["birth_date"]),
        is_placeholder=bool(row["is_placeholder"]),
    )


def _account(row: sqlite3.Row) -> Account:
    return Account(
        id=int(row["id"]),
        telegram_user_id=int(row["telegram_user_id"]),
        current_family_id=(
            None if row["current_family_id"] is None else int(row["current_family_id"])
        ),
        timezone=row["timezone"],
        offsets_days=load_offsets(row["offsets_days"]),
        time_of_day=load_time(row["time_of_day"]),
        chat_id=None if row["chat_id"] is None else int(row["chat_id"]),
        delivery_enabled=bool(row["delivery_enabled"]),
    )


def _membership(row: sqlite3.Row) -> Membership:
    return Membership(
        account_id=int(row["account_id"]),
        family_id=int(row["family_id"]),
        person_id=int(row["person_id"]),
    )


def _event(row: sqlite3.Row) -> Event:
    return Event(
        id=int(row["id"]),
        family_id=int(row["family_id"]),
        person_id=int(row["person_id"]),
        title=row["title"],
        date=load_date(row["date"]),
        is_recurring_yearly=bool(row["is_recurring_yearly"]),
        kind=EventKind(row["kind"]),
    )


def _relation(row: sqlite3.Row) -> Relation:
    if row["kind"] == _PARENT_OF:
        return ParentOf(parent_id=int(row["a_id"]), child_id=int(row["b_id"]))
    return SpouseOf(a_id=int(row["a_id"]), b_id=int(row["b_id"]))


def _relation_columns(relation: Relation) -> tuple[str, int, int]:
    """Ребро в три столбца: вид и две ссылки на людей.

    Направление parent_of сохраняется как есть (a_id — родитель), spouse_of
    лежит одной строкой в том порядке, в котором пришло: обход графа читает его
    в обе стороны (core/relations.py).
    """
    if isinstance(relation, ParentOf):
        return (_PARENT_OF, relation.parent_id, relation.child_id)
    return (_SPOUSE_OF, relation.a_id, relation.b_id)


def _reminder(row: sqlite3.Row) -> Reminder:
    return Reminder(
        id=int(row["id"]),
        event_id=int(row["event_id"]),
        person_id=int(row["person_id"]),
        offset_days=int(row["offset_days"]),
        occurrence_date=load_date(row["occurrence_date"]),
        due_at_utc=load_datetime(row["due_at_utc"]),
        status=ReminderStatus(row["status"]),
        attempts=int(row["attempts"]),
        sent_at=None if row["sent_at"] is None else load_datetime(row["sent_at"]),
    )


def _override(row: sqlite3.Row) -> ReminderOverride:
    return ReminderOverride(
        account_id=int(row["account_id"]),
        event_id=int(row["event_id"]),
        offsets_days=load_offsets(row["offsets_days"]),
        time_of_day=load_time(row["time_of_day"]),
    )


def _invite(row: sqlite3.Row) -> Invite:
    return Invite(
        id=int(row["id"]),
        family_id=int(row["family_id"]),
        person_id=int(row["person_id"]),
        code=row["code"],
        created_at=load_datetime(row["created_at"]),
        expires_at=load_datetime(row["expires_at"]),
        used_at=None if row["used_at"] is None else load_datetime(row["used_at"]),
        revoked_at=None if row["revoked_at"] is None else load_datetime(row["revoked_at"]),
    )


class SqliteUnitOfWork:
    """Реализация core.ports.UnitOfWork: одна транзакция SQLite на сценарий.

    На входе занимает базу под текущую задачу и открывает BEGIN IMMEDIATE — то
    есть право на запись берётся сразу, а не на первом UPDATE, и сценарий не
    падает посередине из-за чужой записи. На выходе фиксирует или откатывает
    транзакцию и отпускает базу.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def __aenter__(self) -> SqliteUnitOfWork:
        await self._database.acquire()
        try:
            await self._database.run(lambda c: c.execute("BEGIN IMMEDIATE"))
        except BaseException:
            self._database.release()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is None:
                await self._database.run(lambda c: c.execute("COMMIT"))
            else:
                await self._database.run(lambda c: c.execute("ROLLBACK"))
        finally:
            # Замок ещё занят, поэтому соединение здесь только наше: если COMMIT
            # или ROLLBACK не дошёл (отмена задачи, таймаут), транзакция
            # закрывается принудительно — иначе следующий BEGIN упал бы.
            self._database.rollback_if_open()
            self._database.release()


class SqliteFamilyRepo:
    """Реализация core.ports.FamilyRepo."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, family_id: int) -> Family:
        row = await self._database.run(
            lambda c: c.execute("SELECT * FROM families WHERE id = ?", (family_id,)).fetchone()
        )
        if row is None:
            raise LookupError(f"Семья {family_id} не найдена")
        return _family(row)

    async def create(self, family: Family) -> Family:
        def work(connection: sqlite3.Connection) -> int:
            cursor = connection.execute(
                "INSERT INTO families (name, owner_account_id) VALUES (?, ?)",
                (family.name, family.owner_account_id),
            )
            return int(cursor.lastrowid or 0)

        family.id = await self._database.run(work)
        return family


class SqlitePersonRepo:
    """Реализация core.ports.PersonRepo."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, person_id: int) -> Person:
        row = await self._database.run(
            lambda c: c.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
        )
        if row is None:
            raise LookupError(f"Человек {person_id} не найден")
        return _person(row)

    async def create(self, person: Person) -> Person:
        def work(connection: sqlite3.Connection) -> int:
            cursor = connection.execute(
                """
                INSERT INTO people (family_id, name, gender, birth_date, is_placeholder)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    person.family_id,
                    person.name,
                    dump_gender(person.gender),
                    None if person.birth_date is None else dump_date(person.birth_date),
                    int(person.is_placeholder),
                ),
            )
            return int(cursor.lastrowid or 0)

        person.id = await self._database.run(work)
        return person

    async def update(self, person: Person) -> Person:
        await self._database.run(
            lambda c: c.execute(
                """
                UPDATE people
                   SET name = ?, gender = ?, birth_date = ?, is_placeholder = ?
                 WHERE id = ?
                """,
                (
                    person.name,
                    dump_gender(person.gender),
                    None if person.birth_date is None else dump_date(person.birth_date),
                    int(person.is_placeholder),
                    person.id,
                ),
            )
        )
        return person

    async def delete(self, person_id: int) -> None:
        await self._database.run(
            lambda c: c.execute("DELETE FROM people WHERE id = ?", (person_id,))
        )

    async def list_by_family(self, family_id: int) -> list[Person]:
        rows = await self._database.run(
            lambda c: c.execute(
                "SELECT * FROM people WHERE family_id = ? ORDER BY id", (family_id,)
            ).fetchall()
        )
        return [_person(row) for row in rows]


class SqliteAccountRepo:
    """Реализация core.ports.AccountRepo."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, account_id: int) -> Account:
        row = await self._database.run(
            lambda c: c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        )
        if row is None:
            raise LookupError(f"Аккаунт {account_id} не найден")
        return _account(row)

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> Account | None:
        row = await self._database.run(
            lambda c: c.execute(
                "SELECT * FROM accounts WHERE telegram_user_id = ?", (telegram_user_id,)
            ).fetchone()
        )
        return None if row is None else _account(row)

    async def save(self, account: Account) -> Account:
        """Создаёт аккаунт при нулевом id, иначе перезаписывает настройки целиком."""

        def work(connection: sqlite3.Connection) -> int:
            values = (
                account.telegram_user_id,
                account.current_family_id,
                account.timezone,
                dump_offsets(account.offsets_days),
                dump_time(account.time_of_day),
                account.chat_id,
                int(account.delivery_enabled),
            )
            if account.id == 0:
                cursor = connection.execute(
                    """
                    INSERT INTO accounts (
                        telegram_user_id, current_family_id, timezone, offsets_days,
                        time_of_day, chat_id, delivery_enabled
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                return int(cursor.lastrowid or 0)
            connection.execute(
                """
                UPDATE accounts
                   SET telegram_user_id = ?, current_family_id = ?, timezone = ?,
                       offsets_days = ?, time_of_day = ?, chat_id = ?, delivery_enabled = ?
                 WHERE id = ?
                """,
                (*values, account.id),
            )
            return account.id

        account.id = await self._database.run(work)
        return account


class SqliteMembershipRepo:
    """Реализация core.ports.MembershipRepo."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_by_account_and_family(self, account_id: int, family_id: int) -> Membership | None:
        row = await self._database.run(
            lambda c: c.execute(
                "SELECT * FROM memberships WHERE account_id = ? AND family_id = ?",
                (account_id, family_id),
            ).fetchone()
        )
        return None if row is None else _membership(row)

    async def list_by_account(self, account_id: int) -> list[Membership]:
        rows = await self._database.run(
            lambda c: c.execute(
                "SELECT * FROM memberships WHERE account_id = ? ORDER BY family_id",
                (account_id,),
            ).fetchall()
        )
        return [_membership(row) for row in rows]

    async def list_by_family(self, family_id: int) -> list[Membership]:
        rows = await self._database.run(
            lambda c: c.execute(
                "SELECT * FROM memberships WHERE family_id = ? ORDER BY person_id", (family_id,)
            ).fetchall()
        )
        return [_membership(row) for row in rows]

    async def get_by_person(self, person_id: int) -> Membership | None:
        row = await self._database.run(
            lambda c: c.execute(
                "SELECT * FROM memberships WHERE person_id = ?", (person_id,)
            ).fetchone()
        )
        return None if row is None else _membership(row)

    async def create(self, membership: Membership) -> Membership:
        await self._database.run(
            lambda c: c.execute(
                "INSERT INTO memberships (account_id, family_id, person_id) VALUES (?, ?, ?)",
                (membership.account_id, membership.family_id, membership.person_id),
            )
        )
        return membership

    async def delete(self, person_id: int) -> None:
        await self._database.run(
            lambda c: c.execute("DELETE FROM memberships WHERE person_id = ?", (person_id,))
        )


class SqliteEventRepo:
    """Реализация core.ports.EventRepo."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, event_id: int) -> Event:
        row = await self._database.run(
            lambda c: c.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        )
        if row is None:
            raise LookupError(f"Событие {event_id} не найдено")
        return _event(row)

    async def create(self, event: Event) -> Event:
        def work(connection: sqlite3.Connection) -> int:
            cursor = connection.execute(
                """
                INSERT INTO events (family_id, person_id, title, date, is_recurring_yearly, kind)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.family_id,
                    event.person_id,
                    event.title,
                    dump_date(event.date),
                    int(event.is_recurring_yearly),
                    event.kind.value,
                ),
            )
            return int(cursor.lastrowid or 0)

        event.id = await self._database.run(work)
        return event

    async def update(self, event: Event) -> Event:
        await self._database.run(
            lambda c: c.execute(
                """
                UPDATE events
                   SET title = ?, date = ?, is_recurring_yearly = ?, kind = ?
                 WHERE id = ?
                """,
                (
                    event.title,
                    dump_date(event.date),
                    int(event.is_recurring_yearly),
                    event.kind.value,
                    event.id,
                ),
            )
        )
        return event

    async def delete(self, event_id: int) -> None:
        await self._database.run(
            lambda c: c.execute("DELETE FROM events WHERE id = ?", (event_id,))
        )

    async def list_by_family(self, family_id: int) -> list[Event]:
        rows = await self._database.run(
            lambda c: c.execute(
                "SELECT * FROM events WHERE family_id = ? ORDER BY id", (family_id,)
            ).fetchall()
        )
        return [_event(row) for row in rows]

    async def list_by_person(self, person_id: int) -> list[Event]:
        rows = await self._database.run(
            lambda c: c.execute(
                "SELECT * FROM events WHERE person_id = ? ORDER BY id", (person_id,)
            ).fetchall()
        )
        return [_event(row) for row in rows]

    async def list_all(self) -> list[Event]:
        rows = await self._database.run(
            lambda c: c.execute("SELECT * FROM events ORDER BY id").fetchall()
        )
        return [_event(row) for row in rows]


class SqliteRelationRepo:
    """Реализация core.ports.RelationRepo."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list_by_family(self, family_id: int) -> list[Relation]:
        rows = await self._database.run(
            lambda c: c.execute(
                "SELECT * FROM relations WHERE family_id = ? ORDER BY kind, a_id, b_id",
                (family_id,),
            ).fetchall()
        )
        return [_relation(row) for row in rows]

    async def add(self, family_id: int, relation: Relation) -> None:
        kind, a_id, b_id = _relation_columns(relation)
        await self._database.run(
            lambda c: c.execute(
                "INSERT INTO relations (family_id, kind, a_id, b_id) VALUES (?, ?, ?, ?)",
                (family_id, kind, a_id, b_id),
            )
        )

    async def remove(self, family_id: int, relation: Relation) -> None:
        kind, a_id, b_id = _relation_columns(relation)
        await self._database.run(
            lambda c: c.execute(
                """
                DELETE FROM relations
                 WHERE family_id = ? AND kind = ? AND a_id = ? AND b_id = ?
                """,
                (family_id, kind, a_id, b_id),
            )
        )


class SqliteReminderOverrideRepo:
    """Реализация core.ports.ReminderOverrideRepo."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, account_id: int, event_id: int) -> ReminderOverride | None:
        row = await self._database.run(
            lambda c: c.execute(
                "SELECT * FROM reminder_overrides WHERE account_id = ? AND event_id = ?",
                (account_id, event_id),
            ).fetchone()
        )
        return None if row is None else _override(row)

    async def set(self, override: ReminderOverride) -> None:
        await self._database.run(
            lambda c: c.execute(
                """
                INSERT INTO reminder_overrides (account_id, event_id, offsets_days, time_of_day)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (account_id, event_id)
                DO UPDATE SET offsets_days = excluded.offsets_days,
                              time_of_day = excluded.time_of_day
                """,
                (
                    override.account_id,
                    override.event_id,
                    dump_offsets(override.offsets_days),
                    dump_time(override.time_of_day),
                ),
            )
        )

    async def revoke(self, account_id: int, event_id: int) -> None:
        await self._database.run(
            lambda c: c.execute(
                "DELETE FROM reminder_overrides WHERE account_id = ? AND event_id = ?",
                (account_id, event_id),
            )
        )


class SqliteInviteRepo:
    """Реализация core.ports.InviteRepo."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, invite_id: int) -> Invite:
        row = await self._database.run(
            lambda c: c.execute("SELECT * FROM invites WHERE id = ?", (invite_id,)).fetchone()
        )
        if row is None:
            raise LookupError(f"Приглашение {invite_id} не найдено")
        return _invite(row)

    async def get_by_code(self, code: str) -> Invite | None:
        row = await self._database.run(
            lambda c: c.execute("SELECT * FROM invites WHERE code = ?", (code,)).fetchone()
        )
        return None if row is None else _invite(row)

    async def list_by_person(self, person_id: int) -> list[Invite]:
        rows = await self._database.run(
            lambda c: c.execute(
                "SELECT * FROM invites WHERE person_id = ? ORDER BY id", (person_id,)
            ).fetchall()
        )
        return [_invite(row) for row in rows]

    async def create(self, invite: Invite) -> Invite:
        def work(connection: sqlite3.Connection) -> int:
            cursor = connection.execute(
                """
                INSERT INTO invites (family_id, person_id, code, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    invite.family_id,
                    invite.person_id,
                    invite.code,
                    dump_datetime(invite.created_at),
                    dump_datetime(invite.expires_at),
                ),
            )
            return int(cursor.lastrowid or 0)

        invite.id = await self._database.run(work)
        return invite

    async def revoke(self, invite_id: int, at: datetime) -> None:
        await self._database.run(
            lambda c: c.execute(
                "UPDATE invites SET revoked_at = ? WHERE id = ?",
                (dump_datetime(at), invite_id),
            )
        )

    async def mark_used(self, invite_id: int, at: datetime) -> None:
        await self._database.run(
            lambda c: c.execute(
                "UPDATE invites SET used_at = ? WHERE id = ?", (dump_datetime(at), invite_id)
            )
        )


class SqliteReminderRepo:
    """Реализация core.ports.ReminderRepo."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def claim_due(self, at: datetime, limit: int) -> list[Reminder]:
        """Забирает наступившие напоминания, переводя каждое из pending в sending.

        SPEC 5.5: строку получает только тот, кто увидел её в состоянии pending,
        поэтому статус меняется условным UPDATE и каждое изменение фиксируется
        сразу (соединение в автокоммите). Строка, которую между выборкой и
        UPDATE успел забрать кто-то другой, в результат не попадает.
        """
        moment = dump_datetime(at)

        def work(connection: sqlite3.Connection) -> list[Reminder]:
            rows = connection.execute(
                """
                SELECT * FROM reminders
                 WHERE status = 'pending' AND due_at_utc <= ?
                 ORDER BY due_at_utc, id
                 LIMIT ?
                """,
                (moment, limit),
            ).fetchall()
            claimed: list[Reminder] = []
            for row in rows:
                cursor = connection.execute(
                    """
                    UPDATE reminders
                       SET status = 'sending', status_changed_at = ?
                     WHERE id = ? AND status = 'pending'
                    """,
                    (moment, row["id"]),
                )
                if cursor.rowcount == 1:
                    reminder = _reminder(row)
                    reminder.status = ReminderStatus.SENDING
                    claimed.append(reminder)
            return claimed

        return await self._database.run(work)

    async def mark_sent(self, reminder_id: int, at: datetime) -> None:
        await self._database.run(
            lambda c: c.execute(
                """
                UPDATE reminders
                   SET status = 'sent', sent_at = ?, status_changed_at = ?
                 WHERE id = ?
                """,
                (dump_datetime(at), dump_datetime(at), reminder_id),
            )
        )

    async def mark_missed(self, reminder_id: int, at: datetime) -> None:
        await self._set_status(reminder_id, ReminderStatus.MISSED, at)

    async def mark_failed(self, reminder_id: int, at: datetime) -> None:
        await self._set_status(reminder_id, ReminderStatus.FAILED, at)

    async def _set_status(self, reminder_id: int, status: ReminderStatus, at: datetime) -> None:
        """sent_at не трогается: по SPEC 5.3 это момент фактической отправки."""
        await self._database.run(
            lambda c: c.execute(
                "UPDATE reminders SET status = ?, status_changed_at = ? WHERE id = ?",
                (status.value, dump_datetime(at), reminder_id),
            )
        )

    async def release(self, reminder_id: int) -> None:
        """Возврат в pending после сетевой ошибки: повтор на следующем тике (SPEC 5.5)."""
        await self._database.run(
            lambda c: c.execute(
                """
                UPDATE reminders
                   SET status = 'pending', attempts = attempts + 1
                 WHERE id = ?
                """,
                (reminder_id,),
            )
        )

    async def add_many(self, reminders: list[Reminder]) -> None:
        """Вставка пачкой; дубли по уникальному индексу SPEC 5.3 молча игнорируются.

        ON CONFLICT DO NOTHING не смотрит на статус уже лежащей строки: если
        напоминание с тем же ключом уже отправлено, повторная материализация
        его не воскресит и второго сообщения не будет.
        """
        if not reminders:
            return
        values = [
            (
                reminder.event_id,
                reminder.person_id,
                reminder.offset_days,
                dump_date(reminder.occurrence_date),
                dump_datetime(reminder.due_at_utc),
                reminder.status.value,
                reminder.attempts,
                None if reminder.sent_at is None else dump_datetime(reminder.sent_at),
            )
            for reminder in reminders
        ]
        await self._database.run(
            lambda c: c.executemany(
                """
                INSERT INTO reminders (
                    event_id, person_id, offset_days, occurrence_date, due_at_utc,
                    status, attempts, sent_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (event_id, person_id, occurrence_date, offset_days) DO NOTHING
                """,
                values,
            )
        )

    async def delete_future_pending_for_event(self, event_id: int, after: datetime) -> None:
        await self._database.run(
            lambda c: c.execute(
                """
                DELETE FROM reminders
                 WHERE event_id = ? AND status = 'pending' AND due_at_utc >= ?
                """,
                (event_id, dump_datetime(after)),
            )
        )

    async def delete_future_pending_for_person(self, person_id: int, after: datetime) -> None:
        await self._database.run(
            lambda c: c.execute(
                """
                DELETE FROM reminders
                 WHERE person_id = ? AND status = 'pending' AND due_at_utc >= ?
                """,
                (person_id, dump_datetime(after)),
            )
        )

    async def delete_future_pending_for_event_and_person(
        self, event_id: int, person_id: int, after: datetime
    ) -> None:
        await self._database.run(
            lambda c: c.execute(
                """
                DELETE FROM reminders
                 WHERE event_id = ? AND person_id = ? AND status = 'pending' AND due_at_utc >= ?
                """,
                (event_id, person_id, dump_datetime(after)),
            )
        )

    async def delete_all_for_event(self, event_id: int) -> None:
        await self._database.run(
            lambda c: c.execute("DELETE FROM reminders WHERE event_id = ?", (event_id,))
        )

    async def delete_all_for_person(self, person_id: int) -> None:
        await self._database.run(
            lambda c: c.execute("DELETE FROM reminders WHERE person_id = ?", (person_id,))
        )

    async def fail_all_sending(self, at: datetime) -> int:
        """SPEC 5.5: строки, застрявшие в sending после падения, повторно не отправляются."""

        def work(connection: sqlite3.Connection) -> int:
            cursor = connection.execute(
                """
                UPDATE reminders
                   SET status = 'failed', status_changed_at = ?
                 WHERE status = 'sending'
                """,
                (dump_datetime(at),),
            )
            return cursor.rowcount

        return await self._database.run(work)
