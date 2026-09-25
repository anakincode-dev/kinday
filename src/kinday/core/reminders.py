"""Расчёт моментов отправки и материализация напоминаний."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from kinday.core.models import Account, Event, Membership, Reminder
from kinday.core.ports import Clock
from kinday.core.recurrence import next_occurrence

MATERIALIZATION_HORIZON_DAYS = 400
MISFIRE_GRACE = 24 * 60 * 60  # секунд, см. SPEC 4: пропуск при простое

_GAP_SEARCH_LIMIT = timedelta(hours=4)
_GAP_SEARCH_STEP = timedelta(minutes=1)


def due_at_utc(occurrence_date_local: date, offset_days: int, account: Account) -> datetime:
    """Локальная дата минус смещение, время суток, локализация в пояс, перевод в UTC.

    Если локального времени не существует из-за перевода часов, сдвигается вперёд
    до ближайшего существующего момента.
    """
    local_date = occurrence_date_local - timedelta(days=offset_days)
    naive = datetime.combine(local_date, account.time_of_day)
    localized = _localize_forward(naive, ZoneInfo(account.timezone))
    return localized.astimezone(UTC)


def _localize_forward(naive: datetime, tz: ZoneInfo) -> datetime:
    """Локализует naive-время в `tz`, при попадании в несуществующий момент

    (перевод часов вперёд) сдвигает вперёд до ближайшего существующего.
    Существование проверяется через обратный перевод: если naive-время не
    round-trip'ится обратно после ухода в UTC и назад, оно не существует.
    """
    candidate = naive
    searched = timedelta()
    while searched <= _GAP_SEARCH_LIMIT:
        aware = candidate.replace(tzinfo=tz)
        roundtrip = aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
        if roundtrip == candidate:
            return aware
        candidate += _GAP_SEARCH_STEP
        searched += _GAP_SEARCH_STEP
    raise ValueError(f"Не удалось локализовать {naive} в {tz.key}: несуществующий момент")


def materialize_for_event(
    event: Event,
    recipients: list[tuple[Membership, Account]],
    clock: Clock,
) -> list[Reminder]:
    """Построить напоминания на MATERIALIZATION_HORIZON_DAYS вперёд для события.

    `recipients` — пары (Membership, Account): Membership даёт person_id
    получателя в семье события, Account — его настройки напоминаний.
    Собственный день рождения человека не порождает напоминание для него
    самого — сравнение идёт по Membership.person_id, а не по Account.id.

    Аккаунты с `delivery_enabled=False` пропускаются (SPEC 5.5, 403 — доставка
    отключена до повторного запуска бота). Даты наступления перебираются
    начиная с "сегодня по UTC минус 1 день", чтобы не упустить наступление,
    которое по UTC уже "прошло", а по поясу получателя — ещё нет. Из
    перебранных дат напоминание остаётся, только если due_at_utc не отстаёт от
    now больше чем на MISFIRE_GRACE и occurrence_date не раньше сегодняшней
    даты в поясе получателя — ровно то, что тик не пометит missed сразу же.
    """
    now = clock.now()
    today_utc = now.date()
    search_start = today_utc - timedelta(days=1)
    horizon = today_utc + timedelta(days=MATERIALIZATION_HORIZON_DAYS)
    occurrences = _occurrences_within_horizon(event, search_start, horizon)
    not_before = now - timedelta(seconds=MISFIRE_GRACE)

    reminders: list[Reminder] = []
    for membership, account in recipients:
        if membership.person_id == event.person_id:
            continue
        if not account.delivery_enabled:
            continue
        today_local = now.astimezone(ZoneInfo(account.timezone)).date()
        for occurrence in occurrences:
            if occurrence < today_local:
                continue
            for offset in account.offsets_days:
                due = due_at_utc(occurrence, offset, account)
                if due < not_before:
                    continue
                reminders.append(
                    Reminder(
                        id=0,
                        event_id=event.id,
                        person_id=membership.person_id,
                        offset_days=offset,
                        occurrence_date=occurrence,
                        due_at_utc=due,
                    )
                )
    return reminders


def _occurrences_within_horizon(event: Event, today: date, horizon: date) -> list[date]:
    if not event.is_recurring_yearly:
        return [event.date] if today <= event.date <= horizon else []

    occurrences: list[date] = []
    occurrence = next_occurrence(event.date, today)
    while occurrence <= horizon:
        occurrences.append(occurrence)
        occurrence = next_occurrence(event.date, occurrence + timedelta(days=1))
    return occurrences
