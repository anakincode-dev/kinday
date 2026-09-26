"""Тик планировщика на двух хранилищах: фейки и настоящий SQLite.

PLAN.md, этап 5. Критерии приёмки: 8 (повторный тик в ту же минуту не шлёт
второго сообщения), 9 (строка, застрявшая в sending, закрывается при старте),
10 (403 отключает доставку), 11 (сетевая ошибка и пятая неудача), 12 (просрочка
в missed, опоздание на два часа — отправляется), 13 («через N дней» считается
в момент отправки).

Часы и транспорт подменены (`FixedClock`, `RecordingNotifier`), тик гоняется
несколько раз подряд без `sleep`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from tests.core.fakes import FixedClock, RecordingNotifier
from tests.scenario_repos import World
from tests.scenarios import add_event_to, add_person_to, create_family_for

from kinday.core.models import ReminderStatus
from kinday.core.ports import Clock, RecipientBlocked, TransportError
from kinday.core.relations import RelationKind
from kinday.scheduler.tick import MAX_ATTEMPTS, fail_stuck_sending, run_tick

ANTON_CHAT_ID = 500
ANTON_BIRTH = date(1990, 6, 15)
PETR_BIRTH = date(1980, 3, 1)

# Дата выбрана как в SPEC 9: день рождения Антона в окно теста не попадает,
# а до дня рождения Петра (1 марта) остаётся чуть больше двух недель.
START = datetime(2027, 2, 15, tzinfo=UTC)

# 09:00 в Москве за 7 дней до 1 марта 2027 года.
DUE_SEVEN_DAYS = datetime(2027, 2, 22, 6, tzinfo=UTC)
# То же самое за один день.
DUE_ONE_DAY = datetime(2027, 2, 28, 6, tzinfo=UTC)

SEVEN_DAYS_TEXT = "Через 7 дней день рождения — Пётр, ваш отец. 1 марта, исполнится 47."


async def _family_with_father(world: World, clock: Clock) -> tuple[int, int, int]:
    """Семья Антона с привязанным чатом и его отец Пётр. Возвращает id семьи, аккаунта, Петра."""
    family = await create_family_for(
        world, clock=clock, telegram_user_id=100, name="Антон", birth_date=ANTON_BIRTH
    )
    [anton] = await world.person.list_by_family(family.id)
    [membership] = await world.membership.list_by_family(family.id)
    account = await world.account.get(membership.account_id)
    account.chat_id = ANTON_CHAT_ID
    await world.account.save(account)
    petr = await add_person_to(
        world,
        clock=clock,
        acting_account_id=account.id,
        family_id=family.id,
        name="Пётр",
        birth_date=PETR_BIRTH,
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=anton.id,
    )
    return family.id, account.id, petr.id


async def _tick(world: World, clock: Clock, notifier: RecordingNotifier) -> None:
    await run_tick(
        clock,
        notifier,
        world.account,
        world.event,
        world.membership,
        world.person,
        world.relation,
        world.reminder,
    )


async def _statuses(world: World) -> list[ReminderStatus]:
    return [reminder.status for reminder in await world.read_reminders()]


@pytest.mark.asyncio
async def test_second_tick_in_same_minute_sends_nothing(world: World) -> None:
    """Критерий 8: повторный запуск тика в ту же минуту не создаёт второго сообщения."""
    clock = FixedClock(START)
    await _family_with_father(world, clock)
    notifier = RecordingNotifier()

    clock.move_to(DUE_SEVEN_DAYS)
    await _tick(world, clock, notifier)
    await _tick(world, clock, notifier)

    assert notifier.sent == [(ANTON_CHAT_ID, SEVEN_DAYS_TEXT)]
    sent = [r for r in await world.read_reminders() if r.status == ReminderStatus.SENT]
    assert len(sent) == 1
    assert sent[0].sent_at == DUE_SEVEN_DAYS


@pytest.mark.asyncio
async def test_days_until_counted_at_send_time(world: World) -> None:
    """Критерий 13: «через N дней» считается в момент отправки, а не материализации.

    Напоминание построено как «за 7 дней», но уходит на 20 часов позже срока —
    по московскому времени уже наступил следующий день, значит в тексте 6 дней.
    """
    clock = FixedClock(START)
    await _family_with_father(world, clock)
    notifier = RecordingNotifier()

    clock.move_to(DUE_SEVEN_DAYS + timedelta(hours=20))
    await _tick(world, clock, notifier)

    assert notifier.sent == [
        (ANTON_CHAT_ID, "Через 6 дней день рождения — Пётр, ваш отец. 1 марта, исполнится 47.")
    ]


@pytest.mark.asyncio
async def test_sending_row_fails_on_startup_and_is_not_resent(world: World) -> None:
    """Критерий 9: падение между send и mark не приводит к двойной отправке.

    Строка, оставшаяся в sending, при старте сервиса переводится в failed
    и следующим тиком не забирается.
    """
    clock = FixedClock(START)
    await _family_with_father(world, clock)
    notifier = RecordingNotifier()

    clock.move_to(DUE_SEVEN_DAYS)
    claimed = await world.reminder.claim_due(clock.now(), 10)
    assert [r.status for r in claimed] == [ReminderStatus.SENDING]

    await fail_stuck_sending(clock, world.reminder)
    await _tick(world, clock, notifier)

    assert notifier.sent == []
    statuses = await _statuses(world)
    assert statuses.count(ReminderStatus.FAILED) == 1
    [failed] = [r for r in await world.read_reminders() if r.status == ReminderStatus.FAILED]
    assert failed.due_at_utc == DUE_SEVEN_DAYS


@pytest.mark.asyncio
async def test_blocked_recipient_disables_delivery(world: World) -> None:
    """Критерий 10: 403 переводит напоминание в failed и отключает доставку человеку."""
    clock = FixedClock(START)
    _, account_id, _ = await _family_with_father(world, clock)
    notifier = RecordingNotifier(error=RecipientBlocked("бот заблокирован"))

    clock.move_to(DUE_SEVEN_DAYS)
    await _tick(world, clock, notifier)

    account = await world.account.get(account_id)
    assert account.delivery_enabled is False
    assert account.chat_id is None
    failed = [r for r in await world.read_reminders() if r.status == ReminderStatus.FAILED]
    assert len(failed) == 1

    notifier.error = None
    clock.move_to(DUE_ONE_DAY)
    await _tick(world, clock, notifier)

    assert notifier.sent == []


@pytest.mark.asyncio
async def test_transport_error_returns_to_pending_and_next_tick_retries(world: World) -> None:
    """Критерий 11: сетевая ошибка возвращает в pending, следующий тик повторяет отправку."""
    clock = FixedClock(START)
    await _family_with_father(world, clock)
    notifier = RecordingNotifier(error=TransportError("таймаут"))

    clock.move_to(DUE_SEVEN_DAYS)
    await _tick(world, clock, notifier)

    [pending] = [r for r in await world.read_reminders() if r.due_at_utc == DUE_SEVEN_DAYS]
    assert pending.status == ReminderStatus.PENDING
    assert pending.attempts == 1
    assert notifier.sent == []

    notifier.error = None
    clock.move_to(DUE_SEVEN_DAYS + timedelta(minutes=1))
    await _tick(world, clock, notifier)

    assert notifier.sent == [(ANTON_CHAT_ID, SEVEN_DAYS_TEXT)]


@pytest.mark.asyncio
async def test_fifth_transport_failure_marks_failed(world: World) -> None:
    """Критерий 11: после пятой неудачи строка становится failed и больше не пробуется."""
    clock = FixedClock(START)
    await _family_with_father(world, clock)
    notifier = RecordingNotifier(error=TransportError("таймаут"))

    for minute in range(MAX_ATTEMPTS):
        clock.move_to(DUE_SEVEN_DAYS + timedelta(minutes=minute))
        await _tick(world, clock, notifier)

    [reminder] = [r for r in await world.read_reminders() if r.due_at_utc == DUE_SEVEN_DAYS]
    assert reminder.status == ReminderStatus.FAILED
    assert reminder.attempts == MAX_ATTEMPTS

    notifier.error = None
    clock.move_to(DUE_SEVEN_DAYS + timedelta(minutes=MAX_ATTEMPTS))
    await _tick(world, clock, notifier)

    assert notifier.sent == []


@pytest.mark.asyncio
async def test_overdue_missed_and_two_hours_late_sent(world: World) -> None:
    """Критерий 12: простой в 48 часов даёт missed, опоздание на 2 часа — обычную отправку."""
    clock = FixedClock(START)
    await _family_with_father(world, clock)
    notifier = RecordingNotifier()

    clock.move_to(DUE_SEVEN_DAYS + timedelta(hours=48))
    await _tick(world, clock, notifier)

    assert notifier.sent == []
    [overdue] = [r for r in await world.read_reminders() if r.due_at_utc == DUE_SEVEN_DAYS]
    assert overdue.status == ReminderStatus.MISSED

    clock.move_to(DUE_ONE_DAY + timedelta(hours=2))
    await _tick(world, clock, notifier)

    assert notifier.sent == [
        (ANTON_CHAT_ID, "Завтра день рождения — Пётр, ваш отец. 1 марта, исполнится 47.")
    ]


@pytest.mark.asyncio
async def test_tick_sends_every_due_reminder(world: World) -> None:
    """Один тик разбирает все подошедшие строки, а не первую из них.

    У Петра два ежегодных события на 1 марта, оба со смещением «за 7 дней»,
    значит обе строки подходят в один и тот же момент. Тик обязан отправить оба
    сообщения: иначе второе ждало бы следующей минуты без всякой причины.
    """
    clock = FixedClock(START)
    family_id, account_id, petr_id = await _family_with_father(world, clock)
    await add_event_to(
        world,
        clock=clock,
        acting_account_id=account_id,
        family_id=family_id,
        person_id=petr_id,
        title="Годовщина свадьбы",
        event_date=date(2002, 3, 1),
        is_recurring_yearly=True,
    )
    notifier = RecordingNotifier()

    clock.move_to(DUE_SEVEN_DAYS)
    await _tick(world, clock, notifier)

    assert sorted(notifier.sent) == sorted(
        [
            (ANTON_CHAT_ID, SEVEN_DAYS_TEXT),
            (
                ANTON_CHAT_ID,
                "Через 7 дней: Годовщина свадьбы — Пётр, ваш отец. 1 марта, 25 лет.",
            ),
        ]
    )
    due_now = [r for r in await world.read_reminders() if r.due_at_utc == DUE_SEVEN_DAYS]
    assert [r.status for r in due_now] == [ReminderStatus.SENT, ReminderStatus.SENT]


@pytest.mark.asyncio
async def test_pending_row_with_exhausted_attempts_is_closed_without_sending(
    world: World,
) -> None:
    """Строка с израсходованными попытками закрывается до отправки, а не пробуется в шестой раз.

    Такое состояние остаётся после падения сервиса между `release` и
    `mark_failed`: счётчик уже равен пяти, а статус ещё pending. SPEC 5.5
    разрешает ровно пять попыток, поэтому тик такую строку только закрывает.
    """
    clock = FixedClock(START)
    await _family_with_father(world, clock)
    notifier = RecordingNotifier()

    clock.move_to(DUE_SEVEN_DAYS)
    for _ in range(MAX_ATTEMPTS):
        [claimed] = await world.reminder.claim_due(clock.now(), 1)
        await world.reminder.release(claimed.id)
    [before] = [r for r in await world.read_reminders() if r.due_at_utc == DUE_SEVEN_DAYS]
    assert (before.status, before.attempts) == (ReminderStatus.PENDING, MAX_ATTEMPTS)

    await _tick(world, clock, notifier)

    assert notifier.sent == []
    [after] = [r for r in await world.read_reminders() if r.due_at_utc == DUE_SEVEN_DAYS]
    assert after.status == ReminderStatus.FAILED
    assert after.attempts == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_overdue_reminder_with_disabled_delivery_is_missed(world: World) -> None:
    """Просрочка сильнее отключённой доставки: строка становится missed, а не failed.

    Аккаунт успел заблокировать бота, и за те же двое суток простоя строка
    протухла. Критерий приёмки 12 требует для неё missed — статус описывает
    причину «не отправили» честно, а failed прятал бы простой за отказом
    доставки.
    """
    clock = FixedClock(START)
    _, account_id, _ = await _family_with_father(world, clock)
    account = await world.account.get(account_id)
    account.delivery_enabled = False
    account.chat_id = None
    await world.account.save(account)
    notifier = RecordingNotifier()

    clock.move_to(DUE_SEVEN_DAYS + timedelta(hours=48))
    await _tick(world, clock, notifier)

    assert notifier.sent == []
    [reminder] = [r for r in await world.read_reminders() if r.due_at_utc == DUE_SEVEN_DAYS]
    assert reminder.status == ReminderStatus.MISSED


@pytest.mark.asyncio
async def test_reminder_without_chat_is_not_sent(world: World) -> None:
    """Без привязанного чата отправлять некуда: строка закрывается как failed, а не висит."""
    clock = FixedClock(START)
    _, account_id, _ = await _family_with_father(world, clock)
    account = await world.account.get(account_id)
    account.chat_id = None
    await world.account.save(account)
    notifier = RecordingNotifier()

    clock.move_to(DUE_SEVEN_DAYS)
    await _tick(world, clock, notifier)

    assert notifier.sent == []
    [reminder] = [r for r in await world.read_reminders() if r.due_at_utc == DUE_SEVEN_DAYS]
    assert reminder.status == ReminderStatus.FAILED
