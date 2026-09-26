"""Сборка aiogram Bot и Dispatcher, запуск длинного опроса.

Здесь связываются слои: репозитории SQLite подставляются в порты ядра, готовый
набор портов кладётся в данные Dispatcher, и handler'ы получают его по имени
параметра `deps`. Ни ядро, ни роутеры не знают, откуда берутся зависимости.
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from kinday.config import Settings
from kinday.core.ports import Clock
from kinday.storage.db import Database
from kinday.storage.repos import (
    SqliteAccountRepo,
    SqliteEventRepo,
    SqliteFamilyRepo,
    SqliteInviteRepo,
    SqliteMembershipRepo,
    SqlitePersonRepo,
    SqliteRelationRepo,
    SqliteReminderOverrideRepo,
    SqliteReminderRepo,
    SqliteUnitOfWork,
)
from kinday.telegram.routers import Deps, build_router


def build_deps(database: Database, clock: Clock) -> Deps:
    """Собирает порты ядра на репозиториях SQLite поверх одного соединения (SPEC 6.2)."""
    return Deps(
        clock=clock,
        uow=SqliteUnitOfWork(database),
        account=SqliteAccountRepo(database),
        event=SqliteEventRepo(database),
        family=SqliteFamilyRepo(database),
        invite=SqliteInviteRepo(database),
        membership=SqliteMembershipRepo(database),
        override=SqliteReminderOverrideRepo(database),
        person=SqlitePersonRepo(database),
        relation=SqliteRelationRepo(database),
        reminder=SqliteReminderRepo(database),
    )


def build_dispatcher(deps: Deps) -> Dispatcher:
    """Dispatcher с хранилищем состояний в памяти и роутером диалогов.

    Состояния диалогов держатся в памяти намеренно: они живут минуты, а всё, что
    диалог решил, уже лежит в базе. Перезапуск сервиса прерывает незаконченный
    диалог — пользователь начинает команду заново, ничего не теряя.

    `deps` уходит в данные Dispatcher: aiogram передаёт их в handler'ы по имени
    параметра, поэтому роутеры остаются обычными функциями без глобального
    состояния.
    """
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["deps"] = deps
    dispatcher.include_router(build_router())
    return dispatcher


def build_bot(settings: Settings) -> Bot:
    """Bot с токеном из окружения (SPEC 6.1): в репозиторий секрет не попадает."""
    return Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=None))


async def run_polling(bot: Bot, dispatcher: Dispatcher) -> None:
    """Длинный опрос: вебхук не нужен, сервису не требуется публичный адрес (SPEC 6.2).

    Обработчики сигналов оставлены за aiogram (`handle_signals` по умолчанию):
    иначе SIGTERM от systemd убивал бы процесс мгновенно, минуя остановку
    планировщика и закрытие базы, а тик, прерванный между send и mark, оставлял
    бы строку в `sending` — то есть возможно потерянное сообщение (SPEC 5.5).
    """
    await dispatcher.start_polling(bot)
