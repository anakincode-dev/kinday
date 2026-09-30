"""Отказы и сбои глазами пользователя: общий текст, чистый журнал, ни одного id.

Две вещи, которые внешнее ревью этапа 6 потребовало закрыть тестом. Первая —
необработанное исключение в handler'е: пользователь получает одну и ту же
короткую фразу, диалог сбрасывается, а в журнал попадают тип ошибки, update_id
и id пользователя, но не то, что человек написал боту. Вторая — доменные отказы
ядра: наружу идёт текст из словаря слоя telegram, а не сообщение исключения, в
котором стоят внутренние id.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from aiogram import loggers as aiogram_loggers
from tests.core.fakes import FixedClock
from tests.scenario_repos import World
from tests.telegram.conftest import FakeTelegram, deps_from_world

from kinday.core import services
from kinday.core.errors import DomainErrorCode
from kinday.telegram.bot import build_dispatcher
from kinday.telegram.errors import (
    AIOGRAM_EVENT_LOGGER,
    GENERIC_FAILURE,
    USER_TEXTS,
    silence_aiogram_update_dump,
)
from kinday.telegram.routers import Deps

ANTON = 100

# То, что пользователь пишет боту, и то, что оказалось в сообщении исключения:
# ни одно из этих слов не должно попасть ни в ответ бота, ни в журнал.
SECRET_NAME = "Аграфена"
SECRET_IN_EXCEPTION = "личная тайна"


def _digits(text: str) -> str:
    return "".join(character for character in text if character.isdigit())


def test_every_domain_code_has_user_text() -> None:
    """Неизвестный код уйдёт в общий текст, поэтому пропуск в словаре виден только тесту."""
    assert set(USER_TEXTS) == set(DomainErrorCode)


def test_user_texts_have_no_identifiers() -> None:
    """Критерий ревью: в ответах бота нет цифр — значит, нет и внутренних id."""
    for code, text in USER_TEXTS.items():
        assert not _digits(text), f"{code.value}: в тексте для пользователя есть цифры — {text!r}"
    assert not _digits(GENERIC_FAILURE)


@pytest.mark.asyncio
async def test_unhandled_error_gives_one_short_answer_and_resets_dialog(
    telegram: FakeTelegram, sqlite_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сбой посреди диалога: общий текст, сброшенное состояние, ничего в базе."""

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError(SECRET_IN_EXCEPTION)

    monkeypatch.setattr(services, "create_family", boom)

    await telegram.send("/new_family", user_id=ANTON)
    await telegram.send(SECRET_NAME, user_id=ANTON)
    await telegram.send("женский", user_id=ANTON)
    await telegram.send("15.06.1990", user_id=ANTON)
    answers = await telegram.send("Санкт-Петербург", user_id=ANTON)

    assert answers == [GENERIC_FAILURE]
    assert await sqlite_world.account.get_by_telegram_user_id(ANTON) is None

    # Состояние сброшено: следующий текст — не ответ на шаг диалога.
    after = await telegram.send(SECRET_NAME, user_id=ANTON)
    assert "не понял" in after[-1].lower()


