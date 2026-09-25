"""Доменные сущности. Чистые данные, без поведения и без внешних зависимостей."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum


class Gender(Enum):
    MALE = "male"
    FEMALE = "female"


class ReminderStatus(Enum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    MISSED = "missed"
    FAILED = "failed"


@dataclass(slots=True)
class Family:
    id: int
    name: str
    owner_account_id: int


@dataclass(slots=True)
class Person:
    """Человек в дереве. Заглушка (см. relations.py) не имеет имени, пола и даты рождения."""

    id: int
    family_id: int
    name: str | None
    gender: Gender | None
    birth_date: date | None
    is_placeholder: bool = False


@dataclass(slots=True)
class Account:
    """Настройки напоминаний принадлежат аккаунту целиком, не семье."""

    id: int
    telegram_user_id: int
    current_family_id: int | None
    timezone: str
    offsets_days: tuple[int, ...]
    time_of_day: time
    chat_id: int | None = None
    delivery_enabled: bool = True


@dataclass(slots=True)
class ParentOf:
    """Направленное ребро parent_of(parent_id, child_id)."""

    parent_id: int
    child_id: int


@dataclass(slots=True)
class SpouseOf:
    """Симметричное ребро spouse_of(a_id, b_id)."""

    a_id: int
    b_id: int


Relation = ParentOf | SpouseOf


@dataclass(slots=True)
class Event:
    id: int
    family_id: int
    person_id: int
    title: str
    date: date
    is_recurring_yearly: bool = True


@dataclass(slots=True)
class ReminderOverride:
    """Переопределение смещений и времени суток для пары (аккаунт, событие)."""

    account_id: int
    event_id: int
    offsets_days: tuple[int, ...]
    time_of_day: time


@dataclass(slots=True)
class Reminder:
    id: int
    event_id: int
    person_id: int
    offset_days: int
    occurrence_date: date
    due_at_utc: datetime
    status: ReminderStatus = ReminderStatus.PENDING
    attempts: int = 0
    sent_at: datetime | None = None


@dataclass(slots=True)
class Invite:
    id: int
    family_id: int
    person_id: int
    code: str
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None
    revoked_at: datetime | None = None
