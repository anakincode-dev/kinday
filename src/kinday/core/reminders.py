"""Расчёт моментов отправки и материализация напоминаний."""

from __future__ import annotations

from datetime import date, datetime

from kinday.core.models import Account, Event, Membership, Reminder

MATERIALIZATION_HORIZON_DAYS = 400
MISFIRE_GRACE = 24 * 60 * 60  # секунд, см. SPEC 4: пропуск при простое


def due_at_utc(occurrence_date_local: date, offset_days: int, account: Account) -> datetime:
    """Локальная дата минус смещение, время суток, локализация в пояс, перевод в UTC.

    Если локального времени не существует из-за перевода часов, сдвигается вперёд
    до ближайшего существующего момента.
    """
    raise NotImplementedError


def materialize_for_event(
    event: Event, recipients: list[tuple[Membership, Account]]
) -> list[Reminder]:
    """Построить напоминания на MATERIALIZATION_HORIZON_DAYS вперёд для события.

    `recipients` — пары (Membership, Account): Membership даёт person_id
    получателя в семье события, Account — его настройки напоминаний.
    Собственный день рождения человека не порождает напоминание для него
    самого — сравнение идёт по Membership.person_id, а не по Account.id.
    """
    raise NotImplementedError
