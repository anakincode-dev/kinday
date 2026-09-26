"""Общие фикстуры: те же сценарии на фейках и на настоящем SQLite (PLAN.md, этап 4)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from tests.scenario_repos import World, build_memory_world, build_sqlite_world

from kinday.storage.db import Database, apply_migrations, connect


@pytest_asyncio.fixture
async def database(tmp_path: object) -> AsyncIterator[Database]:
    """Пустая база в отдельном файле: миграции применяются как при старте сервиса."""
    path = f"{tmp_path}/kinday.sqlite3"
    connection = connect(path)
    try:
        apply_migrations(connection)
        yield Database(connection)
    finally:
        connection.close()


@pytest_asyncio.fixture
async def sqlite_world(database: Database) -> World:
    return build_sqlite_world(database)


@pytest_asyncio.fixture(params=["memory", "sqlite"])
async def world(request: pytest.FixtureRequest, database: Database) -> World:
    """Один и тот же сценарный тест на двух бэкендах.

    Файл базы создаётся и для варианта "memory" — фикстура `database` дешёвая,
    а параметризация так остаётся плоской.
    """
    if request.param == "memory":
        return build_memory_world()
    return build_sqlite_world(database)
