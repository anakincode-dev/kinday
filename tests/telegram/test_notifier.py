"""TelegramNotifier переводит ошибки aiogram в исключения ядра (SPEC 5.2).

Ядро не знает кодов Telegram: 403 приходит к нему как RecipientBlocked, сетевой
сбой — как TransportError. Проверяется именно перевод, потому что на нём стоят
две ветки таблицы обработки ошибок из SPEC 5.5 (критерии приёмки 10 и 11).
"""

from __future__ import annotations

import pytest
from aiogram.exceptions import (
    RestartingTelegram,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import SendMessage

from kinday.core.ports import RecipientBlocked, TransportError
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
