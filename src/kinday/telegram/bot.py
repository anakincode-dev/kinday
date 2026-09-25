"""Сборка aiogram Bot и Dispatcher, запуск длинного опроса."""

from __future__ import annotations

from typing import Any

from kinday.config import Settings


def build_bot(settings: Settings) -> Any:
    raise NotImplementedError


async def run_polling(*args: Any, **kwargs: Any) -> None:
    raise NotImplementedError
