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

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from types import TracebackType

import pytest
from tests.core.fakes import FixedClock, RecordingNotifier
from tests.scenario_repos import World
from tests.scenarios import (
    accept_invite_in,
    add_event_to,
    add_person_to,
    create_family_for,
    enable_delivery_in,
    issue_invite_in,
)

from kinday.core.models import ReminderStatus
from kinday.core.ports import Clock, Notifier, RecipientBlocked, TransportError, UnitOfWork
from kinday.core.relations import RelationKind
from kinday.scheduler.tick import MAX_ATTEMPTS, fail_stuck_sending, run_tick

ANTON_CHAT_ID = 500
PETR_CHAT_ID = 501
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


async def _tick(
    world: World,
    clock: Clock,
    notifier: Notifier,
    unit_of_work: UnitOfWork | None = None,
) -> None:
    await run_tick(
        clock,
        notifier,
        world.account,
        world.event,
        world.membership,
        world.person,
        world.relation,
        world.reminder,
        unit_of_work or world.uow,
    )


async def _statuses(world: World) -> list[ReminderStatus]:
    return [reminder.status for reminder in await world.read_reminders()]


class _TrackingUnitOfWork:
    """Обёртка над настоящей единицей работы: считает входы и текущую глубину.

    Обёртка, а не подмена, чтобы тест работал на обоих бэкендах: внутрь уходит
    `world.uow`, то есть на SQLite проверяется настоящая транзакция.
    """

    def __init__(self, inner: UnitOfWork) -> None:
        self._inner = inner
        self.entered = 0
        self.depth = 0

    async def __aenter__(self) -> _TrackingUnitOfWork:
        await self._inner.__aenter__()
        self.entered += 1
        self.depth += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.depth -= 1
        await self._inner.__aexit__(exc_type, exc, traceback)


@dataclass
class _NotifierWatchingTransaction:
    """Notifier, который запоминает глубину открытых транзакций на момент отправки."""

    unit_of_work: _TrackingUnitOfWork
    sent: list[tuple[int, str]] = field(default_factory=list)
    depth_on_send: list[int] = field(default_factory=list)
    entered_on_send: list[int] = field(default_factory=list)

    async def send(self, chat_id: int, text: str) -> None:
        self.depth_on_send.append(self.unit_of_work.depth)
        self.entered_on_send.append(self.unit_of_work.entered)
        self.sent.append((chat_id, text))


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
    # Судьба остальных строк аккаунта — предмет
    # test_blocked_recipient_closes_all_his_pending_reminders.
    claimed = [r for r in await world.read_reminders() if r.due_at_utc == DUE_SEVEN_DAYS]
    assert [r.status for r in claimed] == [ReminderStatus.FAILED]

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


@pytest.mark.asyncio
async def test_blocked_recipient_closes_his_pending_in_all_families_only(world: World) -> None:
    """403 закрывает pending заблокировавшего аккаунта во всех его семьях — и только его.

    Аккаунт заблокировал бота, значит каждая следующая отправка ему вернёт тот
    же 403. Оставить строки в pending означало бы вызывать Telegram зря на каждом
    тике до конца горизонта, поэтому они закрываются сразу — во всех семьях, ведь
    блокировка у аккаунта одна на всех. Напоминания остальных получателей при
    этом обязаны уцелеть: они бота не блокировали.

    У Антона две семьи (SPEC 2.1), во второй — его мать Марина. Второй
    получатель — Пётр, принявший приглашение в первой семье.
    """
    clock = FixedClock(START)
    first_family_id, account_id, petr_id = await _family_with_father(world, clock)
    [anton] = [p for p in await world.person.list_by_family(first_family_id) if p.id != petr_id]
    second_family = await create_family_for(
        world, clock=clock, telegram_user_id=100, name="Антон", birth_date=ANTON_BIRTH
    )
    [anton_in_second] = await world.person.list_by_family(second_family.id)
    await add_person_to(
        world,
        clock=clock,
        acting_account_id=account_id,
        family_id=second_family.id,
        name="Марина",
        birth_date=date(1962, 3, 1),
        relation_kind=RelationKind.MOTHER,
        relative_to_person_id=anton_in_second.id,
    )
    invite = await issue_invite_in(
        world, clock=clock, acting_account_id=account_id, person_id=petr_id
    )
    await accept_invite_in(world, clock=clock, code=invite.code, telegram_user_id=200)
    petr_account = await world.account.get_by_telegram_user_id(200)
    assert petr_account is not None
    petr_account.chat_id = PETR_CHAT_ID
    await world.account.save(petr_account)
    notifier = RecordingNotifier(error=RecipientBlocked("бот заблокирован"))

    clock.move_to(DUE_SEVEN_DAYS)
    await _tick(world, clock, notifier)

    reminders = await world.read_reminders()
    anton_ids = {anton.id, anton_in_second.id}
    for reminder in reminders:
        expected = (
            ReminderStatus.FAILED if reminder.person_id in anton_ids else (ReminderStatus.PENDING)
        )
        assert reminder.status == expected, reminder
    # Обе семьи Антона действительно представлены в выборке, иначе проверка пуста.
    assert {r.person_id for r in reminders} == anton_ids | {petr_id}
    account = await world.account.get(account_id)
    assert (account.delivery_enabled, account.chat_id) == (False, None)


