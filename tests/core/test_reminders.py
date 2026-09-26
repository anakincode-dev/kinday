"""Тесты due_at_utc и materialize_for_event: моменты отправки и материализация."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from kinday.core.models import Account, Event, EventKind, Membership, Reminder, ReminderStatus
from kinday.core.reminders import MISFIRE_GRACE, due_at_utc, is_overdue, materialize_for_event


class FixedClock:
    """Простая подмена Clock с зафиксированным now(), без реальных часов."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _account(account_id: int, tz: str, offsets: tuple[int, ...] = (7, 1, 0)) -> Account:
    return Account(
        id=account_id,
        telegram_user_id=account_id,
        current_family_id=1,
        timezone=tz,
        offsets_days=offsets,
        time_of_day=time(9, 0),
        chat_id=account_id,
    )


def test_due_at_utc_same_wall_clock_time_in_different_zones() -> None:
    """Критерий приёмки 5: два пояса, оба момента 09:00 по своему времени, но разные UTC."""
    moscow = _account(1, "Europe/Moscow")
    tokyo = _account(2, "Asia/Tokyo")
    occurrence = date(2027, 3, 1)

    moscow_due = due_at_utc(occurrence, 7, moscow)
    tokyo_due = due_at_utc(occurrence, 7, tokyo)

    assert moscow_due != tokyo_due
    assert moscow_due.astimezone(ZoneInfo("Europe/Moscow")).replace(tzinfo=None) == datetime(
        2027, 2, 22, 9, 0
    )
    assert tokyo_due.astimezone(ZoneInfo("Asia/Tokyo")).replace(tzinfo=None) == datetime(
        2027, 2, 22, 9, 0
    )


def test_due_at_utc_shifts_forward_through_dst_gap() -> None:
    """Перевод часов вперёд: 2024-03-10 02:30 в America/New_York не существует -> 03:00."""
    account = _account(1, "America/New_York", offsets=(0,))
    account.time_of_day = time(2, 30)
    occurrence = date(2024, 3, 10)

    due = due_at_utc(occurrence, 0, account)

    local = due.astimezone(ZoneInfo("America/New_York"))
    assert local.replace(tzinfo=None) == datetime(2024, 3, 10, 3, 0)


def test_materialize_for_event_builds_reminders_and_skips_self() -> None:
    """Критерий приёмки 15: о своём дне рождения человеку напоминание не строится."""
    clock = FixedClock(datetime(2027, 1, 1, tzinfo=UTC))
    event = Event(
        id=100,
        family_id=1,
        person_id=2,
        title="День рождения",
        date=date(1980, 3, 1),
        kind=EventKind.BIRTHDAY,
    )
    anton_membership = Membership(account_id=1, family_id=1, person_id=1)
    petr_membership = Membership(account_id=2, family_id=1, person_id=2)

    reminders = materialize_for_event(
        event,
        [
            (anton_membership, _account(1, "Europe/Moscow")),
            (petr_membership, _account(2, "Europe/Moscow")),
        ],
        clock,
    )

    assert {r.person_id for r in reminders} == {1}
    assert len(reminders) == 3
    assert {r.offset_days for r in reminders} == {7, 1, 0}
    assert all(r.occurrence_date == date(2027, 3, 1) for r in reminders)
    assert all(r.status == ReminderStatus.PENDING for r in reminders)
    assert all(r.event_id == 100 for r in reminders)


def test_materialize_for_event_does_not_skip_self_for_non_birthday_event() -> None:
    """SPEC 3.4: исключение из напоминаний всем — только собственный день рождения.

    kind по умолчанию CUSTOM: герой не-днерожденческого события (например,
    выпускной) получает напоминание о нём наравне со всеми, в отличие от дня
    рождения (см. test_materialize_for_event_builds_reminders_and_skips_self).
    """
    clock = FixedClock(datetime(2027, 1, 1, tzinfo=UTC))
    event = Event(
        id=100,
        family_id=1,
        person_id=2,
        title="Выпускной",
        date=date(2027, 6, 3),
        is_recurring_yearly=False,
    )
    marina_membership = Membership(account_id=2, family_id=1, person_id=2)

    reminders = materialize_for_event(
        event, [(marina_membership, _account(2, "Europe/Moscow", offsets=(0,)))], clock
    )

    assert {r.person_id for r in reminders} == {2}


