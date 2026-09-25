"""Адаптеры репозиториев поверх SQLite, реализуют протоколы из core.ports."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from kinday.core.models import Reminder


class SqliteReminderRepo:
    """Реализация core.ports.ReminderRepo. Каждый вызов уходит в asyncio.to_thread."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

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
        raise NotImplementedError

    async def delete_future_pending_for_event(self, event_id: int, after: datetime) -> None:
        raise NotImplementedError

    async def delete_future_pending_for_person(self, person_id: int, after: datetime) -> None:
        raise NotImplementedError

    async def fail_all_sending(self) -> None:
        raise NotImplementedError
