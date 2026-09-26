"""Соединение с SQLite, применение миграций и доступ к базе из цикла событий.

sqlite3 синхронный, бот и планировщик живут в цикле событий (SPEC 6.2): любой
вызов базы уходит в `asyncio.to_thread` через `Database.run`, а адаптеры
репозиториев (repos.py) других способов не знают.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_MIGRATION_NAME = re.compile(r"^(\d{4})_.+\.sql$")

T = TypeVar("T")


class MigrationError(RuntimeError):
    """Миграция не применилась. Сервис останавливается, а не работает на половинчатой схеме."""


def connect(database_path: str) -> sqlite3.Connection:
    """Открывает соединение и настраивает PRAGMA (SPEC 6.2).

    `check_same_thread=False` — обращения приходят из разных потоков
    `asyncio.to_thread`, а не из нескольких сразу: доступ упорядочивает
    `Database`. `isolation_level=None` отключает неявные транзакции драйвера:
    отдельный запрос фиксируется сразу (так работает claim из SPEC 5.5), а
    границу сценария задаёт явный BEGIN в `SqliteUnitOfWork`.
    """
    connection = sqlite3.connect(database_path, check_same_thread=False, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def apply_migrations(connection: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> None:
    """Применяет недостающие файлы `000N_*.sql` по возрастанию номера, каждый в своей транзакции.

    Номер последней применённой миграции лежит в `schema_version`; её создаёт
    первая же миграция, до этого версия считается нулевой. Откат назад не
    поддерживается (SPEC 6.1): при ошибке транзакция откатывается и поднимается
    MigrationError, чтобы сервис остановился, а не поднимал бота на
    половинчатой схеме.
    """
    current = _current_version(connection)
    for version, path in _migration_files(migrations_dir):
        if version <= current:
            continue
        _apply_one(connection, version, path)


def _migration_files(migrations_dir: Path) -> list[tuple[int, Path]]:
    """Миграции по возрастанию номера; посторонние файлы каталога пропускаются.

    Всё, что не `.sql` (README, .gitkeep, файлы редактора), миграцией не
    считается и молча игнорируется. А вот `.sql` с неправильным именем — ошибка:
    такой файл писали как миграцию, и пропустить его молча значит поднять бота
    на схеме, которой автор файла не ожидал.
    """
    files: list[tuple[int, Path]] = []
    for path in sorted(migrations_dir.iterdir()):
        if path.suffix != ".sql":
            continue
        match = _MIGRATION_NAME.match(path.name)
        if match is None:
            raise MigrationError(
                f"Файл {path.name} не похож на миграцию: ожидается формат 000N_имя.sql"
            )
        files.append((int(match.group(1)), path))
    return sorted(files)


def _current_version(connection: sqlite3.Connection) -> int:
    exists = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if exists is None:
        return 0
    row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return 0 if row[0] is None else int(row[0])


def _apply_one(connection: sqlite3.Connection, version: int, path: Path) -> None:
    """Прогоняет один файл вместе с записью номера версии одной транзакцией.

    BEGIN и COMMIT идут внутрь скрипта: `executescript` сам фиксирует
    незакрытую транзакцию перед началом работы, поэтому открытый снаружи BEGIN
    до операторов файла не дожил бы и каждый оператор применился бы отдельно.
    """
    body = path.read_text(encoding="utf-8").strip()
    if not body.endswith(";"):
        body += ";"
    script = (
        "BEGIN;\n"
        f"{body}\n"
        "DELETE FROM schema_version;\n"
        f"INSERT INTO schema_version (version) VALUES ({version});\n"
        "COMMIT;"
    )
    try:
        connection.executescript(script)
    except sqlite3.Error as error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise MigrationError(f"Миграция {path.name} не применилась: {error}") from error


class Database:
    """Соединение плюс порядок доступа к нему из цикла событий.

    Соединение одно, а обращаются к нему разные задачи, поэтому доступ
    упорядочен замком: одновременных запросов к sqlite3.Connection не бывает.
    Транзакция сценария (`SqliteUnitOfWork`) держит этот же замок от BEGIN до
    COMMIT — иначе чужая запись попала бы в чужую транзакцию и откатилась бы
    вместе с ней. Задача, которая владеет транзакцией, замок не перезанимает:
    её собственные запросы идут внутрь уже открытой транзакции.

    Отсюда ограничение для сценариев: внутри единицы работы запросы идут
    последовательно, из той же задачи. Вторая задача (например, ветка
    `asyncio.gather` внутри сценария) ждала бы замок, занятый её же
    родительской задачей, и ждала бы до COMMIT, которого не будет. Поэтому
    `asyncio.Lock` не реентерабелен намеренно: в чужую открытую транзакцию
    никто не пишет, а распараллеливать запросы сценария нечем и не нужно.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task[object] | None = None

    async def run(self, work: Callable[[sqlite3.Connection], T]) -> T:
        if self._owner is not None and self._owner is asyncio.current_task():
            return await asyncio.to_thread(work, self._connection)
        async with self._lock:
            return await asyncio.to_thread(work, self._connection)

    async def acquire(self) -> None:
        """Занимает базу под транзакцию текущей задачи."""
        await self._lock.acquire()
        self._owner = asyncio.current_task()

    def release(self) -> None:
        self._owner = None
        self._lock.release()

    def rollback_if_open(self) -> None:
        """Аварийно закрывает транзакцию, если её COMMIT или ROLLBACK не дошёл.

        Вызывается синхронно из `SqliteUnitOfWork.__aexit__`, пока замок ещё
        занят: `await` там может быть снят отменой задачи (CancelledError,
        таймаут), и тогда транзакция осталась бы открытой, а следующий
        BEGIN IMMEDIATE упал бы на «cannot start a transaction within a
        transaction» — соединение одно на процесс (SPEC 6.2), заменить его
        нечем. Вызов без транзакции ничего не делает.
        """
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")

    def close(self) -> None:
        self._connection.close()
