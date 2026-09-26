"""Фейковый транспорт aiogram: роутеры проверяются без сети (PLAN.md, этап 6).

Настоящий `Bot` собирается поверх подменённой сессии, поэтому апдейты проходят
через настоящий `Dispatcher` — с фильтрами, машиной состояний и внедрением
зависимостей, — а исходящие вызовы Telegram оседают списком в `FakeSession`.
Так тест видит ровно то, что увидел бы пользователь, и ни один байт не уходит
в сеть.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import GetMe, SendMessage, TelegramMethod
from aiogram.types import Chat, Message, Update, User
from tests.core.fakes import FixedClock
from tests.scenario_repos import World

from kinday.telegram.bot import build_dispatcher
from kinday.telegram.routers import Deps

# Формат токена проверяет сам aiogram, так что подделка всё равно должна на него
# походить: цифры, двоеточие, буквенный хвост.
TEST_TOKEN = "123456:AAHtesttokenwithoutnetwork"
BOT_USERNAME = "kinday_test_bot"


class FakeSession(BaseSession):
    """Сессия, которая ничего не отправляет, а записывает вызовы Telegram API."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[object]] = []

    async def close(self) -> None: ...

    async def make_request(
        self, bot: Bot, method: TelegramMethod[object], timeout: int | None = None
    ) -> object:
        self.calls.append(method)
        # Имя бота нужно по-настоящему: из него собирается ссылка-приглашение
        # `t.me/<bot>?start=<код>` (SPEC 3.2).
        if isinstance(method, GetMe):
            return User(id=1, is_bot=True, first_name="kinday", username=BOT_USERNAME)
        # Остальные ответы нужны только чтобы aiogram распарсил результат:
        # handler'ы содержимым ответа Telegram не пользуются.
        return Message(
            message_id=len(self.calls),
            date=datetime(2027, 1, 1, tzinfo=UTC),
            chat=Chat(id=int(getattr(method, "chat_id", 0) or 0), type="private"),
        ).as_(bot)

    async def stream_content(self, *args: object, **kwargs: object) -> AsyncGenerator[bytes, None]:
        yield b""


class FakeTelegram:
    """Диалог с ботом: отправить текст от имени пользователя, прочитать ответы."""

    def __init__(self, bot: Bot, dispatcher: Dispatcher, session: FakeSession) -> None:
        self.bot = bot
        self.dispatcher = dispatcher
        self.session = session
        self.last_update_id = 0
        self.sent: list[tuple[int, str]] = []

    async def send(
        self,
        text: str | None,
        *,
        user_id: int,
        chat_id: int | None = None,
        chat_type: str = "private",
    ) -> list[str]:
        """Прогоняет одно сообщение и возвращает тексты ответов на него.

        `text=None` — сообщение без текста (стикер, фото): такие шаги диалога не
        принимают, и тест проверяет, что бот всё равно отвечает. `chat_type` нужен
        групповому чату: бот работает только в личных сообщениях (SPEC 7).
        """
        self.last_update_id += 1
        chat = chat_id if chat_id is not None else user_id
        update = Update(
            update_id=self.last_update_id,
            message=Message(
                message_id=self.last_update_id,
                date=datetime(2027, 1, 1, tzinfo=UTC),
                chat=Chat(id=chat, type=chat_type),
                from_user=User(id=user_id, is_bot=False, first_name=f"user{user_id}"),
                text=text,
            ),
        )
        before = len(self.session.calls)
        await self.dispatcher.feed_update(self.bot, update)
        answers = [
            (int(call.chat_id if isinstance(call.chat_id, int) else 0), call.text)
            for call in self.session.calls[before:]
            if isinstance(call, SendMessage)
        ]
        self.sent.extend(answers)
        return [text for _, text in answers]


def deps_from_world(world: World, clock: FixedClock) -> Deps:
    """Те же порты, что в сценарных тестах, разложенные по полям `Deps`."""
    return Deps(
        clock=clock,
        uow=world.uow,
        account=world.account,
        event=world.event,
        family=world.family,
        invite=world.invite,
        membership=world.membership,
        override=world.override,
        person=world.person,
        relation=world.relation,
        reminder=world.reminder,
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2027, 1, 1, tzinfo=UTC))


@pytest.fixture
def deps(sqlite_world: World, clock: FixedClock) -> Deps:
    return deps_from_world(sqlite_world, clock)


@pytest.fixture
def fake_session() -> Iterator[FakeSession]:
    yield FakeSession()


@pytest_asyncio.fixture
async def telegram(deps: Deps, fake_session: FakeSession) -> AsyncIterator[FakeTelegram]:
    bot = Bot(token=TEST_TOKEN, session=fake_session)
    try:
        yield FakeTelegram(bot, build_dispatcher(deps), fake_session)
    finally:
        await bot.session.close()
