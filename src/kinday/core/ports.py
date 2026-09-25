"""Протоколы, которые ядро объявляет, а внешние слои реализуют.

Зависимости направлены внутрь: ядро ничего не знает о storage, scheduler и telegram.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from kinday.core.models import Account, Event, Family, Invite, Person, Reminder


class RecipientBlocked(Exception):
    """Получатель заблокировал бота (Telegram вернул 403)."""


class TransportError(Exception):
    """Сетевой сбой или таймаут при отправке."""


class Clock(Protocol):
    def now(self) -> datetime:
        """Всегда timezone-aware, в UTC."""
        ...


class Notifier(Protocol):
    async def send(self, chat_id: int, text: str) -> None:
        """Поднимает RecipientBlocked при 403 и TransportError при сетевом сбое."""
        ...


class ReminderRepo(Protocol):
    async def claim_due(self, at: datetime, limit: int) -> list[Reminder]: ...
    async def mark_sent(self, reminder_id: int, at: datetime) -> None: ...
    async def mark_missed(self, reminder_id: int, at: datetime) -> None: ...
    async def mark_failed(self, reminder_id: int, at: datetime) -> None: ...
    async def release(self, reminder_id: int) -> None: ...


class FamilyRepo(Protocol):
    async def get(self, family_id: int) -> Family: ...
    async def create(self, family: Family) -> Family: ...


class PersonRepo(Protocol):
    async def get(self, person_id: int) -> Person: ...
    async def create(self, person: Person) -> Person: ...
    async def delete(self, person_id: int) -> None: ...


class AccountRepo(Protocol):
    async def get(self, account_id: int) -> Account: ...
    async def get_by_telegram_user_id(self, telegram_user_id: int) -> Account | None: ...
    async def save(self, account: Account) -> Account: ...


class EventRepo(Protocol):
    async def get(self, event_id: int) -> Event: ...
    async def create(self, event: Event) -> Event: ...


class InviteRepo(Protocol):
    async def get_by_code(self, code: str) -> Invite | None: ...
    async def create(self, invite: Invite) -> Invite: ...
    async def revoke(self, invite_id: int) -> None: ...
