"""Сквозной сценарий из SPEC 9: от создания семьи до отправленного сообщения.

Отличие от тестов этапов 1–5: там сценарии ядра и тик проверялись на двух
бэкендах сразу, и половина прогонов шла на фейковых репозиториях. Здесь
бэкенд один и настоящий — файл SQLite с миграциями, применёнными так же, как
при старте сервиса, — а сценарий идёт подряд, без подготовки состояния в обход
портов: создание семьи, /start, добавление отца, тик.

Подменены ровно две вещи, обе по SPEC 9: часы (`FixedClock`) и транспорт
(`RecordingNotifier`). Слой Telegram не импортируется вовсе: от него в сценарии
нужен только `Notifier`, который и подменён, а импорт aiogram стоит секунды —
больше, чем весь бюджет этого теста.

Время двигается переводом часов, а не `sleep`, поэтому тест укладывается
меньше чем в секунду.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from tests.core.fakes import FixedClock, RecordingNotifier
from tests.scenario_repos import World
from tests.scenarios import add_person_to, create_family_for, enable_delivery_in

from kinday.core.models import Gender, ReminderStatus
from kinday.core.relations import RelationKind
from kinday.scheduler.tick import run_tick

# SPEC 9, шаг 1: часы подменены на эту точку.
START = datetime(2027, 2, 15, tzinfo=UTC)
# SPEC 9, шаг 4: 09:00 в Москве, ровно за 7 дней до 1 марта.
DUE = datetime(2027, 2, 22, 6, tzinfo=UTC)
OCCURRENCE = date(2027, 3, 1)
# Смещения по умолчанию (за 7 дней, за 1 день, в день события) и время 09:00 из
# SPEC 9 записаны здесь литералами, а не константами `core.services`: сравнение
# с константой прошло бы при любом её значении, а сценарий SPEC 9 держится
# именно на этих трёх сроках.
DUE_BY_OFFSET = {
    7: datetime(2027, 2, 22, 6, tzinfo=UTC),
    1: datetime(2027, 2, 28, 6, tzinfo=UTC),
    0: datetime(2027, 3, 1, 6, tzinfo=UTC),
}

ANTON_TELEGRAM_USER_ID = 100
ANTON_CHAT_ID = 500
# SPEC 9, шаг 2: дата выбрана так, чтобы день рождения Антона (15 июня) не
# попадал в окно теста и не давал второго сообщения.
ANTON_BIRTH = date(1990, 6, 15)
PETR_BIRTH = date(1980, 3, 1)
TIMEZONE = "Europe/Moscow"

EXPECTED_TEXT = "Через 7 дней день рождения — Пётр, ваш отец. 1 марта, исполнится 47."


async def _tick(world: World, clock: FixedClock, notifier: RecordingNotifier) -> None:
    await run_tick(
        clock,
        notifier,
        world.account,
        world.event,
        world.membership,
        world.person,
        world.relation,
        world.reminder,
        world.uow,
    )


@pytest.mark.asyncio
async def test_reminder_flow(sqlite_world: World) -> None:
    """SPEC 9 целиком: семья, отец, перевод часов, один тик — ровно одно сообщение.

    Повторный тик в ту же минуту список сообщений не меняет (критерий приёмки 8),
    а строка в базе остаётся `sent`.
    """
    world = sqlite_world
    clock = FixedClock(START)
    notifier = RecordingNotifier()

    # Шаг 2: семья и владелец Антон. Смещения и время суток — умолчания SPEC 9.
    family = await create_family_for(
        world,
        clock=clock,
        telegram_user_id=ANTON_TELEGRAM_USER_ID,
        name="Антон",
        birth_date=ANTON_BIRTH,
        gender=Gender.MALE,
        timezone=TIMEZONE,
    )
    [anton] = await world.person.list_by_family(family.id)
    [membership] = await world.membership.list_by_family(family.id)
    account = await world.account.get(membership.account_id)
    assert account.timezone == TIMEZONE

    # Чат привязывается так же, как в боте: /start вызывает enable_delivery.
    await enable_delivery_in(world, clock=clock, account_id=account.id, chat_id=ANTON_CHAT_ID)

    # Шаг 3: отец Пётр. Напоминания строятся сразу, без суточного задания.
    petr = await add_person_to(
        world,
        clock=clock,
        acting_account_id=account.id,
        family_id=family.id,
        name="Пётр",
        birth_date=PETR_BIRTH,
        gender=Gender.MALE,
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=anton.id,
    )
    # Все три срока по ближайшему дню рождения Петра построены сразу и ровно на
    # 09:00 по Москве — этим закреплены и смещения, и время суток из SPEC 9.
    built = await world.read_reminders()
    assert {
        r.offset_days: r.due_at_utc for r in built if r.occurrence_date == OCCURRENCE
    } == DUE_BY_OFFSET

    # Напоминания относятся только к дню рождения Петра: сам себе о своём дне
    # рождения Антон напоминания не получает (критерий приёмки 15), а у Петра нет
    # аккаунта, так что второму сообщению взяться неоткуда.
    events = {event.person_id: event.id for event in await world.event.list_by_family(family.id)}
    assert {r.event_id for r in built} == {events[petr.id]}
    assert {r.person_id for r in built} == {anton.id}

    # Шаги 4 и 5: часы на 09:00 по Москве и один тик.
    clock.move_to(DUE)
    await _tick(world, clock, notifier)

    assert notifier.sent == [(ANTON_CHAT_ID, EXPECTED_TEXT)]

    # Повторный тик в ту же минуту: список сообщений не меняется.
    await _tick(world, clock, notifier)
    assert notifier.sent == [(ANTON_CHAT_ID, EXPECTED_TEXT)]

    sent = [r for r in await world.read_reminders() if r.status == ReminderStatus.SENT]
    assert len(sent) == 1
    assert sent[0].offset_days == 7
    assert sent[0].occurrence_date == OCCURRENCE
    assert sent[0].sent_at == DUE
