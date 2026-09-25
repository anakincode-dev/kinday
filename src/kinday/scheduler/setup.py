"""Сборка APScheduler AsyncIOScheduler с двумя заданиями: тик и суточная материализация.

Хранилище заданий — в памяти: источник истины по напоминаниям только таблица
reminders, расписание известно на старте и создаётся заново при каждом запуске.
"""

from __future__ import annotations

from typing import Any


def build_scheduler(*args: Any, **kwargs: Any) -> Any:
    """Настраивает тик (max_instances=1) и суточную материализацию (coalesce=True)."""
    raise NotImplementedError