def test_materialize_for_event_horizon_excludes_far_future_occurrence() -> None:
    """Горизонт 400 дней: следующая годовщина за пределами горизонта не строится."""
    clock = FixedClock(datetime(2027, 1, 1, tzinfo=UTC))
    event = Event(id=100, family_id=1, person_id=2, title="День рождения", date=date(1980, 3, 1))
    membership = Membership(account_id=1, family_id=1, person_id=1)

    reminders = materialize_for_event(
        event, [(membership, _account(1, "Europe/Moscow", offsets=(0,)))], clock
    )

    assert {r.occurrence_date for r in reminders} == {date(2027, 3, 1)}


def test_due_at_utc_recomputes_on_timezone_change() -> None:
    """Критерий приёмки 6: смена пояса участника меняет момент отправки."""
    occurrence = date(2027, 3, 1)
    before = due_at_utc(occurrence, 7, _account(1, "Europe/Moscow"))
    after = due_at_utc(occurrence, 7, _account(1, "Asia/Tokyo"))

    assert before != after


def test_due_at_utc_recomputes_on_event_date_change() -> None:
    """Критерий приёмки 7: смена даты события меняет момент отправки."""
    account = _account(1, "Europe/Moscow")
    before = due_at_utc(date(2027, 3, 1), 7, account)
    after = due_at_utc(date(2027, 3, 10), 7, account)

    assert before != after
    assert after - before == timedelta(days=9)


def test_materialize_for_event_skips_recipient_with_delivery_disabled() -> None:
    """SPEC 5.5, критерий 403: получателю с delivery_enabled=False напоминания не строятся."""
    clock = FixedClock(datetime(2027, 1, 1, tzinfo=UTC))
    event = Event(id=100, family_id=1, person_id=2, title="День рождения", date=date(1980, 3, 1))
    membership = Membership(account_id=1, family_id=1, person_id=1)
    account = _account(1, "Europe/Moscow", offsets=(0,))
    account.delivery_enabled = False

    reminders = materialize_for_event(event, [(membership, account)], clock)

    assert reminders == []


def test_materialize_for_event_builds_reminder_not_yet_past_in_recipient_timezone() -> None:
    """По UTC-дате событие уже "прошло", но по поясу получателя дата ещё не прошла, а
    отправка впереди — напоминание всё равно строится (поиск идёт от "сегодня по UTC
    минус 1 день", отсекается по due_at_utc и локальной дате получателя)."""
    clock = FixedClock(datetime(2027, 3, 2, 1, 0, tzinfo=UTC))
    event = Event(
        id=100,
        family_id=1,
        person_id=2,
        title="Разовое событие",
        date=date(2027, 3, 1),
        is_recurring_yearly=False,
    )
    membership = Membership(account_id=1, family_id=1, person_id=1)
    account = _account(1, "Pacific/Honolulu", offsets=(0,))
    account.time_of_day = time(20, 0)

    reminders = materialize_for_event(event, [(membership, account)], clock)

    assert len(reminders) == 1
    assert reminders[0].occurrence_date == date(2027, 3, 1)
    assert reminders[0].due_at_utc == datetime(2027, 3, 2, 6, 0, tzinfo=UTC)
    assert reminders[0].due_at_utc > clock.now()


def test_materialize_for_event_drops_reminder_overdue_more_than_misfire_grace() -> None:
    """due_at_utc старше now на 24 часа и больше — напоминание не строится (см. MISFIRE_GRACE,
    иначе тик тут же пометил бы его missed, см. SPEC 5.5).

    occurrence_date (2027-03-05) намеренно оставлена в будущем относительно today_local,
    чтобы просрочку давал исключительно большой offset_days, а не фильтр по локальной дате
    получателя — иначе тест прошёл бы даже без проверки MISFIRE_GRACE.
    """
    clock = FixedClock(datetime(2027, 3, 2, 1, 0, tzinfo=UTC))
    event = Event(
        id=100,
        family_id=1,
        person_id=2,
        title="Разовое событие",
        date=date(2027, 3, 5),
        is_recurring_yearly=False,
    )
    membership = Membership(account_id=1, family_id=1, person_id=1)
    account = _account(1, "UTC", offsets=(5,))
    account.time_of_day = time(0, 0)

    reminders = materialize_for_event(event, [(membership, account)], clock)

    assert reminders == []


def test_materialize_for_event_keeps_reminder_overdue_less_than_misfire_grace() -> None:
    """Опоздание меньше 24 часов при непрошедшей (в поясе получателя) дате — строится."""
    clock = FixedClock(datetime(2027, 3, 2, 1, 0, tzinfo=UTC))
    event = Event(
        id=100,
        family_id=1,
        person_id=2,
        title="Разовое событие",
        date=date(2027, 3, 1),
        is_recurring_yearly=False,
    )
    membership = Membership(account_id=1, family_id=1, person_id=1)
    account = _account(1, "Pacific/Honolulu", offsets=(0,))
    account.time_of_day = time(10, 0)

    reminders = materialize_for_event(event, [(membership, account)], clock)

    assert len(reminders) == 1
    assert reminders[0].due_at_utc == datetime(2027, 3, 1, 20, 0, tzinfo=UTC)


