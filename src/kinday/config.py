"""Чтение конфигурации из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Обязательная переменная окружения не задана или некорректна."""


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    database_path: str
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        bot_token = os.environ.get("BOT_TOKEN", "").strip()
        if not bot_token:
            raise ConfigError(
                "BOT_TOKEN не задан. Укажи его в переменных окружения "
                "(см. .env.example) перед запуском."
            )
        database_path = os.environ.get("DATABASE_PATH", "data/kinday.sqlite3")
        log_level = os.environ.get("LOG_LEVEL", "INFO")
        return cls(bot_token=bot_token, database_path=database_path, log_level=log_level)
