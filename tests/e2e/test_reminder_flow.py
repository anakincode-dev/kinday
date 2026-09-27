"""Сквозной сценарий из SPEC 9: от диалога с ботом до отправленного напоминания.

Отличие от тестов этапов 1–6: там слои проверялись по отдельности — ядро и тик
на портах (половина прогонов на фейковых репозиториях), диалоги на своём наборе
портов. Здесь поднимается целое приложение той же функцией, что и в бою
(`kinday.app.build_application` плюс `start_application`), на настоящем файле
SQLite с миграциями, данные заводятся только диалогами бота, а напоминание
отправляет тот же `tick`, который в бою вызывает APScheduler.

Подменены ровно две вещи: часы (`FixedClock`) и сессия `Bot` (`FakeSession`,
которая записывает вызовы Telegram API вместо сети). Всё между ними настоящее —
`Dispatcher` с фильтрами и машиной состояний, `TelegramNotifier`, репозитории
SQLite, `run_tick`. Прямых вызовов `core.services` в тесте нет: любое состояние
появляется так же, как появилось бы у живого пользователя.

Время двигается переводом часов, а не `sleep`, поэтому оба сценария
укладываются в доли секунды.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from tests.core.fakes import FixedClock
from tests.telegram.conftest import TEST_TOKEN, FakeSession, FakeTelegram

from kinday.app import Application, build_application, start_application, stop_application
from kinday.core.models import ReminderStatus
from kinday.storage.db import Database, apply_migrations, connect

# SPEC 9, шаг 1: часы подменены на эту точку. Дата выбрана так, чтобы день
# рождения Антона (15 июня) не попадал в окно теста и не давал второго
# сообщения.
START = datetime(2027, 2, 15, tzinfo=UTC)
# SPEC 9, шаг 4: 09:00 в Москве, ровно за 7 дней до 1 марта.
DUE_SEVEN_DAYS = datetime(2027, 2, 22, 6, tzinfo=UTC)
# Следующее по счёту напоминание о том же дне рождения — «за 1 день».
DUE_ONE_DAY = datetime(2027, 2, 28, 6, tzinfo=UTC)
# Последнее из трёх — в сам день рождения.
DUE_ON_THE_DAY = datetime(2027, 3, 1, 6, tzinfo=UTC)
OCCURRENCE = date(2027, 3, 1)

# FakeTelegram шлёт сообщения из чата с номером пользователя, так что чат
# получателя совпадает с его telegram-id.
ANTON = 100

# SPEC 3.3, формат дня рождения. Дословно, включая тире и точку в конце.
SEVEN_DAYS_TEXT = "Через 7 дней день рождения — Пётр, ваш отец. 1 марта, исполнится 47."
ONE_DAY_TEXT = "Завтра день рождения — Пётр, ваш отец. 1 марта, исполнится 47."


@dataclass
class Harness:
    """Поднятое приложение вместе с тем, чем его дёргает тест."""

    app: Application
    clock: FixedClock
    session: FakeSession
    telegram: FakeTelegram
    database: Database

    def mark(self) -> int:
        """Метка в журнале вызовов Telegram: с неё читаются сообщения следующего шага."""
        return len(self.session.calls)

    def messages_since(self, mark: int) -> list[tuple[int, str]]:
        """Сообщения, ушедшие в Telegram после метки, как пары «чат, текст».

        Ответы диалогов и напоминания идут одним потоком через одну сессию —
        именно так они идут и в бою, — поэтому шаги теста разделяются меткой, а
        не фильтром по содержимому.
        """
        return [
            (int(call.chat_id if isinstance(call.chat_id, int) else 0), call.text or "")
            for call in self.session.calls[mark:]
            if isinstance(call, SendMessage)
        ]

    async def tick(self) -> None:
        """Тот самый тик, который в бою вызывает APScheduler (`Application.tick`)."""
        await self.app.tick()

    async def statuses_for(self, occurrence: date) -> dict[int, ReminderStatus]:
        """Статус каждого смещения по одной дате события — тесту нужен неразрушающий список.

        Через порты его не получить: `claim_due` меняет статус, а не читает.
        Ответ собирается словарём «смещение → статус», чтобы проверки не зависели
        от того, в каком порядке строки легли в таблицу.
        """
        rows = await self.database.run(
            lambda c: c.execute(
                "SELECT offset_days, status FROM reminders WHERE occurrence_date = ?",
                (occurrence.isoformat(),),
            ).fetchall()
        )
        return {int(row["offset_days"]): ReminderStatus(row["status"]) for row in rows}

    async def due_moments(self, occurrence: date) -> dict[int, datetime]:
        """Момент отправки каждого смещения по одной дате события.

        Тик проверяет срок с суточным запасом (`MISFIRE_GRACE`), поэтому «письмо
        пришло» ничего не говорит о том, на какое время оно было назначено:
        сместись пояс с Москвы на Екатеринбург или время суток с 09:00 на 07:30 —
        сообщение всё равно ушло бы тем же тиком. Пояс и время суток закрепляются
        только сравнением самих сроков.
        """
        rows = await self.database.run(
            lambda c: c.execute(
                "SELECT offset_days, due_at_utc FROM reminders WHERE occurrence_date = ?",
                (occurrence.isoformat(),),
            ).fetchall()
        )
        return {int(row["offset_days"]): datetime.fromisoformat(row["due_at_utc"]) for row in rows}

    async def pending_count(self) -> int:
        """Сколько строк всего ждёт отправки — по всем событиям и всем датам."""
        rows = await self.database.run(
            lambda c: c.execute(
                "SELECT count(*) AS n FROM reminders WHERE status = ?",
                (ReminderStatus.PENDING.value,),
            ).fetchone()
        )
        return int(rows["n"])


@pytest_asyncio.fixture
async def harness(tmp_path: object) -> AsyncIterator[Harness]:
    """Поднимает приложение ровно так, как это делает точка входа.

    `connect` и `apply_migrations` — те же, что в `__main__`; дальше сборку
    целиком делает `build_application`, а жизненный цикл — `start_application` и
    `stop_application`, то есть с закрытием застрявших в sending строк до
    запуска заданий (SPEC 5.5) и с тем же порядком остановки, что в бою.
    """
    connection = connect(f"{tmp_path}/kinday.sqlite3")
    apply_migrations(connection)
    database = Database(connection)

    clock = FixedClock(START)
    session = FakeSession()
    bot = Bot(token=TEST_TOKEN, session=session)
    app = build_application(database, bot, clock)
    await start_application(app)
    try:
        yield Harness(
            app=app,
            clock=clock,
            session=session,
            telegram=FakeTelegram(bot, app.dispatcher, session),
            database=database,
        )
    finally:
        await stop_application(app)


async def _set_up_family(harness: Harness) -> None:
    """Шаги 2 и 3 SPEC 9 — только диалогами бота, без вызовов сценариев ядра.

    `/start` у незнакомого пользователя предлагает завести семью; `/new_family`
    спрашивает имя, пол, дату рождения и город (пояс Europe/Moscow приезжает из
    «Москва») и привязывает чат; `/add_person` спрашивает, относительно кого
    добавляется человек, кем он приходится, и его данные. Смещения за 7 дней,
    за 1 день и в день события и время 09:00 — умолчания нового аккаунта.
    """
    telegram = harness.telegram
    await telegram.send("/start", user_id=ANTON)

    await telegram.send("/new_family", user_id=ANTON)
    await telegram.send("Антон", user_id=ANTON)
    await telegram.send("мужской", user_id=ANTON)
    await telegram.send("15.06.1990", user_id=ANTON)
    created = await telegram.send("Москва", user_id=ANTON)
    assert "создана" in created[-1].lower(), created

    # Первый и единственный человек в списке — сам Антон, Пётр добавляется как
    # его отец. Напоминания строятся сразу, без суточного задания.
    await telegram.send("/add_person", user_id=ANTON)
    await telegram.send("1", user_id=ANTON)
    await telegram.send("отец", user_id=ANTON)
    await telegram.send("Пётр", user_id=ANTON)
    await telegram.send("мужской", user_id=ANTON)
    added = await telegram.send("01.03.1980", user_id=ANTON)
    assert "пётр" in added[-1].lower(), added

    # Все три срока по ближайшему дню рождения Петра уже в базе и все ждут
    # отправки: без этого «ровно одно сообщение» ниже могло бы означать, что
    # остальные напоминания просто не построились.
    assert await harness.statuses_for(OCCURRENCE) == {
        7: ReminderStatus.PENDING,
        1: ReminderStatus.PENDING,
        0: ReminderStatus.PENDING,
    }
    # И назначены они на 09:00 по Москве — то есть город из диалога доехал до
    # пояса аккаунта, а умолчания по смещениям и времени суток применились.
    assert await harness.due_moments(OCCURRENCE) == {
        7: DUE_SEVEN_DAYS,
        1: DUE_ONE_DAY,
        0: DUE_ON_THE_DAY,
    }


@pytest.mark.asyncio
async def test_reminder_reaches_the_owner_chat(harness: Harness) -> None:
    """SPEC 9 целиком: диалоги, перевод часов, один тик — ровно одно сообщение.

    Повторный тик в ту же минуту второго сообщения не даёт (критерий приёмки 8),
    а строка в базе остаётся `sent`.
    """
    await _set_up_family(harness)

    # Шаги 4 и 5: часы на 09:00 по Москве и один тик планировщика.
    harness.clock.move_to(DUE_SEVEN_DAYS)
    mark = harness.mark()
    await harness.tick()

    assert harness.messages_since(mark) == [(ANTON, SEVEN_DAYS_TEXT)]

    # Повторный тик в ту же минуту: сообщение по-прежнему одно.
    await harness.tick()
    assert harness.messages_since(mark) == [(ANTON, SEVEN_DAYS_TEXT)]

    assert await harness.statuses_for(OCCURRENCE) == {
        7: ReminderStatus.SENT,
        1: ReminderStatus.PENDING,
        0: ReminderStatus.PENDING,
    }


@pytest.mark.asyncio
async def test_delivery_resumes_after_block_and_restart(harness: Harness) -> None:
    """403 на отправке гасит доставку, а `/start` возвращает её к следующему напоминанию.

    Отказ приходит от подменённой сессии, то есть оттуда же, откуда пришёл бы
    настоящий: его переводит настоящий `TelegramNotifier`, а решение принимает
    настоящий тик (SPEC 5.5, критерий приёмки 10).
    """
    await _set_up_family(harness)

    harness.clock.move_to(DUE_SEVEN_DAYS)
    harness.session.send_error = TelegramForbiddenError
    mark = harness.mark()
    await harness.tick()

    # Отправить пытались один раз, дошло ничего: Telegram ответил отказом.
    assert harness.messages_since(mark) == [(ANTON, SEVEN_DAYS_TEXT)]
    blocked = await harness.app.deps.account.get_by_telegram_user_id(ANTON)
    assert blocked is not None
    assert blocked.delivery_enabled is False
    # Ни одной строки в ожидании не осталось: каждая следующая получила бы тот же
    # отказ, поэтому доставка закрыта целиком.
    assert await harness.pending_count() == 0

    # Пользователь разблокировал бота и нажал /start. Дальше сеть снова работает.
    harness.session.send_error = None
    restarted = await harness.telegram.send("/start", user_id=ANTON)
    assert "напоминания" in restarted[-1].lower(), restarted

    restored = await harness.app.deps.account.get_by_telegram_user_id(ANTON)
    assert restored is not None
    assert (restored.chat_id, restored.delivery_enabled) == (ANTON, True)
    # Срок, на котором пришёл 403, остаётся закрытым: сообщение могло и дойти.
    # Остальные ждут отправки снова.
    assert await harness.statuses_for(OCCURRENCE) == {
        7: ReminderStatus.FAILED,
        1: ReminderStatus.PENDING,
        0: ReminderStatus.PENDING,
    }

    # Следующее напоминание о том же дне рождения доставляется как обычно.
    harness.clock.move_to(DUE_ONE_DAY)
    mark = harness.mark()
    await harness.tick()

    assert harness.messages_since(mark) == [(ANTON, ONE_DAY_TEXT)]
