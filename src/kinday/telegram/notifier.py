"""Реализация core.ports.Notifier поверх aiogram Bot."""

from __future__ import annotations

import asyncio
from typing import Protocol

from aiogram.exceptions import (
    RestartingTelegram,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

from kinday.core.ports import RecipientBlocked, TransportError

__all__ = ["RecipientBlocked", "TelegramNotifier", "TransportError"]

# Сколько ждём ответа Telegram на одну отправку. Свой таймаут нужен, потому что
# тик идёт при max_instances=1: строка, на которой транспорт завис, остановила бы
# всю очередь до тех пор, пока сеть не разберётся сама. Пятнадцать секунд заметно
# меньше минутного интервала тика, так что зависший вызов не наползает на
# следующий запуск, и заметно больше обычного ответа Telegram, чтобы не рвать
# исправную отправку.
SEND_TIMEOUT_SECONDS = 15.0

# Сбои, после которых напоминание имеет смысл повторить следующим тиком (SPEC
# 5.5, критерий приёмки 11): сеть не дошла, Telegram отвечает пятисоткой или
# просит подождать. Сообщение при этом не доставлено, и повтор безопасен.
#
# TelegramRetryAfter (flood control) попадает сюда же: ждать указанные секунды
# внутри тика нельзя — max_instances=1 задержал бы всю очередь, — а следующий
# тик через минуту как раз и есть ожидание.
#
# TimeoutError — это то, чем asyncio.wait_for отвечает на исчерпанный
# SEND_TIMEOUT_SECONDS: ответа мы не дождались, но отправка могла и состояться.
# Повтор здесь выбран сознательно, как и для остальных сетевых сбоев: SPEC 5.5
# отводит на строку пять попыток, и молчаливый пропуск хуже редкого дубля.
_TRANSIENT_ERRORS = (
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    RestartingTelegram,
    TimeoutError,
)


class SupportsSendMessage(Protocol):
    """Что нужно от aiogram Bot: только отправка текста.

    Узкий протокол вместо самого `Bot`: notifier проверяется тестом без токена и
    без сессии, а подмена в тесте не обязана уметь остальные сто методов API.
    """

    async def send_message(self, chat_id: int, text: str) -> object: ...


class TelegramNotifier:
    """Переводит ошибки aiogram: 403 в RecipientBlocked, сетевые сбои и таймаут в TransportError.

    Ядро не знает кодов ошибок Telegram (SPEC 5.2), поэтому перевод живёт здесь:
    по нему тик выбирает между «закрыть строку и отключить доставку» и «вернуть
    в pending и повторить» (таблица SPEC 5.5).

    Каждая отправка идёт под своим таймаутом (SEND_TIMEOUT_SECONDS): у aiogram
    есть собственный таймаут сессии, но полагаться на чужую настройку в месте,
    где зависший вызов останавливает всю очередь тика, не стоит.

    Остальные ошибки API (например, TelegramBadRequest на несуществующий чат)
    наружу уходят как есть: повторять их бессмысленно, и тик закрывает такую
    строку как failed по своей общей ветке. Переводить их в TransportError
    значило бы израсходовать все пять попыток на заведомо безнадёжный вызов.
    """

    def __init__(self, bot: SupportsSendMessage) -> None:
        self._bot = bot

    async def send(self, chat_id: int, text: str) -> None:
        try:
            await asyncio.wait_for(
                self._bot.send_message(chat_id=chat_id, text=text), SEND_TIMEOUT_SECONDS
            )
        except TelegramForbiddenError as error:
            raise RecipientBlocked(f"Чат {chat_id} недоступен: {error}") from error
        except _TRANSIENT_ERRORS as error:
            raise TransportError(f"Не удалось отправить в чат {chat_id}: {error}") from error
