"""Настоящий старт `python -m kinday`: миграции, планировщик, длинный опрос.

PLAN.md, этап 6: к уже закрытому критерию 3 (нет BOT_TOKEN — понятная ошибка)
добавляется проверка самого старта. Сеть не нужна: длинный опрос подменяется,
а всё, что делает точка входа до него, проверяется по базе и планировщику.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from aiogram import Dispatcher

from kinday import __main__ as entry_point
from kinday.config import ConfigError
from kinday.core.models import ReminderStatus
from kinday.scheduler.setup import TICK_INTERVAL_SECONDS
from kinday.storage.db import apply_migrations, connect

# Строка, застрявшая в sending с прошлого запуска, плюс минимум ссылочной
# целостности вокруг неё: аккаунт, семья, человек и событие.
SEED_STUCK_SENDING = """
INSERT INTO accounts (id, telegram_user_id, timezone, offsets_days, time_of_day)
     VALUES (1, 100, 'Europe/Moscow', '7,1,0', '09:00:00');
INSERT INTO families (id, name, owner_account_id) VALUES (1, 'Семья Антон', 1);
INSERT INTO people (id, family_id, name, gender, birth_date)
     VALUES (1, 1, 'Антон', 'male', '1990-06-15');
INSERT INTO events (id, family_id, person_id, title, date, is_recurring_yearly, kind)
     VALUES (1, 1, 1, 'День рождения', '1990-06-15', 1, 'birthday');
INSERT INTO reminders (event_id, person_id, offset_days, occurrence_date, due_at_utc, status)
     VALUES (1, 1, 7, '2027-06-08', '2027-06-01T06:00:00+00:00', 'sending');
"""


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    database_path = tmp_path / "nested" / "kinday.sqlite3"
    monkeypatch.setenv("BOT_TOKEN", "123456:AAHtesttokenwithoutnetwork")
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    return database_path


@pytest.fixture
def no_polling(monkeypatch: pytest.MonkeyPatch) -> list[Dispatcher]:
    """Вместо сети — запись факта запуска опроса: дальше точка входа не идёт."""
    started: list[Dispatcher] = []

    async def fake_start_polling(self: Dispatcher, *args: Any, **kwargs: Any) -> None:
        started.append(self)

    monkeypatch.setattr(Dispatcher, "start_polling", fake_start_polling)
    return started


def test_main_without_bot_token_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Критерий 3: сервис не стартует без BOT_TOKEN и говорит об этом."""
    monkeypatch.delenv("BOT_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        entry_point.main()


def test_main_applies_migrations_and_starts_polling(
    env: Path, no_polling: list[Dispatcher], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Старт создаёт базу со схемой, поднимает планировщик и уходит в опрос."""
    started_schedulers: list[Any] = []
    jobs_at_start: list[dict[str, Any]] = []
    original: Callable[..., Any] = entry_point.start_scheduler

    async def recording_start(scheduler: Any, clock: Any, reminder_repo: Any) -> None:
        await original(scheduler, clock, reminder_repo)
        started_schedulers.append(scheduler)
        # Расписание снимается пока планировщик жив: при остановке APScheduler
        # выбрасывает задания из хранилища в памяти, и после main() список пуст.
        jobs_at_start.append({job.id: job for job in scheduler.get_jobs()})

    monkeypatch.setattr(entry_point, "start_scheduler", recording_start)

    entry_point.main()

    assert len(no_polling) == 1
    assert len(started_schedulers) == 1
    jobs = jobs_at_start[0]
    assert set(jobs) == {"tick", "daily_materialization"}
    assert jobs["tick"].trigger.interval.total_seconds() == TICK_INTERVAL_SECONDS
    assert not started_schedulers[0].running

    connection = connect(str(env))
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        connection.close()
    assert {"families", "people", "accounts", "reminders", "schema_version"} <= tables


def test_main_closes_stuck_sending_before_polling(env: Path, no_polling: list[Dispatcher]) -> None:
    """Критерий 9: строка, застрявшая в sending, закрывается при старте и не уходит повторно."""
    env.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(str(env))
    try:
        apply_migrations(connection)
        connection.executescript(SEED_STUCK_SENDING)
    finally:
        connection.close()

    entry_point.main()

    connection = connect(str(env))
    try:
        statuses = [row[0] for row in connection.execute("SELECT status FROM reminders").fetchall()]
    finally:
        connection.close()
    assert statuses == [ReminderStatus.FAILED.value]
