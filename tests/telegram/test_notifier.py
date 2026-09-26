"""TelegramNotifier переводит ошибки aiogram в исключения ядра (SPEC 5.2).

Ядро не знает кодов Telegram: 403 приходит к нему как RecipientBlocked, сетевой
сбой — как TransportError. Проверяется именно перевод, потому что на нём стоят
две ветки таблицы обработки ошибок из SPEC 5.5 (критерии приёмки 10 и 11).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import pytest
from aiogram.exceptions import (
    RestartingTelegram,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import SendMessage
from tests.core.fakes import FixedClock
from tests.scenario_repos import World
from tests.scenarios import add_person_to, create_family_for

from kinday.core.models import ReminderStatus
from kinday.core.ports import RecipientBlocked, TransportError
from kinday.core.relations import RelationKind
from kinday.scheduler.tick import run_tick
from kinday.telegram.notifier import TelegramNotifier


class _RecordingBot:
    """Заглушка aiogram Bot: запоминает вызов или поднимает заданную ошибку."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.calls.append((chat_id, text))
        if self.error is not None:
            raise self.error


def _method() -> SendMessage:
    return SendMessage(chat_id=1, text="текст")


@pytest.mark.asyncio
async def test_send_passes_chat_id_and_text() -> None:
    bot = _RecordingBot()

    await TelegramNotifier(bot).send(555, "Сегодня день рождения — Пётр, ваш отец.")

    assert bot.calls == [(555, "Сегодня день рождения — Пётр, ваш отец.")]


@pytest.mark.asyncio
async def test_forbidden_becomes_recipient_blocked() -> None:
    """Критерий 10: 403 доходит до тика как RecipientBlocked, а не как ошибка aiogram."""
    bot = _RecordingBot(TelegramForbiddenError(method=_method(), message="bot was blocked"))

    with pytest.raises(RecipientBlocked):
        await TelegramNotifier(bot).send(555, "текст")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        TelegramNetworkError(method=_method(), message="connection reset"),
        TelegramServerError(method=_method(), message="internal error"),
        RestartingTelegram(method=_method(), message="restarting"),
        TelegramRetryAfter(method=_method(), message="flood", retry_after=5),
        TimeoutError("таймаут"),
    ],
)
async def test_transient_failures_become_transport_error(error: Exception) -> None:
    """Критерий 11: временный сбой — повод вернуть напоминание в pending, не закрыть его."""
    bot = _RecordingBot(error)

    with pytest.raises(TransportError):
        await TelegramNotifier(bot).send(555, "текст")


class _HangingBot:
    """Заглушка Bot, чей send_message не возвращается никогда.

    Запоминает и то, что вызов отменили: висящий запрос обязан сниматься, иначе
    к концу суток от тика остались бы тысячи брошенных задач.
    """

    def __init__(self) -> None:
        self.started = 0
        self.cancelled = 0

    async def send_message(self, chat_id: int, text: str) -> None:
        self.started += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


@pytest.mark.asyncio
async def test_hanging_send_becomes_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Зависший вызов Telegram обрывается таймаутом и приходит в тик как TransportError.

    Без своего таймаута тик встал бы на одной строке: max_instances=1 не пустит
    следующий, и очередь не двигалась бы, пока Telegram не ответит. Сбой по
    таймауту временный — строка возвращается в pending и достаётся следующему
    тику (критерий приёмки 11).
    """
    monkeypatch.setattr("kinday.telegram.notifier.SEND_TIMEOUT_SECONDS", 0.01)
    bot = _HangingBot()

    with pytest.raises(TransportError):
        await TelegramNotifier(bot).send(555, "текст")

    assert bot.started == 1
    # asyncio.wait_for снимает вложенный вызов сам; без этого зависший запрос
    # продолжал бы жить и после того, как тик о нём забыл.
    assert bot.cancelled == 1


@pytest.mark.asyncio
async def test_hanging_send_does_not_hang_the_tick(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тик с зависшим транспортом заканчивается, а строка ждёт следующей попытки."""
    monkeypatch.setattr("kinday.telegram.notifier.SEND_TIMEOUT_SECONDS", 0.01)
    clock = FixedClock(datetime(2027, 2, 15, tzinfo=UTC))
    family = await create_family_for(
        world, clock=clock, telegram_user_id=100, name="Антон", birth_date=date(1990, 6, 15)
    )
    [anton] = await world.person.list_by_family(family.id)
    [membership] = await world.membership.list_by_family(family.id)
    account = await world.account.get(membership.account_id)
    account.chat_id = 500
    await world.account.save(account)
    await add_person_to(
        world,
        clock=clock,
        acting_account_id=account.id,
        family_id=family.id,
        name="Пётр",
        birth_date=date(1980, 3, 1),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=anton.id,
    )
    due = min(r.due_at_utc for r in await world.read_reminders())
    clock.move_to(due)

    await asyncio.wait_for(
        run_tick(
            clock,
            TelegramNotifier(_HangingBot()),
            world.account,
            world.event,
            world.membership,
            world.person,
            world.relation,
            world.reminder,
            world.uow,
        ),
        timeout=5,
    )

    [row] = [r for r in await world.read_reminders() if r.due_at_utc == due]
    assert (row.status, row.attempts) == (ReminderStatus.PENDING, 1)