@pytest.mark.asyncio
async def test_delivery_resumes_after_account_returns(world: World) -> None:
    """После повторного запуска бота напоминания идут снова (SPEC 5.5).

    403 закрыл весь построенный горизонт как failed, и сами по себе такие строки
    не воскресают: уникальный индекс (SPEC 5.3) не даёт вставить на их место
    новые. Поэтому `enable_delivery` удаляет закрытые строки будущего и строит
    горизонт заново — иначе вернувшийся аккаунт молчал бы навсегда.
    """
    clock = FixedClock(START)
    _, account_id, _ = await _family_with_father(world, clock)
    blocked = RecordingNotifier(error=RecipientBlocked("бот заблокирован"))

    clock.move_to(DUE_SEVEN_DAYS)
    await _tick(world, clock, blocked)
    assert await _statuses(world) == [ReminderStatus.FAILED] * len(await world.read_reminders())

    await enable_delivery_in(world, clock=clock, account_id=account_id, chat_id=ANTON_CHAT_ID)
    notifier = RecordingNotifier()
    clock.move_to(DUE_ONE_DAY)
    await _tick(world, clock, notifier)

    assert notifier.sent == [
        (ANTON_CHAT_ID, "Завтра день рождения — Пётр, ваш отец. 1 марта, исполнится 47.")
    ]
    # Строка, на которой пришёл 403, восстановлена вместе с остальным горизонтом, но
    # за сутки простоя просрочилась и закрылась как missed — повторно её не слали.
    [overdue] = [r for r in await world.read_reminders() if r.due_at_utc == DUE_SEVEN_DAYS]
    assert overdue.status == ReminderStatus.MISSED


@pytest.mark.asyncio
async def test_tick_stops_at_row_limit_and_next_tick_takes_the_rest(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тик разбирает не больше MAX_REMINDERS_PER_TICK строк, остаток уходит следующим.

    Лимит нужен при max_instances=1: накопившийся после простоя хвост иначе
    растянул бы один тик на сотни сетевых вызовов. Настоящее значение (сто) в
    тесте подменено на два — важно само поведение на границе, а не число.
    """
    clock = FixedClock(START)
    family_id, account_id, petr_id = await _family_with_father(world, clock)
    for title in ("Годовщина свадьбы", "Выпускной"):
        await add_event_to(
            world,
            clock=clock,
            acting_account_id=account_id,
            family_id=family_id,
            person_id=petr_id,
            title=title,
            event_date=date(2002, 3, 1),
            is_recurring_yearly=True,
        )
    monkeypatch.setattr("kinday.scheduler.tick.MAX_REMINDERS_PER_TICK", 2)
    notifier = RecordingNotifier()

    clock.move_to(DUE_SEVEN_DAYS)
    await _tick(world, clock, notifier)

    due_now = [r for r in await world.read_reminders() if r.due_at_utc == DUE_SEVEN_DAYS]
    assert len(due_now) == 3
    assert len(notifier.sent) == 2
    assert sorted(r.status.value for r in due_now) == ["pending", "sent", "sent"]

    await _tick(world, clock, notifier)

    assert len(notifier.sent) == 3
    due_now = [r for r in await world.read_reminders() if r.due_at_utc == DUE_SEVEN_DAYS]
    assert [r.status for r in due_now] == [ReminderStatus.SENT] * 3


@pytest.mark.asyncio
async def test_send_runs_outside_any_transaction(world: World) -> None:
    """Notifier.send вызывается вне единицы работы, claim и mark — в разных транзакциях.

    Отправка — сетевой вызов: держать на ней замок базы значит остановить всё
    остальное на время таймаута Telegram. Поэтому шагов три и каждый
    фиксируется отдельно (SPEC 5.5).

    Строк в тесте две, и это важно: по числу открытых транзакций на момент
    каждой отправки видно, что mark ставится сразу после своей строки. Первая
    отправка идёт после одного входа (claim), вторая — после трёх (claim, mark
    первой, claim второй). Реализация, копящая все mark до конца тика, дала бы
    здесь [1, 2].
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
    tracking = _TrackingUnitOfWork(world.uow)
    notifier = _NotifierWatchingTransaction(tracking)

    clock.move_to(DUE_SEVEN_DAYS)
    await _tick(world, clock, notifier, tracking)

    assert len(notifier.sent) == 2
    assert notifier.depth_on_send == [0, 0]
    assert notifier.entered_on_send == [1, 3]
    assert tracking.depth == 0


@pytest.mark.asyncio
async def test_days_until_counted_in_recipient_timezone(world: World) -> None:
    """Критерий 13: «через N дней» считается от «сегодня» получателя, а не по UTC.

    Аккаунт в Токио, напоминание «за 7 дней» ушло с опозданием на 16 часов: по
    UTC всё ещё 22 февраля, а в Токио наступило 23-е. Отсчёт идёт от токийской
    даты, поэтому в тексте шесть дней, а не семь.
    """
    clock = FixedClock(START)
    family = await create_family_for(
        world, clock=clock, telegram_user_id=100, name="Антон", birth_date=ANTON_BIRTH
    )
    [anton] = await world.person.list_by_family(family.id)
    [membership] = await world.membership.list_by_family(family.id)
    account = await world.account.get(membership.account_id)
    account.chat_id = ANTON_CHAT_ID
    account.timezone = "Asia/Tokyo"
    await world.account.save(account)
    await add_person_to(
        world,
        clock=clock,
        acting_account_id=account.id,
        family_id=family.id,
        name="Пётр",
        birth_date=PETR_BIRTH,
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=anton.id,
    )
    notifier = RecordingNotifier()

    # 09:00 в Токио за 7 дней до 1 марта — это 22 февраля 00:00 UTC.
    clock.move_to(datetime(2027, 2, 22, 16, tzinfo=UTC))
    await _tick(world, clock, notifier)

    assert notifier.sent == [
        (ANTON_CHAT_ID, "Через 6 дней день рождения — Пётр, ваш отец. 1 марта, исполнится 47.")
    ]
