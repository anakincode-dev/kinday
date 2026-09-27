"""Настоящий старт `python -m kinday`: миграции, планировщик, длинный опрос.

PLAN.md, этап 6: к уже закрытому критерию 3 (нет BOT_TOKEN — понятная ошибка)
добавляется проверка самого старта. Сеть не нужна: длинный опрос либо
подменяется, либо идёт поверх фейковой сессии, а всё остальное проверяется по
базе и планировщику.
"""

from __future__ import annotations

import os
import signal
import sqlite3
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import GetMe, GetUpdates, TelegramMethod
from aiogram.types import User

from kinday import __main__ as entry_point
from kinday import app as composition_root
from kinday.config import ConfigError
from kinday.core.models import ReminderStatus
from kinday.scheduler.setup import TICK_INTERVAL_SECONDS
from kinday.storage.db import apply_migrations, connect

# Формат токена проверяет сам aiogram, так что подделка должна на него походить.
TEST_TOKEN = "123456:AAHtesttokenwithoutnetwork"

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


class PollingSession(BaseSession):
    """Сессия для настоящего длинного опроса без сети.

    `getUpdates` отвечает пустым списком, поэтому опрос крутится как обычно —
    и настоящий `start_polling` успевает поставить обработчики сигналов. На
    первом же опросе тест посылает процессу SIGTERM: так проверяется не наша
    подмена, а честный путь остановки от systemd.
    """

    # Сколько пустых опросов терпим. Сигнал разбирается на следующем витке цикла,
    # так что одного-двух хватает с запасом; предел нужен, чтобы сломанная
    # остановка обернулась падением теста, а не вечным зависанием.
    MAX_POLLS = 10

    def __init__(self) -> None:
        super().__init__()
        self.polls = 0
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def make_request(
        self, bot: Bot, method: TelegramMethod[object], timeout: int | None = None
    ) -> object:
        if isinstance(method, GetMe):
            return User(id=1, is_bot=True, first_name="kinday", username="kinday_test_bot")
        if isinstance(method, GetUpdates):
            self.polls += 1
            if self.polls == 1:
                os.kill(os.getpid(), signal.SIGTERM)
            if self.polls > self.MAX_POLLS:
                msg = "опрос не остановился после SIGTERM"
                raise AssertionError(msg)
            return []
        return True

    async def stream_content(self, *args: object, **kwargs: object) -> AsyncGenerator[bytes, None]:
        yield b""


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


def test_main_without_bot_token_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Критерий 3: сервис не стартует без BOT_TOKEN и говорит об этом.

    Проверяется не только текст ошибки, но и что до неё ничего не произошло:
    база не создана, цикл событий не запущен. Настройки читаются первой строкой
    именно за этим — сервис без токена не должен оставлять следов.
    """
    database_path = tmp_path / "nested" / "kinday.sqlite3"
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(database_path))

    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        entry_point.main()

    assert not database_path.exists()
    assert not database_path.parent.exists()


def test_main_applies_migrations_and_starts_polling(
    env: Path, no_polling: list[Dispatcher], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Старт создаёт базу со схемой, поднимает планировщик и уходит в опрос."""
    started_schedulers: list[Any] = []
    jobs_at_start: list[dict[str, Any]] = []
    original: Callable[..., Any] = composition_root.start_scheduler

    async def recording_start(scheduler: Any, clock: Any, reminder_repo: Any) -> None:
        await original(scheduler, clock, reminder_repo)
        started_schedulers.append(scheduler)
        # Расписание снимается пока планировщик жив: при остановке APScheduler
        # выбрасывает задания из хранилища в памяти, и после main() список пуст.
        jobs_at_start.append({job.id: job for job in scheduler.get_jobs()})

    monkeypatch.setattr(composition_root, "start_scheduler", recording_start)

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


def test_sigterm_stops_polling_and_closes_everything(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIGTERM от systemd останавливает опрос, планировщик и базу, а не рубит процесс.

    Проверяется именно упорядоченная остановка: опрос вышел из цикла, планировщик
    больше не запущен, сессия Telegram и соединение с базой закрыты. Ради этого
    опрос настоящий, с обработчиками сигналов aiogram, и сигнал настоящий —
    подмена `start_polling`, как в остальных тестах файла, обработчики сигналов не
    ставит и проверяла бы саму себя.

    Что процесс не погиб, видно из самого факта проверок после `main()`: при
    непойманном SIGTERM их выполнять было бы уже некому.
    """
    session = PollingSession()
    schedulers: list[Any] = []
    connections: list[sqlite3.Connection] = []
    original_build: Callable[..., Any] = composition_root.build_scheduler
    original_connect: Callable[..., sqlite3.Connection] = entry_point.connect

    def recording_build(*args: Any, **kwargs: Any) -> Any:
        scheduler = original_build(*args, **kwargs)
        schedulers.append(scheduler)
        return scheduler

    def recording_connect(path: str) -> sqlite3.Connection:
        connection = original_connect(path)
        connections.append(connection)
        return connection

    monkeypatch.setattr(composition_root, "build_scheduler", recording_build)
    monkeypatch.setattr(entry_point, "connect", recording_connect)
    monkeypatch.setattr(
        entry_point, "build_bot", lambda settings: Bot(token=TEST_TOKEN, session=session)
    )

    entry_point.main()

    assert session.polls >= 1
    assert session.closed
    assert not schedulers[0].running
    # Закрытое соединение sqlite3 на любой запрос отвечает ProgrammingError.
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute("SELECT 1")
