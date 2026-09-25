"""Расчёт моментов отправки и материализация напоминаний."""

from __future__ import annotations

from datetime import datetime

from kinday.core.models import Account, Event, Reminder

MATERIALIZATION_HORIZON_DAYS = 400
MISFIRE_GRACE = 24 * 60 * 60  # секунд, см. SPEC 4: пропуск при простое


def due_at_utc(occurrence_date_local: datetime, offset_days: int, account: Account) -> datetime:
    """Локальная дата минус смещение, время суток, локализация в пояс, перевод в UTC.

    Если локального времени не существует из-за перевода часов, сдвигается вперёд
    до ближайшего существующего момента.
    """
    raise NotImplementedError


def materialize_for_event(event: Event, recipients: list[Account]) -> list[Reminder]:
    """Построить напоминания на MATERIALIZATION_HORIZON_DAYS вперёд для события.

    Собственный день рождения человека не порождает напоминание для него самого.
    """
    raise NotImplementedError
