"""Миграции схемы: порядок применения и отсутствие повторов (критерий приёмки 28)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kinday.storage.db import MIGRATIONS_DIR, MigrationError, apply_migrations, connect

SPEC_TABLES = {
    "families",
    "people",
    "accounts",
    "memberships",
    "relations",
    "events",
    "reminder_overrides",
    "reminders",
    "invites",
    "schema_version",
}


def _fresh(tmp_path: Path) -> sqlite3.Connection:
    return connect(str(tmp_path / "kinday.sqlite3"))


def _tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def _version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0])


def _write_migrations(directory: Path, files: dict[str, str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, sql in files.items():
        (directory / name).write_text(sql, encoding="utf-8")
    return directory


ORDERED = {
    "0001_init.sql": """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        CREATE TABLE applied (step INTEGER NOT NULL);
        INSERT INTO applied (step) VALUES (1);
    """,
    "0002_second.sql": "INSERT INTO applied (step) VALUES (2);",
    "0003_third.sql": "INSERT INTO applied (step) VALUES (3);",
}


def test_real_migrations_create_full_schema(tmp_path: Path) -> None:
    """Сервис на пустой базе поднимает схему из SPEC 5.3 сам."""
    connection = _fresh(tmp_path)
    try:
        apply_migrations(connection)

        assert _tables(connection) >= SPEC_TABLES
        assert _version(connection) > 0
        indexes = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'reminders'"
        ).fetchall()
        assert any(row["name"] == "reminders_unique" for row in indexes)
    finally:
        connection.close()


def test_migrations_apply_in_order(tmp_path: Path) -> None:
    """Критерий 28: миграции применяются по возрастанию номера."""
    directory = _write_migrations(tmp_path / "migrations", ORDERED)
    connection = _fresh(tmp_path)
    try:
        apply_migrations(connection, directory)

        steps = [row["step"] for row in connection.execute("SELECT step FROM applied").fetchall()]
        assert steps == [1, 2, 3]
        assert _version(connection) == 3
    finally:
        connection.close()


def test_second_start_does_not_reapply(tmp_path: Path) -> None:
    """Критерий 28: повторный старт миграции заново не применяет."""
    directory = _write_migrations(tmp_path / "migrations", ORDERED)
    connection = _fresh(tmp_path)
    try:
        apply_migrations(connection, directory)
        apply_migrations(connection, directory)

        steps = [row["step"] for row in connection.execute("SELECT step FROM applied").fetchall()]
        assert steps == [1, 2, 3]
        assert _version(connection) == 3
    finally:
        connection.close()


def test_new_migration_applies_on_top_of_existing_base(tmp_path: Path) -> None:
    """Дописанная миграция применяется одна, уже применённые не повторяются."""
    directory = _write_migrations(tmp_path / "migrations", ORDERED)
    connection = _fresh(tmp_path)
    try:
        apply_migrations(connection, directory)
        _write_migrations(directory, {"0004_fourth.sql": "INSERT INTO applied (step) VALUES (4);"})

        apply_migrations(connection, directory)

        steps = [row["step"] for row in connection.execute("SELECT step FROM applied").fetchall()]
        assert steps == [1, 2, 3, 4]
        assert _version(connection) == 4
    finally:
        connection.close()


def test_failing_migration_rolls_back_and_stops(tmp_path: Path) -> None:
    """SPEC 6.1: при ошибке сервис останавливается, половинчатой схемы не остаётся."""
    directory = _write_migrations(
        tmp_path / "migrations",
        {
            "0001_init.sql": "CREATE TABLE schema_version (version INTEGER NOT NULL);",
            "0002_broken.sql": """
                CREATE TABLE half_done (id INTEGER PRIMARY KEY);
                СЛОМАННЫЙ SQL;
            """,
        },
    )
    connection = _fresh(tmp_path)
    try:
        with pytest.raises(MigrationError, match=r"0002_broken\.sql"):
            apply_migrations(connection, directory)

        assert "half_done" not in _tables(connection)
        assert _version(connection) == 1
    finally:
        connection.close()


def test_stray_file_does_not_stop_start_but_misnamed_sql_does(tmp_path: Path) -> None:
    """Посторонний файл рядом с миграциями запуск не ломает, а `.sql` с чужим именем — ломает."""
    directory = _write_migrations(tmp_path / "migrations", ORDERED)
    (directory / "README.md").write_text("заметка для людей", encoding="utf-8")
    connection = _fresh(tmp_path)
    try:
        apply_migrations(connection, directory)
        assert _version(connection) == 3

        (directory / "fix_by_hand.sql").write_text(
            "INSERT INTO applied (step) VALUES (9);", "utf-8"
        )
        with pytest.raises(MigrationError, match=r"fix_by_hand\.sql"):
            apply_migrations(connection, directory)

        steps = [row["step"] for row in connection.execute("SELECT step FROM applied").fetchall()]
        assert steps == [1, 2, 3]
    finally:
        connection.close()


def test_migration_files_are_numbered() -> None:
    """Имена настоящих миграций подчиняются формату 000N_*.sql (SPEC 6.1)."""
    names = sorted(path.name for path in MIGRATIONS_DIR.glob("*.sql"))
    assert names
    for index, name in enumerate(names, start=1):
        assert name.startswith(f"{index:04d}_")
