"""Соединение с SQLite и применение миграций.

sqlite3 синхронный, бот и планировщик живут в цикле событий: вызовы должны
заворачиваться в asyncio.to_thread на уровне адаптеров репозиториев (repos.py),
а не здесь.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(database_path: str) -> sqlite3.Connection:
    """Открывает соединение и настраивает PRAGMA (WAL, synchronous, foreign_keys, busy_timeout)."""
    raise NotImplementedError


def apply_migrations(connection: sqlite3.Connection) -> None:
    """Применяет недостающие файлы из migrations/ по возрастанию номера.

    Каждая миграция — в своей транзакции.
    """
    raise NotImplementedError
