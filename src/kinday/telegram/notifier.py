"""Реализация core.ports.Notifier поверх aiogram Bot."""

from __future__ import annotations

from typing import Any

from kinday.core.ports import RecipientBlocked, TransportError

__all__ = ["RecipientBlocked", "TelegramNotifier", "TransportError"]


class TelegramNotifier:
    """Переводит ошибки aiogram: 403 в RecipientBlocked, сетевые сбои в TransportError."""

    def __init__(self, bot: Any) -> None:
        self._bot = bot

    async def send(self, chat_id: int, text: str) -> None:
        raise NotImplementedError