def test_materialize_for_event_feb_29_gives_feb_28_occurrence_in_common_year() -> None:
    """Критерий 4 на уровне напоминаний: 29 февраля в невисокосный год -> 28 февраля."""
    clock = FixedClock(datetime(2027, 1, 15, tzinfo=UTC))
    event = Event(id=100, family_id=1, person_id=2, title="День рождения", date=date(1980, 2, 29))
    membership = Membership(account_id=1, family_id=1, person_id=1)

    reminders = materialize_for_event(
        event, [(membership, _account(1, "Europe/Moscow", offsets=(0,)))], clock
    )

    assert {r.occurrence_date for r in reminders} == {date(2027, 2, 28)}


def test_due_at_utc_ambiguous_fall_back_time_takes_first_moment() -> None:
    """Неоднозначное время при переводе назад (Europe/Berlin): берётся первый момент (fold=0)."""
    account = _account(1, "Europe/Berlin", offsets=(0,))
    account.time_of_day = time(2, 30)
    occurrence = date(2027, 10, 31)

    due = due_at_utc(occurrence, 0, account)

    assert due == datetime(2027, 10, 31, 0, 30, tzinfo=UTC)


def test_materialize_for_event_only_builds_future_occurrences() -> None:
    """Критерии 6, 7: перематериализация строит только будущее, прошлое не переписывается.

    materialize_for_event — чистая функция без состояния, поэтому "не трогать
    прошлое" здесь означает, что она никогда не порождает напоминания на уже
    прошедшую дату события: пересчёт при смене пояса/даты/смещений (SPEC 5.6)
    заменяет только future pending-строки, а сама материализация не может
    случайно создать напоминание в прошлом.
    """
    clock = FixedClock(datetime(2027, 3, 15, tzinfo=UTC))
    event = Event(id=100, family_id=1, person_id=2, title="День рождения", date=date(1980, 3, 1))
    membership = Membership(account_id=1, family_id=1, person_id=1)

    reminders = materialize_for_event(
        event, [(membership, _account(1, "Europe/Moscow", offsets=(0,)))], clock
    )

    assert all(r.occurrence_date >= clock.now().date() for r in reminders)
    assert date(2027, 3, 1) not in {r.occurrence_date for r in reminders}


def _reminder(occurrence: date, due: datetime) -> Reminder:
    return Reminder(
        id=1, event_id=100, person_id=1, offset_days=0, occurrence_date=occurrence, due_at_utc=due
    )


def test_is_overdue_false_for_fresh_reminder() -> None:
    """Опоздание на два часа просрочкой не считается — напоминание ещё уходит (критерий 12)."""
    reminder = _reminder(date(2027, 3, 1), datetime(2027, 3, 1, 6, tzinfo=UTC))

    assert is_overdue(reminder, datetime(2027, 3, 1, 8, tzinfo=UTC), "Europe/Moscow") is False


def test_is_overdue_true_after_misfire_grace() -> None:
    """Простой дольше MISFIRE_GRACE: срок устарел, напоминание пропускается."""
    reminder = _reminder(date(2027, 3, 1), datetime(2027, 3, 1, 6, tzinfo=UTC))
    late = datetime(2027, 3, 1, 6, tzinfo=UTC) + timedelta(seconds=MISFIRE_GRACE + 1)

    assert is_overdue(reminder, late, "Europe/Moscow") is True


def test_is_overdue_true_when_occurrence_date_already_passed() -> None:
    """Вторая ветка просрочки: срок свежий, но дата события в поясе получателя уже прошла.

    Напоминание «в день события» 1 марта должно было уйти в 09:00 по Москве,
    а тик добрался до него через шестнадцать часов — по московскому времени уже
    2 марта, и сообщение «сегодня день рождения» говорило бы о прошедшем дне.
    Простой при этом меньше MISFIRE_GRACE, так что первая ветка молчит.
    """
    reminder = _reminder(date(2027, 3, 1), datetime(2027, 3, 1, 6, tzinfo=UTC))
    late = datetime(2027, 3, 1, 22, tzinfo=UTC)

    assert is_overdue(reminder, late, "Europe/Moscow") is True
    # Тот же момент в поясе западнее: там всё ещё 1 марта, просрочки нет.
    assert is_overdue(reminder, late, "America/New_York") is False