@pytest.mark.asyncio
async def test_error_log_keeps_facts_and_drops_user_text(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """В журнале — тип ошибки, update_id и id пользователя; ни текста, ни трассировки."""

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError(SECRET_IN_EXCEPTION)

    monkeypatch.setattr(services, "create_family", boom)

    await telegram.send("/new_family", user_id=ANTON)
    await telegram.send(SECRET_NAME, user_id=ANTON)
    await telegram.send("женский", user_id=ANTON)
    await telegram.send("15.06.1990", user_id=ANTON)
    with caplog.at_level(logging.INFO):
        await telegram.send("Санкт-Петербург", user_id=ANTON)

    # Именно наша запись, а не любая строка журнала: иначе утверждение про
    # update_id прошло бы за счёт штатного «Update id=... is handled» от aiogram.
    ours = [record for record in caplog.records if record.name == "kinday.telegram.errors"]
    assert len(ours) == 1
    reported = ours[0].getMessage()
    assert "RuntimeError" in reported
    assert f"update_id={telegram.last_update_id}" in reported
    assert f"пользователь={ANTON}" in reported

    assert SECRET_IN_EXCEPTION not in caplog.text
    assert SECRET_NAME not in caplog.text
    assert "Traceback" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_filter_sits_on_the_logger_aiogram_actually_dumps_into() -> None:
    """Иначе переименование логгера в aiogram оставило бы тест зелёным, а прод — без фильтра."""
    assert aiogram_loggers.event.name == AIOGRAM_EVENT_LOGGER


def test_aiogram_update_dump_is_silenced(deps: Deps) -> None:
    """Штатный лог aiogram печатает трассировку и str(ошибки) — там бывает текст человека."""
    build_dispatcher(deps)
    aiogram_logger = logging.getLogger(AIOGRAM_EVENT_LOGGER)

    with_traceback = logging.LogRecord(
        name=AIOGRAM_EVENT_LOGGER,
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Cause exception while process update id=%d by bot id=%d\n%s: %s",
        args=(1, 2, "RuntimeError", SECRET_IN_EXCEPTION),
        exc_info=(RuntimeError, RuntimeError(SECRET_IN_EXCEPTION), None),
    )
    # Запись остаётся — сбой не должен исчезнуть бесследно, — но от неё остаётся
    # только тип ошибки: ни апдейта, ни её текста, ни стека.
    assert aiogram_logger.filter(with_traceback)
    assert with_traceback.exc_info is None
    assert SECRET_IN_EXCEPTION not in with_traceback.getMessage()
    assert "RuntimeError" in with_traceback.getMessage()

    without_traceback = logging.LogRecord(
        name=AIOGRAM_EVENT_LOGGER,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Update id=%s is handled",
        args=(1,),
        exc_info=None,
    )
    assert aiogram_logger.filter(without_traceback)
    assert without_traceback.getMessage() == "Update id=1 is handled"


def test_silencing_is_idempotent(sqlite_world: World, clock: FixedClock) -> None:
    """Каждый тест поднимает свой Dispatcher — фильтры не должны накапливаться."""
    aiogram_logger = logging.getLogger(AIOGRAM_EVENT_LOGGER)
    silence_aiogram_update_dump()
    before = len(aiogram_logger.filters)

    build_dispatcher(deps_from_world(sqlite_world, clock))
    silence_aiogram_update_dump()

    assert len(aiogram_logger.filters) == before


async def _family_with_two_parents(telegram: FakeTelegram, *, user_id: int = ANTON) -> None:
    """Антон, его отец и его мать: оба слота родителей заняты (SPEC 4.1)."""
    await telegram.send("/new_family", user_id=user_id)
    await telegram.send("Антон", user_id=user_id)
    await telegram.send("мужской", user_id=user_id)
    await telegram.send("15.06.1990", user_id=user_id)
    await telegram.send("Санкт-Петербург", user_id=user_id)
    for relation, name, gender, birth in (
        ("отец", "Пётр", "мужской", "01.03.1980"),
        ("мать", "Ольга", "женский", "05.05.1960"),
    ):
        await telegram.send("/add_person", user_id=user_id)
        await telegram.send("1", user_id=user_id)
        await telegram.send(relation, user_id=user_id)
        await telegram.send(name, user_id=user_id)
        await telegram.send(gender, user_id=user_id)
        await telegram.send(birth, user_id=user_id)


@pytest.mark.asyncio
async def test_third_parent_refusal_shows_no_identifiers(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """Критерий 21: третий родитель отклонён, и в отказе нет id человека из ядра."""
    await _family_with_two_parents(telegram)

    await telegram.send("/add_person", user_id=ANTON)
    await telegram.send("1", user_id=ANTON)
    await telegram.send("отец", user_id=ANTON)
    await telegram.send("Степан", user_id=ANTON)
    await telegram.send("мужской", user_id=ANTON)
    answers = await telegram.send("01.01.1950", user_id=ANTON)

    assert not _digits(answers[-1]), answers[-1]
    assert USER_TEXTS[DomainErrorCode.THIRD_PARENT] in answers[-1]
    account = await sqlite_world.account.get_by_telegram_user_id(ANTON)
    assert account is not None and account.current_family_id is not None
    people = await sqlite_world.person.list_by_family(account.current_family_id)
    assert "Степан" not in {person.name for person in people}


@pytest.mark.asyncio
async def test_out_of_range_offsets_refusal_shows_no_identifiers(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """Диапазон смещений — правило ядра, но его сообщение с числами наружу не уходит."""
    await _family_with_two_parents(telegram)

    await telegram.send("/settings", user_id=ANTON)
    await telegram.send("900", user_id=ANTON)
    answers = await telegram.send("09:00", user_id=ANTON)

    assert not _digits(answers[-1]), answers[-1]
    assert USER_TEXTS[DomainErrorCode.BAD_OFFSETS] in answers[-1]
    account = await sqlite_world.account.get_by_telegram_user_id(ANTON)
    assert account is not None
    assert account.offsets_days == (7, 1, 0)


@pytest.mark.asyncio
async def test_expired_invite_refusal_shows_no_identifiers(
    telegram: FakeTelegram, sqlite_world: World, clock: FixedClock
) -> None:
    """Критерий 25: просроченный код отклонён человеческим текстом без id записи."""
    await _family_with_two_parents(telegram)
    listing = await telegram.send("/invite", user_id=ANTON)
    number = next(
        line.split(".", 1)[0].strip()
        for line in listing[-1].splitlines()
        if "Пётр" in line and "." in line
    )
    issued = await telegram.send(number, user_id=ANTON)
    code = next(part for part in issued[-1].split() if "?start=" in part).split("?start=", 1)[1]

    clock.move_to(datetime(2027, 2, 1, tzinfo=UTC))
    answers = await telegram.send(f"/start {code}", user_id=200)

    assert not _digits(answers[-1]), answers[-1]
    assert USER_TEXTS[DomainErrorCode.INVITE_EXPIRED] in answers[-1]
    assert await sqlite_world.account.get_by_telegram_user_id(200) is None
